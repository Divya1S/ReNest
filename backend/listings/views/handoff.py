from __future__ import annotations

import secrets
import textwrap
from datetime import timezone as dt_timezone

from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import permissions, serializers as drf_serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import Notification, Reservation
from typing import Any


def _get_reservation_for_participant(pk: int, user: Any) -> Reservation:
    return get_object_or_404(
        Reservation.objects.select_related("listing", "listing__owner", "claimant"),
        Q(claimant=user) | Q(listing__owner=user),
        pk=pk,
    )


class ReservationSlotsView(APIView):
    """
    PATCH /api/reservations/:id/slots
    Owner: propose up to 5 pickup slots.
    Claimant: confirm one slot from the list.
    """

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        operation_id="reservation_slots",
        request=inline_serializer(
            "ReservationSlotsRequest",
            fields={
                "slots": drf_serializers.ListField(
                    child=drf_serializers.DateTimeField(), required=False, max_length=5
                ),
                "confirm_slot": drf_serializers.DateTimeField(required=False),
            },
        ),
        responses={200: inline_serializer(
            "ReservationSlotsResponse",
            fields={
                "pickup_slots": drf_serializers.ListField(child=drf_serializers.DateTimeField()),
                "confirmed_slot": drf_serializers.DateTimeField(allow_null=True),
            },
        )},
    )
    def patch(self, request: Request, pk: int) -> Response:
        reservation = _get_reservation_for_participant(pk, request.user)
        if reservation.status not in (Reservation.Status.CONFIRMED, Reservation.Status.REQUESTED):
            return Response(
                {"detail": "Slots can only be negotiated on active reservations."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        is_owner = request.user == reservation.listing.owner
        is_claimant = request.user == reservation.claimant

        if "slots" in request.data:
            if not is_owner:
                return Response({"detail": "Only the owner can propose slots."}, status=403)
            raw_slots = request.data["slots"]
            if not isinstance(raw_slots, list) or len(raw_slots) > 5:
                return Response({"detail": "Provide 1–5 slot datetimes."}, status=400)
            # Validate and normalise to ISO strings
            validated = []
            for s in raw_slots:
                ser = drf_serializers.DateTimeField()
                try:
                    dt = ser.to_internal_value(s)
                    validated.append(dt.isoformat())
                except Exception:
                    return Response({"detail": f"Invalid datetime: {s}"}, status=400)
            reservation.pickup_slots = validated
            reservation.save(update_fields=["pickup_slots", "updated_at"])
            # Notify claimant
            Notification.objects.get_or_create(
                dedupe_key=f"slots:{reservation.pk}:{len(validated)}",
                defaults=dict(
                    user=reservation.claimant,
                    type=Notification.Type.RESERVATION,
                    title="Pickup times proposed",
                    body=f"{reservation.listing.owner.display_name} suggested {len(validated)} pickup slot(s) for {reservation.listing.title}.",
                    link_path=f"/handoffs/{reservation.pk}",
                    priority=Notification.Priority.HIGH,
                ),
            )

        elif "confirm_slot" in request.data:
            if not is_claimant:
                return Response({"detail": "Only the claimant can confirm a slot."}, status=403)
            ser = drf_serializers.DateTimeField()
            try:
                confirmed_dt = ser.to_internal_value(request.data["confirm_slot"])
            except Exception:
                return Response({"detail": "Invalid datetime."}, status=400)
            if confirmed_dt.isoformat() not in reservation.pickup_slots:
                return Response({"detail": "Slot not in the proposed list."}, status=400)
            reservation.confirmed_slot = confirmed_dt
            reservation.save(update_fields=["confirmed_slot", "updated_at"])
            # Notify owner
            Notification.objects.get_or_create(
                dedupe_key=f"slot_confirmed:{reservation.pk}",
                defaults=dict(
                    user=reservation.listing.owner,
                    type=Notification.Type.RESERVATION,
                    title="Pickup time confirmed",
                    body=f"{reservation.claimant.display_name} confirmed a pickup slot for {reservation.listing.title}.",
                    link_path=f"/handoffs/{reservation.pk}",
                    priority=Notification.Priority.HIGH,
                ),
            )
        else:
            return Response({"detail": "Provide 'slots' or 'confirm_slot'."}, status=400)

        return Response({
            "pickup_slots": reservation.pickup_slots,
            "confirmed_slot": reservation.confirmed_slot.isoformat() if reservation.confirmed_slot else None,
        })


class ReservationVerifyPinView(APIView):
    """
    POST /api/reservations/:id/verify-pin  { "pin": "123456" }
    Owner submits the PIN the claimant tells them. If correct, marks COMPLETED.
    """

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        operation_id="reservation_verify_pin",
        request=inline_serializer(
            "VerifyPinRequest",
            fields={"pin": drf_serializers.CharField(max_length=6)},
        ),
        responses={200: inline_serializer(
            "VerifyPinResponse",
            fields={"detail": drf_serializers.CharField()},
        )},
    )
    def post(self, request: Request, pk: int) -> Response:
        reservation = _get_reservation_for_participant(pk, request.user)
        if request.user != reservation.listing.owner:
            return Response({"detail": "Only the owner can verify the PIN."}, status=403)
        if reservation.status != Reservation.Status.CONFIRMED:
            return Response({"detail": "PIN verification is only valid for confirmed reservations."}, status=400)
        if not reservation.handoff_pin:
            return Response({"detail": "No PIN has been generated for this reservation."}, status=400)

        submitted = str(request.data.get("pin", "")).strip()
        if submitted != reservation.handoff_pin:
            return Response({"detail": "Incorrect PIN."}, status=status.HTTP_400_BAD_REQUEST)

        reservation.status = Reservation.Status.COMPLETED
        reservation.save(update_fields=["status", "updated_at"])

        # Update listing status
        listing = reservation.listing
        listing.status = listing.Status.PICKED_UP
        listing.save(update_fields=["status", "updated_at"])

        # Notify claimant
        Notification.objects.get_or_create(
            dedupe_key=f"pin_complete:{reservation.pk}",
            defaults=dict(
                user=reservation.claimant,
                type=Notification.Type.RESERVATION,
                title="Handoff complete!",
                body=f"The owner confirmed your pickup of {listing.title}. Leave feedback to close the loop.",
                link_path=f"/handoffs/{reservation.pk}",
                priority=Notification.Priority.HIGH,
            ),
        )

        # Trigger post-completion emails async
        try:
            from ..tasks import send_reservation_completed_email_task
            send_reservation_completed_email_task.delay(reservation.pk)
        except Exception:
            pass

        return Response({"detail": "Handoff marked complete."})


class ReservationEnRouteView(APIView):
    """
    POST /api/reservations/:id/en-route
    Claimant taps "I'm heading over." Sends a push + chat message to the owner.
    """

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        operation_id="reservation_en_route",
        request=None,
        responses={200: inline_serializer(
            "EnRouteResponse",
            fields={"detail": drf_serializers.CharField()},
        )},
    )
    def post(self, request: Request, pk: int) -> Response:
        reservation = _get_reservation_for_participant(pk, request.user)
        if request.user != reservation.claimant:
            return Response({"detail": "Only the claimant can send the en-route signal."}, status=403)
        if reservation.status != Reservation.Status.CONFIRMED:
            return Response({"detail": "The reservation must be confirmed first."}, status=400)

        claimant_name = reservation.claimant.display_name or reservation.claimant.email.split("@")[0]

        # Log as a chat message so it appears in the thread
        from ..models import ReservationMessage
        ReservationMessage.objects.create(
            reservation=reservation,
            sender=request.user,
            body=f"🚶 {claimant_name} is on their way to pick up {reservation.listing.title}.",
        )

        # Notify owner
        Notification.objects.get_or_create(
            dedupe_key=f"en_route:{reservation.pk}:{timezone.now().strftime('%Y%m%d%H')}",
            defaults=dict(
                user=reservation.listing.owner,
                type=Notification.Type.RESERVATION,
                title=f"{claimant_name} is on the way",
                body=f"Head to {reservation.listing.pickup_zone} — they're coming for {reservation.listing.title}.",
                link_path=f"/handoffs/{reservation.pk}",
                priority=Notification.Priority.HIGH,
            ),
        )

        return Response({"detail": "Owner notified."})


class ReservationCalendarView(APIView):
    """
    GET /api/reservations/:id/calendar.ics
    Returns an iCalendar file for the confirmed pickup slot (or pickup_time_window).
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request, pk: int) -> HttpResponse:
        reservation = _get_reservation_for_participant(pk, request.user)

        listing_title = reservation.listing.title
        pickup_zone = reservation.listing.pickup_zone
        deep_link = f"https://renest.app/handoff/{reservation.pk}"

        # Use confirmed_slot if available, otherwise now + 1h as fallback
        if reservation.confirmed_slot:
            dt_start = reservation.confirmed_slot.astimezone(dt_timezone.utc)
        else:
            from datetime import timedelta
            dt_start = timezone.now().astimezone(dt_timezone.utc)

        from datetime import timedelta
        dt_end = dt_start + timedelta(hours=1)

        def _ical_dt(dt: Any) -> str:
            return dt.strftime("%Y%m%dT%H%M%SZ")

        uid = f"renest-reservation-{reservation.pk}@renest.app"
        summary = f"Pickup: {listing_title}"
        description = textwrap.dedent(f"""\
            ReNest pickup for: {listing_title}
            Location: {pickup_zone}
            Handoff hub: {deep_link}
        """).strip()

        ical = textwrap.dedent(f"""\
            BEGIN:VCALENDAR
            VERSION:2.0
            PRODID:-//ReNest//ReNest Handoff//EN
            BEGIN:VEVENT
            UID:{uid}
            SUMMARY:{summary}
            DESCRIPTION:{description.replace(chr(10), "\\n")}
            LOCATION:{pickup_zone}
            DTSTART:{_ical_dt(dt_start)}
            DTEND:{_ical_dt(dt_end)}
            URL:{deep_link}
            END:VEVENT
            END:VCALENDAR
        """).strip()

        return HttpResponse(
            ical,
            content_type="text/calendar; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="dormcycle-pickup-{reservation.pk}.ics"'},
        )
