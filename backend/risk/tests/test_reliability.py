"""Retries, processor, metrics, audit redaction, ids, ops integration, commands."""

from __future__ import annotations

import io
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APITestCase

from risk import ids
from risk.models import AgentPolicy, AuditLog, PaymentTransaction, SimulationRun
from risk.services import audit, metrics
from risk.services.processor import SandboxProcessor
from risk.services.retry import PermanentError, RetryOutcome, RetryPolicy, TransientError, run_with_retries

from .support import make_user, payment_payload


class RetryTests(SimpleTestCase):
    def test_transient_errors_are_retried_up_to_the_limit(self):
        calls = {"n": 0}

        def op():
            calls["n"] += 1
            if calls["n"] < 3:
                raise TransientError("blip")
            return "ok"

        outcome = RetryOutcome(attempts=0, errors=[])
        result = run_with_retries(op, policy=RetryPolicy(attempts=3, sleep=lambda _: None), outcome=outcome)
        self.assertEqual(result, "ok")
        self.assertEqual(outcome.attempts, 3)
        self.assertEqual(len(outcome.errors), 2)

    def test_exhaustion_raises_the_last_error(self):
        with self.assertRaises(TransientError):
            run_with_retries(lambda: (_ for _ in ()).throw(TransientError("down")), policy=RetryPolicy(attempts=2, sleep=lambda _: None))

    def test_permanent_errors_are_never_retried(self):
        calls = {"n": 0}

        def op():
            calls["n"] += 1
            raise PermanentError("declined")

        with self.assertRaises(PermanentError):
            run_with_retries(op, policy=RetryPolicy(attempts=5, sleep=lambda _: None))
        self.assertEqual(calls["n"], 1)

    def test_backoff_is_exponential_and_capped(self):
        policy = RetryPolicy(base_delay=0.1, multiplier=2, max_delay=0.35, jitter=0)
        self.assertEqual([policy.delay_for(a) for a in (1, 2, 3, 4)], [0.1, 0.2, 0.35, 0.35])


class ProcessorTests(SimpleTestCase):
    def test_charge_is_idempotent_per_reference(self):
        processor = SandboxProcessor()
        first = processor.charge(reference="txn_1", amount=Decimal("10"), currency="USD")
        second = processor.charge(reference="txn_1", amount=Decimal("10"), currency="USD")
        self.assertEqual(first, second)
        self.assertEqual(processor._attempts["txn_1"], 1)

    def test_behaviours(self):
        processor = SandboxProcessor()
        with self.assertRaises(TransientError):
            processor.charge(reference="a", amount=Decimal("1"), currency="USD", behavior="fail_transient_once")
        self.assertTrue(processor.charge(reference="a", amount=Decimal("1"), currency="USD", behavior="fail_transient_once").accepted)
        with self.assertRaises(PermanentError):
            processor.charge(reference="b", amount=Decimal("1"), currency="USD", behavior="decline")


class MetricsTests(TestCase):
    def setUp(self):
        cache.clear()
        metrics.reset_all()

    def test_percentiles_and_summary(self):
        values = [float(v) for v in range(1, 101)]
        self.assertEqual(metrics.percentile(values, 0.5), 50.5)
        self.assertEqual(round(metrics.percentile(values, 0.95) or 0, 2), 95.05)
        self.assertIsNone(metrics.percentile([], 0.5))
        summary = metrics.summarize(values)
        self.assertEqual((summary.count, summary.max_ms, summary.mean_ms), (100, 100.0, 50.5))
        self.assertEqual(metrics.summarize([]).p99_ms, None)

    def test_counters_reservoir_and_prometheus_text(self):
        metrics.increment("requests_total", 3)
        for v in range(600):
            metrics.observe("request_ms", v)
        self.assertEqual(metrics.counter("requests_total"), 3)
        self.assertEqual(len(metrics.samples("request_ms")), metrics.RESERVOIR_SIZE)
        text = metrics.prometheus_text()
        self.assertIn("renest_risk_requests_total 3", text)
        self.assertIn('renest_risk_request_ms{quantile="0.95"}', text)
        self.assertIn("renest_risk_request_ms_count 500", text)

    def test_timer_measures(self):
        with metrics.Timer() as t:
            sum(range(1000))
        self.assertGreaterEqual(t.ms, 0)


class MiddlewareTests(APITestCase):
    def setUp(self):
        cache.clear()
        metrics.reset_all()
        self.client.force_authenticate(make_user())

    def test_requests_errors_and_latency_are_recorded_for_risk_paths_only(self):
        self.client.get("/api/risk/rules")
        self.client.get("/api/listings")
        self.assertEqual(metrics.counter("requests_total"), 1)
        self.assertEqual(len(metrics.samples("request_ms")), 1)
        self.client.raise_request_exception = False
        with mock.patch("risk.views.dashboard.RulesView.get", side_effect=RuntimeError("boom")):
            self.assertEqual(self.client.get("/api/risk/rules").status_code, 500)
        self.assertEqual(metrics.counter("errors_total"), 1)
        self.assertEqual(metrics.counter("requests_total"), 2)


class AuditTests(TestCase):
    def test_redaction_and_append_only(self):
        entry = audit.record(
            action="test", target_type="payment", target_id="t", actor_label="service:test",
            metadata={"password": "x", "card": {"pan": "4111", "cvv": "123", "last4": "1111"}, "token": "abc", "list": [{"secret": "s"}]},
        )
        self.assertEqual(entry.metadata["password"], "[redacted]")
        self.assertEqual(entry.metadata["card"]["pan"], "[redacted]")
        self.assertEqual(entry.metadata["card"]["last4"], "1111")
        self.assertEqual(entry.metadata["list"][0]["secret"], "[redacted]")
        entry.reason = "tampered"
        with self.assertRaises(PermissionError):
            entry.save()
        with self.assertRaises(PermissionError):
            entry.delete()
        with self.assertRaises(PermissionError):
            AuditLog.objects.all().delete()
        self.assertEqual(AuditLog.objects.get(pk=entry.pk).reason, "")


class IdTests(SimpleTestCase):
    def test_public_ids_are_prefixed_and_time_sortable(self):
        first = ids.public_id("txn")
        second = ids.public_id("txn")
        self.assertTrue(first.startswith("txn_") and second.startswith("txn_"))
        self.assertEqual(len(first), 4 + 26)
        self.assertNotEqual(first, second)
        self.assertLessEqual(first[:14], second[:14])


class OpsIntegrationTests(TestCase):
    def test_tick_jobs_include_risk_maintenance(self):
        from listings.ops import run_scheduled_jobs

        results = run_scheduled_jobs(["tick"])
        self.assertTrue(results["dispatch_risk_events"]["ok"])
        self.assertTrue(results["purge_idempotency_keys"]["ok"])

    def test_process_events_command_runs_once(self):
        out = io.StringIO()
        call_command("process_risk_events", stdout=out)
        self.assertIn("processed=0", out.getvalue())

    def test_seed_command_creates_real_rows(self):
        user = make_user("seed@example.com")
        out = io.StringIO()
        call_command("seed_risk_demo", email=user.email, payments=6, skip_simulations=True, stdout=out)
        self.assertEqual(PaymentTransaction.objects.filter(user=user, is_simulation=False).count(), 6)
        self.assertEqual(AgentPolicy.objects.filter(user=user).count(), 1)
        self.assertEqual(SimulationRun.objects.count(), 0)
        self.assertIn("payments created: 6", out.getvalue())
