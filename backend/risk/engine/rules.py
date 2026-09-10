"""Scoring rules.

Each rule inspects the TransactionContext and either abstains (returns None)
or returns a RuleResult: a stable code, the signed points it contributes, a
human label and the numbers behind it. Rules are independent: adding a signal
means adding one class and registering it in RULES, nothing else changes.

The point values are a hand-tuned demonstration model, not a trained fraud
model. They are chosen to be legible ("a 6x amount jump is worth more than a
new device") and are documented in the README. A production system would
replace this module with a calibrated model behind the same RuleResult shape,
which is why the explainability layer keys on codes rather than on rule
internals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

from .context import TransactionContext


@dataclass(frozen=True)
class RuleResult:
    code: str
    points: int
    label: str
    detail: dict[str, Any] = field(default_factory=dict)


class Rule(Protocol):
    code: str
    description: str

    def evaluate(self, ctx: TransactionContext) -> RuleResult | None: ...


# ── Amount ───────────────────────────────────────────────────────────────────

# An "average" over one or two payments is noise; the ratio rules wait for this many.
MIN_HISTORY_FOR_AVERAGE = 3


def _reliable_ratio(ctx: TransactionContext) -> Decimal | None:
    if ctx.historical_txn_count < MIN_HISTORY_FOR_AVERAGE:
        return None
    return ctx.amount_ratio


class AmountVsHistory:
    """Spend far above the customer's own average is the strongest single signal."""

    code = "amount_vs_history"
    description = "Transaction amount relative to the customer's historical average (needs 3+ prior payments)."

    def evaluate(self, ctx: TransactionContext) -> RuleResult | None:
        ratio = _reliable_ratio(ctx)
        if ratio is None:
            if not ctx.has_history and ctx.amount >= Decimal("1000"):
                return RuleResult(
                    "first_transaction_large", 18,
                    "Very large first transaction with no spending history",
                    {"amount": str(ctx.amount)},
                )
            if not ctx.has_history and ctx.amount >= Decimal("200"):
                return RuleResult(
                    "first_transaction_large", 10,
                    "Large first transaction with no spending history",
                    {"amount": str(ctx.amount)},
                )
            return None
        if ratio >= 6:
            points = 34
        elif ratio >= 3:
            points = 22
        elif ratio >= 2:
            points = 12
        else:
            return None
        return RuleResult(
            self.code, points,
            f"Transaction amount is {ratio}x above historical average",
            {"ratio": str(ratio), "average": str(ctx.historical_avg_amount), "amount": str(ctx.amount)},
        )


class ExtremeAmount:
    """Absolute size matters too: a campus marketplace rarely sees four figures."""

    code = "extreme_amount"
    description = "Absolute transaction size against what the marketplace normally sees."

    def evaluate(self, ctx: TransactionContext) -> RuleResult | None:
        if ctx.amount >= Decimal("2000"):
            return RuleResult(self.code, 35, "Exceptionally large amount for this marketplace", {"amount": str(ctx.amount)})
        if ctx.amount >= Decimal("1000"):
            return RuleResult(self.code, 12, "Very large amount for this marketplace", {"amount": str(ctx.amount)})
        return None


# ── Device and network ───────────────────────────────────────────────────────

class NewDevice:
    """An established account on a device it has never used before."""

    code = "new_device"
    description = "The account has never transacted from this device."

    def evaluate(self, ctx: TransactionContext) -> RuleResult | None:
        if ctx.is_new_device and ctx.has_history:
            return RuleResult(self.code, 21, "New device", {"device_id": ctx.device_id})
        return None


class KnownDevice:
    code = "known_device"
    description = "The device has a track record with this account."

    def evaluate(self, ctx: TransactionContext) -> RuleResult | None:
        if ctx.device_seen_count >= 3:
            return RuleResult(
                self.code, -12, "Known, trusted device",
                {"device_seen_count": ctx.device_seen_count},
            )
        return None


class DeviceSharing:
    """One device, many accounts in a day: the signature of multi-account abuse."""

    code = "device_sharing"
    description = "Number of distinct accounts transacting from this device today."

    def evaluate(self, ctx: TransactionContext) -> RuleResult | None:
        if ctx.accounts_on_device_24h >= 3:
            return RuleResult(
                self.code, 24, f"Device shared by {ctx.accounts_on_device_24h} accounts today",
                {"accounts_on_device_24h": ctx.accounts_on_device_24h},
            )
        return None


class IpChurn:
    code = "ip_churn"
    description = "Distinct networks used by the account in the last 24 hours."

    def evaluate(self, ctx: TransactionContext) -> RuleResult | None:
        if ctx.distinct_ips_24h >= 4:
            return RuleResult(
                self.code, 15, f"{ctx.distinct_ips_24h} different networks in 24h",
                {"distinct_ips_24h": ctx.distinct_ips_24h},
            )
        return None


class DeviceChurn:
    code = "device_churn"
    description = "Distinct devices used by the account in the last 24 hours."

    def evaluate(self, ctx: TransactionContext) -> RuleResult | None:
        if ctx.distinct_devices_24h >= 3:
            return RuleResult(
                self.code, 16, f"{ctx.distinct_devices_24h} different devices in 24h",
                {"distinct_devices_24h": ctx.distinct_devices_24h},
            )
        return None


# ── Velocity ─────────────────────────────────────────────────────────────────

class HourlyVelocity:
    code = "velocity_1h"
    description = "Transactions in the last hour."

    def evaluate(self, ctx: TransactionContext) -> RuleResult | None:
        if ctx.txn_count_1h >= 5:
            return RuleResult(self.code, 30, f"Unusual velocity: {ctx.txn_count_1h} transactions in the last hour", {"txn_count_1h": ctx.txn_count_1h})
        if ctx.txn_count_1h >= 3:
            return RuleResult(self.code, 18, f"Elevated velocity: {ctx.txn_count_1h} transactions in the last hour", {"txn_count_1h": ctx.txn_count_1h})
        return None


class DailyVelocity:
    code = "velocity_24h"
    description = "Transactions in the last 24 hours."

    def evaluate(self, ctx: TransactionContext) -> RuleResult | None:
        if ctx.txn_count_24h >= 15:
            return RuleResult(self.code, 20, f"{ctx.txn_count_24h} transactions in 24h", {"txn_count_24h": ctx.txn_count_24h})
        if ctx.txn_count_24h >= 8:
            return RuleResult(self.code, 10, f"{ctx.txn_count_24h} transactions in 24h", {"txn_count_24h": ctx.txn_count_24h})
        return None


class CardTesting:
    """Rapid tiny charges are how stolen card numbers get validated."""

    code = "card_testing"
    description = "Burst of very small charges."

    def evaluate(self, ctx: TransactionContext) -> RuleResult | None:
        if ctx.amount < Decimal("5") and ctx.txn_count_1h >= 4:
            return RuleResult(
                self.code, 28, "Card-testing pattern: burst of very small charges",
                {"amount": str(ctx.amount), "txn_count_1h": ctx.txn_count_1h},
            )
        return None


# ── Account ──────────────────────────────────────────────────────────────────

class AccountAge:
    code = "account_age"
    description = "How long the account has existed."

    def evaluate(self, ctx: TransactionContext) -> RuleResult | None:
        days = ctx.account_age_days
        if days < 1:
            return RuleResult("very_new_account", 18, "Account created in the last 24 hours", {"account_age_days": round(days, 2)})
        if days < 7:
            return RuleResult("new_account", 11, "Account created in the last week", {"account_age_days": round(days, 1)})
        if days < 30:
            return RuleResult("recent_account", 5, "Account created in the last month", {"account_age_days": round(days, 1)})
        if days >= 180:
            return RuleResult("established_account", -6, "Established account", {"account_age_days": round(days)})
        return None


class FailedAttempts:
    code = "failed_attempts"
    description = "Failed or blocked payments in the last 24 hours."

    def evaluate(self, ctx: TransactionContext) -> RuleResult | None:
        if ctx.failed_txn_count_24h >= 3:
            return RuleResult(self.code, 20, f"{ctx.failed_txn_count_24h} failed attempts in 24h", {"failed_txn_count_24h": ctx.failed_txn_count_24h})
        if ctx.failed_txn_count_24h >= 1:
            return RuleResult(self.code, 7, "Recent failed attempt", {"failed_txn_count_24h": ctx.failed_txn_count_24h})
        return None


class TrustedCustomer:
    code = "trusted_customer"
    description = "Sustained history of successful payments with no recent failures."

    def evaluate(self, ctx: TransactionContext) -> RuleResult | None:
        if ctx.failed_txn_count_24h:
            return None
        if ctx.successful_txn_count >= 10:
            return RuleResult(self.code, -15, "Trusted customer: consistent successful history", {"successful_txn_count": ctx.successful_txn_count})
        if ctx.successful_txn_count >= 3:
            return RuleResult("consistent_history", -8, "Consistent transaction history", {"successful_txn_count": ctx.successful_txn_count})
        return None


# ── Context ──────────────────────────────────────────────────────────────────

class CurrencyMismatch:
    code = "currency_mismatch"
    description = "Currency the account has never paid in before."

    def evaluate(self, ctx: TransactionContext) -> RuleResult | None:
        if ctx.historical_currencies and ctx.currency not in ctx.historical_currencies:
            return RuleResult(self.code, 8, f"First payment in {ctx.currency}", {"currency": ctx.currency, "seen": list(ctx.historical_currencies)})
        return None


class UnusualHour:
    code = "unusual_hour"
    description = "Local time of day."

    def evaluate(self, ctx: TransactionContext) -> RuleResult | None:
        if ctx.local_hour is not None and 1 <= ctx.local_hour <= 5:
            return RuleResult(self.code, 4, "Transaction in the early hours (01:00-05:00 local)", {"local_hour": ctx.local_hour})
        return None


class NewMerchantLargeAmount:
    code = "new_merchant_large"
    description = "First time at this merchant combined with an unusually large amount."

    def evaluate(self, ctx: TransactionContext) -> RuleResult | None:
        ratio = _reliable_ratio(ctx)
        if ctx.merchant_seen_count == 0 and ratio is not None and ratio >= 2:
            return RuleResult(self.code, 6, "New merchant and above-average amount", {"merchant_id": ctx.merchant_id, "ratio": str(ratio)})
        return None


# Registration order is the display order in explanations. Add new rules here.
RULES: tuple[Rule, ...] = (
    AmountVsHistory(),
    ExtremeAmount(),
    NewDevice(),
    KnownDevice(),
    DeviceSharing(),
    DeviceChurn(),
    IpChurn(),
    HourlyVelocity(),
    DailyVelocity(),
    CardTesting(),
    AccountAge(),
    FailedAttempts(),
    TrustedCustomer(),
    CurrencyMismatch(),
    UnusualHour(),
    NewMerchantLargeAmount(),
)


def run_rules(ctx: TransactionContext, rules: tuple[Rule, ...] = RULES) -> list[RuleResult]:
    results: list[RuleResult] = []
    for rule in rules:
        result = rule.evaluate(ctx)
        if result is not None:
            results.append(result)
    return results
