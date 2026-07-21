from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal
from typing import Any, TYPE_CHECKING

from django.conf import settings
from django.core.mail import EmailMessage
from django.utils import timezone

from .models import Listing, Notification, Reservation

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_REMINDER_WINDOW_HOURS = 24
_REMINDER_LOOKAHEAD_HOURS = 4  # ± window so 30-min cron cadence never double-fires


def _send(*, subject: Any, message: Any, recipient: Any, user: Any = None) -> Any:
    if user is not None and not getattr(user, "email_notifications", True):
        return
    try:
        footer = ""
        headers: dict[str, str] = {}
        if user is not None:
            from accounts.views import make_unsubscribe_url

            unsubscribe_url = make_unsubscribe_url(user)
            footer = f"\n\n---\nTo stop receiving emails from ReNest: {unsubscribe_url}"
            # RFC 8058 one-click unsubscribe — Gmail/Yahoo require these
            # headers for bulk senders, and they materially help inbox placement.
            headers = {
                "List-Unsubscribe": f"<{unsubscribe_url}>",
                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            }
        EmailMessage(
            subject=subject,
            body=message + footer,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[recipient],
            headers=headers,
        ).send(fail_silently=False)
    except Exception:
        logger.exception(
            "Transactional email failed to %s — subject: %s", recipient, subject
        )


def _handoff_url(reservation: Any) -> Any:
    return f"{settings.APP_BASE_URL}/handoffs/{reservation.id}"


def send_reservation_confirmed_email(reservation: Any) -> Any:
    """Both parties get an email + push when status transitions to CONFIRMED."""
    from .webpush import send_push_to_user

    listing = reservation.listing
    owner = listing.owner
    claimant = reservation.claimant
    url = _handoff_url(reservation)
    path = f"/handoffs/{reservation.pk}"

    _send(
        subject=f'Your reservation for "{listing.title}" is confirmed',
        message=(
            f"Hi {claimant.display_name},\n\n"
            f'{owner.display_name} confirmed your pickup for "{listing.title}".\n\n'
            f"Agreed pickup window: {reservation.pickup_time_window}\n\n"
            f"Head to the handoff page to coordinate the details:\n{url}\n\n"
            "Good luck with the move-out!"
        ),
        recipient=claimant.email,
        user=claimant,
    )
    send_push_to_user(
        claimant,
        title="Reservation confirmed!",
        body=f'{owner.display_name} confirmed your pickup for "{listing.title}".',
        url=path,
    )

    _send(
        subject=f'You confirmed the pickup for "{listing.title}"',
        message=(
            f"Hi {owner.display_name},\n\n"
            f"You confirmed {claimant.display_name}'s reservation for \"{listing.title}\".\n\n"
            f"Agreed pickup window: {reservation.pickup_time_window}\n\n"
            f"Track the handoff here:\n{url}\n\n"
            "Thanks for rescuing campus waste!"
        ),
        recipient=owner.email,
        user=owner,
    )


def send_reservation_completed_email(reservation: Any) -> Any:
    """Both parties get a feedback-request email when status transitions to COMPLETED."""
    listing = reservation.listing
    owner = listing.owner
    claimant = reservation.claimant
    url = _handoff_url(reservation)

    for user, counterparty in [(claimant, owner), (owner, claimant)]:
        _send(
            subject=f'How did the handoff go? Leave feedback for "{listing.title}"',
            message=(
                f"Hi {user.display_name},\n\n"
                f'Your pickup of "{listing.title}" with {counterparty.display_name} is complete — great work!\n\n'
                f"Take a moment to leave feedback on the handoff. "
                f"It helps build trust in the ReNest community:\n{url}\n\n"
                "Thank you for being part of ReNest."
            ),
            recipient=user.email,
            user=user,
        )


def send_handoff_reminder_emails() -> Any:
    """
    Send a ~24h pickup reminder to both parties on active reservations whose
    listing.available_until is within the next 24 ± 4 hours.

    Uses a Notification record as a dedupe guard so the email fires at most
    once per reservation regardless of how often the maintenance cron runs.

    Returns the count of reservations for which reminders were sent.
    """
    now = timezone.now()
    lower = now + timedelta(hours=_REMINDER_WINDOW_HOURS - _REMINDER_LOOKAHEAD_HOURS)
    upper = now + timedelta(hours=_REMINDER_WINDOW_HOURS + _REMINDER_LOOKAHEAD_HOURS)

    reservations = Reservation.objects.select_related(
        "listing", "listing__owner", "claimant"
    ).filter(
        status__in=[Reservation.Status.REQUESTED, Reservation.Status.CONFIRMED],
        listing__available_until__range=(lower, upper),
        listing__is_demo=False,
    )

    sent = 0
    for reservation in reservations:
        dedupe_key = f"email_handoff_reminder:{reservation.id}"
        _, created = Notification.objects.get_or_create(
            dedupe_key=dedupe_key,
            defaults={
                "user": reservation.claimant,
                "type": Notification.Type.HANDOFF_URGENT,
                "title": f"24h pickup reminder: {reservation.listing.title}",
                "body": "The move-out deadline for this listing is in ~24 hours.",
                "link_path": f"/handoffs/{reservation.id}",
                "priority": Notification.Priority.HIGH,
            },
        )
        if not created:
            continue

        listing = reservation.listing
        owner = listing.owner
        claimant = reservation.claimant
        url = _handoff_url(reservation)
        deadline = listing.available_until.strftime("%A, %B %-d at %-I:%M %p UTC")

        from .webpush import send_push_to_user
        for user, counterparty in [(claimant, owner), (owner, claimant)]:
            _send(
                subject=f'Reminder: pickup for "{listing.title}" closes in ~24 hours',
                message=(
                    f"Hi {user.display_name},\n\n"
                    f'This is a reminder that the pickup window for "{listing.title}" with '
                    f"{counterparty.display_name} closes around {deadline}.\n\n"
                    f"Coordinate the handoff here:\n{url}\n\n"
                    "If you can no longer complete this pickup, please cancel the reservation "
                    "so the listing can be claimed by someone else."
                ),
                recipient=user.email,
                user=user,
            )
            send_push_to_user(
                user,
                title="Pickup reminder — 24 h left",
                body=f'Your handoff for "{listing.title}" closes around {deadline}.',
                url=f"/handoffs/{reservation.pk}",
            )
        sent += 1

    return sent


# ---------------------------------------------------------------------------
# Post-handoff impact summary
# ---------------------------------------------------------------------------

# Approximate weight in lbs per category, used for landfill diversion estimate.
_CATEGORY_WEIGHT_LBS: dict[str, float] = {
    "storage": 3.5,
    "lighting": 2.0,
    "supplies": 1.0,
    "comfort": 4.0,
    "toiletries": 0.8,
    "decor": 1.5,
    "other": 2.0,
}


def _build_cumulative_impact(owner: Any) -> dict[str, object]:
    rescued = Listing.objects.filter(
        owner=owner,
        status__in=[Listing.Status.PICKED_UP, Listing.Status.DONATED],
        is_demo=False,
    ).values("category", "estimated_retail_value")

    total_retail = Decimal("0")
    total_lbs = 0.0
    for item in rescued:
        total_retail += Decimal(str(item["estimated_retail_value"] or 0))
        total_lbs += _CATEGORY_WEIGHT_LBS.get(item["category"], 2.0)

    return {
        "count": len(list(rescued)),
        "total_retail": total_retail,
        "total_lbs": round(total_lbs, 1),
    }


def send_handoff_impact_notification(reservation: Reservation) -> None:
    """
    After a reservation is marked COMPLETED, notify the listing owner with
    their cumulative rescue impact (retail value saved, weight diverted).
    """
    listing = reservation.listing
    owner = listing.owner
    item_weight = _CATEGORY_WEIGHT_LBS.get(listing.category, 2.0)
    item_retail = Decimal(str(listing.estimated_retail_value or 0))
    cumulative = _build_cumulative_impact(owner)

    # In-app notification
    Notification.objects.create(
        user=owner,
        type=Notification.Type.SYSTEM,
        priority=Notification.Priority.LOW,
        title="Rescue complete 🌱",
        body=(
            f'"{listing.title}" was picked up! '
            f"That's ~{item_weight:.0f} lb diverted from the landfill"
            + (f" and ${item_retail:.0f} in retail value rescued" if item_retail > 0 else "")
            + f". Your ReNest total: {cumulative['count']} item{'s' if cumulative['count'] != 1 else ''}, "
            f"${cumulative['total_retail']:.0f} rescued."
        ),
        link_path=f"/my-listings",
        dedupe_key=f"impact:reservation:{reservation.id}",
    )

    # Email
    body_lines = [
        f"Hi {owner.display_name},",
        "",
        f'"{listing.title}" was just picked up — great work keeping it out of the trash.',
        "",
        "This rescue:",
        f"  • Estimated retail value: ${item_retail:.0f}" if item_retail > 0 else "  • Listed for free",
        f"  • Approx. weight diverted from landfill: {item_weight:.0f} lb",
        "",
        "Your ReNest total so far:",
        f"  • {cumulative['count']} item{'s' if cumulative['count'] != 1 else ''} rescued",
        f"  • ${cumulative['total_retail']:.0f} in retail value saved",
        f"  • ~{cumulative['total_lbs']:.0f} lbs diverted from the landfill",
        "",
        "Thank you for being part of ReNest.",
    ]
    _send(
        subject=f'Your rescue of "{listing.title}" is complete',
        message="\n".join(body_lines),
        recipient=owner.email,
        user=owner,
    )

    check_and_award_milestone(owner)


# ---------------------------------------------------------------------------
# Gamified trust milestones
# ---------------------------------------------------------------------------

_MILESTONE_THRESHOLDS = [
    (10, "campus_hero", "Campus Hero"),
    (5, "active_rescuer", "Active Rescuer"),
    (1, "first_rescue", "First Rescue"),
]


def check_and_award_milestone(user: Any) -> str | None:
    """
    Count completed handoffs for user; award the highest unlocked milestone
    if they haven't received it yet. Returns the new milestone key or None.
    """
    from django.contrib.auth import get_user_model
    User = get_user_model()

    completed = Reservation.objects.filter(
        listing__owner=user,
        status=Reservation.Status.COMPLETED,
        listing__is_demo=False,
    ).count()

    new_milestone = ""
    new_label = ""
    for threshold, key, label in _MILESTONE_THRESHOLDS:
        if completed >= threshold:
            new_milestone = key
            new_label = label
            break

    if not new_milestone or user.milestone == new_milestone:
        return None

    User.objects.filter(pk=user.pk).update(milestone=new_milestone)
    user.milestone = new_milestone

    Notification.objects.get_or_create(
        user=user,
        dedupe_key=f"milestone:{user.id}:{new_milestone}",
        defaults={
            "type": Notification.Type.SYSTEM,
            "title": f"You earned the {new_label} badge!",
            "body": (
                f"You've completed {completed} handoff{'s' if completed != 1 else ''} "
                f"on ReNest. Keep it up — your campus thanks you."
            ),
            "link_path": "/trust/me",
            "priority": Notification.Priority.NORMAL,
        },
    )

    _send(
        subject=f"You earned the {new_label} badge on ReNest!",
        message=(
            f"Hi {user.display_name},\n\n"
            f"You've completed {completed} handoff{'s' if completed != 1 else ''} — "
            f"that earns you the \"{new_label}\" badge.\n\n"
            f"Your badge is now visible on your listings and trust page:\n"
            f"{settings.APP_BASE_URL}/trust/me\n\n"
            "Keep rescuing — every item counts.\n\nReNest"
        ),
        recipient=user.email,
        user=user,
    )

    return new_milestone


def send_donation_receipt_email(listing: Listing) -> None:
    """Email the PDF donation receipt to the listing owner after a hub drop-off."""
    owner = listing.owner
    if not getattr(owner, "email_notifications", True):
        return

    hub_name = listing.donation_hub.name if listing.donation_hub else "a campus donation hub"
    subject = f"Your ReNest donation receipt — {listing.title}"

    from accounts.views import make_unsubscribe_url
    footer = f"\n\n---\nTo stop receiving emails from ReNest: {make_unsubscribe_url(owner)}"
    body = (
        f"Hi {owner.get_full_name() or 'there'},\n\n"
        f"Thank you for donating \"{listing.title}\" to {hub_name} through ReNest.\n\n"
        f"Your PDF receipt is attached. Keep it for your personal records.\n\n"
        f"— The ReNest Team\n"
        f"{settings.APP_BASE_URL}"
        f"{footer}"
    )
    try:
        msg = EmailMessage(
            subject=subject,
            body=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[owner.email],
        )
        if listing.donation_receipt:
            listing.donation_receipt.open("rb")
            pdf_bytes = listing.donation_receipt.read()
            listing.donation_receipt.close()
            filename = f"receipt-{listing.pk}.pdf"
            msg.attach(filename, pdf_bytes, "application/pdf")
        msg.send(fail_silently=False)
    except Exception:
        logger.exception("Donation receipt email failed for listing %s", listing.pk)
