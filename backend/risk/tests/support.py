"""Shared helpers for the risk test suite."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from typing import Any

from django.contrib.auth import get_user_model

from risk.engine.context import TransactionContext
from risk.services.processor import SandboxProcessor, get_processor

NOW = datetime(2026, 9, 9, 14, 0, tzinfo=dt_timezone.utc)


def make_user(email: str = "customer@example.com", *, staff: bool = False, days_old: int = 200) -> Any:
    user = get_user_model().objects.create_user(
        email=email, password="secretpass123", display_name=email.split("@")[0], is_staff=staff,
    )
    if days_old:
        # created_at is auto_now_add; push it back so account-age rules see history.
        get_user_model().objects.filter(pk=user.pk).update(created_at=user.created_at - timedelta(days=days_old))
        user.refresh_from_db()
    return user


def sandbox() -> SandboxProcessor:
    processor = get_processor()
    assert isinstance(processor, SandboxProcessor)
    return processor


def payment_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "merchant_id": "mkt_textbooks",
        "amount": "42.50",
        "currency": "USD",
        "payment_method": "card",
        "device_id": "laptop-1",
        "merchant_category": "books",
    }
    payload.update(overrides)
    return payload


def context(**overrides: Any) -> TransactionContext:
    """An established, trusted customer unless overridden."""
    fields: dict[str, Any] = dict(
        transaction_id="t1",
        user_id=1,
        merchant_id="mkt_textbooks",
        amount=Decimal("40.00"),
        currency="USD",
        payment_method="card",
        device_id="laptop-1",
        timestamp=NOW,
        account_age_days=400,
        historical_txn_count=12,
        successful_txn_count=12,
        historical_avg_amount=Decimal("38.00"),
        historical_currencies=("USD",),
        merchant_seen_count=2,
        device_seen_count=8,
        device_first_seen_days=300,
        txn_count_24h=1,
        local_hour=14,
    )
    fields.update(overrides)
    return TransactionContext(**fields)


def takeover_context(**overrides: Any) -> TransactionContext:
    return context(
        amount=Decimal("420.00"),
        device_id="unknown-phone",
        device_seen_count=0,
        device_first_seen_days=None,
        distinct_ips_24h=5,
        distinct_devices_24h=3,
        merchant_seen_count=0,
        local_hour=3,
        **overrides,
    )
