#!/bin/sh
# Restore a pg_dump created by backup.sh.
# Usage: DUMP_FILE=dormcycle_2026-05-08_02-00.sql.gz ./scripts/restore.sh
set -e

if [ -z "$DUMP_FILE" ]; then
    echo "Error: DUMP_FILE is required." >&2
    echo "  Usage: DUMP_FILE=/backups/dormcycle_YYYY-MM-DD_HH-MM.sql.gz $0" >&2
    exit 1
fi

if [ ! -f "$DUMP_FILE" ]; then
    echo "Error: $DUMP_FILE not found." >&2
    exit 1
fi

echo "[restore] Restoring from ${DUMP_FILE} → ${POSTGRES_DB:-dormcycle} @ ${POSTGRES_HOST:-postgres}:${POSTGRES_PORT:-5432}"
echo "[restore] WARNING: This will DROP and recreate the public schema. Ctrl-C within 5 seconds to abort."
sleep 5

PGPASSWORD="$POSTGRES_PASSWORD" psql \
    -h "${POSTGRES_HOST:-postgres}" \
    -p "${POSTGRES_PORT:-5432}" \
    -U "${POSTGRES_USER:-postgres}" \
    "${POSTGRES_DB:-dormcycle}" \
    -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"

gunzip -c "$DUMP_FILE" | PGPASSWORD="$POSTGRES_PASSWORD" psql \
    -h "${POSTGRES_HOST:-postgres}" \
    -p "${POSTGRES_PORT:-5432}" \
    -U "${POSTGRES_USER:-postgres}" \
    "${POSTGRES_DB:-dormcycle}"

echo "[restore] Done."
