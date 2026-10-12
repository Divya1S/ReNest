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
    StorageHold,
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


# Relations ListingSerializer reads on every row. scan_draft_source is a
# reverse one-to-one: left out of select_related it costs one query per
# listing, including every listing that has no scan draft at all.
LISTING_SERIALIZER_RELATIONS = ("owner", "source_scan_session", "scan_draft_source")

_ACTIVE_LISTING_STATUSES = (Listing.Status.AVAILABLE, Listing.Status.RESERVED)


def owner_campus_name(owner: Any) -> str:
    """Campus name partner webhooks are addressed by.

    The verified campus foreign key wins; the free-text profile field is
    only a fallback for accounts that were never matched to a campus.
    """
    campus = getattr(owner, "campus", None)
    return campus.name if campus else (getattr(owner, "campus_name", "") or "")


def _is_signed_in(user: Any) -> bool:
    return user is not None and bool(getattr(user, "is_authenticated", False))


def _visible_listings(user: Any, *, include_flagged: bool) -> QuerySet[Listing]:
    now = timezone.now()
    qs = Listing.objects.select_related(*LISTING_SERIALIZER_RELATIONS).filter(
        is_demo=False,
        status__in=_ACTIVE_LISTING_STATUSES,
        available_until__gte=now,
    )
    if not include_flagged:
        qs = qs.exclude(moderation_status=Listing.ModerationStatus.FLAGGED)
    if _is_signed_in(user):
        blocked = BlockedUser.objects.filter(
            Q(blocker=user, blocked_id=OuterRef("owner_id")) | Q(blocked=user, blocker_id=OuterRef("owner_id"))
        )
        qs = qs.filter(~Exists(blocked))
    return qs


def visible_listing_queryset(user: Any = None) -> QuerySet[Listing]:
    """Base queryset for every public discovery surface (browse, search, map,
    trending, embed, semantic search).

    Applies the same visibility rules as the marketplace browse: non-demo,
    not soft-deleted (the default manager), not flagged by moderation, active
    status, deadline not passed, and (for signed-in users) neither party has
    blocked the other.
    """
    return _visible_listings(user, include_flagged=False)


def browse_listing_queryset(
    user: Any = None,
    *,
    include_flagged: bool = False,
    campus_slug: Optional[str] = None,
) -> QuerySet[Listing]:
    """`marketplace_listing_queryset` with the two switches only browse has.

    `include_flagged` lets staff see listings awaiting moderation.
    `campus_slug` replaces the viewer's own campus scope; the browse view
    decides who may pass it (guests, staff and campus managers).
    """
    from django.contrib.auth import get_user_model

    qs = _visible_listings(user, include_flagged=include_flagged)
    # Owners with a poor completion record (below 30%) are suppressed.
    poor_owners = get_user_model().objects.filter(
        completion_rate__lt=0.3, completion_rate__isnull=False
    ).values_list("id", flat=True)
    qs = qs.exclude(owner_id__in=poor_owners)
    if campus_slug:
        qs = qs.filter(owner__campus__slug=campus_slug)
    elif _is_signed_in(user) and getattr(user, "campus_id", None):
        qs = qs.filter(owner__campus_id=user.campus_id)
    return qs


def marketplace_listing_queryset(user: Any = None) -> QuerySet[Listing]:
    """What a given person may discover in the marketplace.

    `visible_listing_queryset` plus the two rules browse adds on top: owners
    with a poor completion record are suppressed, and a signed-in user with a
    campus only sees that campus. Every surface that shows inventory to a
    user (browse, search, the concierge tools and its listing cards) builds
    on this one function so the rules cannot drift apart.
    """
    return browse_listing_queryset(user)


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

        dispatch_webhook_event(
            "listing_created",
            {"id": listing.pk, "title": listing.title, "category": listing.category, "status": listing.status},
            owner_campus_name(listing.owner),
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
        # Lets can_reserve reflect an active storage hold without a query per row.
        on_hold_ann=Exists(
            StorageHold.objects.filter(
                listing=OuterRef("pk"),
                status=StorageHold.Status.ACTIVE,
                expires_at__gt=timezone.now(),
            )
        ),
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


def _expire_for_user(user: User) -> None:
    # Scoped write: only touches this user's listings, not the full table.
    # Row by row through Listing.refresh_status, like ops.expire_stale_listings:
    # a queryset .update() would skip the repost token, and a listing expired
    # without one can never be revived by its owner.
    stale = Listing.objects.filter(
        owner=user,
        status__in=_ACTIVE_LISTING_STATUSES,
        available_until__lt=timezone.now(),
    )
    for listing in stale.iterator(chunk_size=200):
        listing.refresh_status(commit=True)


def get_rescue_request_queryset(user: User) -> QuerySet[RescueRequest]:
    queryset: QuerySet[RescueRequest] = RescueRequest.objects.select_related(
        "seeker",
        "matched_listing",
        "matched_listing__owner",
        "matched_listing__source_scan_session",
        "matched_listing__scan_draft_source",
    )
    # The verified campus (the foreign key set from the email domain) decides
    # whose requests a user sees. The free-text campus name on a profile is
    # editable, so it only groups accounts that have no verified campus.
    from ..notifications import same_campus_q

    queryset = queryset.filter(same_campus_q("seeker", user) | Q(seeker=user))
    return queryset.exclude(
        Q(status__in=[RescueRequest.Status.FULFILLED, RescueRequest.Status.CLOSED])
        & ~Q(seeker=user)
    )


def user_can_post_update(user: User, listing: Listing) -> bool:
    if listing.owner_id == user.id:
        return True
    return listing.reservations.filter(claimant=user).exists()
