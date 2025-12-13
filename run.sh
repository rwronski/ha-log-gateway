#!/usr/bin/with-contenv bashio
set -euo pipefail

API_TOKEN="$(bashio::config 'api_token')"
Z2M_SLUG="$(bashio::config 'z2m_slug')"
LINES_DEFAULT="$(bashio::config 'lines_default')"
LINES_MAX="$(bashio::config 'lines_max')"
NO_COLORS="$(bashio::config 'no_colors')"

if bashio::var.is_empty "${API_TOKEN}"; then
  bashio::log.fatal "Option 'api_token' is required."
  exit 1
fi

export LOGGW_TOKEN="${API_TOKEN}"
export LOGGW_Z2M_SLUG="${Z2M_SLUG:-45df7312_zigbee2mqtt}"
export LOGGW_LINES_DEFAULT="${LINES_DEFAULT:-1000}"
export LOGGW_LINES_MAX="${LINES_MAX:-1000}"
export LOGGW_NO_COLORS="${NO_COLORS:-true}"

bashio::log.info "Starting log gateway on 0.0.0.0:8099 (snapshot only)."
exec uvicorn app.main:app --host 0.0.0.0 --port 8099 --proxy-headers
