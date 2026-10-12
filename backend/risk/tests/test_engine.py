"""Pure engine tests: no database."""

from __future__ import annotations

import json
from decimal import Decimal

from django.test import SimpleTestCase

from risk.engine import MODEL_VERSION, RULES, RuleResult, evaluate_transaction, rescore, thresholds_for
from risk.engine.context import TransactionContext
from risk.engine.scoring import confidence_for, decide, score_from

from .support import context, takeover_context


class ThresholdTests(SimpleTestCase):
    def test_sensitivity_moves_both_thresholds_with_constant_review_band(self):
        for s in (0, 25, 50, 75, 100):
            t = thresholds_for(s)
            self.assertEqual(t.block - t.review, 30)
        self.assertEqual((thresholds_for(0).review, thresholds_for(0).block), (60, 90))
        self.assertEqual((thresholds_for(50).review, thresholds_for(50).block), (40, 70))
        self.assertEqual((thresholds_for(100).review, thresholds_for(100).block), (20, 50))

    def test_sensitivity_is_clamped(self):
        self.assertEqual(thresholds_for(-10).sensitivity, 0)
        self.assertEqual(thresholds_for(1000).sensitivity, 100)

    def test_rescore_never_relaxes_when_sensitivity_rises(self):
        order = {"allow": 0, "review": 1, "block": 2}
        for score in range(0, 101, 5):
            previous = -1
            for s in range(0, 101, 10):
                current = order[rescore(score, s)]
                self.assertGreaterEqual(current, previous, f"score={score} s={s}")
                previous = current

    def test_decide_boundaries_are_inclusive(self):
        t = thresholds_for(50)
        self.assertEqual(decide(t.review, t), "review")
        self.assertEqual(decide(t.review - 1, t), "allow")
        self.assertEqual(decide(t.block, t), "block")


class ScoringTests(SimpleTestCase):
    def test_score_is_sum_clamped_to_0_100(self):
        self.assertEqual(score_from([RuleResult("a", 60, "a"), RuleResult("b", 70, "b")]), 100)
        self.assertEqual(score_from([RuleResult("a", -30, "a")]), 0)
        self.assertEqual(score_from([]), 0)

    def test_confidence_stays_in_documented_range(self):
        for ctx in (context(), takeover_context(), context(historical_txn_count=0, device_id="", ip_prefix="")):
            for score in (0, 39, 40, 70, 100):
                value = confidence_for(score, thresholds_for(50), ctx)
                self.assertGreaterEqual(value, 0.50)
                self.assertLessEqual(value, 0.99)

    def test_more_evidence_means_more_confidence(self):
        rich = confidence_for(10, thresholds_for(50), context(ip_prefix="10.1.0.0"))
        sparse = confidence_for(10, thresholds_for(50), context(historical_txn_count=0, device_id="", account_age_days=0, ip_prefix=""))
        self.assertGreater(rich, sparse)


class DecisionTests(SimpleTestCase):
    def test_trusted_customer_is_allowed_with_negative_factors(self):
        decision = evaluate_transaction(context(), sensitivity=50)
        self.assertEqual(decision.decision, "allow")
        self.assertEqual(decision.risk_score, 0)
        codes = {f.code for f in decision.factors}
        self.assertIn("known_device", codes)
        self.assertIn("trusted_customer", codes)
        self.assertIn("established_account", codes)
        self.assertTrue(all(f.points < 0 for f in decision.factors))

    def test_account_takeover_is_blocked_and_explained(self):
        decision = evaluate_transaction(takeover_context(), sensitivity=50)
        self.assertEqual(decision.decision, "block")
        self.assertGreaterEqual(decision.risk_score, 70)
        self.assertEqual(decision.reasons[0], "amount_vs_history")
        self.assertIn("new_device", decision.reasons)
        self.assertIn("ip_churn", decision.reasons)
        self.assertIn("RISK SCORE:", decision.explanation)
        self.assertIn("DECISION: BLOCK", decision.explanation)
        self.assertIn(f"RISK SCORE: {decision.risk_score} / 100", decision.explanation)

    def test_card_testing_pattern_fires_on_small_rapid_charges(self):
        ctx = context(
            amount=Decimal("1.50"), historical_txn_count=0, successful_txn_count=0, historical_avg_amount=None,
            account_age_days=0.2, txn_count_1h=6, txn_count_24h=6, device_seen_count=0,
        )
        decision = evaluate_transaction(ctx, sensitivity=50)
        self.assertIn("card_testing", decision.reasons)
        self.assertIn("velocity_1h", decision.reasons)
        self.assertEqual(decision.decision, "block")

    def test_device_sharing_signals_multi_account_abuse(self):
        ctx = context(account_age_days=0.5, historical_txn_count=0, successful_txn_count=0, historical_avg_amount=None, device_seen_count=0, accounts_on_device_24h=5)
        decision = evaluate_transaction(ctx, sensitivity=50)
        self.assertIn("device_sharing", decision.reasons)
        self.assertNotEqual(decision.decision, "allow")

    def test_same_score_different_sensitivity_changes_decision(self):
        # 6x average (+34), new device (+21), 5 networks (+15), 03:00 (+4), trusted (-15), established (-6) = 53
        ctx = context(amount=Decimal("240.00"), device_seen_count=0, device_first_seen_days=None, distinct_ips_24h=5, local_hour=3)
        low = evaluate_transaction(ctx, sensitivity=0)
        mid = evaluate_transaction(ctx, sensitivity=50)
        high = evaluate_transaction(ctx, sensitivity=100)
        self.assertEqual(low.risk_score, 53)
        self.assertEqual((low.risk_score, mid.risk_score, high.risk_score), (53, 53, 53))
        self.assertEqual((low.decision, mid.decision, high.decision), ("allow", "review", "block"))

    def test_amount_ratio_needs_history_to_be_trusted(self):
        thin = context(historical_txn_count=1, successful_txn_count=1, historical_avg_amount=Decimal("5.00"), amount=Decimal("60.00"))
        decision = evaluate_transaction(thin, sensitivity=50)
        self.assertNotIn("amount_vs_history", decision.reasons)
        deep = context(historical_txn_count=5, successful_txn_count=5, historical_avg_amount=Decimal("5.00"), amount=Decimal("60.00"))
        self.assertIn("amount_vs_history", evaluate_transaction(deep, sensitivity=50).reasons)

    def test_extreme_amount_boundaries(self):
        at_1000 = evaluate_transaction(context(amount=Decimal("1000.00")), sensitivity=50)
        self.assertEqual(next(f.points for f in at_1000.factors if f.code == "extreme_amount"), 12)
        at_2000 = evaluate_transaction(context(amount=Decimal("2000.00")), sensitivity=50)
        self.assertEqual(next(f.points for f in at_2000.factors if f.code == "extreme_amount"), 35)
        below = evaluate_transaction(context(amount=Decimal("999.99")), sensitivity=50)
        self.assertNotIn("extreme_amount", below.reasons)

    def test_rules_are_independently_pluggable(self):
        class AlwaysRisky:
            code = "always_risky"
            description = "test rule"

            def evaluate(self, ctx: TransactionContext) -> RuleResult | None:
                return RuleResult(self.code, 140, "Test rule fired")

        decision = evaluate_transaction(context(), sensitivity=50, rules=RULES + (AlwaysRisky(),))
        self.assertIn("always_risky", decision.reasons)
        self.assertEqual(decision.decision, "block")
        self.assertEqual(decision.risk_score, 100)
        without = evaluate_transaction(context(), sensitivity=50, rules=())
        self.assertEqual(without.risk_score, 0)
        self.assertEqual(without.factors, [])
        self.assertIn("No risk signals fired", without.explanation)

    def test_result_shape_and_measured_latency(self):
        decision = evaluate_transaction(takeover_context(), sensitivity=50)
        payload = decision.as_dict()
        for key in ("decision", "riskScore", "confidence", "reasons", "modelVersion", "evaluationTimeMs", "factors", "thresholds"):
            self.assertIn(key, payload)
        self.assertEqual(payload["modelVersion"], MODEL_VERSION)
        self.assertGreaterEqual(payload["evaluationTimeMs"], 0)
        self.assertLess(payload["evaluationTimeMs"], 50)
        json.dumps(payload)  # JSON-safe

    def test_snapshot_is_json_safe_and_replayable(self):
        ctx = takeover_context()
        snap = ctx.snapshot()
        json.dumps(snap)
        self.assertEqual(snap["amount"], "420.00")
        self.assertTrue(snap["is_new_device"])
        self.assertEqual(snap["historical_currencies"], ["USD"])

    def test_explanation_never_disagrees_with_score(self):
        for ctx in (context(), takeover_context(), context(amount=Decimal("2500"))):
            decision = evaluate_transaction(ctx, sensitivity=50)
            listed = sum(int(line.split()[0]) for line in decision.explanation.splitlines() if line[:1] in "+-")
            self.assertEqual(max(0, min(100, listed)), decision.risk_score)

    def test_currency_mismatch_and_unusual_hour_are_weak_signals(self):
        decision = evaluate_transaction(context(currency="EUR", local_hour=3), sensitivity=50)
        points = {f.code: f.points for f in decision.factors}
        self.assertEqual(points["currency_mismatch"], 8)
        self.assertEqual(points["unusual_hour"], 4)
        self.assertEqual(decision.decision, "allow")
