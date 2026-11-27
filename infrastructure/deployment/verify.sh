#!/bin/sh
# Post-deployment checks against the public URL, from outside the host.
#
#   infrastructure/deployment/verify.sh https://chainsight.example.com
#
# Set VERIFY_INSECURE=1 for a drill with a self-signed certificate (Caddy's internal CA).
set -u
# `cond && pass || fail` is safe here: pass and fail always succeed.
# shellcheck disable=SC2015

BASE=${1:?usage: verify.sh https://<domain>}
BASE=${BASE%/}
CURL="curl -sS --max-time 15"
[ "${VERIFY_INSECURE:-0}" = 1 ] && CURL="$CURL -k"
fails=0
pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1"; fails=$((fails + 1)); }

status() { $CURL -o /dev/null -w '%{http_code}' "$@" 2>/dev/null; }

code=$(status "$BASE/api/v1/health")
[ "$code" = 200 ] && pass "liveness /api/v1/health -> 200" || fail "liveness /api/v1/health -> $code"

code=$(status "$BASE/api/v1/health/ready")
[ "$code" = 200 ] && pass "readiness -> 200" || fail "readiness -> $code (see the body for the failing component)"

code=$(status "$BASE/login")
[ "$code" = 200 ] && pass "sign-in page -> 200" || fail "sign-in page -> $code"

code=$(status "$BASE/api/v1/shipments")
[ "$code" = 401 ] && pass "data routes require authentication (401)" || fail "GET /api/v1/shipments without credentials -> $code, expected 401"

code=$(status "$BASE/metrics")
case "$code" in
  200) fail "/metrics is publicly reachable" ;;
  000) fail "/metrics check could not connect" ;;
  *) pass "/metrics is not exposed ($code)" ;;
esac

case "$BASE" in
  https://*)
    plain="http://${BASE#https://}"
    loc=$($CURL -o /dev/null -w '%{http_code} %{redirect_url}' "$plain/login" 2>/dev/null)
    case "$loc" in
      30[18]\ https://*) pass "HTTP redirects to HTTPS" ;;
      *) fail "HTTP does not redirect to HTTPS ($loc)" ;;
    esac
    if [ "${VERIFY_INSECURE:-0}" != 1 ]; then
      if $CURL -o /dev/null "$BASE/login" 2>/dev/null; then pass "certificate is trusted"
      else fail "TLS certificate is not trusted"; fi
    fi ;;
  *) fail "base URL is not https" ;;
esac

headers=$($CURL -D - -o /dev/null "$BASE/login" 2>/dev/null | tr -d '\r' | tr '[:upper:]' '[:lower:]')
for h in strict-transport-security x-content-type-options x-frame-options referrer-policy; do
  printf '%s\n' "$headers" | grep -q "^$h:" && pass "header $h" || fail "missing header $h"
done
printf '%s\n' "$headers" | grep -q '^server:' && fail "Server header is exposed" || pass "no Server header"

# A spoofed X-Forwarded-For must not change which rate-limit bucket or audit IP is used; that
# is checked in the drill (docs/operations/deployment.md). Here: the API answers through Caddy.
body=$($CURL -X POST -H 'Content-Type: application/json' -H 'X-Requested-With: chainsight' \
  -d '{"email":"nobody@invalid.test","password":"wrong-password-123"}' "$BASE/api/v1/auth/login" 2>/dev/null)
case "$body" in
  *invalid_credentials* | *rate_limited*) pass "sign-in endpoint answers with the generic error" ;;
  *) fail "unexpected sign-in response: $(printf '%s' "$body" | head -c 200)" ;;
esac

printf '\n%s failure(s)\n' "$fails"
[ "$fails" -eq 0 ]
