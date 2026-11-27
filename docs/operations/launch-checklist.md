# Launch checklist

The steps to take ChainSight from a verified local build to a monitored public deployment,
in order. The tooling has been tested (see [deployment.md](deployment.md)). The items left
here need accounts, a host or decisions that only the operator can provide.

Tools:
- `make preflight`: checks `.env.production` and the host before starting.
- `make verify URL=https://<domain>`: checks the running deployment from outside.
- Production compose with `--profile monitoring`: Prometheus, Alertmanager and Grafana.

## 1. Repository and images (GitHub)

- [ ] Push `main`. CI must pass all four jobs: `infra`, `backend`, `frontend` and `e2e`. The
      `e2e` and `infra` jobs have only ever run locally.
- [ ] The **Images** workflow publishes `ghcr.io/<owner>/chainsight-{api,web}:main`. Check the
      SBOM, provenance attestation and Trivy report on the run, and triage any critical
      findings.
- [ ] GHCR packages are **private** by default. Either make both packages public
      (Package settings → Change visibility), or create a fine-grained token with
      `read:packages` for the host (used in step 4).
- [ ] Tag the first release (`git tag v0.1.0 && git push --tags`). Production pins
      `CHAINSIGHT_VERSION=0.1.0`, never `main` or `latest`.
- [ ] Protect `main`: require the CI jobs and a pull request.

## 2. Accounts and credentials

| Need | Variable | Required? |
|---|---|---|
| Domain with DNS you control | `CHAINSIGHT_DOMAIN` | Yes |
| Email for Let's Encrypt expiry notices | `ACME_EMAIL` | Yes |
| aisstream.io API key (live AIS) | `AISSTREAM_API_KEY` | Yes for live vessels; `AIS_PROVIDER=simulator` otherwise |
| FRED API key (research market series) | `FRED_API_KEY` | Recommended |
| Alert destination: webhook, Slack incoming webhook, or SMTP | `ALERT_*` | Recommended |
| Off-host backup storage (S3, B2, another server) | n/a (host cron) | Yes |
| Carrier API credentials (Maersk, Hapag-Lloyd, DCSA) | `CARRIER_DCSA_CONFIG`, … | Optional; demo carrier and CSV import otherwise |
| Carrier push webhook secrets | `CARRIER_WEBHOOK_SECRETS` | Only if a carrier pushes events |
| OTLP collector | `OTEL_EXPORTER_OTLP_ENDPOINT` | Optional |

- [ ] Each secret generated fresh for production, not copied from `.env` or another
      environment: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`.

## 3. Host

Starting size: 4 vCPU, 8 GB RAM, 80 GB SSD. AIS positions dominate disk use: allow 1–2 GB per
million positions before compression.

- [ ] Linux with Docker Engine and Compose v2. Time sync on (chrony or systemd-timesyncd);
      JWTs and webhook signatures depend on the clock.
- [ ] Firewall: inbound 22/tcp, 80/tcp, 443/tcp and 443/udp only. Monitoring ports bind to
      127.0.0.1, so do not open them.
- [ ] SSH key-only, no root login. Unattended security upgrades on.
- [ ] DNS `A`/`AAAA` record for the domain pointing at the host, before the first start
      (Caddy requests the certificate immediately).
- [ ] Clone the repository at the release tag. Compose mounts the Caddyfile, monitoring and
      backup scripts from it: `git clone --branch v0.1.0 <repo> /opt/chainsight`.
- [ ] Create `BACKUP_HOST_DIR` (default `/var/backups/chainsight`).

## 4. First deployment

```bash
cd /opt/chainsight
cp infrastructure/deployment/.env.production.example .env.production
chmod 600 .env.production && $EDITOR .env.production
make preflight                      # fix every FAIL; read every WARN
echo "$GHCR_TOKEN" | docker login ghcr.io -u <user> --password-stdin   # private images only
docker compose -f infrastructure/deployment/docker-compose.prod.yml --env-file .env.production \
  --profile monitoring up -d
docker compose -f infrastructure/deployment/docker-compose.prod.yml --env-file .env.production ps
```

- [ ] `make preflight` shows no FAIL.
- [ ] All services are up; `api` and `db` report healthy. If the API exits, `logs api` names the
      unsafe setting it refused.
- [ ] From another machine, `make verify URL=https://<domain>` shows `0 failure(s)`.
- [ ] Sign in at `https://<domain>/login` as the bootstrap owner. Change the password in
      Settings → Account.
- [ ] Remove `BOOTSTRAP_ADMIN_*` from `.env.production`, then run `up -d` again.

## 5. Day one

- [ ] Settings: name the organization, invite members with the lowest role that works, and
      create API keys for integrations. Keys are shown once.
- [ ] Settings → Data sources: AIS, weather and FRED report fresh data within their schedules
      (AIS within minutes; weather hourly; FRED daily at 13:30 UTC).
- [ ] Grafana over an SSH tunnel
      (`ssh -L 3001:127.0.0.1:3001 -L 9090:127.0.0.1:9090 -L 9093:127.0.0.1:9093 <host>`):
      the **ChainSight overview** dashboard shows traffic, Celery tasks and AIS throughput.
      Prometheus → Status → Targets shows every job `up`.
- [ ] **Test alert delivery** end to end. A firing notification, then a resolved one, must
      reach the receiver:
      ```bash
      docker compose -f infrastructure/deployment/docker-compose.prod.yml --env-file .env.production \
        exec alertmanager amtool alert add ChainSightTestAlert severity=warning \
        --annotation='summary="Launch checklist test"' --alertmanager.url=http://localhost:9093
      ```
- [ ] First backup: `docker compose ... run --rm --entrypoint /scripts/backup.sh backup`.
      Checksum file written ([backups.md](backups.md)).
- [ ] Off-host copy of `BACKUP_HOST_DIR` scheduled with rclone, restic or similar, and one
      copy confirmed at the destination.
- [ ] Restore drill from the **off-host** copy into a scratch database; row counts match.

## 6. First week

- [ ] No alert stays firing. Look at every warning and either fix the cause or tune the rule.
- [ ] AIS ingestion is steady (no "ingestion stalled" alert). Rejection reasons on the
      dashboard look plausible.
- [ ] Disk growth is in line with sizing, and TimescaleDB compression is running on chunks
      older than 7 days.
- [ ] Audit log (Settings): only expected sign-ins, member and key changes.
- [ ] Prediction evaluation (`ml.evaluate`, hourly) accumulates scored predictions. These are
      the inputs for the Phase 8 model work.

## 7. Ongoing

- [ ] Monthly: restore drill from the off-host copy.
- [ ] Each release: back up, change `CHAINSIGHT_VERSION`, `pull`, `up -d`, then `make verify`.
      Read release notes for migrations ([deployment.md](deployment.md#upgrades-and-rollback)).
- [ ] Review Trivy findings and dependency updates for each release.
- [ ] If `JWT_SECRET` or a session may have leaked: rotate the secret **and** revoke sessions.
      Rotation alone only expires access tokens; refresh cookies would issue new ones.
      ```bash
      docker compose ... run --rm --no-deps api python -m services.auth revoke-sessions --all
      ```
      Use `--email` for a single user. Rotate carrier webhook secrets by listing old and new
      values in `CARRIER_WEBHOOK_SECRETS`, then dropping the old one.
- [ ] Remove members who leave, and revoke API keys nobody uses (Settings shows last use).
