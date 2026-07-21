from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from django.db.models import Q, QuerySet
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import generics, permissions, serializers as drf_serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

if TYPE_CHECKING:
    from accounts.models import User as UserType

from ..models import Listing, RescueRequest
from ..permissions import IsRescueRequestOwnerOrReadOnly
from ..serializers import ListingSerializer, RescueRequestSerializer
from dormcycle.typed import current_user

from .helpers import (
    annotate_listing_queryset,
    get_rescue_request_queryset,
)


def build_match_center_payload(user: UserType, request: Request) -> dict[str, Any]:
    now = timezone.now()

    available_owned_listings = list(
        annotate_listing_queryset(
            Listing.objects.select_related("owner", "source_scan_session").filter(
                owner=user,
                is_demo=False,
                status=Listing.Status.AVAILABLE,
            ),
            user,
        )[:8]
    )
    open_my_requests = list(
        get_rescue_request_queryset(user)
        .filter(seeker=user, status=RescueRequest.Status.OPEN)
        .order_by("needed_by", "-created_at")[:8]
    )
    campus_urgent_requests = list(
        get_rescue_request_queryset(user)
        .exclude(seeker=user)
        .filter(status=RescueRequest.Status.OPEN, needed_by__gte=now)
        .order_by("needed_by", "-created_at")[:6]
    )

    # One bulk query for all requests matching any of the user's listing categories,
    # then partition by category in Python — eliminates 2 queries per listing.
    fulfill_opportunities = []
    if available_owned_listings:
        listing_categories = {l.category for l in available_owned_listings}
        requests_qs = (
            RescueRequest.objects.select_related("seeker", "matched_listing", "matched_listing__owner")
            .filter(
                category__in=listing_categories,
                status=RescueRequest.Status.OPEN,
                needed_by__gte=now,
            )
            .exclude(seeker=user)
            .order_by("needed_by", "-created_at")
        )
        if user.campus_name:
            requests_qs = requests_qs.filter(seeker__campus_name=user.campus_name)
        requests_pool = list(requests_qs)

        requests_by_category: dict[str, list[Any]] = {}
        for req in requests_pool:
            requests_by_category.setdefault(req.category, []).append(req)

        for listing in available_owned_listings:
            category_reqs = requests_by_category.get(listing.category, [])
            if listing.price_type == Listing.PriceType.LOW_COST:
                matching = [
                    req for req in category_reqs
                    if Decimal(req.budget_amount or 0) >= Decimal(listing.price_amount or 0)
                ]
            else:
                matching = category_reqs
            if not matching:
                continue
            fulfill_opportunities.append(
                {
                    "listing": ListingSerializer(listing, context={"request": request}).data,
                    "match_count": len(matching),
                    "matches": RescueRequestSerializer(
                        matching[:3],
                        many=True,
                        context={"request": request},
                    ).data,
                }
            )

    # One bulk query for all listings matching any of the user's request categories,
    # then partition by category in Python — eliminates 2 queries per request.
    request_recommendations = []
    if open_my_requests:
        request_categories = {req.category for req in open_my_requests}
        listings_qs = annotate_listing_queryset(
            Listing.objects.select_related("owner", "source_scan_session")
            .exclude(is_demo=True)
            .filter(
                category__in=request_categories,
                status=Listing.Status.AVAILABLE,
                available_until__gte=now,
            )
            .exclude(owner=user)
            .order_by("available_until", "-created_at"),
            user,
        )
        if user.campus_name:
            listings_qs = listings_qs.filter(owner__campus_name=user.campus_name)
        listings_pool = list(listings_qs)

        listings_by_category: dict[str, list[Any]] = {}
        for listing in listings_pool:
            listings_by_category.setdefault(listing.category, []).append(listing)

        for rescue_request in open_my_requests:
            category_listings = listings_by_category.get(rescue_request.category, [])
            budget = Decimal(rescue_request.budget_amount or 0)
            if budget > Decimal("0.00"):
                matching = [
                    l for l in category_listings
                    if l.price_type == Listing.PriceType.FREE
                    or Decimal(l.price_amount or 0) <= budget
                ]
            else:
                matching = [l for l in category_listings if l.price_type == Listing.PriceType.FREE]
            if not matching:
                continue
            request_recommendations.append(
                {
                    "request": RescueRequestSerializer(
                        rescue_request,
                        context={"request": request},
                    ).data,
                    "match_count": len(matching),
                    "matches": ListingSerializer(
                        matching[:3],
                        many=True,
                        context={"request": request},
                    ).data,
                }
            )

    return {
        "stats": {
            "fulfillable_needs": len(fulfill_opportunities),
            "requests_with_matches": len(request_recommendations),
            "campus_urgent_requests": len(campus_urgent_requests),
        },
        "fulfill_opportunities": fulfill_opportunities,
        "request_recommendations": request_recommendations,
        "urgent_requests": RescueRequestSerializer(
            campus_urgent_requests,
            many=True,
            context={"request": request},
        ).data,
    }


class RescueRequestListCreateView(generics.ListCreateAPIView):
    serializer_class = RescueRequestSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self) -> QuerySet[RescueRequest]:
        queryset = get_rescue_request_queryset(current_user(self.request))

        if self.request.query_params.get("mine") == "1":
            queryset = queryset.filter(seeker=current_user(self.request))

        category = self.request.query_params.get("category")
        if category:
            queryset = queryset.filter(category=category)

        status_param = self.request.query_params.get("status")
        if status_param:
            queryset = queryset.filter(status=status_param)

        search = self.request.query_params.get("search")
        if search:
            queryset = queryset.filter(
                Q(title__icontains=search)
                | Q(description__icontains=search)
                | Q(pickup_zone__icontains=search)
            )

        return queryset

    def perform_create(self, serializer: Any) -> None:
        serializer.save(seeker=self.request.user)


class RescueRequestDetailView(generics.RetrieveUpdateAPIView):
    serializer_class = RescueRequestSerializer
    permission_classes = [permissions.IsAuthenticated, IsRescueRequestOwnerOrReadOnly]

    def get_queryset(self) -> QuerySet[RescueRequest]:
        return get_rescue_request_queryset(current_user(self.request))


class RescueRequestMatchView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        operation_id="rescue_request_match",
        request=inline_serializer("RescueRequestMatchRequest", fields={"listing": drf_serializers.IntegerField()}),
        responses={200: RescueRequestSerializer},
    )
    def post(self, request: Request, pk: int) -> Response:
        rescue_request = get_object_or_404(get_rescue_request_queryset(current_user(request)), pk=pk)
        if rescue_request.seeker_id == request.user.id:
            return Response({"detail": "You cannot match your own request."}, status=400)
        if rescue_request.status != RescueRequest.Status.OPEN:
            return Response({"detail": "Only open requests can be matched."}, status=400)

        listing_id = request.data.get("listing")
        if not listing_id:
            return Response({"listing": "Choose one of your listings to match."}, status=400)

        listing = get_object_or_404(
            Listing.objects.select_related("owner", "source_scan_session").exclude(is_demo=True),
            pk=listing_id,
            owner=request.user,
        )
        listing.refresh_status(commit=True)

        if listing.status != Listing.Status.AVAILABLE:
            return Response({"listing": "This listing is no longer available."}, status=400)
        if listing.category != rescue_request.category:
            return Response({"listing": "Pick a listing from the same category as the request."}, status=400)
        if RescueRequest.objects.filter(
            matched_listing=listing,
            status=RescueRequest.Status.MATCHED,
        ).exclude(pk=rescue_request.pk).exists():
            return Response({"listing": "That listing is already matched to another request."}, status=400)

        rescue_request.matched_listing = listing
        rescue_request.status = RescueRequest.Status.MATCHED
        rescue_request.save(update_fields=["matched_listing", "status", "updated_at"])

        serializer = RescueRequestSerializer(rescue_request, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)


@extend_schema(exclude=True)
class MatchCenterView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        return Response(build_match_center_payload(current_user(request), request))
