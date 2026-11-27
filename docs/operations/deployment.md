# Deployment

A single-host production deployment from published images, using
`infrastructure/deployment/docker-compose.prod.yml`. Larger installs can map the same
services onto a container platform. The images, health checks and environment are the same.

## Images

`.github/workflows/images.yml` builds and publishes to GitHub Container Registry:

| Trigger | Tags |
|---|---|
| Push to `main` | `ghcr.io/<owner>/chainsight-{api,web}:main`, `:sha-<short>` |
| Tag `v1.2.3` | `:1.2.3`, `:1.2`, `:latest`, `:sha-<short>` |

Each image ships with an SBOM, build provenance (`actions/attest-build-provenance`) and a
Trivy report. The scan reports findings but does not fail the build. The API image records
the commit (`CHAINSIGHT_GIT_SHA`) on research runs. The web image is built with
`NEXT_PUBLIC_LIVE_WS_URL=same-origin`, so the live map connects to `wss://<host>/api/v1/ws/...`
through the edge proxy.

Pin a version (`CHAINSIGHT_VERSION=1.2.3`) in production, not `main` or `latest`.

## Topology

```
Internet ──443──> caddy ──/api/v1/*──> api (uvicorn × API_WORKERS)
                       └──everything else──> web (Next.js)
internal network: db (TimescaleDB+PostGIS), redis, worker, beat, ais-consumer, backup
```

Only Caddy publishes ports. It gets and renews a Let's Encrypt certificate for
`CHAINSIGHT_DOMAIN`, adds security headers (HSTS, `nosniff`, `DENY` framing), and **replaces**
`X-Forwarded-For` with the real client address. That is why the API runs with
`TRUSTED_PROXY_COUNT=1` here, and only here.

## First deployment

The full go-live sequence (GitHub, accounts, host, day one, first week) is in the
[launch checklist](launch-checklist.md). The essentials:

1. Point the domain's DNS at the host. Open 80/tcp (ACME) and 443/tcp+udp.
2. Create the environment file in the repository root (it is gitignored):
   ```bash
   cp infrastructure/deployment/.env.production.example .env.production
   python -c "import secrets; print(secrets.token_urlsafe(48))"   # JWT_SECRET
   python -c "import secrets; print(secrets.token_urlsafe(32))"   # POSTGRES_PASSWORD
   ```
   Fill in the domain, ACME email, image owner and version, the two secrets, and
   `BOOTSTRAP_ADMIN_EMAIL` / `BOOTSTRAP_ADMIN_PASSWORD`. For monitoring, also set
   `GRAFANA_ADMIN_PASSWORD` and an alert receiver ([observability.md](observability.md#alert-routing-alertmanager)).
   Then run `make preflight` (`infrastructure/deployment/preflight.sh`). It checks file
   permissions, placeholders, secret strength, a pinned version, provider keys, DNS and free
   ports, and exits non-zero on any FAIL. It never prints secret values.
3. Start it. The same file feeds compose interpolation (`--env-file`) and the containers
   (`env_file`). The `monitoring` profile adds Prometheus, Alertmanager and Grafana on
   127.0.0.1:
   ```bash
   docker compose -f infrastructure/deployment/docker-compose.prod.yml --env-file .env.production \
     --profile monitoring up -d
   ```
   `migrate` applies migrations and seeds the port registry, then the API starts. The API
   **refuses to start** with unsafe production settings; check `docker compose logs api`.
   From another machine, run `make verify URL=https://<domain>`
   (`infrastructure/deployment/verify.sh`). It checks:
   - health and readiness;
   - that data routes require authentication and `/metrics` is not exposed;
   - the HTTP→HTTPS redirect and a trusted certificate;
   - the security headers and that no `Server` header is sent;
   - the generic sign-in error.
4. Sign in at `https://<domain>/login` as the bootstrap owner. Then remove the
   `BOOTSTRAP_ADMIN_*` values from `.env.production`. They are only used while no user
   exists, but they should not linger.
5. In Settings: add members, create API keys for integrations, and check carrier
   integrations and source health.
6. Schedule an off-host copy of `BACKUP_HOST_DIR` and run a restore drill
   ([backups.md](backups.md)).

Verified on 27 Nov 2026 by running this exact compose file with locally built images and
`CHAINSIGHT_DOMAIN=localhost` (Caddy's internal CA):
- The Playwright smoke suite passed over HTTPS.
- Cookies were `Secure`, and the WebSocket connected same-origin through Caddy.
- A spoofed `X-Forwarded-For` was ignored in favour of the real peer.
- `/metrics` was not reachable from outside.
- The API rejected a short `JWT_SECRET`.
- Metrics aggregated across both uvicorn workers.

Repeated the same day with `--profile monitoring`:
- `verify.sh` passed 12/12;
- every Prometheus target was up, and Prometheus was connected to Alertmanager;
- Grafana provisioned the dashboard, and the default `admin/admin` login was refused.

## Upgrades and rollback

```bash
sed -i 's/^CHAINSIGHT_VERSION=.*/CHAINSIGHT_VERSION=1.3.0/' .env.production
docker compose -f infrastructure/deployment/docker-compose.prod.yml --env-file .env.production pull
docker compose -f infrastructure/deployment/docker-compose.prod.yml --env-file .env.production up -d
```

- Take a backup first (`docker compose ... run --rm --entrypoint /scripts/backup.sh backup`).
- `migrate` runs before the API starts. Migrations are forward-only in production.
- **Rollback:** set the previous version and `up -d`. If the new version ran a migration the
  old code cannot read, restore the pre-upgrade backup ([backups.md](backups.md)). Release
  notes call out migrations.

## Sizing and scaling

- `API_WORKERS` (default 2) uvicorn processes. Metrics are aggregated across them
  (`PROMETHEUS_MULTIPROC_DIR`). Research analyses are CPU-bound and run in a thread pool, so
  give the host a core per worker.
- `WORKER_CONCURRENCY` (default 2) Celery processes. ML training and research backtests run
  on the `ml` queue. Run a dedicated worker with `-Q ml` if they slow down ingestion.
- Run exactly **one** `beat` and **one** `ais-consumer`.
- Postgres: AIS positions are the largest table (compressed hypertable chunks after 7 days).
  Allow 1–2 GB per million positions before compression.

## Operations checklist

The complete list is in the [launch checklist](launch-checklist.md); the essentials:

- [ ] `.env.production` readable only by the deploy user (`chmod 600`)
- [ ] `JWT_SECRET` and `POSTGRES_PASSWORD` generated, not reused
- [ ] Bootstrap credentials removed after the first sign-in
- [ ] Backups copied off-host; restore drill done
- [ ] `make preflight` clean before starting; `make verify URL=...` clean afterwards
- [ ] `--profile monitoring` running, a receiver configured, and a test alert received
      ([observability.md](observability.md#alert-routing-alertmanager))
- [ ] Provider keys set (`AISSTREAM_API_KEY`, carrier credentials, `FRED_API_KEY`), and
      statuses checked in Settings
