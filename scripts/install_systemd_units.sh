#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOH'
Usage: install_systemd_units.sh [--preflight] [--help]

Render and install the agent-sandbox systemd unit files, then enable them.

Options:
  --preflight  Run validation checks only (non-destructive).
  -h, --help   Show this help text.

Requirements:
  Run install mode with sudo.
EOH
}

MODE="install"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --preflight)
      MODE="preflight"
      shift
      ;;
    -h|--help)
      show_help
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      show_help >&2
      exit 1
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUN_USER="${SUDO_USER:-$USER}"
PROJECT_DIR="${ROOT_DIR}"
SYSTEMD_DIR="/etc/systemd/system"

PASS_COUNT=0
FAIL_COUNT=0
HAS_CRITICAL_FAIL=0

report_pass() {
  local message="$1"
  echo "[PASS] ${message}"
  PASS_COUNT=$((PASS_COUNT + 1))
}

report_fail() {
  local message="$1"
  local hint="$2"
  local severity="${3:-critical}"

  echo "[FAIL] ${message}"
  echo "       hint: ${hint}"
  FAIL_COUNT=$((FAIL_COUNT + 1))
  if [[ "${severity}" == "critical" ]]; then
    HAS_CRITICAL_FAIL=1
  fi
}

check_command() {
  local binary="$1"
  local required_hint="$2"
  local severity="${3:-critical}"

  if command -v "${binary}" >/dev/null 2>&1; then
    report_pass "Found required binary '${binary}'."
  else
    report_fail "Missing binary '${binary}'." "${required_hint}" "${severity}"
  fi
}

check_file() {
  local file_path="$1"
  local hint="$2"

  if [[ -f "${file_path}" ]]; then
    report_pass "Found config file ${file_path}."
  else
    report_fail "Missing config file ${file_path}." "${hint}" "critical"
  fi
}

check_env_var_in_file() {
  local file_path="$1"
  local var_name="$2"
  local hint="$3"
  local severity="${4:-critical}"

  if [[ ! -f "${file_path}" ]]; then
    return
  fi

  local value
  value="$(python3 - "$file_path" "$var_name" <<'PY'
import re
import sys

file_path, var_name = sys.argv[1], sys.argv[2]
pattern = re.compile(r"^\s*" + re.escape(var_name) + r"\s*=\s*(.*)$")

with open(file_path, encoding="utf-8") as handle:
    for raw in handle:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = pattern.match(line)
        if not match:
            continue
        value = match.group(1).strip()
        if len(value) >= 2 and ((value[0] == value[-1]) and value[0] in {'"', "'"}):
            value = value[1:-1]
        print(value)
        raise SystemExit(0)

raise SystemExit(1)
PY
)" || true

  if [[ -n "${value}" ]]; then
    report_pass "${var_name} is set in ${file_path}."
  else
    report_fail "${var_name} is missing or empty in ${file_path}." "${hint}" "${severity}"
  fi
}

env_var_is_truthy_in_file() {
  local file_path="$1"
  local var_name="$2"

  if [[ ! -f "${file_path}" ]]; then
    return 1
  fi

  local value
  value="$(python3 - "$file_path" "$var_name" <<'PY'
import re
import sys

file_path, var_name = sys.argv[1], sys.argv[2]
pattern = re.compile(r"^\s*" + re.escape(var_name) + r"\s*=\s*(.*)$")

with open(file_path, encoding="utf-8") as handle:
    for raw in handle:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = pattern.match(line)
        if not match:
            continue
        value = match.group(1).strip()
        if len(value) >= 2 and ((value[0] == value[-1]) and value[0] in {'"', "'"}):
            value = value[1:-1]
        normalized = value.strip().lower()
        print("true" if normalized in {"1", "true", "yes", "on"} else "false")
        raise SystemExit(0)

print("false")
PY
)"

  [[ "${value}" == "true" ]]
}

check_any_env_var_in_file() {
  local file_path="$1"
  local hint="$2"
  local severity="${3:-critical}"
  shift 3

  local var_name
  for var_name in "$@"; do
    local value
    value="$(python3 - "$file_path" "$var_name" <<'PY'
import re
import sys

file_path, var_name = sys.argv[1], sys.argv[2]
pattern = re.compile(r"^\s*" + re.escape(var_name) + r"\s*=\s*(.*)$")

with open(file_path, encoding="utf-8") as handle:
    for raw in handle:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = pattern.match(line)
        if not match:
            continue
        value = match.group(1).strip()
        if len(value) >= 2 and ((value[0] == value[-1]) and value[0] in {'"', "'"}):
            value = value[1:-1]
        print(value)
        raise SystemExit(0)

raise SystemExit(1)
PY
)" || true

    if [[ -n "${value}" ]]; then
      report_pass "${var_name} is set in ${file_path}."
      return 0
    fi
  done

  report_fail "$(printf "%s or %s is missing or empty in %s." "$1" "$2" "${file_path}")" "${hint}" "${severity}"
}

run_preflight_checks() {
  echo "Running install_systemd_units preflight checks..."

  check_command "systemctl" "Install systemd (systemctl) before installing units."
  check_command "python3" "Install Python 3 so the gateway/relay and env checks can run."
  check_command "virsh" "Install libvirt-clients so VM lifecycle commands are available." "warning"

  check_file "${ROOT_DIR}/config/gateway.env" "Copy config/gateway.env.example to config/gateway.env and update values."
  check_file "${ROOT_DIR}/config/openai-relay.env" "Copy config/openai-relay.env.example to config/openai-relay.env and set OPENAI_API_KEY."
  check_file "${ROOT_DIR}/config/firewall.env" "Copy config/firewall.env.example to config/firewall.env."
  check_file "${ROOT_DIR}/config/nuget-relay.env" "Copy config/nuget-relay.env.example to config/nuget-relay.env."
  check_file "${ROOT_DIR}/config/ado-worker.env" "Copy config/ado-worker.env.example to config/ado-worker.env and configure required values."
  check_file "${ROOT_DIR}/config/host-heartbeat.env" "Copy config/host-heartbeat.env.example to config/host-heartbeat.env."

  check_env_var_in_file "${ROOT_DIR}/config/openai-relay.env" "OPENAI_API_KEY" "Set OPENAI_API_KEY in config/openai-relay.env."
  check_env_var_in_file "${ROOT_DIR}/config/firewall.env" "ALLOWED_TCP_PORTS" "Set ALLOWED_TCP_PORTS in config/firewall.env."
  check_env_var_in_file "${ROOT_DIR}/config/ado-worker.env" "ADO_PROJECT_OR_SOLUTION" "Set ADO_PROJECT_OR_SOLUTION in config/ado-worker.env."
  if env_var_is_truthy_in_file "${ROOT_DIR}/config/ado-worker.env" "ADO_BOOTSTRAP_MSSQL"; then
    check_any_env_var_in_file \
      "${ROOT_DIR}/config/ado-worker.env" \
      "Set ADO_MSSQL_SA_PASSWORD or MSSQL_SA_PASSWORD in config/ado-worker.env when ADO_BOOTSTRAP_MSSQL=true." \
      "critical" \
      "ADO_MSSQL_SA_PASSWORD" \
      "MSSQL_SA_PASSWORD"
  fi
  check_env_var_in_file "${ROOT_DIR}/config/host-heartbeat.env" "HOST_HEARTBEAT_LOG_LEVEL" "Set HOST_HEARTBEAT_LOG_LEVEL in config/host-heartbeat.env."

  echo
  echo "Preflight summary: ${PASS_COUNT} passed, ${FAIL_COUNT} failed."

  if [[ "${HAS_CRITICAL_FAIL}" -ne 0 ]]; then
    echo "Result: FAIL (critical checks failed)."
    return 1
  fi

  if [[ "${FAIL_COUNT}" -ne 0 ]]; then
    echo "Result: PASS with warnings (non-critical checks failed)."
  else
    echo "Result: PASS"
  fi
}

if [[ "${MODE}" == "preflight" ]]; then
  run_preflight_checks
  exit $?
fi

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo." >&2
  exit 1
fi

run_preflight_checks

if [[ "${HAS_CRITICAL_FAIL}" -ne 0 ]]; then
  echo "Aborting install because preflight checks failed." >&2
  exit 1
fi

render_unit() {
  local template="$1"
  local target="$2"
  sed \
    -e "s|__PROJECT_DIR__|${PROJECT_DIR}|g" \
    -e "s|__RUN_USER__|${RUN_USER}|g" \
    "${template}" > "${target}"
}

render_unit \
  "${ROOT_DIR}/systemd/agent-sandbox-gateway.service.template" \
  "${SYSTEMD_DIR}/agent-sandbox-gateway.service"

render_unit \
  "${ROOT_DIR}/systemd/agent-sandbox-openai-relay.service.template" \
  "${SYSTEMD_DIR}/agent-sandbox-openai-relay.service"

render_unit \
  "${ROOT_DIR}/systemd/agent-sandbox-firewall.service.template" \
  "${SYSTEMD_DIR}/agent-sandbox-firewall.service"

render_unit \
  "${ROOT_DIR}/systemd/agent-sandbox-nuget-relay.service.template" \
  "${SYSTEMD_DIR}/agent-sandbox-nuget-relay.service"

render_unit \
  "${ROOT_DIR}/systemd/agent-sandbox-ado-worker.service.template" \
  "${SYSTEMD_DIR}/agent-sandbox-ado-worker.service"

render_unit \
  "${ROOT_DIR}/systemd/agent-sandbox-host-heartbeat.service.template" \
  "${SYSTEMD_DIR}/agent-sandbox-host-heartbeat.service"

systemctl daemon-reload
systemctl enable agent-sandbox-gateway.service
systemctl enable agent-sandbox-openai-relay.service
systemctl enable agent-sandbox-firewall.service
systemctl enable agent-sandbox-nuget-relay.service
systemctl enable agent-sandbox-ado-worker.service
systemctl enable agent-sandbox-host-heartbeat.service

cat <<EOF
Installed systemd units:
  agent-sandbox-gateway.service
  agent-sandbox-openai-relay.service
  agent-sandbox-firewall.service
  agent-sandbox-nuget-relay.service
  agent-sandbox-ado-worker.service
  agent-sandbox-host-heartbeat.service

Next steps:
  1. Edit ${ROOT_DIR}/config/openai-relay.env and set OPENAI_API_KEY.
  2. Edit ${ROOT_DIR}/config/nuget-relay.env and set AZURE_ARTIFACTS_PAT if needed.
  3. Edit ${ROOT_DIR}/config/ado-worker.env and ensure the PAT env var named by config/gateway.yaml is exported.
  4. Edit ${ROOT_DIR}/config/host-heartbeat.env for log level and provider credentials if private PR polling is needed.
  5. Optionally edit ${ROOT_DIR}/config/firewall.env to change allowed host ports.
  6. Start services:
     sudo systemctl start agent-sandbox-firewall.service
     sudo systemctl start agent-sandbox-gateway.service
     sudo systemctl start agent-sandbox-openai-relay.service
     sudo systemctl start agent-sandbox-nuget-relay.service
     sudo systemctl start agent-sandbox-ado-worker.service
     sudo systemctl start agent-sandbox-host-heartbeat.service
EOF
