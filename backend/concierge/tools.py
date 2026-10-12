"""Read-only tools the concierge model can call.

Every handler queries the ORM with .values() and hard limits, and none of them
call .save()/.create()/.delete() on marketplace tables, so the concierge can look
things up but can never mutate marketplace state. tests.py proves this with a
query-log audit, so keep new tools read-only or move them behind explicit
user-confirmed endpoints instead.
"""

from __future__ import annotations

import json
from typing import Any

from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Count, Q, QuerySet

from listings.models import Listing, MoveOutTask, Reservation, RoomScanItemDraft, RoomScanSession
from listings.views.helpers import marketplace_listing_queryset

from . import rag

MAX_RESULTS = 6
# A search query is model-written free text; bound what it can turn into SQL.
MAX_QUERY_TERMS = 6
MAX_TERM_CHARS = 40

# Gemini function declarations ({name, description, parameters}). Gemini has
# no strict-schema mode, so handlers stay defensive (.get() + coercion).
TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_listings",
        "description": (
            "Search the live campus marketplace for available items. Call this whenever the "
            "user asks what's available, wants a specific item, or asks about prices. Returns "
            "up to 6 currently-available listings that this user is allowed to see. Every "
            "keyword must appear in a listing's title or description, so use a few plain item "
            "words. Refer to a result by its id and link it as /listings/<id>."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Item keywords, e.g. 'desk lamp'. All of them must match the title or "
                        "description. Empty string returns the newest listings."
                    ),
                },
                "category": {
                    "type": "string",
                    "enum": ["storage", "lighting", "supplies", "comfort", "toiletries", "decor", "other"],
                    "description": "Optional category filter; omit for all categories.",
                },
                "free_only": {
                    "type": "boolean",
                    "description": "True to return only free items.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_my_activity",
        "description": (
            "Fetch the current user's own marketplace activity: listings they posted (with "
            "pending pickup requests) and reservations they made. Call this for questions like "
            "'what have I posted', 'any requests on my lamp', or 'when is my pickup'."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_move_out_progress",
        "description": (
            "Fetch the current user's room-scan sessions with draft-item triage counts and "
            "move-out task progress. Call this for questions about their scan, publish queue, "
            "checklist, or how close they are to being done moving out."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "search_help",
        "description": (
            "Search the ReNest help knowledge base for how the product works: reserving, "
            "handoff PINs, posting, room scan, pricing rules, safety, alerts, privacy. Call "
            "this before answering any how-does-ReNest-work question so answers stay grounded. "
            "Answer from the returned sections; if they do not cover the question, say you are "
            "not sure."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The user's question, rephrased as a search query."},
            },
            "required": ["question"],
        },
    },
]


def visible_listings(user: Any) -> QuerySet[Listing]:
    """Listings the concierge may show this user: exactly what browse shows.

    Built on the marketplace's own visibility function instead of a local
    copy of its rules. The local copy checked only status, deadline and
    moderation, so the assistant could surface a listing from someone the user
    had blocked, from another campus, or from the demo set, none of which the
    user can open. Reserved listings are visible in browse but cannot be
    requested, so the concierge narrows to available ones.
    """
    return marketplace_listing_queryset(user).filter(status=Listing.Status.AVAILABLE)


def query_terms(query: str) -> list[str]:
    """Split a search query into the words every result must contain.

    The query used to be matched as one contiguous substring, so "desk lamp
    black" found nothing unless a listing contained that exact phrase. Terms
    are stemmed the same way the knowledge base is ("lamps" also finds "lamp")
    and glue words are dropped, since requiring "a" or "the" in a title would
    reject good matches.
    """
    terms: list[str] = []
    for word in rag.content_words(query):
        term = word[:MAX_TERM_CHARS]
        if term not in terms:
            terms.append(term)
    return terms[:MAX_QUERY_TERMS]


def _search_listings(user: Any, tool_input: dict[str, Any]) -> dict[str, Any]:
    queryset = visible_listings(user).order_by("-created_at")
    for term in query_terms(str(tool_input.get("query") or "")):
        queryset = queryset.filter(Q(title__icontains=term) | Q(description__icontains=term))
    if tool_input.get("category"):
        queryset = queryset.filter(category=tool_input["category"])
    if tool_input.get("free_only"):
        queryset = queryset.filter(price_type=Listing.PriceType.FREE)
    results = list(
        queryset.values(
            "id", "title", "category", "condition", "price_type", "price_amount", "pickup_zone", "available_until"
        )[:MAX_RESULTS]
    )
    return {"count": len(results), "listings": results}


def _get_my_activity(user: Any, tool_input: dict[str, Any]) -> dict[str, Any]:
    my_listings = list(
        Listing.objects.filter(owner=user)
        .annotate(pending_requests=Count("reservations", filter=Q(reservations__status=Reservation.Status.REQUESTED)))
        .order_by("-created_at")
        .values("id", "title", "status", "category", "price_type", "pending_requests", "available_until")[:10]
    )
    my_reservations = list(
        Reservation.objects.filter(claimant=user)
        .order_by("-updated_at")
        .values(
            "id",
            "status",
            "pickup_time_window",
            "confirmed_slot",
            "listing__id",
            "listing__title",
            "listing__pickup_zone",
        )[:10]
    )
    return {"my_listings": my_listings, "my_reservations": my_reservations}


def _get_move_out_progress(user: Any, tool_input: dict[str, Any]) -> dict[str, Any]:
    sessions = []
    for session in RoomScanSession.objects.filter(owner=user).order_by("-updated_at")[:5]:
        draft_counts = dict(
            RoomScanItemDraft.objects.filter(scan_session=session)
            .values_list("triage_status")
            .annotate(n=Count("id"))
        )
        task_total = MoveOutTask.objects.filter(scan_session=session).count()
        task_done = MoveOutTask.objects.filter(scan_session=session, status=MoveOutTask.Status.DONE).count()
        sessions.append(
            {
                "id": session.id,
                "name": session.name,
                "status": session.status,
                "progress_percent": session.progress_percent,
                "draft_items_by_triage": draft_counts,
                "tasks_done": task_done,
                "tasks_total": task_total,
            }
        )
    return {"scan_sessions": sessions}


def _search_help(user: Any, tool_input: dict[str, Any]) -> dict[str, Any]:
    chunks = rag.retrieve(str(tool_input.get("question", "")))
    return {"sections": [{"title": c["title"], "content": c["content"]} for c in chunks]}


_HANDLERS = {
    "search_listings": _search_listings,
    "get_my_activity": _get_my_activity,
    "get_move_out_progress": _get_move_out_progress,
    "search_help": _search_help,
}


class _ToolResultEncoder(DjangoJSONEncoder):
    """DjangoJSONEncoder, with str() only for types it does not know.

    Passing `default=str` to json.dumps replaces the encoder's own `default`,
    which silently turned every datetime into "2026-09-20 12:00:00+00:00"
    instead of ISO 8601. Subclassing keeps the ISO formats and still never
    fails a tool call over an unexpected value.
    """

    def default(self, o: Any) -> Any:
        try:
            return super().default(o)
        except TypeError:
            return str(o)


def execute_tool(user: Any, name: str, tool_input: dict[str, Any]) -> tuple[str, bool]:
    """Run one tool call. Returns (json_result, is_error) and never raises,

    so a broken tool degrades to an error the model can recover from instead
    of failing the whole chat request.
    """
    handler = _HANDLERS.get(name)
    if handler is None:
        return json.dumps({"error": f"Unknown tool: {name}"}), True
    try:
        return json.dumps(handler(user, tool_input), cls=_ToolResultEncoder), False
    except Exception as exc:  # noqa: BLE001 (tool errors must not kill the turn)
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"}), True
