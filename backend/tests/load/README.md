# Load Tests

k6 scripts for the three critical paths.  Run these against a staging environment
before every major infrastructure change.

## Prerequisites

```bash
brew install k6          # macOS
# or: https://k6.io/docs/getting-started/installation/
```

## Scripts

| Script | Target | Scenario |
|--------|--------|----------|
| `browse_listings.js` | p95 < 200ms @ 100 RPS | Unauthenticated listing browse |
| `reservation_create.js` | p95 < 500ms @ 20 RPS | Authenticated reservation creation |
| `sse_connections.js` | 500 concurrent connections | SSE event stream scalability |

## Quick start

```bash
# Browse (no auth needed)
k6 run --env BASE_URL=http://staging.dormcycle.app browse_listings.js

# Reservation (needs a valid session + CSRF token + an available listing ID)
k6 run \
  --env BASE_URL=http://staging.dormcycle.app \
  --env SESSION_COOKIE=abc123 \
  --env CSRF_TOKEN=xyz789 \
  --env LISTING_ID=42 \
  reservation_create.js

# SSE (needs a valid session)
k6 run \
  --env BASE_URL=http://staging.dormcycle.app \
  --env SESSION_COOKIE=abc123 \
  sse_connections.js
```

## Baseline results (to be filled in after first run)

| Script | p50 | p95 | p99 | Error rate |
|--------|-----|-----|-----|------------|
| browse_listings | — | — | — | — |
| reservation_create | — | — | — | — |
| sse_connections | — | — | — | — |
