"""Session and issue identity, policy, and persistence helpers."""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import typer

from evander.models import SessionManifest, ToolName
from evander.policy_config import EffectivePolicyConfig
from evander.pr_feedback.store import PrFeedbackStore
from evander.pr_tracking import PrMapStore
from evander.repositories.session_manifests import JsonSessionManifestRepository

from . import cli_common


def _default_issue_session_id(issue_number: str) -> str:
    return f"issue-{issue_number}-{datetime.now(cli_common.UTC):%Y%m%dT%H%M%SZ}-{os.getpid()}"


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


def _matching_issue_session_ids(
    issue_number: str, sessions: dict[str, SessionManifest]
) -> set[str]:
    vm_name = f"vm-{issue_number}"
    prefix = _issue_session_id_prefix(issue_number)
    matches: set[str] = set()
    for session_id, session in sessions.items():
        if session.vm_id == vm_name or session_id == issue_number or session_id.startswith(prefix):
            matches.add(session_id)
    return matches


def _matching_vm_session_ids(vm_name: str, sessions: dict[str, SessionManifest]) -> set[str]:
    return {session_id for session_id, session in sessions.items() if session.vm_id == vm_name}


def _parse_session_extension(value: str | None) -> timedelta:
    raw_value = (value or "1d").strip().lower()
    match = re.fullmatch(r"(\d+)([hd])", raw_value)
    if match is None:
        raise typer.BadParameter(
            "Extension must use the format <number><h|d>, for example 8h or 2d."
        )

    amount = int(match.group(1))
    unit = match.group(2)
    if unit == "h":
        return timedelta(hours=amount)
    return timedelta(days=amount)


def _default_session_ttl(policy: EffectivePolicyConfig) -> timedelta:
    return timedelta(seconds=policy.default_session_ttl_seconds)


def _resolve_session_ttl(policy: EffectivePolicyConfig, ttl_hours: int | None) -> timedelta:
    if ttl_hours is not None:
        return timedelta(hours=ttl_hours)
    return _default_session_ttl(policy)


def _resolve_session_target(
    target: str, sessions: dict[str, SessionManifest]
) -> tuple[str, SessionManifest]:
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
    matching_ids.update(_matching_vm_session_ids(normalized, sessions))

    if not matching_ids:
        raise typer.BadParameter(f"Unknown session target: {target}")

    resolved_session_id = max(
        matching_ids,
        key=lambda session_id: _session_sort_key((session_id, sessions[session_id])),
    )
    return resolved_session_id, sessions[resolved_session_id]


def _runtime_session_repository(runtime: object) -> JsonSessionManifestRepository:
    repository = getattr(runtime, "session_repository", None)
    if isinstance(repository, JsonSessionManifestRepository):
        return repository
    config = getattr(runtime, "config", None)
    path = getattr(config, "session_store_path", None)
    if path is None:
        raise RuntimeError("runtime session repository is unavailable")
    return JsonSessionManifestRepository(path)


def _runtime_sessions_by_id(runtime: object) -> dict[str, SessionManifest]:
    repository = _runtime_session_repository(runtime)
    return {session.session_id: session for session in repository.list_sessions()}


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
    return PrMapStore(path).prune_session_bindings(session_ids=session_ids)


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
    if not streams:
        return 0, 0
    if not path.exists():
        return 0, 0
    return PrFeedbackStore(path).prune_streams(streams)


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
    policy: EffectivePolicyConfig,
    session_id: str,
    vm_id: str,
    directory_path: str,
    ttl_hours: int | None,
    discord_channels: list[str],
    email_mailboxes: list[str],
    email_folders: list[str],
) -> SessionManifest:
    allowed_tools: list[ToolName] = [
        ToolName.APPROVAL_SUBMIT,
        ToolName.APPROVAL_CHECK,
        ToolName.STAGE_ARTIFACT,
    ]

    if discord_channels:
        allowed_tools.extend(
            [
                ToolName.DISCORD_GET_COMMANDS,
                ToolName.DISCORD_POST_STATUS,
                ToolName.ADD_MESSAGE_REACTION,
                ToolName.HAS_PENDING_DISCORD_MESSAGES,
                ToolName.LIST_UNREAD_DISCORD_MESSAGES,
                ToolName.LIST_ALL_DISCORD_MESSAGES,
            ]
        )

    email_requested = bool(email_mailboxes or email_folders)
    resolved_email_mailboxes = email_mailboxes or (
        policy.email_mailbox_allowlist if email_requested else []
    )
    resolved_email_folders = email_folders or (
        sorted(
            {
                folder
                for folders in policy.email_folder_allowlist_by_mailbox.values()
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
        if policy.email_send_enabled:
            allowed_tools.extend([ToolName.EMAIL_SEND, ToolName.EMAIL_SEND_APPROVED])

    metadata: dict[str, object] = {}
    if str(os.environ.get("AGENT_PROVIDER", "")).strip().lower() == "copilot":
        allowed_tools.append(ToolName.COPILOT_HOST_AUTH)
        metadata["agent_provider"] = "copilot"

    unique_tools = list(dict.fromkeys(allowed_tools))
    return SessionManifest(
        session_id=session_id,
        vm_id=vm_id,
        directory_path=directory_path,
        allowed_tools=unique_tools,
        approval_required_tools=list(policy.approval_required_tools),
        discord_channel_allowlist=discord_channels,
        email_folder_allowlist=resolved_email_folders,
        email_mailbox_allowlist=resolved_email_mailboxes,
        email_folder_allowlist_by_mailbox=policy.email_folder_allowlist_by_mailbox,
        email_draft_recipient_allowlist=policy.email_draft_recipient_allowlist,
        email_draft_domain_allowlist=policy.email_draft_domain_allowlist,
        email_send_enabled=policy.email_send_enabled,
        expires_at=datetime.now(cli_common.UTC) + _resolve_session_ttl(policy, ttl_hours),
        metadata=metadata,
    )
