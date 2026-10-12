"""Human-readable explanations for a decision.

The explanation is generated from the same RuleResults that produced the score,
so it can never disagree with the number. Format mirrors what an analyst
expects to read:

    RISK SCORE: 91 / 100
    +34 Transaction amount is 6.8x above historical average
    +21 New device
    DECISION: BLOCK (block threshold 70 at sensitivity 50)
"""

from __future__ import annotations

from .rules import RuleResult
from .scoring import Thresholds

DECISION_SENTENCES = {
    "allow": "Approved automatically.",
    "review": "Held for manual review before any funds move.",
    "block": "Declined. The customer is told the payment could not be completed; no funds move.",
}


def factor_lines(results: list[RuleResult]) -> list[str]:
    ordered = sorted(results, key=lambda r: (-abs(r.points), r.code))
    return [f"{r.points:+d} {r.label}" for r in ordered]


def explain(score: int, decision: str, results: list[RuleResult], thresholds: Thresholds) -> str:
    lines = [f"RISK SCORE: {score} / 100"]
    if results:
        lines.extend(factor_lines(results))
    else:
        lines.append("No risk signals fired: nothing in this payment departed from the account's normal pattern.")
    threshold_note = (
        f"review at {thresholds.review}, block at {thresholds.block}, sensitivity {thresholds.sensitivity}"
    )
    lines.append(f"DECISION: {decision.upper()} ({threshold_note})")
    lines.append(DECISION_SENTENCES[decision])
    return "\n".join(lines)


def summary_sentence(score: int, decision: str, results: list[RuleResult]) -> str:
    """One line for feeds and notifications."""
    positives = sorted((r for r in results if r.points > 0), key=lambda r: -r.points)[:2]
    if decision == "allow":
        if not positives:
            return "Looked like normal activity for this account."
        return "Approved despite " + " and ".join(r.label.lower() for r in positives) + "."
    driver = " and ".join(r.label.lower() for r in positives) or "the combination of signals"
    verb = "Blocked" if decision == "block" else "Held for review"
    return f"{verb} because of {driver} (score {score})."
