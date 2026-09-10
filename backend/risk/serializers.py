"""Serializers for the risk API. Input serializers validate strictly; output
serializers are plain read-only projections of the models."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from rest_framework import serializers

from .models import (
    AgentPolicy,
    AgentTransaction,
    AuditLog,
    Decision,
    Investigation,
    PaymentEvent,
    PaymentMethod,
    PaymentTransaction,
    RiskEvaluation,
    RiskFactor,
    SUPPORTED_CURRENCIES,
    SimulationRun,
    SimulationScenario,
)
from .services.audit import redact
from .services.processor import SANDBOX_BEHAVIORS
from .services.simulation import DEFAULT_COUNT, MAX_COUNT, MIN_COUNT

MAX_AMOUNT = Decimal("1000000")
MAX_METADATA_BYTES = 2048
MAX_METADATA_KEYS = 20


def _validate_amount_for_currency(amount: Decimal, currency: str) -> Decimal:
    decimals = SUPPORTED_CURRENCIES[currency]
    if decimals == 0 and amount != amount.to_integral_value():
        raise serializers.ValidationError({"amount": f"{currency} is a zero-decimal currency; amount must be whole."})
    return amount


def _validate_metadata(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise serializers.ValidationError("metadata must be an object.")
    if len(value) > MAX_METADATA_KEYS:
        raise serializers.ValidationError(f"metadata may hold at most {MAX_METADATA_KEYS} keys.")
    if len(json.dumps(value, default=str)) > MAX_METADATA_BYTES:
        raise serializers.ValidationError(f"metadata may be at most {MAX_METADATA_BYTES} bytes.")
    # Credentials and card data have no business in payment metadata; they are
    # replaced at the boundary so they never reach the database or the logs.
    return dict(redact(value))


# ── Payments ─────────────────────────────────────────────────────────────────

class PaymentCreateSerializer(serializers.Serializer):
    merchant_id = serializers.CharField(max_length=80)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"), max_value=MAX_AMOUNT)
    currency = serializers.ChoiceField(choices=sorted(SUPPORTED_CURRENCIES))
    payment_method = serializers.ChoiceField(choices=PaymentMethod.choices)
    device_id = serializers.CharField(max_length=80, required=False, allow_blank=True, default="")
    merchant_category = serializers.CharField(max_length=40, required=False, allow_blank=True, default="")
    country = serializers.RegexField(r"^[A-Za-z]{2}$", required=False, allow_blank=True, default="")
    sandbox_behavior = serializers.ChoiceField(choices=SANDBOX_BEHAVIORS, required=False, default="succeed")
    metadata = serializers.JSONField(required=False, default=dict)

    def validate_merchant_id(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("merchant_id is required.")
        return value

    def validate_country(self, value: str) -> str:
        return value.upper()

    def validate_metadata(self, value: Any) -> dict[str, Any]:
        return _validate_metadata(value)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        attrs["amount"] = _validate_amount_for_currency(attrs["amount"], attrs["currency"])
        attrs["device_id"] = attrs.get("device_id", "").strip()
        attrs["merchant_category"] = attrs.get("merchant_category", "").strip().lower()
        return attrs


class RiskFactorSerializer(serializers.ModelSerializer):
    class Meta:
        model = RiskFactor
        fields = ("code", "label", "points", "detail")


class RiskEvaluationSerializer(serializers.ModelSerializer):
    factors = RiskFactorSerializer(many=True, read_only=True)
    thresholds = serializers.SerializerMethodField()

    class Meta:
        model = RiskEvaluation
        fields = (
            "id", "score", "decision", "confidence", "model_version", "sensitivity",
            "thresholds", "evaluation_ms", "explanation", "factors", "context_snapshot", "created_at",
        )

    def get_thresholds(self, obj: RiskEvaluation) -> dict[str, int]:
        return {"sensitivity": obj.sensitivity, "review": obj.review_threshold, "block": obj.block_threshold}


class PaymentSummarySerializer(serializers.ModelSerializer):
    risk_band = serializers.CharField(read_only=True)
    reasons = serializers.SerializerMethodField()
    summary = serializers.SerializerMethodField()

    class Meta:
        model = PaymentTransaction
        fields: tuple[str, ...] = (
            "public_id", "merchant_id", "merchant_category", "amount", "currency", "payment_method",
            "device_id", "country", "status", "decision", "risk_score", "risk_band", "confidence",
            "model_version", "evaluation_ms", "is_simulation", "ground_truth_fraud", "request_id",
            "reasons", "summary", "created_at",
        )

    def _latest(self, obj: PaymentTransaction) -> RiskEvaluation | None:
        evaluations = list(obj.evaluations.all())
        return evaluations[0] if evaluations else None

    def get_reasons(self, obj: PaymentTransaction) -> list[str]:
        evaluation = self._latest(obj)
        if evaluation is None:
            return []
        factors = sorted(evaluation.factors.all(), key=lambda f: (-abs(f.points), f.code))
        return [f.code for f in factors if f.points > 0][:4]

    def get_summary(self, obj: PaymentTransaction) -> str:
        evaluation = self._latest(obj)
        if evaluation is None:
            return ""
        return evaluation.explanation.splitlines()[-1] if evaluation.explanation else ""


class PaymentEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentEvent
        fields = (
            "event_id", "sequence", "event_type", "schema_version", "entity_type", "entity_id",
            "payload", "request_id", "occurred_at", "status", "attempts", "processed_handlers",
            "last_error", "next_attempt_at", "processed_at",
        )


class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = (
            "id", "actor_label", "action", "target_type", "target_id", "reason", "decision",
            "model_version", "request_id", "metadata", "created_at",
        )


class PaymentDetailSerializer(PaymentSummarySerializer):
    evaluation = serializers.SerializerMethodField()
    events = serializers.SerializerMethodField()
    audit = serializers.SerializerMethodField()
    manual_review = serializers.SerializerMethodField()
    metadata = serializers.SerializerMethodField()

    class Meta(PaymentSummarySerializer.Meta):
        fields = PaymentSummarySerializer.Meta.fields + (
            "ip_prefix", "processor_reference", "failure_reason", "metadata", "manual_review",
            "evaluation", "events", "audit", "updated_at",
        )

    def get_evaluation(self, obj: PaymentTransaction) -> dict[str, Any] | None:
        evaluation = self._latest(obj)
        return RiskEvaluationSerializer(evaluation).data if evaluation else None

    def get_events(self, obj: PaymentTransaction) -> Any:
        events = PaymentEvent.objects.filter(entity_type="payment", entity_id=obj.public_id).order_by("sequence")
        return PaymentEventSerializer(events, many=True).data

    def _is_staff(self) -> bool:
        request = self.context.get("request")
        user = getattr(request, "user", None)
        return bool(user is not None and getattr(user, "is_staff", False))

    def get_audit(self, obj: PaymentTransaction) -> Any:
        entries = AuditLog.objects.filter(target_type="payment", target_id=obj.public_id).order_by("created_at", "id")
        rows = AuditLogSerializer(entries, many=True).data
        if self._is_staff():
            return rows
        # Customers see that a review happened, never the reviewer's notes.
        for row in rows:
            if row["action"] == "payment.manual_review":
                row["reason"] = f"Manual review: {row['metadata'].get('outcome', 'resolved')}"
                row["metadata"] = {}
        return rows

    def get_metadata(self, obj: PaymentTransaction) -> dict[str, Any]:
        data = dict(obj.metadata) if isinstance(obj.metadata, dict) else {}
        if not self._is_staff():
            data.pop("manual_review", None)
        return data

    def get_manual_review(self, obj: PaymentTransaction) -> dict[str, Any] | None:
        review = obj.metadata.get("manual_review") if isinstance(obj.metadata, dict) else None
        if not isinstance(review, dict):
            return None
        if self._is_staff():
            return review
        return {"outcome": review.get("outcome")}


class ManualReviewSerializer(serializers.Serializer):
    approve = serializers.BooleanField()
    note = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")


# ── Policy ───────────────────────────────────────────────────────────────────

class PolicyUpdateSerializer(serializers.Serializer):
    sensitivity = serializers.IntegerField(min_value=0, max_value=100)
    reason = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


# ── Fraud Lab ────────────────────────────────────────────────────────────────

class SimulationCreateSerializer(serializers.Serializer):
    scenario = serializers.ChoiceField(choices=SimulationScenario.choices)
    sensitivity = serializers.IntegerField(min_value=0, max_value=100, required=False)
    count = serializers.IntegerField(min_value=MIN_COUNT, max_value=MAX_COUNT, required=False, default=DEFAULT_COUNT)
    seed = serializers.IntegerField(min_value=1, max_value=2**31 - 1, required=False)


class SimulationRunSerializer(serializers.ModelSerializer):
    scenario_title = serializers.CharField(source="get_scenario_display", read_only=True)

    class Meta:
        model = SimulationRun
        fields = (
            "public_id", "scenario", "scenario_title", "sensitivity", "seed", "transaction_count",
            "model_version", "metrics", "duration_ms", "created_at",
        )


class SimulationRunListSerializer(serializers.ModelSerializer):
    scenario_title = serializers.CharField(source="get_scenario_display", read_only=True)
    headline = serializers.SerializerMethodField()

    class Meta:
        model = SimulationRun
        fields = (
            "public_id", "scenario", "scenario_title", "sensitivity", "seed", "transaction_count",
            "model_version", "duration_ms", "headline", "created_at",
        )

    def get_headline(self, obj: SimulationRun) -> dict[str, Any]:
        m = obj.metrics or {}
        return {
            key: m.get(key)
            for key in ("analyzed", "blocked", "reviewed", "allowed", "precision_flagged", "recall_flagged", "false_positive_rate")
        }


# ── Investigator ─────────────────────────────────────────────────────────────

class InvestigationCreateSerializer(serializers.Serializer):
    question = serializers.CharField(max_length=500)

    def validate_question(self, value: str) -> str:
        value = " ".join(value.split())
        if len(value) < 3:
            raise serializers.ValidationError("Ask a fuller question.")
        return value


class InvestigationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Investigation
        fields = (
            "public_id", "question", "intent", "queries", "results", "answer", "facts",
            "inferences", "evidence", "mode", "model_version", "latency_ms", "created_at",
        )


# ── Agents ───────────────────────────────────────────────────────────────────

class AgentPolicySerializer(serializers.ModelSerializer):
    allowed_categories = serializers.ListField(child=serializers.CharField(max_length=40), required=False, default=list, max_length=30)
    blocked_merchants = serializers.ListField(child=serializers.CharField(max_length=80), required=False, default=list, max_length=50)
    currency = serializers.ChoiceField(choices=sorted(SUPPORTED_CURRENCIES), required=False, default="USD")
    spent_today = serializers.SerializerMethodField()

    class Meta:
        model = AgentPolicy
        fields = (
            "public_id", "name", "daily_limit", "transaction_limit", "requires_approval_above",
            "allowed_categories", "blocked_merchants", "currency", "active", "spent_today",
            "created_at", "updated_at",
        )
        read_only_fields = ("public_id", "created_at", "updated_at")
        extra_kwargs = {
            "daily_limit": {"min_value": Decimal("0.01"), "max_value": MAX_AMOUNT},
            "transaction_limit": {"min_value": Decimal("0.01"), "max_value": MAX_AMOUNT},
            "requires_approval_above": {"min_value": Decimal("0"), "max_value": MAX_AMOUNT},
        }

    def validate_allowed_categories(self, value: list[str]) -> list[str]:
        return sorted({v.strip().lower() for v in value if v.strip()})

    def validate_blocked_merchants(self, value: list[str]) -> list[str]:
        return sorted({v.strip() for v in value if v.strip()})

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        daily = attrs.get("daily_limit", getattr(self.instance, "daily_limit", None))
        per_txn = attrs.get("transaction_limit", getattr(self.instance, "transaction_limit", None))
        if daily is not None and per_txn is not None and per_txn > daily:
            raise serializers.ValidationError({"transaction_limit": "Per-transaction limit cannot exceed the daily limit."})
        return attrs

    def get_spent_today(self, obj: AgentPolicy) -> str:
        from .services.agents import spent_today

        return str(spent_today(obj))


class AgentTransactionCreateSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, max_value=MAX_AMOUNT)
    currency = serializers.ChoiceField(choices=sorted(SUPPORTED_CURRENCIES), required=False)
    category = serializers.CharField(max_length=40)
    merchant_id = serializers.CharField(max_length=80)
    description = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")


class AgentTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentTransaction
        fields = (
            "id", "amount", "currency", "category", "merchant_id", "description", "decision",
            "reasons", "risk_score", "counts_toward_spend", "created_at",
        )


__all__ = [
    "AgentPolicySerializer",
    "AgentTransactionCreateSerializer",
    "AgentTransactionSerializer",
    "AuditLogSerializer",
    "Decision",
    "InvestigationCreateSerializer",
    "InvestigationSerializer",
    "ManualReviewSerializer",
    "PaymentCreateSerializer",
    "PaymentDetailSerializer",
    "PaymentEventSerializer",
    "PaymentSummarySerializer",
    "PolicyUpdateSerializer",
    "RiskEvaluationSerializer",
    "SimulationCreateSerializer",
    "SimulationRunListSerializer",
    "SimulationRunSerializer",
]
