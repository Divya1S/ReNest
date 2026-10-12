"""Idempotent request handling for payment creation.

Design
    The client sends `Idempotency-Key: <opaque string>` with POST /api/risk/payments.
    The key is scoped to (user, endpoint) so two customers can never collide
    and a key cannot be replayed against a different endpoint.

    What makes two requests "the same request" is the fingerprint of the
    validated, normalised payload (`payment_request_fingerprint`), not the
    bytes the client happened to send: "42.50" and "42.5" are one payment.

    A key row moves through three states:

      IN_PROGRESS, unbound   a request owns the key and has not committed a
                             payment yet
      IN_PROGRESS, bound     the payment is committed; the response has not
                             been stored yet
      COMPLETED              the response is stored and replays verbatim

    The rules that keep "one key, one payment" true under crashes:

      1. `acquire` INSERTs the row with a fresh owner token. The unique
         constraint on (user, scope, key) decides who owns a new key.
      2. `bind` attaches the payment to the key INSIDE the database
         transaction that inserts the payment. Key and payment become
         durable together or not at all, so there is never a committed
         payment whose key is unbound.
      3. Every write by the owning request (`bind`, `complete`, `release`)
         is a conditional UPDATE/DELETE on its owner token. The token is a
         fencing token: a request that lost ownership changes nothing.
      4. `release` only ever removes an unbound key. Whatever goes wrong
         after the payment committed, the key keeps pointing at it.

    What a second request with the same key gets:

      expired                    -> the key starts over (new owner)
      different fingerprint      -> 422 idempotency_key_reused
                                    (same key, different payload: a bug on
                                    the client, never silently served)
      COMPLETED                  -> the stored response, verbatim, with
                                    Idempotent-Replayed: true
      IN_PROGRESS, bound         -> `Recovered`: the payment exists but its
                                    response was never stored (the original
                                    is still capturing, or died). The caller
                                    renders the payment as it is NOW, so the
                                    replay is never a stale provisional body
      IN_PROGRESS, unbound,
        within the lease         -> 409 idempotency_in_progress with
                                    Retry-After; the original is running
      IN_PROGRESS, unbound,
        lease run out            -> the original died before committing (or
                                    is stuck); the retry takes the key over
                                    with a new owner token. If the original
                                    is somehow still alive its `bind` now
                                    matches no row and its payment rolls back

    On a 2xx or 4xx outcome the response is stored on the key (both are
    deterministic answers worth replaying). On an unexpected exception
    before commit the row is deleted so the retry is a real attempt.

    Keys expire after TTL_HOURS. After that the same key starts over; the
    stored response is gone and a new payment would be created, which is why
    the window is documented to clients.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any, Mapping

from django.db import IntegrityError, transaction
from django.utils import timezone

from ..models import SUPPORTED_CURRENCIES, IdempotencyKey, IdempotencyStatus, PaymentTransaction
from . import metrics

TTL_HOURS = 24
# How long an in-progress key with no payment bound is presumed to have a
# live owner. Everything before the bind is validation plus one database
# transaction (milliseconds), so a minute is generous; and the lease is not
# what keeps payments unique (the fencing token is), it only decides how long
# a client whose first request died has to wait.
IN_PROGRESS_LEASE_SECONDS = 60
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


class OwnershipLost(KeyInProgress):
    """This request no longer owns the key: its lease ran out and a retry took
    it over. Nothing this request did may be committed."""


@dataclass(frozen=True)
class Replay:
    status_code: int
    body: Any


@dataclass(frozen=True)
class Recovered:
    """The key is bound to a committed payment but no response was stored.

    The caller renders the payment in its current state (and, once that
    state is final, stores it with `settle`).
    """

    record: IdempotencyKey
    transaction: PaymentTransaction


def request_hash(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def payment_request_fingerprint(*, validated: Mapping[str, Any] | None, raw: Any = None) -> dict[str, Any]:
    """The canonical form of a payment request, for `acquire(payload=...)`.

    A valid request is described by its validated fields, with the amount
    quantised to the currency's decimals, so spellings of the same payment
    ("42.50" / "42.5", " mkt_a " / "mkt_a", "us" / "US") share a fingerprint
    and a retry is recognised as a retry. An invalid request has no normal
    form, so the raw body stands in for it: the stored 400 still replays for
    the identical body, and the two forms are tagged so they can never be
    confused with each other.
    """
    if validated is None:
        return {"form": "raw", "body": raw}
    currency = str(validated["currency"])
    decimals = SUPPORTED_CURRENCIES.get(currency, 2)
    amount = Decimal(validated["amount"]).quantize(Decimal(1).scaleb(-decimals))
    return {
        "form": "validated",
        "merchant_id": validated["merchant_id"],
        "amount": str(amount),
        "currency": currency,
        "payment_method": validated["payment_method"],
        "device_id": validated.get("device_id", ""),
        "merchant_category": validated.get("merchant_category", ""),
        "country": validated.get("country", ""),
        "sandbox_behavior": validated.get("sandbox_behavior", "succeed"),
        "metadata": validated.get("metadata") or {},
    }


def validate_key(raw: str | None) -> str | None:
    if raw is None:
        return None
    key = raw.strip()
    if not key:
        return None
    if len(key) > MAX_KEY_LENGTH:
        raise IdempotencyError(f"Idempotency-Key must be at most {MAX_KEY_LENGTH} characters.")
    return key


def _new_token() -> str:
    return secrets.token_hex(16)


def _owned(record: IdempotencyKey) -> Any:
    """The key row, only while `record`'s request still owns it."""
    return IdempotencyKey.objects.filter(pk=record.pk, owner_token=record.owner_token)


def acquire(
    *, user: Any, scope: str, key: str, payload: Any, legacy_payload: Any = None
) -> IdempotencyKey | Replay | Recovered:
    """Take ownership of a key, or say what the earlier request with it produced.

    `legacy_payload` is the request as it was hashed before fingerprints
    existed (the raw body). A key stored by that version still matches its
    own retry instead of answering 422 for the rest of its 24 hours; new keys
    are never stored under it. It can go one TTL after that release.
    """
    digest = request_hash(payload)
    accepted = {digest} if legacy_payload is None else {digest, request_hash(legacy_payload)}
    # Two passes: the row can disappear between the failed INSERT and the
    # SELECT when its owner releases it, in which case the INSERT is retried.
    for _ in range(2):
        now = timezone.now()
        try:
            with transaction.atomic():
                return IdempotencyKey.objects.create(
                    user=user,
                    scope=scope,
                    key=key,
                    request_hash=digest,
                    owner_token=_new_token(),
                    locked_at=now,
                    expires_at=now + timedelta(hours=TTL_HOURS),
                )
        except IntegrityError:
            pass

        with transaction.atomic():
            # The row lock (PostgreSQL) serialises this decision against the
            # owner's bind, which holds the same lock until its payment
            # transaction ends: we see the key either bound or not, never
            # half way.
            existing = IdempotencyKey.objects.select_for_update().filter(user=user, scope=scope, key=key).first()
            if existing is None:
                continue
            return _resolve_existing(existing, digest, accepted, now)
    raise KeyInProgress("A request with this Idempotency-Key is still being processed. Retry shortly.")


def _resolve_existing(
    existing: IdempotencyKey, digest: str, accepted: set[str], now: Any
) -> IdempotencyKey | Replay | Recovered:
    if existing.expires_at <= now:
        existing.request_hash = digest
        existing.status = IdempotencyStatus.IN_PROGRESS
        existing.response_status = None
        existing.response_body = None
        existing.transaction = None
        existing.owner_token = _new_token()
        existing.locked_at = now
        existing.created_at = now
        existing.completed_at = None
        existing.expires_at = now + timedelta(hours=TTL_HOURS)
        existing.save()
        return existing
    if existing.request_hash not in accepted:
        metrics.increment("idempotency_conflicts_total")
        raise KeyReused(
            "This Idempotency-Key was already used with a different request body. "
            "Use a new key for a new payment."
        )
    if existing.status == IdempotencyStatus.COMPLETED:
        metrics.increment("idempotent_replays_total")
        return Replay(status_code=existing.response_status or 200, body=existing.response_body)

    bound = existing.transaction
    if bound is not None:
        # Committed, response never stored. Not a conflict and not something
        # to wait out: the payment exists, so answer with it.
        metrics.increment("idempotent_replays_total")
        return Recovered(record=existing, transaction=bound)

    if (now - existing.locked_at).total_seconds() >= IN_PROGRESS_LEASE_SECONDS:
        # Nothing was committed and the owner has been silent for longer than
        # any live request takes to reach its commit. Take the key over. The
        # compare-and-set on the old token means only one retry wins, and the
        # new token fences the old owner out for good.
        token = _new_token()
        taken = _owned(existing).filter(
            status=IdempotencyStatus.IN_PROGRESS, transaction__isnull=True
        ).update(owner_token=token, locked_at=now)
        if taken:
            existing.owner_token = token
            existing.locked_at = now
            metrics.increment("idempotency_takeovers_total")
            return existing
    raise KeyInProgress("A request with this Idempotency-Key is still being processed. Retry shortly.")


def bind(record: IdempotencyKey, transaction_obj: PaymentTransaction) -> None:
    """Attach the payment to the key, in the transaction that creates the payment.

    Raises OwnershipLost when this request no longer owns the key; the
    caller's transaction must then roll back, which is what guarantees a
    taken-over request leaves no payment behind.
    """
    if not transaction.get_connection().in_atomic_block:
        # Binding outside the payment's transaction would bring back the
        # window this function exists to close.
        raise RuntimeError("idempotency.bind() must run inside the transaction that inserts the payment.")
    bound = _owned(record).filter(
        status=IdempotencyStatus.IN_PROGRESS, transaction__isnull=True
    ).update(transaction=transaction_obj)
    if not bound:
        raise OwnershipLost("This Idempotency-Key is being processed by another request. Retry shortly.")
    record.transaction = transaction_obj


def complete(record: IdempotencyKey, *, status_code: int, body: Any) -> bool:
    """Store the owning request's final response. False when it no longer owns the key."""
    now = timezone.now()
    stored = _owned(record).update(
        status=IdempotencyStatus.COMPLETED, response_status=status_code, response_body=body, completed_at=now
    )
    if stored:
        record.status = IdempotencyStatus.COMPLETED
        record.response_status = status_code
        record.response_body = body
        record.completed_at = now
    return bool(stored)


def settle(recovered: Recovered, *, status_code: int, body: Any) -> bool:
    """Store the response for a recovered key, unless its owner got there first.

    Only called once the payment has reached a state the original request
    would have answered with; until then the key stays bound and in
    progress and every replay renders the payment afresh.
    """
    stored = _owned(recovered.record).filter(status=IdempotencyStatus.IN_PROGRESS).update(
        status=IdempotencyStatus.COMPLETED, response_status=status_code, response_body=body, completed_at=timezone.now()
    )
    return bool(stored)


def release(record: IdempotencyKey) -> None:
    """The operation raised before committing anything: forget the key so a retry can run.

    Deliberately narrow: it only removes a key this request still owns and
    that has no payment bound. Once a payment is committed the key is bound
    to it, so no failure after the commit, however it reaches this function,
    can free a key whose payment exists.
    """
    _owned(record).filter(status=IdempotencyStatus.IN_PROGRESS, transaction__isnull=True).delete()


def purge_expired(now: Any = None) -> int:
    deleted, _ = IdempotencyKey.objects.filter(expires_at__lte=now or timezone.now()).delete()
    return deleted
