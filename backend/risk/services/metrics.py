"""Application-level observability for the risk subsystem.

Two sources, deliberately kept separate:

* Cache counters and latency reservoirs, recorded by the request middleware
  and the services as things happen. Cheap, approximate, process-local when
  the cache is LocMem and shared when it is Redis. They answer "what is the
  service doing right now".
* The database (PaymentTransaction / RiskEvaluation / PaymentEvent rows),
  which is the source of truth for decision counts and exact evaluation
  latency percentiles. They answer "what has the service done".

Percentiles are computed from a bounded reservoir of recent samples: the last
RESERVOIR_SIZE observations per metric. That is enough for P50/P95/P99 on a
dashboard and costs a single cache read per request. Nothing here is a
substitute for Prometheus at scale; /api/risk/metrics/prometheus exposes the
same numbers in text format so a real scraper can take over.
"""

from __future__ import annotations

import math
import statistics
import time
from dataclasses import dataclass
from typing import Any, Iterable

from django.core.cache import cache

RESERVOIR_SIZE = 500
COUNTER_TTL = 60 * 60 * 24 * 3
_PREFIX = "risk:metrics"


def _key(*parts: str) -> str:
    return ":".join((_PREFIX, *parts))


# ── Counters ─────────────────────────────────────────────────────────────────

COUNTERS = (
    "requests_total",
    "errors_total",
    "payments_created_total",
    "decisions_allow_total",
    "decisions_review_total",
    "decisions_block_total",
    "payments_completed_total",
    "payments_failed_total",
    "events_emitted_total",
    "events_processed_total",
    "events_dead_lettered_total",
    "idempotent_replays_total",
    "idempotency_conflicts_total",
    "simulations_total",
    "investigations_total",
)


def increment(name: str, amount: int = 1) -> None:
    key = _key("counter", name)
    try:
        cache.add(key, 0, COUNTER_TTL)
        cache.incr(key, amount)
    except ValueError:
        cache.set(key, amount, COUNTER_TTL)
    except Exception:
        # Metrics must never break a request.
        pass


def counter(name: str) -> int:
    try:
        return int(cache.get(_key("counter", name), 0) or 0)
    except Exception:
        return 0


def all_counters() -> dict[str, int]:
    return {name: counter(name) for name in COUNTERS}


# ── Latency reservoirs ───────────────────────────────────────────────────────

LATENCY_METRICS = (
    "risk_evaluation_ms",
    "payment_create_ms",
    "request_ms",
    "event_dispatch_ms",
    "simulation_ms",
)


def observe(name: str, value_ms: float) -> None:
    key = _key("latency", name)
    try:
        samples = cache.get(key) or []
        samples.append(round(float(value_ms), 3))
        if len(samples) > RESERVOIR_SIZE:
            samples = samples[-RESERVOIR_SIZE:]
        cache.set(key, samples, COUNTER_TTL)
    except Exception:
        pass


def samples(name: str) -> list[float]:
    try:
        return list(cache.get(_key("latency", name)) or [])
    except Exception:
        return []


def percentile(values: Iterable[float], pct: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    # Nearest-rank with linear interpolation between neighbours.
    rank = (len(ordered) - 1) * pct
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)


@dataclass(frozen=True)
class LatencySummary:
    count: int
    mean_ms: float | None
    p50_ms: float | None
    p95_ms: float | None
    p99_ms: float | None
    max_ms: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "mean_ms": self.mean_ms,
            "p50_ms": self.p50_ms,
            "p95_ms": self.p95_ms,
            "p99_ms": self.p99_ms,
            "max_ms": self.max_ms,
        }


def summarize(values: Iterable[float]) -> LatencySummary:
    data = [float(v) for v in values]
    if not data:
        return LatencySummary(0, None, None, None, None, None)
    r = lambda x: round(x, 3) if x is not None else None  # noqa: E731
    return LatencySummary(
        count=len(data),
        mean_ms=r(statistics.fmean(data)),
        p50_ms=r(percentile(data, 0.50)),
        p95_ms=r(percentile(data, 0.95)),
        p99_ms=r(percentile(data, 0.99)),
        max_ms=r(max(data)),
    )


def latency_summary(name: str) -> LatencySummary:
    return summarize(samples(name))


class Timer:
    """`with Timer() as t: ...; t.ms`"""

    def __enter__(self) -> "Timer":
        self._start = time.perf_counter()
        self.ms = 0.0
        return self

    def __exit__(self, *exc: Any) -> None:
        self.ms = round((time.perf_counter() - self._start) * 1000, 3)


def reset_all() -> None:
    """Test helper: clear every counter and reservoir."""
    for name in COUNTERS:
        cache.delete(_key("counter", name))
    for name in LATENCY_METRICS:
        cache.delete(_key("latency", name))


def prometheus_text() -> str:
    """Expose counters and latency quantiles in Prometheus text format."""
    lines: list[str] = []
    for name in COUNTERS:
        lines.append(f"# TYPE renest_risk_{name} counter")
        lines.append(f"renest_risk_{name} {counter(name)}")
    for name in LATENCY_METRICS:
        summary = latency_summary(name)
        metric = f"renest_risk_{name}"
        lines.append(f"# TYPE {metric} summary")
        for quantile, value in (("0.5", summary.p50_ms), ("0.95", summary.p95_ms), ("0.99", summary.p99_ms)):
            if value is not None:
                lines.append(f'{metric}{{quantile="{quantile}"}} {value}')
        lines.append(f"{metric}_count {summary.count}")
    return "\n".join(lines) + "\n"
