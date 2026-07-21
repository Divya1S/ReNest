# ReNest — Deployment Guide

## Go live in ~15 minutes (Render Blueprint)

The repo ships a [`render.yaml`](../render.yaml) blueprint that provisions the
full stack: **web** (Django API + built SPA served same-origin by uvicorn/ASGI —
SSE-safe), **Celery worker**, **Celery beat**, **Redis**, and **Postgres**.

1. **Push the repo** (once):

   ```bash
   git init && git add -A
   git commit -m "ReNest: campus move-out marketplace with Gemini concierge"
   git branch -M main
   git remote add origin https://github.com/Divya1S/ReNest.git
   git push -u origin main
   ```

2. **Render Dashboard → New → Blueprint** → select the repo → Apply. The build
   runs `scripts/render-build.sh` (backend deps → `npm run build` → collectstatic
   → migrate) and health-checks `/api/health/live`.

3. **Fill the secrets** the blueprint created empty (web + worker services):
   `GEMINI_API_KEY`, `EMAIL_HOST`/`EMAIL_HOST_USER`/`EMAIL_HOST_PASSWORD`/
   `DEFAULT_FROM_EMAIL`, `AWS_S3_*` (see the Email and Media sections below),
   optionally `SENTRY_DSN`. If a name in `render.yaml` was taken, update the
   four `renest-web.onrender.com` URLs to the hostname Render actually assigned.

4. **Post-deploy checklist** (Render Shell on the web service):

   ```bash
   cd backend
   python manage.py sendtestemail you@yourdomain.edu   # email works?
   python manage.py seed_demo                          # optional demo content
   python manage.py assign_campuses                    # campus backfill
   python manage.py run_concierge_evals                # offline evals green?
   ```

   Then from your machine, a burst check against the live URL:

   ```bash
   .venv/bin/python scripts/loadtest.py --base-url https://<your-host> \
     --users 30 --duration 30 --max-error-rate 1
   ```

**How same-origin serving works:** when `frontend/dist/` exists (the build step
creates it), Django serves the SPA — WhiteNoise serves `/assets/*`, `/images/*`,
`/sw.js`, `/manifest.json` from the dist root, and a catch-all view returns
`index.html` (no-cache) for client-side routes. `DATABASE_URL` and `REDIS_URL`
are consumed directly; no config edits needed. Verified locally: `DEBUG=0` +
uvicorn serves `/`, deep SPA routes, hashed assets, service worker, API, and
admin static.

> Railway/Fly/Heroku: the same pieces apply — run `scripts/render-build.sh` as
> the build, the uvicorn command as the web process, plus `celery -A dormcycle
> worker` and `celery -A dormcycle beat` processes, `DATABASE_URL` + `REDIS_URL`
> attached.

---

## Database Connection Pooling

### Django `CONN_MAX_AGE`

`CONN_MAX_AGE = 60` is already set in `dormcycle/settings.py` via the
`POSTGRES_CONN_MAX_AGE` env var (default: 60 seconds). This keeps each
gunicorn worker's database connection alive between requests, avoiding a
new TCP handshake on every request.

### PgBouncer (recommended for > 50 concurrent users)

Each gunicorn worker holds up to 1 persistent connection. At 4 workers × N
dynos you can hit PostgreSQL's `max_connections` (default 100) quickly.
PgBouncer sits between gunicorn and Postgres and multiplexes connections.

**Recommended mode: transaction pooling**

```
[databases]
dormcycle = host=<PG_HOST> port=5432 dbname=<PG_DB>

[pgbouncer]
pool_mode = transaction
max_client_conn = 500
default_pool_size = 20
server_idle_timeout = 600
listen_port = 6432
listen_addr = *
auth_type = md5
auth_file = /etc/pgbouncer/userlist.txt
```

Set `DATABASE_URL` to point at PgBouncer (`localhost:6432`) instead of
Postgres directly. Keep `CONN_MAX_AGE = 0` when using PgBouncer in
transaction mode — persistent connections conflict with transaction pooling.

### Heroku / Render

Both platforms offer managed Postgres. For Heroku, the
`heroku-postgresql:standard-0` plan includes PgBouncer via
`DATABASE_CONNECTION_POOL_URL`. Set that as `DATABASE_URL` and set
`POSTGRES_CONN_MAX_AGE=0`.

---

## Environment Variables

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | — | Required in production |
| `POSTGRES_CONN_MAX_AGE` | `60` | Set to `0` when using PgBouncer transaction mode |
| `DJANGO_SECRET_KEY` | — | Required; generated at deploy time |
| `ALLOWED_HOSTS` | `localhost` | Comma-separated list |
| `CORS_ALLOWED_ORIGINS` | — | Frontend origin(s) |
| `GEMINI_API_KEY` | — | Required for all AI features (concierge, scan detection, moderation) |
| `APP_BASE_URL` | `http://localhost:5173` | Used in email links |
| `CAMPUS_EMAIL_DOMAINS` | *(open)* | Comma-separated; restricts registration |
| `DEFAULT_FROM_EMAIL` | — | Sender address for transactional emails |
| `EMAIL_HOST` / `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` | — | SMTP credentials; unset → boot warning, emails dropped |
| `AWS_S3_BUCKET_NAME` (+ keys) | — | Switches media uploads to S3/R2; unset → boot warning, local disk |

---

## Gunicorn

```bash
gunicorn dormcycle.wsgi:application \
  --workers 4 \
  --worker-class sync \
  --timeout 30 \
  --bind 0.0.0.0:8000
```

For SSE endpoints (Phase 7), switch to `--worker-class gthread` with
`--threads 2` or use an ASGI server (uvicorn/daphne) once async views are
added.

---

## Static Files

```bash
python manage.py collectstatic --noinput
```

Served by WhiteNoise (`whitenoise.middleware.WhiteNoiseMiddleware` is in
`MIDDLEWARE`, compressed + cached). No separate web server or CDN required.

---

## Redis is required in production

Dev falls back to LocMem cache and eager Celery, which is correct on one
process only. With multiple gunicorn workers, these subsystems silently
misbehave without a shared Redis (`REDIS_URL`):

- concierge **circuit breaker** state (each worker would trip separately)
- concierge **semantic cache** (per-worker caches, near-zero hit rate)
- DRF **throttles** (limits multiply by worker count)
- dashboard/insight **aggregation caches** and **sessions**
- **Celery** broker (emails, AI detection, notifications become no-ops without a worker)

Set `REDIS_URL` and run at least one Celery worker + beat alongside the web
process.

---

## Load testing

`scripts/loadtest.py` replays a browse-heavy anonymous traffic mix (weighted:
listings, search, impact, hubs, health) and reports per-endpoint p50/p95/p99 +
total RPS. It exits non-zero above `--max-error-rate`, so it can gate a deploy:

```bash
.venv/bin/python scripts/loadtest.py --base-url https://api.renest.app   --users 50 --duration 60 --max-error-rate 1
```

Baseline (M-series laptop, SQLite dev server, 8 workers): ~1,300 req/s,
p95 20 ms, 0 errors. Re-baseline against the real deployment with Postgres +
Redis before a move-out weekend and keep the numbers in this file.

---

## Email (production)

Dev uses the console backend automatically. In production, configure any SMTP
provider — the app warns at boot if `EMAIL_HOST` is unset:

1. **Pick a provider.** Resend is the fastest to set up (free tier, SMTP:
   `smtp.resend.com`, user `resend`, password = API key). AWS SES is the
   cheapest at volume; Postmark has the best deliverability reputation.
2. **Verify your sending domain** with the provider, then add the DNS records
   they give you — **SPF** and **DKIM** are required, or Gmail will junk
   everything. Add `DMARC` (`v=DMARC1; p=none;`) for monitoring.
3. **Set the env vars** (`EMAIL_HOST`, `EMAIL_PORT=587`, `EMAIL_HOST_USER`,
   `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS=1`, `DEFAULT_FROM_EMAIL` on the
   verified domain).
4. **Verify from the running server:**

   ```bash
   python manage.py sendtestemail you@yourdomain.edu
   ```

Every user-facing email carries RFC 8058 `List-Unsubscribe` one-click headers
(required by Gmail/Yahoo bulk-sender rules) plus a footer link; `EMAIL_TIMEOUT`
(default 10s) keeps a hung SMTP connection from pinning a Celery worker.

---

## Media storage (production)

Uploads (listing photos, room scans, receipts) live on local disk in dev. On
hosts with ephemeral filesystems (Render, Railway, Heroku) they vanish on
redeploy — the app warns at boot until a bucket is configured. Setting
`AWS_S3_BUCKET_NAME` switches Django's default file storage to S3-compatible
storage; thumbnails and AI detection already read through the storage API.

**Cloudflare R2 (recommended — zero egress fees):**

1. Create an R2 bucket; enable public access (or attach a custom domain).
2. Create an R2 API token (Object Read & Write) → access key + secret.
3. Set:

   ```bash
   AWS_S3_BUCKET_NAME=renest-media
   AWS_S3_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com
   AWS_ACCESS_KEY_ID=...
   AWS_SECRET_ACCESS_KEY=...
   AWS_S3_CDN_DOMAIN=media.yourdomain.edu   # the bucket's public/custom domain
   ```

**AWS S3:** same variables minus `AWS_S3_ENDPOINT_URL`, plus
`AWS_S3_REGION_NAME`. Keep the bucket public-read (or set
`AWS_S3_QUERYSTRING_AUTH=1` for signed URLs on a private bucket).

The presigned direct-upload endpoint (`POST /api/listings/:id/upload-url`)
uses the same variables and works with both providers.

**Verify:** upload a listing photo, confirm the image URL points at the
bucket/CDN, then redeploy and confirm the photo survives.
