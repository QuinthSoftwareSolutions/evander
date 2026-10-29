from __future__ import annotations

from evander.execution.logs import log_page_from_end as _log_page_from_end
from evander.admin.services.vm_agent.common_imports import (
    Any,
    DirectoryStore,
    GatewayConfig,
    Mapping,
    Path,
    RuntimeContext,
    SessionManifest,
    SkillLibraryRepository,
    ToolName,
    UTC,
    _normalize_plain_string_list,
    allowed_tools_for_capability_profile,
    datetime,
    load_communication_targets_config,
    normalize_skill_names,
    parse_teams_target,
    re,
    resolve_approval_notification_target_ids_for_directory,
    resources,
    sha256,
)
from evander.admin.services.vm_agent.common_models import (
    _ASSISTANT_AGENTS_DESTINATION_PATH,
    _ASSISTANT_AGENTS_TEMPLATE_NAME,
)
from evander.git_ssh_authorized_keys import remove_git_authorized_key_by_public_key_body
from evander.managed_git_grants import (
    LEGACY_MANAGED_GIT_GRANT_METADATA_KEYS,
    managed_git_grants_from_metadata,
)
from evander.email.grants import (
    normalize_email_mailbox_grants,
    validate_email_mailbox_grants,
)
from evander.models import EmailMailboxGrant


def _deep_merge_mapping(base: Mapping[str, Any], update: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in update.items():
        existing = merged.get(key)
        if isinstance(existing, Mapping) and isinstance(value, Mapping):
            merged[key] = _deep_merge_mapping(existing, value)
            continue
        merged[key] = value
    return merged


def _managed_codex_skills_root(runtime_dir: Path, skill_names: list[str] | None) -> Path | None:
    names = normalize_skill_names(skill_names)
    if not names:
        return None
    repository = SkillLibraryRepository(runtime_dir)
    if all(repository.has_skill(name) for name in names):
        return repository.skills_root
    return None


def _safe_chat_assistant_identifier(value: str) -> str:
    raw_value = str(value or "").strip() or "anonymous"
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", raw_value).strip("-") or "anonymous"
    digest = sha256(raw_value.encode("utf-8")).hexdigest()[:10]
    return f"{normalized[:37]}-{digest}"


def _is_synthetic_chat_assistant_thread_id(value: str | None) -> bool:
    return str(value or "").strip().startswith("chat-thread-")


def _agent_provider_from_session(session: SessionManifest | Any | None) -> str | None:
    metadata = getattr(session, "metadata", {}) if session is not None else {}
    if isinstance(metadata, Mapping):
        candidate = str(
            metadata.get("agent_provider") or metadata.get("agent_harness") or ""
        ).strip()
        if candidate:
            return candidate
    return None


def _revoke_container_git_write_access_for_session(
    session: SessionManifest | None,
) -> bool:
    return _revoke_managed_git_access_for_session(session)


def _revoke_managed_git_access_for_session(
    session: SessionManifest | None,
) -> bool:
    if session is None:
        return False
    metadata = session.metadata if isinstance(session.metadata, Mapping) else {}
    revoked = False
    git_access_records = list(managed_git_grants_from_metadata(metadata).values())
    # Some pre-managed-repository session records contain revocation metadata but
    # no repository identity. They cannot become first-class grants, but teardown
    # must still remove their host authorization.
    for legacy_key in LEGACY_MANAGED_GIT_GRANT_METADATA_KEYS:
        legacy_access = metadata.get(legacy_key)
        if (
            isinstance(legacy_access, Mapping)
            and not str(legacy_access.get("managed_repo_id") or "").strip()
        ):
            git_access_records.append(dict(legacy_access))
    for git_access in git_access_records:
        public_key_body = str(git_access.get("public_key_body") or "").strip()
        authorized_keys_path = str(git_access.get("authorized_keys_path") or "").strip()
        if not public_key_body or not authorized_keys_path:
            continue
        revoked = (
            remove_git_authorized_key_by_public_key_body(
                authorized_keys_path=authorized_keys_path,
                public_key_body=public_key_body,
            )
            or revoked
        )
    return revoked


def _chat_thread_mapping_key(*, agent_thread_id: str | None, thread_id: str | None) -> str:
    normalized_agent_thread_id = str(agent_thread_id or "").strip()
    if normalized_agent_thread_id:
        return normalized_agent_thread_id
    normalized_thread_id = str(thread_id or "").strip()
    if normalized_thread_id:
        return f"admin-chat:{normalized_thread_id}"
    return "admin-chat:unbound"


def _chat_thread_id_from_queue_key(queue_key: str | None) -> str:
    normalized = str(queue_key or "").strip()
    if not normalized.startswith("chat:"):
        return ""
    parts = normalized.split(":", 2)
    return parts[1].strip() if len(parts) >= 2 else ""


def _assistant_capability_profile(settings: dict[str, Any] | None) -> str:
    payload = settings or {}
    if bool(payload.get("read_only", True)):
        return "read-only"
    configured = str(payload.get("default_profile") or "").strip()
    return configured or "operator-limited"


def _assistant_allowed_tools(profile: str) -> list[ToolName]:
    tools = list(allowed_tools_for_capability_profile(profile))
    if ToolName.ASSISTANT_POST_CHAT_REPLY not in tools:
        tools.append(ToolName.ASSISTANT_POST_CHAT_REPLY)
    return tools


def _assistant_mutation_policy(*, profile: str) -> dict[str, Any]:
    return {
        "writable_scopes": [
            "config/assistant",
            "config/workflow",
            "config/directory",
            "runtime/agents",
        ],
        "require_destructive_approval_gate": profile == "full-admin",
        "destructive_approval_gate": "approval_submit",
    }


def _assistant_agents_md_content(project_dir: Path) -> str:
    root = Path(project_dir)
    for candidate in (
        root / "src" / "evander" / "templates" / _ASSISTANT_AGENTS_TEMPLATE_NAME,
        root / "templates" / _ASSISTANT_AGENTS_TEMPLATE_NAME,
    ):
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    return (
        resources.files("evander")
        .joinpath("templates", _ASSISTANT_AGENTS_TEMPLATE_NAME)
        .read_text(encoding="utf-8")
    )


def _assistant_home_file_specs(project_dir: Path) -> list[dict[str, Any]]:
    return [
        {
            "destination_path": _ASSISTANT_AGENTS_DESTINATION_PATH,
            "mode": "0644",
            "content": _assistant_agents_md_content(project_dir),
        }
    ]


def _chat_assistant_vm_name(*, backend: str, session_id: str) -> str:
    if backend == "libvirt_vm":
        return "assistant-shared"
    return session_id


def _append_unique_string_values(base: list[str], values: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for value in [*base, *values]:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        merged.append(normalized)
    return merged


def _email_mailbox_id_from_auth_profile_ref(auth_profile_ref: object, *, target_id: str) -> str:
    normalized = str(auth_profile_ref or "").strip()
    if normalized.startswith("email."):
        normalized = normalized.removeprefix("email.")
    if not normalized:
        raise ValueError(
            f"email communication target {target_id!r} requires policy.auth_profile_ref"
        )
    return normalized


def _admin_agent_communication_allowlists(
    payload: Mapping[str, Any],
    *,
    config: GatewayConfig,
    directory_path: str,
) -> dict[str, list[str] | None]:
    discord_channels = _normalize_plain_string_list(
        payload.get("discord_channels") if "discord_channels" in payload else None,
        field_name="discord_channels",
    )
    teams_channel_allowlist = _normalize_plain_string_list(
        payload.get("teams_channel_allowlist") if "teams_channel_allowlist" in payload else None,
        field_name="teams_channel_allowlist",
    )
    teams_chat_allowlist = _normalize_plain_string_list(
        payload.get("teams_chat_allowlist") if "teams_chat_allowlist" in payload else None,
        field_name="teams_chat_allowlist",
    )
    teams_user_allowlist = _normalize_plain_string_list(
        payload.get("teams_user_allowlist") if "teams_user_allowlist" in payload else None,
        field_name="teams_user_allowlist",
    )
    email_mailboxes = _normalize_plain_string_list(
        payload.get("email_mailboxes") if "email_mailboxes" in payload else None,
        field_name="email_mailboxes",
    )
    email_folders = _normalize_plain_string_list(
        payload.get("email_folders") if "email_folders" in payload else None,
        field_name="email_folders",
    )
    has_communication_target_ids = "communication_target_ids" in payload
    communication_target_ids_are_legacy = (
        has_communication_target_ids and payload.get("communication_target_ids") is None
    )
    communication_target_ids = [
        target_id.lower()
        for target_id in _normalize_plain_string_list(
            payload.get("communication_target_ids")
            if "communication_target_ids" in payload
            else None,
            field_name="communication_target_ids",
        )
    ]
    if not communication_target_ids:
        return {
            "communication_target_ids": (
                None
                if not has_communication_target_ids or communication_target_ids_are_legacy
                else []
            ),
            "discord_channels": discord_channels or None,
            "teams_channel_allowlist": teams_channel_allowlist or None,
            "teams_chat_allowlist": teams_chat_allowlist or None,
            "teams_user_allowlist": teams_user_allowlist or None,
            "email_mailboxes": email_mailboxes or None,
            "email_folders": email_folders or None,
        }

    targets_by_id = load_communication_targets_config(
        runtime_dir=config.runtime_dir,
        directory_store=DirectoryStore(config.directories_store_path),
        directory_path=directory_path,
    ).by_id()
    selected_discord_channels: list[str] = []
    selected_teams_channels: list[str] = []
    selected_teams_chats: list[str] = []
    selected_teams_users: list[str] = []
    for target_id in communication_target_ids:
        target = targets_by_id.get(target_id)
        if target is None:
            raise ValueError(
                "communication_target_ids contains unknown target "
                f"{target_id!r} for directory {directory_path!r}"
            )
        if target.provider == "discord":
            selected_discord_channels.append(target.id)
            continue
        if target.provider == "teams":
            teams_kind = parse_teams_target(target.address).kind
            if teams_kind == "chat":
                selected_teams_chats.append(target.id)
            elif teams_kind == "user":
                selected_teams_users.append(target.id)
            else:
                selected_teams_channels.append(target.id)
            continue
        if target.provider == "email":
            if "write" not in set(target.policy.capabilities):
                raise ValueError(
                    f"email communication target {target.id!r} requires write capability"
                )
            _email_mailbox_id_from_auth_profile_ref(
                target.policy.auth_profile_ref,
                target_id=target.id,
            )
            continue
        raise ValueError(
            "communication_target_ids only supports Teams, Discord, and email targets; "
            f"target {target.id!r} uses provider {target.provider!r}"
        )

    return {
        "communication_target_ids": communication_target_ids,
        "discord_channels": _append_unique_string_values(
            discord_channels, selected_discord_channels
        )
        or None,
        "teams_channel_allowlist": _append_unique_string_values(
            teams_channel_allowlist,
            selected_teams_channels,
        )
        or None,
        "teams_chat_allowlist": _append_unique_string_values(
            teams_chat_allowlist,
            selected_teams_chats,
        )
        or None,
        "teams_user_allowlist": _append_unique_string_values(
            teams_user_allowlist,
            selected_teams_users,
        )
        or None,
        "email_mailboxes": email_mailboxes or None,
        "email_folders": email_folders or None,
    }


def _admin_agent_email_mailbox_grants(
    payload: Mapping[str, Any],
    *,
    runtime: RuntimeContext,
    directory_path: str,
) -> list[EmailMailboxGrant] | None:
    if "email_mailbox_grants" not in payload:
        return None
    grants = normalize_email_mailbox_grants(payload.get("email_mailbox_grants"))
    if grants is None:
        return None

    config = runtime.provider_config.resolve_for_directory(directory_path)
    policy = runtime.policy_config.resolve_for_directory(directory_path)
    return validate_email_mailbox_grants(
        grants,
        mailboxes=config.email_mailboxes,
        allowed_mailbox_ids=policy.email_mailbox_allowlist,
        folder_allowlist_by_mailbox=policy.email_folder_allowlist_by_mailbox,
        email_send_enabled=policy.email_send_enabled,
        directory_path=directory_path,
    )


def _resolve_session_approval_notification_target_ids(
    runtime: RuntimeContext,
    config: GatewayConfig,
    session: SessionManifest,
    target_ids: object,
) -> list[str]:
    return resolve_approval_notification_target_ids_for_directory(
        runtime_dir=config.runtime_dir,
        directory_store=runtime.directories,
        directory_path=session.directory_path,
        target_ids=target_ids,
    )


def _coerce_optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_optional_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _log_page_from_start(
    text: str,
    *,
    limit: int,
    offset: int,
    query: str | None = None,
) -> dict[str, object]:
    lines = text.splitlines()
    normalized_query = str(query or "").strip()
    if normalized_query:
        needle = normalized_query.casefold()
        lines = [line for line in lines if needle in line.casefold()]
    total_lines = len(lines)
    normalized_limit = max(1, int(limit))
    normalized_offset = max(0, int(offset))
    start_index = min(total_lines, normalized_offset)
    end_index = min(total_lines, start_index + normalized_limit)
    page_lines = lines[start_index:end_index]
    return {
        "text": "\n".join(page_lines) + ("\n" if page_lines else ""),
        "limit": normalized_limit,
        "offset": normalized_offset,
        "returned_lines": len(page_lines),
        "total_lines": total_lines,
        "start_line": start_index + 1 if page_lines else 0,
        "end_line": end_index if page_lines else 0,
        "has_more_before": end_index < total_lines,
        "query": normalized_query or None,
    }


def _source_project_dir() -> Path:
    return Path(__file__).resolve().parents[5]


__all__ = [
    "_admin_agent_communication_allowlists",
    "_admin_agent_email_mailbox_grants",
    "_agent_provider_from_session",
    "_append_unique_string_values",
    "_assistant_agents_md_content",
    "_assistant_allowed_tools",
    "_assistant_capability_profile",
    "_assistant_home_file_specs",
    "_assistant_mutation_policy",
    "_chat_assistant_vm_name",
    "_chat_thread_id_from_queue_key",
    "_chat_thread_mapping_key",
    "_coerce_optional_datetime",
    "_coerce_optional_string",
    "_deep_merge_mapping",
    "_email_mailbox_id_from_auth_profile_ref",
    "_is_synthetic_chat_assistant_thread_id",
    "_log_page_from_end",
    "_log_page_from_start",
    "_managed_codex_skills_root",
    "_revoke_container_git_write_access_for_session",
    "_revoke_managed_git_access_for_session",
    "_resolve_session_approval_notification_target_ids",
    "_safe_chat_assistant_identifier",
    "_source_project_dir",
]
