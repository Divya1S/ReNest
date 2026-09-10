"""Agent Payment Sandbox endpoints."""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from rest_framework import permissions, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from dormcycle.pagination import StandardPagination
from dormcycle.typed import current_user

from ..models import AgentPolicy, AgentTransaction
from ..serializers import AgentPolicySerializer, AgentTransactionCreateSerializer, AgentTransactionSerializer
from ..services import audit
from ..services.agents import evaluate_agent_transaction, visible_agents
from ..throttles import AgentAttemptThrottle
from .payments import _request_id

MAX_AGENTS_PER_USER = 20


class AgentListCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        paginator = StandardPagination()
        page = paginator.paginate_queryset(visible_agents(current_user(request)), request, view=self)
        return paginator.get_paginated_response(AgentPolicySerializer(page, many=True).data)

    def post(self, request: Request) -> Response:
        user = current_user(request)
        if AgentPolicy.objects.filter(user=user).count() >= MAX_AGENTS_PER_USER:
            return Response({"detail": f"At most {MAX_AGENTS_PER_USER} agents per account."}, status=status.HTTP_400_BAD_REQUEST)
        serializer = AgentPolicySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        agent = serializer.save(user=user)
        audit.record(
            action="agent.policy_created",
            target_type="agent",
            target_id=agent.public_id,
            actor=user,
            reason=f"Agent '{agent.name}' created",
            request_id=_request_id(request),
            metadata={"daily_limit": str(agent.daily_limit), "transaction_limit": str(agent.transaction_limit)},
        )
        return Response(AgentPolicySerializer(agent).data, status=status.HTTP_201_CREATED)


class AgentDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _get(self, request: Request, public_id: str) -> AgentPolicy:
        return get_object_or_404(visible_agents(current_user(request)), public_id=public_id)

    def get(self, request: Request, public_id: str) -> Response:
        return Response(AgentPolicySerializer(self._get(request, public_id)).data)

    def patch(self, request: Request, public_id: str) -> Response:
        agent = self._get(request, public_id)
        serializer = AgentPolicySerializer(agent, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        changed = sorted(serializer.validated_data)
        serializer.save()
        audit.record(
            action="agent.policy_modified",
            target_type="agent",
            target_id=agent.public_id,
            actor=current_user(request),
            reason=f"Policy fields changed: {', '.join(changed)}",
            request_id=_request_id(request),
            metadata={k: str(v) for k, v in serializer.validated_data.items()},
        )
        return Response(AgentPolicySerializer(agent).data)


class AgentTransactionView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AgentAttemptThrottle]

    def get(self, request: Request, public_id: str) -> Response:
        agent = get_object_or_404(visible_agents(current_user(request)), public_id=public_id)
        paginator = StandardPagination()
        page = paginator.paginate_queryset(AgentTransaction.objects.filter(agent=agent), request, view=self)
        return paginator.get_paginated_response(AgentTransactionSerializer(page, many=True).data)

    def post(self, request: Request, public_id: str) -> Response:
        agent = get_object_or_404(visible_agents(current_user(request)), public_id=public_id)
        serializer = AgentTransactionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        attempt, verdict = evaluate_agent_transaction(
            agent=agent,
            amount=data["amount"],
            currency=data.get("currency") or agent.currency,
            category=data["category"],
            merchant_id=data["merchant_id"],
            description=data["description"],
            actor=current_user(request),
            request_id=_request_id(request),
        )
        return Response(
            {
                "attempt": AgentTransactionSerializer(attempt).data,
                "decision": verdict.decision,
                "reasons": verdict.reasons,
                "risk_score": verdict.risk_score,
                "counts_toward_spend": verdict.counts_toward_spend,
                "spent_today": str(verdict.spent_today),
                "remaining_today": str(verdict.remaining_today),
            },
            status=status.HTTP_201_CREATED,
        )
