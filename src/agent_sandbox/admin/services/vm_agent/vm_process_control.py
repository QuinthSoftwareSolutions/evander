from __future__ import annotations

from agent_sandbox.admin.services.vm_agent.common import (
    VmActionResult,
    process_helpers,
    vm_helpers,
)


class VmAgentProcessControlMixin:
    def kill_agent(self, target: str, pid: int, *, vm_user: str = "agent") -> VmActionResult:
        vm_name = self._resolve_vm_name_from_target(target)
        if pid <= 0:
            return VmActionResult(
                success=False,
                vm_name=vm_name,
                action="kill_agent",
                message=f"Invalid agent pid: {pid}",
            )
        current_state = vm_helpers.dominfo_field(vm_name, "State")
        if current_state is None:
            return VmActionResult(
                success=False,
                vm_name=vm_name,
                action="kill_agent",
                message=f"VM not found: {vm_name}",
            )
        if current_state != "running":
            return VmActionResult(
                success=False,
                vm_name=vm_name,
                action="kill_agent",
                message=f"VM is not running: {vm_name}",
            )
        try:
            ssh_user, vm_ip, ssh_private_key_path = self._resolve_control_ssh_access(
                vm_name, vm_user=vm_user
            )
        except RuntimeError as exc:
            return VmActionResult(
                success=False, vm_name=vm_name, action="kill_agent", message=str(exc)
            )

        remote_script = r"""
set -euo pipefail

TARGET_PID="${TARGET_PID:-}"
AGENT_USER="${AGENT_USER:-agent}"
AGENT_HOME="$(getent passwd "${AGENT_USER}" | cut -d: -f6)"
if [[ -z "${AGENT_HOME}" ]]; then
  echo "Unknown agent user: ${AGENT_USER}" >&2
  exit 1
fi
LOG_DIR="${AGENT_HOME}/.agent-sandbox"

is_valid_agent_pid() {
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

read_state_value() {
  local state_path="$1"
  local key="$2"
  [[ -f "${state_path}" ]] || return 1
  awk -F= -v target_key="${key}" '$1 == target_key {print substr($0, index($0, "=") + 1); exit}' "${state_path}" 2>/dev/null
}

write_idle_state() {
  local state_path="$1"
  local repo_name="$2"
  local dispatch_id="$3"
  local now tmp_path

  now="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  tmp_path="${state_path}.tmp"
  {
    printf "status=idle\n"
    printf "phase=terminated_by_admin\n"
    printf "repo=%s\n" "${repo_name}"
    printf "pid=%s\n" "${TARGET_PID}"
    printf "updated_at=%s\n" "${now}"
    if [[ -n "${dispatch_id}" ]]; then
      printf "dispatch_id=%s\n" "${dispatch_id}"
    fi
    printf "exit_code=143\n"
  } > "${tmp_path}"
  mv "${tmp_path}" "${state_path}"
  chown "${AGENT_USER}:${AGENT_USER}" "${state_path}"
  chmod 0600 "${state_path}"
}

remove_matching_pid_file() {
  local pid_file="$1"
  local file_pid=""
  [[ -f "${pid_file}" ]] || return 0
  file_pid="$(cat "${pid_file}" 2>/dev/null || true)"
  if [[ "${file_pid}" == "${TARGET_PID}" ]]; then
    rm -f "${pid_file}"
  fi
}

if ! is_valid_agent_pid "${TARGET_PID}"; then
  echo "Guest agent pid is not active or not managed by agent-sandbox: ${TARGET_PID}" >&2
  exit 1
fi

matched_states=()
shopt -s nullglob
for state_path in "${LOG_DIR}"/agent-*.state "${LOG_DIR}/codex-resume-last.state"; do
  state_pid="$(read_state_value "${state_path}" "pid" || true)"
  if [[ "${state_pid}" == "${TARGET_PID}" ]]; then
    matched_states+=("${state_path}")
  fi
done

kill "${TARGET_PID}" >/dev/null 2>&1 || true
sleep 1
if kill -0 "${TARGET_PID}" >/dev/null 2>&1; then
  kill -9 "${TARGET_PID}" >/dev/null 2>&1 || true
fi

for state_path in "${matched_states[@]}"; do
  repo_name="$(read_state_value "${state_path}" "repo" || true)"
  dispatch_id="$(read_state_value "${state_path}" "dispatch_id" || true)"
  write_idle_state "${state_path}" "${repo_name}" "${dispatch_id}"
done

for pid_file in "${LOG_DIR}"/agent-*.pid "${LOG_DIR}/codex-heartbeat.pid"; do
  remove_matching_pid_file "${pid_file}"
done
shopt -u nullglob

printf 'terminated pid=%s state_files=%s\n' "${TARGET_PID}" "${#matched_states[@]}"
"""
        completed = process_helpers.run_subprocess(
            [
                "ssh",
                *vm_helpers.ssh_options(vm_name, vm_ip, ssh_private_key_path),
                f"{ssh_user}@{vm_ip}",
                "sudo",
                "-n",
                "env",
                f"AGENT_USER={vm_user}",
                f"TARGET_PID={pid}",
                "bash",
                "-s",
            ],
            capture_output=True,
            text=True,
            timeout=20.0,
            input=remote_script,
        )
        message = (completed.stdout or completed.stderr or "").strip()
        return VmActionResult(
            success=completed.returncode == 0,
            vm_name=vm_name,
            action="kill_agent",
            message=message or f"kill_agent exited with code {completed.returncode}",
        )
