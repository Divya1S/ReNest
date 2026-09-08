from __future__ import annotations

import hashlib
import logging
import hmac
import secrets
import textwrap
from datetime import timezone as dt_timezone

from django.conf import settings
from django.core.cache import cache
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

logger = logging.getLogger(__name__)

# Incorrect PIN entries tolerated per reservation per hour.
_MAX_PIN_ATTEMPTS = 8


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
            if not isinstance(raw_slots, list) or not 1 <= len(raw_slots) <= 5:
                return Response({"detail": "Provide 1–5 slot datetimes."}, status=400)
            # Validate and normalise to ISO strings
            now = timezone.now()
            validated = []
            for s in raw_slots:
                ser = drf_serializers.DateTimeField()
                try:
                    dt = ser.to_internal_value(s)
                except Exception:
                    return Response({"detail": f"Invalid datetime: {s}"}, status=400)
                if dt <= now:
                    return Response({"detail": "Pickup slots must be in the future."}, status=400)
                validated.append(dt.isoformat())
            reservation.pickup_slots = validated
            # Re-proposing invalidates whatever the claimant confirmed before.
            reservation.confirmed_slot = None
            reservation.save(update_fields=["pickup_slots", "confirmed_slot", "updated_at"])
            # Notify claimant. The dedupe key includes the proposal itself, so a
            # genuinely new set of times notifies again while a retry does not.
            proposal_digest = hashlib.sha256("|".join(validated).encode()).hexdigest()[:12]
            Notification.objects.get_or_create(
                dedupe_key=f"slots:{reservation.pk}:{proposal_digest}",
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

        # A 6-digit PIN is brute-forceable in ~1M guesses; cap attempts per
        # reservation so an owner cannot grind the claimant's code.
        attempts_key = f"pin-attempts:{reservation.pk}"
        attempts = cache.get(attempts_key, 0)
        if attempts >= _MAX_PIN_ATTEMPTS:
            return Response(
                {"detail": "Too many incorrect PIN attempts. Try again in an hour."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        submitted = str(request.data.get("pin", "")).strip()
        if not hmac.compare_digest(submitted, reservation.handoff_pin):
            cache.set(attempts_key, attempts + 1, timeout=3600)
            return Response({"detail": "Incorrect PIN."}, status=status.HTTP_400_BAD_REQUEST)
        cache.delete(attempts_key)

        # One shared transition so the PIN path closes the rescue request,
        # invalidates dashboards and sends the same emails as a status PATCH.
        from ..services import complete_reservation

        reservation = complete_reservation(reservation, notify=False)
        listing = reservation.listing

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

        # Post-completion emails (feedback prompt + impact summary).
        try:
            from ..tasks import send_reservation_completed_email_task
            send_reservation_completed_email_task.delay(reservation.pk)
        except Exception:
            logger.exception("Completion emails could not be scheduled for reservation %s", reservation.pk)

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
        # /handoffs/ (plural) is the SPA route; /handoff/ 404s.
        deep_link = f"{settings.APP_BASE_URL}/handoffs/{reservation.pk}"

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

        def _ical_text(value: Any) -> str:
            """Escape per RFC 5545: backslash, semicolon, comma and newlines.

            A listing title containing a comma would otherwise split the
            property value and corrupt the whole calendar entry.
            """
            text = str(value or "")
            text = text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            return text.replace("\r\n", "\\n").replace("\n", "\\n")

        uid = f"renest-reservation-{reservation.pk}@renest.app"
        summary = f"Pickup: {listing_title}"
        description = "\n".join([
            f"ReNest pickup for: {listing_title}",
            f"Location: {pickup_zone}",
            f"Handoff hub: {deep_link}",
        ])

        ical = "\r\n".join([
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//ReNest//ReNest Handoff//EN",
            "CALSCALE:GREGORIAN",
            "METHOD:PUBLISH",
            "BEGIN:VEVENT",
            f"UID:{uid}",
            # DTSTAMP is required by RFC 5545; some clients reject the file without it.
            f"DTSTAMP:{_ical_dt(timezone.now().astimezone(dt_timezone.utc))}",
            f"SUMMARY:{_ical_text(summary)}",
            f"DESCRIPTION:{_ical_text(description)}",
            f"LOCATION:{_ical_text(pickup_zone)}",
            f"DTSTART:{_ical_dt(dt_start)}",
            f"DTEND:{_ical_dt(dt_end)}",
            f"URL:{deep_link}",
            "END:VEVENT",
            "END:VCALENDAR",
        ])

        return HttpResponse(
            ical,
            content_type="text/calendar; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="dormcycle-pickup-{reservation.pk}.ics"'},
        )
