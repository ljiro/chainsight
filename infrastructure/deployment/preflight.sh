#!/bin/sh
# Checks a production host and its .env.production before `docker compose up`.
#
#   infrastructure/deployment/preflight.sh [path/to/.env.production]
#
# Prints one line per check (PASS / WARN / FAIL) and exits 1 if anything FAILed. It reads the
# env file as data (it is never sourced), and it never prints secret values.
set -u

ENV_FILE=${1:-.env.production}
fails=0
warns=0
pass() { printf 'PASS  %s\n' "$1"; }
warn() { printf 'WARN  %s\n' "$1"; warns=$((warns + 1)); }
fail() { printf 'FAIL  %s\n' "$1"; fails=$((fails + 1)); }

# Last assignment wins, as in compose. Strips optional surrounding quotes.
get() {
  sed -n "s/^[[:space:]]*$1=//p" "$ENV_FILE" | tail -n 1 |
    sed 's/[[:space:]]*$//; s/^"\(.*\)"$/\1/; s/^'"'"'\(.*\)'"'"'$/\1/'
}
len() { printf '%s' "$1" | wc -c | tr -d ' '; }

if [ ! -f "$ENV_FILE" ]; then
  fail "$ENV_FILE not found (copy infrastructure/deployment/.env.production.example)"
  exit 1
fi

# --- the file itself ------------------------------------------------------------------------
perms=$(stat -c '%a' "$ENV_FILE" 2>/dev/null || stat -f '%Lp' "$ENV_FILE")
case "$perms" in
  600 | 400) pass "$ENV_FILE permissions are $perms" ;;
  *) fail "$ENV_FILE permissions are $perms; run: chmod 600 $ENV_FILE" ;;
esac
if command -v git >/dev/null 2>&1 && git ls-files --error-unmatch "$ENV_FILE" >/dev/null 2>&1; then
  fail "$ENV_FILE is tracked by git; remove it from the index and rotate its secrets"
fi

# --- required values ------------------------------------------------------------------------
for var in CHAINSIGHT_DOMAIN ACME_EMAIL GHCR_OWNER CHAINSIGHT_VERSION POSTGRES_PASSWORD JWT_SECRET; do
  [ -n "$(get "$var")" ] || fail "$var is empty"
done

domain=$(get CHAINSIGHT_DOMAIN)
case "$domain" in
  "" ) ;;
  *example.com | localhost | *.local | *.localhost) fail "CHAINSIGHT_DOMAIN is a placeholder or local name ($domain)" ;;
  *) pass "CHAINSIGHT_DOMAIN is $domain" ;;
esac
case "$(get ACME_EMAIL)" in
  *@example.com) fail "ACME_EMAIL is the placeholder; Let's Encrypt sends expiry notices there" ;;
esac

version=$(get CHAINSIGHT_VERSION)
case "$version" in
  "") ;;
  main | latest | sha-*) warn "CHAINSIGHT_VERSION=$version is not a release; pin a version tag such as 1.2.3" ;;
  *) pass "CHAINSIGHT_VERSION pinned to $version" ;;
esac

jwt=$(get JWT_SECRET)
if [ -n "$jwt" ]; then
  if [ "$(len "$jwt")" -lt 32 ]; then fail "JWT_SECRET is shorter than 32 characters (the API refuses it)"
  else pass "JWT_SECRET length ok"; fi
fi
pg=$(get POSTGRES_PASSWORD)
if [ -n "$pg" ]; then
  case "$pg" in
    chainsight | postgres | password | change-me) fail "POSTGRES_PASSWORD is a default value" ;;
    *) if [ "$(len "$pg")" -lt 16 ]; then warn "POSTGRES_PASSWORD is shorter than 16 characters"
       else pass "POSTGRES_PASSWORD length ok"; fi ;;
  esac
fi
if [ -n "$jwt" ] && [ "$jwt" = "$pg" ]; then fail "JWT_SECRET and POSTGRES_PASSWORD are the same value"; fi

if [ -n "$(get BOOTSTRAP_ADMIN_PASSWORD)" ]; then
  warn "BOOTSTRAP_ADMIN_* is set: fine for the first start, remove it after the first sign-in"
fi

# --- monitoring and alerting (only if the profile will be used) -----------------------------
if [ -n "$(get GRAFANA_ADMIN_PASSWORD)" ] || [ -n "$(get ALERT_WEBHOOK_URL)$(get ALERT_SLACK_WEBHOOK_URL)$(get ALERT_EMAIL_TO)" ]; then
  [ -n "$(get GRAFANA_ADMIN_PASSWORD)" ] || warn "GRAFANA_ADMIN_PASSWORD is empty; Grafana starts with admin/admin"
  if [ -n "$(get ALERT_WEBHOOK_URL)$(get ALERT_SLACK_WEBHOOK_URL)$(get ALERT_EMAIL_TO)" ]; then
    pass "an alert receiver is configured"
  else
    warn "no alert receiver (ALERT_WEBHOOK_URL / ALERT_SLACK_WEBHOOK_URL / ALERT_EMAIL_TO); alerts go nowhere"
  fi
  if [ -n "$(get ALERT_EMAIL_TO)" ] && { [ -z "$(get ALERT_SMTP_SMARTHOST)" ] || [ -z "$(get ALERT_SMTP_FROM)" ]; }; then
    fail "ALERT_EMAIL_TO needs ALERT_SMTP_SMARTHOST and ALERT_SMTP_FROM"
  fi
else
  warn "monitoring not configured (GRAFANA_ADMIN_PASSWORD and an alert receiver); see docs/operations/observability.md"
fi

# --- providers ------------------------------------------------------------------------------
if [ "$(get AIS_PROVIDER)" = aisstream ] && [ -z "$(get AISSTREAM_API_KEY)" ]; then
  fail "AIS_PROVIDER=aisstream needs AISSTREAM_API_KEY"
fi
[ -n "$(get FRED_API_KEY)" ] || warn "FRED_API_KEY is empty; research market series will not refresh"

# --- host -----------------------------------------------------------------------------------
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  pass "$(docker compose version | head -n 1)"
else
  fail "docker compose v2 is not available"
fi

backup_dir=$(get BACKUP_HOST_DIR)
backup_dir=${backup_dir:-/var/backups/chainsight}
if [ -d "$backup_dir" ]; then pass "backup directory $backup_dir exists"
else warn "backup directory $backup_dir does not exist yet (Docker creates it owned by root)"; fi

if [ -n "$domain" ] && command -v getent >/dev/null 2>&1; then
  if getent hosts "$domain" >/dev/null 2>&1; then pass "$domain resolves to $(getent hosts "$domain" | awk '{print $1}' | head -n 1)"
  else fail "$domain does not resolve; point its DNS at this host before starting (ACME needs it)"; fi
fi

for port in 80 443; do
  if command -v ss >/dev/null 2>&1 && ss -ltnH "sport = :$port" 2>/dev/null | grep -q .; then
    if ! docker ps --format '{{.Names}} {{.Ports}}' 2>/dev/null | grep -q "caddy.*:$port->"; then
      fail "port $port is already in use by another process"
    fi
  fi
done

printf '\n%s failure(s), %s warning(s)\n' "$fails" "$warns"
[ "$fails" -eq 0 ]
