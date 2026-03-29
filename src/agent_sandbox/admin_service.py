from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel
import yaml

from .agent_control import AgentControlStore
from .bootstrap import load_host_bootstrap_config, load_runtime_gateway_config, resolve_project_dir
from .config import (
    AdoWorkerGlobalConfig,
    GatewayConfig,
    HostHeartbeatWorkerGlobalConfig,
    IssueOrchestratorWorkerGlobalConfig,
)
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
from .issue_service import IssueService
from .vm_defaults_config import EffectiveVmDefaultsConfig, VmDefaultsConfigResolver
from .models import (
    DirectoryPath,
    LocalIssueRecord,
    RemoteIssueReference,
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
    ("issue-orchestrator", "Issue Orchestrator Worker", "agent-sandbox-issue-orchestrator.service"),
)

_WORKER_GLOBAL_CONFIG_PATHS: dict[str, str] = {
    "ado": "config/ado-worker/config.json",
    "host-heartbeat": "config/host-heartbeat/config.json",
    "issue-orchestrator": "config/issue-orchestrator/config.json",
}

_WORKER_GLOBAL_CONFIG_MODELS: dict[str, type[BaseModel]] = {
    "ado": AdoWorkerGlobalConfig,
    "host-heartbeat": HostHeartbeatWorkerGlobalConfig,
    "issue-orchestrator": IssueOrchestratorWorkerGlobalConfig,
}
ISSUE_STATES: tuple[str, ...] = ("New", "Approved", "Committed", "In Progress", "Completed", "Closed")
_ISSUE_STATE_SET = set(ISSUE_STATES)
_ISSUE_PRIORITY_ORDER: dict[str, int] = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


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


def _normalize_local_issue_state(state: str | None, *, allow_empty: bool = False) -> str | None:
    normalized = str(state or "").strip()
    if not normalized:
        return None if allow_empty else "New"
    if normalized not in _ISSUE_STATE_SET:
        allowed = ", ".join(ISSUE_STATES)
        raise ValueError(f"Invalid issue state: {normalized!r}. Allowed values: {allowed}.")
    return normalized


def _normalize_issue_priority(priority: str | None) -> str | None:
    normalized = str(priority or "").strip().upper()
    if not normalized:
        return None
    if normalized not in _ISSUE_PRIORITY_ORDER:
        allowed = ", ".join(sorted(_ISSUE_PRIORITY_ORDER))
        raise ValueError(f"Invalid issue priority: {normalized!r}. Allowed values: {allowed}.")
    return normalized


def _local_issue_fields(issue: LocalIssueRecord) -> dict[str, object]:
    fields = issue.metadata.get("fields")
    if isinstance(fields, dict):
        return dict(fields)
    return {}


def _issue_priority_sort_key(issue: LocalIssueRecord) -> tuple[int, datetime, datetime, str]:
    fields = _local_issue_fields(issue)
    priority_value = fields.get("priority")
    if priority_value is None:
        priority_value = fields.get("Priority")
    priority = _normalize_issue_priority(str(priority_value or "").strip()) or "P3"
    return (
        _ISSUE_PRIORITY_ORDER[priority],
        issue.updated_at,
        issue.created_at,
        issue.issue_number,
    )


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


def _extract_issue_number_from_payload(payload: dict[str, Any]) -> str | None:
    for key in ("issue_number", "number", "id"):
        value = payload.get(key)
        if value is None:
            continue
        normalized = str(value).strip()
        if normalized.isdigit():
            return normalized
    source = payload.get("source")
    if isinstance(source, dict):
        for key in ("work_item_id", "issue_id", "work_item_url"):
            value = str(source.get(key) or "").strip()
            if value.isdigit():
                return value
            parsed = _extract_issue_number(value)
            if parsed is not None:
                return parsed
    for key in ("url",):
        parsed = _extract_issue_number(str(payload.get(key) or "").strip())
        if parsed is not None:
            return parsed
    return None


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


def _resolve_vm_name_from_target(target: str, sessions: dict[str, SessionManifest]) -> str:
    normalized = target.strip()
    if not normalized:
        return ""
    if normalized in sessions:
        return sessions[normalized].vm_id or normalized
    return _resolve_vm_name(normalized)


def _issue_session_id_prefix(issue_number: str) -> str:
    return f"issue-{issue_number}-"


def _scoped_issue_id(
    issue_number: str,
    *,
    directory_path: str,
    provider: str,
    organization: str | None = None,
    project: str | None = None,
) -> str:
    normalized_directory_path = DirectoryPath(segments=directory_path).as_posix()
    parts = [normalized_directory_path, provider]
    if organization and project:
        parts.extend([organization.rstrip("/"), project])
    parts.append(issue_number)
    return "::".join(parts)


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


def _default_base_branch_for_repo(repo_path: str) -> str | None:
    candidates = [
        ["git", "-C", repo_path, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"],
        ["git", "-C", repo_path, "branch", "--show-current"],
    ]
    for cmd in candidates:
        result = _run_subprocess(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            continue
        value = (result.stdout or "").strip()
        if not value:
            continue
        if value.startswith("origin/"):
            value = value[len("origin/") :]
        return value or None
    return None


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
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return path
    except OSError:
        fallback = Path("/tmp") / f"agent-sandbox-known_hosts-{os.environ.get('SUDO_USER') or os.environ.get('USER') or 'agent'}"
        fallback.parent.mkdir(parents=True, exist_ok=True)
        fallback.touch(exist_ok=True)
        try:
            fallback.chmod(0o600)
        except OSError:
            pass
        return fallback


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


def _combined_process_message(stdout: str | None, stderr: str | None) -> str:
    stdout_text = (stdout or "").strip()
    stderr_text = (stderr or "").strip()
    if stdout_text and stderr_text:
        if stdout_text == stderr_text:
            return stdout_text
        return f"{stdout_text}\n{stderr_text}"
    return stdout_text or stderr_text


def _python_has_pip(python_bin: str) -> bool:
    version_check = _run_subprocess([python_bin, "--version"], capture_output=True, text=True)
    if version_check.returncode != 0:
        return False
    pip_check = _run_subprocess([python_bin, "-m", "pip", "--version"], capture_output=True, text=True)
    return pip_check.returncode == 0


def _ensure_python_has_pip(python_bin: str) -> bool:
    if _python_has_pip(python_bin):
        return True
    ensurepip_result = _run_subprocess(
        [python_bin, "-m", "ensurepip", "--upgrade", "--default-pip"],
        capture_output=True,
        text=True,
    )
    if ensurepip_result.returncode != 0:
        return False
    return _python_has_pip(python_bin)


def _resolve_host_python_with_pip() -> str:
    configured = os.environ.get("HOST_PYTHON_BIN", "").strip()
    if configured:
        if _ensure_python_has_pip(configured):
            return configured
        raise RuntimeError(f"Host Python must provide pip: {configured}")

    candidates: list[str] = []
    if sys.executable:
        candidates.append(sys.executable)
    candidates.extend(["python3", "python"])

    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if _ensure_python_has_pip(candidate):
            return candidate

    preferred = sys.executable or "python3"
    raise RuntimeError(f"Host Python must provide pip: {preferred}")


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


def _resolve_vm_ip_with_pkexec(vm_name: str) -> str | None:
    result = _run_subprocess(["pkexec", "virsh", "domifaddr", vm_name], capture_output=True, text=True)
    if result.returncode != 0:
        return None
    for line in (result.stdout or "").splitlines():
        parts = line.split()
        if "ipv4" in parts and len(parts) >= 4:
            address = parts[3]
            return address.split("/", 1)[0]
    return None


def _wait_for_vm_ip(vm_name: str, *, timeout_seconds: int = 180, allow_pkexec: bool = False) -> str | None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        ip = _resolve_vm_ip(vm_name)
        if ip:
            return ip
        if allow_pkexec:
            ip = _resolve_vm_ip_with_pkexec(vm_name)
            if ip:
                return ip
        time.sleep(2)
    return None


def _wait_for_vm_ssh(
    vm_name: str,
    vm_ip: str,
    vm_user: str,
    *,
    timeout_seconds: int = 180,
) -> None:
    ssh_private_key_path = _resolve_private_key_path()
    if ssh_private_key_path is None:
        raise RuntimeError(
            "Missing SSH private key. Set SSH_PRIVATE_KEY_PATH to the private key that matches the VM's authorized key."
        )

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        completed = _run_subprocess(
            ["ssh", *_ssh_options(vm_name, vm_ip, ssh_private_key_path), f"{vm_user}@{vm_ip}", "true"],
            capture_output=True,
            text=True,
        )
        if completed.returncode == 0:
            return
        time.sleep(2)

    raise RuntimeError(
        f"Timed out waiting for SSH on '{vm_user}@{vm_ip}' after {timeout_seconds}s (VM '{vm_name}')."
    )


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
codex_state_path="${LOG_DIR}/codex-resume-last.state"
if [[ -f "${codex_state_path}" ]]; then
  found_any_state=1
  status="$(read_state_value "${codex_state_path}" "status" || true)"
  updated_at="$(read_state_value "${codex_state_path}" "updated_at" || true)"
  pid="$(read_state_value "${codex_state_path}" "pid" || true)"
  if [[ -n "${updated_at}" && ( -z "${newest_updated_at}" || "${updated_at}" > "${newest_updated_at}" ) ]]; then
    newest_updated_at="${updated_at}"
  fi
  if [[ "${status}" == "running" ]] && ( state_is_fresh "${updated_at}" || is_active_agent_pid "${pid}" ); then
    active_entries+=("codex-heartbeat")
  fi
fi
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


def _default_vm_session_id(vm_name: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "-", vm_name.strip()).strip("-") or "vm"
    return f"{normalized}-{_utc_now():%Y%m%dT%H%M%SZ}-{os.getpid()}"


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


def _matching_vm_session_ids(vm_name: str, sessions: dict[str, SessionManifest]) -> set[str]:
    return {session_id for session_id, session in sessions.items() if session.vm_id == vm_name}


def _pkexec_env_command(base_cmd: list[str], env: dict[str, str], allowed_keys: tuple[str, ...]) -> list[str]:
    cmd = ["pkexec", "env"]
    for key in allowed_keys:
        value = env.get(key)
        if value:
            cmd.append(f"{key}={value}")
    cmd.extend(base_cmd)
    return cmd


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
                "agent_sandbox.admin_privileged_helper",
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
                raise RuntimeError(str(response.get("error") or "Failed to start privileged helper."))

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
                raise RuntimeError(str(response.get("error") or f"Privileged helper op {op} failed."))
            return response

    def _request_locked(self, payload: dict[str, object]) -> dict[str, object]:
        process = self._process
        if process is None or process.stdin is None or process.stdout is None:
            raise RuntimeError("Privileged helper is unavailable.")
        process.stdin.write(json.dumps(payload) + "\n")
        process.stdin.flush()
        line = process.stdout.readline()
        if not line:
            stderr = process.stderr.read().strip() if process.stderr is not None else ""
            raise RuntimeError(stderr or "Privileged helper exited unexpectedly.")
        return json.loads(line)


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
        self.use_privileged_helper = False
        self._privileged_helper: PrivilegedHelperSession | None = None
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
        self.issue_service = IssueService(
            ProviderConfigResolver(
                self.config,
                directory_store=self.runtime.directories,
                session_store=self.runtime.sessions,
            )
        )

    @property
    def directory_scoped_config_enabled(self) -> bool:
        return self.config.enable_directory_scoped_config

    def unlock_privileged_operations(self) -> str:
        helper = self._privileged_helper
        if helper is None:
            helper = PrivilegedHelperSession(python_executable=sys.executable, project_dir=self.project_dir)
            self._privileged_helper = helper
        helper.start()
        return "Privileged operations unlocked."

    def lock_privileged_operations(self) -> None:
        if self._privileged_helper is not None:
            self._privileged_helper.close()
            self._privileged_helper = None

    def privileged_operations_unlocked(self) -> bool:
        return self._privileged_helper is not None and self._privileged_helper.is_running()

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

    def _resolve_repo_source(self, repo_source: str) -> tuple[str, str]:
        resolve_script = self.project_dir / "scripts" / "resolve_repo_source.sh"
        path_result = _run_subprocess([str(resolve_script), "--path-only", repo_source], capture_output=True, text=True)
        if path_result.returncode != 0:
            raise RuntimeError((path_result.stderr or path_result.stdout or "").strip() or f"Failed to resolve repo source: {repo_source}")
        name_result = _run_subprocess([str(resolve_script), "--name-only", repo_source], capture_output=True, text=True)
        if name_result.returncode != 0:
            raise RuntimeError((name_result.stderr or name_result.stdout or "").strip() or f"Failed to determine repo name: {repo_source}")
        return (path_result.stdout or "").strip(), (name_result.stdout or "").strip()

    def _sync_repo_into_bare_remote(self, repo_name: str, resolved_repo_path: str, *, issue_setup: EffectiveIssueSetupConfig) -> Path:
        create_remote_script = self.project_dir / "scripts" / "create_git_remote.sh"
        completed = _run_subprocess(
            [str(create_remote_script), repo_name, "agents"],
            capture_output=True,
            text=True,
            env=os.environ | {
                "AGENT_SANDBOX_GIT_ROOT": issue_setup.git_root,
                "AGENT_SANDBOX_GIT_HOST": issue_setup.git_host,
                "AGENT_SANDBOX_GIT_USER": issue_setup.git_user,
                "AGENT_SANDBOX_GIT_SSH_ALIAS": issue_setup.git_ssh_alias,
            },
        )
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or completed.stdout or "").strip() or f"Failed to create git remote for {repo_name}")
        bare_repo_path = Path(issue_setup.git_root) / f"{repo_name}.git"
        fetch_result = _run_subprocess(
            [
                "git",
                f"--git-dir={bare_repo_path}",
                "fetch",
                "--prune",
                resolved_repo_path,
                "+refs/heads/*:refs/heads/*",
                "+refs/tags/*:refs/tags/*",
            ],
            capture_output=True,
            text=True,
        )
        if fetch_result.returncode != 0:
            raise RuntimeError((fetch_result.stderr or fetch_result.stdout or "").strip() or f"Failed to mirror repo into {bare_repo_path}")
        default_base_branch = _default_base_branch_for_repo(resolved_repo_path)
        if default_base_branch:
            symbolic_ref_result = _run_subprocess(
                [
                    "git",
                    f"--git-dir={bare_repo_path}",
                    "symbolic-ref",
                    "HEAD",
                    f"refs/heads/{default_base_branch}",
                ],
                capture_output=True,
                text=True,
            )
            if symbolic_ref_result.returncode != 0:
                raise RuntimeError(
                    (symbolic_ref_result.stderr or symbolic_ref_result.stdout or "").strip()
                    or f"Failed to set HEAD for {bare_repo_path}"
                )
        return bare_repo_path

    def _clone_repo_into_vm(
        self,
        *,
        vm_name: str,
        vm_user: str,
        session_id: str,
        repo_name: str,
        issue_setup: EffectiveIssueSetupConfig,
        vm_ip: str | None = None,
    ) -> str:
        bootstrap_git_script = self.project_dir / "scripts" / "bootstrap_git_vm.sh"
        completed = _run_subprocess(
            [str(bootstrap_git_script), vm_name, vm_user],
            capture_output=True,
            text=True,
            env=os.environ | {
                "AGENT_SANDBOX_GIT_ROOT": issue_setup.git_root,
                "AGENT_SANDBOX_GIT_HOST": issue_setup.git_host,
                "AGENT_SANDBOX_GIT_USER": issue_setup.git_user,
                "AGENT_SANDBOX_GIT_SSH_ALIAS": issue_setup.git_ssh_alias,
            },
        )
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or completed.stdout or "").strip() or f"Failed to bootstrap guest git for {vm_name}")

        vm_ip = vm_ip or _resolve_vm_ip(vm_name)
        ssh_private_key_path = _resolve_private_key_path()
        if not vm_ip or ssh_private_key_path is None:
            raise RuntimeError(f"Unable to resolve SSH access for {vm_name}")

        remote_url = f"ssh://{issue_setup.git_ssh_alias}{issue_setup.git_root}/{repo_name}.git"
        guest_repo_root = f"/home/{vm_user}/{repo_name}"
        remote_script = f"""
set -euo pipefail
. ~/.profile
TARGET_DIR="{guest_repo_root}"
REMOTE_URL="{remote_url}"
SESSION_ID="{session_id}"
if [[ -e "${{TARGET_DIR}}" ]]; then
  echo "Guest repo path already exists: ${{TARGET_DIR}}" >&2
  exit 1
fi
git clone "${{REMOTE_URL}}" "${{TARGET_DIR}}"
git -C "${{TARGET_DIR}}" remote add agent "${{REMOTE_URL}}"
git -C "${{TARGET_DIR}}" config user.name "Truman Bot"
git -C "${{TARGET_DIR}}" config user.email "truman@quinth.se"
git -C "${{TARGET_DIR}}" config agent-sandbox.sessionId "${{SESSION_ID}}"
git -C "${{TARGET_DIR}}" config alias.push-agent '!f(){{ session_id=${{SESSION_ID:-$(git config --get agent-sandbox.sessionId || true)}}; if [ -z "${{session_id}}" ]; then echo "SESSION_ID is not set; refusing ambiguous agent push." >&2; return 1; fi; branch=$(git rev-parse --abbrev-ref HEAD); git push agent HEAD:refs/heads/agents/${{session_id}}/${{branch}}; }}; f'
"""
        ssh_result = _run_subprocess(
            ["ssh", *_ssh_options(vm_name, vm_ip, ssh_private_key_path), f"{vm_user}@{vm_ip}", "bash", "-s"],
            capture_output=True,
            text=True,
            timeout=60.0,
            input=remote_script,
        )
        if ssh_result.returncode != 0:
            raise RuntimeError((ssh_result.stderr or ssh_result.stdout or "").strip() or f"Failed to clone repo into {vm_name}")
        return guest_repo_root

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
        vm_name = _resolve_vm_name_from_target(target, self.runtime.sessions.read_all())
        if not vm_name:
            return "Agent log unavailable: no VM target selected."
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
  if [[ -f "${LOG_DIR}/codex-heartbeat-run.log" || -f "${LOG_DIR}/codex-heartbeat.pid" ]]; then
    candidates+=("codex-heartbeat")
  fi
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

if [[ "${RESOLVED_REPO_NAME}" == "codex-heartbeat" ]]; then
  LOG_PATH="${LOG_DIR}/codex-heartbeat-run.log"
else
  LOG_PATH="${LOG_DIR}/agent-${RESOLVED_REPO_NAME}.log"
fi
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
        vm_name = ""
        if "--vm-name" in args:
            try:
                vm_name = args[args.index("--vm-name") + 1]
            except IndexError:
                vm_name = ""
        elif len(args) > 1:
            issue_target = args[1]
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
        vm_name = _resolve_vm_name_from_target(target, self.runtime.sessions.read_all())
        return self._run_script(
            "set_issue_agent_state.sh",
            ["--state", "enable", "--vm-name", vm_name, "--vm-user", vm_user, "--host-state-path", str(self.config.host_agent_control_state_path)],
        )

    def disable_agent(self, target: str, *, vm_user: str = "agent") -> VmActionResult:
        vm_name = _resolve_vm_name_from_target(target, self.runtime.sessions.read_all())
        return self._run_script(
            "set_issue_agent_state.sh",
            ["--state", "disable", "--vm-name", vm_name, "--vm-user", vm_user, "--host-state-path", str(self.config.host_agent_control_state_path)],
        )

    def resume_agent(self, target: str, prompt: str, *, vm_user: str = "agent") -> VmActionResult:
        vm_name = _resolve_vm_name_from_target(target, self.runtime.sessions.read_all())
        return self._run_script(
            "resume_issue_agent.sh",
            ["--prompt", prompt, "--vm-name", vm_name, "--vm-user", vm_user, "--host-control-state-path", str(self.config.host_agent_control_state_path)],
        )

    def list_local_issues(
        self,
        *,
        directory_path: str | None = None,
        direct_only: bool = False,
    ) -> list[LocalIssueRecord]:
        return self.list_issues(directory_path=directory_path, direct_only=direct_only)

    def _extract_local_issue_id_candidate(self, value: str) -> str | None:
        normalized = value.strip()
        if not normalized:
            return None
        if normalized.isdigit():
            return normalized
        if normalized.startswith("vm-") and normalized[3:].isdigit():
            return normalized[3:]
        prefixed_match = re.match(r"^(?:local|issue)[:/\-]([0-9]+)$", normalized, flags=re.IGNORECASE)
        if prefixed_match:
            return prefixed_match.group(1)
        session_match = re.match(r"^issue-([0-9]+)(?:-|$)", normalized)
        if session_match:
            return session_match.group(1)
        return None

    def _find_local_issue_by_remote_identifier(self, issue_identifier: str) -> LocalIssueRecord | None:
        normalized = issue_identifier.strip()
        if not normalized:
            return None
        provider = ""
        remote_issue_id = ""
        prefixed_match = re.match(
            r"^(?:(?:proxy:)?(azure_devops|ado)[:/])([0-9]+)$",
            normalized,
            flags=re.IGNORECASE,
        )
        if prefixed_match:
            provider = "azure_devops"
            remote_issue_id = prefixed_match.group(2)
        if not remote_issue_id:
            return None
        matches: list[LocalIssueRecord] = []
        for issue in self.local_issue_store.read_all().values():
            remote = issue.remote_reference
            if remote is not None:
                issue_provider = str(remote.provider or "").strip()
                issue_remote_id = str(remote.work_item_id or "").strip()
            else:
                legacy_remote = issue.metadata.get("remote") if isinstance(issue.metadata.get("remote"), dict) else {}
                issue_provider = str(legacy_remote.get("provider") or "").strip()
                issue_remote_id = str(legacy_remote.get("issue_id") or "").strip()
            if issue_provider != provider:
                continue
            if issue_remote_id == remote_issue_id:
                matches.append(issue)
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]
        sorted_matches = sorted(issue.issue_id for issue in matches)
        raise ValueError(
            "Ambiguous proxy issue identifier: "
            f"{normalized}. Matches: {', '.join(sorted_matches)}. "
            "Use a canonical proxy issue identifier."
        )
        return None

    def _resolve_local_issue_record(self, issue_identifier: str) -> LocalIssueRecord:
        candidate = Path(issue_identifier)
        if candidate.is_file():
            issue_number = _read_issue_number_from_yaml(candidate)
            issue_key = self._resolve_local_issue_key(issue_number)
            issue = self.local_issue_store.get(issue_key) if issue_key is not None else None
            if issue is None:
                raise ValueError(f"Unknown local issue: {issue_number}")
            return issue

        issue_key = self._resolve_local_issue_key(issue_identifier)
        if issue_key is not None:
            issue = self.local_issue_store.get(issue_key)
            if issue is not None:
                return issue

        by_remote_identifier = self._find_local_issue_by_remote_identifier(issue_identifier)
        if by_remote_identifier is not None:
            return by_remote_identifier

        raise ValueError(f"Unknown local issue identifier: {issue_identifier}")

    def list_issues(
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

    def list_actionable_local_issues(
        self,
        *,
        directory_path: str,
        states: set[str] | None = None,
    ) -> list[LocalIssueRecord]:
        normalized_directory_path = self._normalize_directory_path(directory_path)
        issues = self.list_issues(directory_path=normalized_directory_path, direct_only=True)
        actionable: list[LocalIssueRecord] = []
        for issue in issues:
            if self._proxy_target_for_issue(issue) is not None:
                continue
            if issue.source_type != "local_manual":
                continue
            if states is not None and (issue.state or "New") not in states:
                continue
            actionable.append(issue)
        return actionable

    def _resolve_local_issue_key(self, identifier: str) -> str | None:
        normalized = identifier.strip()
        if not normalized:
            return None
        issues = self.local_issue_store.read_all()
        if normalized in issues:
            return normalized
        matches = [issue_id for issue_id, issue in issues.items() if issue.issue_number == normalized]
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]
        sorted_matches = sorted(matches)
        raise ValueError(
            "Ambiguous local issue identifier: "
            f"{normalized}. Matches: {', '.join(sorted_matches)}. "
            "Use a scoped issue identifier."
        )

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

    def _canonical_local_issue_id(self, issue_number: str, directory_path: str) -> str:
        return _scoped_issue_id(issue_number, directory_path=directory_path, provider="local")

    def _canonical_issue_id(self, record: LocalIssueRecord) -> str:
        remote = record.remote_reference
        if remote is not None and remote.provider == "azure_devops" and remote.work_item_id:
            return _scoped_issue_id(
                remote.work_item_id,
                directory_path=record.directory_path,
                provider="azure_devops",
                organization=remote.organization,
                project=remote.project,
            )
        return self._canonical_local_issue_id(record.issue_number, record.directory_path)

    def create_local_issue(
        self,
        *,
        title: str = "",
        body: str = "",
        repo_source: str | None = None,
        yaml_path: str | None = None,
        directory_path: str = "root",
        metadata: dict[str, Any] | None = None,
    ) -> LocalIssueRecord:
        return self.create_issue(
            title=title,
            body=body,
            repo_source=repo_source,
            yaml_path=yaml_path,
            directory_path=directory_path,
            metadata=metadata,
        )

    def update_local_issue(
        self,
        issue_identifier: str,
        *,
        title: str,
        body: str,
        state: str,
        repo_source: str | None = None,
    ) -> LocalIssueRecord:
        issue = self._resolve_local_issue_record(issue_identifier)
        if issue.source_type != "local_manual":
            raise ValueError("Only local manual issues can be edited from the admin GUI.")
        normalized_title = title.strip()
        if not normalized_title:
            raise ValueError("title is required")
        normalized_state = _normalize_local_issue_state(state, allow_empty=False)
        now = _utc_now()
        issue.title = normalized_title
        issue.body = body.strip()
        issue.state = normalized_state
        issue.repo_source = repo_source.strip() if repo_source else None
        issue.updated_at = now
        self.local_issue_store.put(issue.issue_id, issue)

        yaml_path = Path(issue.yaml_path)
        payload: dict[str, object] = {}
        if yaml_path.exists():
            try:
                loaded = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
            except Exception:
                loaded = {}
            if isinstance(loaded, dict):
                payload = dict(loaded)
        payload["issue_number"] = issue.issue_number
        payload["title"] = issue.title
        payload["body"] = issue.body
        payload["state"] = issue.state
        payload["source"] = issue.source or "local_gui"
        payload["updated_at"] = now.isoformat()
        yaml_path.parent.mkdir(parents=True, exist_ok=True)
        yaml_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
        return issue

    def create_issue(
        self,
        *,
        title: str = "",
        body: str = "",
        repo_source: str | None = None,
        yaml_path: str | None = None,
        directory_path: str = "root",
        metadata: dict[str, Any] | None = None,
    ) -> LocalIssueRecord:
        now = _utc_now()
        normalized_directory_path = self._normalize_directory_path(directory_path)
        merged_metadata: dict[str, Any] = dict(metadata or {})
        source_value = "local_gui"
        source_type = "local_manual"
        remote_reference: RemoteIssueReference | None = None
        state_value: str | None = "New"
        url_value: str | None = None
        comments_value: list[dict[str, str]] = []
        if yaml_path:
            source_path = Path(yaml_path)
            source_text = source_path.read_text(encoding="utf-8")
            try:
                yaml_payload = json.loads(json.dumps(yaml.safe_load(source_text) or {}))
            except Exception:
                yaml_payload = {}
            issue_number = _extract_issue_number_from_payload(yaml_payload) or _read_issue_number_from_yaml(source_path)
            target_yaml_path = self.local_issue_yaml_dir / f"{issue_number}.yaml"
            target_yaml_path.write_text(source_text, encoding="utf-8")
            yaml_text = target_yaml_path.read_text(encoding="utf-8")
            try:
                yaml_payload = json.loads(json.dumps(yaml.safe_load(yaml_text) or {}))
            except Exception:
                yaml_payload = {}
            raw_source = yaml_payload.get("source")
            if isinstance(raw_source, str) and raw_source.strip():
                source_value = raw_source.strip()
            elif isinstance(raw_source, dict):
                source_value = "proxy_azure_devops"
                source_type = "remote_proxy"
                merged_metadata["remote"] = {
                    "provider": "azure_devops",
                    "organization": str(raw_source.get("organization") or "").strip(),
                    "project": str(raw_source.get("project") or "").strip(),
                    "work_item_url": str(raw_source.get("work_item_url") or "").strip(),
                    "issue_id": issue_number,
                }
                remote_reference = RemoteIssueReference(
                    provider="azure_devops",
                    organization=str(raw_source.get("organization") or "").strip() or None,
                    project=str(raw_source.get("project") or "").strip() or None,
                    work_item_id=issue_number,
                    work_item_url=str(raw_source.get("work_item_url") or "").strip() or None,
                    last_synced_at=now,
                )
            if "url" in yaml_payload or "state" in yaml_payload or "comments" in yaml_payload:
                if source_value == "local_gui":
                    source_value = "proxy_azure_devops"
                source_type = "remote_proxy"
                merged_metadata["proxy_cache"] = {
                    "url": str(yaml_payload.get("url") or "").strip(),
                    "state": str(yaml_payload.get("state") or "").strip(),
                    "comments": list(yaml_payload.get("comments") or []),
                    "updated_at": str(yaml_payload.get("updated_date") or "").strip(),
                }
                merged_metadata.setdefault("remote", {"provider": "azure_devops", "issue_id": issue_number})
            state_value = str(yaml_payload.get("state") or "").strip() or None
            url_value = str(yaml_payload.get("url") or "").strip() or None
            comments_value = [
                dict(comment)
                for comment in list(yaml_payload.get("comments") or [])
                if isinstance(comment, dict)
            ]
            if not title:
                title_match = re.search(r"(?m)^\s*title\s*:\s*[\"']?(.*?)[\"']?\s*$", yaml_text)
                title = title_match.group(1).strip() if title_match else str(yaml_payload.get("title") or "").strip()
                if not title:
                    title = f"Local issue {issue_number}"
            if not body:
                body_match = re.search(r"(?m)^\s*body\s*:\s*[\"']?(.*?)[\"']?\s*$", yaml_text)
                body = body_match.group(1).strip() if body_match else str(yaml_payload.get("description") or "").strip()
        else:
            if not title.strip():
                raise ValueError("title is required when yaml_path is not provided")
            issue_number = self._next_local_issue_number()
            target_yaml_path = self.local_issue_yaml_dir / f"{issue_number}.yaml"
            yaml_text = (
                f"issue_number: {issue_number}\n"
                f"title: {json.dumps(title.strip())}\n"
                f"body: {json.dumps(body.strip())}\n"
                f"state: {json.dumps('New')}\n"
                f"source: local_gui\n"
                f"created_at: {json.dumps(now.isoformat())}\n"
            )
            target_yaml_path.write_text(yaml_text, encoding="utf-8")
        if remote_reference is not None and remote_reference.provider == "azure_devops":
            issue_id = _scoped_issue_id(
                issue_number,
                directory_path=normalized_directory_path,
                provider="azure_devops",
                organization=remote_reference.organization,
                project=remote_reference.project,
            )
        else:
            issue_id = self._canonical_local_issue_id(issue_number, normalized_directory_path)
        existing_record = self.local_issue_store.get(issue_id)
        record = LocalIssueRecord(
            issue_id=issue_id,
            issue_number=issue_number,
            title=title.strip() or f"Local issue {issue_number}",
            body=body.strip(),
            yaml_path=str(target_yaml_path),
            directory_path=normalized_directory_path,
            repo_source=repo_source.strip() if repo_source else None,
            source_type=source_type,
            remote_reference=remote_reference,
            state=(
                state_value
                if source_type == "remote_proxy"
                else _normalize_local_issue_state(state_value, allow_empty=False)
            ),
            url=url_value,
            comments=comments_value,
            source=source_value,
            metadata=merged_metadata,
            created_at=existing_record.created_at if existing_record is not None else now,
            updated_at=now,
        )
        self.local_issue_store.put(record.issue_id, record)
        return record

    def _refresh_proxy_cache_from_remote(
        self,
        issue: LocalIssueRecord,
        remote_payload: dict[str, object],
    ) -> LocalIssueRecord:
        issue.metadata = dict(issue.metadata)
        remote_comments = [dict(comment) for comment in list(remote_payload.get("comments") or []) if isinstance(comment, dict)]
        issue.metadata["proxy_cache"] = {
            "title": str(remote_payload.get("title") or issue.title),
            "description": str(remote_payload.get("description") or issue.body),
            "state": str(remote_payload.get("state") or ""),
            "url": str(remote_payload.get("url") or ""),
            "updated_at": str(remote_payload.get("updated_at") or ""),
            "comments": remote_comments,
        }
        issue.title = str(remote_payload.get("title") or issue.title).strip() or issue.title
        issue.body = str(remote_payload.get("description") or issue.body).strip() or issue.body
        issue.state = str(remote_payload.get("state") or issue.state or "").strip() or None
        issue.url = str(remote_payload.get("url") or issue.url or "").strip() or None
        issue.comments = remote_comments
        if issue.remote_reference is not None:
            issue.remote_reference.last_synced_at = _utc_now()
            issue.remote_reference.last_known_remote_state = issue.state
            issue.remote_reference.work_item_url = issue.url or issue.remote_reference.work_item_url
        issue.updated_at = _utc_now()
        self.local_issue_store.put(issue.issue_id, issue)
        return issue

    def _proxy_target_for_issue(self, issue: LocalIssueRecord) -> tuple[str, str] | None:
        remote = issue.remote_reference
        if remote is not None and remote.provider and remote.work_item_id:
            return remote.provider, remote.work_item_id
        return self.issue_service.resolve_proxy_target(
            issue_number=issue.issue_number,
            issue_source=issue.source,
            metadata=issue.metadata,
        )

    def get_issue(
        self,
        issue_id: str,
        *,
        include_remote_comments: bool = True,
        sync_proxy_cache: bool = True,
    ) -> dict[str, object]:
        issue = self._resolve_local_issue_record(issue_id)
        proxy_target = self._proxy_target_for_issue(issue)
        if proxy_target is None:
            comments = issue.comments or (
                issue.metadata.get("comments") if isinstance(issue.metadata.get("comments"), list) else []
            )
            return {
                "issue_number": issue.issue_number,
                "source_type": "local",
                "title": issue.title,
                "description": issue.body,
                "state": issue.state or str(issue.metadata.get("state") or ""),
                "comments": comments,
                "updated_at": issue.updated_at.isoformat(),
            }
        provider, remote_issue_id = proxy_target
        remote_payload = self.issue_service.read_remote_issue(
            provider=provider,
            remote_issue_id=remote_issue_id,
            directory_path=issue.directory_path,
            include_comments=include_remote_comments,
        )
        if sync_proxy_cache:
            issue = self._refresh_proxy_cache_from_remote(issue, remote_payload)
        return {
            "issue_number": issue.issue_number,
            "source_type": "proxy",
            "provider": provider,
            "remote_issue_id": remote_issue_id,
            **remote_payload,
        }

    def read_issue(
        self,
        issue_number: str,
        *,
        include_remote_comments: bool = True,
        sync_proxy_cache: bool = True,
    ) -> dict[str, object]:
        return self.get_issue(
            issue_number,
            include_remote_comments=include_remote_comments,
            sync_proxy_cache=sync_proxy_cache,
        )

    def add_issue_comment(
        self,
        issue_id: str,
        comment: str,
        *,
        sync_proxy_cache: bool = True,
    ) -> dict[str, object]:
        normalized_comment = comment.strip()
        if not normalized_comment:
            raise ValueError("comment must be non-empty")
        issue = self._resolve_local_issue_record(issue_id)
        proxy_target = self._proxy_target_for_issue(issue)
        if proxy_target is None:
            issue.metadata = dict(issue.metadata)
            comments = list(issue.comments or issue.metadata.get("comments") or [])
            comments.append({"text": normalized_comment, "created_at": _utc_now().isoformat()})
            issue.metadata["comments"] = comments
            issue.comments = comments
            issue.updated_at = _utc_now()
            self.local_issue_store.put(issue.issue_id, issue)
            return {"issue_number": issue.issue_number, "source_type": "local", "comment_added": True}

        provider, remote_issue_id = proxy_target
        result = self.issue_service.post_remote_comment(
            provider=provider,
            remote_issue_id=remote_issue_id,
            directory_path=issue.directory_path,
            comment=normalized_comment,
        )
        if sync_proxy_cache:
            remote_payload = self.issue_service.read_remote_issue(
                provider=provider,
                remote_issue_id=remote_issue_id,
                directory_path=issue.directory_path,
                include_comments=True,
            )
            self._refresh_proxy_cache_from_remote(issue, remote_payload)
        return {"issue_number": issue.issue_number, "source_type": "proxy", **result}

    def post_issue_comment(
        self,
        issue_number: str,
        comment: str,
        *,
        sync_proxy_cache: bool = True,
    ) -> dict[str, object]:
        return self.add_issue_comment(issue_number, comment, sync_proxy_cache=sync_proxy_cache)

    def set_issue_state(
        self,
        issue_id: str,
        state: str,
        *,
        sync_proxy_cache: bool = True,
    ) -> dict[str, object]:
        return self.update_issue(
            issue_id,
            state=state,
            fields=None,
            sync_proxy_cache=sync_proxy_cache,
        )

    def update_issue(
        self,
        issue_id: str,
        *,
        state: str | None = None,
        fields: dict[str, object] | None = None,
        sync_proxy_cache: bool = True,
    ) -> dict[str, object]:
        issue = self._resolve_local_issue_record(issue_id)
        proxy_target = self._proxy_target_for_issue(issue)
        if proxy_target is None:
            issue.metadata = dict(issue.metadata)
            if state is not None:
                normalized_state = _normalize_local_issue_state(state, allow_empty=False)
                issue.metadata["state"] = normalized_state
                issue.state = normalized_state
            if fields:
                local_fields = dict(issue.metadata.get("fields") or {})
                local_fields.update(fields)
                issue.metadata["fields"] = local_fields
            issue.updated_at = _utc_now()
            self.local_issue_store.put(issue.issue_id, issue)
            return {"issue_number": issue.issue_number, "source_type": "local", "updated": True}

        provider, remote_issue_id = proxy_target
        result = self.issue_service.update_remote_issue(
            provider=provider,
            remote_issue_id=remote_issue_id,
            directory_path=issue.directory_path,
            state=state,
            fields=fields,
        )
        if sync_proxy_cache:
            remote_payload = self.issue_service.read_remote_issue(
                provider=provider,
                remote_issue_id=remote_issue_id,
                directory_path=issue.directory_path,
                include_comments=True,
            )
            self._refresh_proxy_cache_from_remote(issue, remote_payload)
        return {"issue_number": issue.issue_number, "source_type": "proxy", **result}

    def claim_next_issue(
        self,
        *,
        session_id: str,
    ) -> LocalIssueRecord | None:
        session = self.runtime.sessions.get(session_id)
        if session is None:
            raise ValueError(f"Unknown session: {session_id}")
        candidates = sorted(
            self.list_actionable_local_issues(directory_path=session.directory_path, states={"Committed"}),
            key=_issue_priority_sort_key,
        )
        if not candidates:
            return None
        issue = candidates[0]
        issue.metadata = dict(issue.metadata)
        local_fields = _local_issue_fields(issue)
        local_fields["claimed_by_session_id"] = session_id
        if session.vm_id:
            local_fields["claimed_by_vm_id"] = session.vm_id
        local_fields["claimed_at"] = _utc_now().isoformat()
        issue.metadata["fields"] = local_fields
        issue.state = "In Progress"
        issue.metadata["state"] = issue.state
        issue.updated_at = _utc_now()
        self.local_issue_store.put(issue.issue_id, issue)
        return issue

    def complete_issue(
        self,
        *,
        session_id: str,
        issue_id: str,
        summary: str,
    ) -> LocalIssueRecord:
        session = self.runtime.sessions.get(session_id)
        if session is None:
            raise ValueError(f"Unknown session: {session_id}")
        normalized_summary = summary.strip()
        if not normalized_summary:
            raise ValueError("summary must be non-empty")
        issue = self._resolve_local_issue_record(issue_id)
        if self._proxy_target_for_issue(issue) is not None:
            raise ValueError("Only local issues can be completed through the issue orchestrator.")
        if issue.state != "In Progress":
            raise ValueError(f"Issue {issue.issue_number} must be In Progress before completion.")
        issue.metadata = dict(issue.metadata)
        local_fields = _local_issue_fields(issue)
        local_fields["completed_summary"] = normalized_summary
        local_fields["completed_by_session_id"] = session_id
        if session.vm_id:
            local_fields["completed_by_vm_id"] = session.vm_id
        local_fields["completed_at"] = _utc_now().isoformat()
        issue.metadata["fields"] = local_fields
        issue.state = "Completed"
        issue.metadata["state"] = issue.state
        issue.updated_at = _utc_now()
        self.local_issue_store.put(issue.issue_id, issue)
        return issue

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
        issue_input = issue
        issue_number = _resolve_issue_number(issue_input)
        local_issue: LocalIssueRecord | None = None
        if not Path(issue_input).is_file():
            try:
                local_issue = self._resolve_local_issue_record(issue_input)
                issue_number = local_issue.issue_number
                issue_input = local_issue.yaml_path
            except ValueError:
                pass
        resolved_session_id = session_id or _default_issue_session_id(issue_number)
        resolved_vm_name = f"vm-{issue_number}"
        normalized_directory_path = self._normalize_directory_path(
            local_issue.directory_path if local_issue is not None and directory_path == "root" else directory_path
        )
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

        def _issue_vm_failure(message: str) -> VmActionResult:
            preserved_vm = _dominfo_field(resolved_vm_name, "State") is not None
            if not preserved_vm:
                self.runtime.sessions.delete(resolved_session_id)
            detail = message
            if preserved_vm:
                detail = (
                    f"{detail}\n"
                    f"VM {resolved_vm_name} and session {resolved_session_id} were preserved for inspection."
                )
            return VmActionResult(
                success=False,
                vm_name=resolved_vm_name,
                action="create_issue_vm",
                message=detail,
            )

        try:
            setup_script = Path(__file__).resolve().parents[2] / "scripts" / "setup_issue_vm.sh"
            root_dir = Path(__file__).resolve().parents[2]
            vm_defaults = self._resolve_vm_defaults(normalized_directory_path)
            issue_setup = self._resolve_issue_setup(normalized_directory_path, root_dir=root_dir)
            cmd, run_env = self._build_setup_issue_vm_invocation(
                setup_script=setup_script,
                issue=issue_input,
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
        except Exception as exc:
            return _issue_vm_failure(str(exc) or f"Failed to create VM {resolved_vm_name}")

        message = (completed.stdout or completed.stderr or "").strip()
        if completed.returncode != 0:
            return _issue_vm_failure(message or f"setup_issue_vm.sh exited with code {completed.returncode}")
        return VmActionResult(
            success=True,
            vm_name=resolved_vm_name,
            action="create_issue_vm",
            message=message or f"Created issue VM {resolved_vm_name}",
        )

    def create_issue_vm_from_local_issue(
        self,
        issue_identifier: str,
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
        try:
            issue_key = self._resolve_local_issue_key(issue_identifier)
        except ValueError as exc:
            return VmActionResult(
                success=False,
                vm_name=f"vm-{issue_identifier}",
                action="create_issue_vm",
                message=str(exc),
            )
        issue = self.local_issue_store.get(issue_key) if issue_key is not None else None
        if issue is None:
            return VmActionResult(
                success=False,
                vm_name=f"vm-{issue_identifier}",
                action="create_issue_vm",
                message=f"Unknown local issue: {issue_identifier}",
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

    def create_vm(
        self,
        *,
        vm_name: str,
        session_id: str | None = None,
        repo_source: str | None = None,
        vm_user: str = "agent",
        ttl_hours: int | None = None,
        discord_channels: list[str] | None = None,
        email_mailboxes: list[str] | None = None,
        email_folders: list[str] | None = None,
        python: bool = False,
        dotnet: bool = False,
        nuget: bool = False,
        mssql: bool = False,
        mssql_init_root: str | None = None,
        playwright: bool = False,
        directory_path: str = "root",
    ) -> VmActionResult:
        resolved_vm_name = vm_name.strip()
        if not resolved_vm_name:
            return VmActionResult(success=False, vm_name="", action="create_vm", message="VM name is required.")
        if _dominfo_field(resolved_vm_name, "State") is not None:
            return VmActionResult(
                success=False,
                vm_name=resolved_vm_name,
                action="create_vm",
                message=f"VM already exists: {resolved_vm_name}",
            )

        normalized_directory_path = self._normalize_directory_path(directory_path)
        policy = self.runtime.policy_config.resolve_for_directory(normalized_directory_path)
        resolved_session_id = session_id.strip() if session_id and session_id.strip() else _default_vm_session_id(resolved_vm_name)
        if self.runtime.sessions.get(resolved_session_id) is not None:
            return VmActionResult(
                success=False,
                vm_name=resolved_vm_name,
                action="create_vm",
                message=f"Session already exists: {resolved_session_id}",
            )
        if python and not (repo_source or "").strip():
            return VmActionResult(
                success=False,
                vm_name=resolved_vm_name,
                action="create_vm",
                message="repo_source is required when Python bootstrap is enabled.",
            )
        if mssql and not os.environ.get("MSSQL_SA_PASSWORD", "").strip():
            return VmActionResult(
                success=False,
                vm_name=resolved_vm_name,
                action="create_vm",
                message="MSSQL_SA_PASSWORD must be set when MSSQL bootstrap is enabled.",
            )

        manifest = _build_issue_session_manifest(
            policy=policy,
            session_id=resolved_session_id,
            vm_id=resolved_vm_name,
            issue_number=None,
            directory_path=normalized_directory_path,
            ttl_hours=ttl_hours,
            discord_channels=discord_channels or [],
            email_mailboxes=email_mailboxes or [],
            email_folders=email_folders or [],
        )
        self.runtime.sessions.put(resolved_session_id, manifest)

        root_dir = Path(__file__).resolve().parents[2]
        vm_defaults = self._resolve_vm_defaults(normalized_directory_path)
        issue_setup = self._resolve_issue_setup(normalized_directory_path, root_dir=root_dir)
        setup_script = root_dir / "scripts" / "setup_vm.sh"
        python_script = root_dir / "scripts" / "bootstrap_python_vm.sh"
        run_env = os.environ.copy()
        run_env.update(vm_defaults.guest_environment())

        try:
            resolved_repo_path: str | None = None
            resolved_repo_name: str | None = None
            guest_repo_root: str | None = None
            vm_ip: str | None = None
            helper = self._privileged_helper if self.use_privileged_helper else None
            if helper is not None and not helper.is_running():
                helper.start()
            setup_args = []
            if dotnet:
                setup_args.append("--dotnet")
            if nuget:
                setup_args.append("--nuget")
            if mssql:
                setup_args.append("--mssql")
                if mssql_init_root is not None:
                    setup_args.extend(["--mssql-init-root", mssql_init_root])
            if playwright:
                setup_args.append("--playwright")
            setup_args.extend([resolved_vm_name, resolved_session_id, vm_user])
            if helper is not None:
                response = helper.call(
                    "run_script",
                    script_path=str(setup_script),
                    args=setup_args,
                    env=run_env,
                )
                message = _combined_process_message(
                    str(response.get("stdout") or ""),
                    str(response.get("stderr") or ""),
                )
            else:
                launch_step = _pkexec_env_command(
                    [str(setup_script), *setup_args],
                    run_env,
                    (
                        "SSH_PUBLIC_KEY_FILE",
                        "SSH_PRIVATE_KEY_PATH",
                        "GATEWAY_URL",
                        "LLM_BASE_URL",
                        "NUGET_RELAY_BASE_URL",
                        "AGENT_SANDBOX_RUNTIME_DIR",
                        "VM_STORAGE_DIR",
                        "SHARED_NUGET_CACHE_DIR",
                        "SHARED_NUGET_CACHE_TAG",
                        "SHARED_NUGET_CACHE_MODE",
                        "RAM_MB",
                        "VCPUS",
                        "DISK_SIZE_GB",
                        "VM_WAIT_IP_TIMEOUT_SECONDS",
                        "VM_WAIT_SSH_TIMEOUT_SECONDS",
                        "MSSQL_SA_PASSWORD",
                    ),
                )
                completed = _run_subprocess(launch_step, capture_output=True, text=True, env=run_env)
                message = _combined_process_message(completed.stdout, completed.stderr)
                if completed.returncode != 0:
                    raise RuntimeError(message or f"{setup_script.name} exited with code {completed.returncode}")
            messages: list[str] = []
            if message:
                messages.append(message)
            if helper is not None:
                vm_ip = str(helper.call("virsh_domifaddr", vm_name=resolved_vm_name).get("ip") or "")
            else:
                vm_ip = _resolve_vm_ip(resolved_vm_name)
            if vm_ip:
                run_env["AGENT_SANDBOX_VM_IP"] = vm_ip

            if (repo_source or "").strip():
                resolved_repo_path, resolved_repo_name = self._resolve_repo_source(repo_source.strip())
                self._sync_repo_into_bare_remote(resolved_repo_name, resolved_repo_path, issue_setup=issue_setup)
                guest_repo_root = self._clone_repo_into_vm(
                    vm_name=resolved_vm_name,
                    vm_user=vm_user,
                    session_id=resolved_session_id,
                    repo_name=resolved_repo_name,
                    issue_setup=issue_setup,
                    vm_ip=vm_ip,
                )
            if python:
                if not resolved_repo_path or not guest_repo_root:
                    raise RuntimeError("Python bootstrap requires a resolved guest repo checkout.")
                python_run_env = run_env | {
                    "HOST_PYTHON_BIN": _resolve_host_python_with_pip(),
                }
                completed = _run_subprocess(
                    [str(python_script), "--vm-user", vm_user, resolved_vm_name, resolved_repo_path, guest_repo_root],
                    capture_output=True,
                    text=True,
                    env=python_run_env,
                )
                message = _combined_process_message(completed.stdout, completed.stderr)
                if completed.returncode != 0:
                    raise RuntimeError(message or f"{python_script.name} exited with code {completed.returncode}")
                if message:
                    messages.append(message)
        except Exception as exc:
            preserved_vm = _dominfo_field(resolved_vm_name, "State") is not None
            if not preserved_vm:
                self.runtime.sessions.delete(resolved_session_id)
            detail = f"Failed to create VM {resolved_vm_name}: {exc}"
            if preserved_vm:
                detail = (
                    f"{detail}\n"
                    f"VM {resolved_vm_name} and session {resolved_session_id} were preserved for inspection."
                )
            return VmActionResult(
                success=False,
                vm_name=resolved_vm_name,
                action="create_vm",
                message=detail,
            )

        summary = messages[-1] if messages else f"Created VM {resolved_vm_name}"
        return VmActionResult(success=True, vm_name=resolved_vm_name, action="create_vm", message=summary)

    def destroy_vm(self, target: str) -> VmActionResult:
        vm_name = target.strip()
        if not vm_name:
            return VmActionResult(success=False, vm_name="", action="destroy_vm", message="VM name is required.")
        issue_number = _extract_issue_number(vm_name)
        if issue_number is not None and vm_name == f"vm-{issue_number}":
            return self.destroy_issue_vm(issue_number)

        root_dir = Path(__file__).resolve().parents[2]
        destroy_script = root_dir / "scripts" / "destroy_agent_vm.sh"
        helper = self._privileged_helper if self.use_privileged_helper else None
        if helper is not None and not helper.is_running():
            helper.start()
        if helper is not None:
            response = helper.call("destroy_vm", script_path=str(destroy_script), vm_name=vm_name)
            message = str(response.get("stdout") or response.get("stderr") or "").strip()
        else:
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
                    action="destroy_vm",
                    message=message or f"destroy-vm exited with code {completed.returncode}",
                )

        sessions = self.runtime.sessions.read_all()
        session_ids = _matching_vm_session_ids(vm_name, sessions)
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
        summary = (
            f"Destroyed VM {vm_name}; "
            f"removed_sessions={len(session_ids)} "
            f"removed_pr_map_entries={removed_pr_map_entries} "
            f"removed_approvals={removed_approvals} "
            f"removed_feedback_cursors={removed_cursors} "
            f"removed_feedback_items={removed_feedback}"
        )
        if message:
            summary = f"{message}\n{summary}"
        return VmActionResult(success=True, vm_name=vm_name, action="destroy_vm", message=summary)

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

    def delete_local_issue(self, issue_identifier: str) -> VmActionResult:
        try:
            issue_key = self._resolve_local_issue_key(issue_identifier)
        except ValueError as exc:
            return VmActionResult(
                success=False,
                vm_name=f"vm-{issue_identifier}",
                action="delete_local_issue",
                message=str(exc),
            )
        issue = self.local_issue_store.get(issue_key) if issue_key is not None else None
        if issue is None:
            return VmActionResult(
                success=False,
                vm_name=f"vm-{issue_identifier}",
                action="delete_local_issue",
                message=f"Unknown local issue: {issue_identifier}",
            )
        issue_number = issue.issue_number

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

        self.local_issue_store.delete(issue.issue_id)
        yaml_path = Path(issue.yaml_path)
        if yaml_path.exists():
            yaml_path.unlink()
        return VmActionResult(
            success=True,
            vm_name=vm_name,
            action="delete_local_issue",
            message=f"Deleted local issue {issue_number}",
        )
