#!/usr/bin/env bash
# Container entrypoint: run backup.sh every day at BACKUP_AT (UTC, HH:MM; default 02:30).
set -euo pipefail
AT="${BACKUP_AT:-02:30}"
echo "scheduled backups daily at ${AT} UTC into ${BACKUP_DIR:-/backups}"
if [ "${BACKUP_ON_START:-false}" = "true" ]; then /scripts/backup.sh || echo "initial backup failed"; fi
while true; do
  now=$(date -u +%s)
  next=$(date -u -d "today ${AT}" +%s)
  [ "$next" -le "$now" ] && next=$(date -u -d "tomorrow ${AT}" +%s)
  sleep $((next - now))
  /scripts/backup.sh || echo "backup failed (exit $?)"   # keep the schedule alive; alerts read the log
done
