from __future__ import annotations

from datetime import timedelta
from typing import Any, Optional

from django.conf import settings
from django.core.mail import send_mail
from django.db.models import QuerySet
from django.utils import timezone

from .models import Listing, RoomScanSession, sync_system_move_out_tasks
from .notifications import sync_notifications_for_all_users
from .transactional_emails import send_handoff_reminder_emails


def expire_stale_listings(queryset: Optional[QuerySet[Listing]] = None) -> int:
    base_queryset = queryset if queryset is not None else Listing.objects.all()
    return base_queryset.filter(
        status__in=[Listing.Status.AVAILABLE, Listing.Status.RESERVED],
        available_until__lt=timezone.now(),
    ).update(status=Listing.Status.EXPIRED)


def sync_all_system_move_out_tasks(queryset: Optional[QuerySet[RoomScanSession]] = None) -> int:
    sessions = queryset if queryset is not None else RoomScanSession.objects.filter(is_demo=False)
    count = 0
    for session in sessions:
        count += sync_system_move_out_tasks(session)
    return count


def send_stale_listing_bump_emails() -> int:
    """
    Email owners of active listings with no new views in 3 days.
    Each listing is emailed at most once every 7 days.
    Returns the number of emails sent.
    """
    now = timezone.now()
    inactive_cutoff = now - timedelta(days=3)
    resend_cooldown = now - timedelta(days=7)

    candidates = (
        Listing.objects.select_related("owner")
        .filter(
            status__in=[Listing.Status.AVAILABLE, Listing.Status.RESERVED],
            is_demo=False,
            available_until__gt=now,
            updated_at__lt=inactive_cutoff,
        )
        .exclude(bump_emailed_at__gt=resend_cooldown)
        .exclude(view_events__viewed_at__gt=inactive_cutoff)
        .distinct()
    )

    sent = 0
    confirm_url = f"{settings.APP_BASE_URL}/my-listings"
    for listing in candidates:
        send_mail(
            subject=f'Is your "{listing.title}" still available?',
            message=(
                f"Hi {listing.owner.display_name},\n\n"
                f'Your listing "{listing.title}" hasn\'t had any views in the last 3 days. '
                f"Students are still looking — is it still available?\n\n"
                f"Log in to confirm or update it:\n{confirm_url}\n\n"
                "Thanks for being part of ReNest."
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[listing.owner.email],
            fail_silently=True,
        )
        listing.bump_emailed_at = now
        listing.save(update_fields=["bump_emailed_at"])
        sent += 1

    return sent


def run_maintenance_cycle() -> dict[str, Any]:
    expired = expire_stale_listings()
    synced_tasks = sync_all_system_move_out_tasks()
    notification_counts = sync_notifications_for_all_users()
    reminder_emails = send_handoff_reminder_emails()
    bump_emails = send_stale_listing_bump_emails()
    return {
        "expired": expired,
        "synced_tasks": synced_tasks,
        "reminder_emails": reminder_emails,
        "bump_emails": bump_emails,
        **notification_counts,
    }
