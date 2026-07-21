#!/usr/bin/env python3
"""ReNest load test — the "move-out weekend" burst check.

Hammers the read-heavy anonymous paths (browse, search, impact, hubs, health)
with a weighted scenario mix and reports RPS + latency percentiles per
endpoint. Uses the backend venv's `requests`; no extra tooling.

Usage:
    # against local dev
    cd backend && USE_SQLITE=1 ../.venv/bin/python manage.py runserver &
    ../.venv/bin/python ../scripts/loadtest.py --users 20 --duration 30

    # against a deployment
    .venv/bin/python scripts/loadtest.py --base-url https://api.renest.app \
        --users 50 --duration 60 --max-error-rate 1

Exit code is non-zero when the error rate exceeds --max-error-rate, so this
can gate a deploy pipeline.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field

import requests

# (path, weight) — weights approximate real browse-heavy traffic.
SCENARIOS: list[tuple[str, int]] = [
    ("/api/listings?page=1", 35),
    ("/api/listings?search=lamp", 15),
    ("/api/listings?category=storage&price=free", 10),
    ("/api/impact", 15),
    ("/api/hubs", 10),
    ("/api/community/announcements", 5),
    ("/api/health/live", 5),
    ("/api/health/ready", 5),
]


@dataclass
class Stats:
    lock: threading.Lock = field(default_factory=threading.Lock)
    latencies: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    errors: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def record(self, path: str, elapsed: float, ok: bool) -> None:
        with self.lock:
            self.latencies[path].append(elapsed)
            if not ok:
                self.errors[path] += 1


def worker(base_url: str, stats: Stats, stop_at: float, timeout: float, worker_id: int) -> None:
    session = requests.Session()
    # Deterministic per-worker walk through the weighted mix.
    weighted = [path for path, weight in SCENARIOS for _ in range(weight)]
    index = worker_id * 7  # de-phase workers so they don't march in lockstep
    while time.monotonic() < stop_at:
        path = weighted[index % len(weighted)]
        index += 1
        started = time.monotonic()
        try:
            response = session.get(base_url + path, timeout=timeout)
            ok = response.status_code < 500
        except requests.RequestException:
            ok = False
        stats.record(path, time.monotonic() - started, ok)


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * pct))]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--users", type=int, default=20, help="concurrent workers (default 20)")
    parser.add_argument("--duration", type=int, default=30, help="seconds (default 30)")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--max-error-rate", type=float, default=5.0, help="fail threshold, %% (default 5)")
    args = parser.parse_args()

    print(f"→ {args.users} workers × {args.duration}s against {args.base_url}\n")
    stats = Stats()
    stop_at = time.monotonic() + args.duration
    threads = [
        threading.Thread(target=worker, args=(args.base_url, stats, stop_at, args.timeout, i), daemon=True)
        for i in range(args.users)
    ]
    started = time.monotonic()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    wall = time.monotonic() - started

    total = sum(len(v) for v in stats.latencies.values())
    total_errors = sum(stats.errors.values())
    if not total:
        print("No requests completed — is the server up?")
        return 1

    print(f"{'endpoint':<46}{'reqs':>7}{'err':>6}{'p50':>9}{'p95':>9}{'p99':>9}")
    for path in sorted(stats.latencies, key=lambda p: -len(stats.latencies[p])):
        values = stats.latencies[path]
        print(
            f"{path:<46}{len(values):>7}{stats.errors.get(path, 0):>6}"
            f"{percentile(values, 0.50) * 1000:>8.0f}ms"
            f"{percentile(values, 0.95) * 1000:>8.0f}ms"
            f"{percentile(values, 0.99) * 1000:>8.0f}ms"
        )

    everything = [value for values in stats.latencies.values() for value in values]
    error_rate = 100.0 * total_errors / total
    print(
        f"\nTOTAL {total} requests in {wall:.1f}s → {total / wall:.1f} req/s | "
        f"errors {total_errors} ({error_rate:.2f}%) | "
        f"p50 {percentile(everything, 0.5) * 1000:.0f}ms · "
        f"p95 {percentile(everything, 0.95) * 1000:.0f}ms · "
        f"mean {statistics.fmean(everything) * 1000:.0f}ms"
    )

    if error_rate > args.max_error_rate:
        print(f"\nFAIL: error rate {error_rate:.2f}% exceeds {args.max_error_rate}%")
        return 1
    print("\nPASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
