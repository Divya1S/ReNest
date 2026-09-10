"""Fraud Lab: generators, measured metrics, sweeps, persistence and the API."""

from __future__ import annotations

from decimal import Decimal

from django.core.cache import cache
from rest_framework.test import APITestCase

from risk.engine.signals import build_context
from risk.models import PaymentTransaction, RiskEvaluation, RiskFactor, SimulationRun
from risk.services import simulation

from .support import NOW, make_user


class GeneratorTests(APITestCase):
    def test_same_seed_reproduces_the_dataset(self):
        a = simulation.generate("card_testing", count=120, seed=9, now=NOW)
        b = simulation.generate("card_testing", count=120, seed=9, now=NOW)
        self.assertEqual([r.context.snapshot() for r in a], [r.context.snapshot() for r in b])
        c = simulation.generate("card_testing", count=120, seed=10)
        self.assertNotEqual([r.context.amount for r in a], [r.context.amount for r in c])

    def test_fraud_share_matches_scenario(self):
        rows = simulation.generate("account_takeover", count=200, seed=1)
        self.assertEqual(sum(r.is_fraud for r in rows), round(200 * simulation.SCENARIOS["account_takeover"].fraud_share))
        self.assertEqual(sum(r.is_fraud for r in simulation.generate("normal_customer", count=100, seed=1)), 0)

    def test_every_scenario_generates_valid_contexts(self):
        for key in simulation.SCENARIOS:
            for row in simulation.generate(key, count=60, seed=3):
                self.assertGreater(row.context.amount, 0)
                self.assertIn(row.profile, {"legit_new_account", "legit_established", key} | {s.key for s in simulation.SCENARIOS.values()} | {"new_device_attack"})


class MetricsTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user()

    def test_metrics_are_counted_from_engine_output(self):
        run = simulation.run_scenario(user=self.user, scenario="account_takeover", sensitivity=50, count=300, seed=5)
        m = run.metrics
        self.assertEqual(m["analyzed"], 300)
        self.assertEqual(m["allowed"] + m["reviewed"] + m["blocked"], 300)
        self.assertEqual(m["labelled_fraud"] + m["labelled_legitimate"], 300)
        self.assertEqual(m["fraud_blocked"] + m["fraud_reviewed"] + m["fraud_missed"], m["labelled_fraud"])
        flagged = m["blocked"] + m["reviewed"]
        fraud_flagged = m["fraud_blocked"] + m["fraud_reviewed"]
        self.assertAlmostEqual(m["precision_flagged"], fraud_flagged / flagged, places=4)
        self.assertAlmostEqual(m["recall_flagged"], fraud_flagged / m["labelled_fraud"], places=4)
        self.assertAlmostEqual(m["false_positive_rate"], (m["legitimate_blocked"] + m["legitimate_reviewed"]) / m["labelled_legitimate"], places=4)
        self.assertEqual(sum(m["risk_bands"].values()), 300)
        self.assertEqual(m["latency"]["count"], 300)
        self.assertIsNotNone(m["latency"]["p95_ms"])
        self.assertEqual(m["model_version"], "risk-rules-v1")
        self.assertGreater(float(run.duration_ms), 0)
        # Cross-check one number against the persisted rows.
        stored_blocked = PaymentTransaction.objects.filter(simulation_run=run, decision="block").count()
        self.assertEqual(stored_blocked, m["blocked"])
        protected = PaymentTransaction.objects.filter(simulation_run=run, decision="block", ground_truth_fraud=True).aggregate(
            total=__import__("django.db.models", fromlist=["Sum"]).Sum("amount")
        )["total"]
        self.assertEqual(Decimal(m["protected_amount"]), protected or Decimal("0"))

    def test_normal_traffic_has_no_ground_truth_fraud_so_precision_is_undefined(self):
        run = simulation.run_scenario(user=self.user, scenario="normal_customer", sensitivity=50, count=200, seed=2)
        m = run.metrics
        self.assertEqual(m["labelled_fraud"], 0)
        self.assertIsNone(m["recall_flagged"])
        self.assertIsNone(m["recall_blocked"])
        self.assertIsNotNone(m["false_positive_rate"])

    def test_sweep_trades_detection_for_friction_monotonically(self):
        run = simulation.run_scenario(user=self.user, scenario="new_device_attack", sensitivity=50, count=400, seed=4)
        sweep = run.metrics["sweep"]
        self.assertEqual([p["sensitivity"] for p in sweep], list(range(0, 101, 10)))
        for earlier, later in zip(sweep, sweep[1:]):
            self.assertGreaterEqual(later["detection_rate"], earlier["detection_rate"])
            self.assertGreaterEqual(later["friction_rate"], earlier["friction_rate"])
        self.assertGreater(sweep[-1]["friction_rate"], sweep[0]["friction_rate"])
        self.assertGreater(sweep[-1]["detection_rate"], sweep[0]["detection_rate"])

    def test_rows_are_persisted_labelled_and_pruned(self):
        for i in range(simulation.RETAINED_RUNS_PER_USER + 2):
            simulation.run_scenario(user=self.user, scenario="high_velocity", sensitivity=50, count=simulation.MIN_COUNT, seed=i + 1)
        self.assertEqual(SimulationRun.objects.filter(user=self.user).count(), simulation.RETAINED_RUNS_PER_USER)
        run = SimulationRun.objects.filter(user=self.user).first()
        txns = PaymentTransaction.objects.filter(simulation_run=run)
        self.assertEqual(txns.count(), simulation.MIN_COUNT)
        self.assertTrue(all(t.is_simulation for t in txns))
        self.assertTrue(all(t.ground_truth_fraud in (True, False) for t in txns))
        self.assertEqual(RiskEvaluation.objects.filter(transaction__simulation_run=run).count(), simulation.MIN_COUNT)
        self.assertGreater(RiskFactor.objects.filter(evaluation__transaction__simulation_run=run).count(), 0)
        self.assertEqual(PaymentTransaction.objects.filter(is_simulation=True).count(), simulation.RETAINED_RUNS_PER_USER * simulation.MIN_COUNT)

    def test_simulation_rows_never_leak_into_a_real_profile(self):
        simulation.run_scenario(user=self.user, scenario="card_testing", sensitivity=50, count=100, seed=1)
        ctx = build_context(
            user=self.user, transaction_id="x", merchant_id="m", merchant_category="", amount=Decimal("10"),
            currency="USD", payment_method="card", device_id="dev-1", ip_prefix="", country="",
        )
        self.assertEqual(ctx.historical_txn_count, 0)
        self.assertEqual(ctx.txn_count_24h, 0)
        self.assertEqual(ctx.device_seen_count, 0)


class SimulationApiTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user()
        self.client.force_authenticate(self.user)

    def test_run_and_read_back(self):
        catalogue = self.client.get("/api/risk/scenarios").json()
        self.assertEqual(len(catalogue["scenarios"]), 8)
        response = self.client.post("/api/risk/simulations", {"scenario": "coordinated_fraud", "count": 200, "seed": 11}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["transaction_count"], 200)
        self.assertEqual(body["sensitivity"], 50)
        self.assertEqual(body["metrics"]["analyzed"], 200)
        detail = self.client.get(f"/api/risk/simulations/{body['public_id']}").json()
        self.assertEqual(detail["seed"], 11)
        listing = self.client.get("/api/risk/simulations").json()
        self.assertEqual(listing["count"], 1)
        self.assertIn("headline", listing["results"][0])
        transactions = self.client.get(f"/api/risk/payments?include_simulation=1&simulation={body['public_id']}").json()
        self.assertEqual(transactions["count"], 200)
        self.assertTrue(transactions["results"][0]["is_simulation"])
        self.assertEqual(self.client.get("/api/risk/payments").json()["count"], 0)

    def test_rescore_changes_decisions_without_rerunning_rules(self):
        body = self.client.post("/api/risk/simulations", {"scenario": "suspicious_amount", "count": 200, "seed": 3}, format="json").json()
        lenient = self.client.get(f"/api/risk/simulations/{body['public_id']}/rescore?sensitivity=0").json()
        strict = self.client.get(f"/api/risk/simulations/{body['public_id']}/rescore?sensitivity=100").json()
        self.assertGreaterEqual(strict["blocked"], lenient["blocked"])
        self.assertGreaterEqual(strict["blocked"] + strict["reviewed"], lenient["blocked"] + lenient["reviewed"])
        self.assertEqual(strict["thresholds"], {"review": 20, "block": 50})
        self.assertEqual(RiskEvaluation.objects.filter(transaction__simulation_run__public_id=body["public_id"]).count(), 200)

    def test_validation_and_scoping(self):
        self.assertEqual(self.client.post("/api/risk/simulations", {"scenario": "zombie"}, format="json").status_code, 400)
        self.assertEqual(self.client.post("/api/risk/simulations", {"scenario": "card_testing", "count": 5}, format="json").status_code, 400)
        self.assertEqual(self.client.post("/api/risk/simulations", {"scenario": "card_testing", "sensitivity": 500}, format="json").status_code, 400)
        run = self.client.post("/api/risk/simulations", {"scenario": "card_testing", "count": 50}, format="json").json()
        self.client.force_authenticate(make_user("other@example.com"))
        self.assertEqual(self.client.get(f"/api/risk/simulations/{run['public_id']}").status_code, 404)
        self.assertEqual(self.client.get(f"/api/risk/simulations/{run['public_id']}/rescore").status_code, 404)
