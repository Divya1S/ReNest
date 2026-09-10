#!/usr/bin/env python3
"""Risk Intelligence load test: payment creation, risk evaluation, lookup, Fraud Lab.

Measures what the sandbox actually does on the machine it runs on and prints
avg / P50 / P95 / P99 latency, throughput and error rate per operation. The
numbers in the README were produced by this script; rerun it to reproduce
them on your own hardware.

    # 1. a pool of established sandbox customers (development database only)
    cd backend && USE_SQLITE=1 ../.venv/bin/python manage.py risk_loadtest_users \
        --count 8 --password 'loadtest-pass-123'
    # 2. a running backend
    USE_SQLITE=1 ../.venv/bin/python manage.py runserver 8000
    # 3. the test (session login through the normal auth endpoints; {i} is
    #    replaced by the session index so each worker is a different customer)
    ../.venv/bin/python ../scripts/risk_loadtest.py \
        --email 'loadtest-{i}@renest.local' --password 'loadtest-pass-123' \
        --users 8 --payments 480 --lookups 480 --simulations 3

One account firing hundreds of payments trips the velocity rules (that is
the engine working, not a bug), so a pool of customers is what makes the
measurement representative of ordinary ALLOW traffic. The login endpoint
allows 10 sign-ins per minute per address, so keep --users at 8 or wait a
minute between runs. The run is deliberately
modest: the target is a free-tier deployment and a laptop, not a benchmark
rig. Pass --json to save a machine-readable report.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import threading
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from random import Random

import requests

DEFAULT_PAYLOAD = {
    "merchant_id": "mkt_textbooks",
    "currency": "USD",
    "payment_method": "card",
    "merchant_category": "books",
}


@dataclass
class Stats:
    lock: threading.Lock = field(default_factory=threading.Lock)
    latencies: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    errors: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    evaluation_ms: list[float] = field(default_factory=list)
    decisions: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    # Payment ids per session: lookups must stay within the customer that
    # created them, because the API scopes reads to the owner (a cross-user
    # lookup is a 404 by design, not a measurement).
    ids: dict[int, list[str]] = field(default_factory=lambda: defaultdict(list))

    def record(self, op: str, elapsed: float, ok: bool) -> None:
        with self.lock:
            self.latencies[op].append(elapsed * 1000)
            if not ok:
                self.errors[op] += 1


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = (len(ordered) - 1) * pct
    lo, hi = int(rank), min(int(rank) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (rank - lo)


def login(base_url: str, email: str, password: str) -> requests.Session:
    session = requests.Session()
    session.get(f"{base_url}/api/auth/csrf", timeout=10)
    csrf = session.cookies.get("csrftoken", "")
    response = session.post(
        f"{base_url}/api/auth/login",
        json={"email": email, "password": password},
        headers={"X-CSRFToken": csrf, "Referer": base_url},
        timeout=10,
    )
    response.raise_for_status()
    return session


def headers(session: requests.Session, base_url: str) -> dict[str, str]:
    return {"X-CSRFToken": session.cookies.get("csrftoken", ""), "Referer": base_url}


def payment_worker(session: requests.Session, base_url: str, stats: Stats, count: int, seed: int, timeout: float) -> None:
    rng = Random(seed)
    for _ in range(count):
        amount = str(Decimal(str(round(rng.lognormvariate(3.3, 0.6), 2))))
        body = {**DEFAULT_PAYLOAD, "amount": amount, "device_id": f"load-{seed}"}
        started = time.perf_counter()
        ok = False
        try:
            response = session.post(
                f"{base_url}/api/risk/payments", json=body, timeout=timeout,
                headers={**headers(session, base_url), "Idempotency-Key": str(uuid.uuid4())},
            )
            ok = response.status_code == 201
            if ok:
                data = response.json()
                with stats.lock:
                    stats.ids[seed].append(data["public_id"])
                    stats.decisions[data["decision"]] += 1
                    if data.get("evaluation_ms") is not None:
                        stats.evaluation_ms.append(float(data["evaluation_ms"]))
        except requests.RequestException:
            ok = False
        stats.record("payment_create", time.perf_counter() - started, ok)


def lookup_worker(session: requests.Session, base_url: str, stats: Stats, count: int, seed: int, timeout: float) -> None:
    rng = Random(seed)
    for _ in range(count):
        with stats.lock:
            own = stats.ids.get(seed) or []
            target = rng.choice(own) if own else None
        if target is None:
            return
        started = time.perf_counter()
        try:
            response = session.get(f"{base_url}/api/risk/payments/{target}", timeout=timeout)
            ok = response.status_code == 200
        except requests.RequestException:
            ok = False
        stats.record("payment_lookup", time.perf_counter() - started, ok)


def simulation_runs(session: requests.Session, base_url: str, stats: Stats, count: int, timeout: float) -> None:
    for i in range(count):
        started = time.perf_counter()
        try:
            response = session.post(
                f"{base_url}/api/risk/simulations",
                json={"scenario": "account_takeover", "count": 1000, "seed": 100 + i},
                headers=headers(session, base_url), timeout=timeout,
            )
            ok = response.status_code == 201
            if ok:
                metrics = response.json()["metrics"]
                with stats.lock:
                    stats.latencies["simulation_engine_only"].append(float(metrics["duration_ms"]))
        except requests.RequestException:
            ok = False
        stats.record("simulation_1000", time.perf_counter() - started, ok)


def run_parallel(workers: list[threading.Thread]) -> float:
    started = time.perf_counter()
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    return time.perf_counter() - started


def report(name: str, values: list[float], errors: int, wall: float | None) -> dict[str, float | int | None]:
    return {
        "operation": name,
        "count": len(values),
        "errors": errors,
        "error_rate": round(errors / len(values), 4) if values else None,
        "avg_ms": round(statistics.fmean(values), 2) if values else None,
        "p50_ms": round(percentile(values, 0.50), 2),
        "p95_ms": round(percentile(values, 0.95), 2),
        "p99_ms": round(percentile(values, 0.99), 2),
        "max_ms": round(max(values), 2) if values else None,
        "throughput_per_s": round(len(values) / wall, 2) if wall and values else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--email", required=True, help="account email; may contain {i} for a per-session pool")
    parser.add_argument("--password", required=True)
    parser.add_argument("--users", type=int, default=8, help="concurrent sessions (default 8)")
    parser.add_argument("--payments", type=int, default=400, help="total payments to create")
    parser.add_argument("--lookups", type=int, default=400, help="total detail lookups")
    parser.add_argument("--simulations", type=int, default=3, help="Fraud Lab runs of 1,000 transactions")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--json", help="write the report to this path")
    args = parser.parse_args()

    sessions = [login(args.base_url, args.email.format(i=i), args.password) for i in range(args.users)]
    stats = Stats()
    per_worker = max(1, args.payments // args.users)
    print(f"-> {args.users} sessions, {per_worker * args.users} payments, {args.lookups} lookups, {args.simulations} simulations against {args.base_url}")

    payment_wall = run_parallel([
        threading.Thread(target=payment_worker, args=(sessions[i], args.base_url, stats, per_worker, i, args.timeout), daemon=True)
        for i in range(args.users)
    ])
    lookup_wall = run_parallel([
        threading.Thread(target=lookup_worker, args=(sessions[i], args.base_url, stats, max(1, args.lookups // args.users), i, args.timeout), daemon=True)
        for i in range(args.users)
    ])
    sim_wall = None
    if args.simulations:
        sim_started = time.perf_counter()
        simulation_runs(sessions[0], args.base_url, stats, args.simulations, args.timeout)
        sim_wall = time.perf_counter() - sim_started

    rows = [
        report("payment_create (HTTP, incl. evaluation + sandbox capture)", stats.latencies["payment_create"], stats.errors["payment_create"], payment_wall),
        report("risk_evaluation (engine only, server-measured)", stats.evaluation_ms, 0, None),
        report("payment_lookup (HTTP)", stats.latencies["payment_lookup"], stats.errors["payment_lookup"], lookup_wall),
    ]
    if args.simulations:
        rows.append(report("simulation_1000 (HTTP)", stats.latencies["simulation_1000"], stats.errors["simulation_1000"], sim_wall))
        rows.append(report("simulation_1000 (engine + persistence, server-measured)", stats.latencies["simulation_engine_only"], 0, None))

    print(f"\n{'operation':<62}{'n':>6}{'err%':>7}{'avg':>9}{'p50':>9}{'p95':>9}{'p99':>9}{'rps':>8}")
    for row in rows:
        err = f"{(row['error_rate'] or 0) * 100:.1f}"
        rps = f"{row['throughput_per_s']:.1f}" if row["throughput_per_s"] else "-"
        digits = 3 if (row["p99_ms"] or 0) < 1 else 1
        cells = "".join(f"{(row[key] or 0):>9.{digits}f}" for key in ("avg_ms", "p50_ms", "p95_ms", "p99_ms"))
        print(f"{row['operation']:<62}{row['count']:>6}{err:>7}{cells}{rps:>8}")
    print(f"\ndecisions: {dict(stats.decisions)}")

    result = {
        "machine": {"platform": platform.platform(), "python": platform.python_version(), "processor": platform.processor()},
        "parameters": vars(args) | {"password": "[omitted]"},
        "decisions": dict(stats.decisions),
        "results": rows,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)
        print(f"report written to {args.json}")
    total_errors = sum(stats.errors.values())
    return 1 if total_errors else 0


if __name__ == "__main__":
    sys.exit(main())
