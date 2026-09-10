"""Idempotency-Key semantics for POST /api/risk/payments."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest import mock

from django.utils import timezone
from rest_framework.test import APITestCase

from risk.models import IdempotencyKey, IdempotencyStatus, PaymentTransaction
from risk.services import idempotency

from .support import make_user, payment_payload

URL = "/api/risk/payments"


class IdempotencyTests(APITestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_authenticate(self.user)

    def post(self, key: str | None, **overrides: Any) -> Any:
        if key is None:
            return self.client.post(URL, payment_payload(**overrides), format="json")
        return self.client.post(URL, payment_payload(**overrides), format="json", HTTP_IDEMPOTENCY_KEY=key)

    def test_duplicate_request_replays_original_response(self):
        first = self.post("order-1")
        self.assertEqual(first.status_code, 201, first.content)
        second = self.post("order-1")
        self.assertEqual(second.status_code, 201)
        self.assertEqual(second["Idempotent-Replayed"], "true")
        self.assertEqual(second.json()["public_id"], first.json()["public_id"])
        self.assertEqual(PaymentTransaction.objects.count(), 1)
        self.assertNotIn("Idempotent-Replayed", first)

    def test_same_key_different_body_is_rejected(self):
        self.assertEqual(self.post("order-2").status_code, 201)
        conflict = self.post("order-2", amount="99.00")
        self.assertEqual(conflict.status_code, 422)
        self.assertEqual(conflict.json()["code"], "idempotency_key_reused")
        self.assertEqual(PaymentTransaction.objects.count(), 1)

    def test_concurrent_duplicate_gets_409_while_original_runs(self):
        record = idempotency.acquire(user=self.user, scope=idempotency.SCOPE_PAYMENTS, key="in-flight", payload=payment_payload())
        assert isinstance(record, IdempotencyKey)
        response = self.post("in-flight")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "idempotency_in_progress")
        self.assertEqual(response["Retry-After"], "1")
        self.assertEqual(PaymentTransaction.objects.count(), 0)
        # The original completes; the retry now replays it.
        idempotency.complete(record, status_code=201, body={"public_id": "txn_fake"})
        replay = self.post("in-flight")
        self.assertEqual(replay.status_code, 201)
        self.assertEqual(replay["Idempotent-Replayed"], "true")
        self.assertEqual(replay.json()["public_id"], "txn_fake")

    def test_expired_key_starts_over(self):
        first = self.post("expiring")
        IdempotencyKey.objects.filter(key="expiring").update(expires_at=timezone.now() - timedelta(seconds=1))
        second = self.post("expiring")
        self.assertEqual(second.status_code, 201)
        self.assertNotIn("Idempotent-Replayed", second)
        self.assertNotEqual(second.json()["public_id"], first.json()["public_id"])
        self.assertEqual(PaymentTransaction.objects.count(), 2)

    def test_keys_are_scoped_per_user(self):
        self.assertEqual(self.post("shared-key").status_code, 201)
        other = make_user("other@example.com")
        self.client.force_authenticate(other)
        response = self.post("shared-key")
        self.assertEqual(response.status_code, 201)
        self.assertNotIn("Idempotent-Replayed", response)
        self.assertEqual(PaymentTransaction.objects.count(), 2)

    def test_without_key_every_request_creates_a_payment(self):
        self.post(None)
        self.post(None)
        self.assertEqual(PaymentTransaction.objects.count(), 2)

    def test_key_length_limit(self):
        response = self.post("k" * 256)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "idempotency_error")

    def test_validation_failure_is_stored_and_replayed(self):
        bad = self.post("bad-body", amount="0")
        self.assertEqual(bad.status_code, 400)
        again = self.post("bad-body", amount="0")
        self.assertEqual(again.status_code, 400)
        self.assertEqual(again["Idempotent-Replayed"], "true")
        self.assertEqual(again.json(), bad.json())

    def test_request_hash_is_canonical(self):
        a = idempotency.request_hash({"b": 1, "a": [1, 2]})
        b = idempotency.request_hash({"a": [1, 2], "b": 1})
        self.assertEqual(a, b)
        self.assertNotEqual(a, idempotency.request_hash({"a": [2, 1], "b": 1}))

    def test_unexpected_failure_releases_key_so_retry_can_run(self):
        self.client.raise_request_exception = False
        with mock.patch("risk.views.payments.create_payment", side_effect=RuntimeError("boom")):
            crashed = self.post("crash")
        self.assertEqual(crashed.status_code, 500)
        self.assertFalse(IdempotencyKey.objects.filter(key="crash").exists())
        retry = self.post("crash")
        self.assertEqual(retry.status_code, 201)
        self.assertNotIn("Idempotent-Replayed", retry)

    def test_purge_expired_removes_only_expired_rows(self):
        self.post("fresh")
        self.post("stale")
        IdempotencyKey.objects.filter(key="stale").update(expires_at=timezone.now() - timedelta(hours=1))
        self.assertEqual(idempotency.purge_expired(), 1)
        self.assertEqual(set(IdempotencyKey.objects.values_list("key", flat=True)), {"fresh"})

    def test_completed_record_links_the_transaction(self):
        self.post("linked")
        record = IdempotencyKey.objects.get(key="linked")
        self.assertEqual(record.status, IdempotencyStatus.COMPLETED)
        self.assertEqual(record.response_status, 201)
        assert record.transaction is not None
        self.assertEqual(record.transaction.public_id, (record.response_body or {})["public_id"])
