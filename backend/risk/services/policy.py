"""The live risk policy (sensitivity) and its audited updates."""

from __future__ import annotations

from typing import Any

from django.core.cache import cache
from django.db import transaction

from ..engine import thresholds_for
from ..models import RiskPolicy
from . import audit, events

_CACHE_KEY = "risk:policy:active"
_CACHE_TTL = 60
DEFAULT_SENSITIVITY = 50


def get_active_policy() -> RiskPolicy:
    policy = RiskPolicy.objects.filter(active=True).order_by("id").first()
    if policy is None:
        policy = RiskPolicy.objects.create(name="default", sensitivity=DEFAULT_SENSITIVITY, active=True)
    return policy


def current_sensitivity() -> int:
    cached = cache.get(_CACHE_KEY)
    if isinstance(cached, int):
        return cached
    value = get_active_policy().sensitivity
    cache.set(_CACHE_KEY, value, _CACHE_TTL)
    return value


def update_sensitivity(*, actor: Any, sensitivity: int, reason: str = "", request_id: str = "") -> RiskPolicy:
    if not 0 <= sensitivity <= 100:
        raise ValueError("sensitivity must be between 0 and 100")
    with transaction.atomic():
        policy = get_active_policy()
        previous = policy.sensitivity
        policy.sensitivity = sensitivity
        policy.updated_by = actor
        policy.save(update_fields=["sensitivity", "updated_by", "updated_at"])
        # Invalidate now and again after commit: a reader that slips in
        # between the first delete and the commit would otherwise repopulate
        # the cache with the old value for 60 seconds.
        cache.delete(_CACHE_KEY)
        transaction.on_commit(lambda: cache.delete(_CACHE_KEY))
        thresholds = thresholds_for(sensitivity)
        audit.record(
            action="risk.policy_modified",
            target_type="risk_policy",
            target_id=str(policy.pk),
            actor=actor,
            reason=reason or f"Sensitivity changed {previous} -> {sensitivity}",
            request_id=request_id,
            metadata={
                "previous_sensitivity": previous,
                "sensitivity": sensitivity,
                "review_threshold": thresholds.review,
                "block_threshold": thresholds.block,
            },
        )
        events.emit(
            events.POLICY_UPDATED,
            entity_type="risk_policy",
            entity_id=str(policy.pk),
            payload={"previous": previous, "sensitivity": sensitivity, "actor_id": getattr(actor, "pk", None)},
            request_id=request_id,
        )
    return policy
