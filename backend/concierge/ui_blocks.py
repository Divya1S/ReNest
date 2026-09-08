"""Generative UI: the model composes interfaces, the server supplies the truth.

The model gets three presentation tools. Calling one doesn't inject model text
into the UI — it appends a typed block to this turn's collector, and **every
prop is hydrated server-side from the ORM at call time**. For listing cards
the model contributes only IDs it saw in earlier tool results; titles, prices,
zones, and thumbnails come from the database, and IDs that don't correspond to
a live listing are silently dropped. A hallucinated listing is structurally
impossible to render.

The widget maps block.type → React component via a registry
(frontend/src/components/concierge/generativeUi.jsx); unknown types render
nothing, so the two sides can evolve independently.
"""

from __future__ import annotations

import re
from typing import Any

from django.utils import timezone

from listings.models import Listing

from .tools import _get_move_out_progress

MAX_CARDS = 4
MAX_SUGGESTIONS = 3

# Paths a suggestion chip may deep-link to. Anything else renders as a plain
# "send this text" chip — the model can never construct an arbitrary link.
_SAFE_PATH_RE = re.compile(
    r"^/(browse|scan|dashboard|saved|saved-searches|requests|hubs|impact|leaderboard"
    r"|trust|assistant|settings|my-listings|my-reservations)(\?[A-Za-z0-9=&%_+.\-]*)?$"
    r"|^/listings/\d+$"
)


def _safe_path(path: Any) -> str | None:
    candidate = str(path or "").strip()
    return candidate if candidate and _SAFE_PATH_RE.match(candidate) else None

UI_TOOL_DECLARATIONS: list[dict[str, Any]] = [
    {
        "name": "show_listing_cards",
        "description": (
            "Render interactive listing cards in the chat for listings you found with "
            "search_listings. Pass the ids of the most relevant listings (max 4). Use this "
            "whenever you mention specific listings — cards beat text descriptions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "listing_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "IDs of listings returned by search_listings this conversation.",
                },
            },
            "required": ["listing_ids"],
        },
    },
    {
        "name": "show_move_out_progress",
        "description": (
            "Render the user's live move-out progress (scan sessions, task completion, draft "
            "triage) as a visual panel. Use when discussing their move-out status."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "suggest_followups",
        "description": (
            "Offer up to 3 short tappable follow-ups. Call at most once, near the end of your "
            "turn. Each entry is either a question the user might ask next (label only) or a "
            "shortcut into the app (label + path). Valid paths: /browse, /listings/<id>, "
            "/scan, /dashboard, /saved, /requests, /hubs, /settings, /my-listings. "
            "Labels under 8 words."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "suggestions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "path": {"type": "string", "description": "Optional in-app path to open instead of sending text."},
                        },
                        "required": ["label"],
                    },
                },
            },
            "required": ["suggestions"],
        },
    },
    {
        "name": "propose_saved_search",
        "description": (
            "When the user wants to be alerted about future items ('let me know when a mini "
            "fridge shows up'), render a saved-search proposal card. The card only PROPOSES the "
            "alert — the user confirms with a tap, and nothing exists until they do. Never tell "
            "the user the alert is already active."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "What to watch for, e.g. 'mini fridge'."},
                "category": {
                    "type": "string",
                    "enum": ["storage", "lighting", "supplies", "comfort", "toiletries", "decor", "other"],
                },
                "free_only": {"type": "boolean"},
                "label": {"type": "string", "description": "Short human name for the alert."},
            },
            "required": ["keyword"],
        },
    },
]

UI_TOOL_NAMES = frozenset(d["name"] for d in UI_TOOL_DECLARATIONS)


class UiCollector:
    """Accumulates validated blocks for one turn, in call order."""

    def __init__(self) -> None:
        self.blocks: list[dict[str, Any]] = []

    def add(self, block_type: str, props: dict[str, Any]) -> None:
        self.blocks.append({"type": block_type, "props": props})


def handle_ui_tool(user: Any, name: str, args: dict[str, Any], collector: UiCollector) -> dict[str, Any]:
    """Execute one UI tool: hydrate + validate + collect. Returns the tool result."""
    if name == "show_listing_cards":
        cards = _hydrate_listing_cards(args.get("listing_ids") or [])
        if not cards:
            return {"ok": False, "error": "None of those listing ids are live. Only use ids from search_listings."}
        collector.add("listing_cards", {"listings": cards})
        return {"ok": True, "rendered": len(cards)}
    if name == "show_move_out_progress":
        progress = _get_move_out_progress(user, {})
        if not progress["scan_sessions"]:
            return {"ok": False, "error": "User has no scan sessions to display; answer in text instead."}
        collector.add("move_out_progress", progress)
        return {"ok": True, "rendered": len(progress["scan_sessions"])}
    if name == "suggest_followups":
        cleaned = []
        raw_suggestions = args.get("suggestions")
        if not isinstance(raw_suggestions, list):
            raw_suggestions = []
        for entry in raw_suggestions[: MAX_SUGGESTIONS * 2]:
            if isinstance(entry, str):          # tolerate the old string shape
                entry = {"label": entry}
            if not isinstance(entry, dict):
                continue
            label = str(entry.get("label") or "").strip()
            if not label:
                continue
            cleaned.append({"label": label, "path": _safe_path(entry.get("path"))})
        if cleaned:
            collector.add("suggestions", {"suggestions": cleaned[:MAX_SUGGESTIONS]})
        return {"ok": True}
    if name == "propose_saved_search":
        keyword = str(args.get("keyword") or "").strip()[:200]
        if not keyword:
            return {"ok": False, "error": "keyword is required for a saved-search proposal."}
        category = str(args.get("category") or "")
        if category not in {"storage", "lighting", "supplies", "comfort", "toiletries", "decor", "other"}:
            category = ""
        collector.add(
            "saved_search_proposal",
            {
                "keyword": keyword,
                "category": category,
                "price_type": "free" if args.get("free_only") else "",
                "label": str(args.get("label") or "").strip()[:120] or keyword.title(),
            },
        )
        return {
            "ok": True,
            "note": "Proposal card shown. The alert does NOT exist yet — the user must tap Create.",
        }
    return {"ok": False, "error": f"Unknown UI tool: {name}"}


def _hydrate_listing_cards(listing_ids: Any) -> list[dict[str, Any]]:
    # The model may hand back a bare int or a string instead of a list.
    if not isinstance(listing_ids, list):
        listing_ids = [listing_ids] if listing_ids is not None else []
    ids: list[int] = []
    for value in listing_ids[: MAX_CARDS * 2]:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    listings = (
        Listing.objects.filter(
            id__in=ids,
            status=Listing.Status.AVAILABLE,
            available_until__gte=timezone.now(),
        )
        .exclude(moderation_status=Listing.ModerationStatus.FLAGGED)
    )
    by_id = {listing.id: listing for listing in listings}
    cards = []
    for listing_id in ids:            # preserve the model's relevance ordering
        listing = by_id.get(listing_id)
        if listing is None:
            continue
        thumb = None
        if listing.image_thumb:
            thumb = listing.image_thumb.url
        elif listing.image:
            thumb = listing.image.url
        cards.append(
            {
                "id": listing.id,
                "title": listing.title,
                "category": listing.category,
                "price_type": listing.price_type,
                "price_amount": str(listing.price_amount),
                "pickup_zone": listing.pickup_zone,
                "thumb": thumb,
            }
        )
        if len(cards) == MAX_CARDS:
            break
    return cards
