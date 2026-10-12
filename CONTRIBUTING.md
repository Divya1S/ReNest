# Contributing

ReNest is maintained by one developer. Issues and pull requests are welcome.

## Development setup

Follow "Run locally" in the [README](README.md). In short: start PostgreSQL with `docker compose up -d postgres` (or prefix backend commands with `USE_SQLITE=1`), install `backend/requirements-dev.txt` into a virtualenv at the repository root, and run `npm install` in `frontend/`.

## Quality gates

Every change must pass the gates CI runs (`.github/workflows/ci.yml`). Before opening a pull request:

```bash
# Backend
cd backend
../.venv/bin/python manage.py test
../.venv/bin/mypy .
../.venv/bin/python manage.py makemigrations --check --dry-run
../.venv/bin/pip-audit -r requirements.txt
../.venv/bin/python manage.py run_concierge_evals   # offline AI evals

# Frontend
cd frontend
npm test
npm run lint        # zero warnings allowed
npm run typecheck
npm run build
npm run e2e         # Playwright, mocked API
```

CI also runs the backend suite on PostgreSQL. Run it locally (see "Testing" in the README) before changing queries, constraints or anything that takes row locks.

## Ground rules

- **API contract.** List endpoints return DRF's paginated envelope (`{count, next, previous, results}`). Frontend code reads results through `asResults()` in `frontend/src/lib/api.ts` and never assumes a bare array.
- **AI features are optional.** Every AI code path must keep working, in a reduced form, when `GEMINI_API_KEY` is unset or the provider fails. The concierge's tools are read-only; `ConciergeStateIntegrityTests` enforces this.
- **Migrations.** Ship schema changes with their migration in the same pull request and keep them reversible where practical. When a new constraint could fail on existing rows, split the work: one data migration that repairs the rows, then a second migration that adds the constraint (see `listings/migrations/0041_prepare_integrity_constraints.py` and `0042_integrity_constraints.py`).
- **Tests accompany fixes.** A bug fix lands with a regression test that fails without it.

## Commit style

Conventional-commit prefixes keep the history scannable: `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`, `build:`, `ci:`.
