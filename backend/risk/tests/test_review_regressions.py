"""Regressions for defects found in the staff-level review of the risk app."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from rest_framework.test import APITestCase

from risk.models import AgentPolicy, EventStatus, IdempotencyKey, PaymentEvent, PaymentTransaction, TransactionStatus
from risk.services import events
from risk.services.agents import evaluate_agent_transaction
from risk.services.payments import PaymentInput, create_payment, reconcile_uncaptured
from risk.services.simulation import run_scenario

from .support import make_user, payment_payload, sandbox

URL = "/api/risk/payments"


class LiveSignalTests(APITestCase):
    def setUp(self):
        cache.clear()
        sandbox().reset()
        self.user = make_user()
        self.client.force_authenticate(self.user)

    def test_new_device_fires_for_a_real_payment(self):
        for _ in range(4):
            self.assertEqual(self.client.post(URL, payment_payload(device_id="laptop-1"), format="json").status_code, 201)
        body = self.client.post(URL, payment_payload(device_id="new-phone"), format="json").json()
        self.assertIn("new_device", body["reasons"])
        snapshot = body["evaluation"]["context_snapshot"]
        self.assertEqual(snapshot["device_seen_count"], 0)
        self.assertTrue(snapshot["is_new_device"])
        # The payment being evaluated is not its own history.
        self.assertEqual(snapshot["historical_txn_count"], 4)
        self.assertEqual(snapshot["txn_count_1h"], 4)

    def test_known_device_needs_three_prior_uses(self):
        for _ in range(3):
            self.client.post(URL, payment_payload(device_id="laptop-1"), format="json")
        body = self.client.post(URL, payment_payload(device_id="laptop-1"), format="json").json()
        codes = {f["code"] for f in body["evaluation"]["factors"]}
        self.assertIn("known_device", codes)
        self.assertEqual(body["evaluation"]["context_snapshot"]["device_seen_count"], 3)


class IdempotencyAfterCommitTests(APITestCase):
    def setUp(self):
        cache.clear()
        sandbox().reset()
        self.user = make_user()
        self.client.force_authenticate(self.user)
        self.client.raise_request_exception = False

    def test_crash_after_commit_keeps_the_key_bound_to_the_payment(self):
        with mock.patch("risk.services.payments.capture", side_effect=RuntimeError("processor library crashed")):
            crashed = self.client.post(URL, payment_payload(), format="json", HTTP_IDEMPOTENCY_KEY="after-commit")
        self.assertEqual(crashed.status_code, 500)
        self.assertEqual(PaymentTransaction.objects.count(), 1)
        record = IdempotencyKey.objects.get(key="after-commit")
        self.assertEqual(record.status, "completed")
        self.assertEqual(record.response_status, 201)

        retry = self.client.post(URL, payment_payload(), format="json", HTTP_IDEMPOTENCY_KEY="after-commit")
        self.assertEqual(retry.status_code, 201)
        self.assertEqual(retry["Idempotent-Replayed"], "true")
        self.assertEqual(retry.json()["public_id"], PaymentTransaction.objects.get().public_id)
        self.assertEqual(PaymentTransaction.objects.count(), 1)

    def test_crash_before_commit_still_releases_the_key(self):
        with mock.patch("risk.services.payments.evaluate_transaction", side_effect=RuntimeError("engine crashed")):
            crashed = self.client.post(URL, payment_payload(), format="json", HTTP_IDEMPOTENCY_KEY="before-commit")
        self.assertEqual(crashed.status_code, 500)
        self.assertEqual(PaymentTransaction.objects.count(), 0)
        self.assertFalse(IdempotencyKey.objects.filter(key="before-commit").exists())
        self.assertEqual(self.client.post(URL, payment_payload(), format="json", HTTP_IDEMPOTENCY_KEY="before-commit").status_code, 201)


class ReconciliationTests(APITestCase):
    def setUp(self):
        cache.clear()
        sandbox().reset()
        self.user = make_user()

    def test_approved_but_uncaptured_payments_are_re_driven(self):
        with mock.patch("risk.services.payments.capture", side_effect=RuntimeError("died mid-capture")):
            with self.assertRaises(RuntimeError):
                create_payment(user=self.user, data=PaymentInput("mkt", Decimal("12"), "USD", "card", "d1"))
        txn = PaymentTransaction.objects.get()
        self.assertEqual(txn.status, TransactionStatus.APPROVED)
        self.assertEqual(reconcile_uncaptured()["completed"], 0)  # too recent
        PaymentTransaction.objects.filter(pk=txn.pk).update(updated_at=timezone.now() - timedelta(minutes=10))
        self.assertEqual(reconcile_uncaptured(), {"completed": 1, "failed": 0})
        txn.refresh_from_db()
        self.assertEqual(txn.status, TransactionStatus.COMPLETED)
        self.assertTrue(txn.processor_reference)
        self.assertEqual(reconcile_uncaptured(), {"completed": 0, "failed": 0})


class EventClaimTests(APITestCase):
    def setUp(self):
        cache.clear()

    def test_claim_is_exclusive_and_leases_expire(self):
        with transaction.atomic():
            event = events.emit(events.PAYMENT_COMPLETED, entity_type="payment", entity_id="p1", dispatch_after_commit=False)
        first = events._claim([event.pk], limit=10)
        self.assertEqual([e.pk for e in first], [event.pk])
        self.assertEqual(events._claim([event.pk], limit=10), [])  # already owned
        event.refresh_from_db()
        self.assertEqual(event.status, EventStatus.PROCESSING)
        self.assertTrue(event.claim_token)
        # A dead dispatcher: the lease runs out and the row is claimable again.
        PaymentEvent.objects.filter(pk=event.pk).update(next_attempt_at=timezone.now() - timedelta(seconds=1))
        second = events._claim(None, limit=10)
        self.assertEqual([e.pk for e in second], [event.pk])
        self.assertTrue(events.process_event(second[0]))
        event.refresh_from_db()
        self.assertEqual((event.status, event.claim_token), (EventStatus.PROCESSED, ""))

    def test_request_dispatches_only_its_own_events(self):
        with transaction.atomic():
            backlog = events.emit(events.PAYMENT_COMPLETED, entity_type="payment", entity_id="old", dispatch_after_commit=False)
        with self.captureOnCommitCallbacks(execute=True):
            with transaction.atomic():
                mine = events.emit(events.PAYMENT_COMPLETED, entity_type="payment", entity_id="mine")
        mine.refresh_from_db()
        backlog.refresh_from_db()
        self.assertEqual(mine.status, EventStatus.PROCESSED)
        self.assertEqual(backlog.status, EventStatus.PENDING)
        events.dispatch_pending()
        backlog.refresh_from_db()
        self.assertEqual(backlog.status, EventStatus.PROCESSED)


class AgentBudgetTests(APITestCase):
    def test_over_commit_detected_after_insert_is_blocked(self):
        user = make_user()
        agent = AgentPolicy.objects.create(user=user, name="A", daily_limit=Decimal("100"), transaction_limit=Decimal("80"), requires_approval_above=Decimal("100"))
        # Simulate a concurrent attempt that landed between the pre-check and the insert.
        original = evaluate_agent_transaction.__globals__["spent_today"]
        calls = {"n": 0}

        def racing_spent_today(policy, now=None):
            calls["n"] += 1
            return original(policy, now) + (Decimal("60") if calls["n"] == 2 else Decimal("0"))

        with mock.patch("risk.services.agents.spent_today", side_effect=racing_spent_today):
            _, verdict = evaluate_agent_transaction(agent=agent, amount=Decimal("60"), currency="USD", category="x", merchant_id="m")
        self.assertEqual(verdict.decision, "block")
        self.assertIn("daily_limit", [r["code"] for r in verdict.reasons])
        self.assertFalse(verdict.counts_toward_spend)
        self.assertEqual(original(agent), Decimal("0"))


class PrivacyAndGuardTests(APITestCase):
    def setUp(self):
        cache.clear()
        sandbox().reset()
        self.customer = make_user("held@example.com", days_old=0)
        self.staff = make_user("staff@example.com", staff=True)

    def test_reviewer_notes_are_not_shown_to_the_customer(self):
        self.client.force_authenticate(self.customer)
        held = self.client.post(URL, payment_payload(amount="1200.00"), format="json").json()
        self.assertEqual(held["decision"], "review")
        self.client.force_authenticate(self.staff)
        resolved = self.client.post(f"{URL}/{held['public_id']}/review", {"approve": False, "note": "Suspected stolen card, do not tell customer"}, format="json").json()
        self.assertEqual(resolved["manual_review"]["note"], "Suspected stolen card, do not tell customer")
        self.client.force_authenticate(self.customer)
        seen = self.client.get(f"{URL}/{held['public_id']}").json()
        self.assertEqual(seen["manual_review"], {"outcome": "rejected"})
        self.assertNotIn("stolen", str(seen))

    def test_simulated_rows_cannot_be_reviewed(self):
        run = run_scenario(user=self.staff, scenario="suspicious_amount", sensitivity=50, count=100, seed=2)
        row = PaymentTransaction.objects.filter(simulation_run=run, status=TransactionStatus.REVIEW).first()
        assert row is not None
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.post(f"{URL}/{row.public_id}/review", {"approve": True}, format="json").status_code, 404)

    def test_impossible_since_date_is_ignored(self):
        self.client.force_authenticate(self.customer)
        self.assertEqual(self.client.get(f"{URL}?since=2024-13-45T00:00:00").status_code, 200)

    def test_owner_sees_agent_events(self):
        self.client.force_authenticate(self.customer)
        agent = self.client.post("/api/risk/agents", {"name": "A", "daily_limit": "10", "transaction_limit": "5", "requires_approval_above": "5"}, format="json").json()
        self.client.post(f"/api/risk/agents/{agent['public_id']}/transactions", {"amount": "1", "category": "x", "merchant_id": "m"}, format="json")
        rows = self.client.get("/api/risk/events?event_type=agent.decision").json()
        self.assertEqual(rows["count"], 1)
