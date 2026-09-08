from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

from django.db.models import (
    Case,
    Count,
    Exists,
    IntegerField,
    OuterRef,
    Q,
    QuerySet,
    Value,
    When,
)
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from ..models import (
    BlockedUser,
    Listing,
    ListingReport,
    Reservation,
    RescueRequest,
    SavedListing,
)

if TYPE_CHECKING:
    from accounts.models import User

logger = logging.getLogger(__name__)


def parse_int(
    value: Any,
    *,
    field: str,
    default: Optional[int] = None,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
) -> Optional[int]:
    """Coerce a query/body parameter to int or raise a 400 ValidationError.

    Views used to call ``int(request.query_params[...])`` directly, turning any
    junk value into a 500. Returns ``default`` for missing/blank input.
    """
    if value is None or value == "":
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValidationError({field: "Must be an integer."})
    if minimum is not None and number < minimum:
        raise ValidationError({field: f"Must be at least {minimum}."})
    if maximum is not None and number > maximum:
        raise ValidationError({field: f"Must be at most {maximum}."})
    return number


def parse_int_list(value: Any, *, field: str, max_items: int = 200) -> list[int]:
    """Validate a JSON list of ids; raise a 400 instead of crashing on junk."""
    if not isinstance(value, list) or not value:
        raise ValidationError({field: "Provide a non-empty list of ids."})
    if len(value) > max_items:
        raise ValidationError({field: f"At most {max_items} ids per request."})
    ids: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, str)):
            raise ValidationError({field: "Ids must be integers."})
        try:
            ids.append(int(item))
        except (TypeError, ValueError):
            raise ValidationError({field: "Ids must be integers."})
    return ids


def visible_listing_queryset(user: Any = None) -> QuerySet[Listing]:
    """Base queryset for every public discovery surface (browse, search, map,
    trending, embed, semantic search).

    Applies the same visibility rules as the marketplace browse: non-demo,
    not soft-deleted (the default manager), not flagged by moderation, active
    status, deadline not passed, and (for signed-in users) neither party has
    blocked the other.
    """
    now = timezone.now()
    qs = (
        Listing.objects.select_related("owner", "source_scan_session")
        .filter(is_demo=False, status__in=[Listing.Status.AVAILABLE, Listing.Status.RESERVED])
        .exclude(moderation_status=Listing.ModerationStatus.FLAGGED)
        .filter(available_until__gte=now)
    )
    if user is not None and getattr(user, "is_authenticated", False):
        blocked = BlockedUser.objects.filter(
            Q(blocker=user, blocked_id=OuterRef("owner_id")) | Q(blocked=user, blocker_id=OuterRef("owner_id"))
        )
        qs = qs.annotate(_blocked_ann=Exists(blocked)).filter(_blocked_ann=False)
    return qs


def run_listing_created_hooks(listing: Listing) -> None:
    """Side effects every newly created listing needs, whichever path made it.

    Used by the create endpoint and by the room-scan publish flow so both get
    moderation pre-screen, rescue-request matching, quality hints, embeddings,
    dashboard cache invalidation and partner webhooks. Never raises: a failed
    hook must not undo a saved listing.
    """
    from .dashboard import invalidate_user_dashboard_cache

    invalidate_user_dashboard_cache(listing.owner_id)
    try:
        from ..tasks import post_create_tasks

        post_create_tasks.delay(listing.pk)
    except Exception:
        logger.exception("post-create tasks could not be scheduled for listing %s", listing.pk)
    try:
        from .partner import dispatch_webhook_event

        owner = listing.owner
        dispatch_webhook_event(
            "listing_created",
            {"id": listing.pk, "title": listing.title, "category": listing.category, "status": listing.status},
            owner.campus.name if owner.campus else (owner.campus_name or ""),
        )
    except Exception:
        logger.exception("listing_created webhook dispatch failed for listing %s", listing.pk)


def annotate_listing_queryset(queryset: QuerySet[Listing], user: Optional[User] = None) -> QuerySet[Listing]:
    """
    Push per-listing counts and user-specific flags into the queryset so
    ListingSerializer can read from annotations instead of firing one query
    per listing per field.  Views that skip this fall back to the per-object
    queries already in the serializer methods.
    """
    # Completeness score (0–100): each of 5 criteria contributes 20 points.
    # Used as a secondary sort key in browse so better-filled listings rank higher.
    completeness_score = (
        Case(When(title__length__gte=3, then=Value(20)), default=Value(0), output_field=IntegerField())
        + Case(When(description__length__gte=40, then=Value(20)), default=Value(0), output_field=IntegerField())
        + Case(When(Q(image__isnull=False) & ~Q(image=""), then=Value(20)), default=Value(0), output_field=IntegerField())
        + Case(When(estimated_retail_value__gt=0, then=Value(20)), default=Value(0), output_field=IntegerField())
        + Case(When(pickup_zone__length__gte=10, then=Value(20)), default=Value(0), output_field=IntegerField())
    )
    qs = queryset.annotate(
        reservation_count_ann=Count(
            "reservations",
            filter=~Q(reservations__status=Reservation.Status.CANCELLED),
            distinct=True,
        ),
        saved_count_ann=Count("saved_by", distinct=True),
        update_count_ann=Count("updates", distinct=True),
        view_count_ann=Count("view_events", distinct=True),
        completeness_score_ann=completeness_score,
    )
    if user and user.is_authenticated:
        qs = qs.annotate(
            is_saved_ann=Exists(
                SavedListing.objects.filter(listing=OuterRef("pk"), user=user)
            ),
            user_has_reservation_ann=Exists(
                Reservation.objects.filter(listing=OuterRef("pk"), claimant=user)
            ),
            has_reported_ann=Exists(
                ListingReport.objects.filter(listing=OuterRef("pk"), reporter=user)
            ),
        )
    return qs


def _exclude_deadline_passed(queryset: QuerySet[Listing]) -> QuerySet[Listing]:
    # Read-only: hide listings whose deadline passed but cron hasn't run yet.
    now = timezone.now()
    return queryset.exclude(
        status__in=[Listing.Status.AVAILABLE, Listing.Status.RESERVED],
        available_until__lt=now,
    )


def _expire_for_user(user: User) -> None:
    # Scoped write: only touches this user's listings, not the full table.
    Listing.objects.filter(
        owner=user,
        status__in=[Listing.Status.AVAILABLE, Listing.Status.RESERVED],
        available_until__lt=timezone.now(),
    ).update(status=Listing.Status.EXPIRED)


def get_rescue_request_queryset(user: User) -> QuerySet[RescueRequest]:
    queryset: QuerySet[RescueRequest] = RescueRequest.objects.select_related(
        "seeker",
        "matched_listing",
        "matched_listing__owner",
        "matched_listing__source_scan_session",
    )
    if user.campus_name:
        queryset = queryset.filter(Q(seeker__campus_name=user.campus_name) | Q(seeker=user))
    return queryset.exclude(
        Q(status__in=[RescueRequest.Status.FULFILLED, RescueRequest.Status.CLOSED])
        & ~Q(seeker=user)
    )


def user_can_post_update(user: User, listing: Listing) -> bool:
    if listing.owner_id == user.id:
        return True
    return listing.reservations.filter(claimant=user).exists()
