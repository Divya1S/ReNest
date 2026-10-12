"""Risk Investigator: intent routing, allowed queries, grounding, provider fallback."""

from __future__ import annotations

import os
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from rest_framework.test import APITestCase

from risk.models import Investigation
from risk.services import investigator
from risk.services.payments import PaymentInput, create_payment

from .support import make_user


class ClassifyTests(APITestCase):
    def test_intents_and_windows(self):
        cases = {
            "Why did blocked transactions increase today?": ("blocked_trend", 24),
            "Which risk factors are driving blocks this week?": ("top_block_reasons", 168),
            "Show suspicious new device transactions in the last hour": ("suspicious_new_device", 1),
            "What is waiting in the review queue?": ("review_queue", 24),
            "Is there a card testing or velocity pattern this month?": ("risk_pattern", 720),
            "Which transactions scored highest?": ("high_risk_transactions", 24),
            "How are things looking?": ("summary", 24),
        }
        for question, (intent, hours) in cases.items():
            result = investigator.classify(question)
            self.assertEqual((result.name, result.window_hours), (intent, hours), question)
            self.assertFalse(result.include_simulation)
        self.assertTrue(investigator.classify("blocked simulation transactions in the fraud lab").include_simulation)


class InvestigateTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user()
        self.other = make_user("other@example.com")
        create_payment(user=self.user, data=PaymentInput("mkt", Decimal("20"), "USD", "card", "d1"))
        fresh = make_user("fresh@example.com", days_old=0)
        self.blocked = create_payment(user=fresh, data=PaymentInput("mkt", Decimal("2500"), "USD", "card", "new")).transaction
        self.other_txn = create_payment(user=self.other, data=PaymentInput("mkt", Decimal("30"), "USD", "card", "d2")).transaction

    def test_facts_match_the_database_and_are_scoped(self):
        staff = make_user("staff@example.com", staff=True)
        record = investigator.investigate(user=staff, question="Why did blocked transactions increase today?")
        self.assertEqual(record.intent, "blocked_trend")
        self.assertEqual(record.mode, "deterministic")
        self.assertEqual(record.results["decision_counts"]["current"]["block"], 1)
        self.assertEqual(record.results["decision_counts"]["current"]["total"], 3)
        self.assertIn("3 transactions, 1 blocked", record.facts[0])
        self.assertIn(self.blocked.public_id, record.evidence)
        self.assertTrue(record.inferences)
        self.assertEqual([q["name"] for q in record.queries], ["decision_counts", "top_factors", "high_risk_transactions"])

        mine = investigator.investigate(user=self.user, question="Which transactions scored highest today?")
        self.assertNotIn(self.blocked.public_id, mine.evidence)
        self.assertNotIn(self.other_txn.public_id, mine.evidence)
        self.assertEqual(mine.results["decision_counts"]["current"]["total"], 1)

    def test_every_intent_produces_an_answer(self):
        for question in investigator.SUGGESTED_QUESTIONS:
            record = investigator.investigate(user=self.user, question=question)
            self.assertTrue(record.answer)
            self.assertTrue(record.facts)

    def test_llm_explainer_falls_back_when_provider_fails(self):
        explainer = investigator.GeminiExplainer()
        with mock.patch("listings.ai_client.generate_text", side_effect=RuntimeError("quota")):
            record = investigator.investigate(user=self.user, question="summary", explainer=explainer)
        self.assertEqual(record.mode, "deterministic")

    def test_llm_answer_is_rejected_when_it_invents_numbers(self):
        explainer = investigator.GeminiExplainer()
        with mock.patch("listings.ai_client.generate_text", return_value="There were 999 blocked payments."):
            record = investigator.investigate(user=self.user, question="summary", explainer=explainer)
        self.assertEqual(record.mode, "deterministic")
        with mock.patch("listings.ai_client.generate_text", return_value="Nothing unusual: no blocks in the window."):
            with mock.patch("listings.ai_client._fast_model", return_value="test-model"):
                record = investigator.investigate(user=self.user, question="summary", explainer=explainer)
        self.assertEqual(record.mode, "llm")
        self.assertEqual(record.model_version, "gemini:test-model")
        self.assertEqual(record.answer, "Nothing unusual: no blocks in the window.")

    def test_explainer_selection_respects_environment(self):
        with mock.patch.dict(os.environ, {"RISK_INVESTIGATOR_MODE": "llm", "GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""}):
            self.assertEqual(investigator.get_explainer().mode, "deterministic")
        with mock.patch.dict(os.environ, {"RISK_INVESTIGATOR_MODE": "auto", "GEMINI_API_KEY": "test-key"}):
            self.assertEqual(investigator.get_explainer().mode, "llm")
        with mock.patch.dict(os.environ, {"RISK_INVESTIGATOR_MODE": "deterministic", "GEMINI_API_KEY": "test-key"}):
            self.assertEqual(investigator.get_explainer().mode, "deterministic")


class InvestigatorApiTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user()
        self.client.force_authenticate(self.user)

    def test_ask_list_and_detail(self):
        info = self.client.get("/api/risk/investigator").json()
        self.assertEqual(info["mode"], "deterministic")
        self.assertIn("decision_counts", info["allowed_queries"])
        response = self.client.post("/api/risk/investigations", {"question": "What is waiting in the review queue?"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["intent"], "review_queue")
        self.assertIsInstance(body["facts"], list)
        self.assertIsInstance(body["inferences"], list)
        self.assertEqual(self.client.get(f"/api/risk/investigations/{body['public_id']}").status_code, 200)
        self.assertEqual(self.client.get("/api/risk/investigations").json()["count"], 1)
        self.client.force_authenticate(make_user("other@example.com"))
        self.assertEqual(self.client.get(f"/api/risk/investigations/{body['public_id']}").status_code, 404)

    def test_question_validation(self):
        self.assertEqual(self.client.post("/api/risk/investigations", {"question": ""}, format="json").status_code, 400)
        self.assertEqual(self.client.post("/api/risk/investigations", {"question": "x" * 600}, format="json").status_code, 400)
        self.assertEqual(Investigation.objects.count(), 0)
