"""Payment creation: the orchestration a single POST /api/risk/payments runs.

    validate  ->  persist PaymentTransaction (pending)
              ->  bind the idempotency key to it (same transaction)
              ->  build TransactionContext from history
              ->  evaluate_transaction (pure)
              ->  persist RiskEvaluation + RiskFactors
              ->  emit payment.created / payment.risk_evaluated / decision event
              ->  ALLOW: charge the processor with bounded retries
                         -> payment.completed | payment.failed
                  REVIEW: leave for a human, payment.review_required
                  BLOCK:  payment.blocked
              ->  audit

Everything up to the processor call is one database transaction, so a
payment row can never exist without its evaluation, its events and, when the
client sent an Idempotency-Key, the key that points at it. The processor
call happens after commit: a real PSP is an external side effect that must
not be inside a database transaction (it cannot be rolled back), and the
idempotent processor reference (the transaction's public id) is what makes a
retry after a crash safe.

Order of events around the commit, which is what makes a retry safe:

    1. COMMIT: payment, evaluation, events, audit row and key binding
       become durable together.
    2. Post-commit hooks run: counters, then inline dispatch of this
       request's events. They cannot raise into the request.
    3. ALLOW only: the processor is charged and the terminal state recorded
       in its own transaction (which commits and dispatches the same way).
    4. The caller stores the final response on the idempotency key.

A failure anywhere from step 2 on leaves a bound key: the client's retry is
answered with the payment that exists, never with a second one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.utils import timezone

from ..engine import RiskDecision, evaluate_transaction
from ..engine.signals import build_context
from ..models import (
    Decision,
    IdempotencyKey,
    PaymentTransaction,
    RiskEvaluation,
    RiskFactor,
    TransactionStatus,
)
from . import audit, events, idempotency, metrics
from .policy import current_sensitivity
from .processor import get_processor
from .retry import PermanentError, RetryPolicy, TransientError, run_with_retries

logger = logging.getLogger("risk.payments")

_OUTCOME_EVENT: dict[str, str] = {
    Decision.ALLOW: events.PAYMENT_APPROVED,
    Decision.REVIEW: events.PAYMENT_REVIEW_REQUIRED,
    Decision.BLOCK: events.PAYMENT_BLOCKED,
}

PROCESSOR_RETRY = RetryPolicy(attempts=3, base_delay=0.05, max_delay=0.4, retryable=(TransientError,))


@dataclass(frozen=True)
class PaymentInput:
    merchant_id: str
    amount: Decimal
    currency: str
    payment_method: str
    device_id: str = ""
    merchant_category: str = ""
    country: str = ""
    sandbox_behavior: str = "succeed"
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class PaymentResult:
    transaction: PaymentTransaction
    evaluation: RiskEvaluation
    decision: RiskDecision


def _persist_evaluation(txn: PaymentTransaction, decision: RiskDecision, created_at: datetime | None = None) -> RiskEvaluation:
    evaluation = RiskEvaluation.objects.create(
        **({"created_at": created_at} if created_at is not None else {}),
        transaction=txn,
        score=decision.risk_score,
        decision=decision.decision,
        confidence=Decimal(str(decision.confidence)),
        model_version=decision.model_version,
        sensitivity=decision.thresholds.sensitivity,
        review_threshold=decision.thresholds.review,
        block_threshold=decision.thresholds.block,
        evaluation_ms=Decimal(str(decision.evaluation_time_ms)),
        explanation=decision.explanation,
        context_snapshot=decision.context.snapshot(),
    )
    RiskFactor.objects.bulk_create(
        [
            RiskFactor(evaluation=evaluation, code=f.code, label=f.label, points=f.points, detail=f.detail)
            for f in decision.factors
        ]
    )
    return evaluation


_STATUS_FOR_DECISION: dict[str, str] = {
    Decision.ALLOW: TransactionStatus.APPROVED,
    Decision.REVIEW: TransactionStatus.REVIEW,
    Decision.BLOCK: TransactionStatus.BLOCKED,
}


def _status_for(decision: str) -> str:
    return _STATUS_FOR_DECISION[decision]


def create_payment(
    *,
    user: Any,
    data: PaymentInput,
    request_id: str = "",
    ip_prefix: str = "",
    idempotency_key: IdempotencyKey | None = None,
    now: datetime | None = None,
) -> PaymentResult:
    """Create, evaluate and (when allowed) capture a payment.

    `idempotency_key` is the key record the caller acquired for this request.
    It is bound to the payment inside the transaction that inserts the
    payment, so the two are durable together; if the request has lost
    ownership of the key in the meantime the bind raises
    idempotency.OwnershipLost and nothing is committed.

    `now` evaluates the payment as of an earlier instant (seeded history):
    the row, its evaluation and its events carry that time and the velocity
    windows are measured from it. Live callers leave it unset.
    """
    with metrics.Timer() as total:
        with transaction.atomic():
            txn = PaymentTransaction.objects.create(
                **({"created_at": now} if now is not None else {}),
                user=user,
                merchant_id=data.merchant_id,
                merchant_category=data.merchant_category,
                amount=data.amount,
                currency=data.currency,
                payment_method=data.payment_method,
                device_id=data.device_id,
                ip_prefix=ip_prefix,
                country=data.country,
                request_id=request_id,
                metadata={**(data.metadata or {}), "sandbox_behavior": data.sandbox_behavior},
            )
            if idempotency_key is not None:
                # First thing after the insert: it fails fast when ownership
                # is gone, and on PostgreSQL it holds the key's row lock for
                # the rest of this transaction, so a concurrent retry waits
                # and then sees the key bound instead of racing us.
                idempotency.bind(idempotency_key, txn)
            events.emit(
                events.PAYMENT_CREATED,
                entity_type="payment",
                entity_id=txn.public_id,
                payload={
                    "user_id": user.pk,
                    "amount": str(txn.amount),
                    "currency": txn.currency,
                    "merchant_id": txn.merchant_id,
                    "payment_method": txn.payment_method,
                },
                request_id=request_id,
                occurred_at=now,
            )

            ctx = build_context(
                user=user,
                transaction_id=txn.public_id,
                merchant_id=txn.merchant_id,
                merchant_category=txn.merchant_category,
                amount=txn.amount,
                currency=txn.currency,
                payment_method=txn.payment_method,
                device_id=txn.device_id,
                ip_prefix=ip_prefix,
                country=txn.country,
                now=txn.created_at,
            )
            decision = evaluate_transaction(ctx, sensitivity=current_sensitivity())
            metrics.observe("risk_evaluation_ms", decision.evaluation_time_ms)
            evaluation = _persist_evaluation(txn, decision, created_at=now)

            txn.decision = decision.decision
            txn.risk_score = decision.risk_score
            txn.confidence = Decimal(str(decision.confidence))
            txn.model_version = decision.model_version
            txn.evaluation_ms = Decimal(str(decision.evaluation_time_ms))
            txn.status = _status_for(decision.decision)
            txn.save(update_fields=["decision", "risk_score", "confidence", "model_version", "evaluation_ms", "status", "updated_at"])

            decision_payload = {
                "user_id": user.pk,
                "decision": decision.decision,
                "risk_score": decision.risk_score,
                "confidence": decision.confidence,
                "reasons": decision.reasons,
                "model_version": decision.model_version,
                "summary": decision.summary,
                "evaluation_ms": decision.evaluation_time_ms,
            }
            events.emit(events.PAYMENT_RISK_EVALUATED, entity_type="payment", entity_id=txn.public_id, payload=decision_payload, request_id=request_id, occurred_at=now)
            outcome_event = _OUTCOME_EVENT[decision.decision]
            events.emit(outcome_event, entity_type="payment", entity_id=txn.public_id, payload=decision_payload, request_id=request_id, occurred_at=now)

            audit.record(
                action="payment.evaluated",
                target_type="payment",
                target_id=txn.public_id,
                actor=user,
                reason=decision.summary,
                decision=decision.decision,
                model_version=decision.model_version,
                request_id=request_id,
                metadata={"risk_score": decision.risk_score, "reasons": decision.reasons, "amount": str(txn.amount), "currency": txn.currency},
            )
            # A cache counter does not roll back with the transaction, so it
            # is only incremented once the payment is durable.
            metrics.increment_on_commit("payments_created_total")

        # Outside the transaction: the external side effect.
        if decision.decision == Decision.ALLOW:
            capture(txn, behavior=data.sandbox_behavior, request_id=request_id, occurred_at=now)

    metrics.observe("payment_create_ms", total.ms)
    txn.refresh_from_db()
    return PaymentResult(transaction=txn, evaluation=evaluation, decision=decision)


def capture(
    txn: PaymentTransaction, *, behavior: str = "succeed", request_id: str = "", occurred_at: datetime | None = None
) -> PaymentTransaction:
    """Charge through the processor with bounded retries and record the terminal state.

    Several callers can reach this for the same payment (the request, the
    reconciler from the tick, the reconciler from a worker). The charge is
    idempotent on the reference; the terminal write is conditional on the
    payment still being APPROVED, so only one caller records the outcome and
    emits its event.
    """
    processor = get_processor()
    awaiting_capture = PaymentTransaction.objects.filter(pk=txn.pk, status=TransactionStatus.APPROVED)
    try:
        result = run_with_retries(
            lambda: processor.charge(reference=txn.public_id, amount=txn.amount, currency=txn.currency, behavior=behavior),
            policy=PROCESSOR_RETRY,
            label=f"processor.charge {txn.public_id}",
        )
    except (TransientError, PermanentError) as exc:
        kind = "transient" if isinstance(exc, TransientError) else "permanent"
        with transaction.atomic():
            recorded = awaiting_capture.update(
                status=TransactionStatus.FAILED,
                failure_reason=f"{kind}: {exc}"[:200],
                updated_at=timezone.now(),
            )
            if recorded:
                events.emit(
                    events.PAYMENT_FAILED,
                    entity_type="payment",
                    entity_id=txn.public_id,
                    payload={"user_id": txn.user_id, "reason": str(exc)[:200], "failure_kind": kind, "summary": f"Processor {kind} failure"},
                    request_id=request_id,
                    occurred_at=occurred_at,
                )
        if recorded:
            metrics.increment("payments_failed_total")
            logger.warning("payment %s failed at processor (%s): %s", txn.public_id, kind, exc)
        txn.refresh_from_db()
        return txn

    with transaction.atomic():
        recorded = awaiting_capture.update(
            status=TransactionStatus.COMPLETED,
            processor_reference=result.reference,
            updated_at=timezone.now(),
        )
        if recorded:
            events.emit(
                events.PAYMENT_COMPLETED,
                entity_type="payment",
                entity_id=txn.public_id,
                payload={"user_id": txn.user_id, "processor_reference": result.reference, "processor": processor.name},
                request_id=request_id,
                occurred_at=occurred_at,
            )
    if recorded:
        metrics.increment("payments_completed_total")
    txn.refresh_from_db()
    return txn


def apply_manual_review(*, txn: PaymentTransaction, reviewer: Any, approve: bool, note: str, request_id: str = "") -> PaymentTransaction:
    """A staff member resolves a REVIEW decision. Approving continues to capture."""
    outcome = "approved" if approve else "rejected"
    with transaction.atomic():
        # Lock the row so two reviewers cannot both resolve it.
        txn = PaymentTransaction.objects.select_for_update().get(pk=txn.pk)
        if txn.status != TransactionStatus.REVIEW:
            raise ValueError("Only payments under review can be resolved.")
        txn.status = TransactionStatus.APPROVED if approve else TransactionStatus.BLOCKED
        txn.decision = Decision.ALLOW if approve else Decision.BLOCK
        txn.metadata = {**txn.metadata, "manual_review": {"by": reviewer.pk, "outcome": outcome, "note": note[:500]}}
        txn.save(update_fields=["status", "decision", "metadata", "updated_at"])
        audit.record(
            action="payment.manual_review",
            target_type="payment",
            target_id=txn.public_id,
            actor=reviewer,
            reason=note or f"Manual review: {outcome}",
            decision=txn.decision,
            model_version=txn.model_version,
            request_id=request_id,
            metadata={"outcome": outcome},
        )
        events.emit(
            events.PAYMENT_REVIEWED,
            entity_type="payment",
            entity_id=txn.public_id,
            # No reviewer identity here: the payment's owner can read this
            # event. Who reviewed, and why, is in the audit row above.
            payload={"user_id": txn.user_id, "outcome": outcome, "summary": f"Manual review {outcome}"},
            request_id=request_id,
        )
    if approve:
        behavior = str(txn.metadata.get("sandbox_behavior") or "succeed")
        return capture(txn, behavior=behavior, request_id=request_id)
    txn.refresh_from_db()
    return txn


RECONCILE_AFTER = timedelta(minutes=5)


def reconcile_uncaptured(*, older_than: timedelta = RECONCILE_AFTER, limit: int = 100) -> dict[str, int]:
    """Re-drive payments that were approved but never reached a terminal state.

    A process can die between committing the approval and recording the
    processor's answer. The processor reference is the transaction's public
    id and the sandbox (like a real PSP with idempotency keys) treats a repeat
    charge for the same reference as a no-op, so calling capture again is
    safe. Runs from the maintenance tick and the worker command.
    """
    cutoff = timezone.now() - older_than
    stale = list(
        PaymentTransaction.objects.filter(
            status=TransactionStatus.APPROVED, is_simulation=False, processor_reference="", updated_at__lte=cutoff
        ).order_by("updated_at")[:limit]
    )
    completed = failed = 0
    for txn in stale:
        behavior = str(txn.metadata.get("sandbox_behavior") or "succeed") if isinstance(txn.metadata, dict) else "succeed"
        result = capture(txn, behavior=behavior, request_id=txn.request_id)
        if result.status == TransactionStatus.COMPLETED:
            completed += 1
        else:
            failed += 1
    if stale:
        logger.info("reconciled uncaptured payments", extra={"completed": completed, "failed": failed})
    return {"completed": completed, "failed": failed}
