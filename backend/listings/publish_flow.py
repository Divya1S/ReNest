from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from .models import Listing, RoomScanItemDraft, RoomScanSession


PUBLISH_PRESETS: list[dict[str, Any]] = [
    {
        "key": "storage_bin",
        "label": "Storage Bin",
        "default_title": "Storage bin",
        "category": Listing.Category.STORAGE,
        "default_condition": Listing.Condition.GOOD,
        "default_price_type": Listing.PriceType.FREE,
        "default_price_amount": Decimal("0.00"),
        "default_estimated_retail_value": Decimal("18.00"),
        "default_notes": "Clean storage piece that is easy to reuse during move-in week.",
    },
    {
        "key": "desk_lamp",
        "label": "Desk Lamp",
        "default_title": "Desk lamp",
        "category": Listing.Category.LIGHTING,
        "default_condition": Listing.Condition.GOOD,
        "default_price_type": Listing.PriceType.LOW_COST,
        "default_price_amount": Decimal("8.00"),
        "default_estimated_retail_value": Decimal("24.00"),
        "default_notes": "Working study lamp with an easy handoff value for another student.",
    },
    {
        "key": "fan",
        "label": "Fan",
        "default_title": "Dorm fan",
        "category": Listing.Category.COMFORT,
        "default_condition": Listing.Condition.GOOD,
        "default_price_type": Listing.PriceType.LOW_COST,
        "default_price_amount": Decimal("10.00"),
        "default_estimated_retail_value": Decimal("28.00"),
        "default_notes": "Useful cooling item that usually gets rebought every semester.",
    },
    {
        "key": "mirror",
        "label": "Mirror",
        "default_title": "Dorm mirror",
        "category": Listing.Category.DECOR,
        "default_condition": Listing.Condition.GOOD,
        "default_price_type": Listing.PriceType.LOW_COST,
        "default_price_amount": Decimal("7.00"),
        "default_estimated_retail_value": Decimal("20.00"),
        "default_notes": "Compact mirror that is easy to pick up and reuse.",
    },
    {
        "key": "toiletries_bundle",
        "label": "Unopened Toiletries",
        "default_title": "Unopened toiletries bundle",
        "category": Listing.Category.TOILETRIES,
        "default_condition": Listing.Condition.NEW,
        "default_price_type": Listing.PriceType.FREE,
        "default_price_amount": Decimal("0.00"),
        "default_estimated_retail_value": Decimal("16.00"),
        "default_notes": "Sealed items that are great for free pickup or donation.",
    },
    {
        "key": "hangers",
        "label": "Hangers",
        "default_title": "Hanger pack",
        "category": Listing.Category.STORAGE,
        "default_condition": Listing.Condition.GOOD,
        "default_price_type": Listing.PriceType.FREE,
        "default_price_amount": Decimal("0.00"),
        "default_estimated_retail_value": Decimal("14.00"),
        "default_notes": "Useful closet basics that are easy to claim quickly.",
    },
    {
        "key": "school_supplies",
        "label": "School Supplies",
        "default_title": "School supplies bundle",
        "category": Listing.Category.SUPPLIES,
        "default_condition": Listing.Condition.GOOD,
        "default_price_type": Listing.PriceType.FREE,
        "default_price_amount": Decimal("0.00"),
        "default_estimated_retail_value": Decimal("15.00"),
        "default_notes": "Notebook, folder, and desk-supply style items that still have value.",
    },
]

PUBLISH_PRESET_MAP: dict[str, dict[str, Any]] = {preset["key"]: preset for preset in PUBLISH_PRESETS}


def serialize_preset(preset: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": preset["key"],
        "label": preset["label"],
        "title": preset["default_title"],
        "category": preset["category"],
        "default_condition": preset["default_condition"],
        "default_price_type": preset["default_price_type"],
        "default_price_amount": f"{Decimal(preset['default_price_amount']):.2f}",
        "default_estimated_retail_value": f"{Decimal(preset['default_estimated_retail_value']):.2f}",
        "default_notes": preset["default_notes"],
    }


def get_serialized_publish_presets() -> list[dict[str, Any]]:
    return [serialize_preset(preset) for preset in PUBLISH_PRESETS]


def get_publish_preset(key: str) -> Optional[dict[str, Any]]:
    return PUBLISH_PRESET_MAP.get(key)


def infer_preset_key(item: RoomScanItemDraft) -> Optional[str]:
    if item.preset_key:
        return item.preset_key

    title = (item.title or "").lower()
    if "lamp" in title:
        return "desk_lamp"
    if "fan" in title:
        return "fan"
    if "mirror" in title:
        return "mirror"
    if "toiletr" in title or item.category == Listing.Category.TOILETRIES:
        return "toiletries_bundle"
    if "hanger" in title:
        return "hangers"
    if "supply" in title or item.category == Listing.Category.SUPPLIES:
        return "school_supplies"
    if "bin" in title or item.category == Listing.Category.STORAGE:
        return "storage_bin"
    return None


def build_suggestion_pack(item: RoomScanItemDraft) -> dict[str, Any]:
    preset_key = infer_preset_key(item)
    if preset_key and preset_key in PUBLISH_PRESET_MAP:
        return serialize_preset(PUBLISH_PRESET_MAP[preset_key])

    return {
        "key": None,
        "label": "Current draft",
        "title": item.title,
        "category": item.category,
        "default_condition": item.condition,
        "default_price_type": item.price_type,
        "default_price_amount": f"{Decimal(item.price_amount or 0):.2f}",
        "default_estimated_retail_value": f"{Decimal(item.estimated_retail_value or 0):.2f}",
        "default_notes": item.notes or "",
    }


def build_missing_fields(
    item: RoomScanItemDraft, session: Optional[RoomScanSession] = None
) -> list[str]:
    if item.linked_listing_id:
        return []
    if item.donation_hub_id:
        return []

    missing_fields: list[str] = []
    if item.triage_status == RoomScanItemDraft.TriageStatus.DONATE:
        if not item.donation_hub_id:
            missing_fields.append("donation_hub")
        return missing_fields

    if item.triage_status != RoomScanItemDraft.TriageStatus.SELL:
        return missing_fields

    # Resolve session — caller may pass it to avoid an FK lookup when items
    # were loaded from a prefetch cache that did not include scan_session.
    if session is None:
        session = item.scan_session

    if not (item.title or "").strip():
        missing_fields.append("title")
    if not item.category:
        missing_fields.append("category")
    if not item.condition:
        missing_fields.append("condition")
    if Decimal(item.estimated_retail_value or 0) <= Decimal("0.00"):
        missing_fields.append("estimated_retail_value")
    if not (session.pickup_zone or "").strip():
        missing_fields.append("pickup_zone")
    if not session.move_out_deadline:
        missing_fields.append("move_out_deadline")
    if item.price_type == Listing.PriceType.LOW_COST and Decimal(item.price_amount or 0) <= Decimal("0.00"):
        missing_fields.append("price_amount")

    return missing_fields


def get_publish_readiness(
    item: RoomScanItemDraft, session: Optional[RoomScanSession] = None
) -> str:
    if item.linked_listing_id:
        return "published"
    if item.donation_hub_id:
        return "donation_route"

    if item.triage_status == RoomScanItemDraft.TriageStatus.SELL:
        return "ready" if not build_missing_fields(item, session) else "needs_info"
    if item.triage_status == RoomScanItemDraft.TriageStatus.DONATE:
        return "needs_info"
    return "needs_info"


def get_recommended_action(item: RoomScanItemDraft) -> str:
    if item.linked_listing_id:
        return "publish"
    if item.donation_hub_id:
        return "donate"

    if item.triage_status == RoomScanItemDraft.TriageStatus.SELL:
        return "publish"
    if item.triage_status == RoomScanItemDraft.TriageStatus.DONATE:
        return "donate"
    if item.triage_status == RoomScanItemDraft.TriageStatus.KEEP:
        return "keep"
    if item.triage_status == RoomScanItemDraft.TriageStatus.TOSS:
        return "toss"
    return "review"


def build_publish_description(item: RoomScanItemDraft) -> str:
    if (item.notes or "").strip():
        return item.notes.strip()
    suggestion_pack = build_suggestion_pack(item)
    return suggestion_pack["default_notes"] or "Generated from a Room Rescue Scan draft."
