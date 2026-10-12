"""Risk Investigator: a controlled question-answering pipeline over risk data.

    question  ->  intent (deterministic classifier)
              ->  a fixed set of allowed queries for that intent
              ->  structured results (counts, ids, factor tallies)
              ->  explanation (deterministic templates, or an LLM that is
                  given ONLY the structured results and told to narrate them)

The model, when used, never touches the database and never sees a free-form
prompt from the user beyond the original question; every number in the answer
comes from a query this module ran, and the answer separates what the data
says (facts) from what an analyst might conclude (inferences), with the
transaction ids that back it (evidence).

Provider abstraction
    `Explainer` is a Protocol. `DeterministicExplainer` is the default and
    the mode used in tests and demos without a key. `GeminiExplainer` wraps
    the marketplace's existing Gemini client (free tier) and is chosen only
    when RISK_INVESTIGATOR_MODE=auto|llm and a key is configured; any
    provider failure falls back to the deterministic explainer so the
    endpoint always answers.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import Any, Callable, Protocol

from django.db.models import Count, Q
from django.utils import timezone

from ..models import Decision, Investigation, PaymentTransaction, RiskFactor, TransactionStatus
from . import metrics

logger = logging.getLogger("risk.investigator")

DETERMINISTIC_VERSION = "investigator-templates-v1"
EVIDENCE_LIMIT = 8

# ── Intents ──────────────────────────────────────────────────────────────────

INTENT_BLOCKED_TREND = "blocked_trend"
INTENT_TOP_REASONS = "top_block_reasons"
INTENT_NEW_DEVICE = "suspicious_new_device"
INTENT_REVIEW_QUEUE = "review_queue"
INTENT_RISK_PATTERN = "risk_pattern"
INTENT_HIGH_RISK = "high_risk_transactions"
INTENT_SUMMARY = "summary"

INTENTS: dict[str, str] = {
    INTENT_BLOCKED_TREND: "Why did blocked transactions change?",
    INTENT_TOP_REASONS: "Which risk factors drive blocks?",
    INTENT_NEW_DEVICE: "Which new-device transactions look suspicious?",
    INTENT_REVIEW_QUEUE: "What is waiting in the review queue?",
    INTENT_RISK_PATTERN: "Is there a velocity, device-sharing or card-testing pattern?",
    INTENT_HIGH_RISK: "Which transactions scored highest?",
    INTENT_SUMMARY: "Overall risk summary for the period",
}

# Classification works on whole words, never on substrings: "monitoring" does
# not contain the word "ring", "because" is not "cause", "exchange" is not
# "change". Each intent lists words and multi-word phrases; a question is
# given to the intent whose phrases cover the most of its words, so a specific
# phrase ("shared device", "top risk") outweighs a generic word it happens to
# contain ("device", "top"). Ties go to the intent listed first.
_INTENT_PHRASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (INTENT_NEW_DEVICE, (
        "new device", "unknown device", "unrecognised device", "unrecognized device", "unfamiliar device",
        "unseen device", "first seen device", "device",
    )),
    (INTENT_REVIEW_QUEUE, (
        "review queue", "under review", "pending review", "manual review", "awaiting review",
        "held", "hold", "waiting", "queue", "backlog",
    )),
    (INTENT_RISK_PATTERN, (
        "pattern", "velocity", "card testing", "coordinated", "ring", "multi account", "multiple accounts",
        "many accounts", "shared device", "device sharing", "same device", "burst", "rapid",
    )),
    (INTENT_TOP_REASONS, (
        "reason", "factor", "why are", "why were", "driving", "driver", "cause", "top", "most common",
    )),
    (INTENT_BLOCKED_TREND, (
        "increase", "spike", "trend", "more blocks", "blocked", "block", "declined", "change", "rise", "rising",
        "drop", "decrease",
    )),
    (INTENT_HIGH_RISK, (
        "highest", "riskiest", "top risk", "high risk", "most risky", "worst", "highest scoring", "highest risk",
    )),
)

_WINDOW_PHRASES: tuple[tuple[tuple[str, ...], int], ...] = (
    (("last hour", "past hour", "this hour"), 1),
    (("today", "24 hours", "24h", "last day", "past day", "yesterday"), 24),
    (("this week", "7 days", "past week", "last week", "weekly"), 24 * 7),
    (("this month", "30 days", "past month", "last month"), 24 * 30),
)

_SIMULATION_PHRASES = ("simulation", "simulated", "fraud lab", "synthetic", "lab run")

_WORD = re.compile(r"[a-z0-9]+")


def _stem(word: str) -> str:
    """Fold the plain inflections of a word together: block, blocks, blocked
    and blocking are one word to the classifier, as are device and devices.

    Deliberately crude and deliberately only ever applied to both sides of a
    whole-word comparison: it removes -ing / -ed / -es / -s and a final -e
    when at least three letters remain. It is not a general stemmer and does
    not need to be; it must merely never merge two words the phrase lists
    tell apart, which the tests pin down.
    """
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)]
            break
    if word.endswith("e") and len(word) > 3:
        word = word[:-1]
    return word


def _tokens(text: str) -> tuple[str, ...]:
    """Lower-cased word stems; punctuation and hyphens separate words."""
    return tuple(_stem(w) for w in _WORD.findall(text.lower()))


def _positions(tokens: tuple[str, ...], phrase: tuple[str, ...]) -> set[int]:
    """Indexes of the question's words covered by occurrences of `phrase`."""
    covered: set[int] = set()
    width = len(phrase)
    for start in range(len(tokens) - width + 1):
        if tokens[start:start + width] == phrase:
            covered.update(range(start, start + width))
    return covered


def _compile(phrases: tuple[str, ...]) -> tuple[tuple[str, ...], ...]:
    return tuple(_tokens(p) for p in phrases)


_INTENT_RULES: tuple[tuple[str, tuple[tuple[str, ...], ...]], ...] = tuple(
    (name, _compile(phrases)) for name, phrases in _INTENT_PHRASES
)
_WINDOW_RULES: tuple[tuple[tuple[tuple[str, ...], ...], int], ...] = tuple(
    (_compile(phrases), hours) for phrases, hours in _WINDOW_PHRASES
)
_SIMULATION_RULES = _compile(_SIMULATION_PHRASES)


def _mentions(tokens: tuple[str, ...], phrases: tuple[tuple[str, ...], ...]) -> bool:
    return any(_positions(tokens, phrase) for phrase in phrases)


@dataclass(frozen=True)
class Intent:
    name: str
    window_hours: int
    include_simulation: bool


def classify(question: str) -> Intent:
    tokens = _tokens(question)
    name = INTENT_SUMMARY
    best = 0
    for candidate, phrases in _INTENT_RULES:
        covered: set[int] = set()
        for phrase in phrases:
            covered |= _positions(tokens, phrase)
        if len(covered) > best:  # strictly more: earlier intents win ties
            name, best = candidate, len(covered)
    window = 24
    for phrases, hours in _WINDOW_RULES:
        if _mentions(tokens, phrases):
            window = hours
            break
    return Intent(name=name, window_hours=window, include_simulation=_mentions(tokens, _SIMULATION_RULES))


# ── Allowed queries ──────────────────────────────────────────────────────────
# Each returns (descriptor, result). Descriptors are persisted so a reader can
# see exactly what was asked of the database.

QueryFn = Callable[[Any, Intent], tuple[dict[str, Any], dict[str, Any]]]


def _scope(user: Any, intent: Intent) -> Any:
    qs = PaymentTransaction.objects.for_user(user)
    if not intent.include_simulation:
        qs = qs.filter(is_simulation=False)
    return qs


def _since(hours: int) -> Any:
    return timezone.now() - timedelta(hours=hours)


def q_decision_counts(user: Any, intent: Intent) -> tuple[dict[str, Any], dict[str, Any]]:
    now = timezone.now()
    current_start = now - timedelta(hours=intent.window_hours)
    previous_start = current_start - timedelta(hours=intent.window_hours)
    base = _scope(user, intent)

    def counts(start: Any, end: Any) -> dict[str, int]:
        rows = base.filter(created_at__gte=start, created_at__lt=end).values("decision").annotate(n=Count("id"))
        out: dict[str, int] = {Decision.ALLOW: 0, Decision.REVIEW: 0, Decision.BLOCK: 0}
        for r in rows:
            if r["decision"] in out:
                out[r["decision"]] = r["n"]
        out["total"] = sum(out.values())
        return out

    return (
        {"name": "decision_counts", "params": {"window_hours": intent.window_hours}},
        {"current": counts(current_start, now), "previous": counts(previous_start, current_start)},
    )


def q_top_factors(user: Any, intent: Intent, decision: str = Decision.BLOCK) -> tuple[dict[str, Any], dict[str, Any]]:
    txn_ids = _scope(user, intent).filter(created_at__gte=_since(intent.window_hours), decision=decision).values("id")
    rows = (
        RiskFactor.objects.filter(evaluation__transaction__in=txn_ids, points__gt=0)
        .values("code", "label")
        .annotate(n=Count("id"))
        .order_by("-n", "code")[:8]
    )
    return (
        {"name": "top_factors", "params": {"window_hours": intent.window_hours, "decision": decision}},
        {"factors": [{"code": r["code"], "label": r["label"], "count": r["n"]} for r in rows]},
    )


def _txn_rows(qs: Any, limit: int = EVIDENCE_LIMIT) -> list[dict[str, Any]]:
    return [
        {
            "id": t.public_id,
            "amount": str(t.amount),
            "currency": t.currency,
            "risk_score": t.risk_score,
            "decision": t.decision,
            "status": t.status,
            "device_id": t.device_id,
            "created_at": t.created_at.isoformat(),
        }
        for t in qs[:limit]
    ]


def q_new_device(user: Any, intent: Intent) -> tuple[dict[str, Any], dict[str, Any]]:
    base = _scope(user, intent).filter(created_at__gte=_since(intent.window_hours))
    flagged = base.filter(evaluations__factors__code="new_device").distinct()
    total = flagged.count()
    risky = flagged.filter(decision__in=[Decision.REVIEW, Decision.BLOCK]).order_by("-risk_score", "-created_at")
    return (
        {"name": "new_device_transactions", "params": {"window_hours": intent.window_hours}},
        {"new_device_total": total, "new_device_flagged": risky.count(), "transactions": _txn_rows(risky)},
    )


def q_review_queue(user: Any, intent: Intent) -> tuple[dict[str, Any], dict[str, Any]]:
    qs = _scope(user, intent).filter(status=TransactionStatus.REVIEW).order_by("-risk_score", "-created_at")
    oldest = qs.order_by("created_at").first()
    return (
        {"name": "review_queue", "params": {}},
        {
            "waiting": qs.count(),
            "oldest_waiting_hours": round((timezone.now() - oldest.created_at).total_seconds() / 3600, 1) if oldest else None,
            "transactions": _txn_rows(qs),
        },
    )


def q_patterns(user: Any, intent: Intent) -> tuple[dict[str, Any], dict[str, Any]]:
    base = _scope(user, intent).filter(created_at__gte=_since(intent.window_hours))
    busy_users = list(
        base.values("user_id").annotate(txns=Count("id")).filter(txns__gte=5).order_by("-txns")[:5]
    )
    card_testing = base.filter(evaluations__factors__code="card_testing").distinct()
    velocity = base.filter(evaluations__factors__code__in=["velocity_1h", "velocity_24h"]).distinct()
    result: dict[str, Any] = {
        "busy_accounts": busy_users,
        "card_testing_count": card_testing.count(),
        "velocity_count": velocity.count(),
        "transactions": _txn_rows(
            base.filter(Q(evaluations__factors__code__in=["card_testing", "velocity_1h", "device_sharing"]))
            .distinct().order_by("-risk_score", "-created_at")
        ),
    }
    if getattr(user, "is_staff", False):
        # Device sharing is a statement about several accounts, so it can
        # only be computed over everyone's payments. A customer's scope holds
        # one account: the query could never find anything there, and an
        # empty result would read as "we looked and your device is clean".
        # For non-staff the finding is left out instead of reported empty.
        result["shared_devices"] = list(
            base.exclude(device_id="")
            .values("device_id")
            .annotate(accounts=Count("user_id", distinct=True), txns=Count("id"))
            .filter(accounts__gte=3)
            .order_by("-accounts")[:5]
        )
    return ({"name": "patterns", "params": {"window_hours": intent.window_hours}}, result)


def q_high_risk(user: Any, intent: Intent) -> tuple[dict[str, Any], dict[str, Any]]:
    qs = (
        _scope(user, intent)
        .filter(created_at__gte=_since(intent.window_hours), risk_score__isnull=False)
        .order_by("-risk_score", "-created_at")
    )
    return (
        {"name": "high_risk_transactions", "params": {"window_hours": intent.window_hours, "limit": EVIDENCE_LIMIT}},
        {"transactions": _txn_rows(qs)},
    )


QUERY_PLANS: dict[str, tuple[QueryFn, ...]] = {
    INTENT_BLOCKED_TREND: (q_decision_counts, q_top_factors, q_high_risk),
    INTENT_TOP_REASONS: (q_top_factors, q_decision_counts),
    INTENT_NEW_DEVICE: (q_new_device, q_decision_counts),
    INTENT_REVIEW_QUEUE: (q_review_queue,),
    INTENT_RISK_PATTERN: (q_patterns, q_decision_counts),
    INTENT_HIGH_RISK: (q_high_risk, q_decision_counts),
    INTENT_SUMMARY: (q_decision_counts, q_top_factors, q_review_queue),
}


def run_queries(user: Any, intent: Intent) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    queries: list[dict[str, Any]] = []
    results: dict[str, Any] = {}
    for fn in QUERY_PLANS[intent.name]:
        descriptor, result = fn(user, intent)
        queries.append(descriptor)
        results[descriptor["name"]] = result
    return queries, results


# ── Explanation ──────────────────────────────────────────────────────────────

@dataclass
class Explanation:
    answer: str
    facts: list[str] = field(default_factory=list)
    inferences: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    model_version: str = DETERMINISTIC_VERSION
    mode: str = "deterministic"


class Explainer(Protocol):
    mode: str

    def explain(self, question: str, intent: Intent, results: dict[str, Any]) -> Explanation: ...


def _window_label(hours: int) -> str:
    if hours == 1:
        return "the last hour"
    if hours == 24:
        return "the last 24 hours"
    if hours % 24 == 0:
        return f"the last {hours // 24} days"
    return f"the last {hours} hours"


def _pct_change(current: int, previous: int) -> str:
    if previous == 0:
        return "up from zero" if current else "unchanged at zero"
    delta = (current - previous) / previous * 100
    return f"{'up' if delta >= 0 else 'down'} {abs(delta):.0f}%"


def _evidence_ids(results: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for section in results.values():
        for row in section.get("transactions", []) if isinstance(section, dict) else []:
            if row["id"] not in ids:
                ids.append(row["id"])
    return ids[:EVIDENCE_LIMIT]


class DeterministicExplainer:
    """Template narration. Every sentence maps to a number in `results`."""

    mode = "deterministic"

    def explain(self, question: str, intent: Intent, results: dict[str, Any]) -> Explanation:
        window = _window_label(intent.window_hours)
        facts: list[str] = []
        inferences: list[str] = []

        counts = results.get("decision_counts")
        if counts:
            cur, prev = counts["current"], counts["previous"]
            facts.append(
                f"In {window}: {cur['total']} transactions, {cur['block']} blocked, "
                f"{cur['review']} held for review, {cur['allow']} allowed."
            )
            facts.append(f"Blocks are {_pct_change(cur['block'], prev['block'])} versus the previous period ({prev['block']}).")
            if cur["total"] and cur["block"] / cur["total"] > 0.2:
                inferences.append(
                    "More than a fifth of traffic was blocked, which is unusual for organic traffic; "
                    "check whether a Fraud Lab run or a targeted attack is included in this window."
                )

        factors = (results.get("top_factors") or {}).get("factors") or []
        if factors:
            top = ", ".join(f"{f['label'].lower()} ({f['count']})" for f in factors[:3])
            facts.append(f"The most frequent positive factors behind blocked payments were: {top}.")
            codes = {f["code"] for f in factors[:3]}
            if {"new_device", "amount_vs_history"} <= codes:
                inferences.append("New device combined with above-average amounts is the signature of account takeover.")
            if "card_testing" in codes or "velocity_1h" in codes:
                inferences.append("Small rapid bursts point at card testing or an automated script rather than a person.")
            if "device_sharing" in codes:
                inferences.append("Several accounts on one device suggests multi-account or coordinated abuse.")
        elif "top_factors" in results:
            facts.append(f"No blocked transactions with scored factors in {window}.")

        nd = results.get("new_device_transactions")
        if nd:
            facts.append(
                f"{nd['new_device_total']} transactions in {window} came from a device the account had never used; "
                f"{nd['new_device_flagged']} of them were held or blocked."
            )
            if nd["new_device_flagged"]:
                lead = nd["transactions"][0]
                facts.append(f"The highest-scoring one is {lead['id']} at {lead['risk_score']} ({lead['decision']}).")
                inferences.append("New-device payments that also carry velocity or amount signals deserve manual review first.")

        rq = results.get("review_queue")
        if rq:
            facts.append(f"{rq['waiting']} payments are waiting for manual review.")
            if rq.get("oldest_waiting_hours") is not None:
                facts.append(f"The oldest has waited {rq['oldest_waiting_hours']} hours.")
            if rq["waiting"] > 10:
                inferences.append("A queue this long suggests sensitivity is set high for current volume, or reviewers are behind.")

        pat = results.get("patterns")
        if pat:
            facts.append(
                f"{pat['card_testing_count']} transactions matched the card-testing pattern and "
                f"{pat['velocity_count']} carried a velocity signal in {window}."
            )
            # Absent (not empty) when the reader's scope is a single account.
            sharing_checked = "shared_devices" in pat
            shared = pat.get("shared_devices") or []
            if shared:
                d = shared[0]
                facts.append(f"Device {d['device_id']} was used by {d['accounts']} different accounts ({d['txns']} transactions).")
                inferences.append("A device shared by three or more accounts in a day is consistent with a coordinated ring.")
            if pat["busy_accounts"]:
                b = pat["busy_accounts"][0]
                facts.append(f"The busiest account made {b['txns']} attempts.")
            if not (pat["card_testing_count"] or pat["velocity_count"] or shared):
                facts.append(
                    "No velocity, card-testing or device-sharing pattern was found."
                    if sharing_checked
                    else "No velocity or card-testing pattern was found in your own payments."
                )

        hr = results.get("high_risk_transactions")
        if hr and hr["transactions"]:
            top_txn = hr["transactions"][0]
            facts.append(f"The highest risk score in {window} was {top_txn['risk_score']} on {top_txn['id']} ({top_txn['decision']}).")
        elif hr:
            facts.append(f"No scored transactions in {window}.")

        if not facts:
            facts.append("No data matched the question in the selected window.")

        answer = " ".join(facts)
        if inferences:
            answer += " Possible interpretation: " + " ".join(inferences)
        return Explanation(answer=answer, facts=facts, inferences=inferences, evidence=_evidence_ids(results))


class GeminiExplainer:
    """Narrates the structured results with the marketplace's Gemini client.
    Facts and inferences still come from the deterministic explainer so the
    separation is enforced by code, not by prompt."""

    mode = "llm"

    def __init__(self, fallback: DeterministicExplainer | None = None) -> None:
        self.fallback = fallback or DeterministicExplainer()

    def explain(self, question: str, intent: Intent, results: dict[str, Any]) -> Explanation:
        from listings import ai_client

        base = self.fallback.explain(question, intent, results)
        prompt = (
            "You are a payments risk analyst writing for a colleague. Using ONLY the facts below, "
            "answer the question in at most four plain sentences. Do not invent numbers, ids or causes. "
            "If the facts cannot answer the question, say so.\n\n"
            f"Question: {question}\n"
            f"Facts (JSON): {json.dumps(base.facts)}\n"
            f"Analyst inferences you may mention, labelled as possibilities: {json.dumps(base.inferences)}\n"
        )
        try:
            text = ai_client.generate_text(prompt, max_tokens=220).strip()
        except Exception as exc:  # noqa: BLE001 - any provider failure falls back
            logger.warning("investigator LLM unavailable, using deterministic answer: %s", exc)
            return base
        if not text or not _grounded(text, base):
            return base
        return Explanation(
            answer=text,
            facts=base.facts,
            inferences=base.inferences,
            evidence=base.evidence,
            model_version=f"gemini:{ai_client._fast_model()}",
            mode=self.mode,
        )


# ── Grounding ────────────────────────────────────────────────────────────────
# A narration is accepted only if every quantity and every id in it can be
# found in the facts it was given. Rejection is cheap (the deterministic
# answer is used instead), so the checks lean towards rejecting.

_ID = re.compile(r"\b(?:txn|evt|sim|inv|agent)_[0-9A-Z]{26}\b")
_NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
_LETTERS = re.compile(r"[a-z]+")

_SMALL_NUMBERS: dict[str, int] = {
    "zero": 0, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_SCALES: dict[str, int] = {"hundred": 100, "thousand": 1000, "million": 1_000_000}
# "one" is a pronoun far more often than a count ("the highest-scoring one",
# "no one", "one of them"), so on its own it is only read as the number 1
# when it sits directly in front of something this domain counts. Without
# this, nearly every honest narration would be rejected.
_COUNTED_NOUNS = frozenset({
    "transaction", "transactions", "payment", "payments", "block", "blocks", "attempt", "attempts",
    "account", "accounts", "device", "devices", "network", "networks", "charge", "charges",
    "purchase", "purchases", "customer", "customers", "user", "users", "factor", "factors",
    "hour", "hours", "day", "days", "week", "weeks", "month", "months", "percent", "time", "times",
})


def _is_number_word(words: list[str], i: int) -> bool:
    word = words[i]
    if word in _SMALL_NUMBERS or word in _SCALES:
        return True
    if word != "one":
        return False
    before = words[i - 1] if i else ""
    after = words[i + 1:i + 3]
    if before in _SMALL_NUMBERS or (after and after[0] in _SCALES):  # "twenty one", "one hundred"
        return True
    return any(w in _COUNTED_NOUNS for w in after)  # "one blocked payment"


def _spelled_numbers(text: str) -> list[Decimal]:
    """Cardinal numbers written as words, as values: "twenty-five" -> 25.

    Ordinals and fractions (third, a fifth), vague amounts (dozens,
    hundreds) and the pronoun "one" are not numbers a fact can be checked
    against and are ignored.
    """
    words = _LETTERS.findall(text.lower())
    values: list[Decimal] = []
    i = 0
    while i < len(words):
        if not _is_number_word(words, i):
            i += 1
            continue
        total = current = 0
        while i < len(words):
            word = words[i]
            if word == "and" and current + total and i + 1 < len(words) and _is_number_word(words, i + 1):
                i += 1  # "one hundred and five"
                continue
            if not _is_number_word(words, i):
                break
            if word == "one":
                current += 1
            elif word in _SMALL_NUMBERS:
                current += _SMALL_NUMBERS[word]
            elif word == "hundred":
                current = max(current, 1) * 100
            else:
                total += max(current, 1) * _SCALES[word]
                current = 0
            i += 1
        values.append(Decimal(total + current))
    return values


def _quantities(text: str) -> set[Decimal]:
    """Every number in the text, digits or words, with ids taken out first so
    the digits inside txn_01M4... are not mistaken for counts."""
    plain = _ID.sub(" ", text)
    digits = {Decimal(n.replace(",", "")) for n in _NUMBER.findall(plain)}
    return digits | set(_spelled_numbers(plain))


def _grounded(text: str, base: Explanation) -> bool:
    """Reject a narrative that states a number or an id absent from the facts.

    Numbers are compared by value, so "3", "3.0" and "three" are the same
    claim, and "five blocked payments" is caught when the facts say 3.
    """
    source = " ".join(base.facts + base.inferences)
    if not _quantities(text) <= _quantities(source):
        return False
    known_ids = set(_ID.findall(source)) | set(base.evidence)
    return set(_ID.findall(text)) <= known_ids


def get_explainer() -> Explainer:
    mode = os.getenv("RISK_INVESTIGATOR_MODE", "deterministic").strip().lower()
    if mode in ("auto", "llm"):
        from listings.ai_client import ai_available

        if ai_available():
            return GeminiExplainer()
        if mode == "llm":
            logger.warning("RISK_INVESTIGATOR_MODE=llm but no Gemini key is configured; using deterministic mode")
    return DeterministicExplainer()


# ── Entry point ──────────────────────────────────────────────────────────────

MAX_QUESTION_LENGTH = 500


def investigate(*, user: Any, question: str, request_id: str = "", explainer: Explainer | None = None) -> Investigation:
    question = " ".join(question.split())[:MAX_QUESTION_LENGTH]
    if not question:
        raise ValueError("A question is required.")
    explainer = explainer or get_explainer()
    with metrics.Timer() as timer:
        intent = classify(question)
        queries, results = run_queries(user, intent)
        explanation = explainer.explain(question, intent, results)
    record = Investigation.objects.create(
        user=user,
        question=question,
        intent=intent.name,
        queries=queries,
        results=results,
        answer=explanation.answer,
        facts=explanation.facts,
        inferences=explanation.inferences,
        evidence=explanation.evidence,
        mode=explanation.mode,
        model_version=explanation.model_version,
        latency_ms=str(timer.ms),
    )
    metrics.increment("investigations_total")
    return record


SUGGESTED_QUESTIONS = (
    "Why did blocked transactions increase today?",
    "Which risk factors are driving blocks this week?",
    "Show suspicious new device transactions in the last 24 hours",
    "What is waiting in the review queue?",
    "Is there a card testing or velocity pattern today?",
    "Which transactions scored highest this week?",
)
