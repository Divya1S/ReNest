#!/bin/sh
# Wait for postgres, then run maintenance every 30 minutes.
# A sleep loop is used instead of cron so the container environment
# (POSTGRES_*/DJANGO_* vars) reaches the job and no root cron daemon is needed.
set -e

echo "Waiting for postgres..."
until python -c "
import os, psycopg
psycopg.connect(
    dbname=os.environ.get('POSTGRES_DB','dormcycle'),
    user=os.environ.get('POSTGRES_USER','postgres'),
    password=os.environ.get('POSTGRES_PASSWORD','postgres'),
    host=os.environ.get('POSTGRES_HOST','postgres'),
    port=int(os.environ.get('POSTGRES_PORT','5432')),
).close()
" 2>/dev/null; do
    sleep 2
done
echo "Postgres ready. Running maintenance every 30 minutes."

# The tick set runs on every pass. The daily set runs once per UTC date and
# the weekly set once per ISO week, on Sunday, so all three job sets that the
# hosted cron drives also run under docker compose. State lives in shell
# variables: a restart repeats the daily set (its jobs are idempotent) but
# cannot repeat the weekly digest on any day other than Sunday.
last_daily=""
last_weekly=""

while true; do
    python /app/manage.py run_maintenance || echo "[maintenance] tick failed; retrying in 30 min" >&2

    today=$(date -u +%Y-%m-%d)
    if [ "$today" != "$last_daily" ]; then
        if python /app/manage.py run_maintenance --jobs daily; then
            last_daily="$today"
        else
            echo "[maintenance] daily set failed; retrying on the next pass" >&2
        fi
    fi

    week=$(date -u +%G-%V)
    if [ "$(date -u +%u)" = "7" ] && [ "$week" != "$last_weekly" ]; then
        if python /app/manage.py run_maintenance --jobs weekly; then
            last_weekly="$week"
        else
            echo "[maintenance] weekly set failed; retrying on the next pass" >&2
        fi
    fi

    sleep 1800
done
