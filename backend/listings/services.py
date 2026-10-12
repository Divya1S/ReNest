"""Reservation lifecycle transitions shared by every entry point.

The serializer (PATCH /reservations/:id), the PIN-verify view, the batch
confirm view and the stale-confirmation sweep all move reservations between
states. Keeping the side effects here means a listing can never end up
"confirmed without a PIN" or "completed without its rescue request closed"
depending on which endpoint performed the transition.

Every transition re-reads the listing and the reservation under row locks and
validates the status it finds there. The checks a caller ran on its own
unlocked copy are only a courtesy for a nicer error message: two requests
racing on one reservation are decided here, and the loser gets a
ReservationConflict (HTTP 409).
"""

from __future__ import annotations

import logging
import secrets
from functools import partial
from typing import Any

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import APIException

from .models import Listing, Notification, RescueRequest, Reservation

logger = logging.getLogger(__name__)

# Statuses from which a reservation can still move.
_CONFIRMABLE = (Reservation.Status.REQUESTED,)
_COMPLETABLE = (Reservation.Status.CONFIRMED, Reservation.Status.EXPIRED_UNRESOLVED)
_CANCELLABLE = (
    Reservation.Status.REQUESTED,
    Reservation.Status.CONFIRMED,
    Reservation.Status.EXPIRED_UNRESOLVED,
)
# A listing in one of these states has already left the marketplace for good.
_CLOSED_LISTING_STATUSES = (Listing.Status.PICKED_UP, Listing.Status.DONATED)


class ReservationConflict(APIException):
    """The reservation or its listing moved on before this transition ran.

    Raised from under the row lock, so it reflects committed state rather
    than the caller's stale copy. It is an APIException so any DRF view that
    lets it propagate answers 409 without extra handling.
    """

    status_code = status.HTTP_409_CONFLICT
    default_detail = "This reservation changed while you were working on it. Refresh and try again."
    default_code = "reservation_conflict"


def _invalidate_dashboards(reservation: Reservation) -> None:
    from .views.dashboard import invalidate_user_dashboard_cache

    invalidate_user_dashboard_cache(reservation.claimant_id)
    invalidate_user_dashboard_cache(reservation.listing.owner_id)


def generate_handoff_pin() -> str:
    return str(secrets.randbelow(1_000_000)).zfill(6)


def _lock(reservation: Reservation) -> tuple[Reservation, Listing]:
    """Lock the listing row, then the reservation row, and return fresh copies.

    The order is the same everywhere a listing and its reservations are
    written together (claiming, donating, deleting, placing a hold): listing
    first. Two transactions that take the locks in opposite orders would
    deadlock instead of queueing. all_objects is deliberate: a soft-deleted
    listing still has to be lockable so its reservations can be closed.
    """
    listing = Listing.all_objects.select_for_update().get(pk=reservation.listing_id)
    locked = Reservation.objects.select_for_update().get(pk=reservation.pk)
    locked.listing = listing
    return locked, listing


def _ensure_listing_can_hand_off(reservation: Reservation, listing: Listing) -> None:
    """Refuse to confirm or complete against a listing that has moved on."""
    if listing.deleted_at is not None or listing.status in _CLOSED_LISTING_STATUSES:
        raise ReservationConflict("This listing is no longer available for a handoff.")
    # An EXPIRED_UNRESOLVED handoff does not hold the listing: the sweep
    # reopens it, so somebody else may have claimed it since.
    other_claim = (
        Reservation.objects.filter(listing_id=listing.pk, status__in=Reservation.ACTIVE_STATUSES)
        .exclude(pk=reservation.pk)
        .exists()
    )
    if other_claim:
        raise ReservationConflict(
            "Someone else now has an active claim on this listing. Resolve that claim first."
        )


def dispatch_reservation_confirmed_webhook(reservation: Reservation) -> None:
    """Tell partner endpoints about a confirmed handoff. Never raises.

    Scheduled by confirm_reservation, so the single PATCH and the batch
    confirm cannot disagree about whether partners hear about it.
    """
    try:
        from .views.helpers import owner_campus_name
        from .views.partner import dispatch_webhook_event

        # All delivery rows for one event are written together or not at all.
        with transaction.atomic():
            dispatch_webhook_event(
                "reservation_confirmed",
                {"id": reservation.pk, "listing_id": reservation.listing_id, "status": reservation.status},
                owner_campus_name(reservation.listing.owner),
            )
    except Exception:
        logger.exception("reservation_confirmed webhook dispatch failed for reservation %s", reservation.pk)


def _notify_cancellation(reservation: Reservation, listing: Listing, actor: Any, reason: str) -> None:
    """Tell whoever did not cancel that the handoff is off."""
    actor_id = getattr(actor, "id", None)
    recipient_ids = [
        user_id for user_id in (reservation.claimant_id, listing.owner_id) if user_id != actor_id
    ]
    body = f'The reservation for "{listing.title}" was cancelled.'
    if reason:
        body = f"{body} {reason}"
    for user_id in recipient_ids:
        # The key must not start with a prefix that sync_user_notifications
        # manages ("reservation:", "handoff:"), or the next sync prunes it.
        Notification.objects.get_or_create(
            dedupe_key=f"cancelled:{reservation.pk}:{user_id}",
            defaults={
                "user_id": user_id,
                "type": Notification.Type.RESERVATION,
                "title": "Reservation cancelled",
                "body": body,
                "link_path": f"/handoffs/{reservation.pk}",
                "priority": Notification.Priority.HIGH,
            },
        )


@transaction.atomic
def confirm_reservation(reservation: Reservation, *, notify: bool = True) -> Reservation:
    """REQUESTED -> CONFIRMED: mint the handoff PIN, reserve the listing, email."""
    reservation, listing = _lock(reservation)
    if reservation.status not in _CONFIRMABLE:
        raise ReservationConflict("Only a requested reservation can be confirmed.")
    _ensure_listing_can_hand_off(reservation, listing)

    reservation.status = Reservation.Status.CONFIRMED
    update_fields = ["status", "updated_at"]
    if not reservation.handoff_pin:
        reservation.handoff_pin = generate_handoff_pin()
        update_fields.append("handoff_pin")
    reservation.save(update_fields=update_fields)

    listing.status = Listing.Status.RESERVED
    listing.save(update_fields=["status", "updated_at"])

    _invalidate_dashboards(reservation)
    # After commit: without a worker the delivery task runs inline, and an
    # HTTP call to a partner must not happen while the row locks are held.
    transaction.on_commit(partial(dispatch_reservation_confirmed_webhook, reservation))
    if notify:
        from .tasks import send_reservation_confirmed_email_task

        # Dispatched inline in eager mode (dev, free tier) and enqueued
        # otherwise; the task retries if the row is not yet visible.
        send_reservation_confirmed_email_task.delay(reservation.pk)
    return reservation


@transaction.atomic
def complete_reservation(reservation: Reservation, *, notify: bool = True) -> Reservation:
    """CONFIRMED (or EXPIRED_UNRESOLVED) -> COMPLETED: close the loop everywhere."""
    reservation, listing = _lock(reservation)
    if reservation.status not in _COMPLETABLE:
        raise ReservationConflict("Only a confirmed handoff can be completed.")
    _ensure_listing_can_hand_off(reservation, listing)

    reservation.status = Reservation.Status.COMPLETED
    # The PIN has done its job; a completed row should not keep a live secret.
    reservation.handoff_pin = ""
    reservation.save(update_fields=["status", "handoff_pin", "updated_at"])

    RescueRequest.objects.filter(
        matched_listing=listing,
        status=RescueRequest.Status.MATCHED,
    ).update(status=RescueRequest.Status.FULFILLED, updated_at=timezone.now())
    listing.status = Listing.Status.PICKED_UP
    listing.save(update_fields=["status", "updated_at"])

    _invalidate_dashboards(reservation)
    if notify:
        from .tasks import send_reservation_completed_email_task

        send_reservation_completed_email_task.delay(reservation.pk)
    return reservation


@transaction.atomic
def cancel_reservation(
    reservation: Reservation,
    *,
    previous_status: str | None = None,
    actor: Any = None,
    reason: str = "",
) -> Reservation:
    """Any live state -> CANCELLED: reopen the listing and its rescue request.

    An owner who cancels a handoff they already confirmed earns a trust strike
    (bad-actor signal). A claimant cancelling does not, and neither does
    cancelling an EXPIRED_UNRESOLVED handoff that the sweep already gave up on.

    `previous_status` overrides the status the strike rule looks at; leave it
    None to use the status found under the lock. `reason` is appended to the
    notification the other party receives.
    """
    reservation, listing = _lock(reservation)
    if reservation.status not in _CANCELLABLE:
        raise ReservationConflict("This reservation is already closed.")
    previous = previous_status or reservation.status

    reservation.status = Reservation.Status.CANCELLED
    reservation.handoff_pin = ""
    reservation.save(update_fields=["status", "handoff_pin", "updated_at"])

    other_claim = (
        Reservation.objects.filter(listing_id=listing.pk, status__in=Reservation.ACTIVE_STATUSES)
        .exclude(pk=reservation.pk)
        .exists()
    )
    if not other_claim:
        RescueRequest.objects.filter(
            matched_listing=listing,
            status=RescueRequest.Status.MATCHED,
        ).update(matched_listing=None, status=RescueRequest.Status.OPEN, updated_at=timezone.now())
        # Only a listing this reservation was holding goes back on the shelf.
        # A donated, picked-up or already expired listing stays where it is.
        if listing.status == Listing.Status.RESERVED:
            listing.status = Listing.Status.AVAILABLE
            # Past its deadline it expires instead, with a repost token.
            listing.refresh_status(commit=False)
            listing.save(
                update_fields=["status", "repost_token", "repost_token_expires_at", "updated_at"]
            )

    actor_is_owner = actor is None or getattr(actor, "id", None) == listing.owner_id
    if previous == Reservation.Status.CONFIRMED and actor_is_owner:
        get_user_model().objects.filter(pk=listing.owner_id).update(trust_strikes=F("trust_strikes") + 1)

    _notify_cancellation(reservation, listing, actor, reason)
    _invalidate_dashboards(reservation)
    return reservation


@transaction.atomic
def release_listing_claims(listing: Listing, *, actor: Any = None, reason: str = "") -> int:
    """Cancel every live reservation on a listing that is leaving the market.

    Donating, deleting or reposting a listing used to leave its requested or
    confirmed reservation pointing at it. Going through cancel_reservation
    keeps the claimant informed and the rescue request reopened. Returns the
    number of reservations cancelled.
    """
    # Listing first, matching the lock order in _lock.
    Listing.all_objects.select_for_update().filter(pk=listing.pk).first()
    live = list(
        Reservation.objects.filter(listing_id=listing.pk, status__in=Reservation.ACTIVE_STATUSES)
    )
    for reservation in live:
        cancel_reservation(reservation, actor=actor, reason=reason)
    return len(live)


def apply_status_transition(
    reservation: Reservation,
    next_status: str,
    *,
    previous_status: str | None = None,
    actor: Any = None,
) -> Reservation:
    """Dispatch a status change requested through PATCH /reservations/:id.

    COMPLETED is deliberately absent: a handoff is completed only by the
    owner entering the claimant's PIN (see ReservationVerifyPinView), so this
    dispatcher must not offer a way around it.

    `previous_status` is accepted for existing callers but no longer decides
    anything: each service reads the status under its own lock.
    """
    if next_status == Reservation.Status.CONFIRMED:
        return confirm_reservation(reservation)
    if next_status == Reservation.Status.CANCELLED:
        return cancel_reservation(reservation, actor=actor)
    raise ReservationConflict("That status change is not supported.")


def any_status(*statuses: Any) -> list[str]:
    return [str(s) for s in statuses]
