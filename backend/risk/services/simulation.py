"""Fraud Lab: synthetic, labelled transactions scored by the real engine.

Nothing in this module invents a metric. A scenario generator produces
TransactionContexts with a ground-truth label (fraud or legitimate); every one
of them is pushed through the same `evaluate_transaction` that live payments
use; the numbers reported are counted from what the engine actually decided.

    precision_blocked  = fraud blocked / all blocked
    recall_blocked     = fraud blocked / all fraud
    precision_flagged  = fraud (review or block) / all (review or block)
    recall_flagged     = fraud (review or block) / all fraud
    false_positive_rate = legitimate (review or block) / all legitimate

Precision and recall are only defined because the data is synthetic and the
labels are known. For real traffic there is no ground truth and the API never
reports them.

Generators are deterministic for a given seed, so a run can be reproduced.
The profiles are hand-written caricatures of each fraud type (documented per
generator); they exist to exercise the engine, not to model real fraud.
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Callable

from django.db import transaction
from django.utils import timezone

from ..engine import MODEL_VERSION, RiskDecision, evaluate_transaction, rescore, thresholds_for
from ..engine.context import TransactionContext
from ..models import (
    Decision,
    PaymentTransaction,
    RiskEvaluation,
    RiskFactor,
    SimulationRun,
    SimulationScenario,
    TransactionStatus,
)
from . import audit, metrics

DEFAULT_COUNT = 1000
MIN_COUNT = 50
MAX_COUNT = 2000
RETAINED_RUNS_PER_USER = 6
SWEEP_POINTS = tuple(range(0, 101, 10))
_BATCH = 500

MERCHANTS = (
    ("mkt_textbooks", "books"), ("mkt_dorm_essentials", "home"), ("mkt_electronics", "electronics"),
    ("mkt_campus_cafe", "food"), ("mkt_bike_shop", "transport"), ("mkt_furniture", "home"),
    ("mkt_tickets", "events"), ("mkt_supplies", "supplies"),
)
COUNTRIES = ("US", "US", "US", "CA", "GB")


@dataclass(frozen=True)
class Labelled:
    context: TransactionContext
    is_fraud: bool
    profile: str


Generator = Callable[[random.Random, int, datetime], Labelled]


def _money(value: float) -> Decimal:
    return Decimal(str(round(max(0.5, value), 2)))


def _merchant(rng: random.Random) -> tuple[str, str]:
    return rng.choice(MERCHANTS)


def _base(rng: random.Random, i: int, now: datetime, **overrides: Any) -> TransactionContext:
    merchant_id, category = _merchant(rng)
    fields: dict[str, Any] = dict(
        transaction_id=f"sim-{i}",
        user_id=100_000 + i,
        merchant_id=merchant_id,
        merchant_category=category,
        amount=_money(rng.lognormvariate(3.3, 0.6)),  # median ~27, long tail
        currency="USD",
        payment_method=rng.choice(("card", "card", "card", "wallet", "campus_credit")),
        device_id=f"dev-{i}",
        ip_prefix=f"10.{rng.randint(0, 200)}.0.0",
        country=rng.choice(COUNTRIES),
        timestamp=now - timedelta(minutes=rng.randint(0, 59)),
        local_hour=rng.choice((8, 10, 12, 14, 16, 18, 20, 21, 22)),
    )
    fields.update(overrides)
    return TransactionContext(**fields)


# ── Legitimate profiles ──────────────────────────────────────────────────────

def legit_customer(rng: random.Random, i: int, now: datetime) -> Labelled:
    """An ordinary student. Most have history and their own device; a minority
    are brand-new accounts. Real customers also do slightly odd things (a new
    phone, a big textbook order, a late-night purchase, a card that failed
    once), so a share of legitimate rows carry one or two weak signals. That
    is what makes false positives possible and the friction curve honest."""
    roll = rng.random()
    if roll < 0.15:
        # New but honest customer: first purchase, usually modest.
        ctx = _base(
            rng, i, now,
            account_age_days=rng.uniform(0.1, 6),
            amount=_money(rng.lognormvariate(3.0, 0.5) if rng.random() > 0.08 else rng.uniform(200, 320)),
            txn_count_1h=rng.choice((0, 0, 0, 1)),
        )
        return Labelled(ctx, False, "legit_new_account")
    history = rng.randint(3, 40)
    avg = rng.lognormvariate(3.3, 0.5)
    quirk = rng.random()
    amount_multiplier = rng.uniform(0.4, 1.8)
    if quirk < 0.12:            # bigger than usual: textbooks, a bike, a desk
        amount_multiplier = rng.uniform(2.0, 4.5)
    new_phone = rng.random() < 0.10
    failed_once = rng.random() < 0.07
    late_night = rng.random() < 0.06
    travelling = rng.random() < 0.03
    ctx = _base(
        rng, i, now,
        account_age_days=rng.uniform(30, 900),
        historical_txn_count=history,
        successful_txn_count=history,
        historical_avg_amount=_money(avg),
        historical_currencies=("USD",),
        currency="EUR" if travelling else "USD",
        amount=_money(avg * amount_multiplier),
        merchant_seen_count=0 if rng.random() < 0.3 else rng.randint(1, 5),
        device_seen_count=0 if new_phone else rng.randint(3, 25),
        device_first_seen_days=None if new_phone else rng.uniform(20, 400),
        txn_count_24h=rng.randint(0, 2),
        txn_count_1h=rng.choice((0, 0, 0, 1)),
        failed_txn_count_24h=1 if failed_once else 0,
        distinct_devices_24h=2 if new_phone else 1,
        distinct_ips_24h=rng.choice((1, 1, 2)),
        local_hour=rng.choice((1, 2, 3)) if late_night else rng.choice((8, 10, 12, 14, 16, 18, 20, 21, 22)),
    )
    return Labelled(ctx, False, "legit_established")


# ── Fraud profiles ───────────────────────────────────────────────────────────

def card_testing(rng: random.Random, i: int, now: datetime) -> Labelled:
    """Stolen card validation: bursts of sub-$5 charges from a fresh account."""
    burst = rng.randint(4, 12)
    ctx = _base(
        rng, i, now,
        amount=_money(rng.uniform(0.5, 4.5)),
        account_age_days=rng.uniform(0.01, 0.9),
        txn_count_1h=burst,
        txn_count_24h=burst + rng.randint(0, 6),
        failed_txn_count_24h=rng.randint(0, 4),
        device_id=f"dev-ct-{rng.randint(1, 5)}",
        local_hour=rng.choice((2, 3, 4, 13, 23)),
    )
    return Labelled(ctx, True, "card_testing")


def account_takeover(rng: random.Random, i: int, now: datetime) -> Labelled:
    """A good account, suddenly on a new device from a new network, spending
    several times its average, often at night."""
    history = rng.randint(6, 40)
    avg = rng.lognormvariate(3.2, 0.4)
    ctx = _base(
        rng, i, now,
        account_age_days=rng.uniform(120, 900),
        historical_txn_count=history,
        successful_txn_count=history,
        historical_avg_amount=_money(avg),
        historical_currencies=("USD",),
        amount=_money(avg * rng.uniform(3.5, 12)),
        device_seen_count=0,
        distinct_devices_24h=rng.choice((1, 2, 3)),
        distinct_ips_24h=rng.choice((2, 3, 4, 5)),
        merchant_seen_count=0,
        failed_txn_count_24h=rng.choice((0, 1, 2)),
        local_hour=rng.choice((1, 2, 3, 4, 14)),
        currency=rng.choice(("USD", "USD", "EUR")),
    )
    return Labelled(ctx, True, "account_takeover")


def new_device_attack(rng: random.Random, i: int, now: datetime) -> Labelled:
    """Credential-stuffing style: established accounts, new device, moderate
    to high amounts. Deliberately subtler than a takeover."""
    history = rng.randint(4, 30)
    avg = rng.lognormvariate(3.2, 0.4)
    ctx = _base(
        rng, i, now,
        account_age_days=rng.uniform(60, 700),
        historical_txn_count=history,
        successful_txn_count=history,
        historical_avg_amount=_money(avg),
        historical_currencies=("USD",),
        amount=_money(avg * rng.uniform(1.8, 4.5)),
        device_seen_count=0,
        distinct_devices_24h=rng.choice((1, 2)),
        distinct_ips_24h=rng.choice((1, 2, 4)),
        merchant_seen_count=0,
        txn_count_1h=rng.choice((0, 1, 3)),
    )
    return Labelled(ctx, True, "new_device_attack")


def high_velocity(rng: random.Random, i: int, now: datetime) -> Labelled:
    """Many purchases in a short window, usually a newish account."""
    per_hour = rng.randint(3, 15)
    ctx = _base(
        rng, i, now,
        account_age_days=rng.uniform(0.5, 20),
        txn_count_1h=per_hour,
        txn_count_24h=per_hour + rng.randint(5, 20),
        amount=_money(rng.lognormvariate(3.6, 0.5)),
        distinct_ips_24h=rng.choice((1, 2, 4)),
        failed_txn_count_24h=rng.choice((0, 0, 1, 3)),
    )
    return Labelled(ctx, True, "high_velocity")


def suspicious_amount(rng: random.Random, i: int, now: datetime) -> Labelled:
    """One outsized purchase, sometimes on a known device (the hard case)."""
    history = rng.randint(2, 25)
    avg = rng.lognormvariate(3.0, 0.4)
    ctx = _base(
        rng, i, now,
        account_age_days=rng.uniform(10, 600),
        historical_txn_count=history,
        successful_txn_count=history,
        historical_avg_amount=_money(avg),
        historical_currencies=("USD",),
        amount=_money(max(avg * rng.uniform(6, 30), rng.choice((400, 900, 1400, 2400)))),
        device_seen_count=rng.choice((0, 0, 4)),
        merchant_seen_count=0,
    )
    return Labelled(ctx, True, "suspicious_amount")


def coordinated_fraud(rng: random.Random, i: int, now: datetime) -> Labelled:
    """A ring: many very new accounts, a handful of shared devices, rotating
    networks, similar amounts, all within the same hour."""
    ring_device = f"dev-ring-{rng.randint(1, 4)}"
    ctx = _base(
        rng, i, now,
        account_age_days=rng.uniform(0.02, 2),
        amount=_money(rng.uniform(120, 260)),
        device_id=ring_device,
        accounts_on_device_24h=rng.randint(3, 9),
        distinct_ips_24h=rng.choice((1, 4, 5, 6)),
        txn_count_1h=rng.choice((1, 2, 3, 5)),
        txn_count_24h=rng.randint(2, 10),
        local_hour=rng.choice((2, 3, 15)),
    )
    return Labelled(ctx, True, "coordinated_fraud")


def multi_account(rng: random.Random, i: int, now: datetime) -> Labelled:
    """One person, many accounts, one device: promo and referral abuse."""
    ctx = _base(
        rng, i, now,
        account_age_days=rng.uniform(0.01, 5),
        amount=_money(rng.lognormvariate(3.0, 0.4)),
        device_id=f"dev-multi-{rng.randint(1, 3)}",
        accounts_on_device_24h=rng.randint(3, 12),
        distinct_devices_24h=1,
        txn_count_24h=rng.randint(1, 9),
        failed_txn_count_24h=rng.choice((0, 1, 3)),
    )
    return Labelled(ctx, True, "multi_account")


@dataclass(frozen=True)
class ScenarioSpec:
    key: str
    title: str
    description: str
    fraud_share: float
    fraud_generator: Generator | None


SCENARIOS: dict[str, ScenarioSpec] = {
    SimulationScenario.NORMAL: ScenarioSpec(
        SimulationScenario.NORMAL, "Normal customer traffic",
        "Only legitimate customers. Measures how much friction the engine adds when nothing is wrong.",
        0.0, None,
    ),
    SimulationScenario.CARD_TESTING: ScenarioSpec(
        SimulationScenario.CARD_TESTING, "Card testing",
        "Bursts of tiny charges from fresh accounts validating stolen card numbers, mixed into normal traffic.",
        0.30, card_testing,
    ),
    SimulationScenario.ACCOUNT_TAKEOVER: ScenarioSpec(
        SimulationScenario.ACCOUNT_TAKEOVER, "Account takeover",
        "Good accounts hijacked: new device, new network, spend far above the owner's average.",
        0.25, account_takeover,
    ),
    SimulationScenario.NEW_DEVICE: ScenarioSpec(
        SimulationScenario.NEW_DEVICE, "New device attack",
        "Established accounts used from unseen devices with moderately elevated amounts. The subtle case.",
        0.30, new_device_attack,
    ),
    SimulationScenario.HIGH_VELOCITY: ScenarioSpec(
        SimulationScenario.HIGH_VELOCITY, "High velocity attack",
        "Accounts firing many purchases per hour.",
        0.30, high_velocity,
    ),
    SimulationScenario.SUSPICIOUS_AMOUNT: ScenarioSpec(
        SimulationScenario.SUSPICIOUS_AMOUNT, "Suspicious amount",
        "Single outsized purchases, sometimes from a trusted device.",
        0.25, suspicious_amount,
    ),
    SimulationScenario.COORDINATED: ScenarioSpec(
        SimulationScenario.COORDINATED, "Coordinated fraud",
        "A ring of brand-new accounts sharing devices and rotating networks in the same hour.",
        0.35, coordinated_fraud,
    ),
    SimulationScenario.MULTI_ACCOUNT: ScenarioSpec(
        SimulationScenario.MULTI_ACCOUNT, "Multiple-account attack",
        "One device driving many new accounts: promotion and referral abuse.",
        0.30, multi_account,
    ),
}


def generate(scenario: str, *, count: int, seed: int, now: datetime | None = None) -> list[Labelled]:
    spec = SCENARIOS[scenario]
    rng = random.Random(seed)
    now = now or timezone.now()
    fraud_count = round(count * spec.fraud_share) if spec.fraud_generator else 0
    rows: list[Labelled] = []
    for i in range(count):
        if spec.fraud_generator is not None and i < fraud_count:
            rows.append(spec.fraud_generator(rng, i, now))
        else:
            rows.append(legit_customer(rng, i, now))
    rng.shuffle(rows)
    return rows


# ── Metrics ──────────────────────────────────────────────────────────────────

def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def measure(rows: list[Labelled], decisions: list[RiskDecision], sensitivity: int) -> dict[str, Any]:
    """Count what the engine did against the labels. Every number here is a
    count of real engine outputs; ratios are None when undefined."""
    analyzed = len(rows)
    fraud = sum(1 for r in rows if r.is_fraud)
    legit = analyzed - fraud
    by_decision = Counter(d.decision for d in decisions)

    fraud_blocked = fraud_reviewed = legit_blocked = legit_reviewed = 0
    protected = held = friction = Decimal("0")
    bands: Counter[str] = Counter()
    factor_fraud: Counter[str] = Counter()
    factor_legit: Counter[str] = Counter()
    for row, decision in zip(rows, decisions):
        band = "high" if decision.risk_score >= 60 else "medium" if decision.risk_score >= 30 else "low"
        bands[band] += 1
        for code in decision.reasons:
            (factor_fraud if row.is_fraud else factor_legit)[code] += 1
        if row.is_fraud:
            if decision.decision == Decision.BLOCK:
                fraud_blocked += 1
                protected += row.context.amount
            elif decision.decision == Decision.REVIEW:
                fraud_reviewed += 1
                held += row.context.amount
        else:
            if decision.decision == Decision.BLOCK:
                legit_blocked += 1
                friction += row.context.amount
            elif decision.decision == Decision.REVIEW:
                legit_reviewed += 1
                friction += row.context.amount

    blocked = by_decision[Decision.BLOCK]
    reviewed = by_decision[Decision.REVIEW]
    flagged = blocked + reviewed
    fraud_flagged = fraud_blocked + fraud_reviewed
    legit_flagged = legit_blocked + legit_reviewed
    latency = metrics.summarize(d.evaluation_time_ms for d in decisions)
    thresholds = thresholds_for(sensitivity)

    return {
        "analyzed": analyzed,
        "allowed": by_decision[Decision.ALLOW],
        "reviewed": reviewed,
        "blocked": blocked,
        "labelled_fraud": fraud,
        "labelled_legitimate": legit,
        "fraud_blocked": fraud_blocked,
        "fraud_reviewed": fraud_reviewed,
        "fraud_missed": fraud - fraud_flagged,
        "legitimate_blocked": legit_blocked,
        "legitimate_reviewed": legit_reviewed,
        "precision_blocked": _ratio(fraud_blocked, blocked),
        "recall_blocked": _ratio(fraud_blocked, fraud),
        "precision_flagged": _ratio(fraud_flagged, flagged),
        "recall_flagged": _ratio(fraud_flagged, fraud),
        "false_positive_rate": _ratio(legit_flagged, legit),
        "protected_amount": str(protected),
        "held_amount": str(held),
        "friction_amount": str(friction),
        "risk_bands": {"low": bands["low"], "medium": bands["medium"], "high": bands["high"]},
        "top_factors_fraud": [{"code": c, "count": n} for c, n in factor_fraud.most_common(6)],
        "top_factors_legitimate": [{"code": c, "count": n} for c, n in factor_legit.most_common(6)],
        "latency": latency.as_dict(),
        "thresholds": {"sensitivity": thresholds.sensitivity, "review": thresholds.review, "block": thresholds.block},
        "model_version": MODEL_VERSION,
    }


def sweep(scores: list[tuple[int, bool]], points: tuple[int, ...] = SWEEP_POINTS) -> list[dict[str, Any]]:
    """Detection vs friction at every sensitivity, from stored scores.

    Scores are independent of sensitivity so this is a pure re-threshold.
    """
    fraud_total = sum(1 for _, f in scores if f)
    legit_total = len(scores) - fraud_total
    out: list[dict[str, Any]] = []
    for s in points:
        t = thresholds_for(s)
        fraud_flagged = legit_flagged = blocked = reviewed = 0
        for score, is_fraud in scores:
            d = rescore(score, s)
            if d == Decision.ALLOW:
                continue
            if d == Decision.BLOCK:
                blocked += 1
            else:
                reviewed += 1
            if is_fraud:
                fraud_flagged += 1
            else:
                legit_flagged += 1
        out.append({
            "sensitivity": s,
            "review_threshold": t.review,
            "block_threshold": t.block,
            "blocked": blocked,
            "reviewed": reviewed,
            "detection_rate": _ratio(fraud_flagged, fraud_total),
            "friction_rate": _ratio(legit_flagged, legit_total),
        })
    return out


# ── Run ──────────────────────────────────────────────────────────────────────

def _prune(user: Any) -> None:
    stale = list(
        SimulationRun.objects.filter(user=user)
        .order_by("-created_at", "-id")
        .values_list("id", flat=True)[RETAINED_RUNS_PER_USER:]
    )
    if stale:
        SimulationRun.objects.filter(id__in=stale).delete()


def run_scenario(
    *,
    user: Any,
    scenario: str,
    sensitivity: int,
    count: int = DEFAULT_COUNT,
    seed: int | None = None,
    request_id: str = "",
) -> SimulationRun:
    if scenario not in SCENARIOS:
        raise ValueError(f"Unknown scenario: {scenario}")
    count = max(MIN_COUNT, min(MAX_COUNT, int(count)))
    sensitivity = max(0, min(100, int(sensitivity)))
    seed = seed if seed is not None else random.SystemRandom().randint(1, 2**31 - 1)
    now = timezone.now()

    with metrics.Timer() as timer:
        rows = generate(scenario, count=count, seed=seed, now=now)
        decisions = [evaluate_transaction(r.context, sensitivity=sensitivity) for r in rows]
        results = measure(rows, decisions, sensitivity)
        results["sweep"] = sweep([(d.risk_score, r.is_fraud) for r, d in zip(rows, decisions)])
        results["seed"] = seed
        results["scenario"] = scenario

        with transaction.atomic():
            run = SimulationRun.objects.create(
                user=user,
                scenario=scenario,
                sensitivity=sensitivity,
                seed=seed,
                transaction_count=count,
                model_version=MODEL_VERSION,
                metrics=results,
            )
            _persist(run, user, rows, decisions, now)
            audit.record(
                action="simulation.run",
                target_type="simulation",
                target_id=run.public_id,
                actor=user,
                reason=f"Fraud Lab scenario {scenario} at sensitivity {sensitivity}",
                model_version=MODEL_VERSION,
                request_id=request_id,
                metadata={"scenario": scenario, "count": count, "seed": seed, "sensitivity": sensitivity},
            )
            _prune(user)

    run.duration_ms = Decimal(str(timer.ms))
    run.metrics = {**run.metrics, "duration_ms": timer.ms}
    run.save(update_fields=["duration_ms", "metrics"])
    metrics.observe("simulation_ms", timer.ms)
    metrics.increment("simulations_total")
    return run


_STATUS: dict[str, str] = {
    Decision.ALLOW: TransactionStatus.APPROVED,
    Decision.REVIEW: TransactionStatus.REVIEW,
    Decision.BLOCK: TransactionStatus.BLOCKED,
}


def _persist(run: SimulationRun, user: Any, rows: list[Labelled], decisions: list[RiskDecision], now: datetime) -> None:
    txns = [
        PaymentTransaction(
            user=user,
            merchant_id=r.context.merchant_id,
            merchant_category=r.context.merchant_category,
            amount=r.context.amount,
            currency=r.context.currency,
            payment_method=r.context.payment_method,
            device_id=r.context.device_id,
            ip_prefix=r.context.ip_prefix,
            country=r.context.country,
            status=_STATUS[d.decision],
            decision=d.decision,
            risk_score=d.risk_score,
            confidence=Decimal(str(d.confidence)),
            model_version=d.model_version,
            evaluation_ms=Decimal(str(d.evaluation_time_ms)),
            is_simulation=True,
            simulation_run=run,
            ground_truth_fraud=r.is_fraud,
            metadata={"profile": r.profile, "synthetic_user": r.context.user_id, "scenario": run.scenario},
            created_at=r.context.timestamp,
        )
        for r, d in zip(rows, decisions)
    ]
    PaymentTransaction.objects.bulk_create(txns, batch_size=_BATCH)
    evaluations = [
        RiskEvaluation(
            transaction=t,
            score=d.risk_score,
            decision=d.decision,
            confidence=Decimal(str(d.confidence)),
            model_version=d.model_version,
            sensitivity=d.thresholds.sensitivity,
            review_threshold=d.thresholds.review,
            block_threshold=d.thresholds.block,
            evaluation_ms=Decimal(str(d.evaluation_time_ms)),
            explanation=d.explanation,
            context_snapshot=d.context.snapshot(),
            created_at=now,
        )
        for t, d in zip(txns, decisions)
    ]
    RiskEvaluation.objects.bulk_create(evaluations, batch_size=_BATCH)
    factors = [
        RiskFactor(evaluation=e, code=f.code, label=f.label, points=f.points, detail=f.detail)
        for e, d in zip(evaluations, decisions)
        for f in d.factors
    ]
    RiskFactor.objects.bulk_create(factors, batch_size=_BATCH)


def scenario_catalogue() -> list[dict[str, Any]]:
    return [
        {"key": s.key, "title": s.title, "description": s.description, "fraud_share": s.fraud_share}
        for s in SCENARIOS.values()
    ]
