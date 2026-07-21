"""Semantic router: pick the right model + agent harness per message.

Two routes:
  - "chat"  → fast model, knowledge-base tool only, no planning. Greetings,
              thanks, and how-does-ReNest-work questions don't need a
              reasoning model or a tool plan — routing them small cuts both
              latency and token cost by an order of magnitude.
  - "task"  → reasoning model with the full read-only tool belt behind the
              Plan→Execute→Reflect harness. Anything touching live data or
              multi-step work lands here.

Classification is nearest-exemplar over the same deterministic embeddings the
RAG layer uses, with a few high-precision lexical overrides. Deliberately no
LLM call: routing must be free, instant, and unit-testable. The exemplar lists
are the tuning surface — extend them as new phrasings show up in logs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import rag

CHAT_EXEMPLARS = [
    "hi",
    "hello there",
    "hey how are you",
    "thanks so much",
    "thank you that helped",
    "what is renest",
    "how does renest work",
    "how do handoffs work",
    "what is the handoff pin",
    "how do i post a listing",
    "how does room scan work",
    "is it safe to meet people for pickup",
    "what does low cost mean",
    "can i sell items for money",
    "how do saved search alerts work",
    "what happens to my data",
]

TASK_EXEMPLARS = [
    "what lamps are available right now",
    "find me free storage bins",
    "is there a desk fan i can pick up",
    "show me what's new in decor",
    "do i have any pickup requests on my listings",
    "when is my pickup scheduled",
    "what reservations do i have",
    "how is my move out going",
    "what's left in my publish queue",
    "check my scan session progress",
    "look at my listings and tell me which ones to reprice",
    "search for toiletries and compare the free ones",
    "what should i do next before my move out deadline",
    "alert me when a mini fridge shows up",
    "let me know if someone posts a desk lamp",
    "notify me about new free storage bins",
]

# High-precision overrides, checked before embedding similarity.
# Escape hatch first: the user can always demand the deep harness explicitly —
# the recourse for a misroute is asking harder, not being stuck.
_DEPTH_RE = re.compile(
    r"\b(think (hard|harder|carefully|deeply)|look carefully|double.?check|dig deeper|be thorough)\b",
    re.IGNORECASE,
)
_GREETING_RE = re.compile(
    r"^(hi|hey|hello|yo)( there| everyone| all| renest)?[\s!.,]*$"
    r"|^(thanks|thank you|thanks so much|ok|okay|cool|got it|sounds good)[\s!.,]*$",
    re.IGNORECASE,
)
_MY_DATA_RE = re.compile(
    r"\bmy\s+(listing|item|reservation|pickup|request|scan|session|task|queue|move.?out|dashboard)",
    re.IGNORECASE,
)
_LIVE_DATA_RE = re.compile(
    r"\b(available|right now|currently|in stock|find me|show me|search for|any\s+\w+\s+(left|free))\b",
    re.IGNORECASE,
)

_CHAT_VECTORS = [(text, rag.embed(text)) for text in CHAT_EXEMPLARS]
_TASK_VECTORS = [(text, rag.embed(text)) for text in TASK_EXEMPLARS]

MIN_CHAT_CONFIDENCE = 0.15


@dataclass
class Route:
    name: str            # "chat" | "task"
    reason: str          # override rule or best-matching exemplar (for meta/debugging)
    confidence: float    # 1.0 for overrides; cosine score for embedding matches

    def as_meta(self) -> dict[str, object]:
        return {"name": self.name, "reason": self.reason, "confidence": round(self.confidence, 3)}


def classify(text: str) -> Route:
    stripped = text.strip()
    if _DEPTH_RE.search(stripped):
        return Route("task", "user requested depth", 1.0)
    if _GREETING_RE.match(stripped):
        return Route("chat", "greeting", 1.0)
    if _MY_DATA_RE.search(stripped):
        return Route("task", "mentions user's own data", 1.0)
    if _LIVE_DATA_RE.search(stripped):
        return Route("task", "asks about live availability", 1.0)

    query_vec = rag.embed(stripped)
    chat_text, chat_score = _best(query_vec, _CHAT_VECTORS)
    task_text, task_score = _best(query_vec, _TASK_VECTORS)
    if task_score > chat_score:
        return Route("task", f"~ \"{task_text}\"", task_score)
    # "chat" is the weaker harness, so it must be earned: a real similarity
    # score over the floor. Anything unconfident goes to the reasoning model
    # and its tools rather than risking a shallow answer to a question we
    # didn't understand.
    if chat_score >= MIN_CHAT_CONFIDENCE:
        return Route("chat", f"~ \"{chat_text}\"", chat_score)
    return Route("task", "no confident exemplar match", 0.0)


def _best(query_vec: list[float], exemplars: list[tuple[str, list[float]]]) -> tuple[str, float]:
    best_text, best_score = "", 0.0
    for text, vector in exemplars:
        # A single shared hash bucket can be a collision, not a match — with
        # 256 buckets, one stray term overlapping a short exemplar ("hi")
        # scores ~0.45 on pure noise. Demand ≥2 shared buckets before trusting
        # the cosine; genuinely similar sentences always share several.
        overlap = sum(1 for q, e in zip(query_vec, vector) if q > 0 and e > 0)
        if overlap < 2:
            continue
        score = rag.cosine(query_vec, vector)
        if score > best_score:
            best_text, best_score = text, score
    return best_text, best_score
