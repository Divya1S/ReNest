"""Internal domain events for payments.

Emission
    `emit()` writes a PaymentEvent row inside the caller's database
    transaction, so an event exists if and only if the state change that
    produced it was committed. Nothing is published to an external broker;
    the table *is* the log. Dispatch is scheduled with transaction.on_commit,
    so a rolled-back payment never fans out. The post-commit hook cannot
    raise: by the time it runs the state change is durable, so a dispatch
    problem is logged and counted and the event simply waits for the next
    pass; it never turns a committed payment into a failed request.

Dispatch
    `dispatch_pending()` walks PENDING/FAILED events in sequence order and
    runs every registered handler for the event type. Each handler is wrapped
    in bounded retries with backoff; an event whose handlers still fail after
    MAX_ATTEMPTS is marked DEAD (the dead-letter state) and surfaced on the
    System Health page for a human, never silently dropped.

Guarantees and assumptions
    * At-least-once delivery. A crash between "handler ran" and
      "processed_handlers saved" replays the handler. Every handler is
      therefore idempotent (get_or_create keyed on event_id, counters keyed
      on event_id, etc.).
    * Per-entity ordering by `sequence`. Global ordering across entities is
      not promised and no handler depends on it.
    * Several dispatchers can run at once (the request's on_commit hook, the
      maintenance tick, a worker). A dispatcher claims rows with a single
      conditional UPDATE that stamps its token and a lease
      (status=processing, next_attempt_at=lease expiry); only rows the UPDATE
      touched are processed. A dispatcher that dies mid-batch leaves rows
      whose lease expires, after which they are claimable again; handler
      idempotency makes that replay harmless. The token is also a fencing
      token: the lease is renewed just before an event's handlers run, and
      the write that records the outcome is conditional on still holding
      the token, so a dispatcher that outlived its lease cannot overwrite
      what the dispatcher that took over recorded.
    * A request dispatches only the events it emitted. The global backlog
      (retries, dead-letter candidates) is the tick's and the worker's job,
      so one broken handler never taxes every customer's checkout.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from ..models import EventStatus, PaymentEvent
from . import metrics
from .retry import RetryOutcome, RetryPolicy, TransientError, run_with_retries

logger = logging.getLogger("risk.events")

SCHEMA_VERSION = 1
MAX_ATTEMPTS = 5
BACKOFF_SECONDS = (5, 30, 120, 600)  # between dispatch passes, per attempt
CLAIM_LEASE_SECONDS = 60             # a claimed row becomes claimable again after this

# ── Event vocabulary ─────────────────────────────────────────────────────────

PAYMENT_CREATED = "payment.created"
PAYMENT_RISK_EVALUATED = "payment.risk_evaluated"
PAYMENT_APPROVED = "payment.approved"
PAYMENT_REVIEW_REQUIRED = "payment.review_required"
PAYMENT_BLOCKED = "payment.blocked"
PAYMENT_COMPLETED = "payment.completed"
PAYMENT_FAILED = "payment.failed"
PAYMENT_REVIEWED = "payment.reviewed"
POLICY_UPDATED = "risk.policy_updated"
AGENT_DECISION = "agent.decision"

EVENT_TYPES = (
    PAYMENT_CREATED,
    PAYMENT_RISK_EVALUATED,
    PAYMENT_APPROVED,
    PAYMENT_REVIEW_REQUIRED,
    PAYMENT_BLOCKED,
    PAYMENT_COMPLETED,
    PAYMENT_FAILED,
    PAYMENT_REVIEWED,
    POLICY_UPDATED,
    AGENT_DECISION,
)

Handler = Callable[[PaymentEvent], None]
_HANDLERS: dict[str, list[tuple[str, Handler]]] = {}


def register(event_type: str, name: str) -> Callable[[Handler], Handler]:
    """Decorator: `@register(PAYMENT_BLOCKED, "notify_customer")`."""

    def wrap(fn: Handler) -> Handler:
        _HANDLERS.setdefault(event_type, []).append((name, fn))
        return fn

    return wrap


def handlers_for(event_type: str) -> list[tuple[str, Handler]]:
    return list(_HANDLERS.get(event_type, []))


# ── Emit ─────────────────────────────────────────────────────────────────────

def emit(
    event_type: str,
    *,
    entity_type: str,
    entity_id: str,
    payload: dict[str, Any] | None = None,
    request_id: str = "",
    dispatch_after_commit: bool = True,
    occurred_at: datetime | None = None,
) -> PaymentEvent:
    """Append an event. `occurred_at` is for callers that record something as
    of an earlier instant (seeded history); live callers leave it unset."""
    if event_type not in EVENT_TYPES:
        raise ValueError(f"Unknown event type: {event_type}")
    fields: dict[str, Any] = dict(
        event_type=event_type,
        schema_version=SCHEMA_VERSION,
        entity_type=entity_type,
        entity_id=str(entity_id),
        payload=payload or {},
        request_id=request_id or "",
    )
    if occurred_at is not None:
        fields["occurred_at"] = occurred_at
    event = PaymentEvent.objects.create(**fields)
    # Counted when the row is durable, not when it is written: a rolled-back
    # payment emitted nothing.
    metrics.increment_on_commit("events_emitted_total")
    logger.info(
        "event emitted",
        extra={"event_type": event_type, "event_id": event.event_id, "entity": f"{entity_type}:{entity_id}"},
    )
    if dispatch_after_commit:
        # Only this event: callbacks run in registration order after the
        # outermost commit, which preserves per-entity sequence order.
        transaction.on_commit(_dispatch_hook(event.pk), robust=True)
    return event


def _dispatch_hook(pk: int) -> Callable[[], None]:
    """The post-commit dispatch of one event, as a callable that never raises.

    The hook runs after the caller's transaction has committed. Anything it
    raised would propagate out of the caller's atomic block and look like a
    failed operation although the payment exists, which is how a retry used
    to create a second payment. So the hook contains its own failures: the
    event stays pending (or claimed with a lease that expires) and the tick
    or the worker delivers it. robust=True is a second line of defence, not
    the mechanism: the hook must be safe wherever it is called from.
    """

    def dispatch_after_commit() -> None:
        try:
            dispatch_events([pk])
        except Exception:  # noqa: BLE001 - must not reach the committed caller
            metrics.increment("event_dispatch_errors_total")
            logger.exception("post-commit dispatch failed; the event waits for the next pass", extra={"event_pk": pk})

    return dispatch_after_commit


# ── Dispatch ─────────────────────────────────────────────────────────────────

def _due(now: Any) -> Q:
    """Rows a dispatcher may claim: pending/failed and due, or a claim whose lease expired."""
    return (
        Q(status__in=[EventStatus.PENDING, EventStatus.FAILED]) & (Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
    ) | Q(status=EventStatus.PROCESSING, next_attempt_at__lte=now)


def _claim(pks: Iterable[int] | None, limit: int) -> list[PaymentEvent]:
    """Claim due events with one conditional UPDATE stamped with a fresh token.

    Whatever the database, only rows this UPDATE changed carry the token, so
    two dispatchers can never both own the same row.
    """
    now = timezone.now()
    token = secrets.token_hex(16)
    with transaction.atomic():
        candidates = PaymentEvent.objects.filter(_due(now)).order_by("sequence")
        if pks is not None:
            candidates = candidates.filter(pk__in=list(pks))
        ids = list(candidates.values_list("pk", flat=True)[:limit])
        if not ids:
            return []
        PaymentEvent.objects.filter(pk__in=ids).filter(_due(now)).update(
            status=EventStatus.PROCESSING,
            claim_token=token,
            next_attempt_at=now + timedelta(seconds=CLAIM_LEASE_SECONDS),
        )
    return list(PaymentEvent.objects.filter(claim_token=token).order_by("sequence"))


OUTCOME_PROCESSED = "processed"
OUTCOME_FAILED = "failed"
OUTCOME_LOST = "lost"


def _renew_claim(event: PaymentEvent) -> bool:
    """Extend the lease right before the handlers run; False when the claim is gone.

    A batch is claimed with one lease for all of its rows, so by the time a
    slow batch reaches its last events their lease may have run out and
    another dispatcher may own them. Renewing per event keeps a live
    dispatcher's claim alive and stops it from running handlers for an
    event it no longer owns.
    """
    lease = timezone.now() + timedelta(seconds=CLAIM_LEASE_SECONDS)
    held = PaymentEvent.objects.filter(
        pk=event.pk, claim_token=event.claim_token, status=EventStatus.PROCESSING
    ).update(next_attempt_at=lease)
    if held:
        event.next_attempt_at = lease
    return bool(held)


def _process(event: PaymentEvent) -> str:
    """Run the outstanding handlers and record the outcome, if still the owner."""
    done = set(event.processed_handlers or [])
    errors: list[str] = []
    for name, handler in handlers_for(event.event_type):
        if name in done:
            continue
        outcome = RetryOutcome(attempts=0, errors=[])
        try:
            run_with_retries(
                lambda: handler(event),
                policy=RetryPolicy(attempts=3, base_delay=0.01, max_delay=0.05, retryable=(TransientError,)),
                label=f"{event.event_type}/{name}",
                outcome=outcome,
            )
            done.add(name)
        except Exception as exc:  # noqa: BLE001 - recorded on the event row
            logger.exception("handler %s failed for %s", name, event.event_id)
            errors.append(f"{name}: {type(exc).__name__}: {exc}")

    attempts = event.attempts + 1
    changes: dict[str, Any] = {"processed_handlers": sorted(done), "attempts": attempts, "claim_token": ""}
    dead = False
    if errors:
        changes["last_error"] = "; ".join(errors)[:2000]
        if attempts >= MAX_ATTEMPTS:
            dead = True
            changes.update(status=EventStatus.DEAD, next_attempt_at=None)
        else:
            backoff = BACKOFF_SECONDS[min(attempts - 1, len(BACKOFF_SECONDS) - 1)]
            changes.update(status=EventStatus.FAILED, next_attempt_at=timezone.now() + timedelta(seconds=backoff))
    else:
        changes.update(status=EventStatus.PROCESSED, processed_at=timezone.now(), last_error="", next_attempt_at=None)

    # Fenced write: only the dispatcher whose token is still on the row may
    # record an outcome. One that outlived its lease finds a different token
    # (or none, if the new owner already finished) and changes nothing, so
    # the state and the attempt count belong to exactly one dispatcher. Its
    # handlers did run, which at-least-once delivery already allows for.
    if not PaymentEvent.objects.filter(pk=event.pk, claim_token=event.claim_token).update(**changes):
        metrics.increment("event_claims_lost_total")
        logger.warning("claim lost before the outcome was recorded", extra={"event_id": event.event_id})
        return OUTCOME_LOST
    for field, value in changes.items():
        setattr(event, field, value)

    if errors:
        if dead:
            metrics.increment("events_dead_lettered_total")
            logger.error("event dead-lettered", extra={"event_id": event.event_id, "error": event.last_error})
        return OUTCOME_FAILED
    metrics.increment("events_processed_total")
    return OUTCOME_PROCESSED


def process_event(event: PaymentEvent) -> bool:
    """Run every not-yet-completed handler for one event. Returns True when
    all handlers succeeded and this caller recorded the result."""
    return _process(event) == OUTCOME_PROCESSED


def _run(events: list[PaymentEvent]) -> dict[str, int]:
    processed = failed = skipped = 0
    with metrics.Timer() as timer:
        for event in events:
            if not _renew_claim(event):
                # Another dispatcher owns it now; running the handlers again
                # would only duplicate work whose result we could not record.
                metrics.increment("event_claims_lost_total")
                skipped += 1
                continue
            outcome = _process(event)
            if outcome == OUTCOME_PROCESSED:
                processed += 1
            elif outcome == OUTCOME_FAILED:
                failed += 1
            else:
                skipped += 1
    metrics.observe("event_dispatch_ms", timer.ms)
    return {"processed": processed, "failed": failed, "skipped": skipped}


def dispatch_events(pks: Iterable[int]) -> dict[str, int]:
    """Dispatch specific events (a request's own), if they are due and unclaimed."""
    return _run(_claim(pks, limit=100))


def dispatch_pending(limit: int = 100) -> dict[str, int]:
    """Process the due backlog: the maintenance tick and the worker command."""
    return _run(_claim(None, limit))


def replay_dead(event_id: str) -> PaymentEvent:
    """Operator action: give a dead-lettered event another MAX_ATTEMPTS."""
    # Conditional on the row still being dead, so two operators replaying the
    # same event cannot reset one that a dispatcher has already picked up.
    PaymentEvent.objects.filter(event_id=event_id, status=EventStatus.DEAD).update(
        status=EventStatus.PENDING, attempts=0, next_attempt_at=None, claim_token=""
    )
    return PaymentEvent.objects.get(event_id=event_id)


# ── Built-in handlers ────────────────────────────────────────────────────────
# Each keys its side effect on the event id so a replay is a no-op.

def _decision_key(event: PaymentEvent) -> str:
    return f"risk:evt:{event.event_id}"


@register(PAYMENT_RISK_EVALUATED, "analytics")
def _count_decision(event: PaymentEvent) -> None:
    from django.core.cache import cache

    # Idempotent via a per-event marker: a replay must not double count.
    if not cache.add(_decision_key(event) + ":analytics", 1, 60 * 60 * 24):
        return
    decision = event.payload.get("decision")
    if decision in ("allow", "review", "block"):
        metrics.increment(f"decisions_{decision}_total")


@register(PAYMENT_BLOCKED, "audit")
@register(PAYMENT_REVIEW_REQUIRED, "audit")
@register(PAYMENT_FAILED, "audit")
def _audit_outcome(event: PaymentEvent) -> None:
    from ..models import AuditLog
    from .audit import SERVICE_ACTOR, record

    # One outcome row per (event type, entity). The lookup is the cheap path
    # (and covers rows written before the dedupe key existed); it cannot be
    # the guarantee, because two dispatchers can both pass it before either
    # inserts. The unique dedupe key is what makes the second insert a no-op.
    if AuditLog.objects.filter(target_type=event.entity_type, target_id=event.entity_id, action=event.event_type).exists():
        return
    record(
        action=event.event_type,
        target_type=event.entity_type,
        target_id=event.entity_id,
        actor_label=SERVICE_ACTOR,
        reason=str(event.payload.get("summary", ""))[:500],
        decision=str(event.payload.get("decision", "")),
        model_version=str(event.payload.get("model_version", "")),
        request_id=event.request_id,
        metadata={"event_id": event.event_id, "risk_score": event.payload.get("risk_score")},
        dedupe_key=f"{event.event_type}:{event.entity_type}:{event.entity_id}",
    )


@register(PAYMENT_BLOCKED, "notify_customer")
@register(PAYMENT_REVIEW_REQUIRED, "notify_customer")
def _notify_customer(event: PaymentEvent) -> None:
    """In-app notification through the marketplace's existing Notification
    model; dedupe_key makes it idempotent."""
    from listings.models import Notification

    user_id = event.payload.get("user_id")
    if not user_id:
        return
    blocked = event.event_type == PAYMENT_BLOCKED
    Notification.objects.get_or_create(
        dedupe_key=f"risk:{event.event_id}",
        defaults={
            "user_id": user_id,
            "type": Notification.Type.SYSTEM,
            "title": "Payment declined" if blocked else "Payment held for review",
            "body": (
                "A sandbox payment was declined by risk checks."
                if blocked
                else "A sandbox payment is being reviewed before it completes."
            ),
            "link_path": f"/risk/transactions/{event.entity_id}",
            "priority": Notification.Priority.HIGH if blocked else Notification.Priority.NORMAL,
        },
    )
