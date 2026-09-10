"""The input to the risk engine.

A TransactionContext is a frozen snapshot of every signal the engine is allowed
to look at. It is built once (from the database for real payments, or
synthetically by the Fraud Lab), passed to the rules, and stored verbatim on
the RiskEvaluation so any decision can be replayed and audited later.

Keeping the engine a pure function of this object is what makes it testable
without a database and safe to run 1,000 times in a simulation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class TransactionContext:
    transaction_id: str
    user_id: int
    merchant_id: str
    amount: Decimal
    currency: str
    payment_method: str
    device_id: str
    timestamp: datetime

    merchant_category: str = ""
    ip_prefix: str = ""
    country: str = ""

    # Account
    account_age_days: float = 0.0

    # History (successful, non-simulation transactions before this one)
    historical_txn_count: int = 0
    successful_txn_count: int = 0
    historical_avg_amount: Decimal | None = None
    historical_currencies: tuple[str, ...] = ()
    merchant_seen_count: int = 0

    # Velocity
    txn_count_1h: int = 0
    txn_count_24h: int = 0
    amount_sum_24h: Decimal = Decimal("0")
    failed_txn_count_24h: int = 0

    # Device / network familiarity
    device_seen_count: int = 0          # times this user used this device before
    device_first_seen_days: float | None = None
    distinct_devices_24h: int = 0
    distinct_ips_24h: int = 0
    accounts_on_device_24h: int = 1     # distinct users sharing this device today

    # Time of day in the user's local zone (0-23); None when unknown
    local_hour: int | None = None

    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def has_history(self) -> bool:
        return self.historical_txn_count > 0

    @property
    def is_new_device(self) -> bool:
        return bool(self.device_id) and self.device_seen_count == 0

    @property
    def amount_ratio(self) -> Decimal | None:
        """amount / historical average, or None without history."""
        if not self.historical_avg_amount or self.historical_avg_amount <= 0:
            return None
        return (self.amount / self.historical_avg_amount).quantize(Decimal("0.01"))

    def snapshot(self) -> dict[str, Any]:
        """JSON-safe copy for persistence."""
        raw = asdict(self)
        out: dict[str, Any] = {}
        for key, value in raw.items():
            if isinstance(value, Decimal):
                out[key] = str(value)
            elif isinstance(value, datetime):
                out[key] = value.isoformat()
            elif isinstance(value, tuple):
                out[key] = list(value)
            else:
                out[key] = value
        out["amount_ratio"] = str(self.amount_ratio) if self.amount_ratio is not None else None
        out["is_new_device"] = self.is_new_device
        return out
