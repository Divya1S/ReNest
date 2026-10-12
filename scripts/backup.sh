#!/usr/bin/env bash
# Daily pg_dump with rolling retention (default 7 days).
# Writes to BACKUP_DIR (default /backups), named dormcycle_YYYY-MM-DD_HH-MM.sql.gz.
#
# A dump is only kept, and old dumps are only pruned, after the new file has
# been verified. pipefail matters here: with plain `set -e` the exit status of
# `pg_dump | gzip` is gzip's, so a failed dump used to leave a tiny valid .gz
# behind, report success, and then let retention delete the good backups.
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/backups}"
RETAIN_DAYS="${RETAIN_DAYS:-7}"
# A real dump of this schema is far larger; anything below this is an error
# page or an empty stream, not a backup.
MIN_DUMP_BYTES="${MIN_DUMP_BYTES:-1024}"
TIMESTAMP="$(date -u +%Y-%m-%d_%H-%M)"
FILENAME="dormcycle_${TIMESTAMP}.sql.gz"
FINAL="${BACKUP_DIR}/${FILENAME}"
PARTIAL="${FINAL}.partial"

fail() {
    echo "[backup] ERROR: $*" >&2
    exit 1
}

case "$RETAIN_DAYS" in
    ''|*[!0-9]*) fail "RETAIN_DAYS must be a whole number of days, got '${RETAIN_DAYS}'." ;;
esac
case "$MIN_DUMP_BYTES" in
    ''|*[!0-9]*) fail "MIN_DUMP_BYTES must be a whole number, got '${MIN_DUMP_BYTES}'." ;;
esac
command -v pg_dump >/dev/null 2>&1 || fail "pg_dump is not installed."
command -v gzip >/dev/null 2>&1 || fail "gzip is not installed."

mkdir -p "$BACKUP_DIR"
[ -w "$BACKUP_DIR" ] || fail "BACKUP_DIR ${BACKUP_DIR} is not writable."

# Never leave a half-written file behind, whatever the exit path.
trap 'rm -f "$PARTIAL"' EXIT

echo "[backup] Starting dump to ${FINAL}"
PGPASSWORD="${POSTGRES_PASSWORD:-}" pg_dump \
    -h "${POSTGRES_HOST:-postgres}" \
    -p "${POSTGRES_PORT:-5432}" \
    -U "${POSTGRES_USER:-postgres}" \
    "${POSTGRES_DB:-dormcycle}" \
    | gzip -9 > "$PARTIAL"

# Validate before calling it a backup: the archive must be intact, plausibly
# sized, and end with the marker pg_dump writes only when it ran to completion.
gzip -t "$PARTIAL" || fail "Dump archive is corrupt."
BYTES="$(wc -c < "$PARTIAL" | tr -d ' ')"
[ "$BYTES" -ge "$MIN_DUMP_BYTES" ] || fail "Dump is only ${BYTES} bytes (minimum ${MIN_DUMP_BYTES})."
TAIL="$(gzip -dc "$PARTIAL" | tail -n 20)"
case "$TAIL" in
    *"PostgreSQL database dump complete"*) ;;
    *) fail "Dump is truncated: the pg_dump completion marker is missing." ;;
esac

mv "$PARTIAL" "$FINAL"
SIZE="$(du -sh "$FINAL" | cut -f1)"
echo "[backup] Done. Size: ${SIZE}"

echo "[backup] Pruning dumps older than ${RETAIN_DAYS} days..."
find "$BACKUP_DIR" -maxdepth 1 -name "dormcycle_*.sql.gz" \
    -mtime "+${RETAIN_DAYS}" -delete -print \
    | sed 's/^/[backup] Removed: /'

REMAINING="$(find "$BACKUP_DIR" -maxdepth 1 -name "dormcycle_*.sql.gz" | wc -l | tr -d ' ')"
echo "[backup] Retained ${REMAINING} dump(s)."
