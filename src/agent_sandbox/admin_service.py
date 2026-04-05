from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import fcntl
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, TypeAdapter, ValidationError
import yaml

from .agent_control import AgentControlStore
from .bootstrap import load_host_bootstrap_config, load_runtime_gateway_config, resolve_project_dir
from .config import (
    AdoWorkerGlobalConfig,
    DiscordIngestWorkerGlobalConfig,
    GatewayConfig,
    HostHeartbeatWorkerGlobalConfig,
    IssueOrchestratorWorkerGlobalConfig,
    TriggerOrchestratorWorkerGlobalConfig,
)
from .directory_admin import DirectoryAdminService
from .directory_config import (
    DirectoryConfigResolver,
    DirectoryConfigStore,
    get_domain_base_config,
    validate_effective_domain_payload,
)
from .directories import DirectoryService, DirectoryStore
from .host_config import DEFAULT_RUNTIME_DIR
from .bootstrap import resolve_runtime_dir
from .ownership import DirectoryOwnershipQueries
from .policy_config import EffectivePolicyConfig
from .provider_config import ProviderConfigResolver
from .issue_service import IssueService
from .vm_defaults_config import EffectiveVmDefaultsConfig, VmDefaultsConfigResolver
from .models import (
    ApprovalDecisionRecord,
    ApprovalQueueHealth,
    ApprovalRequest,
    ApprovalStatus,
    DirectoryPath,
    LocalIssueRecord,
    RemoteIssueReference,
    SessionActionResult,
    SessionManifest,
    SessionSummary,
    ToolName,
    VmActionResult,
    VmSummary,
    WakeScheduledRequest,
    WakeScheduleDefinition,
    WorkerActionResult,
    WorkerSummary,
)
from .runtime import RuntimeContext, build_runtime
from .storage import JsonStore
from .events.models import EffectDefinitionDiscriminated, TriggerDefinitionDiscriminated

_WORKER_DEFINITIONS: tuple[tuple[str, str, str], ...] = (
    ("ado", "ADO Worker", "agent-sandbox-ado-worker.service"),
    ("discord-ingest", "Discord Ingest Worker", "agent-sandbox-discord-ingest.service"),
    ("host-heartbeat", "Host Heartbeat Worker", "agent-sandbox-host-heartbeat.service"),
    ("issue-orchestrator", "Issue Orchestrator Worker", "agent-sandbox-issue-orchestrator.service"),
    ("trigger-orchestrator", "Trigger Orchestrator Worker", "agent-sandbox-trigger-orchestrator.service"),
)

_WORKER_GLOBAL_CONFIG_PATHS: dict[str, str] = {
    "ado": "config/ado-worker/config.json",
    "discord-ingest": "config/discord-ingest/config.json",
    "host-heartbeat": "config/host-heartbeat/config.json",
    "issue-orchestrator": "config/issue-orchestrator/config.json",
    "trigger-orchestrator": "config/trigger-orchestrator/config.json",
}

_WORKER_GLOBAL_CONFIG_MODELS: dict[str, type[BaseModel]] = {
    "ado": AdoWorkerGlobalConfig,
    "discord-ingest": DiscordIngestWorkerGlobalConfig,
    "host-heartbeat": HostHeartbeatWorkerGlobalConfig,
    "issue-orchestrator": IssueOrchestratorWorkerGlobalConfig,
    "trigger-orchestrator": TriggerOrchestratorWorkerGlobalConfig,
}
ISSUE_STATES: tuple[str, ...] = ("New", "Approved", "Committed", "In Progress", "Completed", "Closed")
_ISSUE_STATE_SET = set(ISSUE_STATES)
_ISSUE_PRIORITY_ORDER: dict[str, int] = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
_APPROVAL_STATUS_ORDER: dict[ApprovalStatus, int] = {
    ApprovalStatus.PENDING: 0,
    ApprovalStatus.EXPIRED: 1,
    ApprovalStatus.DENIED: 2,
    ApprovalStatus.APPROVED: 3,
}
_SENSITIVE_PAYLOAD_MARKERS: tuple[str, ...] = (
    "token",
    "secret",
    "password",
    "pat",
    "authorization",
    "cookie",
    "private_key",
    "ssh_key",
)


class EffectiveIssueSetupConfig(BaseModel):
    git_root: str
    git_host: str
    git_user: str
    git_ssh_alias: str
    default_model: str | None = None
    post_exec_verification_prompt: str | None = None
    enable_guest_heartbeat: bool = False
    guest_heartbeat_prompt: str = (
        "Resume work for this session. Check for pending feedback first, then continue the most recent task."
    )
    issue_setup_azure_devops_pr_pat_env: str = "AZURE_DEVOPS_PR_PAT"


def resolve_effective_issue_setup_config(
    *,
    config,
    runtime: RuntimeContext,
    directory_path: str,
    root_dir: Path,
) -> EffectiveIssueSetupConfig:
    resolver = DirectoryConfigResolver(
        DirectoryService(runtime.directories),
        base_gateway_config=config,
        config_store=DirectoryConfigStore(runtime.directories, runtime_dir=config.runtime_dir),
    )
    resolution = resolver.resolve_domain(
        directory_path,
        domain="issue_setup",
        base_config=get_domain_base_config(config, "issue_setup"),
        validator=lambda candidate: validate_effective_domain_payload("issue_setup", candidate),
    )
    effective = resolution.effective_config
    provider_config = ProviderConfigResolver(
        config,
        directory_store=runtime.directories,
        session_store=runtime.sessions,
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
            "default_model": str(effective.get("default_model") or "").strip() or None,
            "post_exec_verification_prompt": effective.get("post_exec_verification_prompt"),
            "enable_guest_heartbeat": bool(effective.get("enable_guest_heartbeat", False)),
            "guest_heartbeat_prompt": str(
                effective.get("guest_heartbeat_prompt")
                or "Resume work for this session. Check for pending feedback first, then continue the most recent task."
            ),
            "issue_setup_azure_devops_pr_pat_env": issue_setup_pat_env,
        }
    )


def resolve_agent_model_for_manifest(
    session: SessionManifest,
    *,
    issue_setup: EffectiveIssueSetupConfig,
) -> str:
    session_id = session.session_id
    directory_path = session.directory_path
    session_model = str(session.model or "").strip()
    if session_model:
        return session_model
    default_model = str(issue_setup.default_model or "").strip()
    if default_model:
        return default_model
    raise ValueError(
        f"Cannot resolve AGENT_MODEL for session '{session_id}' in directory '{directory_path}': "
        "missing session.model and missing issue_setup.default_model. "
        f"Remediation: set session.model for session '{session_id}' or configure "
        f"issue_setup.default_model for directory '{directory_path}' (or an inherited parent)."
    )


class WorkerGlobalConfigPayload(BaseModel):
    worker_id: str
    path: str
    exists: bool
    config: dict[str, object]


class WorkerGlobalConfigWritePayload(BaseModel):
    worker_id: str
    path: str
    config: dict[str, object]


class TriggerConfigMutationPayload(BaseModel):
    worker_id: str = "trigger-orchestrator"
    trigger_id: str
    operation: str
    path: str
    config: dict[str, object]


class EffectConfigMutationPayload(BaseModel):
    worker_id: str = "trigger-orchestrator"
    effect_id: str
    operation: str
    path: str
    config: dict[str, object]


_TRIGGER_DEFINITION_ADAPTER = TypeAdapter(TriggerDefinitionDiscriminated)
_EFFECT_DEFINITION_ADAPTER = TypeAdapter(EffectDefinitionDiscriminated)


def _safe_validation_error(exc: ValidationError, *, context: str) -> ValueError:
    details: list[str] = []
    for item in exc.errors(include_input=False):
        location = ".".join(str(part) for part in item.get("loc", ()))
        message = str(item.get("msg", "invalid value"))
        details.append(f"{location}: {message}" if location else message)
    suffix = "; ".join(details) if details else "invalid value"
    return ValueError(f"{context}: {suffix}")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _normalize_resume_mode(value: str | None) -> str:
    normalized = str(value or "").strip() or "same_context"
    if normalized not in {"same_context", "new_context"}:
        raise ValueError("resume_mode must be one of: same_context, new_context")
    return normalized


def _require_valid_cron(cron: str) -> str:
    normalized = " ".join(part.strip() for part in str(cron).split())
    fields = normalized.split(" ")
    if len(fields) != 5:
        raise ValueError(
            "Invalid cron expression. Expected 5 fields (minute hour day month weekday), "
            "for example: '*/15 * * * *'."
        )
    ranges = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))
    for index, token in enumerate(fields):
        lower, upper = ranges[index]
        for chunk in token.split(","):
            part = chunk.strip()
            if not part:
                raise ValueError(f"Invalid cron field #{index + 1}: empty value.")
            if part == "*":
                continue
            if part.startswith("*/"):
                step_text = part[2:]
                if not step_text.isdigit() or int(step_text) <= 0:
                    raise ValueError(f"Invalid cron field #{index + 1}: invalid step value {part!r}.")
                continue
            if not part.isdigit():
                raise ValueError(f"Invalid cron field #{index + 1}: expected integer, '*', or step, got {part!r}.")
            number = int(part)
            if number < lower or number > upper:
                raise ValueError(
                    f"Invalid cron field #{index + 1}: value {number} out of range {lower}-{upper}."
                )
    return normalized


def _parse_cron_field(token: str, lower: int, upper: int) -> set[int] | None:
    normalized = token.strip()
    if not normalized:
        return None
    if normalized == "*":
        return set(range(lower, upper + 1))
    values: set[int] = set()
    for chunk in normalized.split(","):
        part = chunk.strip()
        if not part:
            return None
        if part.startswith("*/"):
            try:
                step = int(part[2:])
            except ValueError:
                return None
            if step <= 0:
                return None
            values.update(range(lower, upper + 1, step))
            continue
        try:
            value = int(part)
        except ValueError:
            return None
        if value < lower or value > upper:
            return None
        values.add(value)
    return values


def _next_cron_time(expression: str, base: datetime) -> datetime | None:
    fields = expression.split()
    if len(fields) != 5:
        return None
    minute_values = _parse_cron_field(fields[0], 0, 59)
    hour_values = _parse_cron_field(fields[1], 0, 23)
    day_values = _parse_cron_field(fields[2], 1, 31)
    month_values = _parse_cron_field(fields[3], 1, 12)
    weekday_values = _parse_cron_field(fields[4], 0, 6)
    if any(value is None for value in (minute_values, hour_values, day_values, month_values, weekday_values)):
        return None
    assert minute_values is not None
    assert hour_values is not None
    assert day_values is not None
    assert month_values is not None
    assert weekday_values is not None
    candidate = base.astimezone(UTC).replace(second=0, microsecond=0) + timedelta(minutes=1)
    deadline = candidate + timedelta(days=366)
    while candidate <= deadline:
        cron_weekday = (candidate.weekday() + 1) % 7
        if (
            candidate.minute in minute_values
            and candidate.hour in hour_values
            and candidate.day in day_values
            and candidate.month in month_values
            and cron_weekday in weekday_values
        ):
            return candidate
        candidate += timedelta(minutes=1)
    return None


def _cron_min_interval_seconds(expression: str) -> float | None:
    base = _utc_now().replace(second=0, microsecond=0) - timedelta(minutes=1)
    first = _next_cron_time(expression, base)
    if first is None:
        return None
    second = _next_cron_time(expression, first)
    if second is None:
        return None
    return (second - first).total_seconds()


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


def _issue_number_sort_key(issue: LocalIssueRecord) -> tuple[int, int | str, str]:
    normalized = issue.issue_number.strip()
    if normalized.isdigit():
        return (0, int(normalized), issue.issue_id)
    return (1, normalized, issue.issue_id)


def _approval_sort_key(request: ApprovalRequest) -> tuple[int, datetime, str]:
    return (_APPROVAL_STATUS_ORDER[request.status], request.created_at, request.request_id)


def _redact_sensitive_payload(value: Any, *, key_name: str | None = None) -> Any:
    normalized_key = (key_name or "").strip().lower()
    if normalized_key and any(marker in normalized_key for marker in _SENSITIVE_PAYLOAD_MARKERS):
        return "<redacted>"
    if isinstance(value, dict):
        return {str(key): _redact_sensitive_payload(item, key_name=str(key)) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_sensitive_payload(item) for item in value]
    return value


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
    matching_ids.update(_matching_vm_session_ids(normalized, sessions))

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


def _copilot_host_auth_enabled() -> bool:
    return str(os.environ.get("AGENT_PROVIDER", "")).strip().lower() == "copilot"


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
        allowed_tools.extend(
            [
                ToolName.DISCORD_GET_COMMANDS,
                ToolName.DISCORD_POST_STATUS,
                ToolName.HAS_PENDING_DISCORD_MESSAGES,
                ToolName.LIST_UNREAD_DISCORD_MESSAGES,
                ToolName.LIST_ALL_DISCORD_MESSAGES,
            ]
        )

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

    metadata = {"issue_number": issue_number} if issue_number is not None else {}
    if _copilot_host_auth_enabled():
        allowed_tools.append(ToolName.COPILOT_HOST_AUTH)
        metadata["agent_provider"] = "copilot"

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
        metadata=metadata,
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
        self.local_issue_create_lock_path = self.config.runtime_dir / "local-issue-create.lock"
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
        return resolve_effective_issue_setup_config(
            config=self.config,
            runtime=self.runtime,
            directory_path=directory_path,
            root_dir=root_dir,
        )

    def _resolve_agent_model_for_session(self, session_or_id: SessionManifest | str) -> str:
        if isinstance(session_or_id, SessionManifest):
            session = session_or_id
            session_id = session.session_id
        else:
            session_id = str(session_or_id).strip()
            if not session_id:
                raise ValueError(
                    "Cannot resolve AGENT_MODEL: missing session id. Provide a valid session id or SessionManifest."
                )
            session = self.runtime.sessions.get(session_id)
            if session is None:
                raise ValueError(
                    f"Cannot resolve AGENT_MODEL for session '{session_id}': session not found. "
                    "Remediation: create the session first or pass a SessionManifest with session.model set."
                )
        issue_setup = self._resolve_issue_setup(session.directory_path, root_dir=self.project_dir)
        return resolve_agent_model_for_manifest(session, issue_setup=issue_setup)

    def resolve_agent_model_for_target(self, target: str) -> str:
        sessions = self.runtime.sessions.read_all()
        resolved_session_id, _ = _resolve_session_target(target, sessions)
        return self._resolve_agent_model_for_session(resolved_session_id)

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
        agent_model = self._resolve_agent_model_for_session(session_id)
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
                "AGENT_MODEL": agent_model,
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
        issue_number: str | None = None,
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
        issue_config_line = ""
        if issue_number:
            issue_config_line = f'git -C "${{TARGET_DIR}}" config agent-sandbox.issueNumber "{issue_number}"\n'
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
{issue_config_line}git -C "${{TARGET_DIR}}" config alias.push-agent '!f(){{ session_id=${{SESSION_ID:-$(git config --get agent-sandbox.sessionId || true)}}; if [ -z "${{session_id}}" ]; then echo "SESSION_ID is not set; refusing ambiguous agent push." >&2; return 1; fi; expected_issue_number=$(git config --get agent-sandbox.issueNumber || true); if [ -n "${{expected_issue_number}}" ]; then case "${{session_id}}" in issue-${{expected_issue_number}}|issue-${{expected_issue_number}}-*) ;; *) echo "SESSION_ID (${{session_id}}) does not match expected issue ${{expected_issue_number}}; refusing agent push." >&2; return 1 ;; esac; fi; branch=$(git rev-parse --abbrev-ref HEAD); git push agent HEAD:refs/heads/agents/${{session_id}}/${{branch}}; }}; f'
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

    def list_wake_schedules(
        self,
        *,
        session_id: str | None = None,
        include_disabled: bool = True,
    ) -> list[dict[str, Any]]:
        payload = self.runtime.wake_store.read_raw()
        raw_requests = payload.get("requests")
        raw_dispatches = payload.get("dispatches")
        if not isinstance(raw_requests, dict):
            return []
        latest_dispatch_by_request_id: dict[str, dict[str, Any]] = {}
        if isinstance(raw_dispatches, dict):
            for raw_value in raw_dispatches.values():
                if not isinstance(raw_value, dict):
                    continue
                dispatch_request_id = str(raw_value.get("request_id") or "").strip()
                if not dispatch_request_id:
                    continue
                dispatched_at = str(raw_value.get("dispatched_at") or "")
                current = latest_dispatch_by_request_id.get(dispatch_request_id)
                if current is None or dispatched_at > str(current.get("dispatched_at") or ""):
                    latest_dispatch_by_request_id[dispatch_request_id] = raw_value

        schedules: list[dict[str, Any]] = []
        for raw_value in raw_requests.values():
            if not isinstance(raw_value, dict):
                continue
            request = WakeScheduledRequest.model_validate(raw_value)
            if session_id and request.session_id != session_id:
                continue
            if not include_disabled and not request.enabled:
                continue
            dispatch = latest_dispatch_by_request_id.get(request.request_id)
            schedules.append(
                {
                    "request_id": request.request_id,
                    "session_id": request.session_id,
                    "resume_at": request.schedule.resume_at,
                    "cron": request.schedule.cron,
                    "prompt": request.resume_prompt.prompt,
                    "resume_mode": request.resume_mode,
                    "enabled": request.enabled,
                    "source": request.source,
                    "created_at": request.created_at,
                    "updated_at": request.updated_at,
                    "last_dispatched_at": request.last_dispatched_at,
                    "next_run_at": request.next_run_at,
                    "last_dispatch": (
                        {
                            "dispatch_id": dispatch.get("dispatch_id"),
                            "status": dispatch.get("status"),
                            "error": dispatch.get("error"),
                            "dispatched_at": dispatch.get("dispatched_at"),
                        }
                        if dispatch is not None
                        else None
                    ),
                }
            )
        schedules.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return schedules

    def _active_wake_requests(self, *, exclude_request_id: str | None = None) -> list[WakeScheduledRequest]:
        payload = self.runtime.wake_store.read_raw()
        raw_requests = payload.get("requests", {})
        if not isinstance(raw_requests, dict):
            return []
        active: list[WakeScheduledRequest] = []
        for raw_value in raw_requests.values():
            if not isinstance(raw_value, dict):
                continue
            request = WakeScheduledRequest.model_validate(raw_value)
            if exclude_request_id and request.request_id == exclude_request_id:
                continue
            if request.enabled:
                active.append(request)
        return active

    def _enforce_wake_schedule_guardrails(
        self,
        *,
        session_id: str,
        schedule: WakeScheduleDefinition,
        enabled: bool,
        exclude_request_id: str | None = None,
    ) -> None:
        if not enabled:
            return
        active_requests = self._active_wake_requests(exclude_request_id=exclude_request_id)
        max_active_per_session = self.config.host_heartbeat_schedule_max_active_per_session
        if max_active_per_session is not None:
            active_for_session = sum(1 for request in active_requests if request.session_id == session_id)
            if active_for_session >= max_active_per_session:
                raise ValueError(
                    "Wake schedule limit reached for session "
                    f"{session_id}: max_active_per_session={max_active_per_session}"
                )
        max_active_total = self.config.host_heartbeat_schedule_max_active_total
        if max_active_total is not None and len(active_requests) >= max_active_total:
            raise ValueError(f"Wake schedule limit reached: max_active_total={max_active_total}")
        min_interval_seconds = self.config.host_heartbeat_schedule_min_interval_seconds
        cron = str(schedule.cron or "").strip()
        if min_interval_seconds is not None and cron:
            actual_interval_seconds = _cron_min_interval_seconds(cron)
            if actual_interval_seconds is None:
                raise ValueError(f"Unable to determine cron interval for schedule: {cron}")
            if actual_interval_seconds < min_interval_seconds:
                raise ValueError(
                    "Recurring wake schedule interval is too short: "
                    f"minimum={min_interval_seconds:g}s actual={actual_interval_seconds:g}s"
                )

    def create_wake_schedule(
        self,
        *,
        session_id: str,
        prompt: str,
        resume_at: datetime | None = None,
        cron: str | None = None,
        resume_mode: str = "same_context",
        enabled: bool = True,
        request_id: str | None = None,
        source: str | None = "admin_cli",
        metadata: dict[str, Any] | None = None,
    ) -> WakeScheduledRequest:
        normalized_prompt = str(prompt or "").strip()
        if not normalized_prompt:
            raise ValueError("prompt cannot be empty")
        normalized_resume_mode = _normalize_resume_mode(resume_mode)
        normalized_source = str(source or "admin_cli").strip() or "admin_cli"
        if normalized_source.startswith("mcp") and normalized_resume_mode != "same_context":
            raise ValueError("MCP scheduling only supports resume_mode='same_context'.")
        normalized_cron = None
        if cron is not None and str(cron).strip():
            normalized_cron = _require_valid_cron(str(cron))
        schedule = WakeScheduleDefinition(resume_at=resume_at, cron=normalized_cron)
        normalized_session_id = session_id.strip()
        self._enforce_wake_schedule_guardrails(
            session_id=normalized_session_id,
            schedule=schedule,
            enabled=bool(enabled),
        )
        return self.runtime.wake_store.schedule_resume_request(
            session_id=normalized_session_id,
            prompt=normalized_prompt,
            schedule=schedule,
            request_id=request_id.strip() if isinstance(request_id, str) and request_id.strip() else None,
            source=normalized_source,
            resume_mode=normalized_resume_mode,
            enabled=bool(enabled),
            metadata=metadata or {},
        )

    def edit_wake_schedule(
        self,
        request_id: str,
        *,
        resume_at: datetime | None = None,
        cron: str | None = None,
        prompt: str | None = None,
        resume_mode: str | None = None,
        enabled: bool | None = None,
    ) -> WakeScheduledRequest:
        existing = self.runtime.wake_store.get_request(request_id.strip())
        if existing is None:
            raise ValueError(f"Wake schedule not found: {request_id}")
        updated_prompt = existing.resume_prompt.prompt if prompt is None else str(prompt).strip()
        if not updated_prompt:
            raise ValueError("prompt cannot be empty")
        updated_mode = existing.resume_mode if resume_mode is None else _normalize_resume_mode(resume_mode)
        if str(existing.source or "").strip().startswith("mcp") and updated_mode != "same_context":
            raise ValueError("MCP scheduling only supports resume_mode='same_context'.")
        resume_changed = resume_at is not None
        cron_changed = cron is not None
        if resume_changed and cron_changed:
            raise ValueError("Provide only one of resume_at or cron when editing a schedule.")
        if resume_changed:
            updated_schedule = WakeScheduleDefinition(resume_at=resume_at, cron=None)
        elif cron_changed:
            normalized_cron = _require_valid_cron(str(cron)) if str(cron).strip() else None
            updated_schedule = WakeScheduleDefinition(resume_at=None, cron=normalized_cron)
        else:
            updated_schedule = existing.schedule

        updated_enabled = existing.enabled if enabled is None else bool(enabled)
        self._enforce_wake_schedule_guardrails(
            session_id=existing.session_id,
            schedule=updated_schedule,
            enabled=updated_enabled,
            exclude_request_id=existing.request_id,
        )
        existing.schedule = updated_schedule
        existing.resume_prompt.prompt = updated_prompt
        existing.resume_mode = updated_mode
        if enabled is not None:
            existing.enabled = bool(enabled)
        existing.updated_at = _utc_now()
        if existing.schedule.resume_at is not None:
            existing.next_run_at = existing.schedule.resume_at
        elif cron_changed:
            existing.next_run_at = _utc_now()
        self.runtime.wake_store.upsert_request(existing)
        return existing

    def delete_wake_schedule(self, request_id: str) -> bool:
        normalized = request_id.strip()
        if not normalized:
            raise ValueError("request_id cannot be empty")
        return self.runtime.wake_store.delete_request(normalized)

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

    def set_session_allowed_tools(self, target: str, allowed_tools: list[str | ToolName]) -> SessionActionResult:
        session_id, manifest = _resolve_session_target(target, self.runtime.sessions.read_all())
        normalized_tools: list[ToolName] = []
        seen: set[ToolName] = set()
        for tool in allowed_tools:
            normalized_tool = tool if isinstance(tool, ToolName) else ToolName(str(tool).strip())
            if normalized_tool in seen:
                continue
            seen.add(normalized_tool)
            normalized_tools.append(normalized_tool)
        manifest.allowed_tools = normalized_tools
        self.runtime.sessions.put(session_id, manifest)
        return SessionActionResult(
            success=True,
            session_id=session_id,
            action="set_session_allowed_tools",
            message=f"Updated allowed tools for session {session_id}",
        )

    def set_session_model(self, target: str, model: str) -> SessionActionResult:
        session_id, manifest = _resolve_session_target(target, self.runtime.sessions.read_all())
        normalized_model = str(model).strip()
        if not normalized_model:
            raise ValueError("model must be non-empty")
        manifest.model = normalized_model
        self.runtime.sessions.put(session_id, manifest)
        return SessionActionResult(
            success=True,
            session_id=session_id,
            action="set_session_model",
            message=f"Updated model for session {session_id}",
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

    def _run_script(self, script_name: str, args: list[str], *, env: dict[str, str] | None = None) -> VmActionResult:
        script_path = Path(__file__).resolve().parents[2] / "scripts" / script_name
        completed = _run_subprocess([str(script_path), *args], capture_output=True, text=True, env=env)
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
        sessions = self.runtime.sessions.read_all()
        vm_name = _resolve_vm_name_from_target(target, sessions)
        try:
            _, manifest = _resolve_session_target(target, sessions)
            model = self._resolve_agent_model_for_session(manifest)
        except ValueError as exc:
            return VmActionResult(
                success=False,
                vm_name=vm_name,
                action="resume_issue_agent",
                message=str(exc),
            )
        return self._run_script(
            "resume_issue_agent.sh",
            ["--prompt", prompt, "--vm-name", vm_name, "--vm-user", vm_user, "--host-control-state-path", str(self.config.host_agent_control_state_path)],
            env=os.environ | {"AGENT_MODEL": model},
        )

    def list_local_issues(
        self,
        *,
        directory_path: str | None = None,
        direct_only: bool = False,
        states: set[str] | None = None,
        exclude_states: set[str] | None = None,
    ) -> list[LocalIssueRecord]:
        return self.list_issues(
            directory_path=directory_path,
            direct_only=direct_only,
            states=states,
            exclude_states=exclude_states,
        )

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
            candidate_path = candidate.resolve()
            for issue in self.local_issue_store.read_all().values():
                if issue.yaml_path and Path(issue.yaml_path).resolve() == candidate_path:
                    return issue
            issue_number = _read_issue_number_from_yaml(candidate)
            raise ValueError(f"Unknown local issue: {issue_number}")

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
        states: set[str] | None = None,
        exclude_states: set[str] | None = None,
    ) -> list[LocalIssueRecord]:
        normalized_directory_path = self._normalize_directory_path(directory_path)
        normalized_states = {value.strip() for value in (states or set()) if value.strip()} or None
        normalized_excluded_states = {
            value.strip() for value in (exclude_states or set()) if value.strip()
        } or None
        issues = sorted(self.local_issue_store.read_all().values(), key=_issue_number_sort_key)
        filtered: list[LocalIssueRecord] = []
        for issue in issues:
            issue_state = (issue.state or "").strip()
            if normalized_states is not None and issue_state not in normalized_states:
                continue
            if normalized_excluded_states is not None and issue_state in normalized_excluded_states:
                continue
            filtered.append(issue)
        if direct_only or directory_path is not None:
            return [issue for issue in filtered if issue.directory_path == normalized_directory_path]
        return filtered

    def _refresh_approval_request(self, request: ApprovalRequest) -> ApprovalRequest:
        session = self.runtime.sessions.get(request.session_id)
        mutated = False
        if not request.directory_path and session is not None:
            request.directory_path = session.directory_path
            mutated = True
        if request.vm_id is None and session is not None and session.vm_id:
            request.vm_id = session.vm_id
            mutated = True
        if not request.tool_name:
            request.tool_name = request.action_type
            mutated = True
        if request.status in {ApprovalStatus.PENDING, ApprovalStatus.APPROVED} and request.expires_at <= _utc_now():
            request.status = ApprovalStatus.EXPIRED
            mutated = True
        if mutated:
            self.runtime.approvals.put(request.request_id, request)
        return request

    def _approval_matches_filters(
        self,
        request: ApprovalRequest,
        *,
        statuses: set[ApprovalStatus] | None = None,
        directory_path: str | None = None,
        direct_only: bool = False,
        session_id: str | None = None,
        vm_id: str | None = None,
        tool_name: str | None = None,
        min_age_seconds: int | None = None,
    ) -> bool:
        if statuses is not None and request.status not in statuses:
            return False
        if directory_path is not None:
            normalized_directory_path = self._normalize_directory_path(directory_path)
            request_directory_path = self._normalize_directory_path(request.directory_path)
            if direct_only or request_directory_path != normalized_directory_path:
                if request_directory_path != normalized_directory_path:
                    return False
        if session_id is not None and request.session_id != session_id.strip():
            return False
        if vm_id is not None and (request.vm_id or "") != vm_id.strip():
            return False
        if tool_name is not None and (request.tool_name or request.action_type) != tool_name.strip():
            return False
        if min_age_seconds is not None:
            age_seconds = int((_utc_now() - request.created_at).total_seconds())
            if age_seconds < min_age_seconds:
                return False
        return True

    def list_approval_requests(
        self,
        *,
        statuses: set[ApprovalStatus] | None = None,
        directory_path: str | None = None,
        direct_only: bool = False,
        session_id: str | None = None,
        vm_id: str | None = None,
        tool_name: str | None = None,
        min_age_seconds: int | None = None,
    ) -> list[ApprovalRequest]:
        requests = [
            self._refresh_approval_request(request)
            for request in self.runtime.approvals.read_all().values()
        ]
        filtered = [
            request
            for request in requests
            if self._approval_matches_filters(
                request,
                statuses=statuses,
                directory_path=directory_path,
                direct_only=direct_only,
                session_id=session_id,
                vm_id=vm_id,
                tool_name=tool_name,
                min_age_seconds=min_age_seconds,
            )
        ]
        return sorted(filtered, key=_approval_sort_key)

    def approval_queue_health(
        self,
        *,
        statuses: set[ApprovalStatus] | None = None,
        directory_path: str | None = None,
        direct_only: bool = False,
        session_id: str | None = None,
        vm_id: str | None = None,
        tool_name: str | None = None,
        min_age_seconds: int | None = None,
    ) -> ApprovalQueueHealth:
        requests = self.list_approval_requests(
            statuses=statuses,
            directory_path=directory_path,
            direct_only=direct_only,
            session_id=session_id,
            vm_id=vm_id,
            tool_name=tool_name,
            min_age_seconds=min_age_seconds,
        )
        health = ApprovalQueueHealth(total=len(requests))
        pending_ages: list[int] = []
        for request in requests:
            if request.status == ApprovalStatus.PENDING:
                health.pending += 1
                pending_ages.append(int((_utc_now() - request.created_at).total_seconds()))
            elif request.status == ApprovalStatus.APPROVED:
                health.approved += 1
            elif request.status == ApprovalStatus.DENIED:
                health.denied += 1
            elif request.status == ApprovalStatus.EXPIRED:
                health.expired += 1
        if pending_ages:
            health.oldest_pending_age_seconds = max(pending_ages)
        return health

    def get_approval_request(self, request_id: str) -> ApprovalRequest:
        request = self.runtime.approvals.get(request_id)
        if request is None:
            raise ValueError(f"Unknown approval request: {request_id}")
        return self._refresh_approval_request(request)

    def _approval_audit_context(self, request: ApprovalRequest, *, limit: int = 8) -> list[dict[str, Any]]:
        if not self.config.audit_log_path.exists():
            return []
        try:
            lines = self.config.audit_log_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return []
        matches: list[dict[str, Any]] = []
        for raw_line in reversed(lines):
            try:
                payload = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            args_payload = payload.get("args") if isinstance(payload.get("args"), dict) else {}
            result_payload = payload.get("result") if isinstance(payload.get("result"), dict) else {}
            if (
                payload.get("session_id") == request.session_id
                or args_payload.get("request_id") == request.request_id
                or result_payload.get("request_id") == request.request_id
            ):
                matches.append(payload)
            if len(matches) >= limit:
                break
        matches.reverse()
        return matches

    def get_approval_review(self, request_id: str) -> dict[str, Any]:
        request = self.get_approval_request(request_id)
        age_seconds = int((_utc_now() - request.created_at).total_seconds())
        expires_in_seconds = int((request.expires_at - _utc_now()).total_seconds())
        request_payload = request.model_dump(mode="json")
        request_payload["payload"] = _redact_sensitive_payload(request.payload)
        return {
            "request": request_payload,
            "redacted_payload": request_payload["payload"],
            "audit_context": self._approval_audit_context(request),
            "age_seconds": age_seconds,
            "expires_in_seconds": expires_in_seconds,
        }

    def decide_approval_request(
        self,
        request_id: str,
        *,
        approver: str,
        approve: bool,
        reason: str,
    ) -> ApprovalRequest:
        request = self.get_approval_request(request_id)
        normalized_approver = approver.strip()
        normalized_reason = reason.strip()
        if not normalized_approver:
            raise ValueError("approver is required")
        if not normalized_reason:
            raise ValueError("reason is required")
        if request.status != ApprovalStatus.PENDING:
            raise ValueError(f"Approval request {request_id} is not pending")
        decision_status = ApprovalStatus.APPROVED if approve else ApprovalStatus.DENIED
        decided_at = _utc_now()
        request.status = decision_status
        request.approver = normalized_approver
        request.decision_at = decided_at
        request.decision_reason = normalized_reason
        request.decision_history.append(
            ApprovalDecisionRecord(
                status=decision_status,
                approver=normalized_approver,
                reason=normalized_reason,
                decided_at=decided_at,
            )
        )
        self.runtime.approvals.put(request.request_id, request)
        self.runtime.audit.log_decision(
            event_type="approval_decision",
            session_id=request.session_id,
            vm_id=request.vm_id,
            decision=request.status.value,
            reason=normalized_reason,
            args={
                "request_id": request.request_id,
                "tool_name": request.tool_name or request.action_type,
                "approver": normalized_approver,
                "payload_digest": request.payload_digest,
            },
            result={"status": request.status.value},
        )
        return request

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

    def upsert_trigger_config(self, trigger_id: str, payload: dict[str, object]) -> TriggerConfigMutationPayload:
        return self._upsert_trigger_config(trigger_id, payload)

    def delete_trigger_config(self, trigger_id: str) -> TriggerConfigMutationPayload:
        return self._delete_trigger_config(trigger_id)

    def upsert_effect_config(self, effect_id: str, payload: dict[str, object]) -> EffectConfigMutationPayload:
        return self._upsert_effect_config(effect_id, payload)

    def delete_effect_config(self, effect_id: str) -> EffectConfigMutationPayload:
        return self._delete_effect_config(effect_id)

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
            raw = model.model_validate(self.config.model_dump(mode="json")).model_dump(mode="json", exclude_none=True)
        model = _WORKER_GLOBAL_CONFIG_MODELS[worker_id]
        config = model.model_validate(raw).model_dump(mode="json", exclude_none=True)
        return WorkerGlobalConfigPayload(worker_id=worker_id, path=str(path), exists=exists, config=config)

    def _write_worker_global_config(self, worker_id: str, payload: dict[str, object]) -> WorkerGlobalConfigWritePayload:
        path = self._worker_global_config_path(worker_id)
        if path is None:
            raise ValueError(f"Unknown worker: {worker_id}")
        model = _WORKER_GLOBAL_CONFIG_MODELS[worker_id]
        try:
            config = model.model_validate(payload).model_dump(mode="json", exclude_none=True)
        except ValidationError as exc:
            raise _safe_validation_error(exc, context=f"Invalid {worker_id} worker config") from exc
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config, indent=2, sort_keys=True), encoding="utf-8")
        return WorkerGlobalConfigWritePayload(worker_id=worker_id, path=str(path), config=config)

    def _upsert_trigger_config(self, trigger_id: str, payload: dict[str, object]) -> TriggerConfigMutationPayload:
        normalized_trigger_id = str(trigger_id).strip()
        if not normalized_trigger_id:
            raise ValueError("trigger_id is required")
        normalized_payload = dict(payload)
        payload_trigger_id = str(normalized_payload.get("id", "")).strip()
        if payload_trigger_id and payload_trigger_id != normalized_trigger_id:
            raise ValueError("trigger_id must match payload.id when payload.id is provided")
        normalized_payload["id"] = normalized_trigger_id
        try:
            validated_trigger = _TRIGGER_DEFINITION_ADAPTER.validate_python(normalized_payload)
        except ValidationError as exc:
            raise _safe_validation_error(exc, context="Invalid trigger config") from exc
        normalized_payload = validated_trigger.model_dump(mode="json", exclude_none=True)

        current = self._read_worker_global_config("trigger-orchestrator")
        config = dict(current.config)
        existing_triggers_raw = config.get("triggers", [])
        if not isinstance(existing_triggers_raw, list):
            raise ValueError("Invalid trigger-orchestrator config: triggers must be a JSON array.")
        existing_triggers = [entry for entry in existing_triggers_raw if isinstance(entry, dict)]
        replacing = False
        for index, existing in enumerate(existing_triggers):
            existing_id = str(existing.get("id", "")).strip()
            if existing_id == normalized_trigger_id:
                existing_triggers[index] = normalized_payload
                replacing = True
                break
        if not replacing:
            existing_triggers.append(normalized_payload)
        config["triggers"] = existing_triggers
        write_result = self._write_worker_global_config("trigger-orchestrator", config)
        return TriggerConfigMutationPayload(
            trigger_id=normalized_trigger_id,
            operation="updated" if replacing else "added",
            path=write_result.path,
            config=write_result.config,
        )

    def _delete_trigger_config(self, trigger_id: str) -> TriggerConfigMutationPayload:
        normalized_trigger_id = str(trigger_id).strip()
        if not normalized_trigger_id:
            raise ValueError("trigger_id is required")
        current = self._read_worker_global_config("trigger-orchestrator")
        config = dict(current.config)
        triggers_raw = config.get("triggers", [])
        if not isinstance(triggers_raw, list):
            raise ValueError("Invalid trigger-orchestrator config: triggers must be a JSON array.")
        filtered = []
        removed = False
        for entry in triggers_raw:
            if not isinstance(entry, dict):
                continue
            entry_id = str(entry.get("id", "")).strip()
            if entry_id == normalized_trigger_id:
                removed = True
                continue
            filtered.append(entry)
        if not removed:
            raise ValueError(f"Trigger not found: {normalized_trigger_id}")
        config["triggers"] = filtered
        write_result = self._write_worker_global_config("trigger-orchestrator", config)
        return TriggerConfigMutationPayload(
            trigger_id=normalized_trigger_id,
            operation="deleted",
            path=write_result.path,
            config=write_result.config,
        )

    def _upsert_effect_config(self, effect_id: str, payload: dict[str, object]) -> EffectConfigMutationPayload:
        normalized_effect_id = str(effect_id).strip()
        if not normalized_effect_id:
            raise ValueError("effect_id is required")
        normalized_payload = dict(payload)
        payload_effect_id = str(normalized_payload.get("id", "")).strip()
        if payload_effect_id and payload_effect_id != normalized_effect_id:
            raise ValueError("effect_id must match payload.id when payload.id is provided")
        normalized_payload["id"] = normalized_effect_id
        try:
            validated_effect = _EFFECT_DEFINITION_ADAPTER.validate_python(normalized_payload)
        except ValidationError as exc:
            raise _safe_validation_error(exc, context="Invalid effect config") from exc
        normalized_payload = validated_effect.model_dump(mode="json", exclude_none=True)

        current = self._read_worker_global_config("trigger-orchestrator")
        config = dict(current.config)
        existing_effects_raw = config.get("effects", [])
        if not isinstance(existing_effects_raw, list):
            raise ValueError("Invalid trigger-orchestrator config: effects must be a JSON array.")
        existing_effects = [entry for entry in existing_effects_raw if isinstance(entry, dict)]
        replacing = False
        for index, existing in enumerate(existing_effects):
            existing_id = str(existing.get("id", "")).strip()
            if existing_id == normalized_effect_id:
                existing_effects[index] = normalized_payload
                replacing = True
                break
        if not replacing:
            existing_effects.append(normalized_payload)
        config["effects"] = existing_effects
        write_result = self._write_worker_global_config("trigger-orchestrator", config)
        return EffectConfigMutationPayload(
            effect_id=normalized_effect_id,
            operation="updated" if replacing else "added",
            path=write_result.path,
            config=write_result.config,
        )

    def _delete_effect_config(self, effect_id: str) -> EffectConfigMutationPayload:
        normalized_effect_id = str(effect_id).strip()
        if not normalized_effect_id:
            raise ValueError("effect_id is required")
        current = self._read_worker_global_config("trigger-orchestrator")
        config = dict(current.config)
        effects_raw = config.get("effects", [])
        if not isinstance(effects_raw, list):
            raise ValueError("Invalid trigger-orchestrator config: effects must be a JSON array.")
        filtered = []
        removed = False
        for entry in effects_raw:
            if not isinstance(entry, dict):
                continue
            entry_id = str(entry.get("id", "")).strip()
            if entry_id == normalized_effect_id:
                removed = True
                continue
            filtered.append(entry)
        if not removed:
            raise ValueError(f"Effect not found: {normalized_effect_id}")
        config["effects"] = filtered
        write_result = self._write_worker_global_config("trigger-orchestrator", config)
        return EffectConfigMutationPayload(
            effect_id=normalized_effect_id,
            operation="deleted",
            path=write_result.path,
            config=write_result.config,
        )

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

    @contextmanager
    def _local_issue_creation_lock(self):
        self.local_issue_create_lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.local_issue_create_lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

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
        self._write_local_issue_yaml(issue, updated_at=now)
        return issue

    def _write_local_issue_yaml(self, issue: LocalIssueRecord, *, updated_at: datetime | None = None) -> None:
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
        fields = _local_issue_fields(issue)
        if fields:
            payload["fields"] = fields
        else:
            payload.pop("fields", None)
        if updated_at is not None:
            payload["updated_at"] = updated_at.isoformat()
        yaml_path.parent.mkdir(parents=True, exist_ok=True)
        yaml_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

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
        with self._local_issue_creation_lock():
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
                    parsed_title = yaml_payload.get("title")
                    normalized_title = str(parsed_title or "").strip() if isinstance(parsed_title, str) else ""
                    if normalized_title:
                        title = normalized_title
                    else:
                        title_match = re.search(r"(?m)^\s*title\s*:\s*[\"']?(.*?)[\"']?\s*$", yaml_text)
                        title = title_match.group(1).strip() if title_match else ""
                    if not title:
                        title = f"Local issue {issue_number}"
                if not body:
                    parsed_body = yaml_payload.get("body")
                    normalized_body = str(parsed_body or "").strip() if isinstance(parsed_body, str) else ""
                    if normalized_body:
                        body = normalized_body
                    else:
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
        base_payload = issue.model_dump(mode="json")
        proxy_target = self._proxy_target_for_issue(issue)
        if proxy_target is None:
            return {
                **base_payload,
                "source_type": "local",
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
            base_payload = issue.model_dump(mode="json")
        return {
            **base_payload,
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
            issue.sync_derived_fields()
            issue.updated_at = _utc_now()
            self.local_issue_store.put(issue.issue_id, issue)
            self._write_local_issue_yaml(issue, updated_at=issue.updated_at)
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
        model: str | None = None,
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
        candidate_issue_path = Path(issue_input)
        if candidate_issue_path.is_file():
            local_issue = self._resolve_local_issue_record(issue_input)
            issue_number = local_issue.issue_number
            issue_input = local_issue.yaml_path
        else:
            try:
                local_issue = self._resolve_local_issue_record(issue_input)
                issue_number = local_issue.issue_number
                issue_input = local_issue.yaml_path
            except ValueError as exc:
                if not str(exc).startswith("Unknown local issue identifier: "):
                    return VmActionResult(
                        success=False,
                        vm_name=f"vm-{issue_number}",
                        action="create_issue_vm",
                        message=str(exc),
                    )
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
        normalized_model = (model or "").strip() or None
        if normalized_model is not None:
            manifest.model = normalized_model
        else:
            try:
                manifest.model = self._resolve_agent_model_for_session(manifest)
            except ValueError:
                manifest.model = None
        self.runtime.sessions.put(resolved_session_id, manifest)

        def _issue_vm_failure(message: str) -> VmActionResult:
            try:
                preserved_vm = _dominfo_field(resolved_vm_name, "State") is not None
            except RuntimeError:
                preserved_vm = False
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
        model: str | None = None,
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
            model=model,
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
        model: str | None = None,
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
        normalized_model = (model or "").strip() or None
        if normalized_model is not None:
            manifest.model = normalized_model
        else:
            try:
                manifest.model = self._resolve_agent_model_for_session(manifest)
            except ValueError:
                manifest.model = None
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
