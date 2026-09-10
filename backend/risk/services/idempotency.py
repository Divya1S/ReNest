"""Idempotent request handling for payment creation.

Design
    The client sends `Idempotency-Key: <opaque string>` with POST /api/risk/payments.
    The key is scoped to (user, endpoint) so two customers can never collide
    and a key cannot be replayed against a different endpoint.

    Flow, all inside one database transaction:

      1. INSERT the key row with status IN_PROGRESS and the SHA-256 of the
         canonical request body. The unique constraint on (user, scope, key)
         is the only lock: whichever request inserts first owns the key.
      2. If the INSERT fails, load the existing row and decide:
           expired                    -> reset it and take ownership
           different request hash     -> 422 idempotency_key_reused
                                         (same key, different payload: a bug
                                         on the client, never silently served)
           still IN_PROGRESS          -> 409 idempotency_in_progress with
                                         Retry-After; the concurrent original
                                         will finish and the retry replays it
           COMPLETED                  -> replay the stored response verbatim
                                         with Idempotent-Replayed: true
      3. Run the operation. On a 2xx or 4xx outcome the response is stored on
         the key (both are deterministic answers worth replaying). On an
         unexpected exception the row is deleted so the client's retry gets a
         fresh attempt rather than a cached failure.

    Keys expire after TTL_HOURS. After that the same key starts over; the
    stored response is gone and a new payment would be created, which is why
    the window is documented to clients.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from ..models import IdempotencyKey, IdempotencyStatus
from . import metrics

TTL_HOURS = 24
MAX_KEY_LENGTH = 255
SCOPE_PAYMENTS = "risk.payments.create"


class IdempotencyError(Exception):
    status_code = 400
    code = "idempotency_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class KeyReused(IdempotencyError):
    """Same key, different request body."""

    status_code = 422
    code = "idempotency_key_reused"


class KeyInProgress(IdempotencyError):
    """Another request with this key is still executing."""

    status_code = 409
    code = "idempotency_in_progress"


@dataclass(frozen=True)
class Replay:
    status_code: int
    body: Any


def request_hash(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def validate_key(raw: str | None) -> str | None:
    if raw is None:
        return None
    key = raw.strip()
    if not key:
        return None
    if len(key) > MAX_KEY_LENGTH:
        raise IdempotencyError(f"Idempotency-Key must be at most {MAX_KEY_LENGTH} characters.")
    return key


def acquire(*, user: Any, scope: str, key: str, payload: Any) -> IdempotencyKey | Replay:
    """Take ownership of a key, or return the stored response to replay."""
    digest = request_hash(payload)
    now = timezone.now()
    try:
        with transaction.atomic():
            return IdempotencyKey.objects.create(
                user=user,
                scope=scope,
                key=key,
                request_hash=digest,
                expires_at=now + timedelta(hours=TTL_HOURS),
            )
    except IntegrityError:
        pass

    with transaction.atomic():
        existing = IdempotencyKey.objects.select_for_update().get(user=user, scope=scope, key=key)
        if existing.expires_at <= now:
            existing.request_hash = digest
            existing.status = IdempotencyStatus.IN_PROGRESS
            existing.response_status = None
            existing.response_body = None
            existing.transaction = None
            existing.created_at = now
            existing.completed_at = None
            existing.expires_at = now + timedelta(hours=TTL_HOURS)
            existing.save()
            return existing
        if existing.request_hash != digest:
            metrics.increment("idempotency_conflicts_total")
            raise KeyReused(
                "This Idempotency-Key was already used with a different request body. "
                "Use a new key for a new payment."
            )
        if existing.status == IdempotencyStatus.IN_PROGRESS:
            raise KeyInProgress("A request with this Idempotency-Key is still being processed. Retry shortly.")
        metrics.increment("idempotent_replays_total")
        return Replay(status_code=existing.response_status or 200, body=existing.response_body)


def complete(record: IdempotencyKey, *, status_code: int, body: Any, transaction_obj: Any = None) -> None:
    record.status = IdempotencyStatus.COMPLETED
    record.response_status = status_code
    record.response_body = body
    record.transaction = transaction_obj
    record.completed_at = timezone.now()
    record.save(update_fields=["status", "response_status", "response_body", "transaction", "completed_at"])


def release(record: IdempotencyKey) -> None:
    """The operation raised unexpectedly: forget the key so a retry can run."""
    IdempotencyKey.objects.filter(pk=record.pk, status=IdempotencyStatus.IN_PROGRESS).delete()


def purge_expired(now: Any = None) -> int:
    deleted, _ = IdempotencyKey.objects.filter(expires_at__lte=now or timezone.now()).delete()
    return deleted
