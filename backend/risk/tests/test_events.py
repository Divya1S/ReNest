"""Event log: emission, ordering, handlers, retries and the dead-letter state."""

from __future__ import annotations

from decimal import Decimal

from django.core.cache import cache
from django.db import transaction
from django.test import TestCase

from listings.models import Notification
from risk.models import AuditLog, EventStatus, PaymentEvent
from risk.services import events, metrics
from risk.services.payments import PaymentInput, create_payment
from risk.services.retry import TransientError

from .support import make_user


class EmissionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user()

    def test_payment_lifecycle_emits_ordered_events(self):
        with self.captureOnCommitCallbacks(execute=True):
            result = create_payment(user=self.user, data=PaymentInput("mkt", Decimal("20"), "USD", "card", "d1"), request_id="req-1")
        rows = list(PaymentEvent.objects.filter(entity_id=result.transaction.public_id).order_by("sequence"))
        self.assertEqual(
            [r.event_type for r in rows],
            ["payment.created", "payment.risk_evaluated", "payment.approved", "payment.completed"],
        )
        for row in rows:
            self.assertTrue(row.event_id.startswith("evt_"))
            self.assertEqual(row.schema_version, 1)
            self.assertEqual(row.request_id, "req-1")
            self.assertEqual(row.status, EventStatus.PROCESSED)
        self.assertEqual(rows[1].payload["decision"], "allow")
        self.assertEqual(rows[1].payload["model_version"], "risk-rules-v1")

    def test_unknown_event_type_is_rejected(self):
        with self.assertRaises(ValueError):
            events.emit("payment.teleported", entity_type="payment", entity_id="x")

    def test_rolled_back_transaction_leaves_no_event(self):
        try:
            with transaction.atomic():
                events.emit(events.PAYMENT_CREATED, entity_type="payment", entity_id="ghost")
                raise RuntimeError("abort")
        except RuntimeError:
            pass
        self.assertFalse(PaymentEvent.objects.filter(entity_id="ghost").exists())

    def test_blocked_payment_notifies_customer_once(self):
        make_user_txn = PaymentInput("mkt", Decimal("2500"), "USD", "card", "brand-new")
        fresh = make_user("fresh@example.com", days_old=0)
        result = create_payment(user=fresh, data=make_user_txn)
        self.assertEqual(result.decision.decision, "block")
        events.dispatch_pending()
        events.dispatch_pending()
        notes = Notification.objects.filter(user=fresh, dedupe_key__startswith="risk:")
        self.assertEqual(notes.count(), 1)
        self.assertEqual(notes.get().link_path, f"/risk/transactions/{result.transaction.public_id}")
        service_rows = AuditLog.objects.filter(action="payment.blocked", target_id=result.transaction.public_id)
        self.assertEqual(service_rows.count(), 1)
        self.assertEqual(service_rows.get().actor_label, "service:risk-engine")


class DispatchTests(TestCase):
    def setUp(self):
        cache.clear()
        metrics.reset_all()

    def _with_handler(self, event_type, name, fn):
        events._HANDLERS.setdefault(event_type, []).append((name, fn))
        self.addCleanup(lambda: events._HANDLERS[event_type].remove((name, fn)))

    def test_failing_handler_retries_with_backoff_then_dead_letters(self):
        calls = []

        def broken(event):
            calls.append(event.event_id)
            raise RuntimeError("downstream is down")

        self._with_handler(events.PAYMENT_COMPLETED, "broken", broken)
        with transaction.atomic():
            event = events.emit(events.PAYMENT_COMPLETED, entity_type="payment", entity_id="p1", dispatch_after_commit=False)

        for attempt in range(1, events.MAX_ATTEMPTS + 1):
            event.next_attempt_at = None
            event.save(update_fields=["next_attempt_at"])
            events.dispatch_pending()
            event.refresh_from_db()
            self.assertEqual(event.attempts, attempt)
            if attempt < events.MAX_ATTEMPTS:
                self.assertEqual(event.status, EventStatus.FAILED)
                self.assertIsNotNone(event.next_attempt_at)
                self.assertIn("downstream is down", event.last_error)
            else:
                self.assertEqual(event.status, EventStatus.DEAD)
                self.assertIsNone(event.next_attempt_at)
        self.assertEqual(metrics.counter("events_dead_lettered_total"), 1)

        # A dead event is not picked up again...
        before = len(calls)
        events.dispatch_pending()
        self.assertEqual(len(calls), before)
        # ...until an operator replays it.
        events.replay_dead(event.event_id)
        events.dispatch_pending()
        self.assertEqual(len(calls), before + 1)

    def test_not_due_events_wait_for_backoff(self):
        def broken(event):
            raise RuntimeError("nope")

        self._with_handler(events.PAYMENT_COMPLETED, "broken2", broken)
        with transaction.atomic():
            event = events.emit(events.PAYMENT_COMPLETED, entity_type="payment", entity_id="p2", dispatch_after_commit=False)
        events.dispatch_pending()
        event.refresh_from_db()
        self.assertEqual(event.attempts, 1)
        events.dispatch_pending()  # next_attempt_at is in the future
        event.refresh_from_db()
        self.assertEqual(event.attempts, 1)

    def test_transient_handler_errors_are_retried_inline(self):
        state = {"calls": 0}

        def flaky(event):
            state["calls"] += 1
            if state["calls"] == 1:
                raise TransientError("try again")

        self._with_handler(events.PAYMENT_COMPLETED, "flaky", flaky)
        with transaction.atomic():
            event = events.emit(events.PAYMENT_COMPLETED, entity_type="payment", entity_id="p3", dispatch_after_commit=False)
        events.dispatch_pending()
        event.refresh_from_db()
        self.assertEqual(event.status, EventStatus.PROCESSED)
        self.assertEqual(state["calls"], 2)
        self.assertIn("flaky", event.processed_handlers)

    def test_successful_handlers_are_not_rerun_when_a_sibling_fails(self):
        seen = []

        def ok(event):
            seen.append("ok")

        def bad(event):
            raise RuntimeError("bad")

        self._with_handler(events.PAYMENT_COMPLETED, "ok", ok)
        self._with_handler(events.PAYMENT_COMPLETED, "bad", bad)
        with transaction.atomic():
            event = events.emit(events.PAYMENT_COMPLETED, entity_type="payment", entity_id="p4", dispatch_after_commit=False)
        events.dispatch_pending()
        event.refresh_from_db()
        event.next_attempt_at = None
        event.save(update_fields=["next_attempt_at"])
        events.dispatch_pending()
        self.assertEqual(seen, ["ok"])

    def test_analytics_handler_is_idempotent_per_event(self):
        with transaction.atomic():
            event = events.emit(events.PAYMENT_RISK_EVALUATED, entity_type="payment", entity_id="p5", payload={"decision": "block"}, dispatch_after_commit=False)
        events.process_event(event)
        events.process_event(event)  # simulated redelivery
        self.assertEqual(metrics.counter("decisions_block_total"), 1)
