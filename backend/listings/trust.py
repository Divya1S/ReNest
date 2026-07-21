from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.db.models import Avg, Q, QuerySet

from .models import HandoffFeedback, Listing, ListingReport, Reservation

if TYPE_CHECKING:
    from accounts.models import User


def build_trust_badge(
    total_reviews: int,
    average_rating: float,
    completed_rescues: int,
) -> str:
    if total_reviews >= 8 and average_rating >= 4.8:
        return "Campus standout"
    if total_reviews >= 4 and average_rating >= 4.4:
        return "Trusted handoff"
    if completed_rescues >= 3:
        return "Reliable rescuer"
    if total_reviews >= 1:
        return "Building trust"
    return "New member"


def get_pending_feedback_queryset(user: User) -> QuerySet[Reservation]:
    completed_handoffs = (
        Reservation.objects.select_related("listing", "listing__owner", "claimant")
        .filter(status=Reservation.Status.COMPLETED, listing__is_demo=False)
        .filter(Q(listing__owner=user) | Q(claimant=user))
        .order_by("-updated_at")
    )
    return completed_handoffs.exclude(
        id__in=HandoffFeedback.objects.filter(reviewer=user).values("reservation_id")
    )


def build_user_trust_summary(user: User) -> dict[str, Any]:
    owned_listings = Listing.objects.filter(owner=user, is_demo=False)
    owner_reservations = Reservation.objects.filter(listing__owner=user, listing__is_demo=False)
    claimant_reservations = Reservation.objects.filter(claimant=user, listing__is_demo=False)
    received_feedback = HandoffFeedback.objects.filter(reviewee=user)
    active_report_count = ListingReport.objects.filter(
        listing__owner=user,
        status__in=[ListingReport.Status.OPEN, ListingReport.Status.REVIEWING],
    ).count()
    feedback_aggregate = received_feedback.aggregate(average_rating=Avg("rating"))
    total_feedback_received = received_feedback.count()
    average_rating = float(feedback_aggregate["average_rating"] or 0)
    positive_feedback_count = received_feedback.filter(rating__gte=4).count()
    pending_feedback_count = get_pending_feedback_queryset(user).count()
    completed_rescues = owned_listings.filter(
        status__in=[Listing.Status.PICKED_UP, Listing.Status.DONATED]
    ).count()

    return {
        "completed_rescues": completed_rescues,
        "active_listings": owned_listings.filter(
            status__in=[Listing.Status.AVAILABLE, Listing.Status.RESERVED]
        ).count(),
        "successful_handoffs": owner_reservations.filter(
            status=Reservation.Status.COMPLETED
        ).count(),
        "claims_completed": claimant_reservations.filter(
            status=Reservation.Status.COMPLETED
        ).count(),
        "open_reports": active_report_count,
        "average_rating": round(average_rating, 1) if total_feedback_received else None,
        "total_feedback_received": total_feedback_received,
        "positive_feedback_rate": round(
            (positive_feedback_count / total_feedback_received) * 100
        ) if total_feedback_received else None,
        "pending_feedback": pending_feedback_count,
        "trust_badge": build_trust_badge(
            total_feedback_received,
            average_rating,
            completed_rescues,
        ),
        "milestone": user.milestone or None,
        "referral_count": user.referral_count,
        "member_since": user.created_at,
        "campus_name": user.campus_name,
    }
