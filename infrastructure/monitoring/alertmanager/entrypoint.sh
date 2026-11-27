#!/bin/sh
# Renders alertmanager.yml from the environment, then starts Alertmanager.
#
# Alertmanager does not expand environment variables in its config, and the destinations
# (webhook URLs, SMTP passwords) are secrets that do not belong in the repository. Each
# receiver below is added only when its variables are set; with none set, alerts are still
# visible in Prometheus and Alertmanager but go nowhere.
#
#   ALERT_WEBHOOK_URL                 generic JSON webhook (ntfy, n8n, an incident tool, ...)
#   ALERT_SLACK_WEBHOOK_URL           Slack incoming webhook
#   ALERT_EMAIL_TO + ALERT_SMTP_*     email (SMARTHOST host:port, FROM, USERNAME, PASSWORD)
#
# `render` as the first argument prints the config instead of starting (used by tests).
set -eu

say() { printf '%s\n' "$*"; }
q() { printf '"%s"' "$(printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g')"; }

render() {
  configured=0
  say "global:"
  say "  resolve_timeout: 5m"
  say "route:"
  say "  receiver: default"
  say "  group_by: [alertname, job]"
  say "  group_wait: 30s"
  say "  group_interval: 5m"
  say "  repeat_interval: 4h"
  say "  routes:"
  say "    - matchers: ['severity=\"critical\"']"
  say "      receiver: default"
  say "      repeat_interval: 1h"
  say "inhibit_rules:"
  say "  # A target that is down also trips its error-rate and staleness alerts."
  say "  - source_matchers: ['alertname=\"ChainSightTargetDown\"']"
  say "    target_matchers: ['severity=\"warning\"']"
  say "receivers:"
  say "  - name: default"
  if [ -n "${ALERT_WEBHOOK_URL:-}" ]; then
    say "    webhook_configs:"
    say "      - url: $(q "$ALERT_WEBHOOK_URL")"
    say "        send_resolved: true"
    configured=1
  fi
  if [ -n "${ALERT_SLACK_WEBHOOK_URL:-}" ]; then
    say "    slack_configs:"
    say "      - api_url: $(q "$ALERT_SLACK_WEBHOOK_URL")"
    say "        send_resolved: true"
    say "        title: '{{ .Status | toUpper }}: {{ .CommonLabels.alertname }}'"
    say "        text: '{{ range .Alerts }}{{ .Annotations.summary }}{{ \"\\n\" }}{{ end }}'"
    configured=1
  fi
  if [ -n "${ALERT_EMAIL_TO:-}" ]; then
    : "${ALERT_SMTP_SMARTHOST:?ALERT_EMAIL_TO needs ALERT_SMTP_SMARTHOST (host:port)}"
    : "${ALERT_SMTP_FROM:?ALERT_EMAIL_TO needs ALERT_SMTP_FROM}"
    say "    email_configs:"
    say "      - to: $(q "$ALERT_EMAIL_TO")"
    say "        from: $(q "$ALERT_SMTP_FROM")"
    say "        smarthost: $(q "$ALERT_SMTP_SMARTHOST")"
    if [ -n "${ALERT_SMTP_USERNAME:-}" ]; then
      say "        auth_username: $(q "$ALERT_SMTP_USERNAME")"
      say "        auth_password: $(q "${ALERT_SMTP_PASSWORD:-}")"
    fi
    say "        send_resolved: true"
    configured=1
  fi
  [ "$configured" = 1 ] || say "No alert receiver configured; alerts will not be delivered." >&2
}

if [ "${1:-}" = render ]; then
  render
  exit 0
fi

render > /tmp/alertmanager.yml
exec /bin/alertmanager --config.file=/tmp/alertmanager.yml --storage.path=/alertmanager "$@"
