"""Build a TransactionContext for a real payment from the database.

This is the only place the engine touches persistence for live payments. It
runs a small, fixed number of aggregate queries (no per-row loops) so a payment
evaluation stays at a handful of milliseconds regardless of history size, and
excludes simulation rows so Fraud Lab runs never pollute a real customer's
profile.
"""

from __future__ import annotations

import ipaddress
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from django.db.models import Count, Q, Sum
from django.utils import timezone

from ..models import PaymentTransaction, TransactionStatus
from .context import TransactionContext
from .currency import USD_REFERENCE_RATES, to_usd

# Statuses that mean "the customer completed a payment successfully".
_SUCCESS = (TransactionStatus.APPROVED, TransactionStatus.COMPLETED)
# Statuses that count as a failure signal.
_FAILURE = (TransactionStatus.BLOCKED, TransactionStatus.FAILED)


def coarse_ip_prefix(remote_addr: str | None) -> str:
    """Reduce an address to a coarse network prefix; never store the full IP.

    IPv4 keeps the first two octets, IPv6 the first two hextets (a /32).
    Anything that does not parse as an address yields "", so a malformed
    header can never be stored as if it were a network.
    """
    try:
        address = ipaddress.ip_address((remote_addr or "").strip())
    except ValueError:
        return ""
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is None:
            return str(ipaddress.ip_network((address, 32), strict=False).network_address)
        # ::ffff:203.0.113.9 is an IPv4 client seen through a dual-stack socket.
        address = address.ipv4_mapped
    return ".".join(str(address).split(".")[:2]) + ".0.0"


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

    # Successful history, one row per currency (at most seven): the counts,
    # the currencies seen and the totals needed for an average in one unit.
    successful = 0
    successful_usd = Decimal("0")
    currencies: list[str] = []
    for row in base.filter(status__in=_SUCCESS).values("currency").annotate(n=Count("id"), total=Sum("amount")).order_by():
        successful += row["n"]
        successful_usd += to_usd(Decimal(str(row["total"] or 0)), row["currency"])
        currencies.append(row["currency"])
    average_usd = (successful_usd / successful).quantize(Decimal("0.01")) if successful else None

    velocity = base.filter(created_at__gte=day_ago).aggregate(
        count_24h=Count("id"),
        count_1h=Count("id", filter=Q(created_at__gte=hour_ago)),
        failed_24h=Count("id", filter=Q(status__in=_FAILURE)),
        devices_24h=Count("device_id", distinct=True, filter=~Q(device_id="")),
        ips_24h=Count("ip_prefix", distinct=True, filter=~Q(ip_prefix="")),
        # One conditional sum per supported currency, so the 24h total can be
        # expressed in one unit without a second query.
        **{f"sum_{code}": Sum("amount", filter=Q(currency=code)) for code in USD_REFERENCE_RATES},
    )
    sum_24h_usd = sum(
        (to_usd(Decimal(str(velocity[f"sum_{code}"] or 0)), code) for code in USD_REFERENCE_RATES),
        Decimal("0"),
    )
    # Every prior attempt, whatever happened to it. Kept apart from the
    # successful count above: "has this account been active" and "has this
    # account spent money" are different questions and different rules ask them.
    attempts = base.count()

    device_seen = 0
    device_first_seen_days: float | None = None
    accounts_on_device = 1
    if device_id:
        device_seen = base.filter(device_id=device_id).count()
        first_use = base.filter(device_id=device_id).order_by("created_at").values_list("created_at", flat=True).first()
        if first_use:
            device_first_seen_days = (now - first_use).total_seconds() / 86400
        # Distinct accounts on the device today, this one included. Counting
        # only the other accounts and adding one is what makes the third
        # account see 3: this user's own earlier rows are irrelevant, and the
        # payment under evaluation must count for its owner even though it is
        # not part of anyone's history yet.
        other_accounts = (
            PaymentTransaction.objects.filter(device_id=device_id, is_simulation=False, created_at__gte=day_ago)
            .exclude(user=user)
            .values("user_id").distinct().count()
        )
        accounts_on_device = other_accounts + 1

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
        historical_txn_count=attempts,
        successful_txn_count=successful,
        historical_avg_amount=average_usd,
        historical_currencies=tuple(sorted(currencies)),
        merchant_seen_count=merchant_seen,
        txn_count_1h=velocity["count_1h"] or 0,
        txn_count_24h=velocity["count_24h"] or 0,
        amount_sum_24h=sum_24h_usd,
        failed_txn_count_24h=velocity["failed_24h"] or 0,
        device_seen_count=device_seen,
        device_first_seen_days=device_first_seen_days,
        distinct_devices_24h=velocity["devices_24h"] or 0,
        distinct_ips_24h=velocity["ips_24h"] or 0,
        accounts_on_device_24h=accounts_on_device,
        local_hour=local_hour,
    )
