"""Authentication, authorisation and abuse-resistance across the risk API."""

from __future__ import annotations

from unittest import mock

from django.core.cache import cache
from rest_framework.test import APIClient, APITestCase

from risk.throttles import SimulationThrottle

from .support import make_user, payment_payload

READ_ENDPOINTS = (
    "/api/risk/payments", "/api/risk/overview", "/api/risk/feed", "/api/risk/distribution", "/api/risk/policy",
    "/api/risk/rules", "/api/risk/health", "/api/risk/metrics", "/api/risk/metrics/prometheus", "/api/risk/scenarios",
    "/api/risk/simulations", "/api/risk/investigator", "/api/risk/investigations", "/api/risk/agents",
    "/api/risk/events", "/api/risk/audit",
)
WRITE_ENDPOINTS = (
    ("post", "/api/risk/payments"), ("put", "/api/risk/policy"), ("post", "/api/risk/simulations"),
    ("post", "/api/risk/investigations"), ("post", "/api/risk/agents"), ("post", "/api/risk/events/dispatch"),
)


class AuthenticationTests(APITestCase):
    def test_anonymous_requests_are_rejected_everywhere(self):
        client = APIClient()
        for path in READ_ENDPOINTS:
            self.assertIn(client.get(path).status_code, (401, 403), path)
        for method, path in WRITE_ENDPOINTS:
            self.assertIn(getattr(client, method)(path, {}, format="json").status_code, (401, 403), path)

    def test_staff_only_endpoints_reject_customers(self):
        self.client.force_authenticate(make_user())
        self.assertEqual(self.client.put("/api/risk/policy", {"sensitivity": 10}, format="json").status_code, 403)
        self.assertEqual(self.client.post("/api/risk/events/dispatch").status_code, 403)
        self.assertEqual(self.client.post("/api/risk/events/evt_x/replay").status_code, 403)
        self.assertEqual(self.client.get("/api/risk/metrics/prometheus").status_code, 403)
        self.assertEqual(self.client.post("/api/risk/payments/txn_x/review", {"approve": True}, format="json").status_code, 403)

    def test_session_auth_requires_csrf_for_writes(self):
        user = make_user()
        client = APIClient(enforce_csrf_checks=True)
        client.login(email=user.email, password="secretpass123")
        response = client.post("/api/risk/payments", payment_payload(), format="json")
        self.assertEqual(response.status_code, 403)


class AbuseTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.client.force_authenticate(make_user())

    def test_simulation_throttle_limits_writes_but_not_reads(self):
        with mock.patch.object(SimulationThrottle, "get_rate", return_value="1/hour"):
            first = self.client.post("/api/risk/simulations", {"scenario": "card_testing", "count": 50}, format="json")
            self.assertEqual(first.status_code, 201)
            second = self.client.post("/api/risk/simulations", {"scenario": "card_testing", "count": 50}, format="json")
            self.assertEqual(second.status_code, 429)
            self.assertEqual(self.client.get("/api/risk/simulations").status_code, 200)

    def test_query_parameters_are_bounded(self):
        self.assertEqual(self.client.get("/api/risk/overview?hours=999999").json()["window_hours"], 720)
        self.assertEqual(self.client.get("/api/risk/overview?hours=abc").json()["window_hours"], 24)
        self.assertEqual(len(self.client.get("/api/risk/feed?limit=9999").json()["results"]), 0)
        self.assertEqual(self.client.get("/api/risk/payments?page_size=100000").status_code, 200)
        self.assertEqual(self.client.get("/api/risk/payments?since=not-a-date").status_code, 200)

    def test_oversized_and_wrong_content_types_are_rejected(self):
        response = self.client.post("/api/risk/payments", "merchant_id=x&amount=1", content_type="text/plain")
        self.assertIn(response.status_code, (400, 415))
        huge = payment_payload(metadata={"blob": "x" * 5000})
        self.assertEqual(self.client.post("/api/risk/payments", huge, format="json").status_code, 400)

    def test_agent_count_is_capped(self):
        from risk.views.agents import MAX_AGENTS_PER_USER

        body = {"name": "A", "daily_limit": "10", "transaction_limit": "5", "requires_approval_above": "5"}
        for _ in range(MAX_AGENTS_PER_USER):
            self.assertEqual(self.client.post("/api/risk/agents", body, format="json").status_code, 201)
        self.assertEqual(self.client.post("/api/risk/agents", body, format="json").status_code, 400)
