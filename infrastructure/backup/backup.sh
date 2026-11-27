#!/usr/bin/env bash
# Logical backup of the ChainSight database with pg_dump (custom format), then verification.
#
#   PGHOST PGPORT PGUSER PGPASSWORD PGDATABASE   connection (libpq variables)
#   BACKUP_DIR            where dumps go                      (default /backups)
#   BACKUP_KEEP_DAILY     how many daily dumps to keep         (default 7)
#   BACKUP_KEEP_WEEKLY    how many Sunday dumps to keep        (default 4)
#
# Each run writes  <db>-<UTC timestamp>.dump  and  .dump.sha256, verifies the archive with
# pg_restore --list, then prunes. Exit status is non-zero on any failure (cron/monitoring).
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/backups}"
KEEP_DAILY="${BACKUP_KEEP_DAILY:-7}"
KEEP_WEEKLY="${BACKUP_KEEP_WEEKLY:-4}"
DB="${PGDATABASE:?PGDATABASE is required}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${BACKUP_DIR}/${DB}-${STAMP}.dump"

mkdir -p "$BACKUP_DIR"
log() { printf '%s backup %s\n' "$(date -u +%FT%TZ)" "$*"; }

log "start db=${DB} file=${OUT}"
# Hypertable chunks produce harmless "circular foreign-key" notices; real errors still fail.
pg_dump --format=custom --compress=6 --no-owner --no-privileges --file="${OUT}.partial" "$DB"
mv "${OUT}.partial" "$OUT"
(cd "$BACKUP_DIR" && sha256sum "$(basename "$OUT")" > "$(basename "$OUT").sha256")

entries="$(pg_restore --list "$OUT" | grep -vc '^;')"
if [ "$entries" -lt 10 ]; then
  log "error: archive has only ${entries} entries"; exit 1
fi
log "ok size=$(du -h "$OUT" | cut -f1) entries=${entries}"

# Retention: keep the newest KEEP_DAILY dumps, plus the newest KEEP_WEEKLY taken on a Sunday.
mapfile -t all < <(ls -1t "$BACKUP_DIR"/"$DB"-*.dump 2>/dev/null)
keep=("${all[@]:0:$KEEP_DAILY}")
weekly=0
for f in "${all[@]}"; do
  day="$(basename "$f" | sed -E 's/.*-([0-9]{8})T.*/\1/')"
  if [ "$(date -u -d "$day" +%u 2>/dev/null || echo 0)" = "7" ] && [ "$weekly" -lt "$KEEP_WEEKLY" ]; then
    keep+=("$f"); weekly=$((weekly + 1))
  fi
done
for f in "${all[@]}"; do
  if ! printf '%s\n' "${keep[@]}" | grep -qxF "$f"; then
    rm -f "$f" "$f.sha256"; log "pruned $(basename "$f")"
  fi
done
