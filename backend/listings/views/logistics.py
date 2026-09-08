from __future__ import annotations

import logging
from collections import defaultdict
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework import permissions, serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from dormcycle.parsers import StrictJSONParser
from hubs.models import DonationHub
from ..models import CollectionEvent, Listing, Reservation, StorageHold
from .helpers import parse_int, parse_int_list
from dormcycle.typed import current_user
from typing import Any

logger = logging.getLogger(__name__)

# A collection run is a real logistics operation; one request should never
# schedule an unbounded amount of work.
_MAX_BATCH = 50
_MAX_HOLD_HOURS = 24 * 14


def is_hub_manager(user: Any, hub_id: Any) -> bool:
    """Whether this user may act on the given hub.

    Storage holds and collection events are hub operations: only the hub's
    managers (or staff) may create, read or complete them. Without this check
    any signed-in student could schedule collection runs for any hub.
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_staff:
        return True
    if hub_id is None:
        return False
    return DonationHub.objects.filter(pk=hub_id, hub_managers__user=user).exists()


def managed_hub_ids(user: Any) -> Any:
    """Hub ids this user manages; None means "all" (staff)."""
    if user.is_staff:
        return None
    return list(DonationHub.objects.filter(hub_managers__user=user).values_list("id", flat=True))


def _forbidden(detail: str = "You do not manage this hub.") -> Response:
    return Response({"detail": detail}, status=status.HTTP_403_FORBIDDEN)


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------

class StorageHoldSerializer(serializers.ModelSerializer):
    listing_title = serializers.CharField(source="listing.title", read_only=True)
    held_by_name = serializers.CharField(source="held_by.display_name", read_only=True)

    class Meta:
        model = StorageHold
        fields = [
            "id", "listing", "listing_title", "held_by_name",
            "hub", "status", "note", "expires_at", "created_at",
        ]
        read_only_fields = ["id", "created_at", "held_by_name", "listing_title"]


class CollectionEventSerializer(serializers.ModelSerializer):
    holds_count = serializers.SerializerMethodField()
    organizer_name = serializers.CharField(source="organizer.display_name", read_only=True)

    def get_holds_count(self, obj: Any) -> Any:
        return obj.holds.count()

    class Meta:
        model = CollectionEvent
        fields = [
            "id", "hub", "title", "scheduled_at", "status",
            "organizer_name", "holds_count", "route_notes", "completed_at", "created_at",
        ]
        read_only_fields = ["id", "created_at", "organizer_name", "holds_count"]


# ---------------------------------------------------------------------------
# Batch reservation confirm
# ---------------------------------------------------------------------------

class BatchConfirmReservationsView(APIView):
    """
    POST /api/logistics/batch-confirm
    Body: { "reservation_ids": [1, 2, 3] }
    Confirms multiple pending reservations owned by the user in one call.
    """

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [StrictJSONParser]

    def post(self, request: Request) -> Response:
        ids = parse_int_list(
            request.data.get("reservation_ids", []), field="reservation_ids", max_items=_MAX_BATCH
        )

        qs = Reservation.objects.filter(
            pk__in=ids,
            listing__owner=current_user(request),
            listing__is_demo=False,
            status=Reservation.Status.REQUESTED,
        ).select_related("listing")

        # Goes through the shared transition so batch-confirmed handoffs get
        # their PIN, listing status, emails and cache invalidation. A bare
        # status write here used to leave PIN verification permanently broken.
        from ..services import confirm_reservation

        confirmed = []
        for reservation in qs:
            try:
                confirm_reservation(reservation)
            except Exception:
                logger.exception("Batch confirm failed for reservation %s", reservation.pk)
                continue
            confirmed.append(reservation.id)

        return Response({"confirmed": confirmed, "count": len(confirmed)})


# ---------------------------------------------------------------------------
# Storage hold endpoints
# ---------------------------------------------------------------------------

class StorageHoldListCreateView(APIView):
    """
    GET  /api/logistics/holds?hub=<id>  — list active holds for a hub
    POST /api/logistics/holds           — place a hold on a listing
    """

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [StrictJSONParser]

    def get(self, request: Request) -> Response:
        user = current_user(request)
        hub_id = parse_int(request.query_params.get("hub"), field="hub", minimum=1)
        qs = StorageHold.objects.filter(
            status=StorageHold.Status.ACTIVE, expires_at__gt=timezone.now()
        ).select_related("listing", "held_by")

        allowed = managed_hub_ids(user)
        if allowed is not None:
            qs = qs.filter(hub_id__in=allowed)
        if hub_id is not None:
            if not is_hub_manager(user, hub_id):
                return _forbidden()
            qs = qs.filter(hub_id=hub_id)
        return Response(StorageHoldSerializer(qs, many=True).data)

    def post(self, request: Request) -> Response:
        listing_id = parse_int(request.data.get("listing_id"), field="listing_id", minimum=1)
        hub_id = parse_int(request.data.get("hub_id"), field="hub_id", minimum=1)
        note = str(request.data.get("note", ""))[:500]
        hours = parse_int(
            request.data.get("hold_hours"), field="hold_hours", default=48, minimum=1, maximum=_MAX_HOLD_HOURS
        ) or 48

        if listing_id is None or hub_id is None:
            return Response({"detail": "listing_id and hub_id required."}, status=400)
        if not is_hub_manager(current_user(request), hub_id):
            return _forbidden()

        try:
            listing = Listing.objects.get(pk=listing_id, is_demo=False)
        except Listing.DoesNotExist:
            return Response({"detail": "Listing not found."}, status=404)

        now = timezone.now()
        existing = StorageHold.objects.filter(
            listing=listing, status=StorageHold.Status.ACTIVE, expires_at__gt=now
        ).first()
        if existing is not None:
            return Response(
                {"detail": "This listing is already on hold.", "hold_id": existing.pk},
                status=409,
            )

        hold = StorageHold.objects.create(
            listing=listing,
            held_by=current_user(request),
            hub_id=hub_id,
            note=note,
            expires_at=now + timedelta(hours=hours),
        )
        return Response(StorageHoldSerializer(hold).data, status=201)


class StorageHoldDetailView(APIView):
    """
    PATCH /api/logistics/holds/<pk>  — update status (release or mark collected)
    """

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [StrictJSONParser]

    def patch(self, request: Request, pk: int) -> Response:
        try:
            hold = StorageHold.objects.get(pk=pk)
        except StorageHold.DoesNotExist:
            return Response({"detail": "Not found."}, status=404)
        if not is_hub_manager(current_user(request), hold.hub_id):
            return _forbidden()

        new_status = request.data.get("status")
        if new_status not in (StorageHold.Status.RELEASED, StorageHold.Status.COLLECTED):
            return Response({"detail": "status must be 'released' or 'collected'."}, status=400)

        hold.status = new_status
        hold.save(update_fields=["status", "updated_at"])
        return Response(StorageHoldSerializer(hold).data)


# ---------------------------------------------------------------------------
# Collection event endpoints
# ---------------------------------------------------------------------------

class CollectionEventListCreateView(APIView):
    """
    GET  /api/logistics/events?hub=<id>  — list events for a hub
    POST /api/logistics/events           — schedule a new collection event
    """

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [StrictJSONParser]

    def get(self, request: Request) -> Response:
        user = current_user(request)
        hub_id = parse_int(request.query_params.get("hub"), field="hub", minimum=1)
        qs = CollectionEvent.objects.select_related("organizer").order_by("-scheduled_at")

        allowed = managed_hub_ids(user)
        if allowed is not None:
            qs = qs.filter(hub_id__in=allowed)
        if hub_id is not None:
            if not is_hub_manager(user, hub_id):
                return _forbidden()
            qs = qs.filter(hub_id=hub_id)
        return Response(CollectionEventSerializer(qs[:20], many=True).data)

    def post(self, request: Request) -> Response:
        hub_id = parse_int(request.data.get("hub_id"), field="hub_id", minimum=1)
        title = str(request.data.get("title") or "Collection Run")[:120]
        scheduled_at = request.data.get("scheduled_at")
        hold_ids = request.data.get("hold_ids", [])

        if hub_id is None or not scheduled_at:
            return Response({"detail": "hub_id and scheduled_at required."}, status=400)
        if not is_hub_manager(current_user(request), hub_id):
            return _forbidden()

        from django.utils.dateparse import parse_datetime
        parsed_at = parse_datetime(str(scheduled_at))
        if not parsed_at:
            return Response({"detail": "Invalid scheduled_at format. Use ISO 8601."}, status=400)
        if timezone.is_naive(parsed_at):
            parsed_at = timezone.make_aware(parsed_at)

        with transaction.atomic():
            event = CollectionEvent.objects.create(
                hub_id=hub_id,
                organizer=current_user(request),
                title=title,
                scheduled_at=parsed_at,
            )
            if hold_ids:
                ids = parse_int_list(hold_ids, field="hold_ids", max_items=_MAX_BATCH)
                event.holds.set(StorageHold.objects.filter(pk__in=ids, hub_id=hub_id))

        return Response(CollectionEventSerializer(event).data, status=201)


class CollectionEventDetailView(APIView):
    """
    GET   /api/logistics/events/<pk>        — event detail with holds
    PATCH /api/logistics/events/<pk>        — update status / add holds / set route
    """

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [StrictJSONParser]

    def _get_event(self, pk: Any) -> Any:
        try:
            return CollectionEvent.objects.prefetch_related("holds__listing").get(pk=pk)
        except CollectionEvent.DoesNotExist:
            return None

    def get(self, request: Request, pk: int) -> Response:
        event = self._get_event(pk)
        if not event:
            return Response({"detail": "Not found."}, status=404)
        if not is_hub_manager(current_user(request), event.hub_id):
            return _forbidden()
        data = CollectionEventSerializer(event).data
        data["holds"] = StorageHoldSerializer(event.holds.all(), many=True).data
        return Response(data)

    def patch(self, request: Request, pk: int) -> Response:
        event = self._get_event(pk)
        if not event:
            return Response({"detail": "Not found."}, status=404)
        if not is_hub_manager(current_user(request), event.hub_id):
            return _forbidden()

        update_fields = []
        if "status" in request.data:
            new_status = request.data["status"]
            if new_status not in CollectionEvent.Status.values:
                return Response(
                    {"status": f"Must be one of {list(CollectionEvent.Status.values)}."}, status=400
                )
            event.status = new_status
            update_fields.append("status")
            if event.status == CollectionEvent.Status.COMPLETED:
                event.completed_at = timezone.now()
                update_fields.append("completed_at")
                # Mark all holds as collected
                event.holds.filter(status=StorageHold.Status.ACTIVE).update(
                    status=StorageHold.Status.COLLECTED
                )
        if "route_notes" in request.data:
            event.route_notes = str(request.data["route_notes"])[:2000]
            update_fields.append("route_notes")
        if "hold_ids" in request.data:
            ids = parse_int_list(request.data["hold_ids"], field="hold_ids", max_items=_MAX_BATCH)
            event.holds.set(StorageHold.objects.filter(pk__in=ids, hub_id=event.hub_id))

        if update_fields:
            update_fields.append("updated_at")
            event.save(update_fields=update_fields)

        data = CollectionEventSerializer(event).data
        data["holds"] = StorageHoldSerializer(event.holds.all(), many=True).data
        return Response(data)


# ---------------------------------------------------------------------------
# Route optimiser — groups holds by building for a collection event
# ---------------------------------------------------------------------------

class RouteOptimiseView(APIView):
    """
    GET /api/logistics/events/<pk>/route
    Returns holds grouped by building in a suggested pickup order.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request, pk: int) -> Response:
        try:
            event = CollectionEvent.objects.prefetch_related(
                "holds__listing"
            ).get(pk=pk)
        except CollectionEvent.DoesNotExist:
            return Response({"detail": "Not found."}, status=404)
        if not is_hub_manager(current_user(request), event.hub_id):
            return _forbidden()

        # Group holds by building; sort buildings alphabetically as a simple
        # proxy for adjacency (works well within a single dorm complex).
        by_building: dict[str, list] = defaultdict(list)
        for hold in event.holds.filter(status=StorageHold.Status.ACTIVE):
            building = hold.listing.building or "Unknown"
            by_building[building].append({
                "hold_id": hold.id,
                "listing_id": hold.listing.id,
                "listing_title": hold.listing.title,
                "pickup_zone": hold.listing.pickup_zone,
                "note": hold.note,
            })

        stops = [{"building": b, "items": items} for b, items in sorted(by_building.items())]
        return Response({
            "event_id": event.id,
            "title": event.title,
            "stop_count": len(stops),
            "stops": stops,
        })
