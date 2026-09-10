"""Turn rule results into a score, a confidence and a decision.

Score
    The signed rule points, summed and clamped to 0..100. Nothing here is a
    probability: 60 means "many strong signals", not "60% likely fraud".

Thresholds
    Derived from a single 0..100 sensitivity knob so the Fraud Lab can show the
    detection/friction trade-off by moving one control:

        review_threshold = 60 - 0.4 * sensitivity   (60 at 0, 40 at 50, 20 at 100)
        block_threshold  = 90 - 0.4 * sensitivity   (90 at 0, 70 at 50, 50 at 100)

    Both lines have the same slope, so the review band keeps a constant width
    of 30 points: raising sensitivity moves the whole window down, it never
    collapses REVIEW into BLOCK.

Confidence
    A heuristic in 0.50..0.99 describing how much evidence backed the decision
    and how far the score sat from a threshold. It is not calibrated and the
    API documents it as such.
"""

from __future__ import annotations

from dataclasses import dataclass

from .context import TransactionContext
from .rules import RuleResult

MODEL_VERSION = "risk-rules-v1"

REVIEW_INTERCEPT = 60
BLOCK_INTERCEPT = 90
SENSITIVITY_SLOPE = 0.4

DECISION_ALLOW = "allow"
DECISION_REVIEW = "review"
DECISION_BLOCK = "block"


@dataclass(frozen=True)
class Thresholds:
    sensitivity: int
    review: int
    block: int


def thresholds_for(sensitivity: int) -> Thresholds:
    s = max(0, min(100, int(sensitivity)))
    review = round(REVIEW_INTERCEPT - SENSITIVITY_SLOPE * s)
    block = round(BLOCK_INTERCEPT - SENSITIVITY_SLOPE * s)
    return Thresholds(sensitivity=s, review=review, block=block)


def score_from(results: list[RuleResult]) -> int:
    return max(0, min(100, sum(r.points for r in results)))


def decide(score: int, thresholds: Thresholds) -> str:
    if score >= thresholds.block:
        return DECISION_BLOCK
    if score >= thresholds.review:
        return DECISION_REVIEW
    return DECISION_ALLOW


def evidence_coverage(ctx: TransactionContext) -> float:
    """Fraction of signal groups the engine had real data for."""
    groups = (
        ctx.has_history,
        bool(ctx.device_id),
        ctx.account_age_days > 0,
        ctx.txn_count_24h > 0 or ctx.has_history,
        bool(ctx.ip_prefix),
    )
    return sum(1 for g in groups if g) / len(groups)


def confidence_for(score: int, thresholds: Thresholds, ctx: TransactionContext) -> float:
    coverage = evidence_coverage(ctx)
    margin = min(abs(score - thresholds.review), abs(score - thresholds.block)) / 100
    value = 0.50 + 0.30 * coverage + 0.19 * min(1.0, margin * 4)
    return round(min(0.99, value), 2)
