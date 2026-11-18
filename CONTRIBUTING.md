# Contributing

ReNest is maintained by one developer. Issues and pull requests are welcome.

## Development setup

Follow "Run it locally" in the [README](README.md). In short: install `backend/requirements-dev.txt` into a virtualenv at the repository root, run `npm install` in `frontend/`, and either prefix backend commands with `USE_SQLITE=1` or start PostgreSQL with `docker compose up -d postgres`.

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

### Backend tests on PostgreSQL

CI also runs the backend suite on PostgreSQL. Row locks, partial and check constraints, full-text search and strict column lengths only behave for real there, and a few concurrency tests are skipped on SQLite. Run it before changing queries, constraints or anything that takes row locks:

```bash
cd backend
USE_POSTGRES_FOR_TESTS=1 DATABASE_URL=postgres://postgres:postgres@127.0.0.1:5432/renest \
  ../.venv/bin/python manage.py test --noinput
```

### Coverage

```bash
cd backend
../.venv/bin/coverage run manage.py test
../.venv/bin/coverage report
```

## Ground rules

- **API contract.** List endpoints return DRF's paginated envelope (`{count, next, previous, results}`), 24 rows a page. Frontend code reads results through `asResults()` in `frontend/src/lib/api.ts` and never assumes a bare array. A page that shows a whole list asks for `?page_size=100`.
- **AI features are optional.** Every AI code path must keep working, in a reduced form, when no AI key (`OPENAI_API_KEY` or `GEMINI_API_KEY`) is set or the provider fails. The concierge's tools are read-only; `ConciergeStateIntegrityTests` enforces this.
- **What other users see.** Nest `PublicUserSerializer`, never `UserSerializer`, and take a person's name from `User.public_name`. Neither exposes any part of an email address.
- **State changes go through the service layer.** Reservations move between states only in `listings/services.py`. Serializers save only the columns that were edited (`save_validated_fields`), so an edit cannot overwrite a status another request just changed.
- **Migrations.** Ship schema changes with their migration in the same pull request and keep them reversible where practical. When a new constraint could fail on existing rows, split the work: one data migration that repairs the rows, then a second migration that adds the constraint (see `listings/migrations/0041_prepare_integrity_constraints.py` and `0042_integrity_constraints.py`).
- **Tests accompany fixes.** A bug fix lands with a regression test that fails without it. Uploads made by tests go to a temporary directory (`dormcycle/test_runner.py`), not to `backend/media`. Tests that call the rate-limited auth endpoints clear the cache in `setUp`.

## Commit style

Conventional-commit prefixes keep the history scannable: `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`, `build:`, `ci:`.
