from __future__ import annotations

import os
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from evander.cron import cron_min_interval_seconds, validate_cron_expression
from evander.approval_notification_targets import (
    normalize_approval_notification_target_ids,
)
from evander.contact_allowlists import validate_allowed_contact_ids_for_directory
from evander.connectors.models import normalize_connector_grants
from evander.host_directories.models import normalize_host_directory_grants
from evander.email.grants import (
    email_mailbox_grant_policy_update,
    normalize_email_mailbox_grants,
)
from evander.models import (
    ApprovalGrantSettings,
    EmailMailboxAccess,
    EmailMailboxGrant,
    McpToolName,
    SessionHttpTargetsMetadata,
    SessionManifest,
    ToolName,
    normalize_approval_tool_settings,
    normalize_tool_name,
    tool_name_value,
)
from evander.policy_config import EffectivePolicyConfig
from evander.provider_config import (
    MODEL_PROVIDER_MODE_METADATA_KEY,
    normalize_model_provider_mode_override,
)
from evander.runtime import RuntimeContext
from evander.services import (
    normalize_teams_chat_or_target_refs,
    normalize_teams_channel_or_target_refs,
    normalize_teams_user_or_target_refs,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def extract_issue_number(value: str | None) -> str | None:
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


def read_issue_number_from_yaml(issue_path: Path) -> str:
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


def resolve_issue_number(issue_input: str) -> str:
    candidate = Path(issue_input)
    if candidate.is_file():
        return read_issue_number_from_yaml(candidate)
    return issue_input


def resolve_vm_name(target: str) -> str:
    normalized = target.strip()
    if normalized.isdigit():
        return f"vm-{normalized}"
    return normalized


def resolve_vm_name_from_target(target: str, sessions: dict[str, SessionManifest]) -> str:
    normalized = target.strip()
    if not normalized:
        return ""
    if normalized in sessions:
        return sessions[normalized].vm_id or normalized
    return resolve_vm_name(normalized)


def issue_session_id_prefix(issue_number: str) -> str:
    return f"issue-{issue_number}-"


def parse_session_extension(value: str | None) -> timedelta:
    raw_value = (value or "1d").strip().lower()
    match = re.fullmatch(r"(\d+)([hd])", raw_value)
    if match is None:
        raise ValueError("Extension must use the format <number><h|d>, for example 8h or 2d.")
    amount = int(match.group(1))
    unit = match.group(2)
    return timedelta(hours=amount) if unit == "h" else timedelta(days=amount)


def normalize_resume_mode(value: str | None) -> str:
    normalized = str(value or "").strip() or "same_context"
    if normalized not in {"same_context", "new_context"}:
        raise ValueError("resume_mode must be one of: same_context, new_context")
    return normalized


def require_valid_cron(cron: str) -> str:
    try:
        return validate_cron_expression(cron)
    except ValueError as exc:
        raise ValueError(f"Invalid cron expression. {exc}") from exc


def cron_min_interval_seconds_for_schedule(expression: str) -> float | None:
    return cron_min_interval_seconds(expression, utc_now().replace(second=0, microsecond=0))


def default_session_ttl(policy: EffectivePolicyConfig) -> timedelta:
    return timedelta(seconds=policy.default_session_ttl_seconds)


def resolve_session_ttl(
    policy: EffectivePolicyConfig,
    ttl_hours: int | None,
    *,
    ttl_seconds: int | None = None,
) -> timedelta:
    if ttl_seconds is not None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be a positive integer")
        return timedelta(seconds=ttl_seconds)
    if ttl_hours is not None:
        return timedelta(hours=ttl_hours)
    return default_session_ttl(policy)


def matching_issue_session_ids(issue_number: str, sessions: dict[str, SessionManifest]) -> set[str]:
    vm_name = f"vm-{issue_number}"
    prefix = issue_session_id_prefix(issue_number)
    matches: set[str] = set()
    for session_id, session in sessions.items():
        if session.vm_id == vm_name or session_id == issue_number or session_id.startswith(prefix):
            matches.add(session_id)
    return matches


def session_sort_key(item: tuple[str, SessionManifest]) -> tuple[datetime, datetime, str]:
    session_id, manifest = item
    return (manifest.created_at, manifest.expires_at, session_id)


def matching_vm_session_ids(vm_name: str, sessions: dict[str, SessionManifest]) -> set[str]:
    return {
        session_id
        for session_id, session in sessions.items()
        if getattr(session, "vm_id", None) == vm_name
    }


def normalize_session_labels(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key).strip()
        if not key or raw_value is None:
            continue
        mapped_value = str(raw_value).strip()
        if mapped_value:
            normalized[key] = mapped_value
    return normalized


def deep_merge_mapping(base: Mapping[str, Any], update: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in update.items():
        existing = merged.get(key)
        if isinstance(existing, Mapping) and isinstance(value, Mapping):
            merged[key] = deep_merge_mapping(existing, value)
            continue
        merged[key] = value
    return merged


def resolve_session_target(
    target: str, sessions: dict[str, SessionManifest]
) -> tuple[str, SessionManifest]:
    normalized = target.strip()
    if not normalized:
        raise ValueError("Target must be a non-empty issue number, vm name, or session id.")

    direct_match = sessions.get(normalized)
    if direct_match is not None:
        return normalized, direct_match

    matching_ids: set[str] = set()
    issue_number = extract_issue_number(normalized)
    if issue_number is not None:
        matching_ids.update(matching_issue_session_ids(issue_number, sessions))
    matching_ids.update(matching_vm_session_ids(normalized, sessions))

    if not matching_ids:
        raise ValueError(f"Unknown session target: {target}")

    resolved_session_id = max(
        matching_ids,
        key=lambda session_id: session_sort_key((session_id, sessions[session_id])),
    )
    return resolved_session_id, sessions[resolved_session_id]


def default_issue_session_id(issue_number: str) -> str:
    return f"issue-{issue_number}-{utc_now():%Y%m%dT%H%M%SZ}-{os.getpid()}"


def default_vm_session_id(vm_name: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "-", vm_name.strip()).strip("-") or "vm"
    return f"{normalized}-{utc_now():%Y%m%dT%H%M%SZ}-{os.getpid()}"


def copilot_host_auth_enabled() -> bool:
    return str(os.environ.get("AGENT_PROVIDER", "")).strip().lower() == "copilot"


def normalize_allowed_tools(
    value: list[str | ToolName], *, field_name: str = "allowed_tools"
) -> list[McpToolName]:
    normalized_tools: list[McpToolName] = []
    seen: set[str] = set()
    for tool in value:
        normalized_tool = normalize_tool_name(tool, field_name=field_name)
        tool_value = tool_name_value(normalized_tool)
        if tool_value in seen:
            continue
        seen.add(tool_value)
        normalized_tools.append(normalized_tool)
    return normalized_tools


def normalize_allowed_script_names(value: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for script_name in value:
        candidate = str(script_name).strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        normalized.append(candidate)
    return normalized


def normalize_plain_string_list(value: object, *, field_name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a list of strings")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        candidate = str(item).strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        normalized.append(candidate)
    return normalized


def normalize_teams_channel_allowlist(value: object, *, field_name: str) -> list[str]:
    return normalize_teams_channel_or_target_refs(
        normalize_plain_string_list(value, field_name=field_name)
    )


def normalize_teams_user_allowlist(value: object, *, field_name: str) -> list[str]:
    return normalize_teams_user_or_target_refs(
        normalize_plain_string_list(value, field_name=field_name)
    )


def build_issue_session_manifest(
    *,
    policy: EffectivePolicyConfig,
    session_id: str,
    vm_id: str,
    issue_number: str | None,
    directory_path: str,
    ttl_hours: int | None,
    ttl_seconds: int | None = None,
    extra_allowed_tools: list[str | ToolName] | None = None,
    connector_grants: dict[str, list[str]] | None = None,
    host_directory_grants: dict[str, list[str]] | None = None,
    tool_argument_policies: dict[str, list[Any]] | None = None,
    allowed_script_names: list[str] | None = None,
    allowed_contact_ids: list[str] | None = None,
    discord_channels: list[str],
    teams_channel_allowlist: list[str] | None = None,
    teams_chat_allowlist: list[str] | None = None,
    teams_user_allowlist: list[str] | None = None,
    email_mailboxes: list[str],
    email_folders: list[str],
    communication_target_ids: list[str] | None = None,
    email_mailbox_grants: list[EmailMailboxGrant] | None = None,
    communication_file_receive_enabled: bool | None = None,
    communication_file_send_enabled: bool | None = None,
    communication_file_max_bytes: int | None = None,
    communication_file_require_approval_for_send: bool | None = None,
    web_search_enabled: bool = False,
    image_generation_enabled: bool = False,
) -> SessionManifest:
    resolved_allowed_tools: list[McpToolName] = [
        ToolName.APPROVAL_SUBMIT,
        ToolName.APPROVAL_CHECK,
        ToolName.STAGE_ARTIFACT,
    ]
    if discord_channels:
        resolved_allowed_tools.extend(
            [
                ToolName.DISCORD_GET_COMMANDS,
                ToolName.DISCORD_POST_STATUS,
                ToolName.ADD_MESSAGE_REACTION,
                ToolName.HAS_PENDING_DISCORD_MESSAGES,
                ToolName.LIST_UNREAD_DISCORD_MESSAGES,
                ToolName.LIST_ALL_DISCORD_MESSAGES,
            ]
        )

    email_requested = (
        bool(email_mailbox_grants)
        if email_mailbox_grants is not None
        else bool(email_mailboxes or email_folders)
    )
    if email_mailbox_grants is not None:
        resolved_email_mailboxes = [grant.mailbox_id for grant in email_mailbox_grants]
        resolved_email_folders_by_mailbox = {
            grant.mailbox_id: list(grant.folders) for grant in email_mailbox_grants
        }
        resolved_email_folders = sorted(
            {folder for grant in email_mailbox_grants for folder in grant.folders}
        )
    else:
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
        resolved_email_folders_by_mailbox = policy.email_folder_allowlist_by_mailbox
    if email_requested:
        resolved_allowed_tools.extend(
            [
                ToolName.EMAIL_LIST_UNREAD,
                ToolName.EMAIL_GET_MESSAGE,
            ]
        )
        if email_mailbox_grants is None or any(
            grant.access in {EmailMailboxAccess.DRAFT, EmailMailboxAccess.SEND}
            for grant in email_mailbox_grants
        ):
            resolved_allowed_tools.append(ToolName.EMAIL_CREATE_DRAFT)
        if policy.email_send_enabled and (
            email_mailbox_grants is None
            or any(grant.access == EmailMailboxAccess.SEND for grant in email_mailbox_grants)
        ):
            resolved_allowed_tools.extend([ToolName.EMAIL_SEND, ToolName.EMAIL_SEND_APPROVED])

    metadata = {"issue_number": issue_number} if issue_number is not None else {}
    if copilot_host_auth_enabled():
        resolved_allowed_tools.append(ToolName.COPILOT_HOST_AUTH)
        metadata["agent_provider"] = "copilot"

    if extra_allowed_tools:
        seen = {tool_name_value(tool) for tool in resolved_allowed_tools}
        for tool in normalize_allowed_tools(extra_allowed_tools):
            tool_value = tool_name_value(tool)
            if tool_value in seen:
                continue
            seen.add(tool_value)
            resolved_allowed_tools.append(tool)

    resolved_host_directory_grants = normalize_host_directory_grants(host_directory_grants or {})
    host_directory_tool_by_capability = {
        "list": ToolName.HOST_DIRECTORY_LIST,
        "import": ToolName.HOST_DIRECTORY_IMPORT,
        "delete": ToolName.HOST_DIRECTORY_DELETE,
    }
    seen_tool_values = {tool_name_value(tool) for tool in resolved_allowed_tools}
    for capabilities in resolved_host_directory_grants.values():
        for capability in capabilities:
            tool = host_directory_tool_by_capability[capability]
            if tool.value not in seen_tool_values:
                seen_tool_values.add(tool.value)
                resolved_allowed_tools.append(tool)

    return SessionManifest(
        session_id=session_id,
        vm_id=vm_id,
        directory_path=directory_path,
        allowed_tools=normalize_allowed_tools(
            [tool_name_value(tool) for tool in resolved_allowed_tools]
        ),
        connector_grants=connector_grants or {},
        host_directory_grants=resolved_host_directory_grants,
        approval_required_tools=list(policy.approval_required_tools),
        tool_argument_policies=tool_argument_policies or {},
        allowed_script_names=normalize_allowed_script_names(allowed_script_names or []),
        allowed_contact_ids=allowed_contact_ids,
        communication_target_ids=communication_target_ids,
        discord_channel_allowlist=discord_channels,
        teams_channel_allowlist=normalize_teams_channel_allowlist(
            teams_channel_allowlist or [], field_name="teams_channel_allowlist"
        ),
        teams_chat_allowlist=normalize_teams_chat_or_target_refs(teams_chat_allowlist),
        teams_user_allowlist=normalize_teams_user_allowlist(
            teams_user_allowlist or [], field_name="teams_user_allowlist"
        ),
        email_folder_allowlist=resolved_email_folders,
        email_mailbox_allowlist=resolved_email_mailboxes,
        email_folder_allowlist_by_mailbox=resolved_email_folders_by_mailbox,
        email_mailbox_grants=email_mailbox_grants,
        email_draft_recipient_allowlist=policy.email_draft_recipient_allowlist,
        email_draft_domain_allowlist=policy.email_draft_domain_allowlist,
        email_send_enabled=policy.email_send_enabled,
        communication_file_receive_enabled=(
            policy.communication_file_receive_enabled
            if communication_file_receive_enabled is None
            else communication_file_receive_enabled
        ),
        communication_file_send_enabled=(
            policy.communication_file_send_enabled
            if communication_file_send_enabled is None
            else communication_file_send_enabled
        ),
        communication_file_max_bytes=(
            policy.communication_file_max_bytes
            if communication_file_max_bytes is None
            else communication_file_max_bytes
        ),
        communication_file_allowed_mime_types=policy.communication_file_allowed_mime_types,
        communication_file_allowed_extensions=policy.communication_file_allowed_extensions,
        communication_file_blocked_extensions=policy.communication_file_blocked_extensions,
        communication_file_require_approval_for_send=(
            policy.communication_file_require_approval_for_send
            if communication_file_require_approval_for_send is None
            else communication_file_require_approval_for_send
        ),
        web_search_enabled=web_search_enabled,
        image_generation_enabled=image_generation_enabled,
        expires_at=utc_now() + resolve_session_ttl(policy, ttl_hours, ttl_seconds=ttl_seconds),
        metadata=metadata,
    )


def validate_allowed_contact_ids_for_runtime_directory(
    runtime: Any,
    *,
    session_id: str,
    directory_path: str,
    allowed_contact_ids: object,
) -> list[str] | None:
    if allowed_contact_ids is None:
        return None
    provider_config = getattr(runtime, "provider_config", None)
    resolve_contacts_for_directory = getattr(
        provider_config, "resolve_contacts_for_directory", None
    )
    if not callable(resolve_contacts_for_directory):
        raise RuntimeError("contacts config resolver is not available")
    contacts_config = resolve_contacts_for_directory(directory_path)
    return validate_allowed_contact_ids_for_directory(
        contacts_config,
        allowed_contact_ids,
        session_id=session_id,
        directory_path=directory_path,
    )


def prune_approvals_for_sessions(runtime: RuntimeContext, *, session_ids: set[str]) -> int:
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


def session_tool_argument_policies_from_payload(
    session: SessionManifest, value: object
) -> dict[str, list[Any]]:
    try:
        validated = SessionManifest.model_validate(
            {
                "session_id": session.session_id,
                "allowed_tools": list(session.allowed_tools),
                "expires_at": session.expires_at,
                "tool_argument_policies": value,
            }
        )
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc
    return validated.tool_argument_policies


COMMUNICATION_FILE_SESSION_POLICY_FIELDS: tuple[str, ...] = (
    "communication_file_receive_enabled",
    "communication_file_send_enabled",
    "communication_file_max_bytes",
    "communication_file_allowed_mime_types",
    "communication_file_allowed_extensions",
    "communication_file_blocked_extensions",
    "communication_file_require_approval_for_send",
)

SESSION_BOOL_POLICY_FIELDS: tuple[str, ...] = (
    "web_search_enabled",
    "image_generation_enabled",
)


def normalize_session_policy_bool(value: object, *, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{field_name} must be a boolean")


def normalize_session_policy_non_negative_int_or_none(
    value: object, *, field_name: str
) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be a non-negative integer or null")
    if value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer or null")
    return value


def communication_file_session_policy_update(payload: Mapping[str, Any]) -> dict[str, Any]:
    update: dict[str, Any] = {}
    for field_name in (
        "communication_file_receive_enabled",
        "communication_file_send_enabled",
        "communication_file_require_approval_for_send",
    ):
        if field_name in payload:
            update[field_name] = normalize_session_policy_bool(
                payload.get(field_name), field_name=field_name
            )
    if "communication_file_max_bytes" in payload:
        update["communication_file_max_bytes"] = normalize_session_policy_non_negative_int_or_none(
            payload.get("communication_file_max_bytes"),
            field_name="communication_file_max_bytes",
        )
    for field_name in (
        "communication_file_allowed_mime_types",
        "communication_file_allowed_extensions",
        "communication_file_blocked_extensions",
    ):
        if field_name in payload:
            update[field_name] = normalize_plain_string_list(
                payload.get(field_name), field_name=field_name
            )
    return update


def session_policy_update_from_payload(
    session: SessionManifest,
    payload: Mapping[str, Any],
    *,
    contacts_config: Any | None = None,
) -> dict[str, Any]:
    allowed_fields = {
        "approval_required_tools",
        "approval_tool_settings",
        "approval_notification_target_ids",
        "tool_argument_policies",
        "connector_grants",
        "host_directory_grants",
        "allowed_script_names",
        "allowed_contact_ids",
        "communication_target_ids",
        "email_mailbox_grants",
        "discord_channel_allowlist",
        "discord_channels",
        "teams_channel_allowlist",
        "teams_channels",
        "teams_chat_allowlist",
        "teams_user_allowlist",
        "http_targets",
        "model_provider_mode",
        "metadata",
        *SESSION_BOOL_POLICY_FIELDS,
        *COMMUNICATION_FILE_SESSION_POLICY_FIELDS,
    }
    unsupported = sorted(set(payload) - allowed_fields)
    if unsupported:
        raise ValueError("Unsupported session policy fields: " + ", ".join(unsupported))

    update: dict[str, Any] = {}
    if "communication_target_ids" in payload:
        raw_target_ids = payload.get("communication_target_ids")
        if raw_target_ids is None:
            update["communication_target_ids"] = None
        else:
            update["communication_target_ids"] = [
                target_id.lower()
                for target_id in normalize_plain_string_list(
                    raw_target_ids, field_name="communication_target_ids"
                )
            ]
    if "email_mailbox_grants" in payload:
        grants = normalize_email_mailbox_grants(payload.get("email_mailbox_grants"))
        update.update(email_mailbox_grant_policy_update(grants))
    if "allowed_script_names" in payload:
        update["allowed_script_names"] = normalize_allowed_script_names(
            normalize_plain_string_list(
                payload.get("allowed_script_names"), field_name="allowed_script_names"
            )
        )
    if "allowed_contact_ids" in payload:
        if contacts_config is None:
            raise ValueError("allowed_contact_ids requires contacts_config")
        update["allowed_contact_ids"] = validate_allowed_contact_ids_for_directory(
            contacts_config,
            payload.get("allowed_contact_ids"),
            session_id=session.session_id,
            directory_path=session.directory_path,
        )
    if "approval_required_tools" in payload:
        update["approval_required_tools"] = normalize_allowed_tools(
            normalize_plain_string_list(
                payload.get("approval_required_tools"),
                field_name="approval_required_tools",
            ),
            field_name="approval_required_tools",
        )
    if "approval_tool_settings" in payload:
        normalized_settings = normalize_approval_tool_settings(
            payload.get("approval_tool_settings"),
            field_name="approval_tool_settings",
        )
        update["approval_tool_settings"] = {
            tool_name: ApprovalGrantSettings.model_validate(settings)
            for tool_name, settings in normalized_settings.items()
        }
    if "approval_notification_target_ids" in payload:
        update["approval_notification_target_ids"] = normalize_approval_notification_target_ids(
            payload.get("approval_notification_target_ids"),
            field_name="approval_notification_target_ids",
        )
    if "tool_argument_policies" in payload:
        update["tool_argument_policies"] = session_tool_argument_policies_from_payload(
            session,
            payload.get("tool_argument_policies"),
        )
    if "connector_grants" in payload:
        update["connector_grants"] = normalize_connector_grants(payload.get("connector_grants"))
    if "host_directory_grants" in payload:
        update["host_directory_grants"] = normalize_host_directory_grants(
            payload.get("host_directory_grants")
        )

    discord_field = (
        "discord_channel_allowlist"
        if "discord_channel_allowlist" in payload
        else "discord_channels"
        if "discord_channels" in payload
        else None
    )
    if discord_field is not None:
        update["discord_channel_allowlist"] = normalize_plain_string_list(
            payload.get(discord_field),
            field_name=discord_field,
        )

    teams_field = (
        "teams_channel_allowlist"
        if "teams_channel_allowlist" in payload
        else "teams_channels"
        if "teams_channels" in payload
        else None
    )
    if teams_field is not None:
        update["teams_channel_allowlist"] = normalize_teams_channel_allowlist(
            payload.get(teams_field),
            field_name=teams_field,
        )
    if "teams_chat_allowlist" in payload:
        update["teams_chat_allowlist"] = normalize_teams_chat_or_target_refs(
            normalize_plain_string_list(
                payload.get("teams_chat_allowlist"), field_name="teams_chat_allowlist"
            )
        )
    if "teams_user_allowlist" in payload:
        update["teams_user_allowlist"] = normalize_teams_user_allowlist(
            payload.get("teams_user_allowlist"),
            field_name="teams_user_allowlist",
        )

    update.update(communication_file_session_policy_update(payload))

    for field_name in SESSION_BOOL_POLICY_FIELDS:
        if field_name in payload:
            update[field_name] = normalize_session_policy_bool(
                payload.get(field_name), field_name=field_name
            )

    if "metadata" in payload:
        metadata = payload.get("metadata")
        if not isinstance(metadata, Mapping):
            raise ValueError("metadata must be a JSON object")
        normalized_metadata = dict(metadata)
        if MODEL_PROVIDER_MODE_METADATA_KEY in normalized_metadata:
            model_provider_mode = normalize_model_provider_mode_override(
                normalized_metadata.get(MODEL_PROVIDER_MODE_METADATA_KEY)
            )
            if model_provider_mode is None:
                normalized_metadata.pop(MODEL_PROVIDER_MODE_METADATA_KEY, None)
            else:
                normalized_metadata[MODEL_PROVIDER_MODE_METADATA_KEY] = model_provider_mode
        if "http_targets" in normalized_metadata:
            normalized_metadata["http_targets"] = normalize_http_targets_metadata(
                normalized_metadata["http_targets"]
            )
        elif "http_targets" in session.metadata:
            normalized_metadata["http_targets"] = session.metadata["http_targets"]
        update["metadata"] = normalized_metadata

    if "http_targets" in payload:
        metadata = dict(update.get("metadata", session.metadata))
        metadata["http_targets"] = normalize_http_targets_metadata(payload.get("http_targets"))
        update["metadata"] = metadata

    if "model_provider_mode" in payload:
        model_provider_mode = normalize_model_provider_mode_override(
            payload.get("model_provider_mode")
        )
        metadata = dict(update.get("metadata", session.metadata))
        if model_provider_mode is None:
            metadata.pop(MODEL_PROVIDER_MODE_METADATA_KEY, None)
        else:
            metadata[MODEL_PROVIDER_MODE_METADATA_KEY] = model_provider_mode
        update["metadata"] = metadata

    return update


def normalize_http_targets_metadata(value: object) -> dict[str, Any]:
    try:
        normalized = SessionHttpTargetsMetadata.model_validate(value)
    except ValidationError as exc:
        raise ValueError(f"http_targets is invalid: {exc}") from exc
    return normalized.model_dump(mode="json")


def wake_eligible_discord_channel_ids(
    runtime: Any,
    session: SessionManifest,
) -> set[str]:
    allowed_tools = {tool_name_value(tool) for tool in session.allowed_tools}
    if tool_name_value(ToolName.DISCORD_GET_COMMANDS) not in allowed_tools:
        return set()

    policy_config = getattr(runtime, "policy_config", None)
    resolve = getattr(policy_config, "resolved_discord_channel_ids_for_session", None)
    if callable(resolve):
        channel_ids = resolve(session, capability="read")
    else:
        channel_ids = session.discord_channel_allowlist
    return {
        str(channel_id or "").strip() for channel_id in channel_ids if str(channel_id or "").strip()
    }


def sync_added_discord_session_cursors(
    runtime: Any,
    *,
    previous_session: SessionManifest | None,
    updated_session: SessionManifest,
) -> dict[str, str]:
    previous_channels = (
        wake_eligible_discord_channel_ids(runtime, previous_session)
        if previous_session is not None
        else set()
    )
    updated_channels = wake_eligible_discord_channel_ids(runtime, updated_session)
    added_channels = sorted(updated_channels - previous_channels)
    if not added_channels:
        return {}

    discord_inbox_service = getattr(runtime, "discord_inbox_service", None)
    fast_forward = getattr(discord_inbox_service, "fast_forward_session_cursors_to_latest", None)
    if not callable(fast_forward):
        return {}
    return dict(
        fast_forward(
            session_id=updated_session.session_id,
            channel_ids=added_channels,
        )
    )


_utc_now = utc_now
_extract_issue_number = extract_issue_number
_read_issue_number_from_yaml = read_issue_number_from_yaml
_resolve_issue_number = resolve_issue_number
_resolve_vm_name = resolve_vm_name
_resolve_vm_name_from_target = resolve_vm_name_from_target
_issue_session_id_prefix = issue_session_id_prefix
_parse_session_extension = parse_session_extension
_default_session_ttl = default_session_ttl
_resolve_session_ttl = resolve_session_ttl
_matching_issue_session_ids = matching_issue_session_ids
_session_sort_key = session_sort_key
_resolve_session_target = resolve_session_target
_default_issue_session_id = default_issue_session_id
_default_vm_session_id = default_vm_session_id
_copilot_host_auth_enabled = copilot_host_auth_enabled
_build_issue_session_manifest = build_issue_session_manifest
_matching_vm_session_ids = matching_vm_session_ids
_normalize_session_labels = normalize_session_labels
_deep_merge_mapping = deep_merge_mapping
_normalize_allowed_tools = normalize_allowed_tools
_session_tool_argument_policies_from_payload = session_tool_argument_policies_from_payload
_normalize_allowed_script_names = normalize_allowed_script_names
_normalize_plain_string_list = normalize_plain_string_list
_normalize_teams_channel_allowlist = normalize_teams_channel_allowlist
_normalize_teams_user_allowlist = normalize_teams_user_allowlist
_validate_allowed_contact_ids_for_directory = validate_allowed_contact_ids_for_runtime_directory
_prune_approvals_for_sessions = prune_approvals_for_sessions
_communication_file_session_policy_update = communication_file_session_policy_update
_session_policy_update_from_payload = session_policy_update_from_payload
_sync_added_discord_session_cursors = sync_added_discord_session_cursors
_wake_eligible_discord_channel_ids = wake_eligible_discord_channel_ids
