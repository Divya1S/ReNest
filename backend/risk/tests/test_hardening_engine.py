"""Regressions from the production-hardening audit of the decision side:
signals and rules (device sharing, history counts, currency), the
investigator, the demo seed and the agent sandbox."""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from typing import Any
from unittest import mock

from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import Campus
from risk.engine import evaluate_transaction
from risk.engine.currency import USD_REFERENCE_RATES, to_usd
from risk.models import (
    SUPPORTED_CURRENCIES,
    AgentPolicy,
    AgentTransaction,
    AuditLog,
    PaymentEvent,
    PaymentTransaction,
    RiskFactor,
    SimulationRun,
)
from risk.services import investigator
from risk.services.agents import evaluate_agent_transaction, spent_today
from risk.services.payments import PaymentInput, create_payment

from .support import context, make_user, payment_payload, sandbox

URL = "/api/risk/payments"


def codes_of(body: dict[str, Any]) -> set[str]:
    return {f["code"] for f in body["evaluation"]["factors"]}


def reasons_of(ctx: Any) -> list[str]:
    return evaluate_transaction(ctx, sensitivity=50).reasons


# ── R4: device sharing counts the account being evaluated ────────────────────

class DeviceSharingTests(APITestCase):
    def setUp(self):
        cache.clear()
        sandbox().reset()

    def pay_as(self, email: str, **overrides: Any) -> dict[str, Any]:
        self.client.force_authenticate(make_user(email))
        response = self.client.post(URL, payment_payload(device_id="shared-kiosk", **overrides), format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def test_third_account_on_a_device_trips_the_rule(self):
        first = self.pay_as("a@example.com")
        second = self.pay_as("b@example.com")
        third = self.pay_as("c@example.com")

        self.assertEqual(first["evaluation"]["context_snapshot"]["accounts_on_device_24h"], 1)
        self.assertEqual(second["evaluation"]["context_snapshot"]["accounts_on_device_24h"], 2)
        self.assertNotIn("device_sharing", codes_of(second))
        self.assertEqual(third["evaluation"]["context_snapshot"]["accounts_on_device_24h"], 3)
        self.assertIn("device_sharing", codes_of(third))

    def test_an_account_reusing_its_own_device_is_one_account(self):
        self.pay_as("solo@example.com")
        self.client.post(URL, payment_payload(device_id="shared-kiosk"), format="json")
        again = self.client.post(URL, payment_payload(device_id="shared-kiosk"), format="json").json()
        self.assertEqual(again["evaluation"]["context_snapshot"]["accounts_on_device_24h"], 1)


# ── R15: attempts and successes are different counts ─────────────────────────

class HistoryCountTests(APITestCase):
    def setUp(self):
        cache.clear()
        sandbox().reset()
        self.user = make_user()
        self.client.force_authenticate(self.user)

    def pay(self, **overrides: Any) -> dict[str, Any]:
        response = self.client.post(URL, payment_payload(**overrides), format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def test_failed_attempts_are_history_but_not_successes(self):
        for _ in range(2):
            self.assertEqual(self.pay(sandbox_behavior="decline")["status"], "failed")
        snapshot = self.pay()["evaluation"]["context_snapshot"]
        self.assertEqual(snapshot["historical_txn_count"], 2)
        self.assertEqual(snapshot["successful_txn_count"], 0)
        self.assertIsNone(snapshot["historical_avg_amount"])

        snapshot = self.pay()["evaluation"]["context_snapshot"]
        self.assertEqual((snapshot["historical_txn_count"], snapshot["successful_txn_count"]), (3, 1))

    def test_failed_attempts_do_not_earn_trust_or_a_reliable_average(self):
        for _ in range(4):
            self.pay(sandbox_behavior="decline", amount="5.00")
        # Four attempts, none successful: a big purchase is still "no spending history".
        body = self.pay(amount="1500.00")
        self.assertIn("first_transaction_large", codes_of(body))
        self.assertNotIn("amount_vs_history", codes_of(body))
        self.assertNotIn("consistent_history", codes_of(body))


class HistoryRuleMappingTests(SimpleTestCase):
    """Which rule reads which count."""

    def test_average_is_trusted_on_the_number_of_successful_payments(self):
        many_attempts_one_success = context(historical_txn_count=10, successful_txn_count=1, historical_avg_amount=Decimal("5.00"), amount=Decimal("60.00"))
        self.assertNotIn("amount_vs_history", reasons_of(many_attempts_one_success))
        self.assertNotIn("new_merchant_large", reasons_of(context(historical_txn_count=10, successful_txn_count=1, historical_avg_amount=Decimal("5.00"), amount=Decimal("60.00"), merchant_seen_count=0)))
        enough = context(historical_txn_count=10, successful_txn_count=3, historical_avg_amount=Decimal("5.00"), amount=Decimal("60.00"))
        self.assertIn("amount_vs_history", reasons_of(enough))

    def test_large_first_purchase_means_no_successful_payment_yet(self):
        retry_after_blocks = context(historical_txn_count=3, successful_txn_count=0, historical_avg_amount=None, amount=Decimal("1500.00"))
        decision = evaluate_transaction(retry_after_blocks, sensitivity=50)
        factor = next(f for f in decision.factors if f.code == "first_transaction_large")
        self.assertEqual(factor.points, 18)
        self.assertIn("no successful payments", factor.label)
        brand_new = context(historical_txn_count=0, successful_txn_count=0, historical_avg_amount=None, amount=Decimal("250.00"))
        factor = next(f for f in evaluate_transaction(brand_new, sensitivity=50).factors if f.code == "first_transaction_large")
        self.assertEqual((factor.points, factor.label), (10, "Large first transaction with no spending history"))
        spender = context(historical_txn_count=2, successful_txn_count=1, historical_avg_amount=Decimal("20.00"), amount=Decimal("1500.00"))
        self.assertNotIn("first_transaction_large", reasons_of(spender))

    def test_new_device_reads_prior_attempts_and_trust_reads_successes(self):
        hopping_after_blocks = context(historical_txn_count=2, successful_txn_count=0, historical_avg_amount=None, device_seen_count=0)
        self.assertIn("new_device", reasons_of(hopping_after_blocks))
        self.assertNotIn("new_device", reasons_of(context(historical_txn_count=0, successful_txn_count=0, historical_avg_amount=None, device_seen_count=0)))
        attempts_only = context(historical_txn_count=12, successful_txn_count=0, historical_avg_amount=None)
        self.assertNotIn("trusted_customer", reasons_of(attempts_only))
        self.assertIn("trusted_customer", reasons_of(context(historical_txn_count=12, successful_txn_count=12)))


# ── R11: amount rules compare in one currency ────────────────────────────────

class CurrencyNormalisationTests(SimpleTestCase):
    def test_reference_table_covers_exactly_the_supported_currencies(self):
        self.assertEqual(set(USD_REFERENCE_RATES), set(SUPPORTED_CURRENCIES))
        self.assertEqual(USD_REFERENCE_RATES["USD"], Decimal("1"))
        self.assertEqual(to_usd(Decimal("2000"), "JPY"), Decimal("13.40"))
        self.assertEqual(to_usd(Decimal("10.00"), "XXX"), Decimal("10.00"))

    def test_two_thousand_yen_is_not_an_extreme_amount(self):
        self.assertNotIn("extreme_amount", reasons_of(context(amount=Decimal("2000"), currency="JPY", historical_currencies=("JPY",))))
        self.assertIn("extreme_amount", reasons_of(context(amount=Decimal("2000"), currency="USD")))

    def test_thresholds_apply_to_the_usd_value_in_every_currency(self):
        cases = {
            # currency: (below 1,000 USD, between 1,000 and 2,000 USD, above 2,000 USD)
            "JPY": ("140000", "160000", "310000"),
            "INR": ("80000", "90000", "170000"),
            "EUR": ("900", "1000", "1900"),
            "GBP": ("780", "800", "1600"),
            "CAD": ("1300", "1400", "2800"),
            "AUD": ("1500", "1600", "3100"),
            "USD": ("999.99", "1000", "2000"),
        }
        self.assertEqual(set(cases), set(SUPPORTED_CURRENCIES))
        for currency, (small, large, extreme) in cases.items():
            def points(amount: str) -> int | None:
                ctx = context(amount=Decimal(amount), currency=currency, historical_currencies=(currency,))
                return next((f.points for f in evaluate_transaction(ctx, sensitivity=50).factors if f.code == "extreme_amount"), None)

            self.assertEqual((points(small), points(large), points(extreme)), (None, 12, 35), currency)

    def test_first_purchase_and_card_testing_thresholds_are_normalised_too(self):
        new = dict(historical_txn_count=0, successful_txn_count=0, historical_avg_amount=None, account_age_days=0.2, device_seen_count=0)
        self.assertNotIn("first_transaction_large", reasons_of(context(amount=Decimal("20000"), currency="JPY", **new)))  # about 134 USD
        self.assertIn("first_transaction_large", reasons_of(context(amount=Decimal("40000"), currency="JPY", **new)))  # about 268 USD
        # 500 JPY is a tiny charge (about 3.35 USD): a burst of them is card testing.
        burst = context(amount=Decimal("500"), currency="JPY", txn_count_1h=6, txn_count_24h=6, **new)
        self.assertIn("card_testing", reasons_of(burst))
        self.assertNotIn("card_testing", reasons_of(context(amount=Decimal("900"), currency="JPY", txn_count_1h=6, txn_count_24h=6, **new)))  # about 6 USD

    def test_snapshot_and_factor_detail_say_what_was_compared(self):
        ctx = context(amount=Decimal("310000"), currency="JPY", historical_currencies=("JPY",), historical_avg_amount=Decimal("40.00"))
        snapshot = ctx.snapshot()
        self.assertEqual(snapshot["amount"], "310000")
        self.assertEqual(snapshot["amount_usd"], "2077.00")
        self.assertEqual(snapshot["usd_reference_rate"], "0.0067")
        self.assertEqual(snapshot["comparison_currency"], "USD")
        self.assertEqual(snapshot["amount_ratio"], "51.92")
        detail = next(f.detail for f in evaluate_transaction(ctx, sensitivity=50).factors if f.code == "extreme_amount")
        self.assertEqual(detail, {"amount": "310000", "currency": "JPY", "amount_usd": "2077.00"})


class LiveCurrencyTests(APITestCase):
    def setUp(self):
        cache.clear()
        sandbox().reset()
        self.client.force_authenticate(make_user())

    def pay(self, **overrides: Any) -> dict[str, Any]:
        response = self.client.post(URL, payment_payload(**overrides), format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def test_yen_payment_is_scored_at_its_dollar_value(self):
        body = self.pay(currency="JPY", amount="2000")
        self.assertEqual(body["decision"], "allow")
        self.assertNotIn("extreme_amount", codes_of(body))
        self.assertEqual(body["evaluation"]["context_snapshot"]["amount_usd"], "13.40")

    def test_average_is_comparable_across_currencies(self):
        for _ in range(3):
            self.pay(amount="40.00")
        same_value_in_yen = self.pay(currency="JPY", amount="6000")
        snapshot = same_value_in_yen["evaluation"]["context_snapshot"]
        self.assertEqual((snapshot["historical_avg_amount"], snapshot["amount_usd"], snapshot["amount_ratio"]), ("40.00", "40.20", "1.00"))
        self.assertNotIn("amount_vs_history", codes_of(same_value_in_yen))
        self.assertIn("currency_mismatch", codes_of(same_value_in_yen))

    def test_a_yen_history_does_not_hide_a_large_dollar_payment(self):
        for _ in range(3):
            self.pay(currency="JPY", amount="6000")
        ordinary = self.pay(currency="JPY", amount="6000")
        self.assertEqual(ordinary["evaluation"]["context_snapshot"]["historical_avg_amount"], "40.20")
        self.assertNotIn("amount_vs_history", codes_of(ordinary))
        jump = self.pay(currency="USD", amount="500.00")
        self.assertIn("amount_vs_history", codes_of(jump))
        self.assertEqual(jump["evaluation"]["context_snapshot"]["amount_sum_24h"], "160.80")


# ── R5: investigator routing, grounding and scope ────────────────────────────

class ClassifierTests(SimpleTestCase):
    def intent(self, question: str) -> str:
        return investigator.classify(question).name

    def test_words_inside_other_words_do_not_route(self):
        # "ring" inside monitoring / during
        self.assertEqual(self.intent("What needs monitoring today?"), "summary")
        self.assertEqual(self.intent("Which payments failed during checkout?"), "summary")
        # "top" inside stopped, "cause" inside because
        self.assertEqual(self.intent("Has the spike stopped?"), "blocked_trend")
        self.assertEqual(self.intent("Blocks went up because of the sale, is the trend still rising?"), "blocked_trend")
        self.assertEqual(self.intent("Payments stopped because the site was down"), "summary")
        # "change" inside exchange
        self.assertEqual(self.intent("Any currency exchange issues?"), "summary")

    def test_specific_phrases_beat_generic_words(self):
        self.assertEqual(self.intent("Is there a shared device pattern?"), "risk_pattern")
        self.assertEqual(self.intent("shared device pattern"), "risk_pattern")
        self.assertEqual(self.intent("What are the top risk transactions?"), "high_risk_transactions")
        self.assertEqual(self.intent("Top reasons for blocks"), "top_block_reasons")
        self.assertEqual(self.intent("Show devices on my account"), "suspicious_new_device")

    def test_whole_words_still_route_in_every_inflection(self):
        cases = {
            "Is a fraud ring operating?": "risk_pattern",
            "Any card-testing bursts?": "risk_pattern",
            "multi-account abuse this week?": "risk_pattern",
            "What caused the blocks?": "top_block_reasons",
            "What is the top factor?": "top_block_reasons",
            "Did anything change this week?": "blocked_trend",
            "Are declines increasing?": "blocked_trend",
            "How many payments are being held?": "review_queue",
            "Unrecognised devices today": "suspicious_new_device",
            "Riskiest payments this month": "high_risk_transactions",
        }
        for question, expected in cases.items():
            self.assertEqual(self.intent(question), expected, question)

    def test_seven_intents_and_windows_are_unchanged(self):
        self.assertEqual(len(investigator.INTENTS), 7)
        self.assertEqual(set(investigator.QUERY_PLANS), set(investigator.INTENTS))
        for question in investigator.SUGGESTED_QUESTIONS:
            self.assertNotEqual(self.intent(question), "summary", question)
        self.assertEqual(investigator.classify("blocks in the last 7 days").window_hours, 168)
        self.assertEqual(investigator.classify("anything in the past-hour?").window_hours, 1)
        self.assertEqual(investigator.classify("what a birthday").window_hours, 24)
        self.assertTrue(investigator.classify("show simulations from the fraud-lab").include_simulation)
        self.assertFalse(investigator.classify("show stimulating patterns").include_simulation)


class GroundingTests(SimpleTestCase):
    def setUp(self):
        self.base = investigator.Explanation(
            answer="",
            facts=[
                "In the last 24 hours: 3 transactions, 1 blocked, 0 held for review, 2 allowed.",
                "The highest risk score in the last 24 hours was 91 on txn_01M46VQ5TKHZ2KGT97REPVRAYZ (block).",
            ],
            inferences=["A device shared by three or more accounts in a day is consistent with a coordinated ring."],
        )

    def test_spelled_out_numbers_must_be_in_the_facts(self):
        self.assertFalse(investigator._grounded("There were five blocked payments.", self.base))
        self.assertFalse(investigator._grounded("Twenty-five payments were blocked.", self.base))
        self.assertFalse(investigator._grounded("A hundred transactions were analysed.", self.base))
        self.assertTrue(investigator._grounded("There were three transactions and one blocked payment.", self.base))
        self.assertTrue(investigator._grounded("Two were allowed over twenty-four hours.", self.base))

    def test_ordinary_words_are_not_mistaken_for_numbers(self):
        for text in (
            "The highest-scoring one was blocked; no one else was affected.",
            "One of them was held, and it is the one worth a second look.",
            "Nothing unusual: no blocks in the window.",
            "More than a fifth of traffic was blocked, a third time this has happened.",
            "Hundreds of students shop here, so tens of signals are normal.",
        ):
            self.assertTrue(investigator._grounded(text, self.base), text)

    def test_digits_ids_and_formats(self):
        self.assertFalse(investigator._grounded("There were 999 blocked payments.", self.base))
        self.assertTrue(investigator._grounded("3.0 transactions, 91 at most.", self.base))
        self.assertTrue(investigator._grounded("The riskiest is txn_01M46VQ5TKHZ2KGT97REPVRAYZ at 91.", self.base))
        self.assertFalse(investigator._grounded("The riskiest is txn_01M46VQ5TKHZ2KGT97REPVRAYY at 91.", self.base))
        # Digits inside an id are not counts: "46" appears in the id, not in the facts.
        self.assertFalse(investigator._grounded("46 payments were blocked.", self.base))
        thousands = investigator.Explanation(answer="", facts=["1200 transactions in the last 7 days."])
        self.assertTrue(investigator._grounded("About 1,200 transactions in 7 days.", thousands))


class PatternScopeTests(TestCase):
    def setUp(self):
        cache.clear()
        sandbox().reset()
        self.customers = [make_user(f"ring-{i}@example.com") for i in range(3)]
        for customer in self.customers:
            create_payment(user=customer, data=PaymentInput("mkt", Decimal("20"), "USD", "card", "ring-device"))

    def test_staff_get_the_device_sharing_finding(self):
        record = investigator.investigate(user=make_user("staff@example.com", staff=True), question="Is there a shared device pattern today?")
        shared = record.results["patterns"]["shared_devices"]
        self.assertEqual([(d["device_id"], d["accounts"]) for d in shared], [("ring-device", 3)])
        self.assertTrue(any("ring-device" in fact for fact in record.facts))

    def test_customers_are_not_shown_an_empty_device_sharing_finding(self):
        record = investigator.investigate(user=self.customers[0], question="Is there a shared device pattern today?")
        self.assertEqual(record.intent, "risk_pattern")
        self.assertNotIn("shared_devices", record.results["patterns"])
        self.assertFalse(any("device-sharing" in fact for fact in record.facts), record.facts)
        self.assertIn("No velocity or card-testing pattern was found in your own payments.", record.facts)


# ── R6: the demo seed is what the README says it is ──────────────────────────

class DemoSeedTests(TestCase):
    def setUp(self):
        cache.clear()
        sandbox().reset()

    def seed(self, email: str, **options: Any) -> tuple[Any, str]:
        user = make_user(email, days_old=options.pop("days_old", 200))
        out = io.StringIO()
        call_command("seed_risk_demo", email=user.email, skip_simulations=True, stdout=out, **options)
        return user, out.getvalue()

    def rows(self, user: Any) -> list[PaymentTransaction]:
        return list(PaymentTransaction.objects.filter(user=user, is_simulation=False).order_by("created_at", "id"))

    def codes(self, txn: PaymentTransaction) -> set[str]:
        return set(RiskFactor.objects.filter(evaluation__transaction=txn, points__gt=0).values_list("code", flat=True))

    def test_composition_is_exact(self):
        user, output = self.seed("demo@example.com")
        rows = self.rows(user)
        profiles = [r.metadata["profile"] for r in rows]
        self.assertEqual(profiles[:24], ["ordinary"] * 24)
        self.assertEqual(profiles[24:], ["large_purchase", "new_device", "processor_decline"] + ["card_testing_burst"] * 6)
        self.assertIn("payments created: 33 (24 ordinary, 1 large purchase, 1 new-device purchase, 1 processor decline, 1 card-testing burst of 6)", output)

        by_profile = {p: [r for r in rows if r.metadata["profile"] == p] for p in set(profiles)}
        large, = by_profile["large_purchase"]
        self.assertIn(large.decision, ("review", "block"))
        self.assertTrue({"amount_vs_history", "extreme_amount"} <= self.codes(large))
        new_device, = by_profile["new_device"]
        self.assertIn("new_device", self.codes(new_device))
        declined, = by_profile["processor_decline"]
        self.assertEqual((declined.decision, declined.status), ("allow", "failed"))
        self.assertTrue(declined.failure_reason.startswith("permanent:"))
        burst = by_profile["card_testing_burst"]
        self.assertTrue(all(r.amount < 5 for r in burst))
        self.assertLessEqual(burst[-1].created_at - burst[0].created_at, timedelta(minutes=15))
        self.assertIn("card_testing", self.codes(burst[-1]))
        self.assertEqual(burst[-1].decision, "block")
        # Exactly one of each: no other row carries these signatures.
        self.assertEqual(sum("card_testing" in self.codes(r) for r in rows if r.metadata["profile"] != "card_testing_burst"), 0)
        self.assertEqual(PaymentTransaction.objects.filter(user=user, status="failed").count(), 1)
        self.assertEqual(sum(r.amount >= 1000 for r in rows), 1)

    def test_ordinary_rows_are_backdated_and_score_as_normal_activity(self):
        user, _ = self.seed("demo@example.com")
        ordinary = [r for r in self.rows(user) if r.metadata["profile"] == "ordinary"]
        now = timezone.now()
        self.assertTrue(all(r.created_at <= now - timedelta(hours=24) for r in ordinary))
        self.assertGreaterEqual(len({r.created_at.date() for r in ordinary}), 11)
        self.assertTrue(all((r.decision, r.status, r.risk_score) == ("allow", "completed", 0) for r in ordinary), [(r.risk_score, self.codes(r)) for r in ordinary])
        self.assertEqual({code for r in ordinary for code in self.codes(r)}, set())
        # The events of a backdated payment carry its time, not the seed's.
        first = ordinary[0]
        stamps = set(PaymentEvent.objects.filter(entity_id=first.public_id).values_list("occurred_at", flat=True))
        self.assertEqual(stamps, {first.created_at})

    def test_two_runs_with_one_seed_make_the_same_decisions(self):
        def signature(user: Any) -> list[tuple[Any, ...]]:
            return [(r.metadata["profile"], r.merchant_id, r.amount, r.decision, r.risk_score, r.status) for r in self.rows(user)]

        first, _ = self.seed("one@example.com")
        second, _ = self.seed("two@example.com")
        self.assertEqual(signature(first), signature(second))

    def test_seeding_twice_does_not_double_the_history(self):
        user, _ = self.seed("demo@example.com", payments=6)
        out = io.StringIO()
        call_command("seed_risk_demo", email=user.email, payments=6, skip_simulations=True, stdout=out)
        self.assertEqual(len(self.rows(user)), 15)
        self.assertIn("already seeded", out.getvalue())
        agent = AgentPolicy.objects.get(user=user)
        self.assertEqual(AgentTransaction.objects.filter(agent=agent).count(), 4)

    def test_a_young_account_is_aged_so_its_history_is_coherent(self):
        user, output = self.seed("fresh@example.com", days_old=0)
        user.refresh_from_db()
        self.assertLessEqual(user.created_at, timezone.now() - timedelta(days=119))
        self.assertIn("account aged to 120 days", output)
        self.assertFalse(RiskFactor.objects.filter(evaluation__transaction__user=user, code__in=["very_new_account", "new_account"]).exists())
        untouched, output = self.seed("asis@example.com", days_old=0, account_age_days=0)
        untouched.refresh_from_db()
        self.assertGreater(untouched.created_at, timezone.now() - timedelta(days=1))
        self.assertNotIn("account aged", output)

    def test_too_few_ordinary_payments_is_an_error(self):
        user = make_user("tiny@example.com")
        with self.assertRaises(CommandError):
            call_command("seed_risk_demo", email=user.email, payments=2, skip_simulations=True, stdout=io.StringIO())
        self.assertEqual(PaymentTransaction.objects.filter(user=user).count(), 0)

    def test_docstring_and_code_agree_on_the_fraud_lab_runs(self):
        from risk.management.commands import seed_risk_demo

        self.assertEqual(len(seed_risk_demo.SIMULATED_SCENARIOS), 3)
        self.assertIn("three Fraud Lab runs", seed_risk_demo.__doc__ or "")
        self.assertNotIn("one Fraud Lab run per scenario", seed_risk_demo.__doc__ or "")
        user = make_user("lab@example.com")
        with mock.patch("risk.management.commands.seed_risk_demo.run_scenario", wraps=seed_risk_demo.run_scenario) as runs:
            call_command("seed_risk_demo", email=user.email, payments=4, stdout=io.StringIO())
        self.assertEqual([c.kwargs["scenario"] for c in runs.call_args_list], list(seed_risk_demo.SIMULATED_SCENARIOS))
        self.assertEqual(SimulationRun.objects.filter(user=user).count(), 3)


# ── R9: held agent attempts can be resolved; the budget day is the campus day ─

def make_agent(user: Any, **overrides: Any) -> AgentPolicy:
    fields: dict[str, Any] = dict(
        name="Restock agent", daily_limit=Decimal("100"), transaction_limit=Decimal("80"),
        requires_approval_above=Decimal("30"), allowed_categories=["supplies"], blocked_merchants=[],
    )
    fields.update(overrides)
    return AgentPolicy.objects.create(user=user, **fields)


class AgentResolutionTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.owner = make_user()
        self.staff = make_user("staff@example.com", staff=True)
        self.agent = make_agent(self.owner)
        self.client.force_authenticate(self.owner)
        self.base = f"/api/risk/agents/{self.agent.public_id}/transactions"

    def attempt(self, amount: str) -> dict[str, Any]:
        response = self.client.post(self.base, {"amount": amount, "category": "supplies", "merchant_id": "mkt_supplies"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def resolve(self, attempt_id: int, **body: Any) -> Any:
        return self.client.post(f"{self.base}/{attempt_id}/resolve", body, format="json")

    def test_rejecting_a_hold_frees_the_budget_it_reserved(self):
        held = self.attempt("70")
        self.assertEqual((held["decision"], held["counts_toward_spend"]), ("review", True))
        self.assertEqual(self.attempt("40")["decision"], "block")  # 70 reserved + 40 > 100

        response = self.resolve(held["attempt"]["id"], approve=False, note="Not this week")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((body["decision"], body["counts_toward_spend"]), ("block", False))
        self.assertEqual(body["reasons"][-1]["code"], "manual_rejection")
        self.assertEqual(spent_today(self.agent), Decimal("0.00"))
        self.assertEqual(self.attempt("25")["decision"], "allow")

    def test_approving_a_hold_keeps_it_counted(self):
        held = self.attempt("70")
        body = self.resolve(held["attempt"]["id"], approve=True).json()
        self.assertEqual((body["decision"], body["counts_toward_spend"]), ("allow", True))
        self.assertEqual(body["reasons"][-1], {"code": "manual_approval", "outcome": "pass", "text": "Approved by owner after review.", "resolved_by": "owner"})
        self.assertEqual(spent_today(self.agent), Decimal("70.00"))
        self.assertEqual(self.attempt("40")["decision"], "block")

    def test_response_has_the_shape_of_an_attempts_list_item(self):
        held = self.attempt("70")
        resolved = self.resolve(held["attempt"]["id"], approve=True).json()
        listed = self.client.get(self.base).json()["results"][0]
        self.assertEqual(resolved, listed)
        self.assertEqual(
            set(resolved),
            {"id", "amount", "currency", "category", "merchant_id", "description", "decision", "reasons", "risk_score", "counts_toward_spend", "created_at"},
        )

    def test_only_held_attempts_can_be_resolved_and_only_once(self):
        allowed = self.attempt("20")
        self.assertEqual(self.resolve(allowed["attempt"]["id"], approve=True).status_code, 409)
        held = self.attempt("60")
        self.assertEqual(self.resolve(held["attempt"]["id"], approve=True).status_code, 200)
        again = self.resolve(held["attempt"]["id"], approve=False)
        self.assertEqual(again.status_code, 409)
        self.assertIn("detail", again.json())
        self.assertEqual(AgentTransaction.objects.get(pk=held["attempt"]["id"]).decision, "allow")

    def test_validation_and_addressing(self):
        held = self.attempt("60")
        attempt_id = held["attempt"]["id"]
        self.assertEqual(self.resolve(attempt_id).status_code, 400)  # approve is required
        self.assertEqual(self.resolve(attempt_id, approve="maybe").status_code, 400)
        self.assertEqual(self.resolve(attempt_id, approve=True, note="x" * 501).status_code, 400)
        self.assertEqual(self.resolve(attempt_id + 999, approve=True).status_code, 404)
        # An attempt cannot be reached through another agent, even one's own.
        other_agent = make_agent(self.owner, name="Other")
        wrong = self.client.post(f"/api/risk/agents/{other_agent.public_id}/transactions/{attempt_id}/resolve", {"approve": True}, format="json")
        self.assertEqual(wrong.status_code, 404)
        self.assertEqual(AgentTransaction.objects.get(pk=attempt_id).decision, "review")

    def test_other_customers_cannot_resolve_but_staff_can(self):
        held = self.attempt("60")
        attempt_id = held["attempt"]["id"]
        self.client.force_authenticate(make_user("other@example.com"))
        self.assertEqual(self.resolve(attempt_id, approve=True).status_code, 404)
        self.client.force_authenticate(None)
        self.assertIn(self.resolve(attempt_id, approve=True).status_code, (401, 403))
        self.client.force_authenticate(self.staff)
        response = self.resolve(attempt_id, approve=False, note="Card flagged by the bank, do not tell the customer")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["reasons"][-1]["resolved_by"], "staff")

        entry = AuditLog.objects.get(action="agent.transaction_resolved")
        self.assertEqual((entry.actor_label, entry.target_id, entry.decision), (f"user:{self.staff.pk}", self.agent.public_id, "block"))
        self.assertEqual(entry.reason, "Card flagged by the bank, do not tell the customer")
        self.assertEqual(entry.metadata["outcome"], "rejected")
        event = PaymentEvent.objects.filter(event_type="agent.decision", entity_id=self.agent.public_id).order_by("-sequence")[0]
        self.assertEqual(event.payload, {"attempt_id": attempt_id, "decision": "block", "amount": "60.00", "category": "supplies", "risk_score": event.payload["risk_score"], "resolution": "rejected"})

        # The owner sees the outcome everywhere they can look, the note nowhere.
        self.client.force_authenticate(self.owner)
        visible = [self.client.get(self.base).json(), self.client.get("/api/risk/events").json(), self.client.get("/api/risk/audit").json()]
        self.assertNotIn("flagged by the bank", str(visible))
        self.assertNotIn(f"user:{self.staff.pk}", str(visible))
        self.assertEqual(visible[0]["results"][0]["decision"], "block")


class AgentBudgetDayTests(TestCase):
    # 02:00 UTC on 10 Sep: still 9 Sep (19:00) in Los Angeles, already 10 Sep (07:30) in Kolkata.
    NOW = datetime(2026, 9, 10, 2, 0, tzinfo=dt_timezone.utc)
    # 10:00 UTC on 9 Sep: "today" in Los Angeles (03:00), "yesterday" in Kolkata (15:30).
    EARLIER = datetime(2026, 9, 9, 10, 0, tzinfo=dt_timezone.utc)

    def spend(self, agent: AgentPolicy) -> None:
        AgentTransaction.objects.create(
            agent=agent, amount=Decimal("90"), currency="USD", category="supplies", merchant_id="m",
            decision="allow", counts_toward_spend=True, created_at=self.EARLIER,
        )

    def test_budget_resets_at_midnight_in_the_owners_campus(self):
        campus = Campus.objects.create(name="Kolkata Tech", slug="kolkata-tech", timezone="Asia/Kolkata")
        student = make_user("student@example.com")
        student.campus = campus
        student.save(update_fields=["campus"])
        agent = make_agent(student)
        self.spend(agent)
        self.assertEqual(spent_today(agent, self.NOW), Decimal("0.00"))
        _, verdict = evaluate_agent_transaction(agent=agent, amount=Decimal("20"), currency="USD", category="supplies", merchant_id="m", now=self.NOW)
        self.assertEqual(verdict.decision, "allow")

    def test_owner_without_a_campus_uses_the_project_zone(self):
        agent = make_agent(make_user("nocampus@example.com"))
        self.spend(agent)
        self.assertEqual(spent_today(agent, self.NOW), Decimal("90.00"))
        _, verdict = evaluate_agent_transaction(agent=agent, amount=Decimal("20"), currency="USD", category="supplies", merchant_id="m", now=self.NOW)
        self.assertEqual(verdict.decision, "block")

    def test_unknown_campus_zone_falls_back_instead_of_failing(self):
        campus = Campus.objects.create(name="Nowhere", slug="nowhere", timezone="Mars/Olympus_Mons")
        student = make_user("lost@example.com")
        student.campus = campus
        student.save(update_fields=["campus"])
        agent = make_agent(student)
        self.spend(agent)
        self.assertEqual(spent_today(agent, self.NOW), Decimal("90.00"))


class AgentHistoryTests(TestCase):
    def test_owner_trust_does_not_make_a_thin_agent_average_reliable(self):
        cache.clear()
        sandbox().reset()
        owner = make_user()
        for _ in range(4):
            create_payment(user=owner, data=PaymentInput("mkt", Decimal("30"), "USD", "card", "laptop"))
        agent = make_agent(owner, requires_approval_above=Decimal("80"))
        evaluate_agent_transaction(agent=agent, amount=Decimal("5"), currency="USD", category="supplies", merchant_id="m")
        # One approved 5.00 purchase is not a baseline, however good the owner's record.
        attempt, verdict = evaluate_agent_transaction(agent=agent, amount=Decimal("60"), currency="USD", category="supplies", merchant_id="m")
        self.assertEqual(verdict.decision, "allow")
        self.assertNotIn("amount_vs_history", str(attempt.reasons))
