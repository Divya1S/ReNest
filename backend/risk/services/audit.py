"""Append-only audit records for security-sensitive actions.

Every record answers WHO (actor), WHAT (action + target), WHEN (created_at)
and WHY (reason, decision, model version). Metadata is redacted before it is
stored: nothing that looks like a credential, a token or a card number ever
reaches the audit table.
"""

from __future__ import annotations

import logging
from typing import Any

from ..models import AuditLog

logger = logging.getLogger("risk.audit")

SERVICE_ACTOR = "service:risk-engine"

# Keys that are never written to audit metadata, regardless of value.
_REDACT_KEYS = frozenset({
    "password", "passwd", "secret", "token", "authorization", "api_key", "apikey",
    "card_number", "pan", "cvv", "cvc", "expiry", "account_number", "iban", "ssn",
})
_REDACTED = "[redacted]"


def redact(value: Any, depth: int = 0) -> Any:
    """Return a copy of `value` with sensitive keys replaced."""
    if depth > 6:
        return _REDACTED
    if isinstance(value, dict):
        return {
            key: (_REDACTED if str(key).lower() in _REDACT_KEYS else redact(item, depth + 1))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item, depth + 1) for item in value]
    if isinstance(value, str) and len(value) > 500:
        return value[:500] + "..."
    return value


def actor_label_for(actor: Any) -> str:
    if actor is None or not getattr(actor, "is_authenticated", False):
        return SERVICE_ACTOR
    return f"user:{actor.pk}"


def record(
    *,
    action: str,
    target_type: str,
    target_id: str,
    actor: Any = None,
    actor_label: str | None = None,
    reason: str = "",
    decision: str = "",
    model_version: str = "",
    request_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    entry = AuditLog.objects.create(
        actor=actor if (actor is not None and getattr(actor, "is_authenticated", False)) else None,
        actor_label=actor_label or actor_label_for(actor),
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        reason=reason[:2000],
        decision=decision,
        model_version=model_version,
        request_id=request_id or "",
        metadata=redact(metadata or {}),
    )
    logger.info(
        "audit",
        extra={
            "audit_action": action,
            "audit_target": f"{target_type}:{target_id}",
            "audit_actor": entry.actor_label,
            "audit_decision": decision,
        },
    )
    return entry
