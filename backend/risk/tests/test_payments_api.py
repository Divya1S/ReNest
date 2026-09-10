"""Payment API: creation, decisions, processor outcomes, review, listing, read models."""

from __future__ import annotations

from decimal import Decimal

from django.core.cache import cache
from rest_framework.test import APITestCase

from risk.models import AuditLog, PaymentEvent, PaymentTransaction, RiskPolicy
from risk.services import metrics

from .support import make_user, payment_payload, sandbox

URL = "/api/risk/payments"


class PaymentCreateTests(APITestCase):
    def setUp(self):
        cache.clear()
        metrics.reset_all()
        sandbox().reset()
        self.user = make_user()
        self.client.force_authenticate(self.user)

    def test_ordinary_payment_is_allowed_and_captured(self):
        response = self.client.post(URL, payment_payload(), format="json", HTTP_X_REQUEST_ID="trace-42")
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertTrue(body["public_id"].startswith("txn_"))
        self.assertEqual(body["decision"], "allow")
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["request_id"], "trace-42")
        self.assertTrue(body["processor_reference"].startswith("sbx_"))
        self.assertEqual(body["model_version"], "risk-rules-v1")
        self.assertIn("evaluation", body)
        self.assertIn("factors", body["evaluation"])
        self.assertEqual([e["event_type"] for e in body["events"]], ["payment.created", "payment.risk_evaluated", "payment.approved", "payment.completed"])
        self.assertEqual([a["action"] for a in body["audit"]], ["payment.evaluated"])
        self.assertEqual(body["audit"][0]["actor_label"], f"user:{self.user.pk}")
        self.assertEqual(response["X-Request-Id"], "trace-42")

    def test_transient_processor_failure_is_retried_then_succeeds(self):
        response = self.client.post(URL, payment_payload(sandbox_behavior="fail_transient_once"), format="json")
        self.assertEqual(response.json()["status"], "completed")

    def test_exhausted_retries_fail_the_payment(self):
        response = self.client.post(URL, payment_payload(sandbox_behavior="fail_transient"), format="json")
        body = response.json()
        self.assertEqual(body["status"], "failed")
        self.assertTrue(body["failure_reason"].startswith("transient:"))
        self.assertIn("payment.failed", [e["event_type"] for e in body["events"]])
        self.assertEqual(metrics.counter("payments_failed_total"), 1)

    def test_decline_is_permanent_and_not_retried(self):
        response = self.client.post(URL, payment_payload(sandbox_behavior="decline"), format="json")
        body = response.json()
        self.assertEqual(body["status"], "failed")
        self.assertTrue(body["failure_reason"].startswith("permanent:"))
        self.assertEqual(sandbox()._attempts[body["public_id"]], 1)

    def test_risky_payment_is_blocked_without_touching_the_processor(self):
        fresh = make_user("fresh@example.com", days_old=0)
        self.client.force_authenticate(fresh)
        response = self.client.post(URL, payment_payload(amount="2400.00", device_id="unknown"), format="json")
        body = response.json()
        self.assertEqual(body["decision"], "block")
        self.assertEqual(body["status"], "blocked")
        self.assertEqual(body["processor_reference"], "")
        self.assertIn("extreme_amount", body["reasons"])
        self.assertIn("payment.blocked", [e["event_type"] for e in body["events"]])
        self.assertNotIn(body["public_id"], sandbox()._attempts)

    def test_metadata_is_redacted_at_the_boundary(self):
        response = self.client.post(URL, payment_payload(metadata={"note": "gift", "card_number": "4111111111111111", "nested": {"password": "hunter2"}}), format="json")
        body = response.json()
        self.assertEqual(body["metadata"]["note"], "gift")
        self.assertEqual(body["metadata"]["card_number"], "[redacted]")
        self.assertEqual(body["metadata"]["nested"]["password"], "[redacted]")
        stored = PaymentTransaction.objects.get(public_id=body["public_id"]).metadata
        self.assertNotIn("4111", str(stored))
        self.assertNotIn("hunter2", str(AuditLog.objects.values_list("metadata", flat=True)))


class PaymentValidationTests(APITestCase):
    def setUp(self):
        self.client.force_authenticate(make_user())

    def assert_rejected(self, field, **overrides):
        response = self.client.post(URL, payment_payload(**overrides), format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn(field, response.json())
        self.assertEqual(PaymentTransaction.objects.count(), 0)

    def test_zero_negative_and_absurd_amounts(self):
        self.assert_rejected("amount", amount="0")
        self.assert_rejected("amount", amount="-5.00")
        self.assert_rejected("amount", amount="1000000.01")
        self.assert_rejected("amount", amount="12.345")
        self.assert_rejected("amount", amount="abc")

    def test_unknown_currency_and_zero_decimal_rule(self):
        self.assert_rejected("currency", currency="XYZ")
        self.assert_rejected("amount", currency="JPY", amount="100.50")
        ok = self.client.post(URL, payment_payload(currency="JPY", amount="100"), format="json")
        self.assertEqual(ok.status_code, 201)

    def test_bad_method_country_merchant_and_metadata(self):
        self.assert_rejected("payment_method", payment_method="crypto")
        self.assert_rejected("country", country="USA")
        self.assert_rejected("merchant_id", merchant_id="   ")
        self.assert_rejected("metadata", metadata=["not", "an", "object"])
        self.assert_rejected("metadata", metadata={f"k{i}": i for i in range(25)})
        self.assert_rejected("metadata", metadata={"blob": "x" * 3000})
        self.assert_rejected("sandbox_behavior", sandbox_behavior="explode")

    def test_missing_fields(self):
        response = self.client.post(URL, {}, format="json")
        self.assertEqual(response.status_code, 400)
        for field in ("merchant_id", "amount", "currency", "payment_method"):
            self.assertIn(field, response.json())

    def test_malformed_json_is_rejected(self):
        response = self.client.post(URL, "{not json", content_type="application/json")
        self.assertEqual(response.status_code, 400)


class ReviewAndListingTests(APITestCase):
    def setUp(self):
        cache.clear()
        sandbox().reset()
        self.user = make_user()
        self.staff = make_user("staff@example.com", staff=True)
        self.client.force_authenticate(self.user)

    def _held_payment(self):
        # Brand-new account + very large first purchase lands in the review band at sensitivity 50.
        fresh = make_user("held@example.com", days_old=0)
        self.client.force_authenticate(fresh)
        response = self.client.post(URL, payment_payload(amount="1200.00"), format="json")
        self.client.force_authenticate(self.user)
        self.assertEqual(response.json()["decision"], "review", response.json()["evaluation"]["explanation"])
        return response.json()["public_id"]

    def test_staff_can_approve_a_held_payment_which_then_captures(self):
        public_id = self._held_payment()
        self.client.force_authenticate(self.staff)
        response = self.client.post(f"{URL}/{public_id}/review", {"approve": True, "note": "Verified by phone"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["manual_review"]["outcome"], "approved")
        self.assertIn("payment.reviewed", [e["event_type"] for e in body["events"]])
        self.assertIn("payment.manual_review", [a["action"] for a in body["audit"]])
        again = self.client.post(f"{URL}/{public_id}/review", {"approve": True}, format="json")
        self.assertEqual(again.status_code, 409)

    def test_staff_can_reject_and_customers_cannot_review(self):
        public_id = self._held_payment()
        forbidden = self.client.post(f"{URL}/{public_id}/review", {"approve": True}, format="json")
        self.assertEqual(forbidden.status_code, 403)
        self.client.force_authenticate(self.staff)
        response = self.client.post(f"{URL}/{public_id}/review", {"approve": False, "note": "Could not verify"}, format="json")
        self.assertEqual(response.json()["status"], "blocked")
        self.assertEqual(response.json()["decision"], "block")

    def test_list_is_scoped_and_filterable(self):
        self.client.post(URL, payment_payload(), format="json")
        other = make_user("other@example.com")
        self.client.force_authenticate(other)
        self.client.post(URL, payment_payload(merchant_id="mkt_other"), format="json")
        self.client.force_authenticate(self.user)
        mine = self.client.get(URL).json()
        self.assertEqual(mine["count"], 1)
        self.assertEqual(mine["results"][0]["merchant_id"], "mkt_textbooks")
        self.client.force_authenticate(self.staff)
        everything = self.client.get(URL).json()
        self.assertEqual(everything["count"], 2)
        self.assertEqual(self.client.get(f"{URL}?q=mkt_other").json()["count"], 1)
        self.assertEqual(self.client.get(f"{URL}?decision=block").json()["count"], 0)
        self.assertEqual(self.client.get(f"{URL}?q={'x' * 500}").status_code, 200)

    def test_detail_is_not_visible_to_other_customers(self):
        public_id = self.client.post(URL, payment_payload(), format="json").json()["public_id"]
        self.client.force_authenticate(make_user("other@example.com"))
        self.assertEqual(self.client.get(f"{URL}/{public_id}").status_code, 404)
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.get(f"{URL}/{public_id}").status_code, 200)


class ReadModelTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user()
        self.client.force_authenticate(self.user)
        self.client.post(URL, payment_payload(), format="json")
        self.client.post(URL, payment_payload(amount="15.00"), format="json")

    def test_overview_counts_real_rows(self):
        body = self.client.get("/api/risk/overview?hours=24").json()
        self.assertEqual(body["totals"]["transactions"], 2)
        self.assertEqual(body["totals"]["allowed"], 2)
        self.assertEqual(body["rates"]["block_rate"], 0.0)
        self.assertEqual(body["distribution"]["low"], 2)
        self.assertEqual(body["evaluation_latency"]["count"], 2)
        self.assertEqual(body["policy"]["sensitivity"], 50)
        self.assertFalse(body["includes_simulation"])
        self.assertEqual(len(body["series"]), 1)

    def test_feed_and_distribution(self):
        feed = self.client.get("/api/risk/feed?limit=1").json()
        self.assertEqual(len(feed["results"]), 1)
        self.assertEqual(feed["results"][0]["amount"], "15.00")
        dist = self.client.get("/api/risk/distribution").json()
        self.assertEqual(sum(b["count"] for b in dist["histogram"]), 2)
        self.assertEqual(dist["thresholds"], {"review": 40, "block": 70})

    def test_policy_update_is_staff_only_audited_and_applied(self):
        self.assertEqual(self.client.get("/api/risk/policy").json()["sensitivity"], 50)
        self.assertEqual(self.client.put("/api/risk/policy", {"sensitivity": 80}, format="json").status_code, 403)
        staff = make_user("staff@example.com", staff=True)
        self.client.force_authenticate(staff)
        self.assertEqual(self.client.put("/api/risk/policy", {"sensitivity": 101}, format="json").status_code, 400)
        response = self.client.put("/api/risk/policy", {"sensitivity": 80, "reason": "Move-out weekend"}, format="json")
        self.assertEqual(response.json()["block_threshold"], 58)
        self.assertEqual(RiskPolicy.objects.get(active=True).sensitivity, 80)
        entry = AuditLog.objects.get(action="risk.policy_modified")
        self.assertEqual(entry.actor_label, f"user:{staff.pk}")
        self.assertEqual(entry.metadata["previous_sensitivity"], 50)
        self.assertTrue(PaymentEvent.objects.filter(event_type="risk.policy_updated").exists())
        created = self.client.post(URL, payment_payload(), format="json").json()
        self.assertEqual(created["evaluation"]["thresholds"]["sensitivity"], 80)

    def test_rules_catalogue_comes_from_the_engine(self):
        body = self.client.get("/api/risk/rules").json()
        self.assertIn("amount_vs_history", [r["code"] for r in body["rules"]])
        self.assertEqual(body["model_version"], "risk-rules-v1")

    def test_health_and_metrics(self):
        health = self.client.get("/api/risk/health")
        self.assertEqual(health.status_code, 200)
        body = health.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["processor"], "sandbox")
        self.assertEqual(body["investigator_mode"], "deterministic")
        self.assertEqual(body["events"]["dead"], 0)
        self.assertEqual(body["database"]["transactions_24h"], 2)
        self.assertGreaterEqual(body["counters"]["payments_created_total"], 2)
        metrics_body = self.client.get("/api/risk/metrics").json()
        self.assertIn("risk_evaluation_ms", metrics_body["latency"])
        self.assertEqual(self.client.get("/api/risk/metrics/prometheus").status_code, 403)
        self.client.force_authenticate(make_user("staff@example.com", staff=True))
        prom = self.client.get("/api/risk/metrics/prometheus")
        self.assertEqual(prom.status_code, 200)
        self.assertIn("renest_risk_payments_created_total", prom.content.decode())

    def test_events_and_audit_are_scoped(self):
        events = self.client.get("/api/risk/events").json()
        self.assertEqual(events["count"], 8)
        audit = self.client.get("/api/risk/audit").json()
        self.assertEqual(audit["count"], 2)
        self.client.force_authenticate(make_user("other@example.com"))
        self.assertEqual(self.client.get("/api/risk/events").json()["count"], 0)
        self.assertEqual(self.client.get("/api/risk/audit").json()["count"], 0)

    def test_event_replay_requires_staff_and_dead_status(self):
        event = PaymentEvent.objects.order_by("sequence")[0]
        self.assertEqual(self.client.post(f"/api/risk/events/{event.event_id}/replay").status_code, 403)
        self.client.force_authenticate(make_user("staff@example.com", staff=True))
        self.assertEqual(self.client.post(f"/api/risk/events/{event.event_id}/replay").status_code, 409)
        self.assertEqual(self.client.post("/api/risk/events/dispatch").json()["failed"], 0)
