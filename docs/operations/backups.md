# Backups and restore runbook

ChainSight keeps all state in PostgreSQL (TimescaleDB + PostGIS). Model artifacts and the
MLflow database on the `mldata` volume can be regenerated: retrain with
`python -m services.ml_jobs`. Redis holds only queues, live fan-out and rate-limit counters,
so it is not backed up.

## What runs

| Piece | Where |
|---|---|
| `infrastructure/backup/backup.sh` | `pg_dump --format=custom`, SHA-256 checksum, `pg_restore --list` verification, retention |
| `infrastructure/backup/restore.sh` | TimescaleDB-aware restore (`timescaledb_pre_restore` / `post_restore`) into a new or `--force`-dropped database |
| `infrastructure/backup/run-scheduled.sh` | Runs a backup daily at `BACKUP_AT` (UTC) |
| Compose service `backup` (profile `backup`) | Runs the schedule with the same Postgres major version as `db`, writing to `./backups` (`BACKUP_HOST_DIR`) |

```bash
docker compose --profile backup up -d backup      # daily schedule
docker compose --profile backup run --rm --entrypoint /scripts/backup.sh backup   # backup now
```

Retention defaults to the newest **7** dumps plus the newest **4** taken on a Sunday
(`BACKUP_KEEP_DAILY`, `BACKUP_KEEP_WEEKLY`).

**Copy dumps off the host.** A dump on the same disk as the database protects against
mistakes, not against losing the machine. Sync `./backups` to object storage with versioning
or object lock (for example `rclone sync` or `aws s3 sync` from cron), and encrypt it if the
bucket is not private. The dumps contain password hashes, API key hashes and audit logs.

### Recovery objectives

- **RPO:** up to 24 h with the daily schedule. If that is too long, run the backup more often
  or move to continuous WAL archiving (pgBackRest, or the managed database's point-in-time
  recovery). AIS positions can partly be backfilled from recorded NDJSON (`AIS_RECORD_FILE`).
  Shipments can be re-fetched from carriers. Research series can be re-collected
  (`python -m services.research_jobs`).
- **RTO:** a 53 MB dump (about 0.5 M positions, 0.7 M indicator observations) restored in
  about 10 s on a laptop. Expect minutes per GB, plus `ANALYZE`.

## Restore drill (do this monthly)

A backup that has never been restored is a hope, not a backup.

```bash
DUMP=/backups/chainsight-YYYYMMDDTHHMMSSZ.dump
docker compose --profile backup run --rm --entrypoint /scripts/restore.sh backup "$DUMP" chainsight_restore_drill --force
docker compose exec db psql -U chainsight -d chainsight_restore_drill -c \
  "SELECT (SELECT version_num FROM alembic_version), (SELECT count(*) FROM vessel_positions), \
          (SELECT count(*) FROM users), (SELECT count(*) FROM timescaledb_information.hypertables)"
docker compose exec db psql -U chainsight -d postgres -c 'DROP DATABASE chainsight_restore_drill WITH (FORCE)'
```

Last drill: 27 Nov 2026, dev stack. The restored schema was revision `0007`. Row counts
matched the source for every table written before the dump, including the 4 hypertables. The
`audit_logs` append-only trigger was still enforced after the restore.

## Disaster recovery: replace the production database

1. **Stop writers** so nothing is written to the old database meanwhile:
   `docker compose stop api worker beat ais-consumer` (and the web app, or show a maintenance page).
2. **Pick the dump.** Check its checksum (`restore.sh` does this automatically when the
   `.sha256` file sits next to the dump).
3. **Restore into a new database**: `restore.sh "$DUMP" chainsight_restored`.
4. **Check it**: schema revision, row counts and the latest `vessel_positions.observed_at`,
   then sign in with a known account against an API pointed at it.
5. **Switch**: rename the databases (`ALTER DATABASE chainsight RENAME TO chainsight_old;`
   then `ALTER DATABASE chainsight_restored RENAME TO chainsight;`), or point `DATABASE_URL`
   at the new name.
6. **Migrate if needed**: `docker compose run --rm migrate` (no-op if the dump is at head).
7. **Start the services** and watch `/api/v1/health/ready`, the Grafana overview dashboard and
   the data-freshness alerts. AIS gaps since the dump show up as feed gaps; they are not
   invented.
8. **Rotate secrets** if the incident involved a compromise: `JWT_SECRET` (signs everyone out),
   API keys (revoke in Settings), webhook secrets and database passwords.
9. Keep `chainsight_old` until you are confident, then drop it.

## Notes and caveats

- `pg_dump` warns about "circular foreign-key constraints" on TimescaleDB catalog tables.
  This is expected for full (schema + data) dumps and harmless; `restore.sh` restores them correctly.
- Restore into the **same TimescaleDB and PostgreSQL major versions** as the source. To upgrade
  versions, restore first and then upgrade.
- `--no-owner --no-privileges`: objects end up owned by the restoring user, which is right
  for the single-role setup here. Re-apply grants if you use separate roles.
