"""The risk decision engine: evaluate_transaction(context) -> RiskDecision.

Pure and synchronous. No I/O, no database, no clock other than the timer that
measures itself. Build the context with risk.engine.signals (real payments) or
risk.services.simulation (Fraud Lab), then call evaluate_transaction.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .context import TransactionContext
from .explain import explain, summary_sentence
from .rules import RULES, Rule, RuleResult, run_rules
from .scoring import (
    MODEL_VERSION,
    Thresholds,
    confidence_for,
    decide,
    score_from,
    thresholds_for,
)


@dataclass(frozen=True)
class RiskDecision:
    decision: str                     # allow | review | block
    risk_score: int                   # 0..100
    confidence: float                 # 0.50..0.99, heuristic
    reasons: list[str]                # stable factor codes, strongest first
    factors: list[RuleResult]
    thresholds: Thresholds
    model_version: str
    evaluation_time_ms: float
    explanation: str
    summary: str
    context: TransactionContext = field(repr=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "riskScore": self.risk_score,
            "confidence": self.confidence,
            "reasons": self.reasons,
            "factors": [
                {"code": f.code, "points": f.points, "label": f.label, "detail": f.detail}
                for f in self.factors
            ],
            "thresholds": {
                "sensitivity": self.thresholds.sensitivity,
                "review": self.thresholds.review,
                "block": self.thresholds.block,
            },
            "modelVersion": self.model_version,
            "evaluationTimeMs": self.evaluation_time_ms,
            "explanation": self.explanation,
            "summary": self.summary,
        }


def evaluate_transaction(
    ctx: TransactionContext,
    *,
    sensitivity: int = 50,
    rules: tuple[Rule, ...] = RULES,
) -> RiskDecision:
    started = time.perf_counter()
    results = run_rules(ctx, rules)
    thresholds = thresholds_for(sensitivity)
    score = score_from(results)
    decision = decide(score, thresholds)
    confidence = confidence_for(score, thresholds, ctx)
    ordered = sorted(results, key=lambda r: (-abs(r.points), r.code))
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    return RiskDecision(
        decision=decision,
        risk_score=score,
        confidence=confidence,
        reasons=[r.code for r in ordered],
        factors=ordered,
        thresholds=thresholds,
        model_version=MODEL_VERSION,
        evaluation_time_ms=elapsed_ms,
        explanation=explain(score, decision, ordered, thresholds),
        summary=summary_sentence(score, decision, ordered),
        context=ctx,
    )


def rescore(score: int, sensitivity: int) -> str:
    """Re-derive a decision for an existing score at a different sensitivity.

    Scores do not depend on sensitivity, only thresholds do, so a sensitivity
    sweep over a stored dataset is a pure re-threshold: no rules re-run.
    """
    return decide(score, thresholds_for(sensitivity))


__all__ = [
    "MODEL_VERSION",
    "RULES",
    "RiskDecision",
    "RuleResult",
    "Thresholds",
    "TransactionContext",
    "evaluate_transaction",
    "rescore",
    "thresholds_for",
]
