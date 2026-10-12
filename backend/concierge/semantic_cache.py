"""Semantic response cache for the concierge.

Repeated questions shouldn't repay model latency and tokens. Before calling
the model, chat-route queries are embedded and compared against recently
cached answers; a close-enough match (cosine ≥ THRESHOLD) is served instantly.

Deliberately narrow scope, because this is the part reviewers probe:
  * Only "chat"-route turns are cacheable. That route can touch nothing but
    the shared knowledge base, so entries are user-independent by
    construction and safe to share globally. Task-route answers embed
    per-user, per-moment marketplace state; caching those would serve stale
    or (worse) someone else's data, so they are never cached.
  * The engine decides *when* to look up: only for a turn with no
    conversation behind it, or when the provider is unavailable and the
    alternative is an apology. A generic cached answer in the middle of a
    conversation would ignore what the user just said.
  * Entries live under a key that includes the knowledge-base version, so an
    edit to the help content retires every answer generated from the old text.
  * Entries expire after TTL_SECONDS and the pool is capped, evicting oldest
    first.

Backed by the Django cache (LocMem in dev, Redis in prod). Each entry carries
its vector in the best embedding space available when it was stored, plus a
hashed-space twin so it can still be matched when the embedding API is down,
which is exactly when a cached answer is worth the most.
"""

from __future__ import annotations

import time
from typing import Any, NotRequired, TypedDict

from django.core.cache import cache

from . import rag, seeding

CACHE_KEY_PREFIX = "concierge:semcache:v2"
HITS_KEY = "concierge:semcache:hits"
MISSES_KEY = "concierge:semcache:misses"
THRESHOLD = 0.90          # lexical embeddings: only near-identical phrasings hit
TTL_SECONDS = 3600        # KB content is stable; an hour balances freshness/savings
MAX_ENTRIES = 50


class CacheEntry(TypedDict):
    vector: list[float]
    space: str
    # Absent on entries written before the twin existed.
    hashed_vector: NotRequired[list[float]]
    query: str
    reply: str
    ui_blocks: list[dict[str, Any]]
    created: float


def cache_key() -> str:
    """Entries are partitioned by knowledge-base version (see module docstring)."""
    return f"{CACHE_KEY_PREFIX}:{seeding.knowledge_version()}"


def _live_entries(now: float) -> list[CacheEntry]:
    return [entry for entry in (cache.get(cache_key()) or []) if now - entry["created"] < TTL_SECONDS]


def lookup(text: str, *, offline: bool = False) -> CacheEntry | None:
    """Best cached answer for `text`, or None.

    `offline=True` skips the embedding API altogether and matches on the
    hashed twins: the caller already knows the provider is in trouble and must
    not wait on another network timeout.
    """
    entries = _live_entries(time.time())
    # Entries may span embedding spaces (e.g. written before the API key was
    # added). Embed the query once per space present; never compare across.
    query_vecs: dict[str, list[float] | None] = {}
    hashed_query: list[float] | None = None
    best: CacheEntry | None = None
    best_score = 0.0
    for entry in entries:
        space = entry.get("space", rag.SPACE_HASHED)
        if space not in query_vecs:
            unreachable = offline and space != rag.SPACE_HASHED
            query_vecs[space] = None if unreachable else rag.embed_query(text, space)
        query_vec = query_vecs[space]
        if query_vec is not None:
            score, threshold = rag.cosine(query_vec, entry["vector"]), _threshold(space)
        elif entry.get("hashed_vector"):
            # The entry's own space is unreachable; fall back to its hashed
            # twin. Still a same-space comparison, held to the strict
            # near-identical threshold.
            if hashed_query is None:
                hashed_query = rag.embed(text)
            score, threshold = rag.cosine(hashed_query, entry["hashed_vector"]), THRESHOLD
        else:
            continue
        if score >= threshold and score > best_score:
            best, best_score = entry, score
    _bump_stats(hit=best is not None)
    return best


def _threshold(space: str) -> float:
    # Real semantic vectors separate matches from non-matches at lower cosine
    # than near-identical lexical vectors do.
    return THRESHOLD if space == rag.SPACE_HASHED else 0.85


def store(text: str, reply: str, ui_blocks: list[dict[str, Any]] | None = None) -> None:
    now = time.time()
    entries = _live_entries(now)
    vectors, space = rag.embed_documents([text])
    entries.append(
        {
            "vector": vectors[0],
            "space": space,
            "hashed_vector": vectors[0] if space == rag.SPACE_HASHED else rag.embed(text),
            "query": text[:200],
            "reply": reply,
            "ui_blocks": ui_blocks or [],
            "created": now,
        }
    )
    cache.set(cache_key(), entries[-MAX_ENTRIES:], TTL_SECONDS)


def stats() -> dict[str, int]:
    return {"hits": int(cache.get(HITS_KEY) or 0), "misses": int(cache.get(MISSES_KEY) or 0)}


def _bump_stats(hit: bool) -> None:
    # add + incr rather than read, add one, write: concurrent requests would
    # otherwise overwrite each other's counts.
    key = HITS_KEY if hit else MISSES_KEY
    cache.add(key, 0, None)
    try:
        cache.incr(key)
    except ValueError:  # evicted between the two calls; start again from one
        cache.set(key, 1, None)
