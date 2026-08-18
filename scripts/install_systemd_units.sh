#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOH'
Usage: install_systemd_units.sh [--preflight] [--help]

Render and install the evander systemd unit files, then enable them.

Options:
  --preflight  Run validation checks only (non-destructive).
  -h, --help   Show this help text.

Requirements:
  Run install mode with sudo.
EOH
}

MODE="install"
SERVICE_PROFILE="${EVANDER_SERVICE_PROFILE:-full}"
SETUP_EXECUTION_BACKEND="${EVANDER_SETUP_EXECUTION_BACKEND:-}"
SETUP_EXECUTION_MODEL="${EVANDER_SETUP_EXECUTION_MODEL:-}"

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
if [[ -n "${SUDO_USER:-}" ]]; then
  RUN_USER="${SUDO_USER}"
elif [[ "$(id -u)" -eq 0 ]]; then
  RUN_USER="$(stat -c '%U' "${ROOT_DIR}")"
else
  RUN_USER="${USER}"
fi
GIT_SERVICE_USER="${EVANDER_GIT_USER:-${RUN_USER}}"
GIT_SERVICE_GROUP="$(id -gn "${GIT_SERVICE_USER}")"
PROJECT_DIR="${ROOT_DIR}"
SYSTEMD_DIR="/etc/systemd/system"
SECRETS_FILE="${ROOT_DIR}/secrets.env"
LOCAL_SECRETS_FILE="/etc/evander/local-secrets.env"

EVANDER_UNITS=(
  evander-gateway.service
  evander-admin-web.service
  evander-openai-relay.service
  evander-didww-sip-bridge.service
  evander-firewall.service
  evander-nuget-relay.service
  evander-npm-audit-facade.service
  evander-http-target-relay.service
  evander-ado-worker.service
  evander-proxy-issue-sync.service
  evander-discord-ingest.service
  evander-discord-wake.service
  evander-scheduled-wake.service
  evander-managed-git-refresh.service
  evander-teams-ingest.service
  evander-teams-meeting-ingest.service
  evander-effect-orchestrator.service
  evander-trigger-orchestrator.service
)

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

admin_frontend_assets_ready() {
  python3 - "${ROOT_DIR}/frontend/admin-web/dist" <<'PY'
import re
import sys
from pathlib import Path

dist_dir = Path(sys.argv[1])
index_path = dist_dir / "index.html"
if not index_path.is_file():
    print(index_path)
    raise SystemExit(1)

html = index_path.read_text(encoding="utf-8")
references = [
    match.group("path").split("?", 1)[0].split("#", 1)[0].lstrip("/")
    for match in re.finditer(r'''(?:src|href)=["'](?P<path>/?assets/[^"']+)["']''', html)
]
if not references:
    print(dist_dir / "assets" / "*.js")
    raise SystemExit(1)

missing = [dist_dir / reference for reference in references if not (dist_dir / reference).is_file()]
if missing:
    print("\n".join(str(path) for path in missing))
    raise SystemExit(1)
PY
}

check_admin_frontend_assets() {
  local missing
  if missing="$(admin_frontend_assets_ready)"; then
    report_pass "Admin Web frontend assets are built."
    return
  fi

  if command -v npm >/dev/null 2>&1; then
    report_pass "Admin Web frontend assets are missing or stale; npm is available to build them during guided setup launch."
  elif [[ "${SERVICE_PROFILE}" == "setup" ]] && command -v apt-get >/dev/null 2>&1 && command -v apt-cache >/dev/null 2>&1 && { [[ "${EUID}" -eq 0 ]] || command -v sudo >/dev/null 2>&1; }; then
    local nodejs_version
    local npm_version
    nodejs_version="$(apt_package_candidate nodejs)"
    npm_version="$(apt_package_candidate npm)"
    if [[ -n "${nodejs_version}" && "${nodejs_version}" != "(none)" && -n "${npm_version}" && "${npm_version}" != "(none)" ]]; then
      report_pass "Admin Web frontend assets are missing or stale; guided setup can install Node.js ${nodejs_version} and npm ${npm_version} during launch after confirmation."
    else
      report_fail \
        "Admin Web frontend assets are missing or stale and npm is not installed." \
        "No apt candidate was found for Node.js/npm. Install Node.js/npm and build frontend/admin-web manually. Missing assets: ${missing}" \
        "critical"
    fi
  else
    report_fail \
      "Admin Web frontend assets are missing or stale and npm is not installed." \
      "Install Node.js/npm and build frontend/admin-web, or run guided setup on an apt-based host with sudo available so it can ask before installing them. Missing assets: ${missing}" \
      "critical"
  fi
}

apt_package_candidate() {
  apt-cache policy "$1" | awk '/Candidate:/ {print $2; exit}'
}

run_preflight_checks() {
  echo "Running install_systemd_units preflight checks..."
  echo "Service profile: ${SERVICE_PROFILE}"

  source_optional_env_file "${SECRETS_FILE}"

  check_command "systemctl" "Install systemd (systemctl) before installing units."
  check_command "python3" "Install Python 3 so the gateway/relay and env checks can run."
  if [[ "${SERVICE_PROFILE}" == "full" ]]; then
    check_command "virsh" "Install libvirt-clients so VM lifecycle commands are available." "warning"
  elif [[ "${SERVICE_PROFILE}" == "setup" && "${SETUP_EXECUTION_BACKEND}" == "libvirt_vm" ]]; then
    check_command "virsh" "Install libvirt-clients so VM lifecycle commands are available."
  else
    report_pass "Skipping virsh check for service profile ${SERVICE_PROFILE}."
  fi
  if [[ "${SERVICE_PROFILE}" == "setup" && "${SETUP_EXECUTION_MODEL}" == "container_same_host" ]]; then
    check_command "docker" "Install Docker so the same-host container worker can run agent containers."
  fi
  check_admin_frontend_assets

  local setup_managed_config_severity="critical"
  if [[ "${SERVICE_PROFILE}" == "setup" ]]; then
    setup_managed_config_severity="warning"
  fi

  check_file "${ROOT_DIR}/config/firewall.env" "Copy config/firewall.env.example to config/firewall.env."
  check_file \
    "${ROOT_DIR}/config/ado-worker/config.json" \
    "Save the ADO worker global settings in the Admin GUI or copy config/ado-worker/config.json.example to config/ado-worker/config.json." \
    "${setup_managed_config_severity}"
  check_file \
    "${ROOT_DIR}/runtime/directory-config/root/ado-worker.json" \
    "Run evander-cli migrate-ado-worker-env-to-root or configure the directory after deployment." \
    "warning"
  check_file \
    "${ROOT_DIR}/config/trigger-orchestrator/config.json" \
    "Copy config/trigger-orchestrator/config-example.json to config/trigger-orchestrator/config.json and copy the workflow pack examples it references to operator-local pack files." \
    "${setup_managed_config_severity}"
  check_file \
    "${ROOT_DIR}/config/teams-meeting-ingest/config.json" \
    "Copy config/teams-meeting-ingest/config.json.example to config/teams-meeting-ingest/config.json if you want to override Teams meeting ingest defaults." \
    "warning"
  check_file \
    "${ROOT_DIR}/config/didww-sip-bridge.env" \
    "Copy config/didww-sip-bridge.env.example to config/didww-sip-bridge.env if you want OpenAI Realtime/DIDWW phone calls." \
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
    -e "s|__GIT_USER__|${GIT_SERVICE_USER}|g" \
    -e "s|__GIT_GROUP__|${GIT_SERVICE_GROUP}|g" \
    "${template}" > "${target}"
}

grant_local_secrets_git_access() {
  local secrets_file="$1"
  local git_user="${EVANDER_GIT_USER:-${RUN_USER}}"
  local secrets_dir git_group

  [[ -f "${secrets_file}" ]] || return 0
  [[ -n "${git_user}" ]] || return 0
  if ! id "${git_user}" >/dev/null 2>&1; then
    echo "Warning: cannot grant Git SSH access to ${secrets_file}; user ${git_user} does not exist." >&2
    return 0
  fi

  secrets_dir="$(dirname "${secrets_file}")"
  if command -v setfacl >/dev/null 2>&1; then
    setfacl -m "u:${git_user}:--x" "${secrets_dir}"
    setfacl -m "u:${git_user}:r--" "${secrets_file}"
    return 0
  fi

  git_group="$(id -gn "${git_user}")"
  chgrp "${git_group}" "${secrets_dir}" "${secrets_file}"
  chmod 0710 "${secrets_dir}"
  chmod 0640 "${secrets_file}"
}

configure_local_secrets() {
  if [[ ! -f "${LOCAL_SECRETS_FILE}" ]]; then
    return
  fi

  grant_local_secrets_git_access "${LOCAL_SECRETS_FILE}"

  local unit dropin_dir dropin_path
  for unit in "${EVANDER_UNITS[@]}"; do
    dropin_dir="${SYSTEMD_DIR}/${unit}.d"
    dropin_path="${dropin_dir}/10-local-secrets.conf"
    install -d -m 0755 "${dropin_dir}"
    printf '[Service]\nEnvironment=EVANDER_SECRETS_ENV_FILE=%s\nEnvironmentFile=-%s\n' \
      "${LOCAL_SECRETS_FILE}" \
      "${LOCAL_SECRETS_FILE}" > "${dropin_path}"
  done
}

configure_local_secrets

render_unit \
  "${ROOT_DIR}/systemd/evander-gateway.service.template" \
  "${SYSTEMD_DIR}/evander-gateway.service"

render_unit \
  "${ROOT_DIR}/systemd/evander-admin-web.service.template" \
  "${SYSTEMD_DIR}/evander-admin-web.service"

render_unit \
  "${ROOT_DIR}/systemd/evander-openai-relay.service.template" \
  "${SYSTEMD_DIR}/evander-openai-relay.service"

render_unit \
  "${ROOT_DIR}/systemd/evander-didww-sip-bridge.service.template" \
  "${SYSTEMD_DIR}/evander-didww-sip-bridge.service"

render_unit \
  "${ROOT_DIR}/systemd/evander-firewall.service.template" \
  "${SYSTEMD_DIR}/evander-firewall.service"

render_unit \
  "${ROOT_DIR}/systemd/evander-nuget-relay.service.template" \
  "${SYSTEMD_DIR}/evander-nuget-relay.service"

render_unit \
  "${ROOT_DIR}/systemd/evander-npm-audit-facade.service.template" \
  "${SYSTEMD_DIR}/evander-npm-audit-facade.service"

render_unit \
  "${ROOT_DIR}/systemd/evander-http-target-relay.service.template" \
  "${SYSTEMD_DIR}/evander-http-target-relay.service"

render_unit \
  "${ROOT_DIR}/systemd/evander-ado-worker.service.template" \
  "${SYSTEMD_DIR}/evander-ado-worker.service"

render_unit \
  "${ROOT_DIR}/systemd/evander-proxy-issue-sync.service.template" \
  "${SYSTEMD_DIR}/evander-proxy-issue-sync.service"

render_unit \
  "${ROOT_DIR}/systemd/evander-discord-ingest.service.template" \
  "${SYSTEMD_DIR}/evander-discord-ingest.service"

render_unit \
  "${ROOT_DIR}/systemd/evander-discord-wake.service.template" \
  "${SYSTEMD_DIR}/evander-discord-wake.service"

render_unit \
  "${ROOT_DIR}/systemd/evander-scheduled-wake.service.template" \
  "${SYSTEMD_DIR}/evander-scheduled-wake.service"

render_unit \
  "${ROOT_DIR}/systemd/evander-managed-git-refresh.service.template" \
  "${SYSTEMD_DIR}/evander-managed-git-refresh.service"

render_unit \
  "${ROOT_DIR}/systemd/evander-teams-ingest.service.template" \
  "${SYSTEMD_DIR}/evander-teams-ingest.service"

render_unit \
  "${ROOT_DIR}/systemd/evander-teams-meeting-ingest.service.template" \
  "${SYSTEMD_DIR}/evander-teams-meeting-ingest.service"

render_unit \
  "${ROOT_DIR}/systemd/evander-effect-orchestrator.service.template" \
  "${SYSTEMD_DIR}/evander-effect-orchestrator.service"

render_unit \
  "${ROOT_DIR}/systemd/evander-trigger-orchestrator.service.template" \
  "${SYSTEMD_DIR}/evander-trigger-orchestrator.service"

systemctl daemon-reload
systemctl enable "${EVANDER_UNITS[@]}"

cat <<EOF
Installed systemd units:
  evander-gateway.service
  evander-admin-web.service
  evander-openai-relay.service
  evander-didww-sip-bridge.service
  evander-firewall.service
  evander-nuget-relay.service
  evander-npm-audit-facade.service
  evander-http-target-relay.service
  evander-ado-worker.service
  evander-proxy-issue-sync.service
  evander-discord-ingest.service
  evander-discord-wake.service
  evander-scheduled-wake.service
  evander-managed-git-refresh.service
  evander-teams-ingest.service
  evander-teams-meeting-ingest.service
  evander-effect-orchestrator.service
  evander-trigger-orchestrator.service

Next steps:
  1. Populate ${ROOT_DIR}/runtime/directory-config/root/ado-worker.json via the root directory config migration commands.
  2. Populate ${ROOT_DIR}/config/ado-worker/config.json via the Admin GUI if you want to override worker defaults.
  3. Populate ${ROOT_DIR}/config/teams-ingest/config.json from the example file if you want to override Teams ingest defaults.
  4. Populate ${ROOT_DIR}/config/teams-meeting-ingest/config.json from the example file if you want to override Teams meeting ingest defaults.
  5. Populate ${ROOT_DIR}/config/trigger-orchestrator/config.json from the example file if you want to enable the trigger orchestrator scaffold.
  6. Populate ${ROOT_DIR}/config/didww-sip-bridge.env from the example file if you want OpenAI Realtime/DIDWW phone calls.
  7. Export the required host secrets in the service manager environment, source ${ROOT_DIR}/secrets.env if you keep one there, or put host-local secrets in ${LOCAL_SECRETS_FILE}.
  8. Populate ${ROOT_DIR}/config/admin-web.env from the example file and keep Admin Web password/session secrets in ${LOCAL_SECRETS_FILE} or another service-manager secret source.
  9. Optionally edit ${ROOT_DIR}/config/firewall.env to change allowed host ports.
  10. Start services:
     sudo systemctl start evander-admin-web.service
     sudo systemctl start evander-firewall.service
     sudo systemctl start evander-gateway.service
     sudo systemctl start evander-openai-relay.service
     sudo systemctl start evander-didww-sip-bridge.service
     sudo systemctl start evander-nuget-relay.service
     sudo systemctl start evander-npm-audit-facade.service
     sudo systemctl start evander-http-target-relay.service
     sudo systemctl start evander-ado-worker.service
     sudo systemctl start evander-proxy-issue-sync.service
     sudo systemctl start evander-discord-ingest.service
     sudo systemctl start evander-discord-wake.service
     sudo systemctl start evander-scheduled-wake.service
     sudo systemctl start evander-managed-git-refresh.service
     sudo systemctl start evander-teams-ingest.service
     sudo systemctl start evander-teams-meeting-ingest.service
     sudo systemctl start evander-effect-orchestrator.service
     sudo systemctl start evander-trigger-orchestrator.service
EOF
