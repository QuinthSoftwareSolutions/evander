#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIG_PATH="${AGENT_SANDBOX_CONFIG:-config/gateway.yaml}"
LOG_LEVEL="${HOST_HEARTBEAT_LOG_LEVEL:-INFO}"
TMP_CONFIG="$(mktemp)"
cleanup() {
  rm -f "${TMP_CONFIG}"
}
trap cleanup EXIT

if [[ ! -f "${CONFIG_PATH}" ]]; then
  echo "Host heartbeat config not found: ${CONFIG_PATH}" >&2
  exit 1
fi

python3 - "${CONFIG_PATH}" "${TMP_CONFIG}" <<'PY'
from __future__ import annotations

import os
import sys

import yaml

config_path, output_path = sys.argv[1], sys.argv[2]
with open(config_path, encoding="utf-8") as handle:
    data = yaml.safe_load(handle) or {}

float_overrides = {
    "HOST_HEARTBEAT_POLL_INTERVAL_SECONDS": "host_heartbeat_poll_interval_seconds",
    "HOST_HEARTBEAT_JITTER_SECONDS": "host_heartbeat_jitter_seconds",
    "HOST_HEARTBEAT_COOLDOWN_SECONDS": "host_heartbeat_cooldown_seconds",
    "HOST_HEARTBEAT_HTTP_TIMEOUT_SECONDS": "host_heartbeat_http_timeout_seconds",
}
string_overrides = {
    "HOST_HEARTBEAT_LOCK_PATH": "host_heartbeat_lock_path",
    "HOST_HEARTBEAT_SESSION_COOLDOWN_PATH": "host_heartbeat_session_cooldown_path",
    "HOST_HEARTBEAT_STREAM_CONFLICT_POLICY": "host_heartbeat_stream_conflict_policy",
}

for env_name, field_name in float_overrides.items():
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        continue
    data[field_name] = float(raw)

for env_name, field_name in string_overrides.items():
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        continue
    data[field_name] = raw

with open(output_path, "w", encoding="utf-8") as handle:
    yaml.safe_dump(data, handle, sort_keys=False)
PY

exec "${ROOT_DIR}/.venv/bin/agent-sandbox-cli" host-heartbeat \
  --config "${TMP_CONFIG}" \
  --log-level "${LOG_LEVEL}"
