"""Read models for the dashboard: overview cards, live feed, distribution,
policy, system health and metrics. Every number is computed from stored rows
or from the metrics reservoir; simulation rows are excluded unless asked for
and are always labelled when included."""

from __future__ import annotations

import os
from collections import Counter
from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.db.models import Avg, Count, Q, Sum
from django.db.models.functions import TruncDay, TruncHour
from django.utils import timezone
from rest_framework import permissions
from rest_framework.renderers import BaseRenderer
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from dormcycle.typed import current_user

from ..engine import MODEL_VERSION, thresholds_for
from ..engine.rules import RULES
from ..models import (
    Decision,
    EventStatus,
    IdempotencyKey,
    IdempotencyStatus,
    PaymentEvent,
    PaymentTransaction,
    RiskEvaluation,
    TransactionStatus,
)
from ..permissions import IsStaff, IsStaffOrReadOnly
from ..serializers import PaymentSummarySerializer, PolicyUpdateSerializer
from ..services import metrics
from ..services.events import handlers_for
from ..services.investigator import get_explainer
from ..services.policy import current_sensitivity, get_active_policy, update_sensitivity
from ..services.processor import get_processor
from .payments import _request_id, _truthy, visible_payments

MAX_WINDOW_HOURS = 24 * 30


def _window_hours(request: Request, default: int = 24) -> int:
    try:
        hours = int(request.query_params.get("hours", default))
    except (TypeError, ValueError):
        hours = default
    return max(1, min(MAX_WINDOW_HOURS, hours))


def _base(request: Request) -> Any:
    qs = PaymentTransaction.objects.for_user(current_user(request))
    if not _truthy(request.query_params.get("include_simulation")):
        qs = qs.filter(is_simulation=False)
    return qs


class OverviewView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        hours = _window_hours(request)
        now = timezone.now()
        since = now - timedelta(hours=hours)
        include_sim = _truthy(request.query_params.get("include_simulation"))
        base = _base(request)
        window = base.filter(created_at__gte=since)

        agg = window.aggregate(
            total=Count("id"),
            allowed=Count("id", filter=Q(decision=Decision.ALLOW)),
            reviewed=Count("id", filter=Q(decision=Decision.REVIEW)),
            blocked=Count("id", filter=Q(decision=Decision.BLOCK)),
            completed=Count("id", filter=Q(status=TransactionStatus.COMPLETED)),
            failed=Count("id", filter=Q(status=TransactionStatus.FAILED)),
            in_review=Count("id", filter=Q(status=TransactionStatus.REVIEW)),
            avg_score=Avg("risk_score"),
            blocked_amount=Sum("amount", filter=Q(decision=Decision.BLOCK)),
            held_amount=Sum("amount", filter=Q(status=TransactionStatus.REVIEW)),
            completed_amount=Sum("amount", filter=Q(status=TransactionStatus.COMPLETED)),
            low=Count("id", filter=Q(risk_score__lt=30)),
            medium=Count("id", filter=Q(risk_score__gte=30, risk_score__lt=60)),
            high=Count("id", filter=Q(risk_score__gte=60)),
            simulated=Count("id", filter=Q(is_simulation=True)),
        )
        total = agg["total"] or 0
        latency = metrics.summarize(
            float(v) for v in window.filter(evaluation_ms__isnull=False).values_list("evaluation_ms", flat=True)[:5000]
        )

        # Series for the chart, bucketed by the database (one row per bucket
        # and decision, whatever the window holds).
        trunc = TruncHour if hours <= 48 else TruncDay
        fmt = "%Y-%m-%dT%H:00" if hours <= 48 else "%Y-%m-%d"
        buckets: dict[str, Counter[str]] = {}
        rows = (
            window.annotate(bucket=trunc("created_at", tzinfo=timezone.get_current_timezone()))
            .values("bucket", "decision")
            .annotate(n=Count("id"))
            .order_by("bucket")
        )
        for row in rows:
            key = row["bucket"].strftime(fmt)
            buckets.setdefault(key, Counter())[row["decision"] or "pending"] += row["n"]
        series = [
            {"bucket": key, "allow": c[Decision.ALLOW], "review": c[Decision.REVIEW], "block": c[Decision.BLOCK]}
            for key, c in sorted(buckets.items())
        ]

        sensitivity = current_sensitivity()
        thresholds = thresholds_for(sensitivity)
        return Response({
            "window_hours": hours,
            "generated_at": now.isoformat(),
            "includes_simulation": include_sim,
            "simulated_rows": agg["simulated"] or 0,
            "totals": {
                "transactions": total,
                "allowed": agg["allowed"] or 0,
                "reviewed": agg["reviewed"] or 0,
                "blocked": agg["blocked"] or 0,
                "completed": agg["completed"] or 0,
                "failed": agg["failed"] or 0,
                "review_queue": PaymentTransaction.objects.for_user(current_user(request)).filter(
                    status=TransactionStatus.REVIEW, is_simulation=False
                ).count(),
            },
            "rates": {
                "block_rate": round((agg["blocked"] or 0) / total, 4) if total else None,
                "review_rate": round((agg["reviewed"] or 0) / total, 4) if total else None,
                "average_risk_score": round(float(agg["avg_score"]), 1) if agg["avg_score"] is not None else None,
            },
            "amounts": {
                "blocked": str(agg["blocked_amount"] or Decimal("0")),
                "held": str(agg["held_amount"] or Decimal("0")),
                "completed": str(agg["completed_amount"] or Decimal("0")),
            },
            "distribution": {"low": agg["low"] or 0, "medium": agg["medium"] or 0, "high": agg["high"] or 0},
            "evaluation_latency": latency.as_dict(),
            "series": series,
            "policy": {"sensitivity": sensitivity, "review_threshold": thresholds.review, "block_threshold": thresholds.block},
            "model_version": MODEL_VERSION,
        })


class FeedView(APIView):
    """The most recent transactions. Poll with ?since=<iso> for new rows."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        try:
            limit = max(1, min(100, int(request.query_params.get("limit", 30))))
        except (TypeError, ValueError):
            limit = 30
        rows = list(visible_payments(request)[:limit])
        return Response({
            "results": PaymentSummarySerializer(rows, many=True).data,
            "generated_at": timezone.now().isoformat(),
        })


class DistributionView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        hours = _window_hours(request)
        window = _base(request).filter(created_at__gte=timezone.now() - timedelta(hours=hours), risk_score__isnull=False)
        histogram = Counter((score // 10) * 10 for score in window.values_list("risk_score", flat=True))
        agg = window.aggregate(
            low=Count("id", filter=Q(risk_score__lt=30)),
            medium=Count("id", filter=Q(risk_score__gte=30, risk_score__lt=60)),
            high=Count("id", filter=Q(risk_score__gte=60)),
        )
        thresholds = thresholds_for(current_sensitivity())
        return Response({
            "window_hours": hours,
            "bands": {"low": agg["low"], "medium": agg["medium"], "high": agg["high"]},
            "histogram": [{"bucket": b, "count": histogram.get(b, 0)} for b in range(0, 101, 10)],
            "thresholds": {"review": thresholds.review, "block": thresholds.block},
        })


class PolicyView(APIView):
    permission_classes = [IsStaffOrReadOnly]

    def get(self, request: Request) -> Response:
        policy = get_active_policy()
        return Response(self._payload(policy.sensitivity, policy))

    def put(self, request: Request) -> Response:
        serializer = PolicyUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        policy = update_sensitivity(
            actor=current_user(request),
            sensitivity=serializer.validated_data["sensitivity"],
            reason=serializer.validated_data["reason"],
            request_id=_request_id(request),
        )
        return Response(self._payload(policy.sensitivity, policy))

    @staticmethod
    def _payload(sensitivity: int, policy: Any) -> dict[str, Any]:
        thresholds = thresholds_for(sensitivity)
        return {
            "name": policy.name,
            "sensitivity": sensitivity,
            "review_threshold": thresholds.review,
            "block_threshold": thresholds.block,
            "updated_at": policy.updated_at.isoformat() if policy.updated_at else None,
            "updated_by": getattr(policy.updated_by, "display_name", None) if policy.updated_by_id else None,
            "model_version": MODEL_VERSION,
            "curve": [
                {"sensitivity": s, "review": thresholds_for(s).review, "block": thresholds_for(s).block}
                for s in range(0, 101, 10)
            ],
        }


class RulesView(APIView):
    """The rule catalogue, straight from the engine, so the UI can never drift
    from what actually runs."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        return Response({
            "model_version": MODEL_VERSION,
            "rules": [{"code": r.code, "description": r.description} for r in RULES],
            "note": "Additive, hand-tuned demonstration model. Scores are not probabilities.",
        })


def _event_health() -> dict[str, Any]:
    now = timezone.now()
    agg = PaymentEvent.objects.aggregate(
        pending=Count("sequence", filter=Q(status__in=[EventStatus.PENDING, EventStatus.PROCESSING])),
        failed=Count("sequence", filter=Q(status=EventStatus.FAILED)),
        dead=Count("sequence", filter=Q(status=EventStatus.DEAD)),
        processed_24h=Count("sequence", filter=Q(status=EventStatus.PROCESSED, processed_at__gte=now - timedelta(hours=24))),
        total=Count("sequence"),
    )
    oldest = PaymentEvent.objects.filter(status__in=[EventStatus.PENDING, EventStatus.PROCESSING, EventStatus.FAILED]).order_by("sequence").first()
    return {
        **agg,
        "oldest_unprocessed_age_s": round((now - oldest.occurred_at).total_seconds(), 1) if oldest else None,
        "handlers": {t: [name for name, _ in handlers_for(t)] for t in sorted({e for e in _handler_types()})},
    }


def _handler_types() -> list[str]:
    from ..services.events import EVENT_TYPES

    return [t for t in EVENT_TYPES if handlers_for(t)]


def health_payload() -> dict[str, Any]:
    now = timezone.now()
    day = now - timedelta(hours=24)
    db_latency = metrics.summarize(
        float(v) for v in RiskEvaluation.objects.filter(created_at__gte=day).values_list("evaluation_ms", flat=True)[:5000]
    )
    events = _event_health()
    counters = metrics.all_counters()
    requests_total = counters["requests_total"]
    status_text = "ok"
    if events["dead"]:
        status_text = "degraded"
    if requests_total and counters["errors_total"] / requests_total > 0.05:
        status_text = "degraded"
    sensitivity = current_sensitivity()
    thresholds = thresholds_for(sensitivity)
    return {
        "status": status_text,
        "generated_at": now.isoformat(),
        "model_version": MODEL_VERSION,
        "processor": get_processor().name,
        "investigator_mode": get_explainer().mode,
        "investigator_setting": os.getenv("RISK_INVESTIGATOR_MODE", "deterministic"),
        "policy": {"sensitivity": sensitivity, "review_threshold": thresholds.review, "block_threshold": thresholds.block},
        "counters": counters,
        "error_rate": round(counters["errors_total"] / requests_total, 4) if requests_total else 0.0,
        "latency": {name: metrics.latency_summary(name).as_dict() for name in metrics.LATENCY_METRICS},
        "database": {
            "transactions_24h": PaymentTransaction.objects.filter(created_at__gte=day, is_simulation=False).count(),
            "simulated_transactions_24h": PaymentTransaction.objects.filter(created_at__gte=day, is_simulation=True).count(),
            "evaluations_24h": db_latency.count,
            "evaluation_latency_24h": db_latency.as_dict(),
        },
        "events": events,
        "idempotency": {
            "active_keys": IdempotencyKey.objects.filter(expires_at__gt=now).count(),
            "in_progress": IdempotencyKey.objects.filter(status=IdempotencyStatus.IN_PROGRESS, expires_at__gt=now).count(),
        },
        "notes": [
            "Counters and latency reservoirs live in the cache (process-local without Redis) and reset on restart.",
            "Database figures are exact and survive restarts.",
        ],
    }


class SystemHealthView(APIView):
    """Dashboard read model. Always 200: the `status` field carries ok/degraded
    so the page can render a degraded system instead of an error. Liveness and
    readiness probes for load balancers live at /api/health/*."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        return Response(health_payload())


class MetricsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request: Request) -> Response:
        return Response({
            "counters": metrics.all_counters(),
            "latency": {name: metrics.latency_summary(name).as_dict() for name in metrics.LATENCY_METRICS},
            "generated_at": timezone.now().isoformat(),
        })


class PlainTextRenderer(BaseRenderer):
    media_type = "text/plain"
    format = "txt"
    charset = "utf-8"

    def render(self, data: Any, accepted_media_type: str | None = None, renderer_context: Any = None) -> Any:
        return str(data).encode(self.charset or "utf-8")


class PrometheusView(APIView):
    permission_classes = [IsStaff]
    renderer_classes = [PlainTextRenderer]

    def get(self, request: Request) -> Response:
        return Response(metrics.prometheus_text(), content_type="text/plain; version=0.0.4; charset=utf-8")
