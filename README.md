# ReNest

[![CI](https://github.com/Divya1S/ReNest/actions/workflows/ci.yml/badge.svg)](https://github.com/Divya1S/ReNest/actions/workflows/ci.yml)

ReNest is a campus move-out marketplace. Students photograph their room, tag the items worth saving, and publish them as listings, donations and coordinated pickups, so usable dorm gear stays on campus instead of going to the dumpster.

## Features

- **Room Rescue Scan**: upload room photos, tag items with hotspots (or let AI vision detect them), and generate listing drafts in bulk
- **Clear-Out Board**: triage every draft into sell, donate, keep or toss with keyboard shortcuts and bulk actions
- **Public marketplace**: browse without an account; reserve, save and post with one. Natural-language search turns a sentence such as "free lamp near north dorms" into filters, and PostgreSQL full-text search (index-backed) ranks the results
- **Nest Concierge**: an AI assistant with read-only tools over live marketplace data. It plans multi-step answers, streams progress over SSE, renders listing cards and action chips, and keeps answering in a reduced form when the AI provider is down or out of quota
- **Handoff Hub**: pickup coordination in stages (approve, schedule, PIN-verified handoff) with in-app chat, calendar export and post-handoff feedback
- **Move-Out Plan**: generated task checklist with due windows tied to the move-out deadline
- **Trust Center**: feedback-based trust badges, listing reports, a moderation workflow and dispute resolution
- **Rescue Requests**: a needs board that matches student requests against live listings
- **Donation hubs**: public hub directory with capacity tracking, one-click donation routing and a manager dispatch console
- **Campus scoping**: multi-campus isolation with email-domain matching, public campus pages, analytics dashboards and CSV/PDF reports
- **Impact stats**: rescue counts, retail value saved and landfill diversion, computed from completed handoffs
- **PWA**: installable, with an offline fallback page and web push notifications
- **Risk Intelligence**: a sandbox payment pipeline with a rule-based, explainable risk engine, an idempotent payment API, an append-only event log with retries and dead-lettering, a Fraud Lab that measures detection against friction on labelled synthetic attacks, a query-grounded investigator and an agent spending-policy sandbox. See [ReNest Risk Intelligence](#renest-risk-intelligence).

## Architecture

```mermaid
flowchart LR
    subgraph Client
        SPA["React 19 SPA<br/>(Vite, Tailwind 4, PWA)"]
    end
    subgraph Server
        API["Django REST Framework<br/>API + SSE streaming"]
        Concierge["AI concierge<br/>router → cache → agent loop"]
        Workers["Celery workers + beat<br/>(email, AI tasks, schedules)"]
    end
    subgraph Data
        PG[("PostgreSQL")]
        Redis[("Redis<br/>cache · sessions · broker")]
        S3[("S3 / R2 media<br/>(optional)")]
    end
    Gemini["Google Gemini API"]

    SPA -->|"REST + SSE"| API
    API --> Concierge
    Concierge -->|"fallbacks + circuit breaker"| Gemini
    API --> PG
    API --> Redis
    Workers --> PG
    Workers --> Redis
    Workers --> Gemini
    API --> S3
```

The concierge routes each message, without a model call, to a chat path or a task path. Repeated general questions are served from a similarity cache; task queries run through a plan, execute, reflect loop over read-only marketplace tools that see exactly the listings the browse page would show that user. Every AI path degrades instead of failing: a circuit breaker, bounded retries inside a hard time budget, a quota-aware model fallback, help-guide answers when the provider is down, and offline golden-set evals in CI.

> The Django project package is named `dormcycle`, the product's original working title. The product is ReNest; the package name was kept because renaming a settings module touches every deployment file for no functional gain.

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | React 19, Vite, Tailwind CSS 4, framer-motion, TypeScript (strict) for the data layer and the newer pages, JavaScript elsewhere |
| Backend | Django 6, Django REST Framework, drf-spectacular (OpenAPI) |
| Data | PostgreSQL (SQLite fallback for zero-setup dev), Redis |
| Async | Celery workers and beat schedules; eager mode in development, so no broker is needed |
| AI | Google Gemini (chat, vision, embeddings) via `google-genai` |
| Testing | Django test runner, mypy, Vitest, ESLint, Playwright e2e |
| Deploy | Render blueprint (`render.yaml`), Docker Compose for local Postgres, WhiteNoise + uvicorn serving API and SPA same-origin |

## Project structure

```
backend/
  accounts/       auth, campuses, profiles, referrals
  listings/       marketplace core: listings, reservations, handoffs, scans, logistics
  hubs/           donation hub directory + manager tools
  concierge/      AI assistant: router, semantic cache, orchestrator, evals
  risk/           Risk Intelligence: engine, payments, events, Fraud Lab, investigator, agents
  dormcycle/      Django project (settings, celery, urls, ASGI/WSGI)
frontend/
  src/            React app (pages, components, hooks, lib)
  e2e/            Playwright specs (mocked API, no backend needed)
scripts/          render build, load tests (marketplace and risk), backup/restore
```

## Run locally

Prerequisites: Python 3.13, Node 22 (Vite accepts 20.19+ or 22.12+; CI uses 22), Docker (optional, for PostgreSQL).

1. Start PostgreSQL (or skip and use `USE_SQLITE=1` below):

```bash
docker compose up -d postgres
```

2. Configure the backend environment:

```bash
cd backend
cp .env.example .env   # fill in values as needed; the placeholder secret key is refused once DJANGO_DEBUG=0
```

3. Install backend dependencies and run Django (`requirements-dev.txt` adds mypy and pip-audit on top of `requirements.txt`):

```bash
python -m venv ../.venv
../.venv/bin/pip install -r requirements-dev.txt
../.venv/bin/python manage.py migrate
../.venv/bin/python manage.py seed_demo
../.venv/bin/python manage.py runserver 127.0.0.1:8000
```

4. Start the frontend:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. Signing in needs a CSRF token like every other write: the app fetches `GET /api/auth/csrf` first, and so must any script that logs in. API docs (Swagger UI) are at `http://127.0.0.1:8000/api/docs/`; health checks at `/api/health/live` and `/api/health/ready`.

**Zero-setup variant:** skip Docker and run every backend command prefixed with `USE_SQLITE=1`.

**AI features:** set `GEMINI_API_KEY` in `backend/.env` (free key at [aistudio.google.com](https://aistudio.google.com)) and restart the server. Everything else works without it; the AI features switch off.

### Demo accounts

Created by `manage.py seed_demo`:

- `maya@renest.local` / `demo-pass-123`
- `leo@renest.local` / `demo-pass-123`

Log in as both (one in a private window) to exercise the full reserve → approve → chat → PIN handoff → feedback loop. A handoff completes only when the owner enters the claimant's PIN.

### Configuration

All settings come from environment variables; in development they are read from `backend/.env`, and `backend/.env.example` documents every one with a placeholder. With `DJANGO_DEBUG=0` the server refuses to start until these are set:

| Variable | Purpose |
|---|---|
| `DJANGO_SECRET_KEY` | Long random string; the shipped placeholders are rejected |
| `DJANGO_ALLOWED_HOSTS` | Public hostname(s) of the deployment |
| `DJANGO_CSRF_TRUSTED_ORIGINS`, `CORS_ALLOWED_ORIGINS` | Public origin(s); localhost values are rejected |

The database comes from `DATABASE_URL` or the `POSTGRES_*` variables (`USE_SQLITE=1` is for development only), and `APP_BASE_URL`, used in emails and deep links, defaults to the first trusted origin. Everything else is optional and switches on one feature at a time (`GEMINI_API_KEY`, `EMAIL_*`, `AWS_S3_*`, `VAPID_*`, `SENTRY_DSN`, `REDIS_URL`, `MAINTENANCE_TOKEN`); the deployment section lists what each one enables.

## Testing

```bash
# Backend: 900+ tests, whole-project type-check, offline AI evals
cd backend
../.venv/bin/python manage.py test
../.venv/bin/mypy .
../.venv/bin/python manage.py run_concierge_evals
../.venv/bin/pip-audit -r requirements.txt   # dependency vulnerability audit

# Frontend: unit, lint, types, build, e2e
cd frontend
npm test
npm run lint
npm run typecheck
npm run build
npm run e2e        # Playwright, mocked API, no backend needed
```

The same gates run in CI on pushes to `main` and on every pull request (`.github/workflows/ci.yml`): a backend job on SQLite, the same backend suite on PostgreSQL, a frontend job, and a Playwright e2e job.

The PostgreSQL run matters because row locks, partial and check constraints, full-text search and strict column lengths only behave for real there, and a few concurrency tests (two threads, two connections) are skipped on SQLite. To run it locally against any PostgreSQL:

```bash
cd backend
USE_POSTGRES_FOR_TESTS=1 DATABASE_URL=postgres://postgres:postgres@127.0.0.1:5432/renest \
  ../.venv/bin/python manage.py test --noinput
```

## Deployment

One-click deploy via the Render blueprint (`render.yaml`): Render Dashboard -> New -> Blueprint -> pick this repo. It provisions one free web service (uvicorn/ASGI serving the API and the built SPA same-origin) and a free PostgreSQL database.

### After the first deploy

1. Fill in the `sync: false` env vars in the Render dashboard. Everything is optional; each unset value only switches its own feature off:
   - `GEMINI_API_KEY` (free key at [aistudio.google.com](https://aistudio.google.com)) turns on the concierge, room-scan detection and AI copywriting.
   - `EMAIL_HOST` / `EMAIL_PORT` / `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` / `DEFAULT_FROM_EMAIL` enable password resets and handoff reminders. Any SMTP provider with a free tier works.
   - `AWS_S3_BUCKET_NAME` (+ `AWS_S3_ENDPOINT_URL` for Cloudflare R2) moves uploads off the container's ephemeral disk. For direct browser uploads the bucket also needs a CORS rule that allows `PUT` from the app's origin.
   - `PLATFORM_ADMIN_EMAIL` receives campus onboarding requests (defaults to `DEFAULT_FROM_EMAIL`).
   - `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` enable web push (`npx web-push generate-vapid-keys`).
   - `SENTRY_DSN` / `VITE_SENTRY_DSN` enable error reporting.
2. Set up the scheduled jobs (see below). Without them nothing expires, no reminders go out, and saved-search alerts never fire.
3. Create an admin user: Render Shell -> `cd backend && python manage.py createsuperuser`.

### Free-tier constraints

| Constraint | Effect | Mitigation |
|---|---|---|
| Free web service sleeps after 15 min idle | First request after idle takes ~1 min | The maintenance cron pings it every 30 min |
| Free plan has no background workers | No Celery worker or beat; tasks run inline in the request | Scheduled jobs run via GitHub Actions cron (below) |
| Render's free Postgres expires 30 days after creation | Database becomes unreachable | Use a free-forever provider (Neon, Supabase): set `DATABASE_URL` on the web service and delete the `databases:` block from `render.yaml` |
| Ephemeral disk | Uploaded photos vanish on redeploy | Set `AWS_S3_BUCKET_NAME` (Cloudflare R2 has a free tier) |

To run real background workers later, uncomment the "Paid tier" block at the bottom of `render.yaml` and set `REDIS_URL`.

## Maintenance

The scheduled jobs are grouped into three sets in `listings/ops.py`: `tick` (listing expiry, handoff reminders, stale-handoff sweep, saved-search alerts, trending cache, held push notifications, risk event dispatch, idempotency-key purge, payment reconciliation), `daily` (demand forecast, expired-session cleanup) and `weekly` (completion rates, campus digest). Each job is idempotent and isolated, so an overlap or one failure is harmless. They run in one of three ways:

**1. GitHub Actions cron (default, free).** `.github/workflows/maintenance.yml` POSTs to a token-protected endpoint on a schedule. Configure once, under Settings -> Secrets and variables -> Actions:

- variable `MAINTENANCE_URL` = your deployment's base URL, e.g. `https://renest-web.onrender.com`
- secret `MAINTENANCE_TOKEN` = the same value as the `MAINTENANCE_TOKEN` env var on the server (the blueprint generates one)

Until both are set the workflow exits early without failing. Note that GitHub disables scheduled workflows on a public repository after 60 days with no commits.

**2. A cron entry or the Render shell**, running the same jobs in-process:

```bash
cd backend
python manage.py run_maintenance                 # every 15-30 min
python manage.py run_maintenance --jobs daily    # once a day
python manage.py run_maintenance --jobs weekly   # once a week
python manage.py createsuperuser                 # Django admin at /admin/
```

**3. Celery beat**, when `REDIS_URL` and a worker are configured. `CELERY_BEAT_SCHEDULE` in `dormcycle/settings.py` has exactly three entries, one per job set, and each calls the same registry the cron uses, so the two cannot drift apart. Use this instead of the cron, not alongside it.

### Backups

`scripts/backup.sh` validates a dump before reporting success, and `scripts/restore.sh` restores in one transaction, so a failed restore leaves the database unchanged. `.github/workflows/backup.yml` runs a daily `pg_dump` and stores it as a workflow artifact with 7-day retention. Add a `DATABASE_URL` secret (or the discrete `POSTGRES_*` secrets) to enable it; it skips cleanly when unconfigured and fails loudly rather than uploading an empty dump.

Artifacts on a **public** repository are downloadable by anyone. Keep backups of real user data off a public repo: point the workflow at a private repository, or run `scripts/backup.sh` on a host you control.

## ReNest Risk Intelligence

A payments risk subsystem built into the marketplace: sandbox payments, an explainable decision engine, a Fraud Lab, an investigator, and an agent spending sandbox. No real money moves anywhere in it. The route is `/risk` in the app and `/api/risk/` on the API.

### Problem

A campus marketplace handles small payments from young accounts on shared Wi-Fi, the kind of traffic that card testers, account-takeover rings and multi-account promo abusers hide inside. The subsystem has to decide in milliseconds with an explanation a human can read, never charge twice when a client retries, never lose a downstream side effect, keep an audit trail that cannot be edited, and measure the trade-off between catching fraud and inconveniencing honest students instead of guessing at it.

### Product

| Page | What it shows |
|---|---|
| Risk Overview (`/risk`) | Counts, block and review rates, amounts declined or held, LOW/MEDIUM/HIGH distribution, decision series, evaluation latency P50/P95/P99, the sensitivity slider, a live feed, and a form to create sandbox payments through the real pipeline |
| Transaction Explorer (`/risk/transactions`) | Search and filters (decision, status, band, simulated rows) over every payment |
| Transaction Investigation (`/risk/transactions/:id`) | Score against the thresholds, signed risk factors, the engine's explanation text, the frozen signal snapshot, the event timeline and the audit trail; staff can resolve a held payment |
| Fraud Lab (`/risk/lab`) | Eight labelled scenarios scored by the live engine, with measured precision, recall, false positive rate, protected and held amounts, latency, a detection-versus-friction sweep and a re-threshold slider |
| Risk Investigator (`/risk/investigator`) | Plain-language questions answered from a fixed query set, with facts, inferences and evidence separated |
| Agent Sandbox (`/risk/agents`) | Spending policies for autonomous agents and an explainable verdict for every attempt |
| System Health (`/risk/health`) | Request and error counters, decision counters, latency percentiles, event queue depth, dead letters with replay, idempotency key counts |

Every number on these pages is counted from stored rows or from the metrics reservoir. Synthetic rows are flagged `is_simulation` and are excluded from real profiles and dashboards unless you ask for them, in which case they are labelled.

### Architecture

```mermaid
flowchart TB
    Client["SPA / API client<br/>Idempotency-Key header"] -->|"POST /api/risk/payments"| View["PaymentListCreateView"]
    View --> Idem["idempotency.acquire<br/>(user, scope, key) unique row"]
    Idem -->|"replay / 409 / 422"| Client
    Idem --> Svc["services.payments.create_payment"]
    subgraph TX["one database transaction"]
        Svc --> Txn["PaymentTransaction (pending)<br/>idempotency key bound to it"]
        Txn --> Ctx["engine.signals.build_context<br/>aggregates over the customer's history"]
        Ctx --> Engine["engine.evaluate_transaction<br/>pure, additive rules"]
        Engine --> Eval["RiskEvaluation + RiskFactor rows"]
        Eval --> Ev["PaymentEvent rows:<br/>created, risk_evaluated,<br/>approved | review_required | blocked"]
        Ev --> Audit["AuditLog (append-only)"]
    end
    TX -->|"on_commit"| Dispatch["dispatch of this request's events<br/>handlers with retries, DLQ"]
    Dispatch --> Handlers["analytics counters<br/>audit rows<br/>customer notification"]
    TX -->|"ALLOW only, after commit"| PSP["SandboxProcessor.charge<br/>bounded retries, idempotent reference"]
    PSP --> Done["PaymentTransaction completed | failed<br/>+ payment.completed | payment.failed"]
    Tick["maintenance tick / process_risk_events"] --> Dispatch
    Tick --> Purge["idempotency.purge_expired"]
```

The whole thing runs on the existing stack (Django, DRF, the configured cache, the maintenance tick). There is no broker: the `PaymentEvent` table is the log, and the same code path dispatches inline after commit, from the scheduled tick, or from a dedicated worker command.

### Risk engine

`risk.engine.evaluate_transaction(context, sensitivity=50)` is a pure function of a frozen `TransactionContext` and returns:

```python
RiskDecision(
    decision="block",              # allow | review | block
    risk_score=74,                 # 0..100, additive, clamped
    confidence=0.83,               # heuristic 0.50..0.99, documented as uncalibrated
    reasons=["amount_vs_history", "new_device", "ip_churn", "unusual_hour"],
    factors=[RuleResult(code, points, label, detail), ...],
    thresholds=Thresholds(sensitivity=50, review=40, block=70),
    model_version="risk-rules-v2",
    evaluation_time_ms=0.031,      # measured with perf_counter around the call
    explanation="RISK SCORE: 74 / 100\n+34 ...",
)
```

Rules live in `risk/engine/rules.py`, one class each, registered in a tuple; adding a signal means adding one class. Points are signed so trust reduces the score. The current model, hand-tuned and documented as a demonstration model, not a trained one:

| Signal | Points | Fires when |
|---|---|---|
| Amount versus history | +12 / +22 / +34 | 2x / 3x / 6x the customer's average, with at least three successful payments |
| Large first transaction | +10 / +18 | No successful payment yet and amount at least 200 / 1,000 |
| Extreme amount | +12 / +35 | Amount at least 1,000 / 2,000 |
| New device | +21 | Established account on a device it never used |
| Known device | -12 | Device used three or more times before and first seen at least a day ago |
| Device shared by accounts | +24 | Three or more accounts on one device in 24h |
| Device churn | +16 | Three or more devices in 24h |
| Network churn | +15 | Four or more network prefixes in 24h |
| Hourly velocity | +18 / +30 | 3 / 5 or more payments in the last hour |
| Daily velocity | +10 / +20 | 8 / 15 or more payments in 24h |
| Card testing | +28 | Amount under 5 and four or more payments in the hour |
| Account age | +18 / +11 / +5 / -6 | Under 1 day / 7 days / 30 days; 180 days or more |
| Failed attempts | +7 / +20 | 1 / 3 or more failed or blocked payments in 24h |
| Trusted customer | -8 / -15 | 3 / 10 or more successful payments and no recent failure |
| Currency mismatch | +8 | First payment in a currency the account never used |
| Unusual hour | +4 | 01:00 to 05:00 in the campus time zone |
| New merchant, large amount | +6 | First time at a merchant and 2x the average |

Amount thresholds and averages compare an approximate US dollar value, taken from a small static reference table in `risk/engine/currency.py`, so 2,000 yen is not treated like 2,000 dollars. The table exists to compare sizes and is never used to move money; the snapshot stored with each evaluation records the value and rate that were compared.

Thresholds come from one 0..100 sensitivity: `review = 60 - 0.4 * s`, `block = 90 - 0.4 * s`. Both lines have the same slope, so the review band is always 30 points wide and raising sensitivity never collapses REVIEW into BLOCK. Because scores do not depend on sensitivity, a sweep over stored scores is a pure re-threshold (`engine.rescore`), which is what makes the Fraud Lab slider instant.

Signals for real payments come from `risk/engine/signals.py`: a fixed number of aggregate queries over the customer's non-simulated history (no per-row loops), so evaluation cost does not grow with history. Only a coarse network prefix (first two octets) is ever stored, never a full IP address.

### Explainability

The explanation is generated from the same `RuleResult` list that produced the score, so it cannot disagree with the number (a test asserts this). Every factor carries a stable code, its points, a label and the figures behind it; the transaction page lists them strongest first and shows the frozen context snapshot the engine saw, so any decision can be replayed later. The "confidence" value is a heuristic built from evidence coverage and distance to the nearest threshold; it is labelled as such in the API and the UI and is not a probability.

### Fraud Lab

`risk/services/simulation.py` generates labelled synthetic transactions for eight scenarios: normal customer traffic (no fraud; measures friction), card testing, account takeover, new device attack, high velocity, suspicious amount, coordinated fraud and multiple-account abuse. Each row is pushed through the same `evaluate_transaction` that live payments use, and every metric is counted from what the engine actually decided:

- analyzed, allowed, reviewed, blocked; labelled fraud and legitimate counts
- `precision_flagged = fraud (review or block) / all (review or block)`, `recall_flagged = fraud (review or block) / all fraud`, and the same pair for blocks only
- `false_positive_rate = legitimate (review or block) / all legitimate`
- amount of labelled fraud blocked outright, held for review, and legitimate spend slowed down
- LOW/MEDIUM/HIGH bands, the most frequent factors in fraud versus legitimate rows, measured engine latency (avg, P50, P95, P99) and the whole-run duration
- a sweep of detection rate against friction rate at every sensitivity from 0 to 100

Precision and recall are only reported because synthetic labels exist; the API never reports them for real traffic. Legitimate rows deliberately include ordinary quirks (a new phone, a big textbook order, a late-night purchase, one failed card) so false positives are possible and the friction curve means something. Runs are seeded and reproducible; each account keeps its last six runs and their rows.

### Idempotency

`POST /api/risk/payments` accepts an `Idempotency-Key` header (up to 255 characters). Keys are scoped to `(user, endpoint)`. A valid request is fingerprinted from its validated fields, with the amount quantised to the currency's decimals, so `42.50` and `42.5` are the same request; an invalid one is fingerprinted from the raw body so its 400 still replays.

The unique constraint on `(user, scope, key)` decides who owns a key, and every acquisition carries an owner token. The key is bound to the payment inside the payment's own database transaction, so the binding, the payment, its evaluation, its events and its audit row commit together or not at all. Nothing that fails after that commit can release the key. Documented behaviour, covered by `risk/tests/test_idempotency.py` and `risk/tests/test_hardening.py` (which includes two-thread tests that run on PostgreSQL):

| Situation | Response |
|---|---|
| Same key, same request, original finished | The stored response replayed with `Idempotent-Replayed: true`; no second payment |
| Same key after a failure anywhere past the commit (event dispatch, the processor call, writing the response) | The key is already bound, so the retry is answered from the payment's current state with `Idempotent-Replayed: true`; still one payment |
| Same key, different request | `422 idempotency_key_reused`; nothing is created |
| Same key while the original has not committed yet | `409 idempotency_in_progress` with `Retry-After: 1` |
| The original died before committing | The unbound key can be taken over after a 60 second lease. A late original can no longer bind (its owner token is stale), so its payment rolls back |
| Key older than 24 hours | Treated as new; a fresh payment is created (the window is part of the contract) |
| Validation failure | The 400 is stored and replayed too, so a retry of a bad request gets the same answer |
| Unexpected server error before anything committed | The key is released so the client's retry gets a real attempt instead of a cached failure |

The processor call is idempotent on the transaction's public id as well, so a crash between "charged" and "recorded" is safe to retry.

### Event architecture

Events: `payment.created`, `payment.risk_evaluated`, `payment.approved`, `payment.review_required`, `payment.blocked`, `payment.completed`, `payment.failed`, `payment.reviewed`, `risk.policy_updated`, `agent.decision`. Each row carries `event_id` (ULID-style), `sequence`, `event_type`, `schema_version`, `entity_type`, `entity_id`, `payload`, `request_id`, `occurred_at`, and its processing state.

Assumptions:

- Emission happens inside the caller's database transaction, so an event exists if and only if the state change committed. Dispatch is scheduled with `transaction.on_commit` in a hook that cannot raise into the request: a dispatch failure is counted and left for the next pass.
- Delivery is at least once. A crash between "handler ran" and "processed_handlers saved" replays the handler, so every handler is idempotent (keyed on `event_id`).
- Ordering is guaranteed per entity by `sequence`, not globally.
- Several dispatchers may run at once (the request's post-commit hook, the maintenance tick, a worker). A dispatcher claims rows with one conditional UPDATE that stamps its token and a 60-second lease, and processes only the rows that UPDATE touched, so two dispatchers never own the same event on any database. A dispatcher that dies leaves rows whose lease expires, after which they are claimable again. The lease is renewed before handlers run, and the write that records the outcome is conditional on still holding the token, so a slow dispatcher cannot overwrite the result of the one that took over.
- A request dispatches only the events it emitted; the backlog (retries, dead-letter candidates) belongs to the tick and the worker, so a broken handler cannot slow every checkout.

### Reliability

- The sandbox processor is charged with bounded retries and exponential backoff with jitter (`risk/services/retry.py`); transient errors retry, permanent declines do not. `sandbox_behavior` on the request (`succeed`, `fail_transient_once`, `fail_transient`, `decline`) plays the role of test-card numbers so every path is demonstrable.
- Event handlers get three inline retries; the event itself gets five dispatch attempts with backoff of 5, 30, 120 and 600 seconds, then moves to `dead`. Dead letters are visible on System Health and can be replayed by staff. Nothing is dropped silently.
- The processor is called after the database transaction commits, never inside it. The idempotency key was bound in that same transaction, so a crash at any later point makes the client's retry replay the same payment instead of creating another.
- Payments that were approved but never reached a terminal state (a process died mid-capture) are re-driven by a reconciler after five minutes; the processor reference is the transaction id, so a repeat charge is a no-op.
- `manage.py process_risk_events --loop 5` runs a dedicated dispatcher; the maintenance tick does the same work plus idempotency-key purging and reconciliation.

### Security

- Session authentication with CSRF on every write, including login itself; every endpoint requires a signed-in user. Non-staff see only their own payments, simulations, investigations, agents, events and audit rows (`ScopedQuerySet.for_user`); staff-only actions are policy changes, manual review, event replay and the Prometheus endpoint.
- Strict input validation: positive amounts with a hard ceiling, supported currencies with zero-decimal handling for JPY, bounded metadata (20 keys, 2 KB), enumerated payment methods and sandbox behaviours, capped query parameters and page sizes, JSON-only bodies.
- Metadata is redacted at the API boundary and again before audit rows are written: keys such as `password`, `token`, `card_number`, `pan`, `cvv` never reach the database or the logs. Only a coarse network prefix is stored, and a forwarded-for header is trusted only when the deployment declares a proxy in front of it.
- Audit rows are append-only by construction: the model refuses updates and deletes, and the queryset refuses bulk deletes.
- Customers see that a manual review happened and its outcome; the reviewer's identity and notes are visible to staff only. One redaction policy (`risk/services/privacy.py`) is applied in the serializers, so the payment detail, the audit list and the event list cannot disagree. Fraud Lab rows can never be reviewed or captured.
- Per-user write throttles on payments, simulations, investigations and agent attempts; the agent count per account is capped.
- The investigator never sends free-form user text to a model as an instruction; the optional narrator receives only the structured facts, and an answer that mentions a number (in digits or spelled out) or a transaction id absent from those facts is discarded.

### AI investigator

`risk/services/investigator.py` is a controlled pipeline: a deterministic classifier maps the question to one of seven intents and a time window; each intent has a fixed plan of allowed queries (decision counts with a previous-period comparison, top factors behind blocks, new-device transactions, the review queue, velocity and device-sharing patterns, highest-risk transactions); the structured results are turned into an answer whose sentences are split into facts (each one maps to a number in the results) and inferences (labelled possibilities), with the transaction ids that back them as evidence. The queries that ran are stored with every investigation.

Explanation providers sit behind a small Protocol. `DeterministicExplainer` is the default and the mode used in tests. `GeminiExplainer` reuses the marketplace's existing Gemini client (free tier) when `RISK_INVESTIGATOR_MODE=auto` or `llm` and a key is configured; it may only narrate the facts it is given, falls back to the deterministic answer on any provider failure, and is rejected if it invents a number. Without a key nothing costs money and everything still answers.

### Agent payment sandbox

An `AgentPolicy` has `dailyLimit`, `transactionLimit`, `requiresApprovalAbove`, `allowedCategories`, `blockedMerchants` and a currency. Every attempt runs the checks in order and records a structured reason for each: policy active, positive amount in the policy currency, merchant not blocked, category allowed, per-transaction limit, daily limit against today's counted spend, the risk engine (the owner is the account, the agent is the device), and finally the approval threshold, which downgrades to REVIEW. Approved and held attempts count toward the daily budget; blocked ones never do. A held attempt is resolved by the agent's owner or by staff through `agents/<id>/transactions/<attempt_id>/resolve`: approving keeps the spend counted, rejecting frees it. The budget day follows the owner's campus time zone. Every attempt and every resolution is audited and emits `agent.decision`.

### Performance (measured)

Produced by `scripts/risk_loadtest.py` against the development server on a laptop, so treat these as the shape of the system rather than a benchmark: `runserver` is one Python process, and event handlers run inline after commit in this configuration. Rerun the script for your own numbers.

Machine: Apple Silicon laptop (macOS 15.7, Python 3.13), `manage.py runserver` against a local PostgreSQL 16, 2026-10-05, after the hardening pass (fenced idempotency keys, re-validated transitions, reviewer redaction). Payments were spread over a pool of eight established sandbox customers (`manage.py risk_loadtest_users`, each with a month-old known device), so the mix was 479 allowed and 1 held for review; all allowed payments went through the sandbox processor capture.

| Operation | Sessions | n | Error rate | Avg | P50 | P95 | P99 | Throughput |
|---|---|---|---|---|---|---|---|---|
| Payment creation over HTTP (idempotency, evaluation, events, audit, sandbox capture) | 8 concurrent | 480 | 0.0% | 118.9 ms | 117.5 ms | 131.2 ms | 164.2 ms | 67.0 req/s |
| Payment creation over HTTP | 2 concurrent | 200 | 0.0% | 29.7 ms | 29.2 ms | 31.9 ms | 34.5 ms | 67.2 req/s |
| Risk evaluation, engine only (server-measured per call) | 8 concurrent | 480 | 0.0% | 0.020 ms | 0.020 ms | 0.030 ms | 0.030 ms | n/a |
| Transaction lookup over HTTP (detail with factors, events, audit) | 8 concurrent | 480 | 0.0% | 29.4 ms | 27.9 ms | 39.2 ms | 71.6 ms | 270.1 req/s |
| Transaction lookup over HTTP | 2 concurrent | 200 | 0.0% | 7.3 ms | 6.8 ms | 9.4 ms | 11.0 ms | 273.0 req/s |
| Fraud Lab run, 1,000 transactions over HTTP (score, persist, sweep) | 1 | 3 | 0.0% | 286.0 ms | 275.5 ms | 307.3 ms | 310.1 ms | 3.5 runs/s |
| Fraud Lab run, 1,000 transactions, server-measured | 1 | 3 | 0.0% | 282.2 ms | 271.8 ms | 303.4 ms | 306.2 ms | n/a |

Throughput is roughly the same whether two or eight sessions are writing, so the limit here is the single server process, not the database: the extra latency at eight sessions is queueing in front of it. The same run on SQLite (2026-09-09, before the hardening pass) reached 51 req/s with a P99 of 1.3 s at eight sessions because SQLite serialises writers; PostgreSQL removes that tail. The script accepts `--base-url` so the same measurement can be taken against a deployed instance.

The engine itself evaluates a transaction in well under a millisecond (measured per call with `perf_counter`, stored on every evaluation and summarised on System Health). Most of the request time is database writes: a payment creates one transaction row, one evaluation, its factors, three or four events, an audit row and the idempotency record, then the processor call and the completion event.

### Tradeoffs

- **Rules, not a model.** An additive rule set is legible and testable and the point values are documented; it is not calibrated on real fraud. The engine keys explanations on factor codes so a trained model could replace `rules.py` behind the same `RuleResult` shape.
- **Table-as-log instead of a broker.** Right for one service on a free tier and it keeps events transactional with the state change. It does not fan out across services and dispatch adds latency to the request when no scheduler is running.
- **Inline dispatch.** A request runs the handlers for its own events after commit, which makes demos self-contained but adds their cost to the request; a deployment with `process_risk_events` running could leave all dispatch to the worker.
- **Cache-backed counters.** Request, error and decision counters and the latency reservoirs live in the configured cache: shared and durable with Redis, per-process and reset on restart with the in-memory cache. Database figures on the same page are exact.
- **Simulations write real rows.** Each 1,000-row run persists transactions, evaluations and factors so they can be explored; retention is capped per account.
- **Synthetic labels only.** Precision and recall are reported for Fraud Lab data and nowhere else. Real traffic has no ground truth here and the UI says so.

### Local setup and demo script

```bash
cd backend
USE_SQLITE=1 ../.venv/bin/python manage.py migrate
USE_SQLITE=1 ../.venv/bin/python manage.py seed_demo            # marketplace demo accounts
USE_SQLITE=1 ../.venv/bin/python manage.py seed_risk_demo --email maya@renest.local
USE_SQLITE=1 ../.venv/bin/python manage.py runserver 127.0.0.1:8000
# optional: a dedicated event dispatcher
USE_SQLITE=1 ../.venv/bin/python manage.py process_risk_events --loop 5
```

Sign in as `maya@renest.local` (or promote a user to staff with `manage.py createsuperuser` to change the live policy and resolve held payments), then:

1. Open `/risk`. The seed created ordinary payments spread over the previous days, plus exactly one large purchase, one new-device purchase, one card-testing burst and one processor decline, so the cards and the feed are populated the same way every time.
2. Submit a sandbox payment from the form with `Processor behaviour: Transient failure, then succeed`; open it from the feed to see the retry recorded on the event timeline.
3. Open `/risk/lab`, run Account takeover at 1,000 transactions, then drag the re-threshold slider and watch blocked, reviewed, recall and false positives move.
4. Open `/risk/investigator` and ask "Why did blocked transactions increase today?".
5. Open `/risk/agents`, pick the seeded agent and attempt a 75.00 purchase in `electronics`; read the reasons.
6. Open `/risk/health` for counters, percentiles and the event queue. `GET /api/risk/metrics/prometheus` (staff) exposes the same numbers in Prometheus text format.

Replay a request with the same `Idempotency-Key` to see `Idempotent-Replayed: true`:

```bash
curl -s -X POST http://127.0.0.1:8000/api/risk/payments \
  -H 'Content-Type: application/json' -H 'X-CSRFToken: <token>' -H 'Idempotency-Key: order-42' \
  -b '<session cookie>' -d '{"merchant_id":"mkt_textbooks","amount":"42.50","currency":"USD","payment_method":"card","device_id":"laptop"}' -i
```

API reference (all under `/api/risk/`, all authenticated): `payments` (GET list, POST create), `payments/<id>`, `payments/<id>/review` (staff), `overview`, `feed`, `distribution`, `policy` (GET; PUT staff), `rules`, `health`, `metrics`, `metrics/prometheus` (staff), `scenarios`, `simulations` (GET, POST), `simulations/<id>`, `simulations/<id>/rescore?sensitivity=`, `investigator`, `investigations` (GET, POST), `investigations/<id>`, `agents` (GET, POST), `agents/<id>` (GET, PATCH), `agents/<id>/transactions` (GET, POST), `agents/<id>/transactions/<attempt_id>/resolve` (POST), `events`, `events/dispatch` (staff), `events/<id>/replay` (staff), `audit`. The OpenAPI schema at `/api/docs/` includes them.

Tests: `manage.py test risk` runs the engine, idempotency, event, payment API, simulation, investigator, agent, reliability, security and hardening suites, including concurrency tests that only run on PostgreSQL; the frontend pages have Vitest tests with axe checks and a Playwright spec (`e2e/risk-intelligence.spec.js`).
