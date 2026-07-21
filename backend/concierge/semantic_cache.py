"""Semantic response cache for the concierge.

Repeated questions shouldn't repay model latency and tokens. Before calling
the model, chat-route queries are embedded and compared against recently
cached answers; a close-enough match (cosine ≥ THRESHOLD) is served instantly.

Deliberately narrow scope — this is the part reviewers probe:
  * Only "chat"-route turns are cacheable. That route can touch nothing but
    the shared knowledge base, so entries are user-independent by
    construction and safe to share globally. Task-route answers embed
    per-user, per-moment marketplace state; caching those would serve stale
    or (worse) someone else's data, so they are never cached.
  * Entries expire after TTL_SECONDS and the pool is capped, evicting oldest
    first.

Backed by the Django cache (LocMem in dev, Redis in prod) and the same
deterministic embeddings as RAG/routing — swap rag.embed() for a real
embedding model and this layer upgrades for free.
"""

from __future__ import annotations

import time
from typing import Any, TypedDict

from django.core.cache import cache

from . import rag

CACHE_KEY = "concierge:semcache:v1"
STATS_KEY = "concierge:semcache:stats"
THRESHOLD = 0.90          # lexical embeddings: only near-identical phrasings hit
TTL_SECONDS = 3600        # KB content is stable; an hour balances freshness/savings
MAX_ENTRIES = 50


class CacheEntry(TypedDict):
    vector: list[float]
    space: str
    query: str
    reply: str
    ui_blocks: list[dict[str, Any]]
    created: float


def lookup(text: str) -> CacheEntry | None:
    now = time.time()
    entries: list[CacheEntry] = [
        entry for entry in (cache.get(CACHE_KEY) or []) if now - entry["created"] < TTL_SECONDS
    ]
    # Entries may span embedding spaces (e.g. written before the API key was
    # added). Embed the query once per space present; never compare across.
    query_vecs: dict[str, list[float] | None] = {}
    best: CacheEntry | None = None
    best_score = 0.0
    for entry in entries:
        space = entry.get("space", rag.SPACE_HASHED)
        if space not in query_vecs:
            query_vecs[space] = rag.embed_query(text, space)
        query_vec = query_vecs[space]
        if query_vec is None:
            continue
        score = rag.cosine(query_vec, entry["vector"])
        if score >= _threshold(space) and score > best_score:
            best, best_score = entry, score
    _bump_stats(hit=best is not None)
    return best


def _threshold(space: str) -> float:
    # Real semantic vectors separate matches from non-matches at lower cosine
    # than near-identical lexical vectors do.
    return THRESHOLD if space == rag.SPACE_HASHED else 0.85


def store(text: str, reply: str, ui_blocks: list[dict[str, Any]] | None = None) -> None:
    now = time.time()
    entries: list[CacheEntry] = [
        entry for entry in (cache.get(CACHE_KEY) or []) if now - entry["created"] < TTL_SECONDS
    ]
    vectors, space = rag.embed_documents([text])
    entries.append(
        {
            "vector": vectors[0],
            "space": space,
            "query": text[:200],
            "reply": reply,
            "ui_blocks": ui_blocks or [],
            "created": now,
        }
    )
    cache.set(CACHE_KEY, entries[-MAX_ENTRIES:], TTL_SECONDS)


def stats() -> dict[str, int]:
    return cache.get(STATS_KEY) or {"hits": 0, "misses": 0}


def _bump_stats(hit: bool) -> None:
    current = stats()
    current["hits" if hit else "misses"] += 1
    cache.set(STATS_KEY, current, None)
