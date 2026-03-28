from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel

from .agent_control import AgentControlStore
from .bootstrap import load_host_bootstrap_config, load_runtime_gateway_config, resolve_project_dir
from .config import AdoWorkerGlobalConfig, GatewayConfig, HostHeartbeatWorkerGlobalConfig
from .directory_admin import DirectoryAdminService
from .directory_config import (
    DirectoryConfigResolver,
    DirectoryConfigStore,
    get_domain_base_config,
    validate_effective_domain_payload,
)
from .directories import DirectoryStore
from .host_config import DEFAULT_RUNTIME_DIR
from .bootstrap import resolve_runtime_dir
from .ownership import DirectoryOwnershipQueries
from .policy_config import EffectivePolicyConfig
from .provider_config import ProviderConfigResolver
from .vm_defaults_config import EffectiveVmDefaultsConfig, VmDefaultsConfigResolver
from .models import (
    LocalIssueRecord,
    SessionActionResult,
    SessionManifest,
    SessionSummary,
    ToolName,
    VmActionResult,
    VmSummary,
    WorkerActionResult,
    WorkerSummary,
)
from .runtime import RuntimeContext, build_runtime
from .storage import JsonStore

_WORKER_DEFINITIONS: tuple[tuple[str, str, str], ...] = (
    ("ado", "ADO Worker", "agent-sandbox-ado-worker.service"),
    ("host-heartbeat", "Host Heartbeat Worker", "agent-sandbox-host-heartbeat.service"),
)

_WORKER_GLOBAL_CONFIG_PATHS: dict[str, str] = {
    "ado": "config/ado-worker/config.json",
    "host-heartbeat": "config/host-heartbeat/config.json",
}

_WORKER_GLOBAL_CONFIG_MODELS: dict[str, type[BaseModel]] = {
    "ado": AdoWorkerGlobalConfig,
    "host-heartbeat": HostHeartbeatWorkerGlobalConfig,
}


class EffectiveIssueSetupConfig(BaseModel):
    git_root: str
    git_host: str
    git_user: str
    git_ssh_alias: str
    post_exec_verification_prompt: str | None = None
    enable_guest_heartbeat: bool = False
    guest_heartbeat_prompt: str = (
        "Resume work for this session. Check for pending feedback first, then continue the most recent task."
    )
    issue_setup_azure_devops_pr_pat_env: str = "AZURE_DEVOPS_PR_PAT"


class WorkerGlobalConfigPayload(BaseModel):
    worker_id: str
    path: str
    exists: bool
    config: dict[str, object]


class WorkerGlobalConfigWritePayload(BaseModel):
    worker_id: str
    path: str
    config: dict[str, object]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _extract_issue_number(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip()
    if normalized.isdigit():
        return normalized
    if normalized.startswith("vm-") and normalized[3:].isdigit():
        return normalized[3:]
    match = re.match(r"^issue-([0-9]+)(?:-|$)", normalized)
    if match:
        return match.group(1)
    return None


def _read_issue_number_from_yaml(issue_path: Path) -> str:
    text = issue_path.read_text(encoding="utf-8")
    patterns = [
        r"(?m)^\s*issue_number\s*:\s*[\"']?([0-9]+)[\"']?\s*$",
        r"(?m)^\s*number\s*:\s*[\"']?([0-9]+)[\"']?\s*$",
        r"(?m)^\s*id\s*:\s*[\"']?([0-9]+)[\"']?\s*$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    raise ValueError(
        f"Could not read issue number from YAML file: {issue_path}. "
        "Expected one of: issue_number:, number:, or id:."
    )


def _resolve_issue_number(issue_input: str) -> str:
    candidate = Path(issue_input)
    if candidate.is_file():
        return _read_issue_number_from_yaml(candidate)
    return issue_input


def _resolve_vm_name(target: str) -> str:
    normalized = target.strip()
    if normalized.isdigit():
        return f"vm-{normalized}"
    return normalized


def _issue_session_id_prefix(issue_number: str) -> str:
    return f"issue-{issue_number}-"


def _parse_session_extension(value: str | None) -> timedelta:
    raw_value = (value or "1d").strip().lower()
    match = re.fullmatch(r"(\d+)([hd])", raw_value)
    if match is None:
        raise ValueError("Extension must use the format <number><h|d>, for example 8h or 2d.")
    amount = int(match.group(1))
    unit = match.group(2)
    return timedelta(hours=amount) if unit == "h" else timedelta(days=amount)


def _default_session_ttl(policy: EffectivePolicyConfig) -> timedelta:
    return timedelta(seconds=policy.default_session_ttl_seconds)


def _resolve_session_ttl(policy: EffectivePolicyConfig, ttl_hours: int | None) -> timedelta:
    if ttl_hours is not None:
        return timedelta(hours=ttl_hours)
    return _default_session_ttl(policy)


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


def _resolve_git_root(root_dir: Path) -> Path:
    env_value = os.environ.get("AGENT_SANDBOX_GIT_ROOT", "").strip()
    if env_value:
        return Path(env_value)
    return root_dir / "git-remotes"


def _matching_issue_session_ids(issue_number: str, sessions: dict[str, SessionManifest]) -> set[str]:
    vm_name = f"vm-{issue_number}"
    prefix = _issue_session_id_prefix(issue_number)
    matches: set[str] = set()
    for session_id, session in sessions.items():
        if session.vm_id == vm_name or session_id == issue_number or session_id.startswith(prefix):
            matches.add(session_id)
    return matches


def _session_sort_key(item: tuple[str, SessionManifest]) -> tuple[datetime, datetime, str]:
    session_id, manifest = item
    return (manifest.created_at, manifest.expires_at, session_id)


def _resolve_session_target(target: str, sessions: dict[str, SessionManifest]) -> tuple[str, SessionManifest]:
    normalized = target.strip()
    if not normalized:
        raise ValueError("Target must be a non-empty issue number, vm name, or session id.")

    direct_match = sessions.get(normalized)
    if direct_match is not None:
        return normalized, direct_match

    matching_ids: set[str] = set()
    issue_number = _extract_issue_number(normalized)
    if issue_number is not None:
        matching_ids.update(_matching_issue_session_ids(issue_number, sessions))
    if normalized.startswith("vm-"):
        matching_ids.update(
            session_id
            for session_id, session in sessions.items()
            if session.vm_id == normalized
        )

    if not matching_ids:
        raise ValueError(f"Unknown session target: {target}")

    resolved_session_id = max(
        matching_ids,
        key=lambda session_id: _session_sort_key((session_id, sessions[session_id])),
    )
    return resolved_session_id, sessions[resolved_session_id]


def _prune_pr_map(path: Path, *, session_ids: set[str]) -> tuple[int, set[tuple[str, str, str]]]:
    if not path.exists():
        return 0, set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0, set()
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        return 0, set()

    removed_streams: set[tuple[str, str, str]] = set()
    kept_entries: dict[str, object] = {}
    removed_count = 0
    for entry_key, raw_entry in entries.items():
        if not isinstance(raw_entry, dict):
            kept_entries[entry_key] = raw_entry
            continue
        session_id = str(raw_entry.get("session_id", "")).strip()
        if session_id in session_ids:
            provider = str(raw_entry.get("provider", "")).strip()
            repo = str(raw_entry.get("repo", "")).strip()
            pr_id = str(raw_entry.get("pr_id", "")).strip()
            if provider and repo and pr_id:
                removed_streams.add((provider, repo, pr_id))
            removed_count += 1
            continue
        kept_entries[entry_key] = raw_entry

    if removed_count:
        payload["entries"] = kept_entries
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return removed_count, removed_streams


def _prune_pr_feedback_store(path: Path, *, streams: set[tuple[str, str, str]]) -> tuple[int, int]:
    if not path.exists() or not streams:
        return 0, 0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0, 0

    removed_cursor_count = 0
    cursors = payload.get("cursors")
    if isinstance(cursors, dict):
        for provider, repo, pr_id in streams:
            stream_key = f"{provider}:{repo}:{pr_id}"
            if stream_key in cursors:
                cursors.pop(stream_key, None)
                removed_cursor_count += 1

    removed_feedback_count = 0
    feedback = payload.get("feedback")
    if isinstance(feedback, dict):
        kept_feedback: dict[str, object] = {}
        for feedback_id, raw_feedback in feedback.items():
            if not isinstance(raw_feedback, dict):
                kept_feedback[feedback_id] = raw_feedback
                continue
            key = (
                str(raw_feedback.get("provider", "")).strip(),
                str(raw_feedback.get("repo", "")).strip(),
                str(raw_feedback.get("pr_id", "")).strip(),
            )
            if key in streams:
                removed_feedback_count += 1
                continue
            kept_feedback[feedback_id] = raw_feedback
        payload["feedback"] = kept_feedback

    if removed_cursor_count or removed_feedback_count:
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return removed_cursor_count, removed_feedback_count


def _prune_approvals_for_sessions(runtime: RuntimeContext, *, session_ids: set[str]) -> int:
    if not session_ids:
        return 0
    approvals = runtime.approvals.read_all()
    removed_count = 0
    remaining = {}
    for request_id, approval in approvals.items():
        if approval.session_id in session_ids:
            removed_count += 1
            continue
        remaining[request_id] = approval
    if removed_count:
        runtime.approvals.write_all(remaining)
    return removed_count


def _remove_issue_bare_repo(issue_number: str, *, root_dir: Path, runtime_dir: Path) -> tuple[bool, str | None]:
    git_root = _resolve_git_root(root_dir)
    bare_repo_path = git_root / f"{issue_number}_repo.git"
    hook_log_paths = [
        root_dir / "runtime" / "hook-logs" / f"{bare_repo_path.name}.post-receive.log",
        runtime_dir / "hook-logs" / f"{bare_repo_path.name}.post-receive.log",
    ]
    trusted_repo_path: str | None = None
    if bare_repo_path.exists():
        try:
            config_result = subprocess.run(
                ["git", f"--git-dir={bare_repo_path}", "config", "--get", "agent-sandbox.trustedRepoPath"],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            config_result = None
        if config_result is not None and config_result.returncode == 0:
            trusted_repo_path = (config_result.stdout or "").strip() or None
        shutil.rmtree(bare_repo_path, ignore_errors=True)
    for hook_log_path in hook_log_paths:
        if hook_log_path.exists():
            hook_log_path.unlink()
    if trusted_repo_path:
        mirror_remote_name = f"{issue_number}-mirror"
        try:
            subprocess.run(
                ["git", "-C", trusted_repo_path, "remote", "remove", mirror_remote_name],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            pass
    return bare_repo_path.exists() is False, str(bare_repo_path) if bare_repo_path.exists() else str(bare_repo_path)


def _caller_home() -> Path:
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


def _resolve_private_key_path() -> Path | None:
    configured = os.environ.get("SSH_PRIVATE_KEY_PATH", "").strip()
    if configured:
        path = Path(configured)
        return path if path.is_file() else None

    home = _caller_home()
    for candidate in (home / ".ssh" / "id_ed25519", home / ".ssh" / "id_rsa"):
        if candidate.is_file():
            return candidate
    return None


def _known_hosts_path() -> Path:
    configured = os.environ.get("AGENT_SANDBOX_GUEST_KNOWN_HOSTS_FILE", "").strip()
    if configured:
        path = Path(configured)
    else:
        path = _caller_home() / ".ssh" / "agent-sandbox-known_hosts"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def _run_subprocess(
    cmd: list[str],
    *,
    check: bool = False,
    capture_output: bool = True,
    text: bool = True,
    timeout: float | None = None,
    input: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        check=check,
        capture_output=capture_output,
        text=text,
        timeout=timeout,
        input=input,
        env=env,
    )


def _run_virsh(args: list[str]) -> subprocess.CompletedProcess[str]:
    commands = [["virsh", *args], ["sudo", "-n", "virsh", *args]]
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
        raise RuntimeError(f"Unable to execute virsh command: {' '.join(args)}")
    return last_result


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
    result = _run_system_service_command(["systemctl", "show", "--no-pager", f"--property={','.join(properties)}", unit])
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


def _virsh_lines(args: list[str]) -> list[str]:
    result = _run_virsh(args)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(stderr or f"virsh {' '.join(args)} failed")
    return [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]


def _dominfo_field(vm_name: str, field_name: str) -> str | None:
    result = _run_virsh(["dominfo", vm_name])
    if result.returncode != 0:
        return None
    for line in (result.stdout or "").splitlines():
        key, _, value = line.partition(":")
        if key.strip() == field_name:
            stripped = value.strip()
            return stripped or None
    return None


def _resolve_vm_ip(vm_name: str) -> str | None:
    result = _run_virsh(["domifaddr", vm_name])
    if result.returncode != 0:
        return None
    for line in (result.stdout or "").splitlines():
        parts = line.split()
        if "ipv4" in parts and len(parts) >= 4:
            address = parts[3]
            return address.split("/", 1)[0]
    return None


def _ssh_options(vm_name: str, vm_ip: str, ssh_private_key_path: Path) -> list[str]:
    return [
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"UserKnownHostsFile={_known_hosts_path()}",
        "-o",
        f"HostKeyAlias={vm_name}",
        "-o",
        "CheckHostIP=no",
        "-o",
        "ConnectTimeout=5",
        "-i",
        str(ssh_private_key_path),
    ]


def _probe_agent_state(vm_name: str, vm_ip: str, vm_user: str) -> tuple[str, list[str], datetime | None]:
    ssh_private_key_path = _resolve_private_key_path()
    if ssh_private_key_path is None:
        return "unknown", [], None

    remote_script = r"""
set -euo pipefail

LOG_DIR="${HOME}/.agent-sandbox"
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
found_any_state=0
for state_path in "${LOG_DIR}"/agent-*.state; do
  found_any_state=1
  status="$(read_state_value "${state_path}" "status" || true)"
  updated_at="$(read_state_value "${state_path}" "updated_at" || true)"
  pid="$(read_state_value "${state_path}" "pid" || true)"
  if [[ -n "${updated_at}" && ( -z "${newest_updated_at}" || "${updated_at}" > "${newest_updated_at}" ) ]]; then
    newest_updated_at="${updated_at}"
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
done
shopt -u nullglob

if [[ ${#active_entries[@]} -gt 0 ]]; then
  repos_json="$(printf '%s\n' "${active_entries[@]}" | python3 -c 'import json,sys; print(json.dumps([line.strip() for line in sys.stdin if line.strip()]))')"
  updated_json="$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1] or None))' "${newest_updated_at}")"
  printf '{"state":"running","repos":%s,"updated_at":%s}\n' "${repos_json}" "${updated_json}"
  exit 0
fi

if [[ "${found_any_state}" == "1" || -n "${control_updated_at}" ]]; then
  printf '{"state":"idle","repos":[],"updated_at":%s}\n' "$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1] or None))' "${newest_updated_at}")"
  exit 0
fi

printf '{"state":"not_started","repos":[],"updated_at":null}\n'
"""

    result = _run_subprocess(
        ["ssh", *_ssh_options(vm_name, vm_ip, ssh_private_key_path), f"{vm_user}@{vm_ip}", "bash", "-s"],
        capture_output=True,
        text=True,
        timeout=15.0,
        input=remote_script,
    )
    if result.returncode != 0:
        return "unknown", [], None
    try:
        payload = json.loads((result.stdout or "").strip() or "{}")
    except json.JSONDecodeError:
        return "unknown", [], None
    updated_at = payload.get("updated_at")
    return (
        str(payload.get("state") or "unknown"),
        [str(item) for item in payload.get("repos", []) if str(item).strip()],
        datetime.fromisoformat(updated_at) if isinstance(updated_at, str) and updated_at else None,
    )


def _default_issue_session_id(issue_number: str) -> str:
    return f"issue-{issue_number}-{_utc_now():%Y%m%dT%H%M%SZ}-{os.getpid()}"


def _build_issue_session_manifest(
    *,
    policy: EffectivePolicyConfig,
    session_id: str,
    vm_id: str,
    issue_number: str | None,
    directory_path: str,
    ttl_hours: int | None,
    discord_channels: list[str],
    email_mailboxes: list[str],
    email_folders: list[str],
) -> SessionManifest:
    allowed_tools: list[ToolName] = [ToolName.APPROVAL_SUBMIT, ToolName.APPROVAL_CHECK]
    if discord_channels:
        allowed_tools.extend([ToolName.DISCORD_GET_COMMANDS, ToolName.DISCORD_POST_STATUS])

    email_requested = bool(email_mailboxes or email_folders)
    resolved_email_mailboxes = email_mailboxes or (policy.email_mailbox_allowlist if email_requested else [])
    resolved_email_folders = email_folders or (
        sorted({folder for folders in policy.email_folder_allowlist_by_mailbox.values() for folder in folders})
        if email_requested
        else []
    )
    if email_requested:
        allowed_tools.extend(
            [ToolName.EMAIL_LIST_UNREAD, ToolName.EMAIL_GET_MESSAGE, ToolName.EMAIL_CREATE_DRAFT]
        )
        if policy.email_send_enabled:
            allowed_tools.append(ToolName.EMAIL_SEND_APPROVED)

    return SessionManifest(
        session_id=session_id,
        vm_id=vm_id,
        directory_path=directory_path,
        allowed_tools=list(dict.fromkeys(allowed_tools)),
        discord_channel_allowlist=discord_channels,
        email_folder_allowlist=resolved_email_folders,
        email_mailbox_allowlist=resolved_email_mailboxes,
        email_folder_allowlist_by_mailbox=policy.email_folder_allowlist_by_mailbox,
        email_draft_recipient_allowlist=policy.email_draft_recipient_allowlist,
        email_draft_domain_allowlist=policy.email_draft_domain_allowlist,
        email_send_enabled=policy.email_send_enabled,
        expires_at=_utc_now() + _resolve_session_ttl(policy, ttl_hours),
        metadata={"issue_number": issue_number} if issue_number is not None else {},
    )


class AdminService:
    def __init__(
        self,
        runtime_dir: str | Path = DEFAULT_RUNTIME_DIR,
        *,
        legacy_config_path: str | Path | None = None,
        config_path: str | Path | None = None,
    ) -> None:
        candidate_runtime = Path(runtime_dir)
        explicit_legacy = Path(legacy_config_path or config_path) if (legacy_config_path or config_path) else None
        if explicit_legacy is None and candidate_runtime.suffix.casefold() in {".yaml", ".yml"}:
            explicit_legacy = candidate_runtime
            candidate_runtime = resolve_runtime_dir(legacy_config_path=explicit_legacy)
        self.runtime_dir = candidate_runtime
        self.legacy_config_path = explicit_legacy
        self.project_dir = resolve_project_dir(legacy_config_path=self.legacy_config_path)
        bootstrap_config = load_host_bootstrap_config(
            runtime_dir=self.runtime_dir,
            legacy_config_path=self.legacy_config_path,
        )
        directory_store = DirectoryStore(bootstrap_config.directories_store_path)
        directory_store.ensure_root()
        DirectoryConfigStore(directory_store, runtime_dir=bootstrap_config.runtime_dir).ensure_root()
        self.config = load_runtime_gateway_config(
            runtime_dir=bootstrap_config.runtime_dir,
            legacy_config_path=self.legacy_config_path,
        )
        self.runtime = build_runtime(self.config)
        self.agent_control_store = AgentControlStore(self.config.host_agent_control_state_path)
        self.local_issue_store = JsonStore(self.config.runtime_dir / "local-issues.json", LocalIssueRecord)
        self.local_issue_counter_path = self.config.runtime_dir / "local-issue-counter.txt"
        self.local_issue_yaml_dir = self.config.runtime_dir / "issues"
        self.local_issue_yaml_dir.mkdir(parents=True, exist_ok=True)
        self.directory_admin = DirectoryAdminService.from_store(
            directory_store=self.runtime.directories,
            gateway_config=self.config,
        )
        self.directory_ownership = DirectoryOwnershipQueries(
            directories=self.directory_admin._directories,
            sessions=self.runtime.sessions,
            pr_map_path=self.config.pr_map_path,
            ado_processed_store_path=self.config.runtime_dir / "ado-processed-work-items.json",
        )

    @property
    def directory_scoped_config_enabled(self) -> bool:
        return self.config.enable_directory_scoped_config

    def _session_ids_by_vm(self) -> dict[str, list[str]]:
        mapping: dict[str, list[str]] = {}
        for session_id, session in self.runtime.sessions.read_all().items():
            if session.vm_id:
                mapping.setdefault(session.vm_id, []).append(session_id)
        for session_ids in mapping.values():
            session_ids.sort()
        return mapping

    def _directory_paths_by_vm(self) -> dict[str, list[str]]:
        mapping: dict[str, set[str]] = {}
        for session in self.runtime.sessions.read_all().values():
            if session.vm_id:
                mapping.setdefault(session.vm_id, set()).add(session.directory_path)
        return {vm_id: sorted(paths) for vm_id, paths in mapping.items()}

    def _normalize_directory_path(self, directory_path: str | None) -> str:
        if not directory_path:
            return "root"
        result = self.directory_admin.get(directory_path)
        if not result.ok or result.data is None:
            return "root"
        return result.data.details.directory.path

    def _resolve_vm_defaults(self, directory_path: str) -> EffectiveVmDefaultsConfig:
        resolver = VmDefaultsConfigResolver(
            self.config,
            directory_store=self.runtime.directories,
            session_store=self.runtime.sessions,
        )
        return resolver.resolve_for_directory(directory_path)

    def _resolve_issue_setup(self, directory_path: str, *, root_dir: Path) -> EffectiveIssueSetupConfig:
        resolver = DirectoryConfigResolver(
            self.directory_admin._directories,
            base_gateway_config=self.config,
            config_store=DirectoryConfigStore(self.runtime.directories, runtime_dir=self.config.runtime_dir),
        )
        resolution = resolver.resolve_domain(
            directory_path,
            domain="issue_setup",
            base_config=get_domain_base_config(self.config, "issue_setup"),
            validator=lambda candidate: validate_effective_domain_payload("issue_setup", candidate),
        )
        effective = resolution.effective_config
        provider_config = ProviderConfigResolver(
            self.config,
            directory_store=self.runtime.directories,
            session_store=self.runtime.sessions,
        ).resolve_for_directory(directory_path)
        provider_pat_env = provider_config.azure_devops_pr_pat_env.strip() or "AZURE_DEVOPS_PR_PAT"
        issue_setup_pat_env_raw = effective.get("issue_setup_azure_devops_pr_pat_env")
        issue_setup_pat_env = (
            str(issue_setup_pat_env_raw).strip()
            if isinstance(issue_setup_pat_env_raw, str)
            else ""
        ) or provider_pat_env
        if not issue_setup_pat_env:
            raise ValueError(
                "Missing env-var name for Azure DevOps PR PAT lookup. "
                "Set issue_setup_azure_devops_pr_pat_env or providers.azure_devops_pr_pat_env."
            )
        return EffectiveIssueSetupConfig.model_validate(
            {
                "git_root": str(effective.get("git_root") or _resolve_git_root(root_dir)),
                "git_host": str(effective.get("git_host") or "192.168.250.1"),
                "git_user": str(effective.get("git_user") or os.environ.get("USER", "agent")),
                "git_ssh_alias": str(effective.get("git_ssh_alias") or "agent-sandbox-git-host"),
                "post_exec_verification_prompt": effective.get("post_exec_verification_prompt"),
                "enable_guest_heartbeat": bool(effective.get("enable_guest_heartbeat", False)),
                "guest_heartbeat_prompt": str(
                    effective.get("guest_heartbeat_prompt")
                    or "Resume work for this session. Check for pending feedback first, then continue the most recent task."
                ),
                "issue_setup_azure_devops_pr_pat_env": issue_setup_pat_env,
            }
        )

    def _build_setup_issue_vm_invocation(
        self,
        *,
        setup_script: Path,
        issue: str,
        session_id: str,
        vm_user: str,
        start_agent: bool,
        python: bool,
        dotnet: bool,
        nuget: str | None,
        mssql: bool,
        mssql_init_root: str | None,
        playwright: bool,
        frontend_deps: bool,
        frontend_host_checkout: str | None,
        frontend_install_roots: list[str] | None,
        repo_source: str | None,
        vm_defaults: EffectiveVmDefaultsConfig,
        issue_setup: EffectiveIssueSetupConfig,
    ) -> tuple[list[str], dict[str, str]]:
        cmd = [str(setup_script)]
        if not start_agent:
            cmd.append("--dry-run")
        if python:
            cmd.append("--python")
        if dotnet:
            cmd.append("--dotnet")
        if nuget is not None:
            cmd.append("--nuget")
        if mssql:
            cmd.append("--mssql")
        if mssql_init_root is not None:
            cmd.extend(["--mssql-init-root", mssql_init_root])
        if playwright:
            cmd.append("--playwright")
        if frontend_deps:
            cmd.append("--frontend-deps")
        if frontend_host_checkout is not None:
            cmd.extend(["--frontend-host-checkout", frontend_host_checkout])
        for install_root in frontend_install_roots or []:
            cmd.extend(["--frontend-install-root", install_root])
        cmd.append(issue)
        if nuget is not None:
            cmd.append(nuget)
        if repo_source is not None:
            cmd.append(repo_source)
        cmd.extend([session_id, vm_user])

        run_env = os.environ.copy()
        run_env.update(vm_defaults.guest_environment())
        run_env.update(
            {
                "AGENT_SANDBOX_GIT_ROOT": issue_setup.git_root,
                "AGENT_SANDBOX_GIT_HOST": issue_setup.git_host,
                "AGENT_SANDBOX_GIT_USER": issue_setup.git_user,
                "AGENT_SANDBOX_GIT_SSH_ALIAS": issue_setup.git_ssh_alias,
                "ENABLE_GUEST_HEARTBEAT": "1" if issue_setup.enable_guest_heartbeat else "0",
                "GUEST_HEARTBEAT_PROMPT": issue_setup.guest_heartbeat_prompt,
                "AGENT_SANDBOX_AZURE_DEVOPS_PR_PAT_ENV": issue_setup.issue_setup_azure_devops_pr_pat_env,
            }
        )
        if issue_setup.post_exec_verification_prompt is not None:
            run_env["AGENT_POST_EXEC_VERIFICATION_PROMPT"] = issue_setup.post_exec_verification_prompt
        return cmd, run_env

    def _direct_owned_entity_ids(self, directory_path: str, *, kind: str) -> set[str]:
        snapshot = self.directory_ownership.list_entities(directory_path, include_descendants=False)
        return {entity.entity_id for entity in snapshot.entities if entity.kind == kind}

    def list_sessions(
        self,
        *,
        include_expired: bool = False,
        expired_only: bool = False,
        directory_path: str | None = None,
        direct_only: bool = False,
    ) -> list[SessionSummary]:
        if include_expired and expired_only:
            raise ValueError("include_expired and expired_only cannot both be true")

        now = _utc_now()
        normalized_directory_path = self._normalize_directory_path(directory_path)
        owned_session_ids = (
            self._direct_owned_entity_ids(normalized_directory_path, kind="session") if direct_only else set()
        )
        summaries: list[SessionSummary] = []
        for session_id, manifest in sorted(self.runtime.sessions.read_all().items(), key=_session_sort_key):
            if direct_only:
                if session_id not in owned_session_ids:
                    continue
            elif directory_path is not None and manifest.directory_path != normalized_directory_path:
                continue
            is_expired = manifest.expires_at <= now
            if expired_only:
                if not is_expired:
                    continue
            elif not include_expired and is_expired:
                continue
            summaries.append(
                SessionSummary(
                    **manifest.model_dump(),
                    issue_number=_extract_issue_number(manifest.vm_id) or _extract_issue_number(session_id),
                    is_expired=is_expired,
                )
            )
        return summaries

    def renew_session(self, target: str, *, extension: str = "1d") -> SessionActionResult:
        session_id, manifest = _resolve_session_target(target, self.runtime.sessions.read_all())
        previous_expires_at = manifest.expires_at
        manifest.expires_at = _utc_now() + _parse_session_extension(extension)
        self.runtime.sessions.put(session_id, manifest)
        return SessionActionResult(
            success=True,
            session_id=session_id,
            action="renew_session",
            message=(
                f"Renewed session {session_id} "
                f"(previous_expires_at={previous_expires_at.isoformat()} "
                f"new_expires_at={manifest.expires_at.isoformat()})"
            ),
        )

    def revoke_session(self, target: str) -> SessionActionResult:
        session_id, manifest = _resolve_session_target(target, self.runtime.sessions.read_all())
        previous_expires_at = manifest.expires_at
        manifest.expires_at = _utc_now()
        self.runtime.sessions.put(session_id, manifest)
        return SessionActionResult(
            success=True,
            session_id=session_id,
            action="revoke_session",
            message=(
                f"Revoked session {session_id} "
                f"(previous_expires_at={previous_expires_at.isoformat()} "
                f"new_expires_at={manifest.expires_at.isoformat()})"
            ),
        )

    def delete_session(self, target: str) -> SessionActionResult:
        session_id, _ = _resolve_session_target(target, self.runtime.sessions.read_all())
        self.runtime.sessions.delete(session_id)
        return SessionActionResult(
            success=True,
            session_id=session_id,
            action="delete_session",
            message=f"Deleted session {session_id}",
        )

    def list_vms(
        self,
        *,
        include_inactive: bool = True,
        omit_state: bool = False,
        vm_user: str = "agent",
        directory_path: str | None = None,
        direct_only: bool = False,
    ) -> list[VmSummary]:
        vm_names = _virsh_lines(["list", "--all" if include_inactive else "--state-running", "--name"])
        session_ids_by_vm = self._session_ids_by_vm()
        directory_paths_by_vm = self._directory_paths_by_vm()
        normalized_directory_path = self._normalize_directory_path(directory_path)
        owned_vm_ids = self._direct_owned_entity_ids(normalized_directory_path, kind="vm") if direct_only else set()
        summaries: list[VmSummary] = []
        for vm_name in vm_names:
            vm_directory_paths = directory_paths_by_vm.get(vm_name, [])
            if direct_only:
                if vm_name not in owned_vm_ids and not (
                    normalized_directory_path == "root" and not vm_directory_paths
                ):
                    continue
            elif directory_path is not None and normalized_directory_path not in vm_directory_paths:
                continue
            libvirt_state = _dominfo_field(vm_name, "State") or "unknown"
            host_control_status = self.agent_control_store.status_for_vm(vm_name)
            ip_address = _resolve_vm_ip(vm_name) if libvirt_state == "running" else None
            if libvirt_state != "running":
                agent_state = "disabled" if host_control_status == "disabled" else "inactive_vm"
                repo_names: list[str] = []
                updated_at = None
            elif omit_state or not ip_address:
                if host_control_status == "disabled":
                    agent_state = "disabled"
                else:
                    agent_state = "unknown" if not ip_address else "omitted"
                repo_names = []
                updated_at = None
            else:
                agent_state, repo_names, updated_at = _probe_agent_state(vm_name, ip_address, vm_user)
            summaries.append(
                VmSummary(
                    vm_name=vm_name,
                    issue_number=_extract_issue_number(vm_name),
                    session_ids=session_ids_by_vm.get(vm_name, []),
                    directory_paths=vm_directory_paths,
                    libvirt_state=libvirt_state,
                    ip_address=ip_address,
                    agent_state=agent_state,
                    agent_repo_names=repo_names,
                    last_state_update_at=updated_at,
                )
            )
        return summaries

    def list_background_workers(self) -> list[WorkerSummary]:
        summaries: list[WorkerSummary] = []
        for worker_id, name, unit in _WORKER_DEFINITIONS:
            try:
                payload = _systemctl_show(unit)
            except RuntimeError as exc:
                summaries.append(
                    WorkerSummary(
                        worker_id=worker_id,
                        name=name,
                        unit=unit,
                        active_state="unavailable",
                        running=False,
                        failed=False,
                        detail=str(exc),
                    )
                )
                continue

            active_state = payload.get("ActiveState", "") or "unknown"
            sub_state = payload.get("SubState", "") or None
            result = payload.get("Result", "") or None
            unit_file_state = payload.get("UnitFileState", "") or None
            description = payload.get("Description", "") or None
            main_pid_raw = payload.get("MainPID", "").strip()
            main_pid = int(main_pid_raw) if main_pid_raw.isdigit() and int(main_pid_raw) > 0 else None
            detail_parts = []
            if payload.get("LoadState", "").strip():
                detail_parts.append(f"load={payload['LoadState'].strip()}")
            exec_code = payload.get("ExecMainCode", "").strip()
            exec_status = payload.get("ExecMainStatus", "").strip()
            if exec_code or exec_status:
                detail_parts.append(f"exec={exec_code or '?'}:{exec_status or '?'}")

            summaries.append(
                WorkerSummary(
                    worker_id=worker_id,
                    name=name,
                    unit=unit,
                    active_state=active_state,
                    sub_state=sub_state,
                    result=result,
                    unit_file_state=unit_file_state,
                    description=description,
                    main_pid=main_pid,
                    running=active_state == "active",
                    failed=active_state == "failed" or result == "failed",
                    detail=", ".join(detail_parts) or None,
                )
            )

        return summaries

    def get_background_worker_log_snapshot(self, worker_id: str, *, lines: int = 200) -> str:
        worker = _worker_definition(worker_id)
        if worker is None:
            return f"Worker log unavailable: unknown worker '{worker_id}'."
        _, name, unit = worker
        text = _journalctl_tail(unit, lines=lines)
        return f"Worker: {name}\nUnit: {unit}\n\n{text}".strip()

    def start_background_worker(self, worker_id: str) -> WorkerActionResult:
        worker = _worker_definition(worker_id)
        if worker is None:
            return WorkerActionResult(
                success=False,
                worker_id=worker_id,
                unit="",
                action="start",
                message=f"Unknown worker: {worker_id}",
            )
        resolved_worker_id, name, unit = worker
        completed = _run_systemd_control("start", unit)
        message = (completed.stdout or completed.stderr or "").strip() or f"Started {name}"
        return WorkerActionResult(
            success=completed.returncode == 0,
            worker_id=resolved_worker_id,
            unit=unit,
            action="start",
            message=message,
        )

    def stop_background_worker(self, worker_id: str) -> WorkerActionResult:
        worker = _worker_definition(worker_id)
        if worker is None:
            return WorkerActionResult(
                success=False,
                worker_id=worker_id,
                unit="",
                action="stop",
                message=f"Unknown worker: {worker_id}",
            )
        resolved_worker_id, name, unit = worker
        completed = _run_systemd_control("stop", unit)
        message = (completed.stdout or completed.stderr or "").strip() or f"Stopped {name}"
        return WorkerActionResult(
            success=completed.returncode == 0,
            worker_id=resolved_worker_id,
            unit=unit,
            action="stop",
            message=message,
        )

    def get_agent_log_snapshot(
        self,
        target: str,
        *,
        lines: int = 50,
        repo_name: str | None = None,
        vm_user: str = "agent",
    ) -> str:
        issue_number = _extract_issue_number(target) or target
        if not issue_number:
            return "Agent log unavailable: no issue number for selected VM."
        vm_name = f"vm-{issue_number}"
        vm_state = _dominfo_field(vm_name, "State")
        if vm_state != "running":
            return f"Agent log unavailable: {vm_name} is not running."
        vm_ip = _resolve_vm_ip(vm_name)
        if not vm_ip:
            return f"Agent log unavailable: could not determine IP for {vm_name}."
        ssh_private_key_path = _resolve_private_key_path()
        if ssh_private_key_path is None:
            return "Agent log unavailable: missing SSH private key for guest access."

        remote_script = r"""
set -euo pipefail

LOG_DIR="$HOME/.agent-sandbox"
RESOLVED_REPO_NAME="${REPO_NAME:-}"
TAIL_LINES="${TAIL_LINES:-50}"

if [[ -z "${RESOLVED_REPO_NAME}" ]]; then
  shopt -s nullglob
  candidates=()
  for path in "${LOG_DIR}"/agent-*.pid "${LOG_DIR}"/agent-*.log; do
    name="$(basename "${path}")"
    name="${name#agent-}"
    name="${name%.pid}"
    name="${name%.log}"
    if [[ -n "${name}" ]]; then
      candidates+=("${name}")
    fi
  done
  shopt -u nullglob

  if [[ ${#candidates[@]} -eq 0 ]]; then
    echo "No guest agent logs or pid files found under ${LOG_DIR}"
    exit 0
  fi

  mapfile -t unique_candidates < <(printf '%s\n' "${candidates[@]}" | sort -u)
  if [[ ${#unique_candidates[@]} -ne 1 ]]; then
    echo "Multiple guest agent logs found under ${LOG_DIR}; pass an explicit repo name."
    printf 'Candidates:\n'
    printf '  %s\n' "${unique_candidates[@]}"
    exit 0
  fi

  RESOLVED_REPO_NAME="${unique_candidates[0]}"
fi

LOG_PATH="${LOG_DIR}/agent-${RESOLVED_REPO_NAME}.log"
if [[ ! -f "${LOG_PATH}" ]]; then
  echo "No guest agent log found at ${LOG_PATH}"
  exit 0
fi

echo "Repo: ${RESOLVED_REPO_NAME}"
echo "Log: ${LOG_PATH}"
echo
tail -n "${TAIL_LINES}" "${LOG_PATH}"
"""
        result = _run_subprocess(
            [
                "ssh",
                *_ssh_options(vm_name, vm_ip, ssh_private_key_path),
                f"{vm_user}@{vm_ip}",
                "env",
                f"REPO_NAME={repo_name or ''}",
                f"TAIL_LINES={lines}",
                "bash",
                "-s",
            ],
            capture_output=True,
            text=True,
            timeout=20.0,
            input=remote_script,
        )
        output = (result.stdout or "").strip()
        error = (result.stderr or "").strip()
        if result.returncode != 0 and error:
            return f"Agent log unavailable: {error}"
        return output or error or "Agent log is empty."

    def start_vm(self, target: str) -> VmActionResult:
        vm_name = _resolve_vm_name(target)
        current_state = _dominfo_field(vm_name, "State")
        if current_state is None:
            return VmActionResult(success=False, vm_name=vm_name, action="start", message=f"VM not found: {vm_name}")
        if current_state == "running":
            return VmActionResult(success=True, vm_name=vm_name, action="start", message=f"VM already running: {vm_name}")
        result = _run_virsh(["start", vm_name])
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            return VmActionResult(success=False, vm_name=vm_name, action="start", message=stderr or f"Failed to start {vm_name}")
        return VmActionResult(success=True, vm_name=vm_name, action="start", message=f"Started VM {vm_name}")

    def shutdown_vm(self, target: str, *, timeout_seconds: int = 60) -> VmActionResult:
        vm_name = _resolve_vm_name(target)
        current_state = _dominfo_field(vm_name, "State")
        if current_state is None:
            return VmActionResult(success=False, vm_name=vm_name, action="shutdown", message=f"VM not found: {vm_name}")
        if current_state != "running":
            return VmActionResult(success=True, vm_name=vm_name, action="shutdown", message=f"VM already stopped: {vm_name}")
        result = _run_virsh(["shutdown", vm_name])
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            return VmActionResult(success=False, vm_name=vm_name, action="shutdown", message=stderr or f"Failed to request shutdown for {vm_name}")
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            state = _dominfo_field(vm_name, "State")
            if state and state != "running":
                return VmActionResult(success=True, vm_name=vm_name, action="shutdown", message=f"Shut down VM {vm_name}")
            time.sleep(2)
        return VmActionResult(
            success=False,
            vm_name=vm_name,
            action="shutdown",
            message=f"Timed out waiting for {vm_name} to shut down after {timeout_seconds}s",
        )

    def _run_script(self, script_name: str, args: list[str]) -> VmActionResult:
        script_path = Path(__file__).resolve().parents[2] / "scripts" / script_name
        completed = _run_subprocess([str(script_path), *args], capture_output=True, text=True)
        issue_target = args[1] if len(args) > 1 else ""
        issue_number = _extract_issue_number(issue_target) or issue_target
        vm_name = _resolve_vm_name(issue_number) if issue_number else ""
        message = (completed.stdout or completed.stderr or "").strip()
        return VmActionResult(
            success=completed.returncode == 0,
            vm_name=vm_name,
            action=script_name.removesuffix(".sh"),
            message=message or f"{script_name} exited with code {completed.returncode}",
        )

    def enable_agent(self, target: str, *, vm_user: str = "agent") -> VmActionResult:
        issue_number = _extract_issue_number(target) or target
        return self._run_script(
            "set_issue_agent_state.sh",
            ["enable", issue_number, vm_user, "--host-state-path", str(self.config.host_agent_control_state_path)],
        )

    def disable_agent(self, target: str, *, vm_user: str = "agent") -> VmActionResult:
        issue_number = _extract_issue_number(target) or target
        return self._run_script(
            "set_issue_agent_state.sh",
            ["disable", issue_number, vm_user, "--host-state-path", str(self.config.host_agent_control_state_path)],
        )

    def resume_agent(self, target: str, prompt: str, *, vm_user: str = "agent") -> VmActionResult:
        issue_number = _extract_issue_number(target) or target
        return self._run_script(
            "resume_issue_agent.sh",
            [prompt, issue_number, vm_user, "--host-control-state-path", str(self.config.host_agent_control_state_path)],
        )

    def list_local_issues(
        self,
        *,
        directory_path: str | None = None,
        direct_only: bool = False,
    ) -> list[LocalIssueRecord]:
        normalized_directory_path = self._normalize_directory_path(directory_path)
        issues = sorted(self.local_issue_store.read_all().values(), key=lambda issue: issue.issue_number)
        if direct_only or directory_path is not None:
            return [issue for issue in issues if issue.directory_path == normalized_directory_path]
        return issues

    def list_direct_owned_entity_ids(self, directory_path: str, *, kind: str) -> list[str]:
        return sorted(self._direct_owned_entity_ids(directory_path, kind=kind))

    def list_directory_tree(self):
        return self.directory_admin.tree("root")

    def get_directory(self, reference: str):
        return self.directory_admin.get(self._normalize_directory_path(reference))

    def create_directory_child(self, parent: str, name: str):
        return self.directory_admin.create_child(parent=self._normalize_directory_path(parent), name=name)

    def read_directory_domain_config(self, directory_path: str, domain: str):
        return self.directory_admin.read_domain_config(self._normalize_directory_path(directory_path), domain=domain)

    def write_directory_domain_config(self, directory_path: str, domain: str, payload: dict[str, object]):
        return self.directory_admin.write_domain_config(self._normalize_directory_path(directory_path), domain=domain, payload=payload)

    def compute_directory_effective_config(self, directory_path: str, domain: str):
        return self.directory_admin.compute_effective_config(self._normalize_directory_path(directory_path), domain=domain)

    def read_worker_global_config(self, worker_id: str):
        return self._read_worker_global_config(worker_id)

    def write_worker_global_config(self, worker_id: str, payload: dict[str, object]):
        return self._write_worker_global_config(worker_id, payload)

    def _worker_global_config_path(self, worker_id: str) -> Path | None:
        relative = _WORKER_GLOBAL_CONFIG_PATHS.get(worker_id)
        if relative is None:
            return None
        return self.project_dir / relative

    def _read_worker_global_config(self, worker_id: str) -> WorkerGlobalConfigPayload:
        path = self._worker_global_config_path(worker_id)
        if path is None:
            raise ValueError(f"Unknown worker: {worker_id}")
        exists = path.exists()
        if exists:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError(f"worker config file {path} must contain a JSON object")
        else:
            model = _WORKER_GLOBAL_CONFIG_MODELS[worker_id]
            raw = model.model_validate(self.config.model_dump(mode="json")).model_dump(mode="json")
        model = _WORKER_GLOBAL_CONFIG_MODELS[worker_id]
        config = model.model_validate(raw).model_dump(mode="json")
        return WorkerGlobalConfigPayload(worker_id=worker_id, path=str(path), exists=exists, config=config)

    def _write_worker_global_config(self, worker_id: str, payload: dict[str, object]) -> WorkerGlobalConfigWritePayload:
        path = self._worker_global_config_path(worker_id)
        if path is None:
            raise ValueError(f"Unknown worker: {worker_id}")
        model = _WORKER_GLOBAL_CONFIG_MODELS[worker_id]
        config = model.model_validate(payload).model_dump(mode="json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config, indent=2, sort_keys=True), encoding="utf-8")
        return WorkerGlobalConfigWritePayload(worker_id=worker_id, path=str(path), config=config)

    def _next_local_issue_number(self) -> str:
        current = 0
        if self.local_issue_counter_path.exists():
            try:
                current = int(self.local_issue_counter_path.read_text(encoding="utf-8").strip() or "0")
            except ValueError:
                current = 0
        current += 1
        self.local_issue_counter_path.parent.mkdir(parents=True, exist_ok=True)
        self.local_issue_counter_path.write_text(f"{current}\n", encoding="utf-8")
        return str(current)

    def create_local_issue(
        self,
        *,
        title: str = "",
        body: str = "",
        repo_source: str | None = None,
        yaml_path: str | None = None,
        directory_path: str = "root",
    ) -> LocalIssueRecord:
        now = _utc_now()
        normalized_directory_path = self._normalize_directory_path(directory_path)
        if yaml_path:
            source_path = Path(yaml_path)
            issue_number = _read_issue_number_from_yaml(source_path)
            target_yaml_path = self.local_issue_yaml_dir / f"{issue_number}.yaml"
            target_yaml_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
            yaml_text = target_yaml_path.read_text(encoding="utf-8")
            if not title:
                title_match = re.search(r"(?m)^\s*title\s*:\s*[\"']?(.*?)[\"']?\s*$", yaml_text)
                title = title_match.group(1).strip() if title_match else f"Local issue {issue_number}"
            if not body:
                body_match = re.search(r"(?m)^\s*body\s*:\s*[\"']?(.*?)[\"']?\s*$", yaml_text)
                body = body_match.group(1).strip() if body_match else ""
        else:
            if not title.strip():
                raise ValueError("title is required when yaml_path is not provided")
            issue_number = self._next_local_issue_number()
            target_yaml_path = self.local_issue_yaml_dir / f"{issue_number}.yaml"
            yaml_text = (
                f"issue_number: {issue_number}\n"
                f"title: {json.dumps(title.strip())}\n"
                f"body: {json.dumps(body.strip())}\n"
                f"source: local_gui\n"
                f"created_at: {json.dumps(now.isoformat())}\n"
            )
            target_yaml_path.write_text(yaml_text, encoding="utf-8")
        record = LocalIssueRecord(
            issue_number=issue_number,
            title=title.strip() or f"Local issue {issue_number}",
            body=body.strip(),
            yaml_path=str(target_yaml_path),
            directory_path=normalized_directory_path,
            repo_source=repo_source.strip() if repo_source else None,
            source="local_gui",
            created_at=now,
            updated_at=now,
        )
        self.local_issue_store.put(record.issue_number, record)
        return record

    def create_issue_vm(
        self,
        *,
        issue: str,
        session_id: str | None = None,
        repo_source: str | None = None,
        vm_user: str = "agent",
        ttl_hours: int | None = None,
        discord_channels: list[str] | None = None,
        email_mailboxes: list[str] | None = None,
        email_folders: list[str] | None = None,
        python: bool = False,
        dotnet: bool = False,
        nuget: str | None = None,
        mssql: bool = False,
        mssql_init_root: str | None = None,
        playwright: bool = False,
        frontend_deps: bool = False,
        frontend_host_checkout: str | None = None,
        frontend_install_roots: list[str] | None = None,
        start_agent: bool = True,
        directory_path: str = "root",
    ) -> VmActionResult:
        issue_number = _resolve_issue_number(issue)
        resolved_session_id = session_id or _default_issue_session_id(issue_number)
        resolved_vm_name = f"vm-{issue_number}"
        normalized_directory_path = self._normalize_directory_path(directory_path)
        policy = self.runtime.policy_config.resolve_for_directory(normalized_directory_path)
        if self.runtime.sessions.get(resolved_session_id) is not None:
            return VmActionResult(
                success=False,
                vm_name=resolved_vm_name,
                action="create_issue_vm",
                message=f"Session already exists: {resolved_session_id}",
            )
        if mssql and not os.environ.get("MSSQL_SA_PASSWORD", "").strip():
            return VmActionResult(
                success=False,
                vm_name=resolved_vm_name,
                action="create_issue_vm",
                message="MSSQL_SA_PASSWORD must be set when --mssql is used.",
            )

        manifest = _build_issue_session_manifest(
            policy=policy,
            session_id=resolved_session_id,
            vm_id=resolved_vm_name,
            issue_number=issue_number,
            directory_path=normalized_directory_path,
            ttl_hours=ttl_hours,
            discord_channels=discord_channels or [],
            email_mailboxes=email_mailboxes or [],
            email_folders=email_folders or [],
        )
        self.runtime.sessions.put(resolved_session_id, manifest)

        setup_script = Path(__file__).resolve().parents[2] / "scripts" / "setup_issue_vm.sh"
        root_dir = Path(__file__).resolve().parents[2]
        vm_defaults = self._resolve_vm_defaults(normalized_directory_path)
        issue_setup = self._resolve_issue_setup(normalized_directory_path, root_dir=root_dir)
        cmd, run_env = self._build_setup_issue_vm_invocation(
            setup_script=setup_script,
            issue=issue,
            session_id=resolved_session_id,
            vm_user=vm_user,
            start_agent=start_agent,
            python=python,
            dotnet=dotnet,
            nuget=nuget,
            mssql=mssql,
            mssql_init_root=mssql_init_root,
            playwright=playwright,
            frontend_deps=frontend_deps,
            frontend_host_checkout=frontend_host_checkout,
            frontend_install_roots=frontend_install_roots,
            repo_source=repo_source,
            vm_defaults=vm_defaults,
            issue_setup=issue_setup,
        )
        completed = _run_subprocess(cmd, capture_output=True, text=True, env=run_env)
        message = (completed.stdout or completed.stderr or "").strip()
        if completed.returncode != 0:
            return VmActionResult(
                success=False,
                vm_name=resolved_vm_name,
                action="create_issue_vm",
                message=message or f"setup_issue_vm.sh exited with code {completed.returncode}",
            )
        return VmActionResult(
            success=True,
            vm_name=resolved_vm_name,
            action="create_issue_vm",
            message=message or f"Created issue VM {resolved_vm_name}",
        )

    def create_issue_vm_from_local_issue(
        self,
        issue_number: str,
        *,
        repo_source: str | None = None,
        vm_user: str = "agent",
        start_agent: bool = True,
        python: bool = False,
        dotnet: bool = False,
        nuget: str | None = None,
        mssql: bool = False,
        mssql_init_root: str | None = None,
        playwright: bool = False,
    ) -> VmActionResult:
        issue = self.local_issue_store.get(issue_number)
        if issue is None:
            return VmActionResult(
                success=False,
                vm_name=f"vm-{issue_number}",
                action="create_issue_vm",
                message=f"Unknown local issue: {issue_number}",
            )
        return self.create_issue_vm(
            issue=issue.yaml_path,
            repo_source=repo_source or issue.repo_source,
            vm_user=vm_user,
            start_agent=start_agent,
            python=python,
            dotnet=dotnet,
            nuget=nuget,
            mssql=mssql,
            mssql_init_root=mssql_init_root,
            playwright=playwright,
            directory_path=issue.directory_path,
        )

    def destroy_issue_vm(self, target: str) -> VmActionResult:
        issue_number = _extract_issue_number(target) or target
        vm_name = f"vm-{issue_number}" if issue_number else target
        root_dir = Path(__file__).resolve().parents[2]
        destroy_script = root_dir / "scripts" / "destroy_agent_vm.sh"

        completed = _run_subprocess(
            ["pkexec", str(destroy_script), vm_name],
            capture_output=True,
            text=True,
        )
        message = (completed.stdout or completed.stderr or "").strip()
        if completed.returncode != 0:
            return VmActionResult(
                success=False,
                vm_name=vm_name,
                action="destroy_issue_vm",
                message=message or f"destroy-issue-vm exited with code {completed.returncode}",
            )

        sessions = self.runtime.sessions.read_all()
        session_ids = _matching_issue_session_ids(issue_number, sessions)
        if session_ids:
            remaining_sessions = {
                session_id: manifest for session_id, manifest in sessions.items() if session_id not in session_ids
            }
            self.runtime.sessions.write_all(remaining_sessions)
        removed_approvals = _prune_approvals_for_sessions(self.runtime, session_ids=session_ids)
        removed_pr_map_entries, removed_streams = _prune_pr_map(self.config.pr_map_path, session_ids=session_ids)
        removed_cursors, removed_feedback = _prune_pr_feedback_store(
            self.config.runtime_dir / "pr-feedback-store.json",
            streams=removed_streams,
        )
        bare_repo_removed, bare_repo_path = _remove_issue_bare_repo(
            issue_number,
            root_dir=root_dir,
            runtime_dir=self.config.runtime_dir,
        )
        summary = (
            f"Destroyed issue VM {vm_name}; "
            f"removed_sessions={len(session_ids)} "
            f"removed_pr_map_entries={removed_pr_map_entries} "
            f"removed_approvals={removed_approvals} "
            f"removed_feedback_cursors={removed_cursors} "
            f"removed_feedback_items={removed_feedback} "
            f"bare_repo_removed={bare_repo_removed} "
            f"bare_repo_path={bare_repo_path}"
        )
        if message:
            summary = f"{message}\n{summary}"
        return VmActionResult(
            success=True,
            vm_name=vm_name,
            action="destroy_issue_vm",
            message=summary,
        )

    def delete_local_issue(self, issue_number: str) -> VmActionResult:
        issue = self.local_issue_store.get(issue_number)
        if issue is None:
            return VmActionResult(
                success=False,
                vm_name=f"vm-{issue_number}",
                action="delete_local_issue",
                message=f"Unknown local issue: {issue_number}",
            )

        vm_name = f"vm-{issue_number}"
        existing_vm_state = _dominfo_field(vm_name, "State")
        sessions = self.runtime.sessions.read_all()
        has_session = any(
            session_id == issue_number
            or session_id.startswith(f"issue-{issue_number}-")
            or session.vm_id == vm_name
            for session_id, session in sessions.items()
        )
        if existing_vm_state is not None or has_session:
            return VmActionResult(
                success=False,
                vm_name=vm_name,
                action="delete_local_issue",
                message=f"Destroy {vm_name} before deleting local issue {issue_number}.",
            )

        self.local_issue_store.delete(issue_number)
        yaml_path = Path(issue.yaml_path)
        if yaml_path.exists():
            yaml_path.unlink()
        return VmActionResult(
            success=True,
            vm_name=vm_name,
            action="delete_local_issue",
            message=f"Deleted local issue {issue_number}",
        )
