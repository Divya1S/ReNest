"""Events and audit log: the traceability surface."""

from __future__ import annotations

from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import permissions
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from dormcycle.pagination import StandardPagination
from dormcycle.typed import current_user

from ..models import AgentPolicy, AuditLog, EventStatus, PaymentEvent, PaymentTransaction
from ..permissions import IsStaff
from ..serializers import AuditLogSerializer, PaymentEventSerializer
from ..services import audit
from ..services.events import EVENT_TYPES, dispatch_pending, replay_dead
from .payments import _request_id


def _own_payment_ids(request: Request) -> "Q":
    """Non-staff see events and audit rows about their own payments only."""
    user = current_user(request)
    if user.is_staff:
        return Q()
    own = PaymentTransaction.objects.filter(user=user).values("public_id")
    own_agents = AgentPolicy.objects.filter(user=user).values("public_id")
    return Q(entity_type="payment", entity_id__in=own) | Q(entity_type="agent", entity_id__in=own_agents)


class EventListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        qs = PaymentEvent.objects.filter(_own_payment_ids(request)).order_by("-sequence")
        params = request.query_params
        if params.get("entity_id"):
            qs = qs.filter(entity_id=params["entity_id"][:40])
        if params.get("event_type") in EVENT_TYPES:
            qs = qs.filter(event_type=params["event_type"])
        if params.get("status") in EventStatus.values:
            qs = qs.filter(status=params["status"])
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(PaymentEventSerializer(page, many=True).data)


class EventReplayView(APIView):
    permission_classes = [IsStaff]

    def post(self, request: Request, event_id: str) -> Response:
        event = get_object_or_404(PaymentEvent, event_id=event_id)
        if event.status != EventStatus.DEAD:
            return Response({"detail": "Only dead-lettered events can be replayed."}, status=409)
        replay_dead(event.event_id)
        audit.record(
            action="event.replayed",
            target_type="event",
            target_id=event.event_id,
            actor=current_user(request),
            reason="Operator replayed a dead-lettered event",
            request_id=_request_id(request),
        )
        summary = dispatch_pending(limit=50)
        event.refresh_from_db()
        return Response({"event": PaymentEventSerializer(event).data, "dispatch": summary})


class EventDispatchView(APIView):
    """Operator nudge: process due events now (the maintenance tick does this on a schedule)."""

    permission_classes = [IsStaff]

    def post(self, request: Request) -> Response:
        return Response(dispatch_pending(limit=200))


class AuditListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        user = current_user(request)
        qs = AuditLog.objects.all()
        if not user.is_staff:
            own = PaymentTransaction.objects.filter(user=user).values("public_id")
            qs = qs.filter(Q(actor=user) | Q(target_type="payment", target_id__in=own))
        params = request.query_params
        if params.get("target_id"):
            qs = qs.filter(target_id=params["target_id"][:40])
        if params.get("action"):
            qs = qs.filter(action=params["action"][:64])
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(AuditLogSerializer(page, many=True).data)
