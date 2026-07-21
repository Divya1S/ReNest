from __future__ import annotations

import logging

from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import permissions, serializers as drf_serializers
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from ..ai_client import ai_available, generate_json
from typing import Any

logger = logging.getLogger(__name__)


class DemandForecastView(APIView):
    """
    GET /api/listings/demand-forecast
    Returns the next-week demand forecast for all categories.
    Includes peak flag and predicted view count.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        from ..models import DemandForecast
        from datetime import timedelta

        now = timezone.now()
        next_iso = (now + timedelta(weeks=1)).isocalendar()

        forecasts = DemandForecast.objects.filter(
            week_number=next_iso.week,
            year=next_iso.year,
        ).order_by("-predicted_views")

        data = [
            {
                "category": f.category,
                "predicted_views": f.predicted_views,
                "predicted_saves": f.predicted_saves,
                "is_peak": f.is_peak,
                "week": f.week_number,
                "year": f.year,
            }
            for f in forecasts
        ]
        return Response({"forecasts": data, "week": next_iso.week, "year": next_iso.year})


class SuggestCategoriesView(APIView):
    """
    POST /api/listings/suggest-categories  { "description": "...", "title": "..." }
    Uses Claude to return the 3 most likely categories with confidence scores,
    factoring in seasonal demand data from DemandForecast.
    """

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        operation_id="listings_suggest_categories",
        request=inline_serializer(
            "SuggestCategoriesRequest",
            fields={
                "description": drf_serializers.CharField(required=False, default=""),
                "title": drf_serializers.CharField(required=False, default=""),
            },
        ),
        responses={200: inline_serializer(
            "SuggestCategoriesResponse",
            fields={
                "suggestions": drf_serializers.ListField(
                    child=inline_serializer("CategorySuggestion", fields={
                        "category": drf_serializers.CharField(),
                        "confidence": drf_serializers.FloatField(),
                        "is_peak_demand": drf_serializers.BooleanField(),
                    })
                )
            },
        )},
    )
    def post(self, request: Request) -> Response:
        title = str(request.data.get("title", "")).strip()
        description = str(request.data.get("description", "")).strip()

        if not title and not description:
            return Response({"detail": "Provide title or description."}, status=400)

        if not ai_available():
            return Response({"suggestions": []})

        # Get current peak categories from DemandForecast for context
        from ..models import DemandForecast
        from datetime import timedelta
        next_iso = (timezone.now() + timedelta(weeks=1)).isocalendar()
        peak_cats = set(
            DemandForecast.objects.filter(
                week_number=next_iso.week,
                year=next_iso.year,
                is_peak=True,
            ).values_list("category", flat=True)
        )
        peak_hint = (
            f"Current peak-demand categories this week: {', '.join(peak_cats)}. "
            if peak_cats else ""
        )

        categories = ["storage", "lighting", "supplies", "comfort", "toiletries", "decor", "other"]
        prompt = (
            f"A student is listing a dorm item.\n"
            f"Title: {title}\nDescription: {description[:300]}\n\n"
            f"{peak_hint}"
            f"From these categories: {', '.join(categories)}\n"
            "Return the 3 best-matching categories with confidence 0.0–1.0. "
            'Output JSON only: [{"category": "...", "confidence": 0.9}, ...]'
        )

        try:
            raw_suggestions = generate_json(prompt, max_tokens=200)
            suggestions = raw_suggestions if isinstance(raw_suggestions, list) else []
            suggestions = [
                s for s in suggestions
                if isinstance(s, dict) and s.get("category") in categories
            ][:3]
        except Exception:
            logger.exception("SuggestCategoriesView failed")
            return Response({"suggestions": []})

        # Annotate is_peak_demand
        for s in suggestions:
            s["is_peak_demand"] = s.get("category") in peak_cats

        return Response({"suggestions": suggestions})


class PriceContextView(APIView):
    """
    GET /api/listings/price-context?category=lighting&condition=good
    Returns a histogram (min, p25, median, p75, max, count) of completed
    transactions in the given category/condition bucket over the last 90 days.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        from ..models import Listing, Reservation
        from datetime import timedelta
        import statistics

        category = request.query_params.get("category", "")
        condition = request.query_params.get("condition", "")

        if not category:
            return Response({"detail": "category is required."}, status=400)

        cutoff = timezone.now() - timedelta(days=90)

        qs = (
            Reservation.objects.filter(
                status=Reservation.Status.COMPLETED,
                created_at__gte=cutoff,
                listing__category=category,
                listing__price_type=Listing.PriceType.LOW_COST,
                listing__is_demo=False,
            )
            .select_related("listing")
        )
        if condition:
            qs = qs.filter(listing__condition=condition)

        prices = [float(r.listing.price_amount) for r in qs if r.listing.price_amount]

        if not prices:
            return Response({
                "category": category,
                "condition": condition,
                "count": 0,
                "min": None,
                "p25": None,
                "median": None,
                "p75": None,
                "max": None,
            })

        prices.sort()
        n = len(prices)

        def percentile(lst: Any, pct: Any) -> Any:
            idx = int(pct / 100 * (len(lst) - 1))
            return round(lst[idx], 2)

        return Response({
            "category": category,
            "condition": condition,
            "count": n,
            "min": round(prices[0], 2),
            "p25": percentile(prices, 25),
            "median": round(statistics.median(prices), 2),
            "p75": percentile(prices, 75),
            "max": round(prices[-1], 2),
        })


class QualityHintsDismissView(APIView):
    """
    POST /api/listings/:id/dismiss-hints
    Owner dismisses the quality hints panel.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request: Request, pk: int) -> Response:
        from django.shortcuts import get_object_or_404
        from ..models import Listing
        listing = get_object_or_404(Listing, pk=pk, owner=request.user)
        Listing.objects.filter(pk=pk).update(quality_hints_dismissed=True)
        return Response({"detail": "Hints dismissed."})
