"""Reference rates for comparing amounts across currencies.

The amount rules ask "is this a lot of money for this marketplace?", which is
only meaningful in one unit: 2,000 JPY is a sandwich, 2,000 USD is not. Rules
therefore compare amounts in approximate US dollars.

The table below holds APPROXIMATE REFERENCE VALUES, fixed in code on purpose:

* they exist to scale risk thresholds, never to move or convert money;
* a static table keeps the engine a pure function (no rate service, no clock),
  so a stored decision can be replayed and gives the same answer;
* the thresholds they feed are coarse (200 / 1,000 / 2,000), so a rate that is
  ten percent stale does not change what the rule means.

Each value is "US dollars per one unit of the currency", rounded. Every
evaluation stores the rate it used in its context snapshot, so a reader can
always see what was compared.
"""

from __future__ import annotations

from decimal import Decimal

REFERENCE_CURRENCY = "USD"

# Approximate reference values (USD per unit). Not live rates.
USD_REFERENCE_RATES: dict[str, Decimal] = {
    "USD": Decimal("1"),
    "EUR": Decimal("1.08"),
    "GBP": Decimal("1.27"),
    "CAD": Decimal("0.73"),
    "AUD": Decimal("0.66"),
    "INR": Decimal("0.012"),
    "JPY": Decimal("0.0067"),
}

_CENT = Decimal("0.01")


def usd_rate(currency: str) -> Decimal:
    """Reference rate for a currency. An unknown code is compared at face
    value (rate 1): the API only accepts the currencies in the table, and a
    synthetic context with another code must still be scorable."""
    return USD_REFERENCE_RATES.get(currency, Decimal("1"))


def to_usd(amount: Decimal, currency: str) -> Decimal:
    """`amount` in approximate US dollars, to the cent."""
    return (amount * usd_rate(currency)).quantize(_CENT)
