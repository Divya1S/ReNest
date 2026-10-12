from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Callable, Iterable, Optional

from django.db.models import QuerySet
from django.utils import timezone

from .models import Listing, ListingViewEvent, RoomScanSession, sync_system_move_out_tasks
from .notifications import sync_notifications_for_all_users
from .transactional_emails import send_handoff_reminder_emails

logger = logging.getLogger(__name__)


def expire_stale_listings(queryset: Optional[QuerySet[Listing]] = None) -> int:
    """Flip overdue listings to EXPIRED, minting each one a repost token.

    Goes row by row through Listing.refresh_status rather than a bulk update:
    the token is what the "your listing expired, repost it?" email links to,
    and a queryset .update() skips the model logic that creates it. The number
    of rows expiring in any one tick is small (they are all past their own
    deadline), so the per-row cost is not a concern.
    """
    base_queryset = queryset if queryset is not None else Listing.objects.all()
    candidates = base_queryset.filter(
        status__in=[Listing.Status.AVAILABLE, Listing.Status.RESERVED],
        available_until__lt=timezone.now(),
    )
    expired = 0
    for listing in candidates.iterator(chunk_size=200):
        listing.refresh_status(commit=True)
        expired += 1
    return expired


def sync_all_system_move_out_tasks(queryset: Optional[QuerySet[RoomScanSession]] = None) -> int:
    sessions = queryset if queryset is not None else RoomScanSession.objects.filter(is_demo=False)
    count = 0
    for session in sessions:
        count += sync_system_move_out_tasks(session)
    return count


BUMP_EMAIL_QUIET_DAYS = 3
BUMP_EMAIL_BATCH_SIZE = 100


def bump_email_candidates() -> QuerySet[Listing]:
    """Listings whose owner is due a "still available?" email, oldest first.

    The one statement of the rule, read by the task that sends the emails and
    by `send_bump_emails --dry-run`, which used to preview a different set:

      * available, not a demo, deadline not passed
      * posted at least BUMP_EMAIL_QUIET_DAYS days ago
      * no view in the last BUMP_EMAIL_QUIET_DAYS days
      * never bump-emailed before (once per listing, ever)
      * owner has not opted out of email (filtered here so opted-out owners
        cannot fill a batch and starve everyone behind them)
    """
    now = timezone.now()
    cutoff = now - timedelta(days=BUMP_EMAIL_QUIET_DAYS)
    return (
        Listing.objects.select_related("owner")
        .filter(
            status=Listing.Status.AVAILABLE,
            is_demo=False,
            available_until__gt=now,
            bump_emailed_at__isnull=True,
            created_at__lt=cutoff,
            owner__email_notifications=True,
        )
        .exclude(
            id__in=ListingViewEvent.objects.filter(viewed_at__gte=cutoff).values("listing_id")
        )
        .order_by("created_at", "id")
    )


def send_stale_listing_bump_emails() -> int:
    """Email owners of active listings that have had no views for 3 days.

    Delegates to the Celery task body so every way of running it shares one
    rule set (opt-out and List-Unsubscribe headers included). Returns the
    number of emails sent.
    """
    from .tasks import send_bump_emails_task

    return int(send_bump_emails_task())


def clear_expired_sessions() -> int:
    """Delete expired session rows (Django's `clearsessions`). Returns how many.

    Every visitor, signed in or not, gets a django_session row, and Django
    never removes expired ones on its own, so without this job the table only
    grows.
    """
    from django.conf import settings
    from django.core.management import call_command

    expired = 0
    if settings.SESSION_ENGINE.endswith((".db", ".cached_db")):
        from django.contrib.sessions.models import Session

        expired = Session.objects.filter(expire_date__lt=timezone.now()).count()
    call_command("clearsessions")
    return expired


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


# ── Scheduled job sets ───────────────────────────────────────────────────────
# This is the list of periodic work, not a copy of one. Every scheduler calls
# run_scheduled_jobs with a set name: Celery beat where there is a worker,
# manage.py run_maintenance or POST /api/internal/maintenance/ from an
# external cron where there is not. The jobs are plain callables so all three
# run them the same way, in-process.

def _job_sweep_stale_confirmations() -> Any:
    from .tasks import sweep_stale_confirmations_task

    return sweep_stale_confirmations_task()


def _job_check_saved_searches() -> Any:
    from .tasks import check_saved_searches

    return check_saved_searches()


def _job_refresh_trending_cache() -> Any:
    from .tasks import refresh_trending_cache

    refresh_trending_cache()
    return None


def _job_flush_quiet_queue() -> Any:
    from .tasks import flush_quiet_queue

    return flush_quiet_queue()


def _job_recompute_demand_forecast() -> Any:
    from .tasks import recompute_demand_forecast_task

    return recompute_demand_forecast_task()


def _job_recompute_completion_rates() -> Any:
    from .tasks import recompute_completion_rates_task

    return recompute_completion_rates_task()


def _job_weekly_campus_digest() -> Any:
    from .tasks import weekly_campus_digest_task

    return weekly_campus_digest_task()


def _job_dispatch_risk_events() -> dict[str, int]:
    from risk.services.events import dispatch_pending

    return dispatch_pending(limit=500)


def _job_purge_idempotency_keys() -> int:
    from risk.services.idempotency import purge_expired

    return purge_expired()


def _job_reconcile_risk_payments() -> dict[str, int]:
    from risk.services.payments import reconcile_uncaptured

    return reconcile_uncaptured()


SCHEDULED_JOB_SETS: dict[str, list[tuple[str, Callable[[], Any]]]] = {
    # Frequent (every 15-30 min): cheap, idempotent sweeps.
    "tick": [
        ("maintenance_cycle", run_maintenance_cycle),
        ("sweep_stale_confirmations", _job_sweep_stale_confirmations),
        ("check_saved_searches", _job_check_saved_searches),
        ("refresh_trending_cache", _job_refresh_trending_cache),
        ("flush_quiet_queue", _job_flush_quiet_queue),
        ("dispatch_risk_events", _job_dispatch_risk_events),
        ("purge_idempotency_keys", _job_purge_idempotency_keys),
        ("reconcile_risk_payments", _job_reconcile_risk_payments),
    ],
    "daily": [
        ("recompute_demand_forecast", _job_recompute_demand_forecast),
        ("clear_expired_sessions", clear_expired_sessions),
    ],
    "weekly": [
        ("recompute_completion_rates", _job_recompute_completion_rates),
        ("weekly_campus_digest", _job_weekly_campus_digest),
    ],
}


def run_scheduled_jobs(job_sets: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Run the named job sets ("tick", "daily", "weekly" or "all") in-process.

    Every job runs inside its own try/except so one failure never blocks the
    rest; the return value reports per-job success, a numeric result when the
    job produces one, and a short error message otherwise.
    """
    requested = list(job_sets)
    if "all" in requested:
        requested = list(SCHEDULED_JOB_SETS)
    results: dict[str, dict[str, Any]] = {}
    for job_set in dict.fromkeys(requested):
        for name, job in SCHEDULED_JOB_SETS.get(job_set, []):
            if name in results:
                continue
            try:
                value = job()
            except Exception as exc:  # noqa: BLE001 - reported to the caller, not swallowed
                logger.exception("Scheduled job %s failed", name)
                results[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}
            else:
                results[name] = {"ok": True, "result": value if isinstance(value, (int, dict)) else None}
    return results
