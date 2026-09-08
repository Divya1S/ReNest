from __future__ import annotations

import logging

from typing import Any

from celery import shared_task

from .ai_client import ai_available, generate_json, generate_text

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=30, name="listings.post_create_tasks")
def post_create_tasks(self: Any, listing_pk: int) -> None:
    """Run moderation pre-screen + AI match notifications after a listing is created."""
    try:
        from .models import Listing
        listing = Listing.objects.select_related("owner").get(pk=listing_pk)

        from .moderation import moderate_listing
        mod_status, flag_reason = moderate_listing(listing)
        if mod_status != listing.moderation_status or flag_reason != listing.moderation_flag_reason:
            listing.moderation_status = mod_status
            listing.moderation_flag_reason = flag_reason
            listing.save(update_fields=["moderation_status", "moderation_flag_reason"])

        from .notifications import notify_matching_rescue_requests
        notify_matching_rescue_requests(listing)

        # Phase 18: async quality hints via the AI model
        quality_coach_task.apply_async((listing_pk,), countdown=5)
        # Phase 25 — embedding for semantic search
        generate_listing_embedding.apply_async((listing_pk,), countdown=10)
    except Exception as exc:
        logger.exception("post_create_tasks failed for listing %s", listing_pk)
        raise self.retry(exc=exc)


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
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=30, name="listings.send_reservation_confirmed_email")
def send_reservation_confirmed_email_task(self: Any, reservation_pk: int) -> None:
    try:
        from .models import Reservation
        reservation = Reservation.objects.select_related(
            "listing", "listing__owner", "claimant"
        ).get(pk=reservation_pk)
        from .transactional_emails import send_reservation_confirmed_email
        send_reservation_confirmed_email(reservation)
    except Exception as exc:
        logger.exception("send_reservation_confirmed_email_task failed for reservation %s", reservation_pk)
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=30, name="listings.send_reservation_completed_email")
def send_reservation_completed_email_task(self: Any, reservation_pk: int) -> None:
    try:
        from .models import Reservation
        reservation = Reservation.objects.select_related(
            "listing", "listing__owner", "claimant"
        ).get(pk=reservation_pk)
        from .transactional_emails import send_reservation_completed_email, send_handoff_impact_notification
        send_reservation_completed_email(reservation)
        send_handoff_impact_notification(reservation)
    except Exception as exc:
        logger.exception("send_reservation_completed_email_task failed for reservation %s", reservation_pk)
        raise self.retry(exc=exc)


@shared_task(name="listings.send_handoff_reminder_emails")
def send_handoff_reminder_emails_task() -> int:
    """Celery beat task — fires every 30 min to send 24h handoff reminders."""
    from .transactional_emails import send_handoff_reminder_emails
    return send_handoff_reminder_emails()


@shared_task(name="listings.expire_listings")
def expire_listings_task() -> int:
    """Celery beat task: fires every 15 min to transition overdue listings to EXPIRED.

    Delegates to listings.ops so the beat schedule, the maintenance command and
    the cron endpoint all expire listings the same way (including minting the
    repost token the "your listing expired" email links to).
    """
    from .ops import expire_stale_listings

    return expire_stale_listings()


@shared_task(name="listings.send_bump_emails")
def send_bump_emails_task() -> int:
    """
    Celery beat task — fires daily at 09:00.
    Emails owners of listings that have had zero views in the past 3 days.
    """
    from django.utils import timezone
    from datetime import timedelta
    from .models import Listing, ListingViewEvent
    from .transactional_emails import _send
    from django.conf import settings

    cutoff = timezone.now() - timedelta(days=3)
    stale_ids = (
        Listing.objects.filter(
            status=Listing.Status.AVAILABLE,
            is_demo=False,
            bump_emailed_at__isnull=True,
            created_at__lt=cutoff,
        )
        .exclude(
            id__in=ListingViewEvent.objects.filter(
                viewed_at__gte=cutoff
            ).values("listing_id")
        )
        .values_list("id", flat=True)
    )

    sent = 0
    for listing in Listing.objects.filter(id__in=stale_ids).select_related("owner")[:100]:
        if not getattr(listing.owner, "email_notifications", True):
            continue
        reactivate_url = f"{settings.APP_BASE_URL}/my-listings"
        _send(
            subject=f'Is "{listing.title}" still available?',
            message=(
                f"Hi {listing.owner.display_name},\n\n"
                f'Your listing "{listing.title}" hasn\'t had any views in 3 days.\n\n'
                f"If it\'s still available, visit your listings page to confirm — "
                f"this will bump it back to the top of the feed:\n{reactivate_url}\n\n"
                "If it\'s already been picked up, mark it as collected so others stop seeing it.\n\n"
                "Thanks for using ReNest!"
            ),
            recipient=listing.owner.email,
            user=listing.owner,
        )
        listing.bump_emailed_at = timezone.now()
        listing.save(update_fields=["bump_emailed_at"])
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


@shared_task(name="listings.match_rescue_suggestions")
def match_rescue_suggestions_task(user_pk: int) -> list:
    """
    For a user with an open RescueRequest, score the top 20 current listings
    against their request using the AI model and return the best matches.
    Results are stored as a system notification if strong matches exist.
    """
    from .models import Listing, RescueRequest, Notification

    open_requests = list(
        RescueRequest.objects.filter(seeker_id=user_pk, status=RescueRequest.Status.OPEN)
        .order_by("-created_at")[:1]
    )
    if not open_requests:
        return []

    rescue = open_requests[0]

    listings = list(
        Listing.objects.filter(
            status=Listing.Status.AVAILABLE,
            is_demo=False,
            moderation_status__in=[
                Listing.ModerationStatus.APPROVED,
                Listing.ModerationStatus.PENDING,
            ],
        )
        .exclude(owner_id=user_pk)
        .order_by("-created_at")[:20]
        .values("id", "title", "category", "description")
    )

    if not listings:
        return []

    if not ai_available():
        return []

    listing_lines = "\n".join(
        f'{i+1}. [{l["category"]}] {l["title"]}: {l["description"][:80]}'
        for i, l in enumerate(listings)
    )
    prompt = (
        f"A student is looking for: {rescue.title} ({rescue.category})\n"
        f"Their description: {rescue.description[:200]}\n\n"
        f"Here are {len(listings)} available listings:\n{listing_lines}\n\n"
        "List the numbers (1-based) of listings that are a GOOD match, comma-separated. "
        "Return ONLY numbers or 'none'. Max 3 matches."
    )

    try:
        raw = generate_text(prompt, max_tokens=50).strip()
    except Exception:
        logger.exception("match_rescue_suggestions_task failed for user %s", user_pk)
        return []

    if raw.lower() == "none":
        return []

    import re
    indices = [int(n) - 1 for n in re.findall(r"\d+", raw) if 1 <= int(n) <= len(listings)]
    matched = [listings[i] for i in indices[:3]]

    if matched:
        titles = ", ".join(l["title"] for l in matched)
        Notification.objects.get_or_create(
            dedupe_key=f"rescue_suggest:{rescue.pk}:{matched[0]['id']}",
            defaults={
                "user_id": user_pk,
                "type": Notification.Type.AI_MATCH,
                "title": f"We found {len(matched)} listing{'s' if len(matched) > 1 else ''} matching your rescue request",
                "body": f'Listings that may match your request for "{rescue.title}": {titles}',
                "link_path": "/match-center",
                "priority": Notification.Priority.NORMAL,
            },
        )

    return [l["id"] for l in matched]


@shared_task(name="listings.sweep_stale_confirmations")
def sweep_stale_confirmations_task() -> int:
    """
    Celery beat task — fires every 6 hours.
    Finds CONFIRMED reservations whose pickup_time_window is > 48h in the past
    with no completion or dispute, transitions them to EXPIRED_UNRESOLVED, and
    notifies both parties with resolution options.
    """
    from datetime import timedelta
    from django.db.models import Q
    from django.utils import timezone
    from .models import Dispute, Listing, Notification, Reservation

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
        Reservation.objects.filter(pk=reservation.pk).update(
            status=Reservation.Status.EXPIRED_UNRESOLVED,
        )

        # Re-open the listing if it is still reserved, unless its own deadline
        # has passed. In that case it is expired, not available.
        if reservation.listing.status == Listing.Status.RESERVED:
            Listing.objects.filter(pk=reservation.listing_id).update(
                status=(
                    Listing.Status.EXPIRED
                    if reservation.listing.available_until < now
                    else Listing.Status.AVAILABLE
                ),
            )

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
                    title="Handoff unresolved — what happened?",
                    body=(
                        f'The confirmed handoff for "{reservation.listing.title}" passed 48h ago '
                        "with no completion. Please mark it complete, open a dispute, or cancel."
                    ),
                    link_path=handoff_url,
                    priority=Notification.Priority.HIGH,
                ),
            )
        updated += 1

    return updated


# ─── Phase 18 — Platform Intelligence ────────────────────────────────────────


@shared_task(name="listings.recompute_demand_forecast")
def recompute_demand_forecast_task() -> dict:
    """
    Celery beat task — fires every Sunday at midnight.
    Aggregates ListingViewEvent + ListingInteractionEvent by category × week,
    fits a linear regression per category to predict next-week demand, and
    upserts DemandForecast rows.
    """
    import datetime
    from collections import defaultdict

    from django.utils import timezone

    from .models import DemandForecast, ListingInteractionEvent, ListingViewEvent

    now = timezone.now()
    cutoff = now - datetime.timedelta(days=365)

    # ── Aggregate view counts per (category, year, week) ──────────────────
    view_counts: dict = defaultdict(int)
    for evt in (
        ListingViewEvent.objects
        .filter(viewed_at__gte=cutoff)
        .select_related("listing")
        .values("listing__category", "viewed_at")
    ):
        cat = evt["listing__category"] or "other"
        iso = evt["viewed_at"].isocalendar()
        view_counts[(cat, iso.year, iso.week)] += 1

    save_counts: dict = defaultdict(int)
    for interaction in (
        ListingInteractionEvent.objects
        .filter(action="save", created_at__gte=cutoff)
        .select_related("listing")
        .values("listing__category", "created_at")
    ):
        cat = interaction["listing__category"] or "other"
        iso = interaction["created_at"].isocalendar()
        save_counts[(cat, iso.year, iso.week)] += 1

    # ── Fit per-category linear regression ───────────────────────────────
    try:
        from sklearn.linear_model import LinearRegression
        import numpy as np
    except ImportError:
        logger.warning("scikit-learn not available — skipping demand forecast")
        return {}

    categories = {k[0] for k in view_counts} | {k[0] for k in save_counts}
    next_iso = (now + datetime.timedelta(weeks=1)).isocalendar()
    results = {}

    for cat in categories:
        weeks_data = sorted({(y, w) for (c, y, w) in view_counts if c == cat})
        if len(weeks_data) < 3:
            continue

        X = np.array([[w] for _, w in weeks_data])
        y_views = np.array([view_counts.get((cat, yr, w), 0) for yr, w in weeks_data], dtype=float)
        y_saves = np.array([save_counts.get((cat, yr, w), 0) for yr, w in weeks_data], dtype=float)

        reg_v = LinearRegression().fit(X, y_views)
        reg_s = LinearRegression().fit(X, y_saves)

        pred_views = max(0.0, float(reg_v.predict([[next_iso.week]])[0]))
        pred_saves = max(0.0, float(reg_s.predict([[next_iso.week]])[0]))

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


@shared_task(bind=True, max_retries=2, default_retry_delay=60, name="listings.quality_coach")
def quality_coach_task(self: Any, listing_pk: int) -> list:
    """
    Phase 18 — fires after listing creation.
    Calls the AI model to produce ≤ 2 specific improvement suggestions for the listing.
    Stores result in listing.quality_hints (JSON).
    """
    from .models import Listing

    try:
        listing = Listing.objects.get(pk=listing_pk)
    except Listing.DoesNotExist:
        return []

    if not ai_available():
        return []

    prompt = (
        f"A student posted this dorm-item listing:\n"
        f"Title: {listing.title}\n"
        f"Category: {listing.category}\n"
        f"Condition: {listing.condition}\n"
        f"Description: {listing.description[:300]}\n"
        f"Price: {listing.price_type} {listing.price_amount}\n"
        f"Pickup zone: {listing.pickup_zone}\n\n"
        "Give exactly 2 short, specific suggestions to improve this listing's chances of being claimed "
        "in under 24 hours. Be direct. Output valid JSON array only: "
        '[{"suggestion": "...", "field": "title|description|price|pickup_zone|condition"}]'
    )

    try:
        hints_raw = generate_json(prompt, max_tokens=250)
        hints = hints_raw if isinstance(hints_raw, list) else []
        hints = [h for h in hints if isinstance(h, dict) and "suggestion" in h][:2]
    except Exception:
        logger.exception("quality_coach_task failed for listing %s", listing_pk)
        return []

    if hints:
        Listing.objects.filter(pk=listing_pk).update(quality_hints=hints)

    return hints


@shared_task(name="listings.recompute_completion_rates")
def recompute_completion_rates_task() -> int:
    """
    Phase 18 — fires weekly on Monday morning.
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
            f"— The ReNest team"
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
# Phase 25 — Semantic Search & Map Discovery
# ---------------------------------------------------------------------------

@shared_task(name="listings.generate_listing_embedding")
def generate_listing_embedding(listing_pk: int) -> None:
    """Compute and store a HashingVectorizer feature vector for a listing."""
    import hashlib
    import math

    try:
        from .models import Listing

        listing = Listing.objects.get(pk=listing_pk)
        text = f"{listing.title} {listing.description} {listing.category} {listing.condition} {listing.pickup_zone}"
        n_features = 128
        tokens = text.lower().split()
        vec = [0.0] * n_features
        for tok in tokens:
            idx = int(hashlib.md5(tok.encode()).hexdigest(), 16) % n_features
            vec[idx] += 1.0
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        listing.embedding = [x / norm for x in vec]
        listing.save(update_fields=["embedding"])
    except Exception:
        logger.exception("generate_listing_embedding failed for listing %s", listing_pk)


@shared_task(name="listings.check_saved_searches")
def check_saved_searches() -> int:
    """
    Compare new listings (created since last_notified_at) against each user's
    saved searches and push a notification if matches exist.
    Runs every 15 min via Celery beat.
    """
    from django.utils import timezone as tz

    from .models import Listing, SavedSearch
    from .webpush import send_push_to_user

    fired = 0
    for search in SavedSearch.objects.select_related("user").iterator():
        cutoff = search.last_notified_at or search.created_at
        qs = Listing.objects.filter(status="available", created_at__gt=cutoff)
        if search.category:
            qs = qs.filter(category=search.category)
        if search.keyword:
            qs = qs.filter(title__icontains=search.keyword)
        if search.price_type:
            qs = qs.filter(price_type=search.price_type)
        if search.user.campus:
            qs = qs.filter(owner__campus=search.user.campus)

        count = qs.count()
        if count:
            try:
                send_push_to_user(
                    search.user,
                    title="New listing matches your search",
                    body=f"{count} new item{'s' if count > 1 else ''} match \"{search.label or search.keyword or search.category}\"",
                    url=f"/browse?category={search.category}&search={search.keyword}",
                    notification_type="ai_match",
                )
                fired += 1
            except Exception:
                logger.exception("check_saved_searches: push failed for user %s", search.user_id)
        search.last_notified_at = tz.now()
        search.save(update_fields=["last_notified_at"])

    return fired


@shared_task(name="listings.refresh_trending_cache")
def refresh_trending_cache() -> None:
    """Pre-warm the trending listings Redis cache for every active campus."""
    from django.core.cache import cache
    from django.db.models import Count
    from django.utils import timezone as tz

    from accounts.models import Campus

    from .models import Listing, ListingViewEvent

    from datetime import timedelta as _timedelta

    cutoff = tz.now() - _timedelta(hours=6)

    for campus in Campus.objects.filter(active=True):
        # Aggregate within the campus: a global top-20 filtered afterwards
        # leaves smaller campuses with an empty trending strip.
        view_rows = (
            ListingViewEvent.objects.filter(
                viewed_at__gte=cutoff,
                listing__owner__campus=campus,
                listing__is_demo=False,
                listing__status=Listing.Status.AVAILABLE,
            )
            .values("listing_id")
            .annotate(view_count=Count("id"))
            .order_by("-view_count")[:20]
        )
        listing_ids = [r["listing_id"] for r in view_rows]
        listings_map = {
            l.id: l
            for l in Listing.objects.filter(
                pk__in=listing_ids,
                status=Listing.Status.AVAILABLE,
                owner__campus=campus,
            )
        }
        results = []
        for row in view_rows:
            listing = listings_map.get(row["listing_id"])
            if listing:
                results.append({
                    "id": listing.id,
                    "title": listing.title,
                    "category": listing.category,
                    "price_type": listing.price_type,
                    "price_amount": str(listing.price_amount),
                    "image": listing.image_cdn_url,
                    "view_count": row["view_count"],
                })
            if len(results) >= 6:
                break
        cache.set(f"trending_listings:{campus.slug}", results, timeout=1800)


@shared_task(name="listings.deliver_webhook", bind=True, max_retries=3)
def deliver_webhook(self: Any, delivery_pk: int) -> None:
    """
    Phase 27 — Deliver a single WebhookDelivery to its endpoint URL.
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
    Phase 26 — dispatch push notifications that were held during a user's quiet hours.
    Runs every 5 minutes via Celery beat; only fires payloads whose scheduled epoch has passed.
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


@shared_task(bind=True, name="listings.run_ai_detection")
def run_ai_detection(self: Any, session_pk: int, image_pk: int) -> None:
    """
    Run vision detection for a scan image in the background and create
    hotspot drafts. Progress is reported through the cache so the API can be
    polled — never raises, so eager (dev) execution cannot 500 the request.
    """
    from django.core.cache import cache

    key = ai_detect_cache_key(session_pk, image_pk)
    try:
        from .models import RoomScanImage, RoomScanSession

        scan_session = RoomScanSession.objects.get(pk=session_pk)
        scan_image = RoomScanImage.objects.get(pk=image_pk, scan_session=scan_session)

        from .ai_detect import detect_items_in_image, sanitise_suggestions

        # Pass the FieldFile, not .path — remote storage (S3/R2) has no path.
        suggestions = sanitise_suggestions(detect_items_in_image(scan_image.image))

        from django.db import transaction

        from .models import RoomScanItemDraft

        created_count = 0
        with transaction.atomic():
            for suggestion in suggestions:
                price_amount = 0.0
                if suggestion["price_type"] == "low_cost":
                    price_amount = round(max(5.0, suggestion["estimated_retail_value"] * 0.35), 2)

                RoomScanItemDraft.objects.create(
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
                    notes="AI-detected from room photo.",
                )
                created_count += 1

            scan_session.refresh_progress()
        cache.set(key, {"status": "done", "created_count": created_count}, timeout=600)
    except Exception:
        # The provider message can carry request ids, model names and key
        # fragments; log it and hand the client a generic failure.
        logger.exception("run_ai_detection failed for session %s image %s", session_pk, image_pk)
        cache.set(
            key,
            {"status": "error", "detail": "AI detection failed. Try again or tag items manually."},
            timeout=600,
        )
