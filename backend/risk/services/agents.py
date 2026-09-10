"""Agent Payment Sandbox: a policy engine for autonomous purchasing agents.

An agent is a principal with an explicit spending policy. Every attempted
purchase runs through the checks below, in order; the first hard failure
blocks, softer conditions downgrade to REVIEW, and each check contributes a
structured reason so the verdict is fully explainable.

    1. policy active
    2. amount is positive and in the policy's currency
    3. merchant not on the blocklist
    4. category on the allowlist
    5. amount <= per-transaction limit
    6. today's approved spend + amount <= daily limit
    7. risk engine score below the block threshold (the agent's owner is the
       account, the agent id is the device)
    8. amount > requires_approval_above  -> REVIEW

Approved and reviewed attempts count toward daily spend (a reviewed purchase
is reserved until a human resolves it); blocked ones never do.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from ..engine import evaluate_transaction, thresholds_for
from ..engine.context import TransactionContext
from ..models import AgentPolicy, AgentTransaction, Decision, PaymentTransaction, TransactionStatus
from . import audit, events
from .policy import current_sensitivity


@dataclass(frozen=True)
class AgentDecision:
    decision: str
    reasons: list[dict[str, Any]]
    risk_score: int | None
    counts_toward_spend: bool
    spent_today: Decimal
    remaining_today: Decimal


@dataclass
class _Checks:
    reasons: list[dict[str, Any]] = field(default_factory=list)
    blocked: bool = False
    review: bool = False

    def block(self, code: str, text: str, **detail: Any) -> None:
        self.blocked = True
        self.reasons.append({"code": code, "outcome": "block", "text": text, **detail})

    def hold(self, code: str, text: str, **detail: Any) -> None:
        self.review = True
        self.reasons.append({"code": code, "outcome": "review", "text": text, **detail})

    def note(self, code: str, text: str, **detail: Any) -> None:
        self.reasons.append({"code": code, "outcome": "pass", "text": text, **detail})


def _day_start(now: datetime) -> datetime:
    """Midnight in the project's time zone (the campus day, not the UTC day)."""
    local = timezone.localtime(now)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def spent_today(agent: AgentPolicy, now: datetime | None = None) -> Decimal:
    now = now or timezone.now()
    total = AgentTransaction.objects.filter(
        agent=agent, counts_toward_spend=True, created_at__gte=_day_start(now)
    ).aggregate(total=Sum("amount"))["total"]
    return Decimal(total or 0).quantize(Decimal("0.01"))


def _risk_context(agent: AgentPolicy, amount: Decimal, currency: str, merchant_id: str, category: str, now: datetime) -> TransactionContext:
    owner = agent.user
    device_id = f"agent:{agent.public_id}"
    day_ago = now - timedelta(hours=24)
    prior = AgentTransaction.objects.filter(agent=agent)
    approved = prior.filter(decision=Decision.ALLOW)
    avg = approved.aggregate(avg=Sum("amount"))["avg"]
    approved_count = approved.count()
    avg_amount = (Decimal(avg) / approved_count).quantize(Decimal("0.01")) if avg and approved_count else None
    joined = getattr(owner, "created_at", None)
    account_age_days = (now - joined).total_seconds() / 86400 if joined else 0.0
    # The owner's real payment history contributes to trust, the agent's own
    # attempts to velocity.
    owner_success = PaymentTransaction.objects.filter(
        user=owner, is_simulation=False, status__in=(TransactionStatus.APPROVED, TransactionStatus.COMPLETED)
    ).count()
    return TransactionContext(
        transaction_id=f"agent-attempt:{agent.public_id}",
        user_id=owner.pk,
        merchant_id=merchant_id,
        merchant_category=category,
        amount=amount,
        currency=currency,
        payment_method="wallet",
        device_id=device_id,
        timestamp=now,
        account_age_days=max(0.0, account_age_days),
        historical_txn_count=approved_count,
        successful_txn_count=max(approved_count, owner_success),
        historical_avg_amount=avg_amount,
        historical_currencies=(agent.currency,) if approved_count else (),
        merchant_seen_count=approved.filter(merchant_id=merchant_id).count(),
        txn_count_1h=prior.filter(created_at__gte=now - timedelta(hours=1)).count(),
        txn_count_24h=prior.filter(created_at__gte=day_ago).count(),
        failed_txn_count_24h=prior.filter(created_at__gte=day_ago, decision=Decision.BLOCK).count(),
        device_seen_count=approved_count,
        distinct_devices_24h=1,
        distinct_ips_24h=1,
        accounts_on_device_24h=1,
    )


def evaluate_agent_transaction(
    *,
    agent: AgentPolicy,
    amount: Decimal,
    currency: str,
    category: str,
    merchant_id: str,
    description: str = "",
    actor: Any = None,
    request_id: str = "",
    now: datetime | None = None,
) -> tuple[AgentTransaction, AgentDecision]:
    now = now or timezone.now()
    with transaction.atomic():
        # One evaluation per agent at a time (row lock on PostgreSQL), so two
        # concurrent attempts cannot both squeeze under the daily limit.
        agent = AgentPolicy.objects.select_for_update().get(pk=agent.pk)
        return _evaluate_locked(agent, amount, currency, category, merchant_id, description, actor, request_id, now)


def _evaluate_locked(
    agent: AgentPolicy,
    amount: Decimal,
    currency: str,
    category: str,
    merchant_id: str,
    description: str,
    actor: Any,
    request_id: str,
    now: datetime,
) -> tuple[AgentTransaction, AgentDecision]:
    checks = _Checks()
    category = (category or "").strip().lower()
    merchant_id = (merchant_id or "").strip()

    if not agent.active:
        checks.block("agent_inactive", "This agent's policy is inactive.")
    if amount <= 0:
        checks.block("invalid_amount", "Amount must be greater than zero.", amount=str(amount))
    if currency != agent.currency:
        checks.block("currency_mismatch", f"Agent may only spend {agent.currency}.", currency=currency)
    if merchant_id and merchant_id.lower() in {m.lower() for m in (agent.blocked_merchants or [])}:
        checks.block("merchant_blocked", f"Merchant {merchant_id} is on the agent's blocklist.", merchant_id=merchant_id)
    allowed = [c.lower() for c in (agent.allowed_categories or [])]
    if allowed and category not in allowed:
        checks.block(
            "category_not_allowed",
            f"Category '{category or 'unspecified'}' is not in the allowed list ({', '.join(allowed)}).",
            category=category,
        )
    else:
        checks.note("category_allowed", f"Category '{category}' is permitted.", category=category)

    if amount > agent.transaction_limit:
        checks.block(
            "transaction_limit",
            f"Transaction exceeds agent transaction limit of {agent.currency} {agent.transaction_limit}.",
            limit=str(agent.transaction_limit), amount=str(amount),
        )
    else:
        checks.note("within_transaction_limit", f"Within the {agent.currency} {agent.transaction_limit} per-transaction limit.")

    used = spent_today(agent, now)
    if used + amount > agent.daily_limit:
        checks.block(
            "daily_limit",
            f"Would exceed the daily limit of {agent.currency} {agent.daily_limit} "
            f"({agent.currency} {used} already spent today).",
            limit=str(agent.daily_limit), spent_today=str(used), amount=str(amount),
        )
    else:
        remaining = agent.daily_limit - used - amount
        checks.note(
            "within_daily_limit",
            f"{agent.currency} {remaining} of today's {agent.currency} {agent.daily_limit} budget would remain.",
            spent_today=str(used), remaining=str(remaining),
        )

    risk_score: int | None = None
    if not checks.blocked:
        ctx = _risk_context(agent, amount, currency, merchant_id, category, now)
        risk = evaluate_transaction(ctx, sensitivity=current_sensitivity())
        risk_score = risk.risk_score
        thresholds = thresholds_for(risk.thresholds.sensitivity)
        if risk.decision == Decision.BLOCK:
            checks.block("risk_block", f"Risk engine blocked the purchase (score {risk.risk_score} at or above {thresholds.block}).", risk_score=risk.risk_score, reasons=risk.reasons)
        elif risk.decision == Decision.REVIEW:
            checks.hold("risk_review", f"Risk engine asked for review (score {risk.risk_score}).", risk_score=risk.risk_score, reasons=risk.reasons)
        else:
            checks.note("risk_allow", f"Risk score {risk.risk_score} is within the automatic band.", risk_score=risk.risk_score)

    if not checks.blocked and amount > agent.requires_approval_above:
        checks.hold(
            "approval_required",
            f"Transaction exceeds the automatic approval threshold of {agent.currency} {agent.requires_approval_above}.",
            threshold=str(agent.requires_approval_above), amount=str(amount),
        )

    decision = Decision.BLOCK if checks.blocked else (Decision.REVIEW if checks.review else Decision.ALLOW)
    counts = decision in (Decision.ALLOW, Decision.REVIEW)

    with transaction.atomic():
        attempt = AgentTransaction.objects.create(
            agent=agent,
            amount=amount,
            currency=currency,
            category=category,
            merchant_id=merchant_id,
            description=description[:200],
            decision=decision,
            reasons=checks.reasons,
            risk_score=risk_score,
            counts_toward_spend=counts,
            created_at=now,
        )
        if counts and spent_today(agent, now) > agent.daily_limit:
            # Belt and braces for databases without row locks: the aggregate
            # now includes this row, so an over-commit is caught before it
            # is reported as approved.
            checks.block(
                "daily_limit",
                f"Would exceed the daily limit of {agent.currency} {agent.daily_limit} once concurrent attempts are counted.",
                limit=str(agent.daily_limit), amount=str(amount),
            )
            decision = Decision.BLOCK
            counts = False
            attempt.decision = decision
            attempt.reasons = checks.reasons
            attempt.counts_toward_spend = False
            attempt.save(update_fields=["decision", "reasons", "counts_toward_spend"])
        audit.record(
            action="agent.transaction_evaluated",
            target_type="agent",
            target_id=agent.public_id,
            actor=actor,
            reason="; ".join(r["text"] for r in checks.reasons if r["outcome"] != "pass")[:500] or "All policy checks passed",
            decision=decision,
            request_id=request_id,
            metadata={"attempt_id": attempt.pk, "amount": str(amount), "category": category, "merchant_id": merchant_id, "risk_score": risk_score},
        )
        events.emit(
            events.AGENT_DECISION,
            entity_type="agent",
            entity_id=agent.public_id,
            payload={"attempt_id": attempt.pk, "decision": decision, "amount": str(amount), "category": category, "risk_score": risk_score},
            request_id=request_id,
        )

    spent_after = used + (amount if counts else Decimal("0"))
    return attempt, AgentDecision(
        decision=decision,
        reasons=checks.reasons,
        risk_score=risk_score,
        counts_toward_spend=counts,
        spent_today=spent_after,
        remaining_today=max(Decimal("0"), agent.daily_limit - spent_after),
    )


def visible_agents(user: Any) -> Any:
    return AgentPolicy.objects.for_user(user).select_related("user")
