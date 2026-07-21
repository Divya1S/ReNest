"""Operational core of the Nest Concierge (Google Gemini).

This module owns everything *around* the model conversation: provider client,
bounded retries with backoff, a cache-backed circuit breaker, the kill switch,
two-layer conversation memory, the semantic response cache, and persistence.
The agent harness itself (plan → execute → reflect, generative UI) lives in
concierge.orchestrator; model choice per turn lives in concierge.router.

The whole package stays isolated from the marketplace: failures here surface
as a degraded chat reply, never as an error in any marketplace flow.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from django.conf import settings
from django.core.cache import cache

from . import orchestrator, router, semantic_cache
from .models import ConciergeMessage, ConciergeThread

logger = logging.getLogger(__name__)

WINDOW_MESSAGES = 12          # verbatim turns sent to the model
COMPRESS_THRESHOLD = 28       # unfolded rows that trigger a summary fold
MAX_ATTEMPTS = 3              # API attempts per model call (1 + 2 retries)
RETRY_DELAYS = (0.4, 1.2)     # seconds before retry 1 and retry 2
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

BREAKER_FAILURES_KEY = "concierge:breaker:failures"
BREAKER_OPEN_KEY = "concierge:breaker:open"
BREAKER_THRESHOLD = 3         # consecutive failed turns that open the breaker
BREAKER_COOLDOWN_SECONDS = 120

DEGRADED_REPLY = (
    "I'm having trouble reaching my assistant service right now, so I can't answer that. "
    "Everything else in ReNest works normally — please try me again in a couple of minutes."
)
QUOTA_REPLY = (
    "My AI provider says this server's daily request allowance is used up, so I can't "
    "answer right now. Everything else in ReNest works normally — I'll be back when the "
    "quota resets (free Gemini keys reset daily; a paid key removes the limit)."
)
OFFLINE_REPLY = (
    "The concierge isn't configured on this server yet, but all of ReNest works without it. "
    "Try Browse to find items or the Dashboard to manage your move-out."
)


def is_enabled() -> bool:
    has_key = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    return has_key and getattr(settings, "CONCIERGE_ENABLED", True)


def _get_client() -> Any:
    from google import genai

    # The client reads GEMINI_API_KEY / GOOGLE_API_KEY from the environment.
    # SDK-level retries stay off (single attempt per HTTP call) so the engine's
    # retry loop and circuit breaker see every real failure.
    return genai.Client(http_options={"timeout": 30_000})  # ms


# ── Circuit breaker ──────────────────────────────────────────────────────────

def breaker_is_open() -> bool:
    return bool(cache.get(BREAKER_OPEN_KEY))


def _record_failure() -> None:
    failures = int(cache.get(BREAKER_FAILURES_KEY) or 0) + 1
    cache.set(BREAKER_FAILURES_KEY, failures, 300)
    if failures >= BREAKER_THRESHOLD:
        cache.set(BREAKER_OPEN_KEY, True, BREAKER_COOLDOWN_SECONDS)
        logger.warning("concierge breaker opened after %s consecutive failures", failures)


def _record_success() -> None:
    cache.delete(BREAKER_FAILURES_KEY)
    cache.delete(BREAKER_OPEN_KEY)


# ── Model calls ──────────────────────────────────────────────────────────────

def _is_retryable(exc: Exception) -> bool:
    """Transient provider trouble: rate limits, 5xx, network. Duck-typed on the
    google-genai APIError `.code` so tests (and future SDKs) need no real
    exception classes."""
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    return getattr(exc, "code", None) in RETRYABLE_STATUS


def _call_model(client: Any, **kwargs: Any) -> Any:
    """One logical model call with bounded retries. Raises on final failure.

    A 429 on the reasoning model downgrades to the fast model right away
    instead of backing off: free-tier Gemini keys have zero quota for the
    pro tier, so retrying the same model can never succeed — and a fast
    answer beats a fail-safe apology.
    """
    last_error: Exception | None = None
    fast = getattr(settings, "CONCIERGE_MODEL_FAST", "gemini-flash-latest")
    for attempt in range(MAX_ATTEMPTS):
        try:
            return client.models.generate_content(**kwargs)
        except Exception as exc:  # noqa: BLE001 — classified below
            if getattr(exc, "code", None) == 429 and kwargs.get("model") != fast:
                kwargs = {**kwargs, "model": fast}
                last_error = exc
                continue  # different quota bucket — no backoff needed
            if not _is_retryable(exc):
                raise
            last_error = exc
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(RETRY_DELAYS[attempt])
    assert last_error is not None
    raise last_error


def _window_messages(thread: ConciergeThread) -> list[dict[str, Any]]:
    rows = list(
        thread.messages.filter(folded=False).order_by("-created_at", "-id")[:WINDOW_MESSAGES]
    )[::-1]
    return [{"role": row.role, "content": row.content} for row in rows]


# ── The turn pipeline ────────────────────────────────────────────────────────

def run_turn(
    user: Any,
    thread: ConciergeThread,
    text: str,
    client: Any | None = None,
    on_event: Any = None,
) -> dict[str, Any]:
    """Route → (semantic cache) → orchestrate → persist.

    Returns {"reply", "used_tools", "degraded", "meta", "message_id"}. Messages
    persist only on success so a degraded turn can simply be retried by the
    user. `on_event`, when given, receives progress dicts ({"phase": ...})
    while the turn runs — the streaming endpoint forwards them as SSE.
    """
    notify = on_event or (lambda event: None)
    if not is_enabled():
        return {"reply": OFFLINE_REPLY, "used_tools": [], "degraded": True, "meta": {}}
    if breaker_is_open():
        return {"reply": DEGRADED_REPLY, "used_tools": [], "degraded": True, "meta": {}}

    started = time.monotonic()
    route = router.classify(text)
    notify({"phase": "routing", "route": route.name})

    # Semantic cache — chat route only. That route can touch nothing but the
    # shared knowledge base, so cached answers are user-independent and safe.
    if route.name == "chat":
        hit = semantic_cache.lookup(text)
        if hit is not None:
            meta = {"route": route.as_meta(), "plan": None, "reflection": None,
                    "revised": False, "cached": True, "ui_blocks": hit["ui_blocks"],
                    "latency_ms": int((time.monotonic() - started) * 1000)}
            notify({"phase": "cached"})
            message_id = _persist(thread, text, hit["reply"], [], meta)
            return {"reply": hit["reply"], "used_tools": [], "degraded": False,
                    "meta": meta, "message_id": message_id}

    client = client or _get_client()

    def call(**kwargs: Any) -> Any:
        return _call_model(client, **kwargs)

    try:
        result = orchestrator.run(
            user,
            call,
            text=text,
            route=route,
            history=_window_messages(thread),
            summary=thread.summary,
            on_event=notify,
        )
    except Exception as exc:
        logger.exception("concierge turn failed")
        _record_failure()
        reply = QUOTA_REPLY if getattr(exc, "code", None) == 429 else DEGRADED_REPLY
        return {"reply": reply, "used_tools": [], "degraded": True,
                "meta": {"route": route.as_meta()}}

    _record_success()
    result["meta"]["latency_ms"] = int((time.monotonic() - started) * 1000)
    message_id = _persist(thread, text, result["reply"], result["used_tools"], result["meta"])

    if route.name == "chat":
        semantic_cache.store(text, result["reply"], result["meta"].get("ui_blocks") or [])

    try:
        _maybe_compress(thread, client)
    except Exception:  # pragma: no cover — compression is best-effort by design
        logger.exception("concierge memory compression failed")

    return {"reply": result["reply"], "used_tools": result["used_tools"],
            "degraded": False, "meta": result["meta"], "message_id": message_id}


def _persist(
    thread: ConciergeThread,
    user_text: str,
    reply: str,
    used_tools: list[str],
    meta: dict[str, Any],
) -> int:
    """Persist the turn; returns the assistant message id (feedback target)."""
    ConciergeMessage.objects.create(thread=thread, role=ConciergeMessage.Role.USER, content=user_text)
    assistant = ConciergeMessage.objects.create(
        thread=thread,
        role=ConciergeMessage.Role.ASSISTANT,
        content=reply,
        used_tools=used_tools,
        meta=meta,
    )
    thread.save(update_fields=["updated_at"])
    return assistant.id


def _maybe_compress(thread: ConciergeThread, client: Any) -> None:
    """Fold everything older than the verbatim window into thread.summary.

    Rows are marked folded (not deleted) so the widget's scrollback survives.
    If the summarisation call fails, fall back to a deterministic digest — the
    context window must stay bounded even when the provider is down.
    """
    unfolded = thread.messages.filter(folded=False)
    if unfolded.count() <= COMPRESS_THRESHOLD:
        return
    to_fold = list(unfolded.order_by("created_at", "id")[: unfolded.count() - WINDOW_MESSAGES])
    transcript = "\n".join(f"{row.role}: {row.content}" for row in to_fold)

    try:
        response = _call_model(
            client,
            model=getattr(settings, "CONCIERGE_MODEL_FAST", "gemini-flash-latest"),
            contents=[
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": (
                                f"Existing summary:\n{thread.summary or '(none)'}\n\n"
                                f"New turns:\n{transcript}"
                            )
                        }
                    ],
                }
            ],
            config={
                "system_instruction": (
                    "Compress this ReNest concierge conversation into at most 120 words. Keep "
                    "the user's goals, items discussed, decisions made, and any promised "
                    "follow-ups. Output only the summary text."
                ),
                "thinking_config": {"thinking_level": "low"},
                "max_output_tokens": 400,
            },
        )
        summary = orchestrator._text(response).strip()
    except Exception:
        logger.warning("concierge summariser unavailable; using deterministic digest")
        topics = [row.content[:60] for row in to_fold if row.role == ConciergeMessage.Role.USER][:8]
        summary = ((thread.summary + " ") if thread.summary else "") + "Earlier topics: " + "; ".join(topics)

    if summary:
        thread.summary = summary[:4000]
        thread.save(update_fields=["summary", "updated_at"])
    ConciergeMessage.objects.filter(id__in=[row.id for row in to_fold]).update(folded=True)
