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
SERVICE_PROFILE="${AGENT_SANDBOX_SERVICE_PROFILE:-full}"

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
SECRETS_FILE="${ROOT_DIR}/secrets.env"

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
  local severity="${3:-critical}"

  if [[ -f "${file_path}" ]]; then
    report_pass "Found config file ${file_path}."
  else
    report_fail "Missing config file ${file_path}." "${hint}" "${severity}"
  fi
}

source_optional_env_file() {
  local file_path="$1"

  if [[ -f "${file_path}" ]]; then
    set -a
    # shellcheck disable=SC1090
    . "${file_path}"
    set +a
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
  echo "Service profile: ${SERVICE_PROFILE}"

  source_optional_env_file "${SECRETS_FILE}"

  check_command "systemctl" "Install systemd (systemctl) before installing units."
  check_command "python3" "Install Python 3 so the gateway/relay and env checks can run."
  if [[ "${SERVICE_PROFILE}" == "full" ]]; then
    check_command "virsh" "Install libvirt-clients so VM lifecycle commands are available." "warning"
  else
    report_pass "Skipping virsh check for service profile ${SERVICE_PROFILE}."
  fi

  check_file "${ROOT_DIR}/config/firewall.env" "Copy config/firewall.env.example to config/firewall.env."
  check_file \
    "${ROOT_DIR}/config/ado-worker/config.json" \
    "Save the ADO worker global settings in the Admin GUI or copy config/ado-worker/config.json.example to config/ado-worker/config.json."
  check_file \
    "${ROOT_DIR}/runtime/directory-config/root/ado-worker.json" \
    "Run agent-sandbox-cli migrate-ado-worker-env-to-root, migrate-global-config-to-root, or configure the directory after deployment." \
    "warning"
  check_file \
    "${ROOT_DIR}/config/trigger-orchestrator/config.json" \
    "Copy config/trigger-orchestrator/config-example.json to config/trigger-orchestrator/config.json and copy the workflow pack examples it references to operator-local pack files."
  check_file \
    "${ROOT_DIR}/config/teams-meeting-ingest/config.json" \
    "Copy config/teams-meeting-ingest/config.json.example to config/teams-meeting-ingest/config.json if you want to override Teams meeting ingest defaults." \
    "warning"

  check_env_var_in_file "${ROOT_DIR}/config/firewall.env" "ALLOWED_TCP_PORTS" "Set ALLOWED_TCP_PORTS in config/firewall.env."

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
  "${ROOT_DIR}/systemd/agent-sandbox-admin-web.service.template" \
  "${SYSTEMD_DIR}/agent-sandbox-admin-web.service"

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
  "${ROOT_DIR}/systemd/agent-sandbox-http-target-relay.service.template" \
  "${SYSTEMD_DIR}/agent-sandbox-http-target-relay.service"

render_unit \
  "${ROOT_DIR}/systemd/agent-sandbox-ado-worker.service.template" \
  "${SYSTEMD_DIR}/agent-sandbox-ado-worker.service"

render_unit \
  "${ROOT_DIR}/systemd/agent-sandbox-proxy-issue-sync.service.template" \
  "${SYSTEMD_DIR}/agent-sandbox-proxy-issue-sync.service"

render_unit \
  "${ROOT_DIR}/systemd/agent-sandbox-discord-ingest.service.template" \
  "${SYSTEMD_DIR}/agent-sandbox-discord-ingest.service"

render_unit \
  "${ROOT_DIR}/systemd/agent-sandbox-discord-wake.service.template" \
  "${SYSTEMD_DIR}/agent-sandbox-discord-wake.service"

render_unit \
  "${ROOT_DIR}/systemd/agent-sandbox-scheduled-wake.service.template" \
  "${SYSTEMD_DIR}/agent-sandbox-scheduled-wake.service"

render_unit \
  "${ROOT_DIR}/systemd/agent-sandbox-teams-ingest.service.template" \
  "${SYSTEMD_DIR}/agent-sandbox-teams-ingest.service"

render_unit \
  "${ROOT_DIR}/systemd/agent-sandbox-teams-meeting-ingest.service.template" \
  "${SYSTEMD_DIR}/agent-sandbox-teams-meeting-ingest.service"

render_unit \
  "${ROOT_DIR}/systemd/agent-sandbox-effect-orchestrator.service.template" \
  "${SYSTEMD_DIR}/agent-sandbox-effect-orchestrator.service"

render_unit \
  "${ROOT_DIR}/systemd/agent-sandbox-trigger-orchestrator.service.template" \
  "${SYSTEMD_DIR}/agent-sandbox-trigger-orchestrator.service"

systemctl daemon-reload
systemctl enable agent-sandbox-gateway.service
systemctl enable agent-sandbox-admin-web.service
systemctl enable agent-sandbox-openai-relay.service
systemctl enable agent-sandbox-firewall.service
systemctl enable agent-sandbox-nuget-relay.service
systemctl enable agent-sandbox-http-target-relay.service
systemctl enable agent-sandbox-ado-worker.service
systemctl enable agent-sandbox-proxy-issue-sync.service
systemctl enable agent-sandbox-discord-ingest.service
systemctl enable agent-sandbox-discord-wake.service
systemctl enable agent-sandbox-scheduled-wake.service
systemctl enable agent-sandbox-teams-ingest.service
systemctl enable agent-sandbox-teams-meeting-ingest.service
systemctl enable agent-sandbox-effect-orchestrator.service
systemctl enable agent-sandbox-trigger-orchestrator.service

cat <<EOF
Installed systemd units:
  agent-sandbox-gateway.service
  agent-sandbox-admin-web.service
  agent-sandbox-openai-relay.service
  agent-sandbox-firewall.service
  agent-sandbox-nuget-relay.service
  agent-sandbox-http-target-relay.service
  agent-sandbox-ado-worker.service
  agent-sandbox-proxy-issue-sync.service
  agent-sandbox-discord-ingest.service
  agent-sandbox-discord-wake.service
  agent-sandbox-scheduled-wake.service
  agent-sandbox-teams-ingest.service
  agent-sandbox-teams-meeting-ingest.service
  agent-sandbox-effect-orchestrator.service
  agent-sandbox-trigger-orchestrator.service

Next steps:
  1. Populate ${ROOT_DIR}/runtime/directory-config/root/ado-worker.json via the root directory config migration commands.
  2. Populate ${ROOT_DIR}/config/ado-worker/config.json via the Admin GUI if you want to override worker defaults.
  3. Populate ${ROOT_DIR}/config/teams-ingest/config.json from the example file if you want to override Teams ingest defaults.
  4. Populate ${ROOT_DIR}/config/teams-meeting-ingest/config.json from the example file if you want to override Teams meeting ingest defaults.
  5. Populate ${ROOT_DIR}/config/trigger-orchestrator/config.json from the example file if you want to enable the trigger orchestrator scaffold.
  6. Export the required host secrets in the service manager environment, or source ${ROOT_DIR}/secrets.env if you keep one there.
  7. Populate ${ROOT_DIR}/config/admin-web.env from the example file and set host/auth settings for the admin UI.
  8. Optionally edit ${ROOT_DIR}/config/firewall.env to change allowed host ports.
  9. Start services:
     sudo systemctl start agent-sandbox-admin-web.service
     sudo systemctl start agent-sandbox-firewall.service
     sudo systemctl start agent-sandbox-gateway.service
     sudo systemctl start agent-sandbox-openai-relay.service
     sudo systemctl start agent-sandbox-nuget-relay.service
     sudo systemctl start agent-sandbox-http-target-relay.service
     sudo systemctl start agent-sandbox-ado-worker.service
     sudo systemctl start agent-sandbox-proxy-issue-sync.service
     sudo systemctl start agent-sandbox-discord-ingest.service
     sudo systemctl start agent-sandbox-discord-wake.service
     sudo systemctl start agent-sandbox-scheduled-wake.service
     sudo systemctl start agent-sandbox-teams-ingest.service
     sudo systemctl start agent-sandbox-teams-meeting-ingest.service
     sudo systemctl start agent-sandbox-effect-orchestrator.service
     sudo systemctl start agent-sandbox-trigger-orchestrator.service
EOF
