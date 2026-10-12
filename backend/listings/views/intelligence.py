from __future__ import annotations

import logging

from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import permissions, serializers as drf_serializers
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from ..ai_client import (
    UNTRUSTED_TEXT_RULE,
    ai_available,
    coerce_number,
    generate_json,
    quote_untrusted,
)
from typing import Any

logger = logging.getLogger(__name__)


class SuggestCategoriesThrottle(UserRateThrottle):
    """Per-user cap on the model-backed category suggester.

    Each request is a paid model call, and the endpoint had no limit at all.
    The rate is set on the class, which DRF uses in preference to a
    DEFAULT_THROTTLE_RATES lookup, so no settings entry is needed. The scope
    still matters: it keeps this counter separate from other user throttles.
    """

    scope = "suggest_categories"
    rate = "30/hour"


_CATEGORY_SUGGESTIONS_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {"category": {"type": "STRING"}, "confidence": {"type": "NUMBER"}},
        "required": ["category", "confidence"],
    },
}


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
    Uses the AI model to return the 3 most likely categories with confidence scores,
    factoring in seasonal demand data from DemandForecast.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [SuggestCategoriesThrottle]

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
            "A student is listing a dorm item.\n"
            f"Title: {quote_untrusted(title, limit=140)}\n"
            f"Description: {quote_untrusted(description, limit=300)}\n\n"
            f"{UNTRUSTED_TEXT_RULE}\n"
            f"{peak_hint}"
            f"From these categories: {', '.join(categories)}\n"
            "Return the 3 best-matching categories with confidence 0.0 to 1.0. "
            'Output JSON only: [{"category": "...", "confidence": 0.9}, ...]'
        )

        try:
            raw_suggestions = generate_json(
                prompt, max_tokens=200, response_schema=_CATEGORY_SUGGESTIONS_SCHEMA
            )
        except Exception:
            logger.exception("SuggestCategoriesView failed")
            return Response({"suggestions": []})

        # Rebuild each item from validated parts instead of passing the
        # model's objects through: the response schema promises exactly
        # category, confidence and is_peak_demand.
        suggestions: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in raw_suggestions if isinstance(raw_suggestions, list) else []:
            category = item.get("category") if isinstance(item, dict) else None
            if not isinstance(category, str) or category not in categories or category in seen:
                continue
            confidence = coerce_number(item.get("confidence"))
            seen.add(category)
            suggestions.append({
                "category": category,
                "confidence": min(max(confidence, 0.0), 1.0) if confidence is not None else 0.0,
                "is_peak_demand": category in peak_cats,
            })
            if len(suggestions) >= 3:
                break

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
