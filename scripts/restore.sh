#!/usr/bin/env bash
# Restore a pg_dump created by backup.sh.
# Usage: DUMP_FILE=dormcycle_2026-05-08_02-00.sql.gz ./scripts/restore.sh
#
# The dump is checked before anything is dropped, and the drop plus the whole
# restore run as one transaction with ON_ERROR_STOP. Without that flag psql
# carries on past a failed statement and still exits 0, so a broken restore
# used to print "Done." over a half-filled database. Now the first error
# aborts and rolls back, leaving the database exactly as it was.
set -euo pipefail

DUMP_FILE="${DUMP_FILE:-}"

if [ -z "$DUMP_FILE" ]; then
    echo "Error: DUMP_FILE is required." >&2
    echo "  Usage: DUMP_FILE=/backups/dormcycle_YYYY-MM-DD_HH-MM.sql.gz $0" >&2
    exit 1
fi

if [ ! -f "$DUMP_FILE" ]; then
    echo "Error: $DUMP_FILE not found." >&2
    exit 1
fi

command -v psql >/dev/null 2>&1 || { echo "Error: psql is not installed." >&2; exit 1; }

if ! gzip -t "$DUMP_FILE"; then
    echo "Error: $DUMP_FILE is not a valid gzip archive. Nothing was changed." >&2
    exit 1
fi
TAIL="$(gzip -dc "$DUMP_FILE" | tail -n 20)"
case "$TAIL" in
    *"PostgreSQL database dump complete"*) ;;
    *)
        echo "Error: $DUMP_FILE is truncated (no pg_dump completion marker). Nothing was changed." >&2
        exit 1
        ;;
esac

echo "[restore] Restoring from ${DUMP_FILE} to ${POSTGRES_DB:-dormcycle} @ ${POSTGRES_HOST:-postgres}:${POSTGRES_PORT:-5432}"
echo "[restore] WARNING: This will DROP and recreate the public schema. Ctrl-C within ${RESTORE_GRACE_SECONDS:-5} seconds to abort."
sleep "${RESTORE_GRACE_SECONDS:-5}"

if ! {
    echo "DROP SCHEMA public CASCADE;"
    echo "CREATE SCHEMA public;"
    gzip -dc "$DUMP_FILE"
} | PGPASSWORD="${POSTGRES_PASSWORD:-}" psql \
    --no-psqlrc \
    --quiet \
    --set ON_ERROR_STOP=1 \
    --single-transaction \
    -h "${POSTGRES_HOST:-postgres}" \
    -p "${POSTGRES_PORT:-5432}" \
    -U "${POSTGRES_USER:-postgres}" \
    "${POSTGRES_DB:-dormcycle}"
then
    echo "[restore] FAILED. The restore was rolled back; the database is unchanged." >&2
    exit 1
fi

echo "[restore] Done."
