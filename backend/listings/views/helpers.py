from __future__ import annotations

from typing import TYPE_CHECKING, Optional

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

from ..models import (
    Listing,
    ListingReport,
    Reservation,
    RescueRequest,
    SavedListing,
)

if TYPE_CHECKING:
    from accounts.models import User


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
