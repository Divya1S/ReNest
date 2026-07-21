#!/bin/sh
# Daily pg_dump with 7-day rolling retention.
# Writes to BACKUP_DIR (default /backups), named dormcycle_YYYY-MM-DD_HH-MM.sql.gz.
set -e

BACKUP_DIR="${BACKUP_DIR:-/backups}"
RETAIN_DAYS="${RETAIN_DAYS:-7}"
TIMESTAMP="$(date -u +%Y-%m-%d_%H-%M)"
FILENAME="dormcycle_${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"

echo "[backup] Starting dump → ${BACKUP_DIR}/${FILENAME}"
PGPASSWORD="$POSTGRES_PASSWORD" pg_dump \
    -h "${POSTGRES_HOST:-postgres}" \
    -p "${POSTGRES_PORT:-5432}" \
    -U "${POSTGRES_USER:-postgres}" \
    "${POSTGRES_DB:-dormcycle}" \
    | gzip -9 > "${BACKUP_DIR}/${FILENAME}"

SIZE="$(du -sh "${BACKUP_DIR}/${FILENAME}" | cut -f1)"
echo "[backup] Done. Size: ${SIZE}"

echo "[backup] Pruning dumps older than ${RETAIN_DAYS} days..."
find "$BACKUP_DIR" -maxdepth 1 -name "dormcycle_*.sql.gz" \
    -mtime "+${RETAIN_DAYS}" -delete -print \
    | sed 's/^/[backup] Removed: /'

REMAINING="$(find "$BACKUP_DIR" -maxdepth 1 -name "dormcycle_*.sql.gz" | wc -l | tr -d ' ')"
echo "[backup] Retained ${REMAINING} dump(s)."
