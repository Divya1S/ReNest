"""Build a TransactionContext for a real payment from the database.

This is the only place the engine touches persistence for live payments. It
runs a small, fixed number of aggregate queries (no per-row loops) so a payment
evaluation stays at a handful of milliseconds regardless of history size, and
excludes simulation rows so Fraud Lab runs never pollute a real customer's
profile.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone

from ..models import PaymentTransaction, TransactionStatus
from .context import TransactionContext

# Statuses that mean "the customer completed a payment successfully".
_SUCCESS = (TransactionStatus.APPROVED, TransactionStatus.COMPLETED)
# Statuses that count as a failure signal.
_FAILURE = (TransactionStatus.BLOCKED, TransactionStatus.FAILED)


def coarse_ip_prefix(remote_addr: str | None) -> str:
    """Reduce an address to a coarse network prefix; never store the full IP."""
    if not remote_addr:
        return ""
    if ":" in remote_addr:  # IPv6: keep the first two hextets
        parts = remote_addr.split(":")
        return ":".join(parts[:2]) + "::"
    parts = remote_addr.split(".")
    if len(parts) == 4:
        return ".".join(parts[:2]) + ".0.0"
    return ""


def build_context(
    *,
    user: Any,
    transaction_id: str,
    merchant_id: str,
    merchant_category: str,
    amount: Decimal,
    currency: str,
    payment_method: str,
    device_id: str,
    ip_prefix: str,
    country: str,
    now: datetime | None = None,
) -> TransactionContext:
    now = now or timezone.now()
    day_ago = now - timedelta(hours=24)
    hour_ago = now - timedelta(hours=1)

    # The payment under evaluation is already stored (so its events can be
    # written in the same transaction); it must not count as its own history,
    # otherwise every device looks "seen" and velocity is off by one.
    base = PaymentTransaction.objects.filter(user=user, is_simulation=False).exclude(public_id=transaction_id)

    history = base.filter(status__in=_SUCCESS).aggregate(
        count=Count("id"), avg=Avg("amount"),
    )
    velocity = base.filter(created_at__gte=day_ago).aggregate(
        count_24h=Count("id"),
        sum_24h=Sum("amount"),
        count_1h=Count("id", filter=Q(created_at__gte=hour_ago)),
        failed_24h=Count("id", filter=Q(status__in=_FAILURE)),
        devices_24h=Count("device_id", distinct=True, filter=~Q(device_id="")),
        ips_24h=Count("ip_prefix", distinct=True, filter=~Q(ip_prefix="")),
    )

    device_seen = 0
    device_first_seen_days: float | None = None
    accounts_on_device = 1
    if device_id:
        device_seen = base.filter(device_id=device_id).count()
        first_use = base.filter(device_id=device_id).order_by("created_at").values_list("created_at", flat=True).first()
        if first_use:
            device_first_seen_days = (now - first_use).total_seconds() / 86400
        accounts_on_device = (
            PaymentTransaction.objects.filter(
                device_id=device_id, is_simulation=False, created_at__gte=day_ago
            ).exclude(public_id=transaction_id).values("user_id").distinct().count()
        ) or 1

    currencies = tuple(
        sorted(base.filter(status__in=_SUCCESS).values_list("currency", flat=True).distinct())
    )
    merchant_seen = base.filter(merchant_id=merchant_id, status__in=_SUCCESS).count()

    joined = getattr(user, "created_at", None) or getattr(user, "date_joined", None)
    account_age_days = (now - joined).total_seconds() / 86400 if joined else 0.0

    local_hour: int | None = None
    campus = getattr(user, "campus", None)
    tz_name = getattr(campus, "timezone", None)
    if tz_name:
        try:
            import zoneinfo

            local_hour = now.astimezone(zoneinfo.ZoneInfo(tz_name)).hour
        except Exception:
            local_hour = None

    return TransactionContext(
        transaction_id=transaction_id,
        user_id=user.pk,
        merchant_id=merchant_id,
        merchant_category=merchant_category,
        amount=amount,
        currency=currency,
        payment_method=payment_method,
        device_id=device_id,
        ip_prefix=ip_prefix,
        country=country,
        timestamp=now,
        account_age_days=max(0.0, account_age_days),
        historical_txn_count=history["count"] or 0,
        successful_txn_count=history["count"] or 0,
        historical_avg_amount=Decimal(str(history["avg"])).quantize(Decimal("0.01")) if history["avg"] else None,
        historical_currencies=currencies,
        merchant_seen_count=merchant_seen,
        txn_count_1h=velocity["count_1h"] or 0,
        txn_count_24h=velocity["count_24h"] or 0,
        amount_sum_24h=Decimal(str(velocity["sum_24h"] or 0)),
        failed_txn_count_24h=velocity["failed_24h"] or 0,
        device_seen_count=device_seen,
        device_first_seen_days=device_first_seen_days,
        distinct_devices_24h=velocity["devices_24h"] or 0,
        distinct_ips_24h=velocity["ips_24h"] or 0,
        accounts_on_device_24h=accounts_on_device,
        local_hour=local_hour,
    )
