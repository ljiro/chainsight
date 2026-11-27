#!/bin/sh
# Starts Prometheus with infrastructure/monitoring/prometheus.yml. When METRICS_TOKEN is set,
# the API job gets the bearer token (Prometheus does not expand environment variables in its
# config, and the token should not be written into the repository).
set -eu
config=/etc/prometheus/prometheus.yml
if [ -n "${METRICS_TOKEN:-}" ]; then
  umask 077
  printf '%s' "$METRICS_TOKEN" > /prometheus/.metrics_token
  sed 's|^    metrics_path: /metrics$|    metrics_path: /metrics\n    authorization: { credentials_file: /prometheus/.metrics_token }|' \
    "$config" > /prometheus/.prometheus.yml
  config=/prometheus/.prometheus.yml
fi
exec /bin/prometheus --config.file="$config" --storage.tsdb.path=/prometheus "$@"
