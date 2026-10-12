"""
Trust & Safety social endpoints:

  POST   /api/users/:id/block       - block a user
  DELETE /api/users/:id/block       - unblock a user
  POST   /api/reservations/:id/dispute  - open a dispute
  PATCH  /api/disputes/:id          - resolve/dismiss (staff only)
  GET    /api/disputes              - list disputes (staff: all; user: own)
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import permissions, serializers as drf_serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import BlockedUser, Dispute, Notification, Reservation
from dormcycle.typed import current_user

User = get_user_model()


# ---------------------------------------------------------------------------
# Blocking
# ---------------------------------------------------------------------------

class BlockUserView(APIView):
    """
    POST   /api/users/:id/block  - block the user
    DELETE /api/users/:id/block  - unblock the user
    """

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(operation_id="block_user", request=None, responses={201: None, 200: None})
    def post(self, request: Request, pk: int) -> Response:
        target = get_object_or_404(User, pk=pk)
        if target.pk == request.user.pk:
            return Response({"detail": "You cannot block yourself."}, status=400)
        _, created = BlockedUser.objects.get_or_create(blocker=current_user(request), blocked=target)
        return Response(
            {"blocked": True, "user_id": target.pk},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @extend_schema(operation_id="unblock_user", request=None, responses={200: None})
    def delete(self, request: Request, pk: int) -> Response:
        target = get_object_or_404(User, pk=pk)
        BlockedUser.objects.filter(blocker=current_user(request), blocked=target).delete()
        return Response({"blocked": False, "user_id": target.pk})


# ---------------------------------------------------------------------------
# Disputes
# ---------------------------------------------------------------------------

_DisputeSerializer = inline_serializer(
    "DisputeDetail",
    fields={
        "id": drf_serializers.IntegerField(),
        "reservation_id": drf_serializers.IntegerField(),
        "opened_by_id": drf_serializers.IntegerField(),
        "reason": drf_serializers.CharField(),
        "status": drf_serializers.CharField(),
        "resolved_by_id": drf_serializers.IntegerField(allow_null=True),
        "resolution_note": drf_serializers.CharField(allow_blank=True),
        "created_at": drf_serializers.DateTimeField(),
    },
)


def _dispute_to_dict(d: Dispute) -> dict:
    return {
        "id": d.pk,
        "reservation_id": d.reservation_id,
        "opened_by_id": d.opened_by_id,
        "reason": d.reason,
        "status": d.status,
        "resolved_by_id": d.resolved_by_id,
        "resolution_note": d.resolution_note,
        "created_at": d.created_at,
    }


def _disputed_party_id(reservation: Reservation, opened_by_id: int) -> int:
    """The participant a dispute is raised against: whoever did not open it."""
    if opened_by_id == reservation.claimant_id:
        return int(reservation.listing.owner_id)
    return int(reservation.claimant_id)


class ReservationDisputeView(APIView):
    """POST /api/reservations/:id/dispute: open a dispute on a completed reservation."""

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        operation_id="open_dispute",
        request=inline_serializer(
            "OpenDisputeRequest",
            fields={"reason": drf_serializers.CharField(max_length=1000)},
        ),
        responses={201: _DisputeSerializer},
    )
    def post(self, request: Request, pk: int) -> Response:
        # Scoped to the caller's own reservations, so someone else's id is a
        # plain 404. Fetching by pk first and answering 403 told any signed-in
        # user which reservation ids exist.
        reservation = get_object_or_404(
            Reservation.objects.select_related("listing", "listing__owner", "claimant").filter(
                Q(claimant=request.user) | Q(listing__owner=request.user)
            ),
            pk=pk,
        )

        # A dispute is about a handoff that went wrong, so there has to have
        # been a handoff: opening one on a brand-new request would let anyone
        # strike a stranger's trust score by reserving and immediately disputing.
        disputable = {
            Reservation.Status.CONFIRMED,
            Reservation.Status.COMPLETED,
            Reservation.Status.EXPIRED_UNRESOLVED,
        }
        if reservation.status not in disputable:
            return Response(
                {"detail": "Disputes can only be opened on confirmed, completed or unresolved handoffs."},
                status=400,
            )

        reason = str(request.data.get("reason", "")).strip()[:1000]
        if not reason:
            return Response({"reason": "This field is required."}, status=400)

        already_open = Response(
            {"detail": "A dispute from you on this reservation already exists."},
            status=status.HTTP_409_CONFLICT,
        )
        opener = current_user(request)
        other_id = _disputed_party_id(reservation, opener.pk)
        # The dispute and the strike it carries are one unit, and the unique
        # constraint (reservation, opened_by) is what makes a double submit
        # safe: get_or_create alone could insert twice and strike twice.
        try:
            with transaction.atomic():
                dispute, created = Dispute.objects.get_or_create(
                    reservation=reservation,
                    opened_by=opener,
                    defaults={"reason": reason},
                )
                if created:
                    # F() so concurrent disputes cannot overwrite each
                    # other's increment.
                    User.objects.filter(pk=other_id).update(trust_strikes=F("trust_strikes") + 1)
        except IntegrityError:
            return already_open
        if not created:
            return already_open

        # Notify the actual moderators. The previous version addressed the row
        # to the person who opened the dispute, so staff were never told.
        for staff_user in User.objects.filter(is_staff=True, is_active=True):
            Notification.objects.get_or_create(
                dedupe_key=f"dispute:staff:{dispute.pk}:{staff_user.pk}",
                defaults={
                    "user": staff_user,
                    "type": Notification.Type.SYSTEM,
                    "title": "New dispute opened",
                    "body": f"Dispute on reservation #{reservation.pk}: {reason[:120]}",
                    "link_path": f"/handoffs/{reservation.pk}",
                    "priority": Notification.Priority.HIGH,
                },
            )

        return Response(_dispute_to_dict(dispute), status=status.HTTP_201_CREATED)


class DisputeListView(APIView):
    """GET /api/disputes: staff sees all open disputes; users see their own."""

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(operation_id="list_disputes", responses={200: inline_serializer("DisputeList", fields={
        "id": drf_serializers.IntegerField(),
        "reservation_id": drf_serializers.IntegerField(),
        "status": drf_serializers.CharField(),
        "reason": drf_serializers.CharField(),
        "created_at": drf_serializers.DateTimeField(),
    }, many=True)})
    def get(self, request: Request) -> Response:
        if request.user.is_staff:
            qs = Dispute.objects.filter(status=Dispute.Status.OPEN).order_by("-created_at")[:200]
        else:
            qs = Dispute.objects.filter(opened_by=current_user(request)).order_by("-created_at")[:50]
        return Response([_dispute_to_dict(d) for d in qs])


class DisputeDetailView(APIView):
    """PATCH /api/disputes/:id: staff resolves or dismisses a dispute."""

    permission_classes = [permissions.IsAdminUser]

    @extend_schema(
        operation_id="resolve_dispute",
        request=inline_serializer(
            "ResolveDisputeRequest",
            fields={
                "action": drf_serializers.ChoiceField(choices=["resolve", "dismiss"]),
                "resolution_note": drf_serializers.CharField(required=False, allow_blank=True),
            },
        ),
        responses={200: _DisputeSerializer},
    )
    def patch(self, request: Request, pk: int) -> Response:
        action = request.data.get("action", "")
        note = str(request.data.get("resolution_note", "")).strip()

        if action not in {"resolve", "dismiss"}:
            return Response(
                {"detail": "action must be 'resolve' or 'dismiss'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            # Locked so two moderators closing the same dispute cannot both
            # pass the OPEN check and hand the strike back twice.
            dispute = get_object_or_404(Dispute.objects.select_for_update(), pk=pk)
            if dispute.status != Dispute.Status.OPEN:
                return Response({"detail": "Dispute is already closed."}, status=400)

            dispute.status = (
                Dispute.Status.RESOLVED if action == "resolve" else Dispute.Status.DISMISSED
            )
            dispute.resolved_by = current_user(request)
            dispute.resolution_note = note
            dispute.save(update_fields=["status", "resolved_by", "resolution_note", "updated_at"])

            if dispute.status == Dispute.Status.DISMISSED:
                # Opening the dispute struck the other party. A dismissal
                # says the complaint did not hold, so the strike goes back.
                # The filter keeps the counter from going below zero.
                reservation = Reservation.objects.select_related("listing").get(pk=dispute.reservation_id)
                User.objects.filter(
                    pk=_disputed_party_id(reservation, dispute.opened_by_id), trust_strikes__gt=0
                ).update(trust_strikes=F("trust_strikes") - 1)

        # Notify the opener
        Notification.objects.get_or_create(
            dedupe_key=f"dispute:resolved:{dispute.pk}",
            defaults={
                "user": dispute.opened_by,
                "type": Notification.Type.SYSTEM,
                "title": f"Your dispute has been {dispute.status}",
                "body": note or f"Staff has {dispute.status} your dispute on reservation #{dispute.reservation_id}.",
                "link_path": f"/reservations/{dispute.reservation_id}",
                "priority": Notification.Priority.NORMAL,
            },
        )

        return Response(_dispute_to_dict(dispute))
