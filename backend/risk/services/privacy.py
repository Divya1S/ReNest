"""What a non-staff caller may read from audit rows and events.

One policy, applied by the serializers, so every endpoint that returns these
rows (payment detail, GET /api/risk/audit, GET /api/risk/events) agrees:

    A customer may see THAT something happened to their payment and what the
    outcome was. They may not see WHO on staff did it, what staff wrote about
    it, or internal error text.

Audit rows written by anyone other than the viewer or a service are staff
actions (a customer can only read rows about their own payments, and the only
other humans who touch those are staff). For such a row the actor is shown as
"staff", the free-text reason is replaced by the outcome and the metadata is
emptied. The rule is "not mine and not a service", rather than a list of
action names, so an action added later is private until someone decides
otherwise.

The policy fails closed: a serializer used without a request in its context
treats the reader as a customer.
"""

from __future__ import annotations

from typing import Any

STAFF_ACTOR = "staff"
SERVICE_PREFIX = "service:"

# Event payload keys that identify staff or carry their words. Stripped for
# customers on read; new payloads should not contain them in the first place.
PRIVATE_PAYLOAD_KEYS = frozenset({"reviewer_id", "reviewer", "actor_id", "resolved_by_id", "note"})

_STAFF_REASONS: dict[str, str] = {
    "payment.manual_review": "Manual review: {outcome}",
    "agent.transaction_resolved": "Held purchase {outcome}",
}
HANDLER_ERROR_NOTICE = "A downstream handler failed; staff can see the details."


def viewer_from(context: dict[str, Any] | None) -> Any:
    request = (context or {}).get("request")
    return getattr(request, "user", None)


def is_staff(viewer: Any) -> bool:
    return bool(viewer is not None and getattr(viewer, "is_authenticated", False) and getattr(viewer, "is_staff", False))


def _own_label(viewer: Any) -> str | None:
    pk = getattr(viewer, "pk", None)
    return f"user:{pk}" if pk is not None else None


def audit_row_for(viewer: Any, row: dict[str, Any]) -> dict[str, Any]:
    """An AuditLog representation as `viewer` may see it."""
    if is_staff(viewer):
        return row
    label = str(row.get("actor_label") or "")
    if label.startswith(SERVICE_PREFIX) or label == _own_label(viewer):
        return row
    metadata = row.get("metadata")
    outcome = metadata.get("outcome") if isinstance(metadata, dict) else None
    template = _STAFF_REASONS.get(str(row.get("action")), "Action taken by staff")
    return {
        **row,
        "actor_label": STAFF_ACTOR,
        "reason": template.format(outcome=outcome or "resolved"),
        "metadata": {},
    }


def event_row_for(viewer: Any, row: dict[str, Any]) -> dict[str, Any]:
    """A PaymentEvent representation as `viewer` may see it."""
    if is_staff(viewer):
        return row
    payload = row.get("payload")
    cleaned = {k: v for k, v in payload.items() if k not in PRIVATE_PAYLOAD_KEYS} if isinstance(payload, dict) else payload
    return {
        **row,
        "payload": cleaned,
        # Exception text from a handler can quote SQL, row values or paths.
        "last_error": HANDLER_ERROR_NOTICE if row.get("last_error") else "",
    }
