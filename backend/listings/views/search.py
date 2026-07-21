from __future__ import annotations

import json
import logging
import math

from django.core.cache import cache
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import BuildingCoord, Listing, ListingViewEvent, SavedSearch
from dormcycle.typed import current_user
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers — lightweight HashingVectorizer-style embedding
# ---------------------------------------------------------------------------

def _text_to_vector(text: str, n_features: int = 128) -> list[float]:
    """Convert text to a normalised hash-trick feature vector (no fitting required)."""
    import hashlib
    tokens = text.lower().split()
    vec = [0.0] * n_features
    for tok in tokens:
        idx = int(hashlib.md5(tok.encode()).hexdigest(), 16) % n_features
        vec[idx] += 1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _listing_text(listing: Any) -> str:
    return f"{listing.title} {listing.description} {listing.category} {listing.condition} {listing.pickup_zone}"


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------

class SavedSearchSerializer(serializers.ModelSerializer):
    class Meta:
        model = SavedSearch
        fields = ["id", "label", "category", "keyword", "price_type", "last_notified_at", "created_at"]
        read_only_fields = ["id", "last_notified_at", "created_at"]


class BuildingCoordSerializer(serializers.ModelSerializer):
    class Meta:
        model = BuildingCoord
        fields = ["id", "building", "latitude", "longitude"]


# ---------------------------------------------------------------------------
# Semantic search
# ---------------------------------------------------------------------------

class SemanticSearchView(APIView):
    """
    POST /api/listings/semantic-search
    Body: { "query": "something to sit on", "limit": 20 }
    Re-ranks up to 200 campus listings by cosine similarity to the query embedding.
    Falls back to title keyword match when no embeddings exist yet.
    """

    def post(self, request: Request) -> Response:
        query = request.data.get("query", "").strip()
        limit = min(int(request.data.get("limit", 20)), 50)
        if not query:
            return Response({"detail": "query is required."}, status=400)

        campus = current_user(request).campus
        qs = Listing.objects.filter(
            status=Listing.Status.AVAILABLE,
        )
        if campus:
            qs = qs.filter(owner__campus=campus)

        listings = list(qs.order_by("-created_at")[:200])
        if not listings:
            return Response({"results": []})

        query_vec = _text_to_vector(query)

        scored = []
        for listing in listings:
            if listing.embedding:
                score = _cosine_similarity(query_vec, listing.embedding)
            else:
                # fallback: keyword overlap score
                words = set(query.lower().split())
                title_words = set(listing.title.lower().split())
                score = len(words & title_words) / (len(words) or 1)
            scored.append((score, listing))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = [l for _, l in scored[:limit]]

        from .listings import ListingSerializer
        return Response({
            "results": ListingSerializer(top, many=True, context={"request": request}).data,
            "query": query,
        })


# ---------------------------------------------------------------------------
# Map data
# ---------------------------------------------------------------------------

class MapDataView(APIView):
    """
    GET /api/listings/map-data
    Returns active listings grouped by building with lat/lon from BuildingCoord.
    Only buildings that have a registered coord entry are included.
    """

    def get(self, request: Request) -> Response:
        campus = current_user(request).campus
        qs = Listing.objects.filter(
            status=Listing.Status.AVAILABLE,
            building__gt="",
        )
        if campus:
            qs = qs.filter(owner__campus=campus)
            coords = {
                bc.building: {"lat": bc.latitude, "lng": bc.longitude}
                for bc in BuildingCoord.objects.filter(campus=campus)
            }
        else:
            coords = {}

        # Aggregate by building
        building_map: dict[str, list] = {}
        for listing in qs.values("id", "title", "category", "price_type", "price_amount", "building", "image_cdn_url")[:500]:
            b = listing["building"]
            if b not in building_map:
                building_map[b] = []
            building_map[b].append({
                "id": listing["id"],
                "title": listing["title"],
                "category": listing["category"],
                "price_type": listing["price_type"],
                "image": listing["image_cdn_url"],
            })

        pins: list[dict[str, Any]] = []
        for building, items in building_map.items():
            coord = coords.get(building)
            pins.append({
                "building": building,
                "count": len(items),
                "items": items[:5],
                "lat": coord["lat"] if coord else None,
                "lng": coord["lng"] if coord else None,
                "has_coord": coord is not None,
            })

        pins.sort(key=lambda p: p["count"], reverse=True)
        return Response({"pins": pins, "total_with_coords": sum(1 for p in pins if p["has_coord"])})


class BuildingCoordListCreateView(APIView):
    """
    GET  /api/buildings  — list building coords for the user's campus
    POST /api/buildings  — create/update a building coord (campus manager only)
    """

    def get(self, request: Request) -> Response:
        campus = current_user(request).campus
        if not campus:
            return Response([])
        qs = BuildingCoord.objects.filter(campus=campus)
        return Response(BuildingCoordSerializer(qs, many=True).data)

    def post(self, request: Request) -> Response:
        if not (current_user(request).is_staff or current_user(request).is_campus_manager):
            return Response({"detail": "Permission denied."}, status=403)
        campus = current_user(request).campus
        if not campus:
            return Response({"detail": "No campus assigned."}, status=400)

        building = request.data.get("building", "").strip()
        lat = request.data.get("latitude")
        lng = request.data.get("longitude")
        if not building or lat is None or lng is None:
            return Response({"detail": "building, latitude, and longitude required."}, status=400)

        coord, _ = BuildingCoord.objects.update_or_create(
            campus=campus,
            building=building,
            defaults={"latitude": float(lat), "longitude": float(lng)},
        )
        return Response(BuildingCoordSerializer(coord).data, status=201)


# ---------------------------------------------------------------------------
# Trending listings
# ---------------------------------------------------------------------------

class TrendingListingsView(APIView):
    """
    GET /api/listings/trending
    Returns up to 6 listings with the highest view velocity (views/hour) in the
    last 6 hours on the user's campus. Result is Redis-cached for 30 min.
    """

    def get(self, request: Request) -> Response:
        campus = current_user(request).campus
        campus_key = campus.slug if campus else "all"
        cache_key = f"trending_listings:{campus_key}"

        cached = cache.get(cache_key)
        if cached is not None:
            return Response({"results": cached})

        from datetime import timedelta as _timedelta

        cutoff = timezone.now() - _timedelta(hours=6)
        qs = (
            ListingViewEvent.objects.filter(viewed_at__gte=cutoff)
            .values("listing_id")
            .annotate(view_count=Count("id"))
            .order_by("-view_count")[:20]
        )

        listing_ids = [row["listing_id"] for row in qs]  # type: ignore[index]  # .values() rows are dicts
        listings_map = {
            l.id: l
            for l in Listing.objects.filter(
                pk__in=listing_ids,
                status=Listing.Status.AVAILABLE,
                **({} if not campus else {"owner__campus": campus}),
            )
        }

        results = []
        for row in qs:
            listing = listings_map.get(row["listing_id"])  # type: ignore[index]  # .values() rows are dicts
            if listing:
                results.append({
                    "id": listing.id,
                    "title": listing.title,
                    "category": listing.category,
                    "price_type": listing.price_type,
                    "price_amount": str(listing.price_amount),
                    "image": listing.image_cdn_url,
                    "view_count": row["view_count"],  # type: ignore[index]  # .values() rows are dicts
                })
            if len(results) >= 6:
                break

        cache.set(cache_key, results, timeout=1800)
        return Response({"results": results})


# ---------------------------------------------------------------------------
# Saved searches
# ---------------------------------------------------------------------------

class SavedSearchListCreateView(APIView):
    """
    GET  /api/saved-searches       — list the user's saved searches
    POST /api/saved-searches       — create a saved search (max 5 per user)
    """

    def get(self, request: Request) -> Response:
        qs = SavedSearch.objects.filter(user=current_user(request))
        return Response(SavedSearchSerializer(qs, many=True).data)

    def post(self, request: Request) -> Response:
        if SavedSearch.objects.filter(user=current_user(request)).count() >= 5:
            return Response({"detail": "Maximum 5 saved searches allowed."}, status=400)

        ser = SavedSearchSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ser.save(user=request.user)
        return Response(ser.data, status=201)


class SavedSearchDetailView(APIView):
    """DELETE /api/saved-searches/:pk"""

    def delete(self, request: Request, pk: int) -> Response:
        try:
            obj = SavedSearch.objects.get(pk=pk, user=current_user(request))
        except SavedSearch.DoesNotExist:
            return Response({"detail": "Not found."}, status=404)
        obj.delete()
        return Response(status=204)


# ---------------------------------------------------------------------------
# Proximity sort helper
# ---------------------------------------------------------------------------

class ProximityRouteView(APIView):
    """
    GET /api/reservations/pickup-route?lat=<>&lng=<>
    Returns confirmed reservations sorted by proximity to the given lat/lng.
    Distance is computed from BuildingCoord; reservations without a coord go last.
    """

    def get(self, request: Request) -> Response:
        from ..models import Reservation

        try:
            user_lat = float(request.query_params["lat"])
            user_lng = float(request.query_params["lng"])
        except (KeyError, ValueError):
            return Response({"detail": "lat and lng query params required."}, status=400)

        reservations = (
            Reservation.objects.filter(
                claimant=current_user(request),
                status=Reservation.Status.CONFIRMED,
            )
            .select_related("listing")
            .order_by("-created_at")[:20]
        )

        campus = current_user(request).campus
        coords = {}
        if campus:
            coords = {
                bc.building: (bc.latitude, bc.longitude)
                for bc in BuildingCoord.objects.filter(campus=campus)
            }

        def dist(res: Any) -> Any:
            building = res.listing.building
            if building in coords:
                lat2, lng2 = coords[building]
                return math.sqrt((user_lat - lat2) ** 2 + (user_lng - lng2) ** 2)
            return float("inf")

        sorted_res = sorted(reservations, key=dist)

        return Response({
            "stops": [
                {
                    "reservation_id": r.id,
                    "listing_title": r.listing.title,
                    "building": r.listing.building,
                    "pickup_zone": r.listing.pickup_zone,
                    "owner": r.listing.owner.display_name,
                    "status": r.status,
                }
                for r in sorted_res
            ]
        })
