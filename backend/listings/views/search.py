from __future__ import annotations

import hashlib
import logging
import math
import re
import unicodedata
from datetime import timedelta
from typing import Any

from django.core.cache import cache
from django.db import IntegrityError
from django.db.models import Count
from django.utils import timezone
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import BuildingCoord, Listing, ListingViewEvent, SavedSearch
from .helpers import marketplace_listing_queryset, parse_int, visible_listing_queryset
from dormcycle.typed import current_user

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lexical similarity (the hashing trick)
# ---------------------------------------------------------------------------
# This is word matching, not understanding. Each text becomes a bag of
# normalised word tokens, every token is hashed into one of
# LEXICAL_VECTOR_SIZE buckets, and two texts are compared by the cosine of
# their bucket-count vectors. No model is involved and nothing is learned:
# "lamp" matches "lamps" because the plural is folded, and it does not match
# "light" because they are different words. Unrelated words can also share a
# bucket, which gives an occasional small false score. The endpoint keeps the
# name "semantic-search" for URL compatibility only.

LEXICAL_VECTOR_SIZE = 128
# Bump when tokenisation or hashing changes: stored vectors from an older
# version are then ignored and recomputed instead of being compared against
# query vectors they are no longer compatible with.
LEXICAL_VECTOR_VERSION = 2

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is", "it",
        "my", "of", "on", "or", "that", "the", "this", "to", "with",
    }
)


def _fold_plural(token: str) -> str:
    """Reduce simple English plurals so "lamps" and "lamp" share a token.

    The result only has to be the same for both forms, not a dictionary word:
    "movies" and "movie" both become "movy". Irregular plurals (shelves) are
    left alone.
    """
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith("ie"):
        return token[:-2] + "y"
    if len(token) > 4 and token.endswith(("ches", "shes", "sses", "xes", "zes")):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def tokenize(text: str) -> list[str]:
    """Lowercased word tokens with punctuation, accents and stopwords removed.

    Splitting on whitespace alone left punctuation attached, so "lamp," in a
    description never matched the query "lamp".
    """
    decomposed = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return [
        _fold_plural(token)
        for token in _TOKEN_RE.findall(plain)
        if len(token) > 1 and token not in _STOPWORDS
    ]


def text_to_vector(text: str) -> list[float]:
    """Unit-length bucket-count vector for `text` (all zeros when it has no tokens).

    The one vectoriser for queries and listings alike. The hash is a stable
    digest, not Python's per-process salted hash(), so vectors stored by one
    process are comparable in another.
    """
    vector = [0.0] * LEXICAL_VECTOR_SIZE
    for token in tokenize(text):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=4).digest()
        vector[int.from_bytes(digest, "big") % LEXICAL_VECTOR_SIZE] += 1.0
    norm = math.sqrt(sum(value * value for value in vector))
    if not norm:
        return vector
    return [round(value / norm, 6) for value in vector]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Dot product of two unit vectors, which is their cosine."""
    return sum(x * y for x, y in zip(a, b))


def listing_search_text(listing: Any) -> str:
    return f"{listing.title} {listing.description} {listing.category} {listing.condition} {listing.pickup_zone}"


def _text_fingerprint(text: str) -> str:
    return hashlib.blake2b(text.encode("utf-8"), digest_size=8).hexdigest()


def build_search_vector(listing: Any) -> dict[str, Any]:
    """What gets stored in Listing.embedding: the vector plus what it was built from.

    The fingerprint ties the vector to the exact text it was computed for, so
    a vector left behind by an edit can be recognised as stale when read.
    """
    text = listing_search_text(listing)
    return {"v": LEXICAL_VECTOR_VERSION, "h": _text_fingerprint(text), "vec": text_to_vector(text)}


def listing_search_vector(listing: Any) -> list[float]:
    """The listing's lexical vector: the stored one if still valid, else computed now.

    Every listing is therefore scored by the same formula on the same scale,
    whether or not the background task has stored its vector yet. Vectors
    from before LEXICAL_VECTOR_VERSION (a bare list) fail the check and are
    recomputed.
    """
    text = listing_search_text(listing)
    stored = getattr(listing, "embedding", None)
    if (
        isinstance(stored, dict)
        and stored.get("v") == LEXICAL_VECTOR_VERSION
        and stored.get("h") == _text_fingerprint(text)
    ):
        vector = stored.get("vec")
        if (
            isinstance(vector, list)
            and len(vector) == LEXICAL_VECTOR_SIZE
            and all(isinstance(value, (int, float)) for value in vector)
        ):
            return vector
    return text_to_vector(text)


# ---------------------------------------------------------------------------
# Trending (campus-level cache, per-user visibility on read)
# ---------------------------------------------------------------------------

TRENDING_WINDOW_HOURS = 6
# More candidates are cached than are shown, so that hiding some of them for
# one viewer (a block, a listing reserved since) still leaves a full strip.
TRENDING_CANDIDATE_LIMIT = 20
TRENDING_DISPLAY_LIMIT = 6
TRENDING_CACHE_TTL_SECONDS = 1800


def trending_cache_key(campus: Any) -> str:
    # A new key namespace: entries written by the previous format (rendered
    # payloads) must not be read as rows after a deploy.
    return f"trending_rows:{campus.slug if campus else 'all'}"


def compute_trending_rows(campus: Any) -> list[list[int]]:
    """Most viewed listings on a campus as [listing id, view count], best first.

    Holds only facts that are the same for every viewer on the campus. It is
    built with no user, so no one person's block list can shape what is
    cached for everyone else.
    """
    cutoff = timezone.now() - timedelta(hours=TRENDING_WINDOW_HOURS)
    candidates = marketplace_listing_queryset(None).filter(status=Listing.Status.AVAILABLE)
    if campus is not None:
        # Scope the aggregate itself: taking a global top-20 and filtering
        # afterwards leaves smaller campuses with an empty strip.
        candidates = candidates.filter(owner__campus=campus)
    rows = (
        ListingViewEvent.objects.filter(viewed_at__gte=cutoff, listing__in=candidates)
        .values("listing_id")
        .annotate(view_count=Count("id"))
        .order_by("-view_count", "listing_id")[:TRENDING_CANDIDATE_LIMIT]
    )
    return [[row["listing_id"], row["view_count"]] for row in rows]


def _valid_trending_rows(value: Any) -> list[list[int]] | None:
    if not isinstance(value, list):
        return None
    for row in value:
        if not (
            isinstance(row, (list, tuple))
            and len(row) == 2
            and all(isinstance(part, int) and not isinstance(part, bool) for part in row)
        ):
            return None
    return [list(row) for row in value]


def trending_rows(campus: Any) -> list[list[int]]:
    """Campus trending rows from the cache, computing and storing them on a miss."""
    key = trending_cache_key(campus)
    rows = _valid_trending_rows(cache.get(key))
    if rows is None:
        rows = compute_trending_rows(campus)
        cache.set(key, rows, timeout=TRENDING_CACHE_TTL_SECONDS)
    return rows


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
# Lexical search (served at the historical "semantic-search" path)
# ---------------------------------------------------------------------------

LEXICAL_SEARCH_POOL = 200


class SemanticSearchView(APIView):
    """
    POST /api/listings/semantic-search
    Body: { "query": "desk lamp", "limit": 20 }

    Despite the path, this is lexical similarity using the hashing trick, not
    embeddings: it ranks the newest LEXICAL_SEARCH_POOL listings the caller
    may see by how many (normalised) words they share with the query. It does
    not know that "something to sit on" means a chair. Listings that share no
    word bucket with the query are left out.
    """

    def post(self, request: Request) -> Response:
        query = str(request.data.get("query", "")).strip()[:200]
        limit = parse_int(request.data.get("limit"), field="limit", default=20, minimum=1, maximum=50) or 20
        if not query:
            return Response({"detail": "query is required."}, status=400)

        user = current_user(request)
        qs = marketplace_listing_queryset(user).filter(status=Listing.Status.AVAILABLE)
        listings = list(qs.order_by("-created_at")[:LEXICAL_SEARCH_POOL])

        query_vector = text_to_vector(query)
        scored = [
            (cosine_similarity(query_vector, listing_search_vector(listing)), listing)
            for listing in listings
        ]
        # sorted() is stable, so equal scores stay newest first.
        ranked = sorted((pair for pair in scored if pair[0] > 0), key=lambda pair: pair[0], reverse=True)
        top = [listing for _, listing in ranked[:limit]]

        from .listings import ListingSerializer
        return Response({
            "results": ListingSerializer(top, many=True, context={"request": request}).data,
            "query": query,
            "ranking": "lexical",
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
        user = current_user(request)
        campus = user.campus
        qs = visible_listing_queryset(user).filter(
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
    GET  /api/buildings  - list building coords for the user's campus
    POST /api/buildings  - create/update a building coord (campus manager only)
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

        building = str(request.data.get("building", "")).strip()[:120]
        lat = request.data.get("latitude")
        lng = request.data.get("longitude")
        if not building or lat is None or lng is None:
            return Response({"detail": "building, latitude, and longitude required."}, status=400)
        try:
            lat_value, lng_value = float(lat), float(lng)
        except (TypeError, ValueError):
            return Response({"detail": "latitude and longitude must be numbers."}, status=400)
        if not (-90 <= lat_value <= 90) or not (-180 <= lng_value <= 180):
            return Response({"detail": "latitude/longitude are out of range."}, status=400)

        coord, _ = BuildingCoord.objects.update_or_create(
            campus=campus,
            building=building,
            defaults={"latitude": lat_value, "longitude": lng_value},
        )
        return Response(BuildingCoordSerializer(coord).data, status=201)


# ---------------------------------------------------------------------------
# Trending listings
# ---------------------------------------------------------------------------

class TrendingListingsView(APIView):
    """
    GET /api/listings/trending
    Returns up to 6 listings with the most views in the last 6 hours on the
    user's campus.

    Only the campus-level ranking (listing ids and view counts) is cached, for
    30 minutes. Whether this viewer may see each listing is decided on every
    request, so blocks apply to the person who made them and to nobody else,
    and a listing reserved or flagged since the ranking was cached drops out.
    """

    def get(self, request: Request) -> Response:
        user = current_user(request)
        rows = trending_rows(user.campus)

        visible = {
            listing.id: listing
            for listing in marketplace_listing_queryset(user).filter(
                pk__in=[listing_id for listing_id, _ in rows],
                status=Listing.Status.AVAILABLE,
            )
        }

        results = []
        for listing_id, view_count in rows:
            listing = visible.get(listing_id)
            if listing is None:
                continue
            results.append({
                "id": listing.id,
                "title": listing.title,
                "category": listing.category,
                "price_type": listing.price_type,
                "price_amount": str(listing.price_amount),
                "image": listing.image_cdn_url,
                "view_count": view_count,
            })
            if len(results) >= TRENDING_DISPLAY_LIMIT:
                break

        return Response({"results": results})


# ---------------------------------------------------------------------------
# Saved searches
# ---------------------------------------------------------------------------

class SavedSearchListCreateView(APIView):
    """
    GET  /api/saved-searches       - list the user's saved searches
    POST /api/saved-searches       - create a saved search (max 5 per user)
    """

    def get(self, request: Request) -> Response:
        qs = SavedSearch.objects.filter(user=current_user(request))
        return Response(SavedSearchSerializer(qs, many=True).data)

    def post(self, request: Request) -> Response:
        user = current_user(request)
        if SavedSearch.objects.filter(user=user).count() >= 5:
            return Response({"detail": "Maximum 5 saved searches allowed."}, status=400)

        ser = SavedSearchSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            ser.save(user=request.user)
        except IntegrityError:
            # unique_saved_search: the same preset already exists.
            return Response({"detail": "You already saved this search."}, status=409)
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
