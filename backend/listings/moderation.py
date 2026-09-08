from __future__ import annotations

from typing import TYPE_CHECKING

from .ai_client import ai_available, generate_json

if TYPE_CHECKING:
    from .models import Listing as ListingType


_WEIGHT_LIMIT_LBS = 50

_PROHIBITED_HINTS = [
    "microwave", "mini fridge", "refrigerator", "fridge", "air conditioner",
    "washing machine", "washer", "dryer", "dishwasher", "oven", "stove",
]


def moderate_listing(listing: ListingType) -> tuple[str, str]:
    """
    Run a fast model pre-screen on the listing.
    Returns (moderation_status, flag_reason) where status is one of:
      "approved" — looks fine
      "flagged"  — potential issue detected
    Errors default to "approved" so moderation failures are never blocking.
    """
    if not ai_available():
        return "approved", ""

    # Quick local checks before spending an API call
    title_lower = (listing.title or "").lower()
    desc_lower = (listing.description or "").lower()
    combined = f"{title_lower} {desc_lower}"

    for hint in _PROHIBITED_HINTS:
        if hint in combined:
            return "flagged", (
                f"Item may be a prohibited large appliance (matched: {hint!r}). "
                "Please confirm the item fits within the weight/size limit."
            )

    from decimal import Decimal
    retail = Decimal(listing.estimated_retail_value or 0)
    price = Decimal(listing.price_amount or 0)
    if listing.price_type == "low_cost" and retail > 0 and price > retail * Decimal("1.5"):
        return "flagged", (
            f"Listed price (${price:.2f}) is more than 150% of the estimated retail value "
            f"(${retail:.2f}). This may violate the no-price-gouging policy."
        )

    prompt = (
        "You are a content moderator for a campus item marketplace where students give away "
        "or sell dorm items during move-out. Flag listings that:\n"
        "1. Include large prohibited appliances (microwaves, fridges, washing machines, etc.)\n"
        "2. Show signs of price gouging (asking price far above item value for what should be free/cheap)\n"
        "3. Describe prohibited or dangerous items (weapons, alcohol, controlled substances)\n"
        "4. Have suspicious or misleading titles (obvious spam, scam language)\n\n"
        f"Listing title: {listing.title}\n"
        f"Category: {listing.category}\n"
        f"Condition: {listing.condition}\n"
        f"Price type: {listing.price_type}\n"
        f"Price: ${listing.price_amount or 0}\n"
        f"Estimated retail value: ${listing.estimated_retail_value or 0}\n"
        f"Description: {(listing.description or '').strip()[:400]}\n\n"
        "Respond ONLY with a JSON object:\n"
        '{"status": "approved" | "flagged", "reason": "<one sentence if flagged, empty string if approved>"}'
    )

    try:
        parsed = generate_json(prompt, max_tokens=150)
    except Exception:
        return "approved", ""

    if not isinstance(parsed, dict):
        return "approved", ""

    status = parsed.get("status", "approved")
    reason = parsed.get("reason", "")

    if status not in {"approved", "flagged"}:
        return "approved", ""

    return status, (reason if status == "flagged" else "")
