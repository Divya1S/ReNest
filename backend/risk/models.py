"""Persistence for ReNest Risk Intelligence.

Tables, in the order a payment touches them:

  PaymentTransaction  the payment itself and its final decision/status
  RiskEvaluation      one row per engine run (a transaction can be re-evaluated)
  RiskFactor          the individual scored signals behind an evaluation
  PaymentEvent        the append-only event log (payment.created, ...)
  AuditLog            who did what, when and why, for security-sensitive actions
  IdempotencyKey      request de-duplication for POST /api/risk/payments
  RiskPolicy          the active sensitivity/thresholds, editable by staff
  SimulationRun       a Fraud Lab run and its measured metrics
  AgentPolicy         spending policy for an autonomous agent
  AgentTransaction    an agent's attempted purchase and the policy verdict
  Investigation       a Risk Investigator question, the queries it ran, the answer

Money is Decimal(12, 2); every datetime is timezone-aware (USE_TZ).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db import models
from django.utils import timezone

from .ids import public_id


class Decision(models.TextChoices):
    ALLOW = "allow", "Allow"
    REVIEW = "review", "Review"
    BLOCK = "block", "Block"


class TransactionStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REVIEW = "review", "Under review"
    BLOCKED = "blocked", "Blocked"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"


class PaymentMethod(models.TextChoices):
    CARD = "card", "Card"
    BANK = "bank", "Bank transfer"
    WALLET = "wallet", "Wallet"
    CAMPUS_CREDIT = "campus_credit", "Campus credit"


# Currencies the sandbox accepts. JPY is zero-decimal: amounts must be whole.
SUPPORTED_CURRENCIES: dict[str, int] = {
    "USD": 2, "EUR": 2, "GBP": 2, "CAD": 2, "AUD": 2, "INR": 2, "JPY": 0,
}


class ScopedQuerySet(models.QuerySet):
    def for_user(self, user: Any) -> "ScopedQuerySet":
        """Staff see everything; everyone else only their own rows."""
        if getattr(user, "is_staff", False):
            return self
        return self.filter(user=user)


def _txn_id() -> str:
    return public_id("txn")


def _evt_id() -> str:
    return public_id("evt")


def _sim_id() -> str:
    return public_id("sim")


def _inv_id() -> str:
    return public_id("inv")


def _agent_id() -> str:
    return public_id("agent")


class PaymentTransaction(models.Model):
    """A sandbox payment. No real money moves; see risk.services.processor."""

    public_id = models.CharField(max_length=40, unique=True, default=_txn_id, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="risk_transactions"
    )
    merchant_id = models.CharField(max_length=80)
    merchant_category = models.CharField(max_length=40, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3)
    payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices)
    device_id = models.CharField(max_length=80, blank=True)
    # Coarse network signal: the first two octets (IPv4) or /32 prefix (IPv6),
    # never a full address. Enough for "same network" reasoning, not tracking.
    ip_prefix = models.CharField(max_length=40, blank=True)
    country = models.CharField(max_length=2, blank=True)

    status = models.CharField(
        max_length=16, choices=TransactionStatus.choices, default=TransactionStatus.PENDING
    )
    decision = models.CharField(max_length=8, choices=Decision.choices, blank=True)
    risk_score = models.PositiveSmallIntegerField(null=True, blank=True)
    confidence = models.DecimalField(max_digits=4, decimal_places=3, null=True, blank=True)
    model_version = models.CharField(max_length=40, blank=True)
    evaluation_ms = models.DecimalField(max_digits=8, decimal_places=3, null=True, blank=True)

    request_id = models.CharField(max_length=64, blank=True)
    processor_reference = models.CharField(max_length=80, blank=True)
    failure_reason = models.CharField(max_length=200, blank=True)

    # Fraud Lab rows live alongside real ones but are always labelled.
    is_simulation = models.BooleanField(default=False)
    simulation_run = models.ForeignKey(
        "SimulationRun", null=True, blank=True, on_delete=models.CASCADE, related_name="transactions"
    )
    # Ground truth is only ever known for synthetic data.
    ground_truth_fraud = models.BooleanField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ScopedQuerySet.as_manager()

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=["user", "-created_at"], name="risk_txn_user_created_idx"),
            models.Index(fields=["user", "device_id"], name="risk_txn_user_device_idx"),
            models.Index(fields=["decision", "-created_at"], name="risk_txn_decision_idx"),
            models.Index(fields=["is_simulation", "-created_at"], name="risk_txn_sim_idx"),
            models.Index(fields=["device_id", "-created_at"], name="risk_txn_device_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=Decimal("0")), name="risk_txn_amount_positive"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.public_id} {self.amount} {self.currency} {self.decision or self.status}"

    @property
    def risk_band(self) -> str:
        if self.risk_score is None:
            return "unknown"
        if self.risk_score >= 60:
            return "high"
        if self.risk_score >= 30:
            return "medium"
        return "low"


class RiskEvaluation(models.Model):
    """One run of the decision engine over a transaction context."""

    transaction = models.ForeignKey(
        PaymentTransaction, on_delete=models.CASCADE, related_name="evaluations"
    )
    score = models.PositiveSmallIntegerField()
    decision = models.CharField(max_length=8, choices=Decision.choices)
    confidence = models.DecimalField(max_digits=4, decimal_places=3)
    model_version = models.CharField(max_length=40)
    sensitivity = models.PositiveSmallIntegerField()
    review_threshold = models.PositiveSmallIntegerField()
    block_threshold = models.PositiveSmallIntegerField()
    evaluation_ms = models.DecimalField(max_digits=8, decimal_places=3)
    explanation = models.TextField(blank=True)
    # The exact signals the engine saw, so a decision can be replayed later.
    context_snapshot = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=["transaction", "-created_at"], name="risk_eval_txn_idx"),
            models.Index(fields=["-created_at"], name="risk_eval_created_idx"),
        ]

    def __str__(self) -> str:
        return f"eval {self.pk} {self.decision} {self.score}"


class RiskFactor(models.Model):
    """A single scored signal. Points are signed: negative reduces risk."""

    evaluation = models.ForeignKey(RiskEvaluation, on_delete=models.CASCADE, related_name="factors")
    code = models.CharField(max_length=40)
    label = models.CharField(max_length=160)
    points = models.SmallIntegerField()
    detail = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("-points", "code")
        indexes = [models.Index(fields=["code"], name="risk_factor_code_idx")]

    def __str__(self) -> str:
        return f"{self.code} {self.points:+d}"


class EventStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSING = "processing", "Claimed by a dispatcher"
    PROCESSED = "processed", "Processed"
    FAILED = "failed", "Failed (will retry)"
    DEAD = "dead", "Dead-lettered"


class PaymentEvent(models.Model):
    """Append-only domain event.

    Delivery guarantee is at-least-once: a handler can see the same event twice
    after a crash between "handler ran" and "processed_handlers written", so
    every handler must be idempotent. Ordering is guaranteed per entity by the
    auto-increment `sequence`, not globally.
    """

    event_id = models.CharField(max_length=40, unique=True, default=_evt_id, editable=False)
    sequence = models.BigAutoField(primary_key=True)
    event_type = models.CharField(max_length=64)
    schema_version = models.PositiveSmallIntegerField(default=1)
    entity_type = models.CharField(max_length=40)
    entity_id = models.CharField(max_length=40)
    payload = models.JSONField(default=dict, blank=True)
    request_id = models.CharField(max_length=64, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now)

    status = models.CharField(max_length=12, choices=EventStatus.choices, default=EventStatus.PENDING)
    # Set by the dispatcher that owns the row; the lease expires at next_attempt_at.
    claim_token = models.CharField(max_length=40, blank=True, default="")
    attempts = models.PositiveSmallIntegerField(default=0)
    processed_handlers = models.JSONField(default=list, blank=True)
    last_error = models.TextField(blank=True)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("sequence",)
        indexes = [
            models.Index(fields=["entity_type", "entity_id", "sequence"], name="risk_event_entity_idx"),
            models.Index(fields=["status", "next_attempt_at"], name="risk_event_status_idx"),
            models.Index(fields=["event_type", "-occurred_at"], name="risk_event_type_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.event_type} {self.entity_id}"


class AuditLogQuerySet(models.QuerySet):
    def delete(self) -> Any:
        raise PermissionError("Audit records are append-only.")


class AuditLog(models.Model):
    """WHO did WHAT to which record, WHEN, and WHY. Append-only by construction:
    the model refuses updates and the queryset refuses deletes."""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    actor_label = models.CharField(max_length=80)  # "user:42" or "service:risk-engine"
    action = models.CharField(max_length=64)
    target_type = models.CharField(max_length=40)
    target_id = models.CharField(max_length=40)
    reason = models.TextField(blank=True)
    decision = models.CharField(max_length=16, blank=True)
    model_version = models.CharField(max_length=40, blank=True)
    request_id = models.CharField(max_length=64, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    objects = AuditLogQuerySet.as_manager()

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=["target_type", "target_id", "-created_at"], name="risk_audit_target_idx"),
            models.Index(fields=["action", "-created_at"], name="risk_audit_action_idx"),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.pk is not None:
            raise PermissionError("Audit records cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> Any:
        raise PermissionError("Audit records cannot be deleted.")

    def __str__(self) -> str:
        return f"{self.action} {self.target_type}:{self.target_id} by {self.actor_label}"


class IdempotencyStatus(models.TextChoices):
    IN_PROGRESS = "in_progress", "In progress"
    COMPLETED = "completed", "Completed"


class IdempotencyKey(models.Model):
    """One row per (user, scope, key). See risk.services.idempotency."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    scope = models.CharField(max_length=64)
    key = models.CharField(max_length=255)
    request_hash = models.CharField(max_length=64)
    status = models.CharField(
        max_length=16, choices=IdempotencyStatus.choices, default=IdempotencyStatus.IN_PROGRESS
    )
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    response_body = models.JSONField(null=True, blank=True)
    transaction = models.ForeignKey(
        PaymentTransaction, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "scope", "key"], name="risk_idempotency_unique"),
        ]
        indexes = [models.Index(fields=["expires_at"], name="risk_idempotency_expiry_idx")]

    def __str__(self) -> str:
        return f"{self.scope}:{self.key} ({self.status})"


class RiskPolicy(models.Model):
    """The live sensitivity. A single active row; edits are audited."""

    name = models.CharField(max_length=80, default="default")
    sensitivity = models.PositiveSmallIntegerField(default=50)
    active = models.BooleanField(default=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(sensitivity__gte=0, sensitivity__lte=100),
                name="risk_policy_sensitivity_range",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} (sensitivity {self.sensitivity})"


class SimulationScenario(models.TextChoices):
    NORMAL = "normal_customer", "Normal customer"
    CARD_TESTING = "card_testing", "Card testing"
    ACCOUNT_TAKEOVER = "account_takeover", "Account takeover"
    NEW_DEVICE = "new_device_attack", "New device attack"
    HIGH_VELOCITY = "high_velocity", "High velocity attack"
    SUSPICIOUS_AMOUNT = "suspicious_amount", "Suspicious amount"
    COORDINATED = "coordinated_fraud", "Coordinated fraud"
    MULTI_ACCOUNT = "multi_account", "Multiple-account attack"


class SimulationRun(models.Model):
    """A Fraud Lab run: synthetic transactions with ground-truth labels, scored
    by the real engine, with metrics measured from what the engine actually did."""

    public_id = models.CharField(max_length=40, unique=True, default=_sim_id, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="risk_simulations")
    scenario = models.CharField(max_length=32, choices=SimulationScenario.choices)
    sensitivity = models.PositiveSmallIntegerField()
    seed = models.PositiveIntegerField()
    transaction_count = models.PositiveIntegerField()
    model_version = models.CharField(max_length=40)
    metrics = models.JSONField(default=dict, blank=True)
    duration_ms = models.DecimalField(max_digits=10, decimal_places=3, default=Decimal("0"))
    created_at = models.DateTimeField(default=timezone.now)

    objects = ScopedQuerySet.as_manager()

    class Meta:
        ordering = ("-created_at", "-id")

    def __str__(self) -> str:
        return f"{self.public_id} {self.scenario} n={self.transaction_count}"


class AgentPolicy(models.Model):
    """Spending policy for an autonomous purchasing agent."""

    public_id = models.CharField(max_length=40, unique=True, default=_agent_id, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="risk_agents")
    name = models.CharField(max_length=80)
    daily_limit = models.DecimalField(max_digits=12, decimal_places=2)
    transaction_limit = models.DecimalField(max_digits=12, decimal_places=2)
    requires_approval_above = models.DecimalField(max_digits=12, decimal_places=2)
    allowed_categories = models.JSONField(default=list, blank=True)
    blocked_merchants = models.JSONField(default=list, blank=True)
    currency = models.CharField(max_length=3, default="USD")
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ScopedQuerySet.as_manager()

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.public_id} {self.name}"


class AgentTransaction(models.Model):
    """An agent's purchase attempt and the policy engine's verdict."""

    agent = models.ForeignKey(AgentPolicy, on_delete=models.CASCADE, related_name="attempts")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3)
    category = models.CharField(max_length=40)
    merchant_id = models.CharField(max_length=80)
    description = models.CharField(max_length=200, blank=True)
    decision = models.CharField(max_length=8, choices=Decision.choices)
    reasons = models.JSONField(default=list, blank=True)
    risk_score = models.PositiveSmallIntegerField(null=True, blank=True)
    counts_toward_spend = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [models.Index(fields=["agent", "-created_at"], name="risk_agent_txn_idx")]

    def __str__(self) -> str:
        return f"{self.agent_id} {self.amount} {self.decision}"


class Investigation(models.Model):
    """A Risk Investigator question and the evidence-backed answer."""

    public_id = models.CharField(max_length=40, unique=True, default=_inv_id, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="risk_investigations")
    question = models.CharField(max_length=500)
    intent = models.CharField(max_length=40)
    queries = models.JSONField(default=list, blank=True)
    results = models.JSONField(default=dict, blank=True)
    answer = models.TextField()
    facts = models.JSONField(default=list, blank=True)
    inferences = models.JSONField(default=list, blank=True)
    evidence = models.JSONField(default=list, blank=True)
    mode = models.CharField(max_length=16)  # "deterministic" | "llm"
    model_version = models.CharField(max_length=60, blank=True)
    latency_ms = models.DecimalField(max_digits=10, decimal_places=3, default=Decimal("0"))
    created_at = models.DateTimeField(default=timezone.now)

    objects = ScopedQuerySet.as_manager()

    class Meta:
        ordering = ("-created_at", "-id")

    def __str__(self) -> str:
        return f"{self.public_id} {self.intent}"
