"""Reservation lifecycle transitions shared by every entry point.

The serializer (PATCH /reservations/:id), the PIN-verify view, the batch
confirm view and the stale-confirmation sweep all move reservations between
states. Keeping the side effects here means a listing can never end up
"confirmed without a PIN" or "completed without its rescue request closed"
depending on which endpoint performed the transition.
"""

from __future__ import annotations

import secrets
from typing import Any

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import Listing, RescueRequest, Reservation


def _invalidate_dashboards(reservation: Reservation) -> None:
    from .views.dashboard import invalidate_user_dashboard_cache

    invalidate_user_dashboard_cache(reservation.claimant_id)
    invalidate_user_dashboard_cache(reservation.listing.owner_id)


def generate_handoff_pin() -> str:
    return str(secrets.randbelow(1_000_000)).zfill(6)


@transaction.atomic
def confirm_reservation(reservation: Reservation, *, notify: bool = True) -> Reservation:
    """REQUESTED -> CONFIRMED: mint the handoff PIN, reserve the listing, email."""
    reservation = Reservation.objects.select_for_update().select_related("listing").get(pk=reservation.pk)
    reservation.status = Reservation.Status.CONFIRMED
    update_fields = ["status", "updated_at"]
    if not reservation.handoff_pin:
        reservation.handoff_pin = generate_handoff_pin()
        update_fields.append("handoff_pin")
    reservation.save(update_fields=update_fields)

    listing = reservation.listing
    listing.refresh_status(commit=False)
    listing.status = Listing.Status.RESERVED
    listing.save(update_fields=["status", "updated_at"])

    _invalidate_dashboards(reservation)
    if notify:
        from .tasks import send_reservation_confirmed_email_task

        # Dispatched inline in eager mode (dev, free tier) and enqueued
        # otherwise; the task retries if the row is not yet visible.
        send_reservation_confirmed_email_task.delay(reservation.pk)
    return reservation


@transaction.atomic
def complete_reservation(reservation: Reservation, *, notify: bool = True) -> Reservation:
    """CONFIRMED (or EXPIRED_UNRESOLVED) -> COMPLETED: close the loop everywhere."""
    reservation = Reservation.objects.select_for_update().select_related("listing").get(pk=reservation.pk)
    reservation.status = Reservation.Status.COMPLETED
    reservation.save(update_fields=["status", "updated_at"])

    listing = reservation.listing
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
) -> Reservation:
    """Any live state -> CANCELLED: reopen the listing and its rescue request.

    An owner who cancels a handoff they already confirmed earns a trust strike
    (bad-actor signal). A claimant cancelling does not, and neither does
    cancelling an EXPIRED_UNRESOLVED handoff that the sweep already gave up on.
    """
    previous = previous_status or reservation.status
    reservation = Reservation.objects.select_for_update().select_related("listing").get(pk=reservation.pk)
    reservation.status = Reservation.Status.CANCELLED
    reservation.save(update_fields=["status", "updated_at"])

    listing = reservation.listing
    RescueRequest.objects.filter(
        matched_listing=listing,
        status=RescueRequest.Status.MATCHED,
    ).update(matched_listing=None, status=RescueRequest.Status.OPEN, updated_at=timezone.now())
    listing.status = (
        Listing.Status.EXPIRED if listing.available_until < timezone.now() else Listing.Status.AVAILABLE
    )
    listing.save(update_fields=["status", "updated_at"])

    actor_is_owner = actor is None or getattr(actor, "id", None) == listing.owner_id
    if previous == Reservation.Status.CONFIRMED and actor_is_owner:
        get_user_model().objects.filter(pk=listing.owner_id).update(trust_strikes=F("trust_strikes") + 1)

    _invalidate_dashboards(reservation)
    return reservation


def apply_status_transition(
    reservation: Reservation, next_status: str, *, previous_status: str, actor: Any = None
) -> Reservation:
    """Dispatch to the right service for a validated status change."""
    if next_status == Reservation.Status.CONFIRMED:
        return confirm_reservation(reservation)
    if next_status == Reservation.Status.COMPLETED:
        return complete_reservation(reservation)
    if next_status == Reservation.Status.CANCELLED:
        return cancel_reservation(reservation, previous_status=previous_status, actor=actor)
    if next_status == Reservation.Status.REQUESTED:
        with transaction.atomic():
            listing = reservation.listing
            listing.status = Listing.Status.RESERVED
            listing.save(update_fields=["status", "updated_at"])
        return reservation
    return reservation


def any_status(*statuses: Any) -> list[str]:
    return [str(s) for s in statuses]
