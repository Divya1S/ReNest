# Contributing

Thanks for your interest in ReNest. This project is maintained by a single
developer, but issues and pull requests are welcome.

## Development setup

Follow the "Run locally" section of the [README](README.md). The short
version: Postgres via `docker compose up -d postgres` (or `USE_SQLITE=1`),
`pip install -r backend/requirements.txt`, `npm install` in `frontend/`.

## Quality gates

Every change must pass the same gates CI runs. Before opening a PR:

```bash
# Backend
cd backend
../.venv/bin/python manage.py test
../.venv/bin/mypy .
../.venv/bin/python manage.py run_concierge_evals   # offline AI evals

# Frontend
cd frontend
npm test
npm run lint        # zero warnings allowed
npm run typecheck
npm run build
npm run e2e         # Playwright, mocked API
```

## Ground rules

- **API contract:** all list endpoints return DRF's paginated envelope
  (`{count, next, previous, results}`). Frontend consumers must normalize
  through `asResults()` in `frontend/src/lib/api.ts` — never index into a
  response assuming a bare array.
- **AI features are optional:** every AI code path must degrade gracefully
  when `GEMINI_API_KEY` is unset or the provider errors. The concierge's
  tools are read-only by design; tests enforce this
  (`ConciergeStateIntegrityTests`).
- **Migrations:** ship schema changes with their migration in the same PR,
  and keep them reversible where practical.
- **Tests accompany fixes:** a bug fix should land with a regression test
  that fails without it.

## Commit style

Conventional-commit prefixes keep history scannable: `feat:`, `fix:`,
`docs:`, `chore:`, `refactor:`, `test:`, `build:`, `ci:`.
