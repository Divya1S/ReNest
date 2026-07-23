# ReNest

[![CI](https://github.com/Divya1S/ReNest/actions/workflows/ci.yml/badge.svg)](https://github.com/Divya1S/ReNest/actions/workflows/ci.yml)

ReNest is a campus move-out rescue marketplace. Students photograph their room, tag rescue-worthy items with hotspots, and turn a chaotic move-out into structured listings, donations, and coordinated pickups — so usable dorm gear stays on campus instead of going to the dumpster.

## Features

- **Room Rescue Scan** — upload room photos, tag items with hotspots (or let AI vision detect them), and generate structured listing drafts in bulk
- **Clear-Out Board** — triage every draft into sell / donate / keep / toss with keyboard shortcuts and bulk actions
- **Public marketplace** — browse without an account; reserve, save, and post with one; natural-language search ("free lamp near north dorms") with semantic ranking
- **Nest Concierge** — an AI assistant with tool access to live marketplace data: plans multi-step answers, streams progress over SSE, renders listing cards and action chips, and degrades gracefully when the AI provider is down or out of quota
- **Handoff Hub** — stage-driven pickup coordination: approve → schedule → PIN-verified handoff, with in-app chat, calendar export, and post-handoff feedback
- **Move-Out Plan** — auto-generated operational tasks with due windows tied to your move-out deadline
- **Trust Center** — feedback-driven trust badges, listing reports, moderation workflow, and dispute resolution
- **Rescue Requests** — a needs board that matches student requests against live listings
- **Donation hubs** — public hub directory with capacity tracking, one-click donation routing, and a manager dispatch console
- **Campus scale** — multi-campus scoping with email-domain auto-match, public campus pages, analytics dashboards, and CSV/PDF reporting
- **Live impact stats** — real rescue counts, retail value saved, and landfill diversion
- **PWA** — installable, offline fallback, and web push notifications

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

The AI concierge routes each message semantically (chat / help / task), serves repeat questions from an embedding cache, and runs task queries through a plan–execute–reflect loop over read-only marketplace tools. Every AI path fails open: circuit breakers, bounded retries, quota-aware model fallbacks, and offline golden-set evals in CI.

> Naming note: the Django project module is `dormcycle` (the product's original working title). Renaming a Django settings module is high-churn and low-value, so the module name stays; the product is ReNest.

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | React 19, Vite, Tailwind CSS 4, framer-motion, TypeScript (strict on lib/pages) |
| Backend | Django 6, Django REST Framework, drf-spectacular (OpenAPI) |
| Data | PostgreSQL (SQLite fallback for zero-setup dev), Redis |
| Async | Celery workers + beat schedules (eager mode in dev — no broker needed) |
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
  dormcycle/      Django project (settings, celery, urls, ASGI/WSGI)
frontend/
  src/            React app (pages, components, hooks, lib)
  e2e/            Playwright specs (mocked API — no backend needed)
docs/             deployment runbook, concierge architecture, mobile shell, roadmap
scripts/          render build, load test, backup/restore
```

## Run locally

Prerequisites: Python 3.13, Node 20+, Docker (optional, for Postgres).

1. Start PostgreSQL (or skip and use `USE_SQLITE=1` below):

```bash
docker compose up -d postgres
```

2. Configure the backend environment:

```bash
cd backend
cp .env.example .env   # then fill in values as needed
```

3. Install backend dependencies and run Django:

```bash
python -m venv ../.venv
../.venv/bin/pip install -r requirements.txt
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

Open `http://localhost:5173`. API docs (Swagger UI): `http://127.0.0.1:8000/api/docs/` · Health: `/api/health/live`, `/api/health/ready`.

**Zero-setup variant:** skip Docker and run every backend command prefixed with `USE_SQLITE=1`.

**AI features:** set `GEMINI_API_KEY` in `backend/.env` (free key at [aistudio.google.com](https://aistudio.google.com)) and restart the server. Everything works without it — AI surfaces simply switch off.

### Demo accounts

Created by `manage.py seed_demo`:

- `maya@renest.local` / `demo-pass-123`
- `leo@renest.local` / `demo-pass-123`

Log in as both (one in a private window) to exercise the full reserve → approve → chat → PIN handoff → feedback loop.

## Testing

```bash
# Backend: 200+ tests, whole-project type-check, offline AI evals
cd backend
../.venv/bin/python manage.py test
../.venv/bin/mypy .
../.venv/bin/python manage.py run_concierge_evals

# Frontend: unit, lint, types, build, e2e
cd frontend
npm test
npm run lint
npm run typecheck
npm run build
npm run e2e        # Playwright, fully mocked API — no backend needed
```

The same gates run in CI on every push (`.github/workflows/ci.yml`): a backend job, a frontend job, and a Playwright e2e job.

## Deployment

One-click deploy via the Render blueprint (`render.yaml`): web service (uvicorn/ASGI serving API + built SPA), Celery worker, Celery beat, Redis, and PostgreSQL. Full runbook, alternative-host notes, backup strategy, and load-testing.


## Maintenance

```bash
cd backend
../.venv/bin/python manage.py run_maintenance   # expiry sweep + task sync (cron target)
../.venv/bin/python manage.py createsuperuser   # Django admin at /admin/
```

With Redis configured, Celery beat runs the full schedule (handoff reminders, saved-search alerts, trending cache, weekly digests) instead of the manual command.
