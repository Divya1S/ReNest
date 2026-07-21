from __future__ import annotations

from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import HandoffFeedback, Listing, ListingReport
from ..serializers import HandoffFeedbackSerializer, ListingReportSerializer, ListingSerializer
from ..trust import build_user_trust_summary, get_pending_feedback_queryset
from .helpers import annotate_listing_queryset
from dormcycle.typed import current_user


@extend_schema(exclude=True)
class TrustOverviewView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        summary = build_user_trust_summary(current_user(request))
        received_feedback = (
            HandoffFeedback.objects.select_related("reservation__listing", "reviewer", "reviewee")
            .filter(reviewee=current_user(request))
            .order_by("-created_at")[:8]
        )
        pending_feedback = list(get_pending_feedback_queryset(current_user(request))[:6])
        reports_filed = (
            ListingReport.objects.select_related("listing", "reporter")
            .filter(reporter=current_user(request))
            .order_by("-created_at")[:8]
        )
        reports_on_my_listings = (
            ListingReport.objects.select_related("listing", "reporter")
            .filter(listing__owner=current_user(request))
            .order_by("status", "-created_at")[:8]
        )
        active_rescues = annotate_listing_queryset(
            Listing.objects.select_related("owner").filter(
                owner=current_user(request),
                is_demo=False,
                status__in=[Listing.Status.AVAILABLE, Listing.Status.RESERVED],
            ),
            current_user(request),
        )[:4]
        return Response(
            {
                "summary": {
                    **summary,
                    "member_since": current_user(request).created_at,
                },
                "reports_filed": ListingReportSerializer(
                    reports_filed,
                    many=True,
                    context={"request": request},
                ).data,
                "reports_on_my_listings": ListingReportSerializer(
                    reports_on_my_listings,
                    many=True,
                    context={"request": request},
                ).data,
                "active_rescues": ListingSerializer(
                    active_rescues,
                    many=True,
                    context={"request": request},
                ).data,
                "received_feedback": HandoffFeedbackSerializer(
                    received_feedback,
                    many=True,
                    context={"request": request},
                ).data,
                "pending_feedback": [
                    {
                        "reservation_id": reservation.id,
                        "listing_id": reservation.listing_id,
                        "listing_title": reservation.listing.title,
                        "counterparty_name": (
                            reservation.claimant.display_name
                            if reservation.listing.owner_id == request.user.id
                            else reservation.listing.owner.display_name
                        ),
                        "updated_at": reservation.updated_at,
                    }
                    for reservation in pending_feedback
                ],
            }
        )
