"""Fraud Lab and Risk Investigator endpoints."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.shortcuts import get_object_or_404
from rest_framework import permissions, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from dormcycle.pagination import StandardPagination
from dormcycle.typed import current_user

from ..engine import MODEL_VERSION, rescore, thresholds_for
from ..models import Decision, Investigation, PaymentTransaction, SimulationRun
from ..serializers import (
    InvestigationCreateSerializer,
    InvestigationSerializer,
    SimulationCreateSerializer,
    SimulationRunListSerializer,
    SimulationRunSerializer,
)
from ..services import investigator, simulation
from ..services.policy import current_sensitivity
from ..throttles import InvestigationThrottle, SimulationThrottle
from .payments import _request_id


class ScenarioListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        return Response({
            "scenarios": simulation.scenario_catalogue(),
            "default_count": simulation.DEFAULT_COUNT,
            "min_count": simulation.MIN_COUNT,
            "max_count": simulation.MAX_COUNT,
            "model_version": MODEL_VERSION,
            "current_sensitivity": current_sensitivity(),
            "note": (
                "Synthetic, labelled data scored by the live engine. Precision and recall are "
                "measured against the synthetic labels; they say nothing about real traffic."
            ),
        })


class SimulationListCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [SimulationThrottle]

    def get(self, request: Request) -> Response:
        paginator = StandardPagination()
        qs = SimulationRun.objects.for_user(current_user(request))
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(SimulationRunListSerializer(page, many=True).data)

    def post(self, request: Request) -> Response:
        serializer = SimulationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        run = simulation.run_scenario(
            user=current_user(request),
            scenario=data["scenario"],
            sensitivity=data.get("sensitivity", current_sensitivity()),
            count=data["count"],
            seed=data.get("seed"),
            request_id=_request_id(request),
        )
        return Response(SimulationRunSerializer(run).data, status=status.HTTP_201_CREATED)


class SimulationDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request, public_id: str) -> Response:
        run = get_object_or_404(SimulationRun.objects.for_user(current_user(request)), public_id=public_id)
        return Response(SimulationRunSerializer(run).data)


class SimulationRescoreView(APIView):
    """Re-threshold a stored run at another sensitivity. Rules do not re-run;
    stored scores are compared against the new thresholds, which is exactly
    what changing the live policy would do to the same transactions."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request, public_id: str) -> Response:
        run = get_object_or_404(SimulationRun.objects.for_user(current_user(request)), public_id=public_id)
        raw = request.query_params.get("sensitivity")
        try:
            sensitivity = max(0, min(100, int(raw))) if raw is not None else run.sensitivity
        except (TypeError, ValueError):
            sensitivity = run.sensitivity
        rows = list(
            PaymentTransaction.objects.filter(simulation_run=run, risk_score__isnull=False)
            .values_list("risk_score", "ground_truth_fraud", "amount")
        )
        counts: dict[str, Any] = {
            "analyzed": len(rows), "allowed": 0, "reviewed": 0, "blocked": 0,
            "fraud_blocked": 0, "fraud_reviewed": 0, "legitimate_blocked": 0, "legitimate_reviewed": 0,
        }
        protected = friction = Decimal("0")
        for score, is_fraud, amount in rows:
            decision = rescore(int(score or 0), sensitivity)
            if decision == Decision.ALLOW:
                counts["allowed"] += 1
                continue
            bucket = "blocked" if decision == Decision.BLOCK else "reviewed"
            counts[bucket] += 1
            label = "fraud" if is_fraud else "legitimate"
            counts[f"{label}_{bucket}"] += 1
            if is_fraud and decision == Decision.BLOCK:
                protected += amount
            elif not is_fraud:
                friction += amount
        fraud_total = sum(1 for _, f, _ in rows if f)
        legit_total = len(rows) - fraud_total
        flagged = counts["blocked"] + counts["reviewed"]
        fraud_flagged = counts["fraud_blocked"] + counts["fraud_reviewed"]
        legit_flagged = counts["legitimate_blocked"] + counts["legitimate_reviewed"]
        thresholds = thresholds_for(sensitivity)
        counts.update({
            "sensitivity": sensitivity,
            "thresholds": {"review": thresholds.review, "block": thresholds.block},
            "precision_blocked": simulation._ratio(counts["fraud_blocked"], counts["blocked"]),
            "recall_blocked": simulation._ratio(counts["fraud_blocked"], fraud_total),
            "precision_flagged": simulation._ratio(fraud_flagged, flagged),
            "recall_flagged": simulation._ratio(fraud_flagged, fraud_total),
            "false_positive_rate": simulation._ratio(legit_flagged, legit_total),
            "protected_amount": str(protected),
            "friction_amount": str(friction),
        })
        return Response(counts)


class InvestigationListCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [InvestigationThrottle]

    def get(self, request: Request) -> Response:
        paginator = StandardPagination()
        qs = Investigation.objects.for_user(current_user(request))
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(InvestigationSerializer(page, many=True).data)

    def post(self, request: Request) -> Response:
        serializer = InvestigationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        record = investigator.investigate(
            user=current_user(request),
            question=serializer.validated_data["question"],
            request_id=_request_id(request),
        )
        return Response(InvestigationSerializer(record).data, status=status.HTTP_201_CREATED)


class InvestigationDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request, public_id: str) -> Response:
        record = get_object_or_404(Investigation.objects.for_user(current_user(request)), public_id=public_id)
        return Response(InvestigationSerializer(record).data)


class InvestigatorInfoView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        explainer = investigator.get_explainer()
        return Response({
            "mode": explainer.mode,
            "intents": [{"key": k, "label": v} for k, v in investigator.INTENTS.items()],
            "suggested_questions": list(investigator.SUGGESTED_QUESTIONS),
            "allowed_queries": sorted({fn.__name__.removeprefix("q_") for plan in investigator.QUERY_PLANS.values() for fn in plan}),
        })
