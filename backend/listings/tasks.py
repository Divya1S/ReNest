from __future__ import annotations

import logging

from typing import Any

from celery import shared_task

from .ai_client import (
    UNTRUSTED_TEXT_RULE,
    ai_available,
    coerce_int,
    generate_json,
    quote_untrusted,
)

logger = logging.getLogger(__name__)


def _retry_unless_eager(task: Any, exc: Exception, what: str) -> None:
    """Re-queue a failed task on a real worker; log and stop in eager mode.

    Without a broker (CELERY_TASK_ALWAYS_EAGER, the free-tier setup) a task
    runs inside the web request that triggered it, and self.retry re-runs it
    inline, immediately, for the whole retry ladder. That would hold the
    request open through every attempt, and because eager exceptions
    propagate, the final failure would surface as a 500 for an action that
    already succeeded. So eager mode gets one attempt.
    """
    if task.request.is_eager:
        logger.error("%s failed in eager mode and was not retried", what)
        return
    raise task.retry(exc=exc)


def _screen_and_store(listing: Any) -> str:
    """Pre-screen `listing` and persist the verdict. Returns the stored status.

    Two guards stop an automated verdict from overruling what it should not:

      * A flag whose reason was not written by the pre-screen is a staff
        decision. Only staff lift it; otherwise an owner could undo a
        rejection by editing one character and being re-screened.
      * A fail-open result (the provider was down) never lifts an existing
        flag. "Could not check" is not "checked and fine".

    The write is conditional on the status read before the model call, so a
    moderator who acts while that call is in flight is not overwritten.
    """
    from .models import Listing
    from .moderation import AUTO_FLAG_PREFIX, screen_listing

    flagged = Listing.ModerationStatus.FLAGGED
    previous_status = listing.moderation_status
    previous_reason = listing.moderation_flag_reason or ""
    if previous_status == flagged and not previous_reason.startswith(AUTO_FLAG_PREFIX):
        return str(previous_status)

    result = screen_listing(listing)
    if previous_status == flagged and not result.decided:
        return str(previous_status)
    if (result.status, result.reason) == (previous_status, previous_reason):
        return str(previous_status)

    changed = Listing.objects.filter(
        pk=listing.pk,
        moderation_status=previous_status,
        moderation_flag_reason=previous_reason,
    ).update(moderation_status=result.status, moderation_flag_reason=result.reason)
    if not changed:
        listing.refresh_from_db(fields=["moderation_status", "moderation_flag_reason"])
        return str(listing.moderation_status)
    listing.moderation_status = result.status
    listing.moderation_flag_reason = result.reason
    return result.status


@shared_task(bind=True, max_retries=3, default_retry_delay=30, name="listings.post_create_tasks")
def post_create_tasks(self: Any, listing_pk: int) -> None:
    """Run moderation pre-screen + AI match notifications after a listing is created."""
    from .models import Listing

    try:
        listing = Listing.objects.select_related("owner").get(pk=listing_pk)
        _screen_and_store(listing)
    except Exception as exc:
        logger.exception("post_create_tasks failed for listing %s", listing_pk)
        _retry_unless_eager(self, exc, f"post_create_tasks for listing {listing_pk}")
        return

    # Everything below is best-effort and isolated: retrying the whole task
    # for a failure here would repeat the moderation model call above.
    try:
        from .notifications import notify_matching_rescue_requests

        notify_matching_rescue_requests(listing)
    except Exception:
        logger.exception("Rescue-request matching failed for listing %s", listing_pk)

    try:
        # Phase 18: async quality hints via the AI model
        quality_coach_task.apply_async((listing_pk,), countdown=5)
    except Exception:
        logger.exception("Quality hints could not be scheduled for listing %s", listing_pk)
    try:
        # Phase 25: lexical search vector
        generate_listing_embedding.apply_async((listing_pk,), countdown=10)
    except Exception:
        logger.exception("Search vector could not be scheduled for listing %s", listing_pk)


@shared_task(name="listings.remoderate_listing")
def remoderate_listing_task(listing_pk: int) -> str:
    """Screen a listing again after its public text, price or photo changed.

    Creation is not the only moment content can become unacceptable: an owner
    can post something clean and edit it afterwards. The same edit also makes
    the stored search vector stale, so it is rebuilt here. Returns the
    moderation status now stored on the listing.
    """
    from .models import Listing

    try:
        listing = Listing.objects.select_related("owner").get(pk=listing_pk)
    except Listing.DoesNotExist:
        return "missing"
    status = _screen_and_store(listing)
    try:
        refresh_listing_search_vector(listing)
    except Exception:
        # Search recomputes a missing or stale vector on the fly, so this is
        # an optimisation lost, not a reason to fail the re-screen.
        logger.exception("Search vector refresh failed for listing %s", listing_pk)
    return status


@shared_task(bind=True, max_retries=3, default_retry_delay=30, name="listings.send_donation_receipt_email")
def send_donation_receipt_email_task(self: Any, listing_pk: int) -> None:
    """Send PDF donation receipt email to the listing owner."""
    try:
        from .models import Listing
        listing = Listing.objects.select_related("owner", "donation_hub").get(pk=listing_pk)
        from .transactional_emails import send_donation_receipt_email
        send_donation_receipt_email(listing)
    except Exception as exc:
        logger.exception("send_donation_receipt_email_task failed for listing %s", listing_pk)
        _retry_unless_eager(self, exc, f"Donation receipt email for listing {listing_pk}")


@shared_task(bind=True, max_retries=3, default_retry_delay=30, name="listings.send_reservation_confirmed_email")
def send_reservation_confirmed_email_task(self: Any, reservation_pk: int) -> None:
    """Email both parties that a reservation was confirmed.

    Safe to retry: each recipient's email is claimed with a sent marker
    (transactional_emails.send_once), so a retry caused by one failed
    delivery does not send the other party a second copy.
    """
    try:
        from .models import Reservation
        reservation = Reservation.objects.select_related(
            "listing", "listing__owner", "claimant"
        ).get(pk=reservation_pk)
        from .transactional_emails import send_reservation_confirmed_email
        send_reservation_confirmed_email(reservation)
    except Exception as exc:
        logger.exception("send_reservation_confirmed_email_task failed for reservation %s", reservation_pk)
        _retry_unless_eager(self, exc, f"Confirmation email for reservation {reservation_pk}")


@shared_task(bind=True, max_retries=3, default_retry_delay=30, name="listings.send_reservation_completed_email")
def send_reservation_completed_email_task(self: Any, reservation_pk: int) -> None:
    """Send the feedback request and impact summary. Safe to retry (see above)."""
    try:
        from .models import Reservation
        reservation = Reservation.objects.select_related(
            "listing", "listing__owner", "claimant"
        ).get(pk=reservation_pk)
        from .transactional_emails import (
            EmailDeliveryError,
            send_handoff_impact_notification,
            send_reservation_completed_email,
        )

        # Run both steps even if the first one's mail delivery fails: the
        # second also writes the in-app impact notification and the milestone,
        # which must not wait on a mail server (or be lost where there is no
        # retry). The failure is re-raised afterwards so the task retries.
        deferred: EmailDeliveryError | None = None
        for step in (send_reservation_completed_email, send_handoff_impact_notification):
            try:
                step(reservation)
            except EmailDeliveryError as exc:
                deferred = deferred or exc
        if deferred is not None:
            raise deferred
    except Exception as exc:
        logger.exception("send_reservation_completed_email_task failed for reservation %s", reservation_pk)
        _retry_unless_eager(self, exc, f"Completion email for reservation {reservation_pk}")


@shared_task(name="listings.send_handoff_reminder_emails")
def send_handoff_reminder_emails_task() -> int:
    """Scheduled job (tick set): sends the 24h handoff reminders."""
    from .transactional_emails import send_handoff_reminder_emails
    return send_handoff_reminder_emails()


@shared_task(name="listings.expire_listings")
def expire_listings_task() -> int:
    """Scheduled job (tick set): transitions overdue listings to EXPIRED.

    Delegates to listings.ops so the beat schedule, the maintenance command and
    the cron endpoint all expire listings the same way (including minting the
    repost token the "your listing expired" email links to).
    """
    from .ops import expire_stale_listings

    return expire_stale_listings()


@shared_task(name="listings.send_bump_emails")
def send_bump_emails_task() -> int:
    """
    Scheduled daily. Emails owners of listings that nobody has viewed for
    3 days, at most once per listing and 100 per run. The selection rule lives
    in ops.bump_email_candidates so `send_bump_emails --dry-run` previews
    exactly this set.
    """
    from django.utils import timezone
    from .ops import BUMP_EMAIL_BATCH_SIZE, bump_email_candidates
    from .transactional_emails import _send
    from django.conf import settings

    from .models import Listing

    sent = 0
    for listing in bump_email_candidates()[:BUMP_EMAIL_BATCH_SIZE]:
        # Claim before sending (a conditional UPDATE), so the beat schedule
        # and the maintenance cron overlapping cannot both email the owner.
        claimed = Listing.objects.filter(pk=listing.pk, bump_emailed_at__isnull=True).update(
            bump_emailed_at=timezone.now()
        )
        if not claimed:
            continue
        reactivate_url = f"{settings.APP_BASE_URL}/my-listings"
        _send(
            subject=f'Is "{listing.title}" still available?',
            message=(
                f"Hi {listing.owner.display_name},\n\n"
                f'Your listing "{listing.title}" hasn\'t had any views in 3 days.\n\n'
                f"If it\'s still available, visit your listings page to confirm. "
                f"This will bump it back to the top of the feed:\n{reactivate_url}\n\n"
                "If it\'s already been picked up, mark it as collected so others stop seeing it.\n\n"
                "Thanks for using ReNest!"
            ),
            recipient=listing.owner.email,
            user=listing.owner,
        )
        sent += 1

    return sent


@shared_task(name="listings.recompute_category_affinity")
def recompute_category_affinity_task(user_pk: int) -> dict:
    """
    Compute a per-user category affinity vector from the last 50 interactions
    and store it as a JSONField on the user.  Called after each view/save/reserve
    interaction once the user has >= 10 interactions.

    Result shape: { "storage": 0.6, "lighting": 0.3, ... }
    """
    from collections import Counter
    from django.contrib.auth import get_user_model
    from .models import ListingInteractionEvent

    User = get_user_model()

    interactions = list(
        ListingInteractionEvent.objects.filter(user_id=user_pk)
        .select_related("listing")
        .order_by("-created_at")[:50]
        .values_list("listing__category", "action")
    )

    if not interactions:
        return {}

    # Weight: reserve=3, save=2, view=1
    weights = {"reserve": 3, "save": 2, "view": 1}
    counts: Counter = Counter()
    for category, action in interactions:
        if category:
            counts[category] += weights.get(action, 1)

    total = sum(counts.values()) or 1
    affinity = {cat: round(score / total, 3) for cat, score in counts.most_common()}

    User.objects.filter(pk=user_pk).update(category_affinity=affinity)
    return affinity


RESCUE_SUGGESTION_CANDIDATES = 20
RESCUE_SUGGESTION_LIMIT = 3

_MATCHED_IDS_SCHEMA = {"type": "ARRAY", "items": {"type": "INTEGER"}}


def parse_matched_ids(raw: Any, allowed_ids: list[int], *, limit: int) -> list[int]:
    """The candidate ids the model picked, in its order, without repeats.

    Anything that is not a whole number naming a listing we actually offered
    is dropped: the answer is model output, not a trusted list of keys.
    """
    if not isinstance(raw, list):
        return []
    allowed = set(allowed_ids)
    picked: list[int] = []
    for item in raw:
        listing_id = coerce_int(item)
        if listing_id is None or listing_id not in allowed or listing_id in picked:
            continue
        picked.append(listing_id)
        if len(picked) >= limit:
            break
    return picked


@shared_task(name="listings.match_rescue_suggestions")
def match_rescue_suggestions_task(user_pk: int) -> list:
    """
    For a user with an open RescueRequest, ask the model which of the newest
    listings they are allowed to see, on their own campus, fit the request.
    Returns up to three listing ids, best first, and records a notification
    when there is at least one.
    """
    from django.contrib.auth import get_user_model

    from .models import Listing, Notification, RescueRequest
    from .notifications import same_campus_q
    from .views.helpers import marketplace_listing_queryset

    user = get_user_model().objects.filter(pk=user_pk, is_active=True).first()
    if user is None:
        return []

    rescue = (
        RescueRequest.objects.filter(seeker_id=user_pk, status=RescueRequest.Status.OPEN)
        .order_by("-created_at")
        .first()
    )
    if rescue is None:
        return []

    # marketplace_listing_queryset is the one definition of what this person
    # may discover (not demo, not flagged, not past its deadline, no block in
    # either direction). The campus filter is repeated explicitly because that
    # function leaves users without a campus unscoped.
    listings = list(
        marketplace_listing_queryset(user)
        .filter(same_campus_q("owner", user), status=Listing.Status.AVAILABLE)
        .exclude(owner_id=user_pk)
        .order_by("-created_at")[:RESCUE_SUGGESTION_CANDIDATES]
        .values("id", "title", "category", "description")
    )

    if not listings:
        return []

    if not ai_available():
        return []

    listing_lines = "\n".join(
        f"- id {row['id']} [{row['category']}] title {quote_untrusted(row['title'], limit=140)}; "
        f"details {quote_untrusted(row['description'], limit=80)}"
        for row in listings
    )
    prompt = (
        f"A student is looking for: {quote_untrusted(rescue.title, limit=140)} ({rescue.category})\n"
        f"Their description: {quote_untrusted(rescue.description, limit=200)}\n\n"
        f"Here are {len(listings)} available listings:\n{listing_lines}\n\n"
        f"{UNTRUSTED_TEXT_RULE}\n"
        "Return a JSON array holding the ids of the listings that are a GOOD match, best first, "
        f"at most {RESCUE_SUGGESTION_LIMIT}. Return [] when none fit."
    )

    try:
        raw = generate_json(prompt, max_tokens=60, response_schema=_MATCHED_IDS_SCHEMA)
    except Exception:
        logger.exception("match_rescue_suggestions_task failed for user %s", user_pk)
        return []

    by_id = {row["id"]: row for row in listings}
    matched_ids = parse_matched_ids(raw, list(by_id), limit=RESCUE_SUGGESTION_LIMIT)

    if matched_ids:
        titles = ", ".join(by_id[listing_id]["title"] for listing_id in matched_ids)
        Notification.objects.get_or_create(
            dedupe_key=f"rescue_suggest:{rescue.pk}:{matched_ids[0]}",
            defaults={
                "user_id": user_pk,
                "type": Notification.Type.AI_MATCH,
                "title": (
                    f"We found {len(matched_ids)} listing{'s' if len(matched_ids) > 1 else ''} "
                    "matching your rescue request"
                ),
                "body": f'Listings that may match your request for "{rescue.title}": {titles}',
                "link_path": "/match-center",
                "priority": Notification.Priority.NORMAL,
            },
        )

    return matched_ids


def _expire_unresolved_reservation(reservation: Any, now: Any) -> bool:
    """Move one stale CONFIRMED reservation to EXPIRED_UNRESOLVED and free its listing.

    Returns False, changing nothing, when the row is no longer CONFIRMED and
    undisputed. The caller's copy was read a moment earlier; the owner may
    have completed the handoff or a dispute may have been opened since, and
    an unconditional update would overwrite that newer state.
    """
    from django.db import transaction

    from .models import Dispute, Listing, Reservation
    from .ops import expire_stale_listings

    with transaction.atomic():
        changed = (
            Reservation.objects.filter(pk=reservation.pk, status=Reservation.Status.CONFIRMED)
            .exclude(disputes__status=Dispute.Status.OPEN)
            .update(status=Reservation.Status.EXPIRED_UNRESOLVED, updated_at=now)
        )
        if not changed:
            return False

        # Re-open the listing, again only if it is still RESERVED in the
        # database rather than in the copy loaded earlier. Past its own
        # deadline it is expired instead, through the shared expiry path so
        # it gets a repost token like every other expired listing.
        if reservation.listing.available_until < now:
            expire_stale_listings(Listing.objects.filter(pk=reservation.listing_id))
        else:
            Listing.objects.filter(
                pk=reservation.listing_id, status=Listing.Status.RESERVED
            ).update(status=Listing.Status.AVAILABLE, updated_at=now)
    return True


@shared_task(name="listings.sweep_stale_confirmations")
def sweep_stale_confirmations_task() -> int:
    """
    Scheduled sweep. Finds CONFIRMED reservations whose pickup is more than
    48h in the past with no completion or dispute, moves them to
    EXPIRED_UNRESOLVED, and notifies both parties with resolution options.
    """
    from datetime import timedelta
    from django.db.models import Q
    from django.utils import timezone
    from .models import Dispute, Notification, Reservation
    from .views.dashboard import invalidate_user_dashboard_cache

    now = timezone.now()
    cutoff = now - timedelta(hours=48)

    # "Stale" means the pickup itself is 48h in the past, not merely that the
    # row has not been touched. A handoff confirmed today for next Saturday
    # must survive this sweep.
    stale = list(
        Reservation.objects.filter(status=Reservation.Status.CONFIRMED)
        .filter(
            Q(confirmed_slot__lt=cutoff)
            | Q(confirmed_slot__isnull=True, listing__available_until__lt=cutoff)
        )
        .exclude(disputes__status=Dispute.Status.OPEN)
        .select_related("listing", "listing__owner", "claimant")[:100]
    )

    updated = 0
    for reservation in stale:
        if not _expire_unresolved_reservation(reservation, now):
            continue

        handoff_url = f"/handoffs/{reservation.pk}"
        for user, role in [
            (reservation.claimant, "claimant"),
            (reservation.listing.owner, "owner"),
        ]:
            Notification.objects.get_or_create(
                dedupe_key=f"stale_confirm:{reservation.pk}:{role}",
                defaults=dict(
                    user=user,
                    type=Notification.Type.RESERVATION,
                    title="Handoff unresolved: what happened?",
                    body=(
                        f'The confirmed handoff for "{reservation.listing.title}" passed 48h ago '
                        "with no completion. Enter the pickup PIN to complete it, open a dispute, or cancel."
                    ),
                    link_path=handoff_url,
                    priority=Notification.Priority.HIGH,
                ),
            )
        # Both dashboards list this reservation under its old status until
        # their cached payload is dropped.
        invalidate_user_dashboard_cache(reservation.claimant_id)
        invalidate_user_dashboard_cache(reservation.listing.owner_id)
        updated += 1

    return updated


# ─── Phase 18: Platform Intelligence ────────────────────────────────────────


def continuous_week_index(moment: Any) -> int:
    """Whole weeks since a fixed Monday, for a date or datetime.

    The ISO week number restarts every January (52 or 53 is followed by 1), so
    a regression fitted on it sees time jump back a year at each new year.
    This index rises by exactly one per ISO week across that boundary.
    """
    # Proleptic ordinal 1 (0001-01-01) is a Monday, so this is Monday-aligned
    # like ISO weeks are.
    return int((moment.toordinal() - 1) // 7)


@shared_task(name="listings.recompute_demand_forecast")
def recompute_demand_forecast_task() -> dict:
    """
    Scheduled (daily job set). Aggregates ListingViewEvent +
    ListingInteractionEvent by category and week, fits a linear regression per
    category over a continuous week index to predict next-week demand, and
    upserts DemandForecast rows.
    """
    import datetime
    from collections import defaultdict

    from django.utils import timezone

    from .models import DemandForecast, ListingInteractionEvent, ListingViewEvent

    now = timezone.now()
    cutoff = now - datetime.timedelta(days=365)

    # Aggregate view counts per (category, continuous week index).
    view_counts: dict = defaultdict(int)
    for evt in (
        ListingViewEvent.objects
        .filter(viewed_at__gte=cutoff)
        .select_related("listing")
        .values("listing__category", "viewed_at")
    ):
        cat = evt["listing__category"] or "other"
        view_counts[(cat, continuous_week_index(evt["viewed_at"]))] += 1

    save_counts: dict = defaultdict(int)
    for interaction in (
        ListingInteractionEvent.objects
        .filter(action="save", created_at__gte=cutoff)
        .select_related("listing")
        .values("listing__category", "created_at")
    ):
        cat = interaction["listing__category"] or "other"
        save_counts[(cat, continuous_week_index(interaction["created_at"]))] += 1

    # Fit a per-category linear regression.
    try:
        from sklearn.linear_model import LinearRegression
        import numpy as np
    except ImportError:
        logger.warning("scikit-learn not available; skipping demand forecast")
        return {}

    categories = {k[0] for k in view_counts} | {k[0] for k in save_counts}
    next_week = now + datetime.timedelta(weeks=1)
    next_iso = next_week.isocalendar()
    next_index = continuous_week_index(next_week)
    results = {}

    for cat in categories:
        weeks_data = sorted({week for (c, week) in view_counts if c == cat})
        if len(weeks_data) < 3:
            continue

        X = np.array([[week] for week in weeks_data])
        y_views = np.array([view_counts.get((cat, week), 0) for week in weeks_data], dtype=float)
        y_saves = np.array([save_counts.get((cat, week), 0) for week in weeks_data], dtype=float)

        reg_v = LinearRegression().fit(X, y_views)
        reg_s = LinearRegression().fit(X, y_saves)

        pred_views = max(0.0, float(reg_v.predict([[next_index]])[0]))
        pred_saves = max(0.0, float(reg_s.predict([[next_index]])[0]))

        # is_peak: at or above 75th percentile for this category
        p75 = float(np.percentile(y_views, 75)) if len(y_views) else 0.0
        is_peak = pred_views >= p75

        DemandForecast.objects.update_or_create(
            category=cat,
            week_number=next_iso.week,
            year=next_iso.year,
            defaults={
                "predicted_views": round(pred_views, 1),
                "predicted_saves": round(pred_saves, 1),
                "is_peak": is_peak,
            },
        )
        results[cat] = {"predicted_views": pred_views, "is_peak": is_peak}

    return results


QUALITY_HINT_FIELDS = frozenset({"title", "description", "price", "pickup_zone", "condition"})
QUALITY_HINT_LIMIT = 2

_QUALITY_HINTS_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "suggestion": {"type": "STRING"},
            "field": {"type": "STRING", "enum": sorted(QUALITY_HINT_FIELDS)},
        },
        "required": ["suggestion", "field"],
    },
}


def clean_quality_hints(raw: Any) -> list[dict[str, str]]:
    """Keep well-formed hints only. They are stored and rendered to the owner."""
    hints: list[dict[str, str]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("suggestion"), str):
            continue
        suggestion = " ".join(item["suggestion"].split())[:240]
        if not suggestion:
            continue
        field = item.get("field")
        # An unrecognised field is blanked rather than guessed: the hint text
        # is still useful, a wrong label on it is not.
        hints.append({"suggestion": suggestion, "field": field if field in QUALITY_HINT_FIELDS else ""})
        if len(hints) >= QUALITY_HINT_LIMIT:
            break
    return hints


@shared_task(name="listings.quality_coach")
def quality_coach_task(listing_pk: int) -> list:
    """
    Phase 18: fires after listing creation.
    Calls the AI model to produce up to 2 specific improvement suggestions for
    the listing and stores them in listing.quality_hints (JSON).

    Deliberately not a retrying task. The hints are a nicety, the client
    helper already retries a rate-limited or overloaded call once, and a
    further task-level retry would spend scarce model quota on them.
    """
    from .models import Listing

    try:
        listing = Listing.objects.get(pk=listing_pk)
    except Listing.DoesNotExist:
        return []

    if not ai_available():
        return []

    prompt = (
        "A student posted this dorm-item listing:\n"
        f"Title: {quote_untrusted(listing.title, limit=140)}\n"
        f"Category: {listing.category}\n"
        f"Condition: {listing.condition}\n"
        f"Description: {quote_untrusted(listing.description, limit=300)}\n"
        f"Price: {listing.price_type} {listing.price_amount}\n"
        f"Pickup zone: {quote_untrusted(listing.pickup_zone, limit=140)}\n\n"
        f"{UNTRUSTED_TEXT_RULE}\n"
        "Give exactly 2 short, specific suggestions to improve this listing's chances of being claimed "
        "in under 24 hours. Be direct. Output valid JSON array only: "
        '[{"suggestion": "...", "field": "title|description|price|pickup_zone|condition"}]'
    )

    try:
        hints = clean_quality_hints(
            generate_json(prompt, max_tokens=250, response_schema=_QUALITY_HINTS_SCHEMA)
        )
    except Exception:
        logger.exception("quality_coach_task failed for listing %s", listing_pk)
        return []

    if hints:
        Listing.objects.filter(pk=listing_pk).update(quality_hints=hints)

    return hints


@shared_task(name="listings.recompute_completion_rates")
def recompute_completion_rates_task() -> int:
    """
    Phase 18: fires weekly on Monday morning.
    Recomputes completion_rate for all users with >= 5 reservations as owner.
    Owners with completion_rate < 0.3 are suppressed from browse.
    """
    from django.contrib.auth import get_user_model
    from django.db.models import Count, Q

    from .models import Reservation

    User = get_user_model()

    qs = (
        Reservation.objects.filter(listing__is_demo=False)
        .values("listing__owner_id")
        .annotate(
            total=Count("id"),
            completed=Count("id", filter=Q(status=Reservation.Status.COMPLETED)),
        )
        .filter(total__gte=5)
    )

    updated = 0
    for row in qs:
        owner_id = row["listing__owner_id"]
        rate = row["completed"] / row["total"] if row["total"] else None
        User.objects.filter(pk=owner_id).update(completion_rate=rate)
        updated += 1

    return updated


@shared_task(name="listings.weekly_campus_digest")
def weekly_campus_digest_task() -> int:
    """Email each campus manager a weekly summary of their campus activity."""
    from datetime import timedelta

    from django.contrib.auth import get_user_model
    from django.db.models import Count, Sum
    from django.utils import timezone

    from accounts.models import Campus
    from .models import Listing, Reservation
    from .transactional_emails import _send

    User = get_user_model()
    since = timezone.now() - timedelta(days=7)
    sent = 0

    for campus in Campus.objects.filter(active=True, onboarding_status="approved"):
        managers = User.objects.filter(
            campus=campus, is_campus_manager=True, email_notifications=True
        )
        if not managers.exists():
            continue

        week_listings = Listing.objects.filter(owner__campus=campus, created_at__gte=since).count()
        week_claimed = Reservation.objects.filter(
            listing__owner__campus=campus,
            status=Reservation.Status.COMPLETED,
            updated_at__gte=since,
        ).count()
        week_value = float(
            Reservation.objects.filter(
                listing__owner__campus=campus,
                status=Reservation.Status.COMPLETED,
                updated_at__gte=since,
            ).aggregate(v=Sum("listing__estimated_retail_value"))["v"] or 0
        )
        active_users = User.objects.filter(
            campus=campus, listings__created_at__gte=since
        ).distinct().count()

        top_cat_row = (
            Listing.objects.filter(owner__campus=campus, created_at__gte=since)
            .values("category")
            .annotate(n=Count("id"))
            .order_by("-n")
            .first()
        )
        top_cat = top_cat_row["category"].title() if top_cat_row else "—"

        from django.conf import settings as _settings
        frontend = _settings.APP_BASE_URL
        subject = f"[ReNest] {campus.name} weekly digest"
        body = (
            f"Hi,\n\n"
            f"Here's what happened on ReNest at {campus.name} this week:\n\n"
            f"  Listings posted:     {week_listings}\n"
            f"  Items claimed:       {week_claimed}\n"
            f"  Value rescued:       ${week_value:,.0f}\n"
            f"  Active contributors: {active_users}\n"
            f"  Top category:        {top_cat}\n\n"
            f"Open your dashboard: {frontend}/analytics\n\n"
            f"The ReNest team"
        )

        for manager in managers:
            try:
                # _send carries the List-Unsubscribe headers and the opt-out check.
                _send(subject=subject, message=body, recipient=manager.email, user=manager)
                sent += 1
            except Exception:
                logger.exception("weekly_campus_digest_task: failed to email %s", manager.email)

        campus.last_digest_sent_at = timezone.now()
        campus.save(update_fields=["last_digest_sent_at"])

    return sent


# ---------------------------------------------------------------------------
# Phase 25: lexical search, saved-search alerts, trending
# ---------------------------------------------------------------------------

def refresh_listing_search_vector(listing: Any) -> None:
    """Rebuild and store the listing's lexical search vector.

    The vectoriser lives in views/search.py and is the same one the search
    endpoint applies to queries; this used to be a second, slightly different
    copy inlined here.
    """
    from .models import Listing
    from .views.search import build_search_vector

    payload = build_search_vector(listing)
    # A queryset update: saving the instance would write back whatever other
    # fields this (possibly stale) copy holds and bump updated_at.
    Listing.objects.filter(pk=listing.pk).update(embedding=payload)
    listing.embedding = payload


@shared_task(name="listings.generate_listing_embedding")
def generate_listing_embedding(listing_pk: int) -> None:
    """Store the lexical (hashing trick) search vector for a new listing.

    The task keeps its historical name, but the vector is a bag-of-words
    hash, not a model embedding. Search recomputes a missing or stale vector
    on the fly, so a failure here costs speed, not correctness.
    """
    try:
        from .models import Listing

        refresh_listing_search_vector(Listing.objects.get(pk=listing_pk))
    except Exception:
        logger.exception("generate_listing_embedding failed for listing %s", listing_pk)


def filter_by_keyword(queryset: Any, keyword: str) -> Any:
    """The text match browse applies for ?search=, so an alert counts what its link shows.

    Mirrors ListingListCreateView: full-text search over title, description
    and pickup zone on PostgreSQL, a case-insensitive substring match on the
    same three fields elsewhere.
    """
    from django.db import connection
    from django.db.models import Q

    if connection.vendor == "postgresql":
        from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector

        vector = (
            SearchVector("title", weight="A")
            + SearchVector("description", weight="B")
            + SearchVector("pickup_zone", weight="C")
        )
        query = SearchQuery(keyword, search_type="websearch")
        return queryset.annotate(keyword_rank=SearchRank(vector, query)).filter(keyword_rank__gt=0)
    return queryset.filter(
        Q(title__icontains=keyword)
        | Q(description__icontains=keyword)
        | Q(pickup_zone__icontains=keyword)
    )


@shared_task(name="listings.check_saved_searches")
def check_saved_searches() -> int:
    """
    Compare new listings (created since last_notified_at) against each user's
    saved searches and push a notification if matches exist. Scheduled in the
    tick job set.

    Matches are drawn from marketplace_listing_queryset, the same definition
    of "what this person may discover" that browse uses, so an alert can never
    point at a demo, flagged, expired or blocked-owner listing, or at one on
    another campus.
    """
    from urllib.parse import urlencode

    from django.utils import timezone as tz

    from .models import Listing, SavedSearch
    from .views.helpers import marketplace_listing_queryset
    from .webpush import send_push_to_user

    fired = 0
    for search in SavedSearch.objects.select_related("user").filter(user__is_active=True).iterator():
        cutoff = search.last_notified_at or search.created_at
        # The window is (cutoff, checked_at]. Stamping "now" after the query
        # instead would silently skip anything posted while it ran.
        checked_at = tz.now()
        qs = (
            marketplace_listing_queryset(search.user)
            .filter(
                status=Listing.Status.AVAILABLE,
                created_at__gt=cutoff,
                created_at__lte=checked_at,
            )
            .exclude(owner_id=search.user_id)
        )
        if search.category:
            qs = qs.filter(category=search.category)
        if search.keyword:
            qs = filter_by_keyword(qs, search.keyword)
        if search.price_type:
            qs = qs.filter(price_type=search.price_type)

        count = qs.count()
        if count:
            params = {
                "category": search.category,
                "search": search.keyword,
                "price_type": search.price_type,
            }
            try:
                send_push_to_user(
                    search.user,
                    title="New listing matches your search",
                    body=f"{count} new item{'s' if count > 1 else ''} match \"{search.label or search.keyword or search.category}\"",
                    # urlencode: a keyword containing "&" or a space would
                    # otherwise produce a link that opens a different search.
                    url="/browse?" + urlencode({k: v for k, v in params.items() if v}),
                    notification_type="ai_match",
                )
                fired += 1
            except Exception:
                logger.exception("check_saved_searches: push failed for user %s", search.user_id)
        search.last_notified_at = checked_at
        search.save(update_fields=["last_notified_at"])

    return fired


@shared_task(name="listings.refresh_trending_cache")
def refresh_trending_cache() -> None:
    """Pre-warm the trending cache for every active campus.

    Writes exactly what the endpoint would compute on a miss: campus-level
    rows of listing ids and view counts drawn from publicly visible listings
    (so never a flagged one). Per-user rules are applied when it is read.
    """
    from django.core.cache import cache

    from accounts.models import Campus

    from .views.search import (
        TRENDING_CACHE_TTL_SECONDS,
        compute_trending_rows,
        trending_cache_key,
    )

    # None is the "all campuses" strip shown to users who have not joined one.
    for campus in [None, *Campus.objects.filter(active=True)]:
        cache.set(
            trending_cache_key(campus),
            compute_trending_rows(campus),
            timeout=TRENDING_CACHE_TTL_SECONDS,
        )


@shared_task(name="listings.deliver_webhook", bind=True, max_retries=3)
def deliver_webhook(self: Any, delivery_pk: int) -> None:
    """
    Phase 27: Deliver a single WebhookDelivery to its endpoint URL.
    Signs the payload with HMAC-SHA256 and retries up to 3 times with exponential back-off.
    """
    import hashlib
    import hmac as _hmac
    import json as _json

    import requests as _requests
    from django.utils import timezone as tz

    from .models import WebhookDelivery

    try:
        delivery = WebhookDelivery.objects.select_related("endpoint").get(pk=delivery_pk)
    except WebhookDelivery.DoesNotExist:
        return

    if delivery.status == WebhookDelivery.Status.DELIVERED:
        return

    payload_bytes = _json.dumps(delivery.payload, separators=(",", ":")).encode()
    sig = _hmac.new(
        delivery.endpoint.secret.encode(),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()

    delivery.attempts += 1
    delivery.last_attempted_at = tz.now()

    try:
        # Checked again at send time, not only at registration: the host was
        # public when the partner registered it, but its DNS record can be
        # repointed at an internal address afterwards.
        from .net_safety import public_https_url_error

        url_error = public_https_url_error(delivery.endpoint.url, noun="webhook")
        if url_error:
            raise ValueError(url_error)
        resp = _requests.post(
            delivery.endpoint.url,
            data=payload_bytes,
            headers={
                "Content-Type": "application/json",
                "X-ReNest-Signature": f"sha256={sig}",
                "X-ReNest-Event": delivery.event_type,
            },
            timeout=10,
            # A redirect could point at an internal address the registration
            # check already rejected; never follow one.
            allow_redirects=False,
        )
        delivery.response_status = resp.status_code
        if 200 <= resp.status_code < 300:
            delivery.status = WebhookDelivery.Status.DELIVERED
        else:
            delivery.status = WebhookDelivery.Status.FAILED
            raise Exception(f"Non-2xx response: {resp.status_code}")
    except Exception as exc:
        delivery.status = WebhookDelivery.Status.FAILED
        delivery.save(update_fields=["attempts", "last_attempted_at", "response_status", "status"])
        # In eager mode (no broker) self.retry runs the task inline, which would
        # block the caller for the whole back-off ladder; log and stop instead.
        if self.request.is_eager or self.request.retries >= self.max_retries:
            logger.error(
                "Webhook delivery %s permanently failed after %d attempts",
                delivery_pk,
                delivery.attempts,
            )
            return
        raise self.retry(exc=exc, countdown=60 * (2 ** delivery.attempts))

    delivery.save(update_fields=["attempts", "last_attempted_at", "response_status", "status"])


@shared_task(name="listings.flush_quiet_queue")
def flush_quiet_queue() -> int:
    """
    Phase 26: dispatch push notifications that were held during a user's quiet hours.
    Runs with the tick job set; only fires payloads whose scheduled epoch has passed.
    """
    from django.contrib.auth import get_user_model as _get_user_model

    from .webpush import flush_quiet_queue_for_user

    User = _get_user_model()
    total = 0
    for user in User.objects.filter(is_active=True).only("id"):
        total += flush_quiet_queue_for_user(user)
    return total


def ai_detect_cache_key(session_pk: int, image_pk: int) -> str:
    return f"ai-detect:{session_pk}:{image_pk}"


AI_DRAFT_NOTE = "AI-detected from room photo."
# Two boxes on the same photo that overlap this much are the same object.
_SAME_ITEM_OVERLAP = 0.5
# Scan sessions live for the weeks around move-out; a month covers them.
_AI_DRAFT_RECORD_TTL_SECONDS = 30 * 24 * 3600


def _ai_draft_record_key(image_pk: int) -> str:
    return f"ai-detect-drafts:{image_pk}"


def draft_fingerprint(draft: Any) -> str:
    """Digest of everything on a draft that a user action can change."""
    import hashlib
    import json
    from decimal import Decimal

    from .ai_detect import clamp

    box = draft.hotspot_box if isinstance(draft.hotspot_box, dict) else {}
    parts = [
        draft.title,
        draft.category,
        draft.condition,
        draft.price_type,
        f"{Decimal(str(draft.price_amount or 0)):.2f}",
        f"{Decimal(str(draft.estimated_retail_value or 0)):.2f}",
        draft.triage_status,
        draft.notes or "",
        draft.preset_key or "",
        str(draft.donation_hub_id or ""),
        str(draft.linked_listing_id or ""),
        # Rounded, so a float that came back from the database with a
        # different last digit does not read as an edit.
        json.dumps({key: round(clamp(box.get(key)), 6) for key in ("x", "y", "width", "height")}),
    ]
    return hashlib.sha256("\x1f".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:32]


def is_untouched_ai_draft(draft: Any, recorded: dict[str, str]) -> bool:
    """True only when a draft is provably still what detection wrote.

    The model has no "edited" column, so each run records a fingerprint of
    every draft it creates and a later run compares. The default is to keep:
    a draft with no record (the cache entry expired or was lost in a restart)
    or with any difference belongs to the user and is never replaced. Losing
    the record can leave an old suggestion in place; it cannot lose an edit.
    """
    return recorded.get(str(draft.pk)) == draft_fingerprint(draft)


def _is_same_item(suggestion: dict[str, Any], draft: Any) -> bool:
    from .ai_detect import box_overlap

    same_title = suggestion["title"].strip().lower() == (draft.title or "").strip().lower()
    return same_title or box_overlap(suggestion["hotspot_box"], draft.hotspot_box) >= _SAME_ITEM_OVERLAP


@shared_task(bind=True, name="listings.run_ai_detection")
def run_ai_detection(self: Any, session_pk: int, image_pk: int) -> None:
    """
    Run vision detection for a scan image in the background and create
    hotspot drafts. Progress is reported through the cache so the API can be
    polled. Never raises, so eager (dev) execution cannot 500 the request.

    Idempotent per image: a re-run replaces the drafts the previous run made
    that nobody has touched, keeps every draft the user edited, triaged or
    published, and does not add a second draft for an item a kept draft
    already covers. Running it twice therefore leaves one set, not two.
    """
    from django.core.cache import cache

    key = ai_detect_cache_key(session_pk, image_pk)
    try:
        from .models import RoomScanImage, RoomScanSession

        scan_session = RoomScanSession.objects.get(pk=session_pk)
        scan_image = RoomScanImage.objects.get(pk=image_pk, scan_session=scan_session)

        from .ai_detect import detect_items_in_image, sanitise_suggestions

        # Pass the FieldFile, not .path: remote storage (S3/R2) has no path.
        # sanitise_suggestions also enforces the item cap.
        suggestions = sanitise_suggestions(detect_items_in_image(scan_image.image))

        from django.db import transaction

        from .models import RoomScanItemDraft

        record_key = _ai_draft_record_key(scan_image.pk)
        created = []
        with transaction.atomic():
            # Lock the image row so two runs for the same photo take turns:
            # the second then sees (and replaces) what the first one wrote.
            RoomScanImage.objects.select_for_update().get(pk=scan_image.pk)

            recorded = cache.get(record_key)
            if not isinstance(recorded, dict):
                recorded = {}
            kept = []
            for draft in RoomScanItemDraft.objects.filter(scan_session=scan_session, source_image=scan_image):
                if is_untouched_ai_draft(draft, recorded):
                    draft.delete()
                else:
                    kept.append(draft)

            for suggestion in suggestions:
                if any(_is_same_item(suggestion, draft) for draft in kept):
                    continue
                price_amount = 0.0
                if suggestion["price_type"] == "low_cost":
                    price_amount = round(max(5.0, suggestion["estimated_retail_value"] * 0.35), 2)

                created.append(RoomScanItemDraft.objects.create(
                    scan_session=scan_session,
                    source_image=scan_image,
                    hotspot_box=suggestion["hotspot_box"],
                    title=suggestion["title"],
                    category=suggestion["category"],
                    condition=suggestion["condition"],
                    price_type=suggestion["price_type"],
                    price_amount=price_amount,
                    estimated_retail_value=suggestion["estimated_retail_value"],
                    triage_status="sell" if suggestion["price_type"] == "low_cost" else "review",
                    notes=AI_DRAFT_NOTE,
                ))

            scan_session.refresh_progress()

            # Fingerprint what the database now holds (decimals as stored, not
            # the floats passed to create), so an unchanged draft compares
            # equal later. Written while the image row is still locked, so a
            # run waiting on that lock reads this record, not the old one.
            fresh = RoomScanItemDraft.objects.filter(pk__in=[draft.pk for draft in created])
            cache.set(
                record_key,
                {str(draft.pk): draft_fingerprint(draft) for draft in fresh},
                timeout=_AI_DRAFT_RECORD_TTL_SECONDS,
            )
        cache.set(key, {"status": "done", "created_count": len(created)}, timeout=600)
    except Exception:
        # The provider message can carry request ids, model names and key
        # fragments; log it and hand the client a generic failure.
        logger.exception("run_ai_detection failed for session %s image %s", session_pk, image_pk)
        cache.set(
            key,
            {"status": "error", "detail": "AI detection failed. Try again or tag items manually."},
            timeout=600,
        )
