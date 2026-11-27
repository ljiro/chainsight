#!/usr/bin/env bash
# Restore a ChainSight dump into a database (TimescaleDB-aware). See docs/operations/backups.md.
#
#   restore.sh <dump file> <target database> [--force]
#
# The target must not exist, unless --force is given (it is then DROPPED first). Restoring
# into a new database and switching DATABASE_URL is safer than restoring in place.
set -euo pipefail

DUMP="${1:?usage: restore.sh <dump> <target db> [--force]}"
TARGET="${2:?usage: restore.sh <dump> <target db> [--force]}"
FORCE="${3:-}"
log() { printf '%s restore %s\n' "$(date -u +%FT%TZ)" "$*"; }

if [ -f "${DUMP}.sha256" ]; then
  (cd "$(dirname "$DUMP")" && sha256sum --check --status "$(basename "$DUMP").sha256") \
    || { log "error: checksum mismatch for ${DUMP}"; exit 1; }
  log "checksum ok"
fi

exists="$(psql -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '${TARGET}'")"
if [ "$exists" = "1" ]; then
  if [ "$FORCE" != "--force" ]; then
    log "error: database ${TARGET} exists (use --force to drop it)"; exit 1
  fi
  log "dropping existing ${TARGET}"
  psql -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE \"${TARGET}\" WITH (FORCE)"
fi

psql -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE \"${TARGET}\""
psql -d "$TARGET" -v ON_ERROR_STOP=1 <<SQL
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS postgis;
SELECT timescaledb_pre_restore();
SQL
# Extensions already exist, so their CREATE EXTENSION entries may report "already exists".
pg_restore --no-owner --no-privileges --exit-on-error --dbname="$TARGET" \
  --use-list=<(pg_restore --list "$DUMP" | grep -vE 'EXTENSION - (timescaledb|postgis)') "$DUMP"
psql -d "$TARGET" -v ON_ERROR_STOP=1 -c "SELECT timescaledb_post_restore();" >/dev/null
psql -d "$TARGET" -v ON_ERROR_STOP=1 -c "ANALYZE;" >/dev/null

log "done: $(psql -d "$TARGET" -tAc "SELECT version_num FROM alembic_version") is the schema revision"
