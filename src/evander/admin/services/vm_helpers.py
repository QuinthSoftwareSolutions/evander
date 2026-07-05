from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from datetime import datetime
from pathlib import Path

from evander.admin.services import process_helpers


_ENVIRONMENT_VARIABLE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_environment_variable_name(name: object) -> str:
    normalized = str(name)
    if not _ENVIRONMENT_VARIABLE_NAME_PATTERN.fullmatch(normalized):
        raise ValueError(
            "Invalid environment variable name "
            f"{normalized!r}; expected ^[A-Za-z_][A-Za-z0-9_]*$"
        )
    return normalized


def caller_home() -> Path:
    caller_user = os.environ.get("SUDO_USER") or os.environ.get("USER") or ""
    caller_home = os.environ.get("HOME", "")
    if caller_user:
        try:
            passwd_entry = subprocess.run(
                ["getent", "passwd", caller_user],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            passwd_entry = None
        if passwd_entry and passwd_entry.returncode == 0:
            fields = (passwd_entry.stdout or "").strip().split(":")
            if len(fields) >= 6 and fields[5]:
                caller_home = fields[5]
    return Path(caller_home or Path.home())


def resolve_private_key_path() -> Path | None:
    configured = os.environ.get("SSH_PRIVATE_KEY_PATH", "").strip()
    if configured:
        path = Path(configured)
        return path if path.is_file() else None

    home = caller_home()
    for candidate in (home / ".ssh" / "id_ed25519", home / ".ssh" / "id_rsa"):
        if candidate.is_file():
            return candidate
    return None


def known_hosts_path() -> Path:
    configured = os.environ.get("EVANDER_GUEST_KNOWN_HOSTS_FILE", "").strip()
    if configured:
        path = Path(configured)
    else:
        path = caller_home() / ".ssh" / "evander-known_hosts"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return path
    except OSError:
        fallback = (
            Path("/tmp")
            / f"evander-known_hosts-{os.environ.get('SUDO_USER') or os.environ.get('USER') or 'agent'}"
        )
        fallback.parent.mkdir(parents=True, exist_ok=True)
        fallback.touch(exist_ok=True)
        try:
            fallback.chmod(0o600)
        except OSError:
            pass
        return fallback


def run_virsh(args: list[str]) -> subprocess.CompletedProcess[str]:
    commands = [
        ["virsh", "-c", "qemu:///system", *args],
        ["sudo", "-n", "virsh", "-c", "qemu:///system", *args],
        ["virsh", *args],
        ["sudo", "-n", "virsh", *args],
    ]
    last_result: subprocess.CompletedProcess[str] | None = None
    for cmd in commands:
        try:
            result = process_helpers.run_subprocess(cmd)
        except OSError:
            continue
        last_result = result
        if result.returncode == 0:
            return result
    if last_result is None:
        raise RuntimeError(f"Unable to execute virsh command: {' '.join(args)}")
    return last_result


def virsh_lines(args: list[str]) -> list[str]:
    result = run_virsh(args)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(stderr or f"virsh {' '.join(args)} failed")
    return [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]


def dominfo_field(vm_name: str, field_name: str) -> str | None:
    result = run_virsh(["dominfo", vm_name])
    if result.returncode != 0:
        return None
    for line in (result.stdout or "").splitlines():
        key, _, value = line.partition(":")
        if key.strip() == field_name:
            stripped = value.strip()
            return stripped or None
    return None


def resolve_vm_ip(vm_name: str) -> str | None:
    result = run_virsh(["domifaddr", vm_name])
    if result.returncode != 0:
        return None
    for line in (result.stdout or "").splitlines():
        parts = line.split()
        if "ipv4" in parts and len(parts) >= 4:
            address = parts[3]
            return address.split("/", 1)[0]
    return None


def resolve_vm_ip_with_pkexec(vm_name: str) -> str | None:
    result = process_helpers.run_subprocess(
        ["pkexec", "virsh", "domifaddr", vm_name], capture_output=True, text=True
    )
    if result.returncode != 0:
        return None
    for line in (result.stdout or "").splitlines():
        parts = line.split()
        if "ipv4" in parts and len(parts) >= 4:
            address = parts[3]
            return address.split("/", 1)[0]
    return None


def wait_for_vm_ip(
    vm_name: str, *, timeout_seconds: int = 180, allow_pkexec: bool = False
) -> str | None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        ip = resolve_vm_ip(vm_name)
        if ip:
            return ip
        if allow_pkexec:
            ip = resolve_vm_ip_with_pkexec(vm_name)
            if ip:
                return ip
        time.sleep(2)
    return None


def ssh_options(vm_name: str, vm_ip: str, ssh_private_key_path: Path) -> list[str]:
    return [
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"UserKnownHostsFile={known_hosts_path()}",
        "-o",
        f"HostKeyAlias={vm_name}",
        "-o",
        "CheckHostIP=no",
        "-o",
        "ConnectTimeout=5",
        "-i",
        str(ssh_private_key_path),
    ]


def wait_for_vm_ssh(
    vm_name: str,
    vm_ip: str,
    vm_user: str,
    *,
    timeout_seconds: int = 180,
) -> None:
    ssh_private_key_path = resolve_private_key_path()
    if ssh_private_key_path is None:
        raise RuntimeError(
            "Missing SSH private key. Set SSH_PRIVATE_KEY_PATH to the private key that matches the VM's authorized key."
        )

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        completed = process_helpers.run_subprocess(
            [
                "ssh",
                *ssh_options(vm_name, vm_ip, ssh_private_key_path),
                f"{vm_user}@{vm_ip}",
                "true",
            ],
            capture_output=True,
            text=True,
        )
        if completed.returncode == 0:
            return
        time.sleep(2)

    raise RuntimeError(
        f"Timed out waiting for SSH on '{vm_user}@{vm_ip}' after {timeout_seconds}s (VM '{vm_name}')."
    )


def resolve_vm_ssh_access(
    vm_name: str, vm_user: str, *, timeout_seconds: int = 180
) -> tuple[str, Path]:
    vm_ip = resolve_vm_ip(vm_name)
    if vm_ip is None:
        vm_ip = wait_for_vm_ip(vm_name, timeout_seconds=timeout_seconds)
    if vm_ip is None:
        raise RuntimeError(f"Timed out waiting for an IP address for VM '{vm_name}'.")
    wait_for_vm_ssh(vm_name, vm_ip, vm_user, timeout_seconds=timeout_seconds)
    ssh_private_key_path = resolve_private_key_path()
    if ssh_private_key_path is None:
        raise RuntimeError(
            "Missing SSH private key. Set SSH_PRIVATE_KEY_PATH to the private key that matches the VM's authorized key."
        )
    return vm_ip, ssh_private_key_path


def remote_shell_script(
    *,
    command: str | list[str],
    working_directory: str | None = None,
    environment: dict[str, str] | None = None,
) -> str:
    script_lines = ["set -euo pipefail"]
    for key, value in sorted((environment or {}).items()):
        env_key = validate_environment_variable_name(key)
        script_lines.append(f"export {env_key}={shlex.quote(value)}")
    if working_directory:
        script_lines.append(f"cd {shlex.quote(working_directory)}")
    if isinstance(command, str):
        script_lines.append(command)
    else:
        script_lines.append(shlex.join(command))
    return "\n".join(script_lines) + "\n"


def probe_agent_state(
    vm_name: str, vm_ip: str, vm_user: str
) -> tuple[str, list[str], datetime | None, str | None, int | None]:
    ssh_private_key_path = resolve_private_key_path()
    if ssh_private_key_path is None:
        return "unknown", [], None, None, None

    remote_script = r"""
set -euo pipefail

LOG_DIR="${HOME}/.evander"
CONTROL_STATE_PATH="${LOG_DIR}/agent-control.state"
STATE_MAX_AGE_SECONDS="${VM_AGENT_STATE_MAX_AGE_SECONDS:-600}"

read_state_value() {
  local state_path="$1"
  local key="$2"
  [[ -f "${state_path}" ]] || return 1
  awk -F= -v target_key="${key}" '$1 == target_key {print substr($0, index($0, "=") + 1); exit}' "${state_path}" 2>/dev/null
}

is_active_agent_pid() {
  local pid="$1"
  local cmdline_path="/proc/${pid}/cmdline"
  local cmdline=""

  [[ -n "${pid}" && "${pid}" =~ ^[0-9]+$ ]] || return 1
  kill -0 "${pid}" >/dev/null 2>&1 || return 1
  [[ -r "${cmdline_path}" ]] || return 1
  cmdline="$(tr '\000' ' ' < "${cmdline_path}" 2>/dev/null || true)"
  [[ -n "${cmdline}" ]] || return 1
  [[ "${cmdline}" == *"agent_runner_exec"* || "${cmdline}" == *"agent_runner_resume_last"* ]]
}

iso_to_epoch() {
  local raw="$1"
  [[ -n "${raw}" ]] || return 1
  date -u -d "${raw}" +%s 2>/dev/null
}

state_is_fresh() {
  local updated_at="$1"
  local updated_at_epoch now
  updated_at_epoch="$(iso_to_epoch "${updated_at}")" || return 1
  now="$(date -u +%s)"
  (( now - updated_at_epoch <= STATE_MAX_AGE_SECONDS ))
}

if [[ ! -d "${LOG_DIR}" ]]; then
  printf '{"state":"not_started","repos":[],"updated_at":null}\n'
  exit 0
fi

control_status=""
control_updated_at=""
if [[ -f "${CONTROL_STATE_PATH}" ]]; then
  control_status="$(read_state_value "${CONTROL_STATE_PATH}" "status" || true)"
  control_updated_at="$(read_state_value "${CONTROL_STATE_PATH}" "updated_at" || true)"
  if [[ "${control_status}" == "disabled" ]]; then
    printf '{"state":"disabled","repos":[],"updated_at":%s}\n' "$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1] or None))' "${control_updated_at}")"
    exit 0
  fi
fi

shopt -s nullglob
active_entries=()
newest_updated_at="${control_updated_at}"
newest_dispatch_id=""
active_pid=""
found_any_state=0
for state_path in "${LOG_DIR}"/agent-*.state; do
  found_any_state=1
  status="$(read_state_value "${state_path}" "status" || true)"
  updated_at="$(read_state_value "${state_path}" "updated_at" || true)"
  dispatch_id="$(read_state_value "${state_path}" "dispatch_id" || true)"
  pid="$(read_state_value "${state_path}" "pid" || true)"
  if [[ -n "${updated_at}" && ( -z "${newest_updated_at}" || "${updated_at}" > "${newest_updated_at}" ) ]]; then
    newest_updated_at="${updated_at}"
    newest_dispatch_id="${dispatch_id}"
  fi
  if [[ "${status}" != "running" ]]; then
    continue
  fi
  if ! state_is_fresh "${updated_at}" && ! is_active_agent_pid "${pid}"; then
    continue
  fi
  name="$(basename "${state_path}")"
  name="${name#agent-}"
  name="${name%.state}"
  active_entries+=("${name}")
  if [[ -z "${active_pid}" ]]; then
    active_pid="${pid}"
  fi
done
codex_state_path="${LOG_DIR}/codex-resume-last.state"
if [[ -f "${codex_state_path}" ]]; then
  found_any_state=1
  status="$(read_state_value "${codex_state_path}" "status" || true)"
  updated_at="$(read_state_value "${codex_state_path}" "updated_at" || true)"
  dispatch_id="$(read_state_value "${codex_state_path}" "dispatch_id" || true)"
  pid="$(read_state_value "${codex_state_path}" "pid" || true)"
  if [[ -n "${updated_at}" && ( -z "${newest_updated_at}" || "${updated_at}" > "${newest_updated_at}" ) ]]; then
    newest_updated_at="${updated_at}"
    newest_dispatch_id="${dispatch_id}"
  fi
  if [[ "${status}" == "running" ]] && ( state_is_fresh "${updated_at}" || is_active_agent_pid "${pid}" ); then
    active_entries+=("codex-heartbeat")
    if [[ -z "${active_pid}" ]]; then
      active_pid="${pid}"
    fi
  fi
fi
shopt -u nullglob

if [[ ${#active_entries[@]} -gt 0 ]]; then
  repos_json="$(printf '%s\n' "${active_entries[@]}" | python3 -c 'import json,sys; print(json.dumps([line.strip() for line in sys.stdin if line.strip()]))')"
  updated_json="$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1] or None))' "${newest_updated_at}")"
  dispatch_json="$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1] or None))' "${newest_dispatch_id}")"
  pid_json="$(python3 -c 'import json,sys; value=sys.argv[1].strip(); print(json.dumps(int(value) if value.isdigit() else None))' "${active_pid}")"
  printf '{"state":"running","repos":%s,"updated_at":%s,"dispatch_id":%s,"pid":%s}\n' "${repos_json}" "${updated_json}" "${dispatch_json}" "${pid_json}"
  exit 0
fi

if [[ "${found_any_state}" == "1" || -n "${control_updated_at}" ]]; then
  printf '{"state":"idle","repos":[],"updated_at":%s,"dispatch_id":%s,"pid":null}\n' \
    "$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1] or None))' "${newest_updated_at}")" \
    "$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1] or None))' "${newest_dispatch_id}")"
  exit 0
fi

printf '{"state":"not_started","repos":[],"updated_at":null,"dispatch_id":null,"pid":null}\n'
"""

    result = process_helpers.run_subprocess(
        [
            "ssh",
            *ssh_options(vm_name, vm_ip, ssh_private_key_path),
            f"{vm_user}@{vm_ip}",
            "bash",
            "-s",
        ],
        capture_output=True,
        text=True,
        timeout=15.0,
        input=remote_script,
    )
    if result.returncode != 0:
        return "unknown", [], None, None, None
    try:
        payload = json.loads((result.stdout or "").strip() or "{}")
    except json.JSONDecodeError:
        return "unknown", [], None, None, None
    updated_at = payload.get("updated_at")
    return (
        str(payload.get("state") or "unknown"),
        [str(item) for item in payload.get("repos", []) if str(item).strip()],
        datetime.fromisoformat(updated_at) if isinstance(updated_at, str) and updated_at else None,
        str(payload.get("dispatch_id")).strip()
        if payload.get("dispatch_id") is not None and str(payload.get("dispatch_id")).strip()
        else None,
        int(payload["pid"]) if isinstance(payload.get("pid"), int) else None,
    )


_caller_home = caller_home
_resolve_private_key_path = resolve_private_key_path
_known_hosts_path = known_hosts_path
_run_virsh = run_virsh
_virsh_lines = virsh_lines
_dominfo_field = dominfo_field
_resolve_vm_ip = resolve_vm_ip
_resolve_vm_ip_with_pkexec = resolve_vm_ip_with_pkexec
_wait_for_vm_ip = wait_for_vm_ip
_wait_for_vm_ssh = wait_for_vm_ssh
_ssh_options = ssh_options
_resolve_vm_ssh_access = resolve_vm_ssh_access
_validate_environment_variable_name = validate_environment_variable_name
_remote_shell_script = remote_shell_script
_probe_agent_state = probe_agent_state
