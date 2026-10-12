"""Regressions from the production-hardening audit of the payment pipeline:
idempotency durability, request fingerprints, customer privacy, event
fencing, client address, counters, the policy admin and request ids.

Tests that depend on what happens after a real COMMIT use the transaction
test cases, where on_commit hooks actually run. The classes marked
PostgreSQL-only use two threads on two connections, because row locks and
unique constraints only arbitrate between real sessions.
"""

from __future__ import annotations

import importlib.util
import sys
import threading
import time
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Callable
from unittest import mock, skipUnless

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import OperationalError, connection, connections, transaction
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase, APITransactionTestCase

from risk.engine.signals import coarse_ip_prefix
from risk.models import (
    AgentPolicy,
    AgentTransaction,
    AuditLog,
    AuditLogQuerySet,
    EventStatus,
    IdempotencyKey,
    IdempotencyStatus,
    PaymentEvent,
    PaymentTransaction,
    TransactionStatus,
)
from risk.services import events, idempotency, metrics
from risk.services import payments as payment_service
from risk.services.agents import evaluate_agent_transaction, spent_today
from risk.services.payments import PaymentInput, create_payment, reconcile_uncaptured
from risk.services.policy import current_sensitivity, get_active_policy
from risk.services.simulation import run_scenario
from risk.views import payments as payment_views

from .support import make_user, payment_payload, sandbox

URL = "/api/risk/payments"
IS_POSTGRES = connection.vendor == "postgresql"


def ordinary_input(**overrides: Any) -> PaymentInput:
    fields: dict[str, Any] = dict(merchant_id="mkt_textbooks", amount=Decimal("42.50"), currency="USD", payment_method="card", device_id="laptop-1")
    fields.update(overrides)
    return PaymentInput(**fields)


def run_together(*calls: Callable[[], Any]) -> list[Any]:
    """Run each callable on its own thread (so its own database connection),
    released at the same instant. Re-raises the first error."""
    barrier = threading.Barrier(len(calls))
    results: list[Any] = [None] * len(calls)
    errors: list[BaseException] = []

    def runner(index: int, call: Callable[[], Any]) -> None:
        try:
            barrier.wait(timeout=10)
            results[index] = call()
        except BaseException as exc:  # noqa: BLE001 - reported to the test thread
            errors.append(exc)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=runner, args=(i, call)) for i, call in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    if errors:
        raise errors[0]
    return results


# ── R1: the key and the payment are durable together ─────────────────────────

class IdempotencyDurabilityTests(APITransactionTestCase):
    """Real commits: the post-commit hooks run exactly as they do in production."""

    def setUp(self):
        cache.clear()
        metrics.reset_all()
        sandbox().reset()
        self.user = make_user()
        self.client.force_authenticate(self.user)
        self.client.raise_request_exception = False

    def post(self, key: str, **overrides: Any) -> Any:
        return self.client.post(URL, payment_payload(**overrides), format="json", HTTP_IDEMPOTENCY_KEY=key)

    def test_dispatch_failure_right_after_commit_cannot_cause_a_second_payment(self):
        # The dispatch infrastructure breaks in the post-commit hook, i.e.
        # after the payment is durable and before the response is stored.
        with mock.patch("risk.services.events._claim", side_effect=OperationalError("database is locked")):
            first = self.post("dispatch-down")
        retry = self.post("dispatch-down")

        self.assertEqual(PaymentTransaction.objects.count(), 1, "the retry created a second payment")
        payment = PaymentTransaction.objects.get()
        record = IdempotencyKey.objects.get(key="dispatch-down")
        self.assertEqual(record.transaction_id, payment.pk)
        # The payment succeeded, so the request does too: a dispatch problem
        # is the dispatcher's to retry, not the customer's.
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(first.json()["public_id"], payment.public_id)
        self.assertEqual(retry.status_code, 201)
        self.assertEqual(retry["Idempotent-Replayed"], "true")
        self.assertEqual(retry.json()["public_id"], payment.public_id)
        self.assertGreaterEqual(metrics.counter("event_dispatch_errors_total"), 1)
        # Nothing was lost: the events are still there for the next pass.
        pending = PaymentEvent.objects.filter(entity_id=payment.public_id, status=EventStatus.PENDING)
        self.assertTrue(pending.exists())
        events.dispatch_pending()
        self.assertFalse(PaymentEvent.objects.filter(entity_id=payment.public_id).exclude(status=EventStatus.PROCESSED).exists())

    def test_a_raising_post_commit_hook_never_reaches_the_caller(self):
        with mock.patch("risk.services.events.dispatch_events", side_effect=RuntimeError("dispatcher exploded")):
            result = create_payment(user=self.user, data=ordinary_input())
        self.assertEqual(result.transaction.status, TransactionStatus.COMPLETED)
        self.assertEqual(PaymentTransaction.objects.count(), 1)

    def test_crash_after_commit_replays_the_current_state_not_a_provisional_one(self):
        with mock.patch("risk.services.payments.capture", side_effect=RuntimeError("process died mid-capture")):
            crashed = self.post("died-in-capture")
        self.assertEqual(crashed.status_code, 500)
        payment = PaymentTransaction.objects.get()
        record = IdempotencyKey.objects.get(key="died-in-capture")
        self.assertEqual(record.transaction_id, payment.pk)

        # Whatever tries to release it, a key with a payment stays.
        idempotency.release(record)
        self.assertTrue(IdempotencyKey.objects.filter(pk=record.pk).exists())

        replay = self.post("died-in-capture")
        self.assertEqual((replay.status_code, replay["Idempotent-Replayed"]), (201, "true"))
        self.assertEqual(replay.json()["public_id"], payment.public_id)
        self.assertEqual(replay.json()["status"], "approved")

        # The reconciler finishes the capture; the same key now says so.
        PaymentTransaction.objects.filter(pk=payment.pk).update(updated_at=timezone.now() - timedelta(minutes=10))
        self.assertEqual(reconcile_uncaptured(), {"completed": 1, "failed": 0})
        final = self.post("died-in-capture")
        self.assertEqual((final.status_code, final["Idempotent-Replayed"]), (201, "true"))
        self.assertEqual(final.json()["status"], "completed")
        self.assertTrue(final.json()["processor_reference"])
        self.assertEqual(PaymentTransaction.objects.count(), 1)
        # A settled answer is stored and from now on replayed verbatim.
        record.refresh_from_db()
        self.assertEqual(record.status, IdempotencyStatus.COMPLETED)
        self.assertEqual((record.response_body or {})["status"], "completed")

    def test_bound_but_unanswered_key_replays_with_header_and_current_status(self):
        # The original committed and captured, then died before storing its response.
        record = idempotency.acquire(user=self.user, scope=idempotency.SCOPE_PAYMENTS, key="unanswered", payload=self._fingerprint())
        assert isinstance(record, IdempotencyKey)
        created = create_payment(user=self.user, data=ordinary_input(merchant_category="books"), idempotency_key=record).transaction
        record.refresh_from_db()
        self.assertEqual((record.status, record.transaction_id), (IdempotencyStatus.IN_PROGRESS, created.pk))

        replay = self.post("unanswered")
        self.assertEqual(replay.status_code, 201)
        self.assertEqual(replay["Idempotent-Replayed"], "true")
        self.assertEqual(replay.json()["public_id"], created.public_id)
        self.assertEqual(replay.json()["status"], "completed")
        self.assertEqual(PaymentTransaction.objects.count(), 1)

    def test_unbound_key_blocks_only_until_its_lease_runs_out(self):
        # A request acquired the key and was killed before committing anything.
        record = idempotency.acquire(user=self.user, scope=idempotency.SCOPE_PAYMENTS, key="orphan", payload=self._fingerprint())
        assert isinstance(record, IdempotencyKey)

        waiting = self.post("orphan")
        self.assertEqual(waiting.status_code, 409)
        self.assertEqual(waiting.json()["code"], "idempotency_in_progress")
        self.assertEqual(waiting["Retry-After"], "1")

        stale = timezone.now() - timedelta(seconds=idempotency.IN_PROGRESS_LEASE_SECONDS + 1)
        IdempotencyKey.objects.filter(pk=record.pk).update(locked_at=stale)
        retry = self.post("orphan")
        self.assertEqual(retry.status_code, 201, retry.content)
        self.assertNotIn("Idempotent-Replayed", retry)
        self.assertEqual(PaymentTransaction.objects.count(), 1)
        self.assertGreaterEqual(metrics.counter("idempotency_takeovers_total"), 1)
        record.refresh_from_db()
        self.assertEqual(record.status, IdempotencyStatus.COMPLETED)
        self.assertEqual(record.transaction_id, PaymentTransaction.objects.get().pk)
        again = self.post("orphan")
        self.assertEqual(again["Idempotent-Replayed"], "true")
        self.assertEqual(PaymentTransaction.objects.count(), 1)

    def test_a_taken_over_original_cannot_bind_and_its_payment_rolls_back(self):
        original = idempotency.acquire(user=self.user, scope=idempotency.SCOPE_PAYMENTS, key="fenced", payload=self._fingerprint())
        assert isinstance(original, IdempotencyKey)
        stale = timezone.now() - timedelta(seconds=idempotency.IN_PROGRESS_LEASE_SECONDS + 1)
        IdempotencyKey.objects.filter(pk=original.pk).update(locked_at=stale)
        successor = idempotency.acquire(user=self.user, scope=idempotency.SCOPE_PAYMENTS, key="fenced", payload=self._fingerprint())
        assert isinstance(successor, IdempotencyKey)
        self.assertNotEqual(successor.owner_token, original.owner_token)

        # The original wakes up and tries to finish.
        with self.assertRaises(idempotency.OwnershipLost):
            create_payment(user=self.user, data=ordinary_input(), idempotency_key=original)
        self.assertEqual(PaymentTransaction.objects.count(), 0)
        self.assertEqual(PaymentEvent.objects.count(), 0)
        self.assertEqual(AuditLog.objects.count(), 0)
        # Its token is void for every other write as well.
        self.assertFalse(idempotency.complete(original, status_code=201, body={"public_id": "txn_stale"}))
        idempotency.release(original)
        live = IdempotencyKey.objects.get(pk=successor.pk)
        self.assertEqual((live.status, live.owner_token, live.transaction_id), (IdempotencyStatus.IN_PROGRESS, successor.owner_token, None))

        created = create_payment(user=self.user, data=ordinary_input(), idempotency_key=successor).transaction
        self.assertEqual(PaymentTransaction.objects.count(), 1)
        live.refresh_from_db()
        self.assertEqual(live.transaction_id, created.pk)

    def test_request_that_loses_ownership_mid_flight_gets_409_and_creates_nothing(self):
        real = payment_views.create_payment

        def taken_over_just_before_the_bind(**kwargs: Any) -> Any:
            # Another request takes the key over while this one is still validating.
            IdempotencyKey.objects.filter(pk=kwargs["idempotency_key"].pk).update(owner_token="someone-else")
            return real(**kwargs)

        with mock.patch("risk.views.payments.create_payment", side_effect=taken_over_just_before_the_bind):
            response = self.post("lost-mid-flight")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "idempotency_in_progress")
        self.assertEqual(response["Retry-After"], "1")
        self.assertEqual(PaymentTransaction.objects.count(), 0)
        # And it did not delete the key out from under the new owner.
        self.assertEqual(IdempotencyKey.objects.get(key="lost-mid-flight").owner_token, "someone-else")

    def test_bind_refuses_to_run_outside_the_payment_transaction(self):
        record = idempotency.acquire(user=self.user, scope=idempotency.SCOPE_PAYMENTS, key="unsafe-bind", payload=self._fingerprint())
        assert isinstance(record, IdempotencyKey)
        payment = create_payment(user=self.user, data=ordinary_input()).transaction
        with self.assertRaises(RuntimeError):
            idempotency.bind(record, payment)
        with transaction.atomic():
            idempotency.bind(record, payment)
        self.assertEqual(IdempotencyKey.objects.get(pk=record.pk).transaction_id, payment.pk)

    def _fingerprint(self) -> dict[str, Any]:
        from .support import fingerprint

        return fingerprint()


@skipUnless(IS_POSTGRES, "needs two real sessions: row locks and unique constraints arbitrate the race")
class ConcurrentIdempotencyTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        sandbox().reset()
        self.user = make_user()

    def _post(self, key: str) -> Callable[[], tuple[int, dict[str, Any], str | None]]:
        def call() -> tuple[int, dict[str, Any], str | None]:
            client = APIClient()
            client.force_authenticate(self.user)
            response = client.post(URL, payment_payload(), format="json", HTTP_IDEMPOTENCY_KEY=key)
            return response.status_code, response.json(), response.get("Idempotent-Replayed")

        return call

    def test_two_simultaneous_first_requests_create_exactly_one_payment(self):
        real = payment_service.evaluate_transaction

        def slow_evaluation(*args: Any, **kwargs: Any) -> Any:
            # Keep the winner's transaction open long enough for the other
            # request to arrive while the key is in flight.
            time.sleep(0.2)
            return real(*args, **kwargs)

        with mock.patch("risk.services.payments.evaluate_transaction", side_effect=slow_evaluation):
            for round_number in range(4):
                key = f"race-{round_number}"
                outcomes = run_together(self._post(key), self._post(key))

                payments = PaymentTransaction.objects.filter(user=self.user, request_id__isnull=False)
                self.assertEqual(payments.count(), round_number + 1, f"{key}: {outcomes}")
                record = IdempotencyKey.objects.get(key=key)
                assert record.transaction is not None
                winner_id = record.transaction.public_id

                created = [o for o in outcomes if o[0] == 201 and o[2] is None]
                self.assertEqual(len(created), 1, f"{key}: exactly one request creates the payment: {outcomes}")
                for status_code, body, replayed in outcomes:
                    if status_code == 201:
                        self.assertEqual(body["public_id"], winner_id)
                    else:
                        # The loser either waits (409) or is answered from the committed payment (201 replay).
                        self.assertEqual((status_code, body["code"], replayed), (409, "idempotency_in_progress", None))

                status_code, body, replayed = self._post(key)()
                self.assertEqual((status_code, body["public_id"], replayed), (201, winner_id, "true"))
        self.assertEqual(PaymentTransaction.objects.count(), 4)


@skipUnless(IS_POSTGRES, "needs two real sessions: the agent row lock is what serialises the budget")
class ConcurrentAgentBudgetTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user()
        self.agent = AgentPolicy.objects.create(
            user=self.user, name="Budget", daily_limit=Decimal("100"), transaction_limit=Decimal("80"), requires_approval_above=Decimal("100"),
        )

    def test_two_simultaneous_attempts_cannot_both_fit_under_the_daily_limit(self):
        from risk.services import agents as agent_service

        real = agent_service._risk_context

        def slow_context(*args: Any, **kwargs: Any) -> Any:
            time.sleep(0.2)  # hold the agent lock so the other attempt really waits on it
            return real(*args, **kwargs)

        def attempt() -> str:
            _, verdict = evaluate_agent_transaction(agent=self.agent, amount=Decimal("60"), currency="USD", category="supplies", merchant_id="mkt_supplies", actor=self.user)
            return verdict.decision

        with mock.patch("risk.services.agents._risk_context", side_effect=slow_context):
            decisions = run_together(attempt, attempt)

        self.assertEqual(sorted(decisions), ["allow", "block"])
        self.assertEqual(spent_today(self.agent), Decimal("60"))
        rows = AgentTransaction.objects.filter(agent=self.agent)
        self.assertEqual(rows.filter(counts_toward_spend=True).count(), 1)
        blocked = rows.get(decision="block")
        self.assertIn("daily_limit", [r["code"] for r in blocked.reasons])

    def test_two_simultaneous_resolutions_of_one_hold_cannot_both_win(self):
        from risk.services.agents import AttemptNotHeld, resolve_agent_transaction

        self.agent.requires_approval_above = Decimal("10")
        self.agent.save()
        held, verdict = evaluate_agent_transaction(agent=self.agent, amount=Decimal("60"), currency="USD", category="supplies", merchant_id="mkt_supplies", actor=self.user)
        self.assertEqual(verdict.decision, "review")

        def resolve(approve: bool) -> Callable[[], str]:
            def call() -> str:
                try:
                    return resolve_agent_transaction(agent=self.agent, attempt_id=held.pk, approve=approve, actor=self.user).decision
                except AttemptNotHeld:
                    return "conflict"

            return call

        outcomes = run_together(resolve(True), resolve(False))
        self.assertEqual(outcomes.count("conflict"), 1, outcomes)
        held.refresh_from_db()
        winner = next(o for o in outcomes if o != "conflict")
        self.assertEqual(held.decision, winner)
        self.assertEqual(held.counts_toward_spend, winner == "allow")
        self.assertEqual(AuditLog.objects.filter(action="agent.transaction_resolved").count(), 1)


# ── R2: the key is compared on the validated request ─────────────────────────

class RequestFingerprintTests(APITestCase):
    def setUp(self):
        cache.clear()
        sandbox().reset()
        self.user = make_user()
        self.client.force_authenticate(self.user)

    def post(self, key: str, **overrides: Any) -> Any:
        return self.client.post(URL, payment_payload(**overrides), format="json", HTTP_IDEMPOTENCY_KEY=key)

    def assert_same_request(self, key: str, first: dict[str, Any], second: dict[str, Any]) -> None:
        original = self.post(key, **first)
        self.assertEqual(original.status_code, 201, original.content)
        again = self.post(key, **second)
        self.assertEqual(again.status_code, 201, again.content)
        self.assertEqual(again["Idempotent-Replayed"], "true")
        self.assertEqual(again.json()["public_id"], original.json()["public_id"])

    def test_equivalent_spellings_of_one_payment_share_a_key(self):
        self.assert_same_request("amount", {"amount": "42.50"}, {"amount": "42.5"})
        self.assert_same_request("number", {"amount": "19.00"}, {"amount": 19})
        self.assert_same_request("whitespace", {"merchant_id": "mkt_textbooks"}, {"merchant_id": "  mkt_textbooks "})
        self.assert_same_request("case", {"country": "us", "merchant_category": "Books"}, {"country": "US", "merchant_category": "books"})
        self.assert_same_request("zero-decimal", {"currency": "JPY", "amount": "100"}, {"currency": "JPY", "amount": "100.00"})
        self.assert_same_request("defaults", {}, {"sandbox_behavior": "succeed", "metadata": {}, "country": ""})
        self.assertEqual(PaymentTransaction.objects.count(), 6)

    def test_a_genuinely_different_payment_is_still_rejected(self):
        self.assertEqual(self.post("order", amount="42.50").status_code, 201)
        for changed in ({"amount": "42.51"}, {"currency": "EUR"}, {"merchant_id": "mkt_other"}, {"device_id": "phone"}, {"metadata": {"gift": True}}, {"sandbox_behavior": "decline"}):
            conflict = self.post("order", **changed)
            self.assertEqual(conflict.status_code, 422, changed)
            self.assertEqual(conflict.json()["code"], "idempotency_key_reused")
        self.assertEqual(PaymentTransaction.objects.count(), 1)

    def test_invalid_bodies_still_replay_and_do_not_collide_with_valid_ones(self):
        bad = self.post("bad", amount="0")
        self.assertEqual(bad.status_code, 400)
        again = self.post("bad", amount="0")
        self.assertEqual((again.status_code, again["Idempotent-Replayed"]), (400, "true"))
        self.assertEqual(again.json(), bad.json())
        # Fixing the body under the same key is a different request.
        self.assertEqual(self.post("bad", amount="10.00").status_code, 422)
        self.assertEqual(PaymentTransaction.objects.count(), 0)

    def test_a_key_stored_before_fingerprints_still_matches_its_own_retry(self):
        # What the previous release stored: the hash of the raw body.
        IdempotencyKey.objects.create(
            user=self.user, scope=idempotency.SCOPE_PAYMENTS, key="pre-upgrade",
            request_hash=idempotency.request_hash(payment_payload()), status=IdempotencyStatus.COMPLETED,
            response_status=201, response_body={"public_id": "txn_before_upgrade"}, expires_at=timezone.now() + timedelta(hours=1),
        )
        replay = self.post("pre-upgrade")
        self.assertEqual((replay.status_code, replay["Idempotent-Replayed"]), (201, "true"))
        self.assertEqual(replay.json()["public_id"], "txn_before_upgrade")
        self.assertEqual(self.post("pre-upgrade", amount="99.00").status_code, 422)
        self.assertEqual(PaymentTransaction.objects.count(), 0)

    def test_fingerprint_forms_are_tagged(self):
        raw = idempotency.payment_request_fingerprint(validated=None, raw=payment_payload())
        self.assertEqual(raw["form"], "raw")
        from .support import fingerprint

        self.assertEqual(fingerprint()["form"], "validated")
        self.assertEqual(fingerprint(amount="42.5"), fingerprint(amount="42.50"))
        self.assertEqual(fingerprint(currency="JPY", amount="100.00")["amount"], "100")


# ── R3: one redaction policy for customers ───────────────────────────────────

class CustomerPrivacyTests(APITestCase):
    NOTE = "Suspected stolen card, do not tell customer"

    def setUp(self):
        cache.clear()
        sandbox().reset()
        self.customer = make_user("held@example.com", days_old=0)
        self.staff = make_user("staff@example.com", staff=True)
        self.client.force_authenticate(self.customer)
        held = self.client.post(URL, payment_payload(amount="1200.00"), format="json").json()
        self.assertEqual(held["decision"], "review")
        self.public_id = held["public_id"]
        self.client.force_authenticate(self.staff)
        response = self.client.post(f"{URL}/{self.public_id}/review", {"approve": False, "note": self.NOTE}, format="json")
        self.assertEqual(response.status_code, 200, response.content)

    def assert_nothing_private(self, payload: Any) -> None:
        text = str(payload)
        self.assertNotIn("stolen", text)
        self.assertNotIn(f"user:{self.staff.pk}", text)
        self.assertNotIn("reviewer_id", text)

    def test_customer_never_sees_the_reviewer_or_the_note(self):
        self.client.force_authenticate(self.customer)

        detail = self.client.get(f"{URL}/{self.public_id}").json()
        self.assert_nothing_private(detail)
        review = next(row for row in detail["audit"] if row["action"] == "payment.manual_review")
        self.assertEqual((review["actor_label"], review["reason"], review["metadata"]), ("staff", "Manual review: rejected", {}))
        reviewed = next(e for e in detail["events"] if e["event_type"] == "payment.reviewed")
        self.assertEqual(reviewed["payload"]["outcome"], "rejected")
        # Their own rows are untouched.
        self.assertIn(f"user:{self.customer.pk}", [row["actor_label"] for row in detail["audit"]])

        audit = self.client.get("/api/risk/audit").json()
        self.assertGreaterEqual(audit["count"], 2)
        self.assert_nothing_private(audit)
        listed = next(row for row in audit["results"] if row["action"] == "payment.manual_review")
        self.assertEqual((listed["actor_label"], listed["reason"], listed["metadata"]), ("staff", "Manual review: rejected", {}))
        self.assert_nothing_private(self.client.get(f"/api/risk/audit?target_id={self.public_id}").json())

        event_list = self.client.get("/api/risk/events").json()
        self.assertIn("payment.reviewed", [e["event_type"] for e in event_list["results"]])
        self.assert_nothing_private(event_list)

    def test_staff_still_see_everything(self):
        self.client.force_authenticate(self.staff)
        detail = self.client.get(f"{URL}/{self.public_id}").json()
        review = next(row for row in detail["audit"] if row["action"] == "payment.manual_review")
        self.assertEqual((review["actor_label"], review["reason"]), (f"user:{self.staff.pk}", self.NOTE))
        listed = self.client.get("/api/risk/audit?action=payment.manual_review").json()["results"][0]
        self.assertEqual((listed["actor_label"], listed["reason"], listed["metadata"]), (f"user:{self.staff.pk}", self.NOTE, {"outcome": "rejected"}))

    def test_reviewer_identity_is_not_written_to_the_event_at_all(self):
        stored = PaymentEvent.objects.get(event_type="payment.reviewed", entity_id=self.public_id)
        self.assertNotIn("reviewer_id", stored.payload)
        self.assertNotIn(self.staff.pk, stored.payload.values())

    def test_rows_written_before_the_fix_are_redacted_on_read(self):
        with transaction.atomic():
            legacy = events.emit(
                events.PAYMENT_REVIEWED, entity_type="payment", entity_id=self.public_id,
                payload={"user_id": self.customer.pk, "outcome": "rejected", "reviewer_id": self.staff.pk, "note": self.NOTE},
                dispatch_after_commit=False,
            )
        PaymentEvent.objects.filter(pk=legacy.pk).update(status=EventStatus.FAILED, last_error='notify_customer: IntegrityError: duplicate key value "secret-internal"')
        self.client.force_authenticate(self.customer)
        for body in (self.client.get(f"{URL}/{self.public_id}").json()["events"], self.client.get("/api/risk/events").json()["results"]):
            row = next(e for e in body if e["event_id"] == legacy.event_id)
            self.assertEqual(row["payload"], {"user_id": self.customer.pk, "outcome": "rejected"})
            self.assertNotIn("secret-internal", row["last_error"])
            self.assertTrue(row["last_error"])  # the customer can still tell it failed
        self.client.force_authenticate(self.staff)
        row = next(e for e in self.client.get("/api/risk/events").json()["results"] if e["event_id"] == legacy.event_id)
        self.assertEqual(row["payload"]["reviewer_id"], self.staff.pk)
        self.assertIn("secret-internal", row["last_error"])

    def test_serializers_redact_when_no_request_is_supplied(self):
        from risk.serializers import AuditLogSerializer, PaymentEventSerializer

        review = AuditLog.objects.get(action="payment.manual_review")
        self.assertEqual(AuditLogSerializer(review).data["actor_label"], "staff")
        self.assertNotIn("stolen", str(AuditLogSerializer(review).data))
        with transaction.atomic():
            event = events.emit(events.PAYMENT_REVIEWED, entity_type="payment", entity_id="p", payload={"reviewer_id": 9}, dispatch_after_commit=False)
        self.assertEqual(PaymentEventSerializer(event).data["payload"], {})


# ── R7: event outcome writes are fenced; the audit handler is race-safe ──────

class EventFencingTests(TestCase):
    def setUp(self):
        cache.clear()
        metrics.reset_all()

    def _with_handler(self, event_type: str, name: str, fn: Callable[[PaymentEvent], None]) -> None:
        events._HANDLERS.setdefault(event_type, []).append((name, fn))
        self.addCleanup(lambda: events._HANDLERS[event_type].remove((name, fn)))

    def _claimed_twice(self) -> tuple[PaymentEvent, PaymentEvent]:
        """One event as seen by a dispatcher whose lease ran out and by the one that took over."""
        with transaction.atomic():
            event = events.emit(events.PAYMENT_COMPLETED, entity_type="payment", entity_id="p-fenced", dispatch_after_commit=False)
        stale = events._claim([event.pk], limit=10)[0]
        PaymentEvent.objects.filter(pk=event.pk).update(next_attempt_at=timezone.now() - timedelta(seconds=1))
        current = events._claim(None, limit=10)[0]
        self.assertNotEqual(stale.claim_token, current.claim_token)
        return stale, current

    def test_a_dispatcher_that_outlived_its_lease_cannot_overwrite_the_outcome(self):
        stale, current = self._claimed_twice()

        def fails_only_for_the_stale_dispatcher(event: PaymentEvent) -> None:
            if event.claim_token == stale.claim_token:
                raise RuntimeError("stale dispatcher saw a failure")

        self._with_handler(events.PAYMENT_COMPLETED, "picky", fails_only_for_the_stale_dispatcher)

        self.assertTrue(events.process_event(current))
        self.assertFalse(events.process_event(stale))

        row = PaymentEvent.objects.get(pk=current.pk)
        self.assertEqual((row.status, row.attempts, row.last_error, row.claim_token), (EventStatus.PROCESSED, 1, "", ""))
        self.assertIsNone(row.next_attempt_at)
        self.assertEqual(row.processed_handlers, ["picky"])
        self.assertEqual(metrics.counter("event_claims_lost_total"), 1)
        self.assertEqual(metrics.counter("events_processed_total"), 1)

    def test_stale_dispatcher_finishing_first_does_not_block_the_owner(self):
        stale, current = self._claimed_twice()
        self.assertFalse(events.process_event(stale))  # token already replaced
        row = PaymentEvent.objects.get(pk=current.pk)
        self.assertEqual((row.status, row.attempts, row.claim_token), (EventStatus.PROCESSING, 0, current.claim_token))
        self.assertTrue(events.process_event(current))
        row.refresh_from_db()
        self.assertEqual((row.status, row.attempts), (EventStatus.PROCESSED, 1))

    def test_a_batch_skips_events_whose_claim_was_lost_without_running_handlers(self):
        calls: list[str] = []
        self._with_handler(events.PAYMENT_COMPLETED, "recorder", lambda event: calls.append(event.claim_token))
        stale, current = self._claimed_twice()
        owner_token = current.claim_token
        self.assertEqual(events._run([stale]), {"processed": 0, "failed": 0, "skipped": 1})
        self.assertEqual(calls, [])
        self.assertEqual(events._run([current]), {"processed": 1, "failed": 0, "skipped": 0})
        self.assertEqual(calls, [owner_token])

    def test_replay_only_resets_an_event_that_is_still_dead(self):
        with transaction.atomic():
            event = events.emit(events.PAYMENT_COMPLETED, entity_type="payment", entity_id="p-live", dispatch_after_commit=False)
        PaymentEvent.objects.filter(pk=event.pk).update(status=EventStatus.FAILED, attempts=3)
        self.assertEqual(events.replay_dead(event.event_id).attempts, 3)
        PaymentEvent.objects.filter(pk=event.pk).update(status=EventStatus.DEAD, attempts=5)
        replayed = events.replay_dead(event.event_id)
        self.assertEqual((replayed.status, replayed.attempts), (EventStatus.PENDING, 0))

    def test_audit_outcome_is_written_once_even_when_two_dispatchers_pass_the_check(self):
        with transaction.atomic():
            event = events.emit(
                events.PAYMENT_BLOCKED, entity_type="payment", entity_id="txn_race",
                payload={"summary": "Blocked", "decision": "block"}, dispatch_after_commit=False,
            )
        # Both dispatchers ran the existence check before either inserted.
        with mock.patch.object(AuditLogQuerySet, "exists", return_value=False):
            events._audit_outcome(event)
            events._audit_outcome(event)
        rows = AuditLog.objects.filter(action="payment.blocked", target_id="txn_race")
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.get().dedupe_key, "payment.blocked:payment:txn_race")

    def test_ordinary_audit_rows_are_not_deduplicated(self):
        from risk.services import audit

        for _ in range(2):
            audit.record(action="payment.evaluated", target_type="payment", target_id="txn_same", actor_label="service:test")
        self.assertEqual(AuditLog.objects.filter(target_id="txn_same").count(), 2)

    def test_capture_records_the_terminal_state_once(self):
        user = make_user()
        with self.captureOnCommitCallbacks(execute=True):
            txn = create_payment(user=user, data=ordinary_input()).transaction
        self.assertEqual(txn.status, TransactionStatus.COMPLETED)
        before = metrics.counter("payments_completed_total")
        # A second caller (a reconciler that loaded the row a moment earlier).
        payment_service.capture(txn)
        self.assertEqual(PaymentEvent.objects.filter(entity_id=txn.public_id, event_type="payment.completed").count(), 1)
        self.assertEqual(metrics.counter("payments_completed_total"), before)


# ── R8: the client address honours NUM_PROXIES ───────────────────────────────

def _drf(**overrides: Any) -> dict[str, Any]:
    return {**settings.REST_FRAMEWORK, **overrides}


class ClientAddressTests(APITestCase):
    def _request(self, **meta: str) -> Any:
        return SimpleNamespace(META=meta)

    def test_forwarded_header_is_trusted_only_as_far_as_num_proxies_says(self):
        spoofed = self._request(REMOTE_ADDR="10.9.8.7", HTTP_X_FORWARDED_FOR="198.51.100.7, 203.0.113.9")
        with override_settings(REST_FRAMEWORK=_drf(NUM_PROXIES=0)):
            self.assertEqual(payment_views._client_ip(spoofed), "10.9.8.7")
        with override_settings(REST_FRAMEWORK=_drf(NUM_PROXIES=None)):
            self.assertEqual(payment_views._client_ip(spoofed), "10.9.8.7")
        with override_settings(REST_FRAMEWORK=_drf(NUM_PROXIES=1)):
            self.assertEqual(payment_views._client_ip(spoofed), "203.0.113.9")
            self.assertEqual(payment_views._client_ip(self._request(REMOTE_ADDR="10.9.8.7")), "10.9.8.7")
        with override_settings(REST_FRAMEWORK=_drf(NUM_PROXIES=2)):
            self.assertEqual(payment_views._client_ip(spoofed), "198.51.100.7")
        with override_settings(REST_FRAMEWORK=_drf(NUM_PROXIES=5)):
            self.assertEqual(payment_views._client_ip(spoofed), "198.51.100.7")

    def test_stored_prefix_follows_the_setting(self):
        sandbox().reset()
        self.client.force_authenticate(make_user())
        with override_settings(REST_FRAMEWORK=_drf(NUM_PROXIES=0)):
            direct = self.client.post(URL, payment_payload(), format="json", HTTP_X_FORWARDED_FOR="203.0.113.9").json()
        self.assertEqual(direct["ip_prefix"], "127.0.0.0")
        with override_settings(REST_FRAMEWORK=_drf(NUM_PROXIES=1)):
            proxied = self.client.post(URL, payment_payload(), format="json", HTTP_X_FORWARDED_FOR="198.51.100.7, 203.0.113.9").json()
        self.assertEqual(proxied["ip_prefix"], "203.0.0.0")


class CoarsePrefixTests(SimpleTestCase):
    def test_prefix_is_coarse_and_only_for_real_addresses(self):
        self.assertEqual(coarse_ip_prefix("203.0.113.9"), "203.0.0.0")
        self.assertEqual(coarse_ip_prefix(" 203.0.113.9 "), "203.0.0.0")
        self.assertEqual(coarse_ip_prefix("2001:db8:85a3::8a2e:370:7334"), "2001:db8::")
        self.assertEqual(coarse_ip_prefix("2001:db8::1"), "2001:db8::")
        self.assertEqual(coarse_ip_prefix("::1"), "::")
        self.assertEqual(coarse_ip_prefix("::ffff:203.0.113.9"), "203.0.0.0")
        for junk in ("", None, "unknown", "203.0.113", "203.0.113.9:4431", "not an address, 1.2.3.4"):
            self.assertEqual(coarse_ip_prefix(junk), "", junk)


# ── R10: health figures and counters ─────────────────────────────────────────

class HealthPayloadTests(APITestCase):
    def test_database_evaluation_figures_cover_live_payments_only(self):
        cache.clear()
        sandbox().reset()
        user = make_user()
        self.client.force_authenticate(user)
        self.assertEqual(self.client.post(URL, payment_payload(), format="json").status_code, 201)
        run_scenario(user=user, scenario="card_testing", sensitivity=50, count=60, seed=3)

        database = self.client.get("/api/risk/health").json()["database"]
        self.assertEqual(database["transactions_24h"], 1)
        self.assertEqual(database["simulated_transactions_24h"], 60)
        self.assertEqual(database["evaluations_24h"], 1)
        self.assertEqual(database["evaluation_latency_24h"]["count"], 1)


class CounterCommitTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        metrics.reset_all()
        sandbox().reset()
        self.user = make_user()

    def counters(self) -> tuple[int, int]:
        return metrics.counter("payments_created_total"), metrics.counter("events_emitted_total")

    def test_counters_move_only_when_the_payment_is_durable(self):
        create_payment(user=self.user, data=ordinary_input())
        # created, risk_evaluated, approved, completed
        self.assertEqual(self.counters(), (1, 4))

        with mock.patch("risk.services.payments.evaluate_transaction", side_effect=RuntimeError("engine down")):
            with self.assertRaises(RuntimeError):
                create_payment(user=self.user, data=ordinary_input())
        self.assertEqual(PaymentTransaction.objects.count(), 1)
        self.assertEqual(self.counters(), (1, 4))

        # The caller's own transaction rolls back and takes the payment with it.
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                create_payment(user=self.user, data=ordinary_input(amount=Decimal("2500"), device_id="unseen"))
                raise RuntimeError("caller rolled back")
        self.assertEqual(PaymentTransaction.objects.count(), 1)
        self.assertEqual(self.counters(), (1, 4))


# ── R12: the admin cannot change the live policy behind the audit trail ──────

class PolicyAdminTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = get_user_model().objects.create_superuser(email="root@example.com", password="secretpass123", display_name="Root")
        self.client.force_login(self.admin)
        self.policy = get_active_policy()
        self.url = reverse("admin:risk_riskpolicy_change", args=[self.policy.pk])
        self.assertEqual(current_sensitivity(), 50)  # the value is now cached

    def test_admin_change_is_audited_emitted_and_takes_effect_immediately(self):
        self.assertContains(self.client.get(self.url), 'name="reason"')
        response = self.client.post(self.url, {"name": "default", "sensitivity": "80", "reason": "Move-out weekend"})
        self.assertEqual(response.status_code, 302, getattr(response, "content", b"")[:400])

        self.assertEqual(current_sensitivity(), 80)
        entry = AuditLog.objects.get(action="risk.policy_modified")
        self.assertEqual(entry.actor_label, f"user:{self.admin.pk}")
        self.assertEqual(entry.reason, "Move-out weekend")
        self.assertEqual((entry.metadata["previous_sensitivity"], entry.metadata["sensitivity"]), (50, 80))
        self.assertTrue(PaymentEvent.objects.filter(event_type="risk.policy_updated").exists())
        self.policy.refresh_from_db()
        self.assertEqual((self.policy.sensitivity, self.policy.updated_by_id), (80, self.admin.pk))

    def test_rename_alone_is_not_a_policy_change(self):
        response = self.client.post(self.url, {"name": "move-out", "sensitivity": "50", "reason": ""})
        self.assertEqual(response.status_code, 302)
        self.policy.refresh_from_db()
        self.assertEqual(self.policy.name, "move-out")
        self.assertFalse(AuditLog.objects.filter(action="risk.policy_modified").exists())

    def test_out_of_range_add_and_delete_are_refused(self):
        response = self.client.post(self.url, {"name": "default", "sensitivity": "150", "reason": ""})
        self.assertEqual(response.status_code, 200)  # redisplayed with an error
        self.assertContains(response, "between 0 and 100")
        self.policy.refresh_from_db()
        self.assertEqual(self.policy.sensitivity, 50)
        self.assertEqual(self.client.get(reverse("admin:risk_riskpolicy_add")).status_code, 403)
        self.assertEqual(self.client.post(reverse("admin:risk_riskpolicy_delete", args=[self.policy.pk]), {"post": "yes"}).status_code, 403)
        self.assertEqual(current_sensitivity(), 50)


# ── R13: request ids fit their column ────────────────────────────────────────

class RequestIdTests(APITestCase):
    def _request(self, **attributes: str) -> Any:
        return SimpleNamespace(**attributes)

    def test_request_id_is_bounded_before_it_is_stored(self):
        self.assertEqual(payment_views._request_id(self._request(request_id="r" * 200)), "r" * 64)
        self.assertEqual(payment_views._request_id(self._request(request_id="trace-42")), "trace-42")
        self.assertEqual(payment_views._request_id(self._request()), "")

    def test_oversized_header_still_creates_the_payment(self):
        sandbox().reset()
        self.client.force_authenticate(make_user())
        response = self.client.post(URL, payment_payload(), format="json", HTTP_X_REQUEST_ID="x" * 300)
        self.assertEqual(response.status_code, 201, response.content)
        stored = PaymentTransaction.objects.get()
        self.assertLessEqual(len(stored.request_id), 64)
        self.assertFalse(PaymentEvent.objects.filter(entity_id=stored.public_id).exclude(request_id=stored.request_id).exists())


# ── R14: the load-test script logs in with the CSRF header ───────────────────

class LoadTestLoginTests(SimpleTestCase):
    def test_login_sends_the_csrf_token_it_was_given(self):
        path = settings.BASE_DIR.parent / "scripts" / "risk_loadtest.py"
        if not path.exists():
            self.skipTest("scripts/ is not part of this checkout (a backend-only image)")
        spec = importlib.util.spec_from_file_location("risk_loadtest_under_test", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        # The script's dataclasses look their module up while it is executing.
        sys.modules[spec.name] = module
        self.addCleanup(sys.modules.pop, spec.name, None)
        spec.loader.exec_module(module)

        calls: list[tuple[str, str, dict[str, Any]]] = []

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

        class FakeSession:
            def __init__(self) -> None:
                self.cookies: dict[str, str] = {}

            def get(self, url: str, **kwargs: Any) -> FakeResponse:
                calls.append(("GET", url, kwargs))
                self.cookies["csrftoken"] = "token-from-server"
                return FakeResponse()

            def post(self, url: str, **kwargs: Any) -> FakeResponse:
                calls.append(("POST", url, kwargs))
                return FakeResponse()

        with mock.patch.object(module.requests, "Session", FakeSession):
            module.login("http://testserver", "a@example.com", "pw")
        self.assertEqual([c[:2] for c in calls], [("GET", "http://testserver/api/auth/csrf"), ("POST", "http://testserver/api/auth/login")])
        self.assertEqual(calls[1][2]["headers"]["X-CSRFToken"], "token-from-server")
        self.assertEqual(calls[1][2]["headers"]["Referer"], "http://testserver")

        class NoCookieSession(FakeSession):
            def get(self, url: str, **kwargs: Any) -> FakeResponse:
                return FakeResponse()

        with mock.patch.object(module.requests, "Session", NoCookieSession):
            with self.assertRaises(SystemExit):
                module.login("http://testserver", "a@example.com", "pw")
