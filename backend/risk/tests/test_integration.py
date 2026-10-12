"""Rule behaviour settled while integrating the audit fixes."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from django.test import SimpleTestCase

from risk.engine import evaluate_transaction
from risk.engine.context import TransactionContext


def burst_context(**overrides: Any) -> TransactionContext:
    base: dict[str, Any] = dict(
        transaction_id="t",
        user_id=1,
        merchant_id="mkt_snacks",
        amount=Decimal("2.00"),
        currency="USD",
        payment_method="card",
        device_id="phone-1",
        timestamp=datetime(2026, 10, 5, 15, 0, tzinfo=timezone.utc),
        account_age_days=0.2,
        txn_count_1h=5,
        txn_count_24h=5,
        device_seen_count=5,
    )
    base.update(overrides)
    return TransactionContext(**base)


class KnownDeviceAgeTests(SimpleTestCase):
    def codes(self, ctx):
        return {factor.code for factor in evaluate_transaction(ctx).factors}

    def test_a_burst_on_a_brand_new_device_earns_no_trust_discount(self):
        ctx = burst_context(device_first_seen_days=0.01)
        self.assertNotIn("known_device", self.codes(ctx))
        self.assertIn("card_testing", self.codes(ctx))

    def test_a_device_used_over_time_keeps_the_discount(self):
        self.assertIn("known_device", self.codes(burst_context(device_first_seen_days=30.0)))

    def test_unknown_age_is_not_treated_as_new(self):
        self.assertIn("known_device", self.codes(burst_context(device_first_seen_days=None)))

    def test_the_discount_still_needs_three_prior_uses(self):
        self.assertNotIn("known_device", self.codes(burst_context(device_seen_count=2, device_first_seen_days=30.0)))

    def test_the_burst_scores_higher_without_the_discount(self):
        new = evaluate_transaction(burst_context(device_first_seen_days=0.01)).risk_score
        aged = evaluate_transaction(burst_context(device_first_seen_days=30.0)).risk_score
        self.assertEqual(new - aged, 12)
