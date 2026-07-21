"""
Phase 27 — White-Label & Partner API

Endpoints:
  GET  /api/embed/:campus_slug/listings   — public listing feed (no auth, CORS-open)
  GET  /api/partner/listings              — listing feed (Bearer key auth)
  GET  /api/partner/reservations          — reservation feed (Bearer key auth)
  GET  /api/partner/usage                 — usage stats for key's campus
  GET  /api/partner/webhooks              — list webhook endpoints
  POST /api/partner/webhooks              — register a webhook endpoint
  DELETE /api/partner/webhooks/:pk        — remove a webhook endpoint
  POST /api/partner/keys                  — provision a new API key (campus-manager gated)
  GET  /api/campus/:slug/theme            — public campus theme (no auth)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging

from django.utils import timezone
from rest_framework import permissions
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.parsers import JSONParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import PartnerAPIKey, WebhookDelivery, WebhookEndpoint
from typing import Any, cast

logger = logging.getLogger(__name__)

_ALLOWED_FONTS = {"Inter", "Roboto", "Open Sans", "Lato", "Poppins", "system-ui"}


# ── Authentication ─────────────────────────────────────────────────────────────

class PartnerKeyAuthentication(BaseAuthentication):
    """Authenticate requests via `Authorization: Bearer dc_<token>`."""

    def authenticate(self, request: Any) -> Any:
        auth = request.META.get("HTTP_AUTHORIZATION", "")
        if not auth.startswith("Bearer "):
            return None
        raw = auth[7:].strip()
        if not raw:
            return None
        key_hash = hashlib.sha256(raw.encode()).hexdigest()
        try:
            key = PartnerAPIKey.objects.select_related("campus").get(
                key_hash=key_hash, active=True
            )
        except PartnerAPIKey.DoesNotExist:
            raise AuthenticationFailed("Invalid or inactive API key.")
        key.last_used_at = timezone.now()
        key.save(update_fields=["last_used_at"])
        return (key, None)

    def authenticate_header(self, request: Any) -> Any:
        return "Bearer"


class PartnerKeyPermission(permissions.BasePermission):
    """Require a PartnerAPIKey (not a regular user session)."""

    def has_permission(self, request: Any, view: Any) -> bool:
        return isinstance(request.auth, PartnerAPIKey) or (
            hasattr(request, "_partner_key")
        )


def _require_scope(request: Any, scope: str) -> Response | None:
    key = cast(PartnerAPIKey, request.auth)
    if scope not in (key.scopes or []):
        return Response({"detail": f"Scope '{scope}' not granted for this key."}, status=403)
    return None


# ── Embed endpoint (no auth, CORS-open) ────────────────────────────────────────

class EmbedListingsView(APIView):
    """GET /api/embed/:campus_slug/listings — unauthenticated public listing feed."""
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def get(self, request: Request, campus_slug: str) -> Response:
        from accounts.models import Campus

        from ..models import Listing
        from ..serializers import ListingSerializer

        try:
            campus = Campus.objects.get(slug=campus_slug, active=True)
        except Campus.DoesNotExist:
            return Response({"detail": "Campus not found."}, status=404)

        listings = (
            Listing.objects.filter(
                owner__campus=campus,
                status=Listing.Status.AVAILABLE,
                deleted_at__isnull=True,
            )
            .select_related("owner")
            .order_by("-created_at")[:20]
        )
        return Response(
            {
                "campus": campus.name,
                "count": listings.count(),
                "results": ListingSerializer(listings, many=True).data,
            },
            headers={"Access-Control-Allow-Origin": "*"},
        )


# ── Partner listings & reservations ───────────────────────────────────────────

class PartnerListingsView(APIView):
    """GET /api/partner/listings — listing feed for the key's campus."""
    authentication_classes = [PartnerKeyAuthentication]
    permission_classes = [PartnerKeyPermission]

    def get(self, request: Request) -> Response:
        err = _require_scope(request, "listing_read")
        if err:
            return err

        from ..models import Listing
        from ..serializers import ListingSerializer

        key = cast(PartnerAPIKey, request.auth)
        status_filter = request.query_params.get("status", "available")
        listings = (
            Listing.objects.filter(
                owner__campus=key.campus,
                deleted_at__isnull=True,
            )
            .order_by("-created_at")
        )
        if status_filter != "all":
            listings = listings.filter(status=status_filter)

        page_size = min(int(request.query_params.get("page_size", "50")), 200)
        page = max(int(request.query_params.get("page", "1")), 1)
        offset = (page - 1) * page_size
        return Response({
            "page": page,
            "page_size": page_size,
            "results": ListingSerializer(listings[offset: offset + page_size], many=True).data,
        })


class PartnerReservationsView(APIView):
    """GET /api/partner/reservations — reservation feed for the key's campus."""
    authentication_classes = [PartnerKeyAuthentication]
    permission_classes = [PartnerKeyPermission]

    def get(self, request: Request) -> Response:
        err = _require_scope(request, "reservation_read")
        if err:
            return err

        from ..models import Reservation
        from ..serializers import ReservationSerializer

        key = cast(PartnerAPIKey, request.auth)
        reservations = (
            Reservation.objects.filter(listing__owner__campus=key.campus)
            .select_related("listing", "claimant")
            .order_by("-created_at")[:200]
        )
        return Response({"results": ReservationSerializer(reservations, many=True).data})


# ── Usage stats ────────────────────────────────────────────────────────────────

class PartnerUsageView(APIView):
    """GET /api/partner/usage — usage stats for the key's campus."""
    authentication_classes = [PartnerKeyAuthentication]
    permission_classes = [PartnerKeyPermission]

    def get(self, request: Request) -> Response:
        from django.db.models import Count

        from ..models import Listing, Reservation

        key = cast(PartnerAPIKey, request.auth)

        listing_count = Listing.objects.filter(
            owner__campus=key.campus, deleted_at__isnull=True
        ).count()
        reserved_count = Reservation.objects.filter(
            listing__owner__campus=key.campus,
            status__in=["confirmed", "completed"],
        ).count()
        webhook_count = WebhookEndpoint.objects.filter(partner=key, active=True).count()
        delivery_stats = (
            WebhookDelivery.objects.filter(endpoint__partner=key)
            .values("status")
            .annotate(total=Count("id"))
        )

        return Response({
            "campus": key.campus.name,
            "partner": key.partner_name,
            "scopes": key.scopes,
            "listing_count": listing_count,
            "reservation_count": reserved_count,
            "webhook_endpoints": webhook_count,
            "webhook_deliveries": {row["status"]: row["total"] for row in delivery_stats},
            "rate_limit_per_day": key.rate_limit_per_day,
        })


# ── Webhook endpoint management ────────────────────────────────────────────────

class PartnerWebhookListCreateView(APIView):
    """GET/POST /api/partner/webhooks"""
    authentication_classes = [PartnerKeyAuthentication]
    permission_classes = [PartnerKeyPermission]
    parser_classes = [JSONParser]

    def get(self, request: Request) -> Response:
        err = _require_scope(request, "webhook")
        if err:
            return err
        key = cast(PartnerAPIKey, request.auth)
        endpoints = WebhookEndpoint.objects.filter(partner=key)
        return Response({
            "results": [
                {
                    "id": ep.id,
                    "url": ep.url,
                    "events": ep.events,
                    "active": ep.active,
                    "created_at": ep.created_at.isoformat(),
                }
                for ep in endpoints
            ]
        })

    def post(self, request: Request) -> Response:
        err = _require_scope(request, "webhook")
        if err:
            return err
        import secrets

        key = cast(PartnerAPIKey, request.auth)
        url = request.data.get("url", "").strip()
        events = request.data.get("events", [])
        if not url:
            return Response({"detail": "url is required."}, status=400)
        if not isinstance(events, list):
            return Response({"detail": "events must be a list."}, status=400)

        secret = secrets.token_hex(32)
        ep = WebhookEndpoint.objects.create(
            partner=key,
            url=url,
            secret=secret,
            events=events,
        )
        return Response({
            "id": ep.id,
            "url": ep.url,
            "events": ep.events,
            "secret": secret,
            "active": ep.active,
        }, status=201)


class PartnerWebhookDetailView(APIView):
    """DELETE /api/partner/webhooks/:pk"""
    authentication_classes = [PartnerKeyAuthentication]
    permission_classes = [PartnerKeyPermission]

    def delete(self, request: Request, pk: int) -> Response:
        err = _require_scope(request, "webhook")
        if err:
            return err
        key = cast(PartnerAPIKey, request.auth)
        deleted, _ = WebhookEndpoint.objects.filter(pk=pk, partner=key).delete()
        if not deleted:
            return Response({"detail": "Not found."}, status=404)
        return Response({"deleted": True})


# ── API key provisioning (campus-manager gated) ────────────────────────────────

class PartnerKeyProvisionView(APIView):
    """
    POST /api/partner/keys — provision a new partner API key.
    Gated to authenticated campus managers / staff.
    Returns the raw key once — it is never stored.
    """
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [JSONParser]

    def post(self, request: Request) -> Response:
        user = request.user
        campus = getattr(user, "campus", None)
        if not campus:
            return Response({"detail": "No campus associated with your account."}, status=400)
        if not (user.is_staff or getattr(user, "is_campus_manager", False)):
            return Response({"detail": "Campus manager or staff required."}, status=403)

        partner_name = request.data.get("partner_name", "").strip()
        scopes = request.data.get("scopes", ["listing_read"])
        if not partner_name:
            return Response({"detail": "partner_name is required."}, status=400)
        if not isinstance(scopes, list):
            return Response({"detail": "scopes must be a list."}, status=400)

        allowed_scopes = {"listing_read", "reservation_read", "webhook"}
        invalid = set(scopes) - allowed_scopes
        if invalid:
            return Response({"detail": f"Invalid scopes: {invalid}"}, status=400)

        key_obj, raw_key = PartnerAPIKey.generate(
            campus=campus,
            partner_name=partner_name,
            scopes=scopes,
        )
        return Response({
            "id": key_obj.pk,
            "partner_name": key_obj.partner_name,
            "key": raw_key,
            "scopes": key_obj.scopes,
            "note": "Store this key securely — it will not be shown again.",
        }, status=201)


# ── Campus theme (public, no auth) ─────────────────────────────────────────────

_SAFE_HEX_RE = __import__("re").compile(r"^#[0-9A-Fa-f]{3,8}$")


class CampusThemeView(APIView):
    """GET /api/campus/:slug/theme — return the campus white-label theme."""
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def get(self, request: Request, slug: str) -> Response:
        from accounts.models import Campus

        try:
            campus = Campus.objects.only("name", "slug", "theme").get(slug=slug, active=True)
        except Campus.DoesNotExist:
            return Response({"detail": "Campus not found."}, status=404)

        raw = campus.theme or {}
        theme = _sanitise_theme(raw)
        return Response({"campus": campus.name, "slug": campus.slug, "theme": theme})


def _sanitise_theme(raw: dict) -> dict:
    """Return a server-validated copy of the theme dict."""
    out: dict = {}
    primary = raw.get("primary_color", "")
    if primary and _SAFE_HEX_RE.match(primary):
        out["primary_color"] = primary
    logo = raw.get("logo_url", "")
    if logo and logo.startswith("https://"):
        out["logo_url"] = logo
    font = raw.get("font_family", "")
    if font in _ALLOWED_FONTS:
        out["font_family"] = font
    hero = raw.get("hero_message", "")
    if isinstance(hero, str):
        out["hero_message"] = hero[:160]
    return out


# ── Webhook dispatch helper (called from tasks.py) ─────────────────────────────

def dispatch_webhook_event(event_type: str, payload: dict, campus_name: str) -> int:
    """
    Fire `event_type` to all active webhook endpoints subscribed to this event
    for the given campus. Creates WebhookDelivery records and enqueues the task.
    Returns the number of endpoints targeted.
    """
    from ..tasks import deliver_webhook

    endpoints = (
        WebhookEndpoint.objects.filter(
            partner__campus__name=campus_name,
            active=True,
        )
        .select_related("partner")
    )
    count = 0
    for ep in endpoints:
        if event_type not in (ep.events or []):
            continue
        delivery = WebhookDelivery.objects.create(
            endpoint=ep,
            event_type=event_type,
            payload=payload,
        )
        deliver_webhook.apply_async((delivery.pk,), countdown=2)
        count += 1
    return count
