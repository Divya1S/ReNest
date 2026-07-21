from __future__ import annotations

import logging
from collections import defaultdict

from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import CollectionEvent, Listing, Reservation, StorageHold
from dormcycle.typed import current_user
from typing import Any

logger = logging.getLogger(__name__)


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

    def post(self, request: Request) -> Response:
        ids = request.data.get("reservation_ids", [])
        if not ids or not isinstance(ids, list):
            return Response({"detail": "reservation_ids list required."}, status=400)

        qs = Reservation.objects.filter(
            pk__in=ids,
            listing__owner=current_user(request),
            status=Reservation.Status.REQUESTED,
        ).select_related("listing")

        confirmed = []
        for reservation in qs:
            reservation.status = Reservation.Status.CONFIRMED
            reservation.save(update_fields=["status", "updated_at"])
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

    def get(self, request: Request) -> Response:
        hub_id = request.query_params.get("hub")
        qs = StorageHold.objects.filter(status=StorageHold.Status.ACTIVE).select_related(
            "listing", "held_by"
        )
        if hub_id:
            try:
                qs = qs.filter(hub_id=int(hub_id))
            except ValueError:
                return Response({"detail": "hub must be an integer id."}, status=400)
        return Response(StorageHoldSerializer(qs, many=True).data)

    def post(self, request: Request) -> Response:
        from datetime import timedelta
        listing_id = request.data.get("listing_id")
        hub_id = request.data.get("hub_id")
        note = request.data.get("note", "")
        hours = int(request.data.get("hold_hours", 48))

        if not listing_id or not hub_id:
            return Response({"detail": "listing_id and hub_id required."}, status=400)

        try:
            listing = Listing.objects.get(pk=listing_id)
        except Listing.DoesNotExist:
            return Response({"detail": "Listing not found."}, status=404)

        hold = StorageHold.objects.create(
            listing=listing,
            held_by=current_user(request),
            hub_id=hub_id,
            note=note,
            expires_at=timezone.now() + timedelta(hours=hours),
        )
        return Response(StorageHoldSerializer(hold).data, status=201)


class StorageHoldDetailView(APIView):
    """
    PATCH /api/logistics/holds/<pk>  — update status (release or mark collected)
    """

    def patch(self, request: Request, pk: int) -> Response:
        try:
            hold = StorageHold.objects.get(pk=pk)
        except StorageHold.DoesNotExist:
            return Response({"detail": "Not found."}, status=404)

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

    def get(self, request: Request) -> Response:
        hub_id = request.query_params.get("hub")
        qs = CollectionEvent.objects.select_related("organizer")
        if hub_id:
            try:
                qs = qs.filter(hub_id=int(hub_id))
            except ValueError:
                return Response({"detail": "hub must be an integer id."}, status=400)
        return Response(CollectionEventSerializer(qs[:20], many=True).data)

    def post(self, request: Request) -> Response:
        hub_id = request.data.get("hub_id")
        title = request.data.get("title", "Collection Run")
        scheduled_at = request.data.get("scheduled_at")
        hold_ids = request.data.get("hold_ids", [])

        if not hub_id or not scheduled_at:
            return Response({"detail": "hub_id and scheduled_at required."}, status=400)

        from django.utils.dateparse import parse_datetime
        parsed_at = parse_datetime(scheduled_at)
        if not parsed_at:
            return Response({"detail": "Invalid scheduled_at format. Use ISO 8601."}, status=400)

        event = CollectionEvent.objects.create(
            hub_id=hub_id,
            organizer=current_user(request),
            title=title,
            scheduled_at=parsed_at,
        )
        if hold_ids:
            holds = StorageHold.objects.filter(pk__in=hold_ids)
            event.holds.set(holds)

        return Response(CollectionEventSerializer(event).data, status=201)


class CollectionEventDetailView(APIView):
    """
    GET   /api/logistics/events/<pk>        — event detail with holds
    PATCH /api/logistics/events/<pk>        — update status / add holds / set route
    """

    def _get_event(self, pk: Any) -> Any:
        try:
            return CollectionEvent.objects.prefetch_related("holds__listing").get(pk=pk)
        except CollectionEvent.DoesNotExist:
            return None

    def get(self, request: Request, pk: int) -> Response:
        event = self._get_event(pk)
        if not event:
            return Response({"detail": "Not found."}, status=404)
        data = CollectionEventSerializer(event).data
        data["holds"] = StorageHoldSerializer(event.holds.all(), many=True).data
        return Response(data)

    def patch(self, request: Request, pk: int) -> Response:
        event = self._get_event(pk)
        if not event:
            return Response({"detail": "Not found."}, status=404)

        update_fields = []
        if "status" in request.data:
            event.status = request.data["status"]
            update_fields.append("status")
            if event.status == CollectionEvent.Status.COMPLETED:
                event.completed_at = timezone.now()
                update_fields.append("completed_at")
                # Mark all holds as collected
                event.holds.filter(status=StorageHold.Status.ACTIVE).update(
                    status=StorageHold.Status.COLLECTED
                )
        if "route_notes" in request.data:
            event.route_notes = request.data["route_notes"]
            update_fields.append("route_notes")
        if "hold_ids" in request.data:
            holds = StorageHold.objects.filter(pk__in=request.data["hold_ids"])
            event.holds.set(holds)

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

    def get(self, request: Request, pk: int) -> Response:
        try:
            event = CollectionEvent.objects.prefetch_related(
                "holds__listing"
            ).get(pk=pk)
        except CollectionEvent.DoesNotExist:
            return Response({"detail": "Not found."}, status=404)

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
