"""Golden-set evals: make prompt/router/KB changes regression-testable.

Two tiers:

  * **Offline** — router classification and KB retrieval against
    evals/golden.json. Deterministic, free, no network; runs in CI and as part
    of the test suite. Editing exemplars, the knowledge base, or embedding
    logic without breaking these is the bar for merging.
  * **Live** — full engine turns against the real Gemini API, graded with
    simple containment rules. Opt-in (`run_concierge_evals --live`): costs
    tokens and needs a key, so it's a pre-release smoke check, not CI.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

GOLDEN_PATH = Path(__file__).resolve().parent / "evals" / "golden.json"


@dataclass
class EvalReport:
    name: str
    passed: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.passed + len(self.failures)

    @property
    def ok(self) -> bool:
        return not self.failures


def load_golden() -> dict[str, Any]:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def run_router_evals(cases: list[dict[str, Any]] | None = None) -> EvalReport:
    from . import router

    report = EvalReport("router")
    for case in cases if cases is not None else load_golden()["router"]:
        route = router.classify(case["text"])
        if route.name == case["expect"]:
            report.passed += 1
        else:
            report.failures.append(
                f'"{case["text"]}" → {route.name} (expected {case["expect"]}; reason: {route.reason})'
            )
    return report


def run_retrieval_evals(cases: list[dict[str, Any]] | None = None) -> EvalReport:
    from . import rag
    from .seeding import ensure_seeded

    ensure_seeded()
    report = EvalReport("retrieval")
    for case in cases if cases is not None else load_golden()["retrieval"]:
        results = rag.retrieve(case["query"])
        titles = [r["title"] for r in results]
        needle = case["expect_title_contains"].lower()
        if any(needle in title.lower() for title in titles):
            report.passed += 1
        else:
            report.failures.append(f'"{case["query"]}" retrieved {titles} (expected ~"{needle}")')
    return report


def run_live_evals(user: Any, cases: list[dict[str, Any]] | None = None) -> EvalReport:
    """Full turns against the real provider. Requires GEMINI_API_KEY; costs tokens."""
    from . import engine
    from .models import ConciergeThread

    report = EvalReport("live")
    for case in cases if cases is not None else load_golden()["live"]:
        # A fresh thread per case: no cross-case memory contamination.
        ConciergeThread.objects.filter(user=user).delete()
        thread = ConciergeThread.objects.create(user=user)
        result = engine.run_turn(user, thread, case["message"])
        reply = result["reply"]
        problems = []
        if result["degraded"]:
            problems.append("degraded reply")
        if case.get("must_contain_any") and not any(s.lower() in reply.lower() for s in case["must_contain_any"]):
            problems.append(f'missing all of {case["must_contain_any"]}')
        for banned in case.get("must_not_contain", []):
            if banned.lower() in reply.lower():
                problems.append(f'contains banned "{banned}"')
        if problems:
            report.failures.append(f'"{case["message"]}" → {"; ".join(problems)} | reply: {reply[:160]}')
        else:
            report.passed += 1
    return report
