"""Retrieval embeddings for the concierge: remote-first, deterministic fallback.

Two embedding spaces coexist, and vectors are always tagged with theirs:

  * ``gemini-v1`` — Gemini's embedding API (real semantic vectors; paraphrases
    like "explain the handoff process" ≈ "how do handoffs work" actually
    match). Used whenever the API key is present and the call succeeds.
  * ``hashed-v1`` — the original hashed bag-of-terms vectors: dependency-free,
    deterministic, zero-latency. The permanent offline fallback, and still the
    router's space (routing needs µs latency and has regex overrides doing the
    precision work).

The cardinal rule enforced everywhere: **vectors from different spaces are
never compared**. KnowledgeChunk rows and semantic-cache entries store their
space; retrieval embeds the query in the space of the stored corpus.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from typing import TypedDict

from django.conf import settings

from .models import KnowledgeChunk

logger = logging.getLogger(__name__)

VECTOR_DIM = 256                  # hashed space
REMOTE_DIM = 768                  # requested from the embedding API
SPACE_HASHED = "hashed-v1"
SPACE_REMOTE = "gemini-v1"

_WORD_RE = re.compile(r"[a-z0-9]+")

# Tiny stopword list — enough to keep glue words from dominating the buckets.
_STOPWORDS = frozenset(
    "a an and are as at be by can do does for from how i in is it my of on or "
    "the to what when where who will with you your".split()
)


class RetrievedChunk(TypedDict):
    slug: str
    title: str
    content: str
    score: float


# ── Hashed space (deterministic, offline) ────────────────────────────────────

def _stem(word: str) -> str:
    # Naive plural folding ("handoffs"→"handoff", "lamps"→"lamp"). Applied to
    # both documents and queries, so consistency matters more than accuracy.
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _terms(text: str) -> list[str]:
    words = [_stem(w) for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS]
    bigrams = [f"{a}_{b}" for a, b in zip(words, words[1:])]
    return words + bigrams


def _bucket(term: str) -> int:
    # Python's built-in hash() is salted per-process; md5 keeps vectors stable
    # across processes so seeded vectors match query-time vectors.
    return int.from_bytes(hashlib.md5(term.encode()).digest()[:4], "big") % VECTOR_DIM


def embed(text: str) -> list[float]:
    """Hashed-space embedding. Deterministic; always available."""
    vec = [0.0] * VECTOR_DIM
    for term in _terms(text):
        vec[_bucket(term)] += 1.0
    return _normalise(vec)


def _normalise(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec] if norm > 0 else vec


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):  # cross-space comparison guard — always score 0
        return 0.0
    return sum(x * y for x, y in zip(a, b))


# ── Remote space (Gemini embedding API) ──────────────────────────────────────

# After a remote failure, skip remote attempts for a while so an embedding-API
# outage costs one timeout, not one per request (and can't cause reseed churn).
_REMOTE_COOLDOWN_SECONDS = 300.0
_remote_down_until = 0.0


def remote_available() -> bool:
    import os
    import time

    if time.time() < _remote_down_until:
        return False
    return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))


def _note_remote_failure() -> None:
    import time

    global _remote_down_until
    _remote_down_until = time.time() + _REMOTE_COOLDOWN_SECONDS


def _embed_remote(texts: list[str], task_type: str) -> list[list[float]]:
    """Batch-embed via Gemini. Raises on any failure — callers decide fallback."""
    from typing import Any, cast

    from google import genai

    client = genai.Client(http_options={"timeout": 20_000})
    response = client.models.embed_content(
        model=getattr(settings, "AI_MODEL_EMBEDDING", "gemini-embedding-001"),
        # The SDK's contents union doesn't spell plain list[str], though it
        # accepts one at runtime.
        contents=cast(Any, texts),
        config={"task_type": task_type, "output_dimensionality": REMOTE_DIM},
    )
    embeddings = response.embeddings or []
    if len(embeddings) != len(texts):
        raise ValueError("embedding API returned a mismatched vector count")
    # Truncated-dimension vectors must be re-normalised for cosine to be valid.
    return [_normalise(list(e.values or [])) for e in embeddings]


def embed_documents(texts: list[str]) -> tuple[list[list[float]], str]:
    """Embed corpus documents in the best available space."""
    if remote_available():
        try:
            return _embed_remote(texts, "RETRIEVAL_DOCUMENT"), SPACE_REMOTE
        except Exception:
            logger.exception("remote embedding failed; falling back to hashed space")
            _note_remote_failure()
    return [embed(t) for t in texts], SPACE_HASHED


def embed_query(text: str, space: str) -> list[float] | None:
    """Embed a query in a *specific* space (the stored corpus's space).

    Returns None when that space is unreachable (remote corpus, API down) —
    callers treat that as "no results" rather than comparing junk.
    """
    if space == SPACE_HASHED:
        return embed(text)
    if not remote_available():
        return None
    try:
        return _embed_remote([text], "RETRIEVAL_QUERY")[0]
    except Exception:
        logger.exception("remote query embedding failed")
        _note_remote_failure()
        return None


# ── Retrieval ────────────────────────────────────────────────────────────────

def retrieve(query: str, limit: int = 3, min_score: float = 0.05) -> list[RetrievedChunk]:
    """Return the best-matching knowledge chunks for a natural-language query."""
    chunks = list(KnowledgeChunk.objects.all())
    if not chunks:
        return []
    space = chunks[0].vector_space or SPACE_HASHED
    query_vec = embed_query(query, space)
    if query_vec is None:
        return []
    # Real semantic vectors score higher across the board than lexical ones;
    # each space gets its own floor so thresholds stay meaningful.
    floor = min_score if space == SPACE_HASHED else 0.3
    scored: list[RetrievedChunk] = []
    for chunk in chunks:
        if (chunk.vector_space or SPACE_HASHED) != space:
            continue
        score = cosine(query_vec, list(chunk.vector))
        if score >= floor:
            scored.append(
                {
                    "slug": chunk.slug,
                    "title": chunk.title,
                    "content": chunk.content,
                    "score": round(score, 4),
                }
            )
    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored[:limit]
