"""Semantic router: pick the right model + agent harness per message.

Two routes:
  - "chat"  → fast model, knowledge-base tool only, no planning. Greetings,
              thanks, and how-does-ReNest-work questions don't need a
              reasoning model or a tool plan; routing them small cuts both
              latency and token cost by an order of magnitude.
  - "task"  → reasoning model with the full read-only tool belt behind the
              Plan→Execute→Reflect harness. Anything touching live data or
              multi-step work lands here.

Classification is a short ladder of high-precision lexical rules followed by
nearest-exemplar matching over exact terms. Deliberately no LLM call: routing
must be free, instant, and unit-testable. The exemplar lists are the tuning
surface: extend them as new phrasings show up in logs, and add the phrasing
to evals/golden.json as a paraphrase rather than a copy.

The two routes are not symmetric. "task" is the stronger harness, so it is
the default and its rules may be generous: a wrongly escalated message costs
tokens. "chat" has to be earned by a positive signal, because a live-data
question answered by the chat harness (which cannot search) is a wrong answer.
"""

from __future__ import annotations

import re
from collections import Counter
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

# High-precision overrides, checked before exemplar similarity.
# Escape hatch first: the user can always demand the deep harness explicitly;
# the recourse for a misroute is asking harder, not being stuck.
_DEPTH_RE = re.compile(
    r"\b(think (hard|harder|carefully|deeply)|look carefully|double.?check|dig deeper|be thorough)\b",
    re.IGNORECASE,
)

# Pleasantries are recognised by vocabulary, not by a list of whole phrases:
# "ok cool thanks" and "awesome, really appreciate it!" are the same message
# as "thanks". A message is a pleasantry when every word is social filler and
# at least one word carries the greeting, thanks or acknowledgement.
# Bare "yes", "sure", "please" and "do" are deliberately absent: "yes please"
# and "ok do it" usually accept an offer the assistant just made, and acting
# on that needs the tool belt.
_PLEASANTRY_CORE = frozenset(
    "hi hey hello yo hiya howdy greetings morning afternoon evening "
    "thanks thank thx ty cheers appreciate appreciated "
    "ok okay kk cool nice great awesome perfect sweet neat alright brilliant wonderful lovely amazing "
    "good got sounds makes understood noted bye goodbye cya".split()
)
_PLEASANTRY_FILLER = frozenset(
    "you u so much very really a lot lots again that this it was is for the your all there everyone "
    "renest help helped helps helpful sense night day one have see later ya no problem worries "
    "to know i m im bunch ton tons million heaps mate man guys team buddy".split()
)
_PLEASANTRY_MAX_WORDS = 8
_WORD_RE = re.compile(r"[a-z0-9]+")

# Someone asking the marketplace for an item, as opposed to asking how the
# product works. These always win: a how-to phrased around live inventory
# ("how do I find a free lamp right now") still needs the search tool.
_LIVE_REQUEST_RE = re.compile(
    r"\b(find me|show me(?! how\b)|search for|look(ing)? for|in stock|up for grabs"
    r"|got any|any\s+(free|cheap|spare)\b|any\s+\w+\s+(left|free)"
    r"|find (a|an|any|some)\b|are there any\b"
    # "is there a way to ..." asks for a procedure, not for an item.
    r"|is there (a|an|any)\b(?! (way|fee|limit|rule|charge|cost|deadline|setting|option)\b)"
    r"|what(?:'?s| is) (new|available|out there)|anything (new|free|available)"
    r"|\bhave (a|an|any|some)\b)",
    re.IGNORECASE,
)
# Words that usually mean "live inventory" but are also how people ask about
# the product itself ("is the app available on iOS"). Checked after the
# product-subject rule below so the subject of the question decides.
_LIVE_STATE_RE = re.compile(r"\b(available|right now|currently)\b", re.IGNORECASE)
# The question is about ReNest as a product, not about anything stored in it.
# The product has to be the grammatical subject ("is the app ...", "does
# ReNest ..."): a mere mention ("what is available on ReNest right now") is
# still a question about inventory.
_PRODUCT = r"(renest|(the|this|your) (app|site|website|platform|service))"
_PRODUCT_SUBJECT_RE = re.compile(
    rf"\b(is|isn't|are|does|doesn't|do|can|will|has|did|was) {_PRODUCT}\b"
    rf"|\b{_PRODUCT} (is|does|works?|costs?|supports?|has|runs?)\b",
    re.IGNORECASE,
)
_ALERT_RE = re.compile(
    r"\b((alert|notify|ping|text|email|message) me\b|(let me know|tell me|heads up) (when|if|once)\b)",
    re.IGNORECASE,
)
# Procedural phrasing: the user wants steps, not their own data, even when the
# sentence names something of theirs ("how do I delete my listing").
_HOW_TO_RE = re.compile(
    r"\bhow (do|can|should|would|could) (i|we|you|one|someone|a student)\b"
    r"|\bhow to\b"
    r"|\b(best|right|easiest|quickest|proper) way to\b"
    r"|\bsteps? (to|for)\b|\bwalk me through\b|\b(is|are) there (a|any) ways? to\b"
    r"|\b(process|procedure) (for|to|of)\b"
    r"|\bhow (do|does) .{1,40}\bwork\b"
    r"|\bwhat (do|does) .{1,40}\bmean\b",
    re.IGNORECASE,
)
# Up to two words may sit between "my" and the noun ("my room scan", "my
# publish queue"); without that the sentence fell through to exemplar matching
# and a status question could land on the chat harness.
_MY_DATA_RE = re.compile(
    r"\bmy\s+(?:[\w'-]+\s+){0,2}?"
    r"(listing|item|reservation|pickup|request|scan|session|task|queue|move.?out|dashboard|draft|post|checklist)",
    re.IGNORECASE,
)
# Definitional phrasing. Weaker than _HOW_TO_RE, so it is checked after the
# personal-data rule: "explain why my listing was flagged" needs the tools.
_CONCEPT_RE = re.compile(r"\bexplain\b|\bwhat(\s+is|'s)\s+an?\s", re.IGNORECASE)

MIN_CHAT_CONFIDENCE = 0.15
# An exemplar counts as a match only when it shares at least this many terms
# (stemmed words or adjacent word pairs) with the message.
MIN_SHARED_TERMS = 2

_CHAT_TERMS = [(text, rag.term_counts(text)) for text in CHAT_EXEMPLARS]
_TASK_TERMS = [(text, rag.term_counts(text)) for text in TASK_EXEMPLARS]


@dataclass
class Route:
    name: str            # "chat" | "task"
    reason: str          # override rule or best-matching exemplar (for meta/debugging)
    confidence: float    # 1.0 for overrides; cosine score for exemplar matches

    def as_meta(self) -> dict[str, object]:
        return {"name": self.name, "reason": self.reason, "confidence": round(self.confidence, 3)}


# Route reasons other modules branch on.
REASON_PLEASANTRY = "pleasantry"


def is_pleasantry(text: str) -> bool:
    words = _WORD_RE.findall(text.lower())
    if not words or len(words) > _PLEASANTRY_MAX_WORDS:
        return False
    if not any(word in _PLEASANTRY_CORE for word in words):
        return False
    return all(word in _PLEASANTRY_CORE or word in _PLEASANTRY_FILLER for word in words)


def classify(text: str) -> Route:
    stripped = text.strip()
    if _DEPTH_RE.search(stripped):
        return Route("task", "user requested depth", 1.0)
    if is_pleasantry(stripped):
        return Route("chat", REASON_PLEASANTRY, 1.0)
    if _LIVE_REQUEST_RE.search(stripped):
        return Route("task", "asks about live availability", 1.0)
    if _ALERT_RE.search(stripped):
        return Route("task", "asks for an alert", 1.0)
    if _PRODUCT_SUBJECT_RE.search(stripped) and not _MY_DATA_RE.search(stripped):
        return Route("chat", "asks about the product", 1.0)
    if _LIVE_STATE_RE.search(stripped):
        return Route("task", "asks about live availability", 1.0)
    if _HOW_TO_RE.search(stripped):
        return Route("chat", "how-to phrasing", 1.0)
    if _MY_DATA_RE.search(stripped):
        return Route("task", "mentions user's own data", 1.0)
    if _CONCEPT_RE.search(stripped):
        return Route("chat", "asks what something is", 1.0)

    query_terms = rag.term_counts(stripped)
    chat_text, chat_score = _best(query_terms, _CHAT_TERMS)
    task_text, task_score = _best(query_terms, _TASK_TERMS)
    if task_score > chat_score:
        return Route("task", f"~ \"{task_text}\"", task_score)
    # "chat" is the weaker harness, so it must be earned: a real similarity
    # score over the floor. Anything unconfident goes to the reasoning model
    # and its tools rather than risking a shallow answer to a question we
    # didn't understand.
    if chat_score >= MIN_CHAT_CONFIDENCE:
        return Route("chat", f"~ \"{chat_text}\"", chat_score)
    return Route("task", "no confident exemplar match", 0.0)


def _best(query_terms: Counter[str], exemplars: list[tuple[str, Counter[str]]]) -> tuple[str, float]:
    """Best exemplar by cosine over exact terms.

    Matching used to run on the 256-bucket hashed vectors, where two sentences
    with no word in common can share buckets: "ok cool thanks" scored 0.52
    against "when is my pickup scheduled" and was sent to the task harness.
    Comparing the terms themselves makes a shared term mean a shared word.
    """
    best_text, best_score = "", 0.0
    for text, terms in exemplars:
        if len(query_terms.keys() & terms.keys()) < MIN_SHARED_TERMS:
            continue
        score = rag.sparse_cosine(query_terms, terms)
        if score > best_score:
            best_text, best_score = text, score
    return best_text, best_score
