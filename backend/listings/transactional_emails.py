from __future__ import annotations

import hashlib
import logging
import smtplib
from datetime import timedelta
from decimal import Decimal
from typing import Any, Sequence

from django.conf import settings
from django.core.cache import cache
from django.core.mail import EmailMessage
from django.utils import timezone

from .models import Listing, Notification, Reservation

logger = logging.getLogger(__name__)

_REMINDER_WINDOW_HOURS = 24
_REMINDER_LOOKAHEAD_HOURS = 4  # ± window so 30-min cron cadence never double-fires

# How long a "sent" marker lives. It has to outlast a task's whole retry
# ladder and any broker redelivery of the same message; a day is far beyond
# both and short enough that the keys do not accumulate.
_SENT_MARKER_TTL_SECONDS = 24 * 60 * 60


class EmailDeliveryError(Exception):
    """A delivery attempt failed in a way a later attempt may fix.

    Raised by `send_once` so the calling Celery task can retry.
    """


def _sanitise_subject(subject: Any) -> str:
    """Collapse newlines: a header value containing CR/LF is silently dropped
    (or, worse, injects headers). Listing titles flow straight into subjects."""
    text = " ".join(str(subject).splitlines()).strip()
    return text[:200]


def _build_message(*, subject: str, message: str, recipient: str, user: Any = None) -> EmailMessage:
    footer = ""
    headers: dict[str, str] = {}
    if user is not None:
        from accounts.views import make_unsubscribe_api_url, make_unsubscribe_url

        unsubscribe_url = make_unsubscribe_url(user)
        footer = f"\n\n---\nTo stop receiving emails from ReNest: {unsubscribe_url}"
        # RFC 8058 one-click unsubscribe: Gmail/Yahoo require these headers
        # for bulk senders. The header URL must accept POST, so it points at
        # the API endpoint; the footer link stays on the SPA confirmation page.
        headers = {
            "List-Unsubscribe": f"<{make_unsubscribe_api_url(user)}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        }
    return EmailMessage(
        subject=subject,
        body=message + footer,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
        headers=headers,
    )


def _send(*, subject: Any, message: Any, recipient: Any, user: Any = None) -> Any:
    """Best-effort delivery: a failure is logged and never raised.

    For sweeps and digests that loop over many recipients with no task retry
    behind them, where one bad address must not abort the rest of the batch.
    Emails sent from a retrying task use `send_once` instead.
    """
    if user is not None and not getattr(user, "email_notifications", True):
        return
    subject = _sanitise_subject(subject)
    try:
        _build_message(subject=subject, message=message, recipient=recipient, user=user).send(
            fail_silently=False
        )
    except Exception:
        logger.exception(
            "Transactional email failed to %s (subject: %s)", recipient, subject
        )


def _is_permanent_failure(exc: Exception) -> bool:
    """True when the mail server refused the message outright (a 5xx reply).

    Retrying cannot change that answer. Everything else (timeouts, dropped
    connections, 4xx "try again later" replies) is treated as transient.
    """
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        codes = [code for code, _ in exc.recipients.values()]
        return bool(codes) and all(500 <= code < 600 for code in codes)
    if isinstance(exc, smtplib.SMTPResponseException):
        return 500 <= exc.smtp_code < 600
    return False


def _marker_key(kind: str, obj: Any, recipient: str) -> str:
    # created_at is part of the object's identity here: primary keys can be
    # reused (SQLite after a rollback, a restored database), and a marker left
    # by an earlier row must not silence mail about a different one.
    created_at = getattr(obj, "created_at", None)
    stamp = int(created_at.timestamp() * 1_000_000) if created_at else 0
    digest = hashlib.sha256(recipient.strip().lower().encode("utf-8")).hexdigest()[:16]
    return f"sent:{kind}:{obj.pk}:{stamp}:{digest}"


def claim_once(kind: str, obj: Any, recipient: str) -> bool:
    """Atomically claim the right to notify `recipient` about `obj`. False if already claimed."""
    return bool(cache.add(_marker_key(kind, obj, recipient), 1, timeout=_SENT_MARKER_TTL_SECONDS))


def send_once(
    *,
    kind: str,
    obj: Any,
    subject: Any,
    message: str,
    recipient: str,
    user: Any = None,
    attachments: Sequence[tuple[str, bytes, str]] = (),
) -> bool:
    """Send one email at most once per (kind, object, recipient).

    Built for tasks that retry and that write to more than one person. The
    recipient's marker is claimed before sending (cache.add is atomic, the
    same claim-then-act shape as the reminder job's conditional UPDATE), so:

      * a retry caused by one recipient's failure skips everyone whose copy
        already went out
      * a duplicate run of the task (broker redelivery) sends nothing twice

    Returns True if this call sent the email, False if it was skipped
    (opted out, already sent, or refused for good by the mail server).
    Raises EmailDeliveryError on a transient failure, after releasing the
    marker so the retry can claim it again.
    """
    if user is not None and not getattr(user, "email_notifications", True):
        return False
    subject = _sanitise_subject(subject)
    if not claim_once(f"email:{kind}", obj, recipient):
        return False
    try:
        email = _build_message(subject=subject, message=message, recipient=recipient, user=user)
        for filename, content, mimetype in attachments:
            email.attach(filename, content, mimetype)
        email.send(fail_silently=False)
    except Exception as exc:
        # Nothing was delivered, so the marker must not claim otherwise.
        cache.delete(_marker_key(f"email:{kind}", obj, recipient))
        logger.exception("Transactional email failed to %s (subject: %s)", recipient, subject)
        if _is_permanent_failure(exc):
            return False
        raise EmailDeliveryError(f"{kind} email for object {obj.pk} was not delivered") from exc
    return True


def _send_each_once(kind: str, obj: Any, emails: Sequence[dict[str, Any]]) -> None:
    """`send_once` for several recipients: try them all, then report a failure.

    One party's mail server being down must not hold back the other party's
    copy until the retry.
    """
    failure: EmailDeliveryError | None = None
    for email in emails:
        try:
            send_once(kind=kind, obj=obj, **email)
        except EmailDeliveryError as exc:
            failure = failure or exc
    if failure is not None:
        raise failure


def _handoff_url(reservation: Any) -> Any:
    return f"{settings.APP_BASE_URL}/handoffs/{reservation.id}"


def send_reservation_confirmed_email(reservation: Any) -> Any:
    """Both parties get an email + push when status transitions to CONFIRMED.

    Retry-safe (see `send_once`); raises EmailDeliveryError if a delivery
    should be attempted again.
    """
    from .webpush import send_push_to_user

    listing = reservation.listing
    owner = listing.owner
    claimant = reservation.claimant
    url = _handoff_url(reservation)
    path = f"/handoffs/{reservation.pk}"

    # The push carries its own marker: it is not an email, but a retried task
    # would repeat it just the same.
    if claim_once("push:reservation_confirmed", reservation, f"user:{claimant.pk}"):
        send_push_to_user(
            claimant,
            title="Reservation confirmed!",
            body=f'{owner.display_name} confirmed your pickup for "{listing.title}".',
            url=path,
            notification_type="reservation",
        )

    _send_each_once(
        "reservation_confirmed",
        reservation,
        [
            dict(
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
            ),
            dict(
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
            ),
        ],
    )


def send_reservation_completed_email(reservation: Any) -> Any:
    """Both parties get a feedback-request email when status transitions to COMPLETED.

    Retry-safe (see `send_once`).
    """
    listing = reservation.listing
    owner = listing.owner
    claimant = reservation.claimant
    url = _handoff_url(reservation)

    emails = []
    for user, counterparty in [(claimant, owner), (owner, claimant)]:
        emails.append(dict(
            subject=f'How did the handoff go? Leave feedback for "{listing.title}"',
            message=(
                f"Hi {user.display_name},\n\n"
                f'Your pickup of "{listing.title}" with {counterparty.display_name} is complete. Great work!\n\n'
                f"Take a moment to leave feedback on the handoff. "
                f"It helps build trust in the ReNest community:\n{url}\n\n"
                "Thank you for being part of ReNest."
            ),
            recipient=user.email,
            user=user,
        ))
    _send_each_once("reservation_completed", reservation, emails)


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
    for reservation in reservations.filter(reminder_sent_at__isnull=True):
        # Claim the reservation before sending: a conditional UPDATE means two
        # overlapping runs can never both send the same reminder.
        claimed = Reservation.objects.filter(
            pk=reservation.pk, reminder_sent_at__isnull=True
        ).update(reminder_sent_at=now)
        if not claimed:
            continue
        Notification.objects.get_or_create(
            dedupe_key=f"email_handoff_reminder:{reservation.id}",
            defaults={
                "user": reservation.claimant,
                "type": Notification.Type.HANDOFF_URGENT,
                "title": f"24h pickup reminder: {reservation.listing.title}",
                "body": "The move-out deadline for this listing is in ~24 hours.",
                "link_path": f"/handoffs/{reservation.id}",
                "priority": Notification.Priority.HIGH,
            },
        )

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
                title="Pickup reminder: 24 h left",
                body=f'Your handoff for "{listing.title}" closes around {deadline}.',
                url=f"/handoffs/{reservation.pk}",
                notification_type="handoff_urgent",
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

    # In-app notification. get_or_create, not create: dedupe_key is unique, so
    # a retried task used to die here on IntegrityError before reaching the
    # email it was retrying for.
    Notification.objects.get_or_create(
        dedupe_key=f"impact:reservation:{reservation.id}",
        defaults=dict(
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
            link_path="/my-listings",
        ),
    )

    # The milestone is database work and must not wait on a mail server, so
    # it runs before the email that may raise for a retry.
    check_and_award_milestone(owner)

    # Email
    body_lines = [
        f"Hi {owner.display_name},",
        "",
        f'"{listing.title}" was just picked up. Great work keeping it out of the trash.',
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
    send_once(
        kind="handoff_impact",
        obj=reservation,
        subject=f'Your rescue of "{listing.title}" is complete',
        message="\n".join(body_lines),
        recipient=owner.email,
        user=owner,
    )


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
                f"on ReNest. Keep it up. Your campus thanks you."
            ),
            "link_path": "/trust/me",
            "priority": Notification.Priority.NORMAL,
        },
    )

    _send(
        subject=f"You earned the {new_label} badge on ReNest!",
        message=(
            f"Hi {user.display_name},\n\n"
            f"You've completed {completed} handoff{'s' if completed != 1 else ''}, "
            f"and that earns you the \"{new_label}\" badge.\n\n"
            f"Your badge is now visible on your listings and trust page:\n"
            f"{settings.APP_BASE_URL}/trust/me\n\n"
            "Keep rescuing: every item counts.\n\nReNest"
        ),
        recipient=user.email,
        user=user,
    )

    return new_milestone


def send_donation_receipt_email(listing: Listing) -> None:
    """Email the PDF donation receipt to the listing owner after a hub drop-off.

    Retry-safe (see `send_once`): raises EmailDeliveryError on a transient
    failure instead of swallowing it, so the task's retries actually happen.
    """
    owner = listing.owner
    if not getattr(owner, "email_notifications", True):
        return

    hub_name = listing.donation_hub.name if listing.donation_hub else "a campus donation hub"
    subject = f"Your ReNest donation receipt: {listing.title}"

    body = (
        f"Hi {owner.get_full_name() or 'there'},\n\n"
        f"Thank you for donating \"{listing.title}\" to {hub_name} through ReNest.\n\n"
        f"Your PDF receipt is attached. Keep it for your personal records.\n\n"
        f"The ReNest Team\n"
        f"{settings.APP_BASE_URL}"
    )
    attachments: list[tuple[str, bytes, str]] = []
    if listing.donation_receipt:
        listing.donation_receipt.open("rb")
        try:
            pdf_bytes = listing.donation_receipt.read()
        finally:
            listing.donation_receipt.close()
        attachments.append((f"receipt-{listing.pk}.pdf", pdf_bytes, "application/pdf"))
    # The unsubscribe footer and List-Unsubscribe headers come from the shared
    # message builder, like every other transactional email.
    send_once(
        kind="donation_receipt",
        obj=listing,
        subject=subject,
        message=body,
        recipient=owner.email,
        user=owner,
        attachments=attachments,
    )
