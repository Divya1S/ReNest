"""Agent Payment Sandbox: the policy engine and its API."""

from __future__ import annotations

from decimal import Decimal

from django.core.cache import cache
from rest_framework.test import APITestCase

from risk.models import AgentPolicy, AgentTransaction, AuditLog, PaymentEvent
from risk.services.agents import evaluate_agent_transaction, spent_today
from risk.services.policy import update_sensitivity

from .support import make_user


def make_agent(user, **overrides):
    fields = dict(
        name="Restock agent", daily_limit=Decimal("100"), transaction_limit=Decimal("50"),
        requires_approval_above=Decimal("30"), allowed_categories=["supplies", "food"], blocked_merchants=["mkt_tickets"],
    )
    fields.update(overrides)
    return AgentPolicy.objects.create(user=user, **fields)


class PolicyEngineTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user()
        self.agent = make_agent(self.user)

    def attempt(self, amount, category="supplies", merchant="mkt_supplies", currency="USD", agent=None):
        _, verdict = evaluate_agent_transaction(agent=agent or self.agent, amount=Decimal(str(amount)), currency=currency, category=category, merchant_id=merchant, actor=self.user)
        return verdict

    def codes(self, verdict):
        return [r["code"] for r in verdict.reasons if r["outcome"] != "pass"]

    def test_ordinary_purchase_is_allowed_and_counts_toward_spend(self):
        verdict = self.attempt(20)
        self.assertEqual(verdict.decision, "allow")
        self.assertEqual(self.codes(verdict), [])
        self.assertTrue(verdict.counts_toward_spend)
        self.assertEqual(verdict.remaining_today, Decimal("80"))
        self.assertEqual(spent_today(self.agent), Decimal("20"))

    def test_each_hard_rule_blocks(self):
        self.assertEqual(self.codes(self.attempt(20, category="electronics")), ["category_not_allowed"])
        self.assertEqual(self.codes(self.attempt(20, merchant="mkt_tickets")), ["merchant_blocked"])
        self.assertIn("transaction_limit", self.codes(self.attempt(55)))
        self.assertIn("currency_mismatch", self.codes(self.attempt(20, currency="EUR")))
        self.assertIn("invalid_amount", self.codes(self.attempt(0)))
        self.agent.active = False
        self.agent.save()
        self.assertIn("agent_inactive", self.codes(self.attempt(5)))
        self.assertEqual(spent_today(self.agent), Decimal("0"))

    def test_daily_limit_accumulates_across_allowed_and_reviewed_attempts(self):
        self.assertEqual(self.attempt(25).decision, "allow")
        held = self.attempt(45)  # above requires_approval_above
        self.assertEqual(held.decision, "review")
        self.assertIn("approval_required", self.codes(held))
        self.assertTrue(held.counts_toward_spend)
        self.assertEqual(spent_today(self.agent), Decimal("70"))
        blocked = self.attempt(40)
        self.assertEqual(blocked.decision, "block")
        self.assertIn("daily_limit", self.codes(blocked))
        self.assertFalse(blocked.counts_toward_spend)
        self.assertEqual(self.attempt(30).decision, "allow")
        self.assertEqual(spent_today(self.agent), Decimal("100"))
        self.assertEqual(self.attempt(1).decision, "block")

    def test_risk_engine_can_block_an_agent_purchase(self):
        owner = make_user("new-owner@example.com", days_old=0)
        big = make_agent(owner, name="Big", daily_limit=Decimal("5000"), transaction_limit=Decimal("5000"), requires_approval_above=Decimal("5000"), allowed_categories=[])
        update_sensitivity(actor=self.user, sensitivity=100)
        verdict = self.attempt(2400, agent=big, category="anything")
        self.assertEqual(verdict.decision, "block")
        self.assertIn("risk_block", self.codes(verdict))
        self.assertIsNotNone(verdict.risk_score)
        self.assertGreaterEqual(verdict.risk_score, 50)

    def test_attempts_are_audited_and_emit_events(self):
        self.attempt(20)
        entry = AuditLog.objects.get(action="agent.transaction_evaluated")
        self.assertEqual(entry.target_id, self.agent.public_id)
        self.assertEqual(entry.decision, "allow")
        self.assertTrue(PaymentEvent.objects.filter(event_type="agent.decision", entity_id=self.agent.public_id).exists())
        self.assertEqual(AgentTransaction.objects.filter(agent=self.agent).count(), 1)


class AgentApiTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user()
        self.client.force_authenticate(self.user)

    def test_create_update_and_attempt(self):
        payload = {
            "name": "Textbook agent", "daily_limit": "200", "transaction_limit": "80", "requires_approval_above": "60",
            "allowed_categories": ["Books", " supplies "], "blocked_merchants": ["mkt_scam"],
        }
        response = self.client.post("/api/risk/agents", payload, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        agent = response.json()
        self.assertEqual(agent["allowed_categories"], ["books", "supplies"])
        self.assertEqual(agent["spent_today"], "0.00")
        public_id = agent["public_id"]

        bad = self.client.post("/api/risk/agents", {**payload, "transaction_limit": "500"}, format="json")
        self.assertEqual(bad.status_code, 400)
        self.assertIn("transaction_limit", bad.json())

        patched = self.client.patch(f"/api/risk/agents/{public_id}", {"requires_approval_above": "10"}, format="json")
        self.assertEqual(patched.json()["requires_approval_above"], "10.00")

        attempt = self.client.post(f"/api/risk/agents/{public_id}/transactions", {"amount": "25", "category": "books", "merchant_id": "mkt_textbooks"}, format="json")
        self.assertEqual(attempt.status_code, 201, attempt.content)
        body = attempt.json()
        self.assertEqual(body["decision"], "review")
        self.assertIn("approval_required", [r["code"] for r in body["reasons"]])
        self.assertEqual(body["spent_today"], "25.00")
        history = self.client.get(f"/api/risk/agents/{public_id}/transactions").json()
        self.assertEqual(history["count"], 1)
        self.assertEqual(AuditLog.objects.filter(target_id=public_id).count(), 3)

    def test_agents_are_private_to_their_owner(self):
        public_id = self.client.post("/api/risk/agents", {"name": "A", "daily_limit": "10", "transaction_limit": "5", "requires_approval_above": "5"}, format="json").json()["public_id"]
        self.client.force_authenticate(make_user("other@example.com"))
        self.assertEqual(self.client.get(f"/api/risk/agents/{public_id}").status_code, 404)
        self.assertEqual(self.client.post(f"/api/risk/agents/{public_id}/transactions", {"amount": "1", "category": "x", "merchant_id": "m"}, format="json").status_code, 404)
        self.assertEqual(self.client.get("/api/risk/agents").json()["count"], 0)

    def test_attempt_validation(self):
        public_id = self.client.post("/api/risk/agents", {"name": "A", "daily_limit": "10", "transaction_limit": "5", "requires_approval_above": "5"}, format="json").json()["public_id"]
        self.assertEqual(self.client.post(f"/api/risk/agents/{public_id}/transactions", {"amount": "abc", "category": "x", "merchant_id": "m"}, format="json").status_code, 400)
        self.assertEqual(self.client.post(f"/api/risk/agents/{public_id}/transactions", {"amount": "1"}, format="json").status_code, 400)
