from __future__ import annotations

import json
import inspect
import logging
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from itertools import count
from pathlib import Path
from typing import Annotated

import httpx
import typer
from pydantic import ValidationError
from typer.core import TyperGroup

from .admin_service import AdminService
from .audit import AuditEventType
from .ado_issue_worker import run_worker_command
from .config import GatewayConfig, load_config
from .host_heartbeat_worker import build_host_heartbeat_worker
from .migrations import migrate_directory_ownership_defaults
from .email.providers import JsonEmailProvider, MultiMailboxEmailProvider
from .models import ApprovalStatus, DiscordCommandEnvelope, EmailMessage, SessionManifest, ToolName
from .runtime import build_runtime

_COMMAND_PANELS = {
    "Health and State": 0,
    "Sessions and Feedback": 1,
    "Issue Lifecycle": 2,
    "VM Operations": 3,
    "Seeding and Fixtures": 4,
    "Background Workers": 5,
}


class GroupedHelpTyperGroup(TyperGroup):
    def list_commands(self, ctx) -> list[str]:
        def command_sort_key(name: str) -> tuple[int, str]:
            command = self.commands[name]
            panel = getattr(command, "rich_help_panel", None)
            panel_rank = _COMMAND_PANELS.get(panel or "", len(_COMMAND_PANELS))
            return (panel_rank, name.casefold())

        return sorted(self.commands, key=command_sort_key)


app = typer.Typer(help="Utilities for the agent sandbox host control plane.", cls=GroupedHelpTyperGroup)
directories_app = typer.Typer(help="Directory management and directory-scoped config inspection.")
app.add_typer(directories_app, name="directories")

ConfigOption = Annotated[Path, typer.Option("--config", help="Path to the gateway YAML config.")]
_discord_seed_sequence = count()
UTC = timezone.utc


@dataclass(slots=True)
class PathStatus:
    path: str
    exists: bool
    is_dir: bool
    readable: bool
    writable: bool


@dataclass(slots=True)
class EndpointStatus:
    name: str
    url: str
    reachable: bool
    status_code: int | None
    error: str | None


@dataclass(slots=True)
class UnitStatus:
    unit: str
    state: str
    available: bool
    detail: str | None


@dataclass(slots=True)
class HeartbeatSummary:
    healthy: bool
    reason: str
    last_cycle_at: str | None
    last_cycle_streams_polled: int | None
    last_cycle_discovered_feedback: int | None
    last_cycle_next_sleep_seconds: float | None
    last_busy_skip_at: str | None
    last_busy_skip_reason: str | None
    last_feedback_detected_at: str | None
    last_resume_outcome: str | None
    last_resume_reason: str | None
    last_vm_start_outcome: str | None
    last_vm_start_reason: str | None


def _path_status(path: Path, *, is_dir: bool = False) -> PathStatus:
    return PathStatus(
        path=str(path),
        exists=path.exists(),
        is_dir=path.is_dir() if path.exists() else is_dir,
        readable=os.access(path, os.R_OK),
        writable=os.access(path, os.W_OK),
    )


def _normalize_host_for_local_check(host: str) -> str:
    if host in {"0.0.0.0", "::", ""}:
        return "127.0.0.1"
    return host


def _get_systemd_unit_state(unit: str) -> UnitStatus:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", unit],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return UnitStatus(unit=unit, state="unavailable", available=False, detail="systemctl not found")

    detail = result.stderr.strip() or None
    if result.returncode == 0:
        return UnitStatus(unit=unit, state=result.stdout.strip() or "active", available=True, detail=detail)

    state = result.stdout.strip() or "unknown"
    return UnitStatus(unit=unit, state=state, available=True, detail=detail)


def _check_health_endpoint(name: str, url: str) -> EndpointStatus:
    try:
        response = httpx.get(url, timeout=3.0)
    except Exception as exc:
        return EndpointStatus(name=name, url=url, reachable=False, status_code=None, error=str(exc))
    return EndpointStatus(
        name=name,
        url=url,
        reachable=response.status_code < 500,
        status_code=response.status_code,
        error=None,
    )


def _format_rows(rows: list[tuple[str, str]]) -> str:
    key_width = max(len(key) for key, _ in rows)
    return "\n".join(f"{key:<{key_width}}  {value}" for key, value in rows)


def _run_subprocess_compat(
    cmd: list[str],
    *,
    check: bool,
    capture_output: bool | None = None,
    text: bool | None = None,
) -> subprocess.CompletedProcess[str]:
    kwargs: dict[str, object] = {"check": check}
    try:
        parameters = inspect.signature(subprocess.run).parameters
    except (TypeError, ValueError):
        parameters = {}
    if capture_output is not None and "capture_output" in parameters:
        kwargs["capture_output"] = capture_output
    if text is not None and "text" in parameters:
        kwargs["text"] = text
    return subprocess.run(cmd, **kwargs)


def _echo_admin_result_or_exit(result, *, success_message: str | None = None) -> None:
    if getattr(result, "ok", False):
        if success_message is not None:
            typer.echo(success_message)
        return
    errors = getattr(result, "errors", []) or []
    message = errors[0].message if errors else "operation failed"
    raise typer.BadParameter(message)


def _load_heartbeat_summary(audit_log_path: Path, *, max_events: int = 400) -> HeartbeatSummary:
    summary = HeartbeatSummary(
        healthy=False,
        reason="no-heartbeat-events",
        last_cycle_at=None,
        last_cycle_streams_polled=None,
        last_cycle_discovered_feedback=None,
        last_cycle_next_sleep_seconds=None,
        last_busy_skip_at=None,
        last_busy_skip_reason=None,
        last_feedback_detected_at=None,
        last_resume_outcome=None,
        last_resume_reason=None,
        last_vm_start_outcome=None,
        last_vm_start_reason=None,
    )
    if not audit_log_path.exists():
        summary.reason = "missing-audit-log"
        return summary

    try:
        lines = audit_log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        summary.reason = "audit-log-unreadable"
        return summary

    for raw in reversed(lines[-max_events:]):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        event_type = str(event.get("event_type", ""))
        created_at = event.get("created_at")
        reason = event.get("reason")
        decision = event.get("decision")
        result = event.get("result")
        if event_type == AuditEventType.HOST_HEARTBEAT_CYCLE_COMPLETED and summary.last_cycle_at is None:
            summary.last_cycle_at = str(created_at) if created_at else None
            if isinstance(result, dict):
                streams_polled = result.get("streams_polled")
                discovered_feedback = result.get("discovered_feedback")
                next_sleep_seconds = result.get("next_sleep_seconds")
                summary.last_cycle_streams_polled = int(streams_polled) if isinstance(streams_polled, int) else None
                summary.last_cycle_discovered_feedback = (
                    int(discovered_feedback) if isinstance(discovered_feedback, int) else None
                )
                summary.last_cycle_next_sleep_seconds = (
                    float(next_sleep_seconds) if isinstance(next_sleep_seconds, (int, float)) else None
                )
            summary.reason = str(reason or decision or "cycle-seen")
            summary.healthy = True
        elif event_type == AuditEventType.BUSY_SKIP and summary.last_busy_skip_at is None:
            summary.last_busy_skip_at = str(created_at) if created_at else None
            summary.last_busy_skip_reason = str(reason) if reason else None
        elif event_type == AuditEventType.FEEDBACK_DETECTED and summary.last_feedback_detected_at is None:
            summary.last_feedback_detected_at = str(created_at) if created_at else None
        elif event_type in {
            AuditEventType.RESUME_ATTEMPTED,
            AuditEventType.RESUME_SUCCEEDED,
            AuditEventType.RESUME_FAILED,
        } and summary.last_resume_outcome is None:
            summary.last_resume_outcome = event_type
            summary.last_resume_reason = str(reason) if reason else None
        elif event_type in {
            AuditEventType.VM_START_ATTEMPTED,
            AuditEventType.VM_START_SUCCEEDED,
            AuditEventType.VM_START_FAILED,
        } and summary.last_vm_start_outcome is None:
            summary.last_vm_start_outcome = event_type
            summary.last_vm_start_reason = str(reason) if reason else None

    return summary


def _default_issue_session_id(issue_number: str) -> str:
    return f"issue-{issue_number}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{os.getpid()}"


def _issue_session_id_prefix(issue_number: str) -> str:
    return f"issue-{issue_number}-"


def _resolve_vm_name(target: str) -> str:
    normalized = target.strip()
    if normalized.isdigit():
        return f"vm-{normalized}"
    return normalized


def _resolve_issue_number_or_raise(target: str) -> str:
    normalized = target.strip()
    if normalized.isdigit():
        return normalized
    if normalized.startswith("vm-") and normalized[3:].isdigit():
        return normalized[3:]
    raise typer.BadParameter(
        f"Could not resolve issue number from target {target!r}. "
        "Pass an issue number or vm-<issue-number>."
    )


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
    raise typer.BadParameter(
        f"Could not read issue number from YAML file: {issue_path}. "
        "Expected one of: issue_number:, number:, or id:."
    )


def _resolve_issue_number(issue_input: str) -> str:
    candidate = Path(issue_input)
    if candidate.is_file():
        return _read_issue_number_from_yaml(candidate)
    return issue_input


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
    env_file_values = _load_optional_env_file(root_dir / "config" / "git-server.env")
    configured = env_file_values.get("AGENT_SANDBOX_GIT_ROOT", "").strip()
    if configured:
        return Path(configured)
    return root_dir / "git-remotes"


def _matching_issue_session_ids(issue_number: str, sessions: dict[str, SessionManifest]) -> set[str]:
    vm_name = f"vm-{issue_number}"
    prefix = _issue_session_id_prefix(issue_number)
    matches: set[str] = set()
    for session_id, session in sessions.items():
        if session.vm_id == vm_name or session_id == issue_number or session_id.startswith(prefix):
            matches.add(session_id)
    return matches


def _parse_session_extension(value: str | None) -> timedelta:
    raw_value = (value or "1d").strip().lower()
    match = re.fullmatch(r"(\d+)([hd])", raw_value)
    if match is None:
        raise typer.BadParameter("Extension must use the format <number><h|d>, for example 8h or 2d.")

    amount = int(match.group(1))
    unit = match.group(2)
    if unit == "h":
        return timedelta(hours=amount)
    return timedelta(days=amount)


def _default_session_ttl(cfg: GatewayConfig) -> timedelta:
    return timedelta(seconds=cfg.default_session_ttl_seconds)


def _resolve_session_ttl(cfg: GatewayConfig, ttl_hours: int | None) -> timedelta:
    if ttl_hours is not None:
        return timedelta(hours=ttl_hours)
    return _default_session_ttl(cfg)


def _resolve_session_target(target: str, sessions: dict[str, SessionManifest]) -> tuple[str, SessionManifest]:
    normalized = target.strip()
    if not normalized:
        raise typer.BadParameter("Target must be a non-empty issue number, vm name, or session id.")

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
        raise typer.BadParameter(f"Unknown session target: {target}")

    resolved_session_id = max(
        matching_ids,
        key=lambda session_id: _session_sort_key((session_id, sessions[session_id])),
    )
    return resolved_session_id, sessions[resolved_session_id]


def _session_sort_key(item: tuple[str, SessionManifest]) -> tuple[datetime, datetime, str]:
    session_id, manifest = item
    return (manifest.created_at, manifest.expires_at, session_id)


def _session_streams_for_session_ids(
    sessions: dict[str, SessionManifest],
    *,
    session_ids: set[str],
) -> set[tuple[str, str, str]]:
    streams: set[tuple[str, str, str]] = set()
    for session_id in session_ids:
        session = sessions.get(session_id)
        if session is None:
            continue
        raw_streams = session.metadata.get("pr_feedback_streams")
        if not isinstance(raw_streams, list):
            continue
        for raw_stream in raw_streams:
            if not isinstance(raw_stream, dict):
                continue
            provider = str(raw_stream.get("provider", "")).strip()
            repo = str(raw_stream.get("repo", "")).strip()
            pr_id = str(raw_stream.get("pr_id", "")).strip()
            if provider and repo and pr_id:
                streams.add((provider, repo, pr_id))
    return streams


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


def _prune_pr_map_for_issue_numbers(
    path: Path,
    *,
    issue_numbers: set[str],
) -> tuple[int, set[tuple[str, str, str]]]:
    if not path.exists() or not issue_numbers:
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
        session_issue_number = _extract_issue_number(str(raw_entry.get("session_id", "")).strip())
        if session_issue_number in issue_numbers:
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


def _prune_approvals_for_sessions(runtime: object, *, session_ids: set[str]) -> int:
    approvals_store = getattr(runtime, "approvals", None)
    if approvals_store is None or not session_ids:
        return 0
    approvals = approvals_store.read_all()
    removed_count = 0
    remaining = {}
    for request_id, approval in approvals.items():
        if approval.session_id in session_ids:
            removed_count += 1
            continue
        remaining[request_id] = approval
    if removed_count:
        approvals_store.write_all(remaining)
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


def _list_existing_issue_vm_names() -> set[str]:
    completed = subprocess.run(
        ["virsh", "list", "--all", "--name"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise typer.Exit(code=completed.returncode or 1)
    return {line.strip() for line in (completed.stdout or "").splitlines() if line.strip()}


def _issue_numbers_from_pr_map(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        return set()
    issue_numbers: set[str] = set()
    for raw_entry in entries.values():
        if not isinstance(raw_entry, dict):
            continue
        issue_number = _extract_issue_number(str(raw_entry.get("session_id", "")).strip())
        if issue_number is not None:
            issue_numbers.add(issue_number)
    return issue_numbers


def _issue_numbers_from_bare_repos(root_dir: Path) -> set[str]:
    git_root = _resolve_git_root(root_dir)
    if not git_root.exists():
        return set()
    issue_numbers: set[str] = set()
    for bare_repo in git_root.glob("*_repo.git"):
        issue_number = _extract_issue_number(bare_repo.name.removesuffix("_repo.git"))
        if issue_number is not None:
            issue_numbers.add(issue_number)
    return issue_numbers


def _parse_pr_feedback_streams(values: list[str]) -> list[dict[str, str]]:
    streams: list[dict[str, str]] = []
    for raw in values:
        provider, separator, remainder = raw.partition(":")
        repo, separator2, pr_id = remainder.rpartition(":")
        provider = provider.strip()
        repo = repo.strip()
        pr_id = pr_id.strip()
        if not separator or not separator2 or not provider or not repo or not pr_id:
            raise typer.BadParameter(
                f"Invalid --pr-feedback-stream value {raw!r}. Expected provider:repo:pr_id."
            )
        streams.append({"provider": provider, "repo": repo, "pr_id": pr_id})
    return streams


def _build_issue_session_manifest(
    *,
    cfg: GatewayConfig,
    session_id: str,
    vm_id: str,
    ttl_hours: int | None,
    discord_channels: list[str],
    email_mailboxes: list[str],
    email_folders: list[str],
) -> SessionManifest:
    allowed_tools: list[ToolName] = [
        ToolName.APPROVAL_SUBMIT,
        ToolName.APPROVAL_CHECK,
    ]

    if discord_channels:
        allowed_tools.extend(
            [
                ToolName.DISCORD_GET_COMMANDS,
                ToolName.DISCORD_POST_STATUS,
            ]
        )

    email_requested = bool(email_mailboxes or email_folders)
    resolved_email_mailboxes = email_mailboxes or (cfg.email_mailbox_allowlist if email_requested else [])
    resolved_email_folders = email_folders or (
        sorted(
            {
                folder
                for folders in cfg.email_folder_allowlist_by_mailbox.values()
                for folder in folders
            }
        )
        if email_requested
        else []
    )
    if email_requested:
        allowed_tools.extend(
            [
                ToolName.EMAIL_LIST_UNREAD,
                ToolName.EMAIL_GET_MESSAGE,
                ToolName.EMAIL_CREATE_DRAFT,
            ]
        )
        if cfg.email_send_enabled:
            allowed_tools.append(ToolName.EMAIL_SEND_APPROVED)

    unique_tools = list(dict.fromkeys(allowed_tools))
    return SessionManifest(
        session_id=session_id,
        vm_id=vm_id,
        directory_path="root",
        allowed_tools=unique_tools,
        discord_channel_allowlist=discord_channels,
        email_folder_allowlist=resolved_email_folders,
        email_mailbox_allowlist=resolved_email_mailboxes,
        email_folder_allowlist_by_mailbox=cfg.email_folder_allowlist_by_mailbox,
        email_draft_recipient_allowlist=cfg.email_draft_recipient_allowlist,
        email_draft_domain_allowlist=cfg.email_draft_domain_allowlist,
        email_send_enabled=cfg.email_send_enabled,
        expires_at=datetime.now(UTC) + _resolve_session_ttl(cfg, ttl_hours),
        metadata={},
    )


@app.command(
    "init-state",
    rich_help_panel="Health and State",
    short_help="Create runtime state files.",
)
def init_state(config: ConfigOption = Path("config/gateway.yaml")) -> None:
    cfg = load_config(config)
    runtime = build_runtime(cfg)
    runtime.directories.ensure_root()
    typer.echo(f"Initialized runtime state under {cfg.runtime_dir}")


@directories_app.command("list", short_help="List directory children.")
def directories_list(
    config: ConfigOption = Path("config/gateway.yaml"),
    parent: Annotated[str, typer.Option("--parent", help="Parent directory path or id.")] = "root",
) -> None:
    service = AdminService(config)
    result = service.directory_admin.list(parent)
    _echo_admin_result_or_exit(result)
    payload = result.data
    assert payload is not None
    typer.echo(json.dumps(payload.model_dump(mode="json"), indent=2, sort_keys=True))


@directories_app.command("show", short_help="Show one directory and its ancestry.")
def directories_show(
    reference: str,
    config: ConfigOption = Path("config/gateway.yaml"),
) -> None:
    service = AdminService(config)
    result = service.get_directory(reference)
    _echo_admin_result_or_exit(result)
    payload = result.data
    assert payload is not None
    typer.echo(json.dumps(payload.model_dump(mode="json"), indent=2, sort_keys=True))


@directories_app.command("create", short_help="Create a child directory.")
def directories_create(
    name: str,
    config: ConfigOption = Path("config/gateway.yaml"),
    parent: Annotated[str, typer.Option("--parent", help="Parent directory path or id.")] = "root",
) -> None:
    service = AdminService(config)
    result = service.create_directory_child(parent, name)
    _echo_admin_result_or_exit(result)
    assert result.data is not None
    typer.echo(result.data.details.directory.path)


@directories_app.command("rename", short_help="Rename a directory.")
def directories_rename(
    reference: str,
    new_name: str,
    config: ConfigOption = Path("config/gateway.yaml"),
) -> None:
    service = AdminService(config)
    result = service.directory_admin.update(reference, new_name=new_name)
    _echo_admin_result_or_exit(result)
    assert result.data is not None
    typer.echo(result.data.details.directory.path)


@directories_app.command("move", short_help="Move a directory to a new parent.")
def directories_move(
    reference: str,
    config: ConfigOption = Path("config/gateway.yaml"),
    new_parent: Annotated[str, typer.Option("--new-parent", help="New parent directory path or id.")] = "root",
) -> None:
    service = AdminService(config)
    result = service.directory_admin.update(reference, new_parent=new_parent)
    _echo_admin_result_or_exit(result)
    assert result.data is not None
    typer.echo(result.data.details.directory.path)


@directories_app.command("delete", short_help="Delete a directory.")
def directories_delete(
    reference: str,
    config: ConfigOption = Path("config/gateway.yaml"),
    recursive: Annotated[bool, typer.Option("--recursive", help="Delete non-empty directories recursively.")] = False,
) -> None:
    service = AdminService(config)
    result = service.directory_admin.delete(reference, recursive=recursive)
    _echo_admin_result_or_exit(result)
    assert result.data is not None
    typer.echo(json.dumps(result.data.model_dump(mode="json"), indent=2, sort_keys=True))


@directories_app.command("config-show", short_help="Show raw and effective config for one directory domain.")
def directories_config_show(
    reference: str,
    domain: Annotated[str, typer.Option("--domain", help="Directory config domain name.")],
    config: ConfigOption = Path("config/gateway.yaml"),
) -> None:
    service = AdminService(config)
    raw_result = service.read_directory_domain_config(reference, domain)
    effective_result = service.compute_directory_effective_config(reference, domain)
    _echo_admin_result_or_exit(raw_result)
    _echo_admin_result_or_exit(effective_result)
    assert raw_result.data is not None
    assert effective_result.data is not None
    typer.echo(
        json.dumps(
            {
                "raw": raw_result.data.model_dump(mode="json"),
                "effective": effective_result.data.model_dump(mode="json"),
                "validation_diagnostics": [
                    item.model_dump(mode="json") for item in effective_result.validation_diagnostics
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )


@directories_app.command("config-set", short_help="Write raw config for one directory domain.")
def directories_config_set(
    reference: str,
    payload: str,
    domain: Annotated[str, typer.Option("--domain", help="Directory config domain name.")],
    config: ConfigOption = Path("config/gateway.yaml"),
) -> None:
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"invalid JSON payload: {exc}") from exc
    if not isinstance(parsed, dict):
        raise typer.BadParameter("config payload must be a JSON object")
    service = AdminService(config)
    result = service.write_directory_domain_config(reference, domain, parsed)
    _echo_admin_result_or_exit(result)
    assert result.data is not None
    typer.echo(json.dumps(result.data.model_dump(mode="json"), indent=2, sort_keys=True))


@app.command(
    "migrate-directory-ownership",
    rich_help_panel="Health and State",
    short_help="Backfill missing directory ownership fields to root.",
)
def migrate_directory_ownership(
    config: ConfigOption = Path("config/gateway.yaml"),
) -> None:
    cfg = load_config(config)
    summary = migrate_directory_ownership_defaults(cfg)
    typer.echo(
        f"Backfilled {summary.changed_records} record(s) across {summary.changed_files} file(s)"
    )


@app.command(
    "status",
    rich_help_panel="Health and State",
    short_help="Show host and runtime health.",
)
def status(config: ConfigOption = Path("config/gateway.yaml"), as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    report: dict[str, object] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "overall": "ok",
        "config": {"loaded": False, "path": str(config), "error": None},
        "runtime_paths": [],
        "systemd_units": [],
        "health": [],
        "host_heartbeat": {},
        "sessions": {
            "total": 0,
            "active": 0,
            "expired": 0,
            "next_expiration": None,
            "last_expiration": None,
        },
    }

    cfg: GatewayConfig | None = None
    try:
        cfg = load_config(config)
        report["config"] = {"loaded": True, "path": str(config), "error": None}
    except Exception as exc:
        report["overall"] = "degraded"
        report["config"] = {"loaded": False, "path": str(config), "error": str(exc)}

    if cfg is not None:
        runtime_paths = [
            _path_status(cfg.runtime_dir, is_dir=True),
            _path_status(cfg.session_store_path),
            _path_status(cfg.approval_store_path),
            _path_status(cfg.draft_store_path),
            _path_status(cfg.email_store_path),
            _path_status(cfg.email_cursor_store_path),
            _path_status(cfg.discord_command_store_path),
            _path_status(cfg.directories_store_path),
            _path_status(cfg.audit_log_path),
        ]
        report["runtime_paths"] = [asdict(path_status) for path_status in runtime_paths]

        for path_status in runtime_paths:
            if not path_status.exists or not path_status.readable or not path_status.writable:
                report["overall"] = "degraded"

        units = [
            "agent-sandbox-firewall.service",
            "agent-sandbox-gateway.service",
            "agent-sandbox-openai-relay.service",
            "agent-sandbox-host-heartbeat.service",
        ]
        unit_statuses = [_get_systemd_unit_state(unit) for unit in units]
        report["systemd_units"] = [asdict(unit_status) for unit_status in unit_statuses]
        for unit_status in unit_statuses:
            if unit_status.available and unit_status.state != "active":
                report["overall"] = "degraded"

        gateway_health_url = cfg.gateway_url
        relay_health_url = (
            f"http://{_normalize_host_for_local_check(cfg.openai_relay_host)}:{cfg.openai_relay_port}/healthz"
        )
        health_results = [
            _check_health_endpoint("gateway", gateway_health_url),
            _check_health_endpoint("openai_relay", relay_health_url),
        ]
        report["health"] = [asdict(result) for result in health_results]
        for endpoint in health_results:
            if not endpoint.reachable:
                report["overall"] = "degraded"

        heartbeat_summary = _load_heartbeat_summary(cfg.audit_log_path)
        report["host_heartbeat"] = asdict(heartbeat_summary)
        if not heartbeat_summary.healthy and heartbeat_summary.reason not in {
            "no-heartbeat-events",
            "missing-audit-log",
        }:
            report["overall"] = "degraded"

        if cfg.session_store_path.exists():
            try:
                raw_sessions = json.loads(cfg.session_store_path.read_text(encoding="utf-8"))
                sessions = [
                    SessionManifest.model_validate(value)
                    for value in raw_sessions.values()
                ]
            except (json.JSONDecodeError, ValidationError, OSError) as exc:
                report["overall"] = "degraded"
                report["sessions"] = {
                    "total": 0,
                    "active": 0,
                    "expired": 0,
                    "next_expiration": None,
                    "last_expiration": None,
                    "error": str(exc),
                }
            else:
                now = datetime.now(UTC)
                active = [session for session in sessions if session.expires_at > now]
                expired = [session for session in sessions if session.expires_at <= now]
                next_expiration = min((session.expires_at for session in active), default=None)
                last_expiration = max((session.expires_at for session in sessions), default=None)
                report["sessions"] = {
                    "total": len(sessions),
                    "active": len(active),
                    "expired": len(expired),
                    "next_expiration": next_expiration.isoformat() if next_expiration else None,
                    "last_expiration": last_expiration.isoformat() if last_expiration else None,
                }

    if as_json:
        typer.echo(json.dumps(report, indent=2, sort_keys=True))
        return

    config_info = report["config"]
    config_loaded = "yes" if config_info["loaded"] else "no"
    rows: list[tuple[str, str]] = [
        ("Overall", str(report["overall"])),
        ("Generated", str(report["generated_at"])),
        ("Config loaded", config_loaded),
        ("Config path", str(config_info["path"])),
    ]
    if config_info["error"]:
        rows.append(("Config error", str(config_info["error"])))

    typer.echo("Agent Sandbox Status")
    typer.echo(_format_rows(rows))

    if report["runtime_paths"]:
        typer.echo("\nRuntime paths")
        for path_status in report["runtime_paths"]:
            status = "ok" if path_status["exists"] and path_status["readable"] and path_status["writable"] else "degraded"
            typer.echo(
                f"- {path_status['path']}: {status} "
                f"(exists={path_status['exists']} read={path_status['readable']} write={path_status['writable']})"
            )

    if report["systemd_units"]:
        typer.echo("\nSystemd units")
        for unit_status in report["systemd_units"]:
            availability = "available" if unit_status["available"] else "unavailable"
            typer.echo(f"- {unit_status['unit']}: {unit_status['state']} ({availability})")

    if report["health"]:
        typer.echo("\nHealth endpoints")
        for endpoint in report["health"]:
            status = "ok" if endpoint["reachable"] else "degraded"
            status_code = endpoint["status_code"] if endpoint["status_code"] is not None else "n/a"
            typer.echo(f"- {endpoint['name']}: {status} (url={endpoint['url']} status_code={status_code})")
            if endpoint["error"]:
                typer.echo(f"  error: {endpoint['error']}")

    if report["host_heartbeat"]:
        heartbeat = report["host_heartbeat"]
        typer.echo("\nHost heartbeat")
        typer.echo(
            _format_rows(
                [
                    ("Healthy", "yes" if heartbeat["healthy"] else "no"),
                    ("Reason", str(heartbeat["reason"])),
                    ("Last cycle", str(heartbeat["last_cycle_at"] or "n/a")),
                    ("Streams polled", str(heartbeat["last_cycle_streams_polled"] or "n/a")),
                    ("Feedback discovered", str(heartbeat["last_cycle_discovered_feedback"] or "n/a")),
                    ("Next sleep seconds", str(heartbeat["last_cycle_next_sleep_seconds"] or "n/a")),
                    ("Last busy skip", str(heartbeat["last_busy_skip_at"] or "n/a")),
                    ("Busy skip reason", str(heartbeat["last_busy_skip_reason"] or "n/a")),
                    ("Last feedback detected", str(heartbeat["last_feedback_detected_at"] or "n/a")),
                    ("Last resume outcome", str(heartbeat["last_resume_outcome"] or "n/a")),
                    ("Last resume reason", str(heartbeat["last_resume_reason"] or "n/a")),
                    ("Last vm start outcome", str(heartbeat["last_vm_start_outcome"] or "n/a")),
                    ("Last vm start reason", str(heartbeat["last_vm_start_reason"] or "n/a")),
                ]
            )
        )

    session_summary = report["sessions"]
    typer.echo("\nSessions")
    typer.echo(
        _format_rows(
            [
                ("Total", str(session_summary["total"])),
                ("Active", str(session_summary["active"])),
                ("Expired", str(session_summary["expired"])),
                ("Next expiration", str(session_summary["next_expiration"] or "n/a")),
                ("Last expiration", str(session_summary["last_expiration"] or "n/a")),
            ]
        )
    )


@app.command(
    "create-session",
    rich_help_panel="Sessions and Feedback",
    short_help="Create a session manifest.",
)
def create_session(
    session_id: str,
    config: ConfigOption = Path("config/gateway.yaml"),
    vm_id: Annotated[str | None, typer.Option("--vm-id", help="VM identifier to bind to the session.")] = None,
    discord_channel: Annotated[
        list[str] | None,
        typer.Option("--discord-channel", help="Allowlisted Discord control channel."),
    ] = None,
    email_folder: Annotated[
        list[str] | None,
        typer.Option("--email-folder", help="Allowlisted email folder."),
    ] = None,
    ttl_hours: Annotated[int | None, typer.Option("--ttl-hours", help="Session time to live in hours.")] = None,
) -> None:
    cfg = load_config(config)
    runtime = build_runtime(cfg)
    manifest = SessionManifest(
        session_id=session_id,
        vm_id=vm_id,
        directory_path="root",
        allowed_tools=[
            ToolName.DISCORD_GET_COMMANDS,
            ToolName.DISCORD_POST_STATUS,
            ToolName.EMAIL_LIST_UNREAD,
            ToolName.EMAIL_GET_MESSAGE,
            ToolName.EMAIL_CREATE_DRAFT,
            ToolName.APPROVAL_SUBMIT,
            ToolName.APPROVAL_CHECK,
            ToolName.HAS_PENDING_FEEDBACK,
            ToolName.LIST_UNREAD_FEEDBACK,
            ToolName.LIST_ALL_FEEDBACK,
            ToolName.POST_PR_FEEDBACK_REPLY,
        ],
        discord_channel_allowlist=discord_channel or [],
        email_folder_allowlist=email_folder or [],
        email_mailbox_allowlist=cfg.email_mailbox_allowlist,
        email_folder_allowlist_by_mailbox=cfg.email_folder_allowlist_by_mailbox,
        email_draft_recipient_allowlist=cfg.email_draft_recipient_allowlist,
        email_draft_domain_allowlist=cfg.email_draft_domain_allowlist,
        email_send_enabled=cfg.email_send_enabled,
        expires_at=datetime.now(UTC) + _resolve_session_ttl(cfg, ttl_hours),
    )
    if manifest.email_send_enabled:
        manifest.allowed_tools.append(ToolName.EMAIL_SEND_APPROVED)
    runtime.sessions.put(session_id, manifest)
    typer.echo(f"Created session manifest {session_id}")


@app.command(
    "prune-sessions",
    rich_help_panel="Sessions and Feedback",
    short_help="Delete expired or all sessions.",
)
def prune_sessions(
    config: ConfigOption = Path("config/gateway.yaml"),
    expired_only: Annotated[
        bool,
        typer.Option(
            "--expired-only/--all",
            help="Delete only expired session manifests, or all session manifests.",
        ),
    ] = True,
) -> None:
    cfg = load_config(config)
    runtime = build_runtime(cfg)
    sessions = runtime.sessions.read_all()
    now = datetime.now(UTC)

    if expired_only:
        keys_to_delete = [
            session_id
            for session_id, session in sessions.items()
            if session.expires_at <= now
        ]
    else:
        keys_to_delete = list(sessions.keys())

    for session_id in keys_to_delete:
        runtime.sessions.delete(session_id)

    mode = "expired" if expired_only else "all"
    typer.echo(f"Pruned {len(keys_to_delete)} {mode} session(s)")


@app.command(
    "list-sessions",
    rich_help_panel="Sessions and Feedback",
    short_help="List active, expired, or all session manifests.",
)
def list_sessions(
    config: ConfigOption = Path("config/gateway.yaml"),
    all_sessions: Annotated[
        bool,
        typer.Option("--all", help="Show both active and expired sessions."),
    ] = False,
    expired_only: Annotated[
        bool,
        typer.Option("--expired-only", help="Show only expired sessions."),
    ] = False,
) -> None:
    if all_sessions and expired_only:
        raise typer.BadParameter("--all and --expired-only cannot be used together.")

    cfg = load_config(config)
    runtime = build_runtime(cfg)
    sessions = runtime.sessions.read_all()
    now = datetime.now(UTC)

    selected: list[tuple[str, SessionManifest]] = []
    for item in sorted(sessions.items(), key=_session_sort_key):
        _, manifest = item
        is_expired = manifest.expires_at <= now
        if expired_only:
            if is_expired:
                selected.append(item)
        elif all_sessions:
            selected.append(item)
        elif not is_expired:
            selected.append(item)

    if not selected:
        typer.echo("No sessions found.")
        return

    for session_id, manifest in selected:
        status = "expired" if manifest.expires_at <= now else "active"
        typer.echo(
            f"{session_id}\tstatus={status}\tvm_id={manifest.vm_id or '-'}\t"
            f"created_at={manifest.created_at.isoformat()}\texpires_at={manifest.expires_at.isoformat()}"
        )


@app.command(
    "renew-session",
    rich_help_panel="Sessions and Feedback",
    short_help="Extend or reissue a session manifest.",
)
def renew_session(
    target: str,
    extension: Annotated[
        str,
        typer.Option(
            "--extend-by",
            help="Duration to set from now, in <number><h|d> form.",
        ),
    ] = "1d",
    config: ConfigOption = Path("config/gateway.yaml"),
) -> None:
    cfg = load_config(config)
    runtime = build_runtime(cfg)
    sessions = runtime.sessions.read_all()
    session_id, manifest = _resolve_session_target(target, sessions)
    duration = _parse_session_extension(extension)
    base_time = datetime.now(UTC)
    previous_expires_at = manifest.expires_at
    manifest.expires_at = base_time + duration
    runtime.sessions.put(session_id, manifest)
    typer.echo(
        f"Renewed session {session_id} "
        f"(previous_expires_at={previous_expires_at.isoformat()} "
        f"new_expires_at={manifest.expires_at.isoformat()})"
    )


@app.command(
    "revoke-session",
    rich_help_panel="Sessions and Feedback",
    short_help="Expire a session immediately.",
)
def revoke_session(
    target: str,
    config: ConfigOption = Path("config/gateway.yaml"),
) -> None:
    cfg = load_config(config)
    runtime = build_runtime(cfg)
    sessions = runtime.sessions.read_all()
    session_id, manifest = _resolve_session_target(target, sessions)
    previous_expires_at = manifest.expires_at
    manifest.expires_at = datetime.now(UTC)
    runtime.sessions.put(session_id, manifest)
    typer.echo(
        f"Revoked session {session_id} "
        f"(previous_expires_at={previous_expires_at.isoformat()} "
        f"new_expires_at={manifest.expires_at.isoformat()})"
    )


@app.command(
    "delete-session",
    rich_help_panel="Sessions and Feedback",
    short_help="Delete a session manifest.",
)
def delete_session(
    target: str,
    config: ConfigOption = Path("config/gateway.yaml"),
) -> None:
    cfg = load_config(config)
    runtime = build_runtime(cfg)
    sessions = runtime.sessions.read_all()
    session_id, _ = _resolve_session_target(target, sessions)
    runtime.sessions.delete(session_id)
    typer.echo(f"Deleted session {session_id}")


@app.command(
    "delete-expired-sessions",
    rich_help_panel="Sessions and Feedback",
    short_help="Delete all expired session manifests.",
)
def delete_expired_sessions(
    config: ConfigOption = Path("config/gateway.yaml"),
) -> None:
    cfg = load_config(config)
    runtime = build_runtime(cfg)
    sessions = runtime.sessions.read_all()
    now = datetime.now(UTC)
    expired_session_ids = [
        session_id
        for session_id, session in sessions.items()
        if session.expires_at <= now
    ]
    for session_id in expired_session_ids:
        runtime.sessions.delete(session_id)
    typer.echo(f"Deleted {len(expired_session_ids)} expired session(s)")


@app.command(
    "destroy-issue-vm",
    rich_help_panel="Issue Lifecycle",
    short_help="Destroy an issue VM and state.",
)
def destroy_issue_vm(
    target: str,
    config: ConfigOption = Path("config/gateway.yaml"),
) -> None:
    issue_number = _resolve_issue_number_or_raise(target)
    vm_name = f"vm-{issue_number}"
    cfg = load_config(config)
    runtime = build_runtime(cfg)

    sessions = runtime.sessions.read_all()
    session_ids = _matching_issue_session_ids(issue_number, sessions)
    if session_ids:
        remaining_sessions = {
            session_id: manifest for session_id, manifest in sessions.items() if session_id not in session_ids
        }
        runtime.sessions.write_all(remaining_sessions)
    removed_approvals = _prune_approvals_for_sessions(runtime, session_ids=session_ids)

    removed_pr_map_entries, removed_streams = _prune_pr_map(cfg.pr_map_path, session_ids=session_ids)
    removed_cursors, removed_feedback = _prune_pr_feedback_store(
        cfg.runtime_dir / "pr-feedback-store.json",
        streams=removed_streams,
    )

    root_dir = Path(__file__).resolve().parents[2]
    bare_repo_removed, bare_repo_path = _remove_issue_bare_repo(
        issue_number,
        root_dir=root_dir,
        runtime_dir=cfg.runtime_dir,
    )

    destroy_script = root_dir / "scripts" / "destroy_agent_vm.sh"
    _run_subprocess_compat([str(destroy_script), vm_name], check=True, capture_output=False, text=False)

    typer.echo(
        f"Destroyed issue VM {vm_name}; "
        f"removed_sessions={len(session_ids)} "
        f"removed_pr_map_entries={removed_pr_map_entries} "
        f"removed_approvals={removed_approvals} "
        f"removed_feedback_cursors={removed_cursors} "
        f"removed_feedback_items={removed_feedback} "
        f"bare_repo_removed={bare_repo_removed} "
        f"bare_repo_path={bare_repo_path}"
    )


@app.command(
    "prune-orphaned-issue-state",
    rich_help_panel="Issue Lifecycle",
    short_help="Remove issue state for missing VMs.",
)
def prune_orphaned_issue_state(
    config: ConfigOption = Path("config/gateway.yaml"),
) -> None:
    cfg = load_config(config)
    runtime = build_runtime(cfg)
    live_vm_names = _list_existing_issue_vm_names()
    live_issue_numbers = {
        issue_number
        for issue_number in (_extract_issue_number(vm_name) for vm_name in live_vm_names)
        if issue_number is not None
    }
    root_dir = Path(__file__).resolve().parents[2]

    sessions = runtime.sessions.read_all()
    orphan_session_ids = {
        session_id
        for session_id, session in sessions.items()
        if (
            (issue_number := (_extract_issue_number(session.vm_id) or _extract_issue_number(session_id))) is not None
            and f"vm-{issue_number}" not in live_vm_names
        )
    }
    orphan_issue_numbers = {
        issue_number
        for issue_number in (
            (_extract_issue_number(sessions[session_id].vm_id) or _extract_issue_number(session_id))
            for session_id in orphan_session_ids
        )
        if issue_number is not None
    }
    orphan_issue_numbers.update(
        issue_number
        for issue_number in _issue_numbers_from_pr_map(cfg.pr_map_path)
        if issue_number not in live_issue_numbers
    )
    orphan_issue_numbers.update(
        issue_number
        for issue_number in _issue_numbers_from_bare_repos(root_dir)
        if issue_number not in live_issue_numbers
    )

    session_streams = _session_streams_for_session_ids(sessions, session_ids=orphan_session_ids)
    if orphan_session_ids:
        runtime.sessions.write_all(
            {session_id: manifest for session_id, manifest in sessions.items() if session_id not in orphan_session_ids}
        )
    removed_approvals = _prune_approvals_for_sessions(runtime, session_ids=orphan_session_ids)

    removed_pr_map_entries_a, removed_pr_map_streams_a = _prune_pr_map(
        cfg.pr_map_path,
        session_ids=orphan_session_ids,
    )
    removed_pr_map_entries_b, removed_pr_map_streams_b = _prune_pr_map_for_issue_numbers(
        cfg.pr_map_path,
        issue_numbers=orphan_issue_numbers,
    )
    removed_streams = session_streams | removed_pr_map_streams_a | removed_pr_map_streams_b
    removed_cursors, removed_feedback = _prune_pr_feedback_store(
        cfg.runtime_dir / "pr-feedback-store.json",
        streams=removed_streams,
    )

    removed_bare_repo_count = 0
    for issue_number in sorted(orphan_issue_numbers):
        if issue_number in live_issue_numbers:
            continue
        bare_repo_removed, _ = _remove_issue_bare_repo(
            issue_number,
            root_dir=root_dir,
            runtime_dir=cfg.runtime_dir,
        )
        if bare_repo_removed:
            removed_bare_repo_count += 1

    typer.echo(
        f"Pruned orphaned issue state "
        f"removed_sessions={len(orphan_session_ids)} "
        f"removed_approvals={removed_approvals} "
        f"removed_pr_map_entries={removed_pr_map_entries_a + removed_pr_map_entries_b} "
        f"removed_feedback_cursors={removed_cursors} "
        f"removed_feedback_items={removed_feedback} "
        f"removed_bare_repos={removed_bare_repo_count}"
    )


@app.command(
    "list-vms",
    rich_help_panel="VM Operations",
    short_help="List known VMs.",
)
def list_vms(
    vm_user: Annotated[str, typer.Option("--vm-user", help="Guest user to inspect over SSH.")] = "agent",
    all_vms: Annotated[
        bool,
        typer.Option("--all", help="Include inactive libvirt VMs in the output."),
    ] = False,
    omit_state: Annotated[
        bool,
        typer.Option(
            "--omit-state",
            help="Skip SSH guest state inspection for faster VM listings.",
        ),
    ] = False,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable VM summaries."),
    ] = False,
    config: ConfigOption = Path("config/gateway.yaml"),
) -> None:
    if as_json:
        service = AdminService(config)
        payload = [
            summary.model_dump(mode="json")
            for summary in service.list_vms(include_inactive=all_vms, omit_state=omit_state, vm_user=vm_user)
        ]
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    script_path = Path(__file__).resolve().parents[2] / "scripts" / "list_running_vms.sh"
    cmd = [str(script_path)]
    if all_vms:
        cmd.append("--all")
    if omit_state:
        cmd.append("--omit-state")
    cmd.append(vm_user)
    _run_subprocess_compat(cmd, check=True, capture_output=False, text=False)


def _echo_action_result_or_exit(result) -> None:
    typer.echo(result.message)
    if not result.success:
        raise typer.Exit(code=1)


@app.command(
    "start-vm",
    rich_help_panel="VM Operations",
    short_help="Start a VM.",
)
def start_vm(
    target: str,
    config: ConfigOption = Path("config/gateway.yaml"),
) -> None:
    service = AdminService(config)
    _echo_action_result_or_exit(service.start_vm(target))


@app.command(
    "shutdown-vm",
    rich_help_panel="VM Operations",
    short_help="Gracefully stop a VM.",
)
def shutdown_vm(
    target: str,
    config: ConfigOption = Path("config/gateway.yaml"),
    timeout_seconds: Annotated[
        int,
        typer.Option("--timeout-seconds", min=1, help="How long to wait for graceful shutdown."),
    ] = 60,
) -> None:
    service = AdminService(config)
    _echo_action_result_or_exit(service.shutdown_vm(target, timeout_seconds=timeout_seconds))


@app.command(
    "create-local-issue",
    rich_help_panel="Issue Lifecycle",
    short_help="Create a local issue record.",
)
def create_local_issue(
    config: ConfigOption = Path("config/gateway.yaml"),
    title: Annotated[
        str,
        typer.Option("--title", help="Local issue title."),
    ] = "",
    body: Annotated[
        str,
        typer.Option("--body", help="Local issue body/description."),
    ] = "",
    repo_source: Annotated[
        str | None,
        typer.Option("--repo-source", help="Default repo source used when starting a VM for this local issue."),
    ] = None,
    yaml_path: Annotated[
        Path | None,
        typer.Option("--yaml-path", help="Import an existing issue YAML file instead of creating one from title/body."),
    ] = None,
) -> None:
    service = AdminService(config)
    issue = service.create_local_issue(
        title=title,
        body=body,
        repo_source=repo_source,
        yaml_path=str(yaml_path) if yaml_path is not None else None,
    )
    typer.echo(f"Created local issue {issue.issue_number} ({issue.title}) at {issue.yaml_path}")


@app.command(
    "list-local-issues",
    rich_help_panel="Issue Lifecycle",
    short_help="List local issue records.",
)
def list_local_issues(
    config: ConfigOption = Path("config/gateway.yaml"),
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable local issue records."),
    ] = False,
) -> None:
    service = AdminService(config)
    issues = service.list_local_issues()
    if as_json:
        typer.echo(json.dumps([issue.model_dump(mode="json") for issue in issues], indent=2, sort_keys=True))
        return
    if not issues:
        return
    typer.echo(f"{'Issue':<8} {'Title':<32} YAML")
    for issue in issues:
        typer.echo(f"{issue.issue_number:<8} {issue.title[:32]:<32} {issue.yaml_path}")


@app.command(
    "ssh",
    rich_help_panel="VM Operations",
    short_help="Open an SSH session.",
)
def ssh(
    target: str,
    vm_user: Annotated[str, typer.Option("--vm-user", help="Guest user to connect as.")] = "agent",
) -> None:
    vm_name = _resolve_vm_name(target)
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "ssh.sh"
    _run_subprocess_compat([str(script_path), vm_name, vm_user], check=True, capture_output=False, text=False)


@app.command(
    "logs",
    rich_help_panel="VM Operations",
    short_help="Tail guest agent logs.",
)
def logs(
    target: str,
    repo_name: Annotated[
        str | None,
        typer.Option(
            "--repo-name",
            help="Repo basename used to select the guest agent log.",
        ),
    ] = None,
    lines: Annotated[
        int,
        typer.Option("--lines", min=1, help="Number of log lines to print before following the log."),
    ] = 10,
    vm_user: Annotated[str, typer.Option("--vm-user", help="Guest user to inspect over SSH.")] = "agent",
) -> None:
    issue_number = _resolve_issue_number_or_raise(target)
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "tail_issue_vm_log.sh"
    cmd = [str(script_path), issue_number, str(lines)]
    if repo_name is not None:
        cmd.append(repo_name)
        cmd.append(vm_user)
    elif vm_user != "agent":
        cmd.extend(["", vm_user])
    _run_subprocess_compat(cmd, check=True, capture_output=False, text=False)


def _set_agent_control_state(target: str, *, action: str, vm_user: str, config: Path) -> None:
    issue_number = _resolve_issue_number_or_raise(target)
    cfg = load_config(config)
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "set_issue_agent_state.sh"
    _run_subprocess_compat(
        [
            str(script_path),
            action,
            issue_number,
            vm_user,
            "--host-state-path",
            str(cfg.host_agent_control_state_path),
        ],
        check=True,
        capture_output=False,
        text=False,
    )


@app.command(
    "disable-agent",
    rich_help_panel="VM Operations",
    short_help="Pause the guest agent.",
)
def disable_agent(
    target: str,
    config: ConfigOption = Path("config/gateway.yaml"),
    vm_user: Annotated[str, typer.Option("--vm-user", help="Guest user to connect as.")] = "agent",
) -> None:
    _set_agent_control_state(target, action="disable", vm_user=vm_user, config=config)


@app.command(
    "enable-agent",
    rich_help_panel="VM Operations",
    short_help="Allow the guest agent to run.",
)
def enable_agent(
    target: str,
    config: ConfigOption = Path("config/gateway.yaml"),
    vm_user: Annotated[str, typer.Option("--vm-user", help="Guest user to connect as.")] = "agent",
) -> None:
    _set_agent_control_state(target, action="enable", vm_user=vm_user, config=config)


@app.command(
    "resume-agent",
    rich_help_panel="VM Operations",
    short_help="Resume an agent with a prompt.",
)
def resume_agent(
    target: str,
    message: Annotated[
        str,
        typer.Option(
            "--message",
            help="Prompt passed to the agent resume command.",
        ),
    ],
    config: ConfigOption = Path("config/gateway.yaml"),
    observe: Annotated[
        bool,
        typer.Option(
            "--observe",
            help="Tail the guest agent log after resuming the agent instead of returning immediately.",
        ),
    ] = False,
    lines: Annotated[
        int,
        typer.Option("--lines", min=1, help="Number of log lines to print before following when observing."),
    ] = 10,
    vm_user: Annotated[str, typer.Option("--vm-user", help="Guest user to connect as.")] = "agent",
) -> None:
    issue_number = _resolve_issue_number_or_raise(target)
    vm_name = f"vm-{issue_number}"
    cfg = load_config(config)
    runtime = build_runtime(cfg)
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "resume_issue_agent.sh"
    cmd = [
        str(script_path),
        message,
        issue_number,
        vm_user,
        "--host-control-state-path",
        str(cfg.host_agent_control_state_path),
    ]
    if observe:
        cmd.extend(["--observe", str(lines)])

    runtime.audit.log_decision(
        event_type=AuditEventType.VM_START_ATTEMPTED,
        session_id=target,
        vm_id=vm_name,
        decision="attempted",
        reason="resume-agent-command",
    )
    runtime.audit.log_decision(
        event_type=AuditEventType.RESUME_ATTEMPTED,
        session_id=target,
        vm_id=vm_name,
        decision="attempted",
        reason="resume-agent-command",
    )
    if observe:
        completed = subprocess.run(cmd, check=False)
    else:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
        )
    if completed.returncode == 0:
        runtime.audit.log_decision(
            event_type=AuditEventType.VM_START_SUCCEEDED,
            session_id=target,
            vm_id=vm_name,
            decision="succeeded",
            reason="resume-script-success",
        )
        runtime.audit.log_decision(
            event_type=AuditEventType.RESUME_SUCCEEDED,
            session_id=target,
            vm_id=vm_name,
            decision="succeeded",
            reason="resume-script-success",
        )
        return

    reason = f"resume-script-exit-{completed.returncode}"
    runtime.audit.log_decision(
        event_type=AuditEventType.VM_START_FAILED,
        session_id=target,
        vm_id=vm_name,
        decision="failed",
        reason=reason,
        args={"stderr": (completed.stderr or "").strip()},
    )
    runtime.audit.log_decision(
        event_type=AuditEventType.RESUME_FAILED,
        session_id=target,
        vm_id=vm_name,
        decision="failed",
        reason=reason,
        args={"stderr": (completed.stderr or "").strip()},
    )
    if getattr(completed, "stdout", ""):
        typer.echo(completed.stdout, nl=False)
    if getattr(completed, "stderr", ""):
        typer.echo(completed.stderr, err=True, nl=False)
    raise typer.Exit(code=completed.returncode)


@app.command(
    "create-issue-vm",
    rich_help_panel="Issue Lifecycle",
    short_help="Create and bootstrap an issue VM.",
)
def create_issue_vm(
    issue: str,
    config: ConfigOption = Path("config/gateway.yaml"),
    session_id: Annotated[
        str | None,
        typer.Option("--session-id", help="Session identifier to create and pass into the VM setup flow."),
    ] = None,
    repo_source: Annotated[
        str | None,
        typer.Option(
            "--repo-source",
            help="Host repo path or remote URL to mirror for guest Git access.",
        ),
    ] = None,
    vm_user: Annotated[str, typer.Option("--vm-user", help="Guest user to configure.")] = "agent",
    ttl_hours: Annotated[int | None, typer.Option("--ttl-hours", help="Session time to live in hours.")] = None,
    discord_channel: Annotated[
        list[str],
        typer.Option("--discord-channel", help="Allowlisted Discord control channel."),
    ] = [],
    email_mailbox: Annotated[
        list[str],
        typer.Option("--email-mailbox", help="Allowlisted mailbox id. Repeat for multiple mailboxes."),
    ] = [],
    email_folder: Annotated[
        list[str],
        typer.Option("--email-folder", help="Allowlisted email folder. Repeat for multiple folders."),
    ] = [],
    python: Annotated[
        bool, typer.Option("--python", help="Bootstrap a guest-local Python virtualenv for Python-first repos.")
    ] = False,
    dotnet: Annotated[bool, typer.Option("--dotnet", help="Bootstrap the host .NET SDK into the guest.")] = False,
    nuget: Annotated[
        str | None,
        typer.Option(
            "--nuget",
            help="Project or solution path used to prewarm the shared NuGet cache and bootstrap NuGet relay config.",
        ),
    ] = None,
    mssql: Annotated[
        bool, typer.Option("--mssql", help="Provision a guest-local Microsoft SQL Server instance.")
    ] = False,
    mssql_init_root: Annotated[
        str | None,
        typer.Option(
            "--mssql-init-root",
            help="Host directory copied into /var/opt/mssql/init. If present, init.sql is executed.",
        ),
    ] = None,
    playwright: Annotated[
        bool, typer.Option("--playwright", help="Bootstrap the host-seeded Playwright runtime into the guest.")
    ] = False,
    frontend_deps: Annotated[
        bool,
        typer.Option(
            "--frontend-deps",
            help="Bootstrap host-seeded frontend install roots into the guest.",
        ),
    ] = False,
    frontend_host_checkout: Annotated[
        str | None,
        typer.Option(
            "--frontend-host-checkout",
            help="Host checkout root for frontend install roots.",
        ),
    ] = None,
    frontend_install_root: Annotated[
        list[str],
        typer.Option(
            "--frontend-install-root",
            help="Install root inside the host checkout. Repeat for multiple roots.",
        ),
    ] = [],
    start_agent: Annotated[
        bool,
        typer.Option(
            "--start-agent/--no-start-agent",
            help="Start the guest agent run after setup.",
        ),
    ] = True,
) -> None:
    issue_number = _resolve_issue_number(issue)
    resolved_session_id = session_id or _default_issue_session_id(issue_number)
    resolved_vm_name = f"vm-{issue_number}"

    cfg = load_config(config)
    runtime = build_runtime(cfg)
    if runtime.sessions.get(resolved_session_id) is not None:
        raise typer.BadParameter(f"Session already exists: {resolved_session_id}")
    if mssql and not os.environ.get("MSSQL_SA_PASSWORD", "").strip():
        raise typer.BadParameter("MSSQL_SA_PASSWORD must be set in the environment when --mssql is used.")
    if mssql_init_root is not None and not mssql:
        raise typer.BadParameter("--mssql-init-root requires --mssql.")

    manifest = _build_issue_session_manifest(
        cfg=cfg,
        session_id=resolved_session_id,
        vm_id=resolved_vm_name,
        ttl_hours=ttl_hours,
        discord_channels=discord_channel,
        email_mailboxes=email_mailbox,
        email_folders=email_folder,
    )
    runtime.sessions.put(resolved_session_id, manifest)

    setup_script = Path(__file__).resolve().parents[2] / "scripts" / "setup_issue_vm.sh"
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
    for install_root in frontend_install_root:
        cmd.extend(["--frontend-install-root", install_root])

    cmd.append(issue)
    if nuget is not None:
        cmd.append(nuget)
    if repo_source is not None:
        cmd.append(repo_source)
    cmd.extend([resolved_session_id, vm_user])

    _run_subprocess_compat(cmd, check=True, capture_output=False, text=False)
    typer.echo(
        f"Created issue VM session {resolved_session_id} for issue {issue_number} "
        f"using VM {resolved_vm_name}"
    )


@app.command(
    "add-tool-permission",
    rich_help_panel="Sessions and Feedback",
    short_help="Add an allowed tool to a session manifest.",
)
def add_tool_permission(
    target: str,
    tool: ToolName,
    config: ConfigOption = Path("config/gateway.yaml"),
) -> None:
    cfg = load_config(config)
    runtime = build_runtime(cfg)
    sessions = runtime.sessions.read_all()
    session_id, manifest = _resolve_session_target(target, sessions)
    if tool in manifest.allowed_tools:
        typer.echo(f"Tool permission {tool.value} already present on session {session_id}")
        return

    manifest.allowed_tools.append(tool)
    runtime.sessions.put(session_id, manifest)
    typer.echo(f"Added tool permission {tool.value} to session {session_id}")


@app.command(
    "revoke-tool-permission",
    rich_help_panel="Sessions and Feedback",
    short_help="Remove an allowed tool from a session manifest.",
)
def revoke_tool_permission(
    target: str,
    tool: ToolName,
    config: ConfigOption = Path("config/gateway.yaml"),
) -> None:
    cfg = load_config(config)
    runtime = build_runtime(cfg)
    sessions = runtime.sessions.read_all()
    session_id, manifest = _resolve_session_target(target, sessions)
    if tool not in manifest.allowed_tools:
        typer.echo(f"Tool permission {tool.value} not present on session {session_id}")
        return

    manifest.allowed_tools.remove(tool)
    runtime.sessions.put(session_id, manifest)
    typer.echo(f"Revoked tool permission {tool.value} from session {session_id}")


@app.command(
    "attach-pr-feedback",
    rich_help_panel="Sessions and Feedback",
    short_help="Bind a PR feedback stream.",
)
def attach_pr_feedback(
    session_id: str,
    provider: Annotated[str, typer.Option("--provider", help="Feedback provider name.")] ,
    repo: Annotated[str, typer.Option("--repo", help="Provider repository identifier.")] ,
    pr_id: Annotated[str, typer.Option("--pr-id", help="Pull request identifier.")] ,
    config: ConfigOption = Path("config/gateway.yaml"),
) -> None:
    cfg = load_config(config)
    runtime = build_runtime(cfg)
    manifest = runtime.sessions.get(session_id)
    if manifest is None:
        raise typer.BadParameter(f"Unknown session: {session_id}")

    stream = {
        "provider": provider.strip(),
        "repo": repo.strip(),
        "pr_id": pr_id.strip(),
    }
    if not all(stream.values()):
        raise typer.BadParameter("--provider, --repo, and --pr-id must all be non-empty.")

    existing_streams = manifest.metadata.get("pr_feedback_streams")
    if not isinstance(existing_streams, list):
        existing_streams = []

    if stream not in existing_streams:
        existing_streams.append(stream)
    manifest.metadata["pr_feedback_streams"] = existing_streams

    for tool in (
        ToolName.HAS_PENDING_FEEDBACK,
        ToolName.LIST_UNREAD_FEEDBACK,
        ToolName.LIST_ALL_FEEDBACK,
        ToolName.POST_PR_FEEDBACK_REPLY,
    ):
        if tool not in manifest.allowed_tools:
            manifest.allowed_tools.append(tool)

    runtime.sessions.put(session_id, manifest)
    typer.echo(
        f"Attached PR feedback stream {stream['provider']}:{stream['repo']}:{stream['pr_id']} "
        f"to session {session_id}"
    )


@app.command(
    "approve",
    rich_help_panel="Sessions and Feedback",
    short_help="Approve or deny a request.",
)
def approve(
    request_id: str,
    approver: str,
    config: ConfigOption = Path("config/gateway.yaml"),
    approve: Annotated[bool, typer.Option("--approve/--deny", help="Approve or deny the request.")] = True,
) -> None:
    cfg = load_config(config)
    runtime = build_runtime(cfg)
    request = runtime.approvals.get(request_id)
    if request is None:
        raise typer.BadParameter(f"Unknown approval request: {request_id}")
    request.status = ApprovalStatus.APPROVED if approve else ApprovalStatus.DENIED
    request.approver = approver
    request.decision_at = datetime.now(UTC)
    runtime.approvals.put(request_id, request)
    typer.echo(f"Updated approval {request_id} to {request.status.value}")


@app.command(
    "seed-email",
    rich_help_panel="Seeding and Fixtures",
    short_help="Insert a test email.",
)
def seed_email(
    message_id: str,
    folder: str,
    sender: str,
    subject: str,
    body_text: str,
    mailbox_id: Annotated[str, typer.Option("--mailbox-id", help="Mailbox identifier.")] = "default",
    recipient: Annotated[
        list[str],
        typer.Option("--recipient", help="Repeat for each recipient address."),
    ] = [],
    config: ConfigOption = Path("config/gateway.yaml"),
) -> None:
    if not recipient:
        raise typer.BadParameter("At least one --recipient value is required.")
    cfg = load_config(config)
    runtime = build_runtime(cfg)
    message = EmailMessage(
        message_id=message_id,
        mailbox_id=mailbox_id,
        folder=folder,
        sender=sender,
        recipients=recipient,
        subject=subject,
        body_text=body_text,
    )
    provider = runtime.email_service.provider
    if isinstance(provider, JsonEmailProvider):
        provider.email_store.put(message_id, message)
        typer.echo(f"Seeded email {message_id}")
        return

    if isinstance(provider, MultiMailboxEmailProvider):
        mailbox_provider = provider.providers_by_mailbox.get(mailbox_id)
        if isinstance(mailbox_provider, JsonEmailProvider):
            mailbox_provider.email_store.put(message_id, message)
            typer.echo(f"Seeded email {message_id}")
            return

    raise typer.BadParameter(
        f"mailbox {mailbox_id!r} is not configured with json_store provider and cannot be locally seeded"
    )


@app.command(
    "seed-discord-channel",
    rich_help_panel="Seeding and Fixtures",
    short_help="Insert a test Discord cursor.",
)
def seed_discord_channel(
    channel_id: str,
    config: ConfigOption = Path("config/gateway.yaml"),
) -> None:
    cfg = load_config(config)
    runtime = build_runtime(cfg)
    now = datetime.now(UTC)
    seed_cursor = str(int(now.timestamp() * 1_000_000) + next(_discord_seed_sequence))
    envelope = DiscordCommandEnvelope(
        command_id=seed_cursor,
        author_id="discord-channel-seed",
        channel_id=channel_id,
        content="seed",
        created_at=now,
        metadata={
            "source": "discord_channel_seed",
            "channel_id": channel_id,
            "author_id": "discord-channel-seed",
            "seeded_at": now.isoformat(),
        },
    )
    runtime.discord_service.command_store.put(seed_cursor, envelope)
    typer.echo(
        f"Seeded Discord channel {channel_id} with cursor {seed_cursor}. "
        "Use that cursor as the initial since_cursor for smoke tests."
    )




@app.command(
    "host-heartbeat",
    rich_help_panel="Background Workers",
    short_help="Run the host heartbeat worker.",
)
def host_heartbeat(
    config: ConfigOption = Path("config/gateway.yaml"),
    run_once: Annotated[
        bool,
        typer.Option("--once", help="Run a single heartbeat cycle and exit."),
    ] = False,
    log_level: Annotated[str, typer.Option("--log-level", help="Python logging level.")] = "INFO",
) -> None:
    logging_level = getattr(logging, log_level.upper(), None)
    if not isinstance(logging_level, int):
        raise typer.BadParameter(f"Invalid --log-level value: {log_level}")
    logging.basicConfig(
        level=logging_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    cfg = load_config(config)
    worker = build_host_heartbeat_worker(cfg)
    if run_once:
        result = worker.run_once_with_lock()
        typer.echo(
            f"Host heartbeat cycle complete streams={result.streams_polled} "
            f"discovered={result.discovered_feedback} sleep_seconds={result.next_sleep_seconds:.3f}"
        )
        return

    worker.run_forever()


@app.command(
    "poll-ado-backlog",
    rich_help_panel="Background Workers",
    short_help="Poll Azure DevOps backlog.",
)
def poll_ado_backlog(
    log_level: Annotated[str, typer.Option("--log-level", help="Python logging level.")] = "INFO",
    reprocess: Annotated[
        int | None,
        typer.Option(
            "--reprocess",
            help="Force reprocess a specific work item ID by forgetting previous state first.",
        ),
    ] = None,
    forget: Annotated[
        int | None,
        typer.Option("--forget", help="Forget a specific work item ID in the processed store, then exit."),
    ] = None,
    list_unprocessed: Annotated[
        bool,
        typer.Option(
            "--list-unprocessed",
            help="List eligible work items that match the worker query but are not yet in the processed store.",
        ),
    ] = False,
) -> None:
    run_worker_command(
        log_level=log_level,
        reprocess=reprocess,
        forget=forget,
        list_unprocessed=list_unprocessed,
    )


if __name__ == "__main__":
    app()
