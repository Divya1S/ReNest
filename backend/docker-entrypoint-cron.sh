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

while true; do
    python /app/manage.py run_maintenance || echo "[maintenance] run failed; retrying in 30 min" >&2
    sleep 1800
done
