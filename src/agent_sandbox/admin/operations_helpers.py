from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

from agent_sandbox.admin.services import process_helpers, session_helpers, vm_helpers
from agent_sandbox.admin.services import vm_agent_service

_run_subprocess = process_helpers.run_subprocess

_WORKER_DEFINITIONS: tuple[tuple[str, str, str], ...] = (
    ("ado", "ADO Worker", "agent-sandbox-ado-worker.service"),
    (
        "proxy-issue-sync",
        "Proxy Issue Sync Worker",
        "agent-sandbox-proxy-issue-sync.service",
    ),
    ("discord-ingest", "Discord Ingest", "agent-sandbox-discord-ingest.service"),
    ("discord-wake", "Discord Wake Worker", "agent-sandbox-discord-wake.service"),
    ("scheduled-wake", "Scheduled Wake Worker", "agent-sandbox-scheduled-wake.service"),
    ("teams-ingest", "Teams Ingest Worker", "agent-sandbox-teams-ingest.service"),
    (
        "teams-meeting-ingest",
        "Teams Meeting Ingest Worker",
        "agent-sandbox-teams-meeting-ingest.service",
    ),
    (
        "effect-orchestrator",
        "Effect Orchestrator Worker",
        "agent-sandbox-effect-orchestrator.service",
    ),
    (
        "trigger-orchestrator",
        "Trigger Orchestrator Worker",
        "agent-sandbox-trigger-orchestrator.service",
    ),
)

def _load_optional_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        values[key] = value.strip().strip('"').strip("'")
    return values


def _run_system_service_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    commands = [args, ["sudo", "-n", *args]]
    last_result: subprocess.CompletedProcess[str] | None = None
    for cmd in commands:
        try:
            result = _run_subprocess(cmd)
        except OSError:
            continue
        last_result = result
        if result.returncode == 0:
            return result
    if last_result is None:
        raise RuntimeError(f"Unable to execute command: {' '.join(args)}")
    return last_result


def _systemctl_show(unit: str) -> dict[str, str]:
    properties = [
        "LoadState",
        "ActiveState",
        "SubState",
        "Result",
        "MainPID",
        "ExecMainStatus",
        "ExecMainCode",
        "UnitFileState",
        "Description",
    ]
    result = _run_system_service_command(
        ["systemctl", "show", "--no-pager", f"--property={','.join(properties)}", unit]
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        detail = stderr or stdout or f"systemctl show failed for {unit}"
        raise RuntimeError(detail)

    payload: dict[str, str] = {}
    for line in (result.stdout or "").splitlines():
        key, _, value = line.partition("=")
        if key:
            payload[key] = value
    return payload


def _journalctl_tail(unit: str, *, lines: int) -> str:
    result = _run_system_service_command(
        ["journalctl", "--no-pager", "--output=short-iso", "-u", unit, "-n", str(lines)]
    )
    output = (result.stdout or "").strip()
    error = (result.stderr or "").strip()
    if result.returncode != 0 and error:
        return f"Worker log unavailable: {error}"
    return output or error or "No journal entries found."


def _worker_definition(worker_id: str) -> tuple[str, str, str] | None:
    normalized = worker_id.strip()
    return next((entry for entry in _WORKER_DEFINITIONS if entry[0] == normalized), None)


def _run_systemd_control(action: str, unit: str) -> subprocess.CompletedProcess[str]:
    commands = [
        ["systemctl", action, unit],
        ["sudo", "-n", "systemctl", action, unit],
        ["pkexec", "systemctl", action, unit],
    ]
    last_result: subprocess.CompletedProcess[str] | None = None
    for cmd in commands:
        try:
            result = _run_subprocess(cmd)
        except OSError:
            continue
        last_result = result
        if result.returncode == 0:
            return result
    if last_result is None:
        raise RuntimeError("Neither systemctl, sudo, nor pkexec was available.")
    return last_result


_virsh_lines = vm_helpers.virsh_lines
_dominfo_field = vm_helpers.dominfo_field
_resolve_vm_ip = vm_helpers.resolve_vm_ip
_resolve_vm_ip_with_pkexec = vm_helpers.resolve_vm_ip_with_pkexec
_wait_for_vm_ip = vm_helpers.wait_for_vm_ip
_wait_for_vm_ssh = vm_helpers.wait_for_vm_ssh
_ssh_options = vm_helpers.ssh_options
_resolve_vm_ssh_access = vm_helpers.resolve_vm_ssh_access
_remote_shell_script = vm_helpers.remote_shell_script
_probe_agent_state = vm_helpers.probe_agent_state


_default_issue_session_id = session_helpers.default_issue_session_id
_default_vm_session_id = session_helpers.default_vm_session_id
_copilot_host_auth_enabled = session_helpers.copilot_host_auth_enabled
_build_issue_session_manifest = session_helpers.build_issue_session_manifest
_matching_vm_session_ids = session_helpers.matching_vm_session_ids
_normalize_session_labels = session_helpers.normalize_session_labels


_append_unique_string_values = vm_agent_service._append_unique_string_values


_email_mailbox_id_from_auth_profile_ref = vm_agent_service._email_mailbox_id_from_auth_profile_ref


_admin_agent_communication_allowlists = vm_agent_service._admin_agent_communication_allowlists


_normalize_allowed_tools = session_helpers.normalize_allowed_tools
_session_tool_argument_policies_from_payload = (
    session_helpers.session_tool_argument_policies_from_payload
)
_normalize_allowed_script_names = session_helpers.normalize_allowed_script_names
_normalize_plain_string_list = session_helpers.normalize_plain_string_list
_normalize_teams_channel_allowlist = session_helpers.normalize_teams_channel_allowlist
_normalize_teams_user_allowlist = session_helpers.normalize_teams_user_allowlist
_validate_allowed_contact_ids_for_directory = (
    session_helpers.validate_allowed_contact_ids_for_runtime_directory
)


COMMUNICATION_FILE_SESSION_POLICY_FIELDS = session_helpers.COMMUNICATION_FILE_SESSION_POLICY_FIELDS
SESSION_BOOL_POLICY_FIELDS = session_helpers.SESSION_BOOL_POLICY_FIELDS
_normalize_session_policy_bool = session_helpers.normalize_session_policy_bool
_normalize_session_policy_non_negative_int_or_none = (
    session_helpers.normalize_session_policy_non_negative_int_or_none
)
_communication_file_session_policy_update = session_helpers.communication_file_session_policy_update
_session_policy_update_from_payload = session_helpers.session_policy_update_from_payload


_resolve_session_approval_notification_target_ids = (
    vm_agent_service._resolve_session_approval_notification_target_ids
)


_sync_added_discord_session_cursors = session_helpers.sync_added_discord_session_cursors
_wake_eligible_discord_channel_ids = session_helpers.wake_eligible_discord_channel_ids


_deep_merge_mapping = session_helpers.deep_merge_mapping
_pkexec_env_command = process_helpers.pkexec_env_command
_sudo_env_command = process_helpers.sudo_env_command
_can_prompt_for_privileges = process_helpers.can_prompt_for_privileges


class PrivilegedHelperSession:
    def __init__(self, *, python_executable: str, project_dir: Path) -> None:
        self.python_executable = python_executable
        self.project_dir = project_dir
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> None:
        with self._lock:
            if self.is_running():
                return
            env_cmd = [
                "pkexec",
                "env",
                f"PYTHONPATH={self.project_dir / 'src'}",
                self.python_executable,
                "-m",
                "agent_sandbox.admin.privileged_helper",
            ]
            self._process = subprocess.Popen(
                env_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            response = self._request_locked({"op": "ping"})
            if response.get("ok") is not True:
                raise RuntimeError(
                    str(response.get("error") or "Failed to start privileged helper.")
                )

    def close(self) -> None:
        with self._lock:
            if not self.is_running():
                self._process = None
                return
            try:
                self._request_locked({"op": "exit"})
            except Exception:
                pass
            process = self._process
            self._process = None
            if process is not None and process.poll() is None:
                process.terminate()

    def call(self, op: str, **payload: object) -> dict[str, object]:
        with self._lock:
            if not self.is_running():
                raise RuntimeError("Privileged helper is not running.")
            response = self._request_locked({"op": op, **payload})
            if response.get("ok") is not True:
                raise RuntimeError(
                    str(response.get("error") or f"Privileged helper op {op} failed.")
                )
