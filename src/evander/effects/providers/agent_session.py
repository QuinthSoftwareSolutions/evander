from __future__ import annotations

import html
import json
import re
import shlex
import time
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from evander.codex_auth import (
    CodexAuthMode,
    CodexTurnSpec,
    effective_codex_auth_mode,
    normalize_codex_auth_mode,
)
from evander.config import GatewayConfig
from evander.directory_config import ContactDirectoryConfig
from evander.runtime import allowed_tools_for_capability_profile
from evander.services import (
    normalize_teams_chat_or_target_refs,
    normalize_teams_channel_or_target_refs,
    normalize_teams_user_or_target_refs,
)
from evander.execution import (
    ExecutionBackendKind,
    ExecutionStatusKind,
    build_execution_backend,
)
from evander.models import (
    EmailMailboxAccess,
    EmailMailboxGrant,
    McpToolName,
    SessionManifest,
    ToolName,
    normalize_tool_name,
    tool_name_value,
)
from evander.repositories.session_manifests import (
    JsonSessionManifestRepository,
    as_session_manifest_repository,
)
from evander.session_expiry import ensure_session_not_expired
from evander.session_auth import MCP_SESSION_TOKEN_QUERY_PARAM_NAMES

from .base import EffectRuntimeContext
from .vm_targeting import (
    read_all_sessions as _read_all_sessions_canonical,
    resolve_session_id_from_vm_name as _resolve_session_id_from_vm_name_canonical,
    resolve_vm_name_from_session as _resolve_vm_name_from_session_canonical,
    resolve_vm_target as _resolve_vm_target_canonical,
)

_CODEX_NON_PREFIXED_MCP_TOOL_NAMES_FEATURE = "non_prefixed_mcp_tool_names"
_CODEX_OPTIONAL_FEATURE_ARGS_MARKER = "__EVANDER_CODEX_OPTIONAL_FEATURE_ARGS__"

NEXUS_PACKAGE_CACHE_PORT = 8081
NEXUS_PYPI_GROUP_REPOSITORY = "pypi-all"
NEXUS_NPM_GROUP_REPOSITORY = "npm-all"
DEFAULT_CODEX_AGENT_MODEL = "gpt-5.4"
DEFAULT_CLAUDE_AGENT_MODEL = "claude-sonnet-4-6"
_CONTAINER_THREAD_ID_LOG_PATTERN = re.compile(
    r"^session id:\s*(?P<thread_id>\S+)\s*$", re.IGNORECASE | re.MULTILINE
)


def _contains_url_control_character(value: str) -> bool:
    return any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)


def normalize_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def default_agent_model_for_provider(
    agent_provider: str | None = None,
    config: GatewayConfig | None = None,
) -> str:
    provider = normalize_optional_string(agent_provider) or default_agent_provider_for_config(
        config
    )
    if provider == "claude":
        return DEFAULT_CLAUDE_AGENT_MODEL
    return DEFAULT_CODEX_AGENT_MODEL


def default_agent_provider_for_config(config: GatewayConfig | None = None) -> str:
    if normalize_optional_string(getattr(config, "model_provider_mode", None)) == "anthropic":
        return "claude"
    return "codex"


def normalize_optional_http_url(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    raw_text = str(value)
    if _contains_url_control_character(raw_text):
        raise ValueError(f"{field_name} must be a valid http(s) URL, got {value!r}")
    text = raw_text.strip()
    if not text:
        raise ValueError(f"{field_name} must be a valid http(s) URL, got {value!r}")
    parts = urlsplit(text)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError(f"{field_name} must be a valid http(s) URL, got {value!r}")
    return _ensure_trailing_slash(text)


def normalize_existing_agent_behavior(value: Any) -> str:
    text = normalize_optional_string(value)
    if text is None:
        return "upsert"
    normalized = text.lower().replace("_", "-")
    if normalized == "upsert":
        return "upsert"
    if normalized in {"noop", "no-op", "no op"}:
        return "noop"
    raise ValueError("effect:agent:create existing_agent_behavior must be one of: upsert, noop")


def required_string(value: Any, message: str) -> str:
    text = normalize_optional_string(value)
    if text is None:
        raise ValueError(message)
    return text


def as_string_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return None
    normalized = [str(item).strip() for item in value if str(item).strip()]
    return normalized or None


def as_string_mapping(value: Any) -> dict[str, str] | None:
    if value is None or not isinstance(value, Mapping):
        return None
    normalized: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key).strip()
        if not key or raw_value is None:
            continue
        mapped_value = str(raw_value).strip()
        if mapped_value:
            normalized[key] = mapped_value
    return normalized or None


def as_int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def as_bool_or_none(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def normalize_teams_channel_allowlist(value: list[str] | None) -> list[str]:
    return normalize_teams_channel_or_target_refs(value)


def normalize_teams_chat_allowlist(value: list[str] | None) -> list[str]:
    return normalize_teams_chat_or_target_refs(value)


def normalize_teams_user_allowlist(value: list[str] | None) -> list[str]:
    return normalize_teams_user_or_target_refs(value)


def runtime_session_repository(context: EffectRuntimeContext, config: GatewayConfig):
    runtime = getattr(context.admin_service, "runtime", None)
    repository = getattr(runtime, "session_repository", None)
    if repository is None:
        repository = getattr(context.admin_service, "session_repository", None)
    if repository is not None:
        return as_session_manifest_repository(repository)
    return JsonSessionManifestRepository(config.session_store_path)


def _provider_config_resolvers(context: EffectRuntimeContext) -> tuple[Any, ...]:
    runtime = getattr(context.admin_service, "runtime", None)
    resolvers = (
        getattr(runtime, "provider_config", None),
        getattr(context.admin_service, "provider_config", None),
        getattr(context, "provider_config_resolver", None),
    )
    return tuple(resolver for resolver in resolvers if resolver is not None)


def effective_config_for_directory(
    context: EffectRuntimeContext,
    directory_path: str | None,
) -> GatewayConfig:
    for resolver in _provider_config_resolvers(context):
        resolve_for_directory = getattr(resolver, "resolve_for_directory", None)
        if callable(resolve_for_directory):
            return resolve_for_directory(directory_path)
    return GatewayConfig.model_validate(dict(context.provider_config))


def contacts_config_for_directory(
    context: EffectRuntimeContext,
    directory_path: str | None,
) -> ContactDirectoryConfig:
    for resolver in _provider_config_resolvers(context):
        resolve_contacts_for_directory = getattr(resolver, "resolve_contacts_for_directory", None)
        if callable(resolve_contacts_for_directory):
            return resolve_contacts_for_directory(directory_path)
    raise RuntimeError("contacts_config_for_directory is not available")


def effective_config_for_session(
    context: EffectRuntimeContext,
    session_id: str,
    *,
    fallback_directory_path: str | None = None,
) -> GatewayConfig:
    for resolver in _provider_config_resolvers(context):
        resolve_for_session_id = getattr(resolver, "resolve_for_session_id", None)
        if callable(resolve_for_session_id):
            try:
                return resolve_for_session_id(session_id)
            except ValueError:
                pass
    config = effective_config_for_directory(context, fallback_directory_path)
    repository = runtime_session_repository(context, config)
    session = repository.get_session(session_id)
    if session is not None:
        return effective_config_for_directory(context, session.directory_path)
    return config


def container_worker_image_for_session(
    session: SessionManifest | Any | None,
    config: GatewayConfig,
) -> str:
    metadata = getattr(session, "metadata", {}) if session is not None else {}
    if isinstance(metadata, Mapping):
        container_worker = metadata.get("container_worker")
        if isinstance(container_worker, Mapping):
            image = normalize_optional_string(container_worker.get("image"))
            if image:
                return image
    default_image = normalize_optional_string(
        getattr(config, "container_worker_default_image", None)
    )
    if default_image is None:
        raise ValueError("container_worker_default_image cannot be empty")
    return default_image


def execution_backend_for_session(
    session: SessionManifest | Any | None,
    *,
    fallback_config: GatewayConfig,
) -> str:
    provider = normalize_optional_string(getattr(session, "vm_provider", None))
    if provider is None:
        metadata = getattr(session, "metadata", {}) if session is not None else {}
        if isinstance(metadata, Mapping):
            provider = normalize_optional_string(
                metadata.get("execution_backend") or metadata.get("vm_provider")
            )
    if provider == ExecutionBackendKind.CONTAINER_WORKER_VM.value:
        return ExecutionBackendKind.CONTAINER_WORKER_VM.value
    if provider in {ExecutionBackendKind.LIBVIRT_VM.value, "libvirt", "azure"}:
        return ExecutionBackendKind.LIBVIRT_VM.value
    if provider:
        return provider
    return str(fallback_config.execution_backend)


def selected_execution_backend(
    context: EffectRuntimeContext,
    payload: Mapping[str, Any],
    *,
    directory_path: str | None,
) -> tuple[str, GatewayConfig]:
    config = effective_config_for_directory(context, directory_path)
    backend = normalize_optional_string(payload.get("execution_backend")) or str(
        config.execution_backend
    )
    return backend, config


def chat_assistant_scope_for_backend(execution_backend: str, *, user_id: str) -> tuple[str, str]:
    """Return assistant mapping scope key and effective owner key for backend-specific resolution."""
    normalized_backend = normalize_optional_string(execution_backend) or "libvirt_vm"
    normalized_user = normalize_optional_string(user_id) or "anonymous"
    if normalized_backend == "libvirt_vm":
        return "chat_assistant", "global"
    return "chat_assistant", normalized_user


def selected_execution_backend_for_target(
    context: EffectRuntimeContext,
    payload: Mapping[str, Any],
    *,
    directory_path: str | None,
) -> tuple[str, GatewayConfig]:
    explicit_backend = normalize_optional_string(payload.get("execution_backend"))
    base_config = effective_config_for_directory(context, directory_path)
    if explicit_backend is not None:
        return explicit_backend, base_config

    session_id = normalize_optional_string(payload.get("session_id"))
    target = normalize_optional_string(payload.get("target"))
    if session_id is None and target:
        session_id = target
    if session_id:
        try:
            session = runtime_session_repository(context, base_config).get_session(session_id)
        except Exception:
            session = None
        if session is not None:
            config = effective_config_for_session(
                context,
                session_id,
                fallback_directory_path=session.directory_path,
            )
            return execution_backend_for_session(session, fallback_config=config), config
    return str(base_config.execution_backend), base_config


def container_backend(context: EffectRuntimeContext, config: GatewayConfig):
    return build_execution_backend(
        config.model_copy(
            update={"execution_backend": ExecutionBackendKind.CONTAINER_WORKER_VM.value}
        ),
        admin_service=context.admin_service,
    )


def require_container_session_id(
    context: EffectRuntimeContext,
    payload: Mapping[str, Any],
    *,
    effect_type: str,
    config: GatewayConfig,
) -> tuple[str, SessionManifest | None]:
    session_id = normalize_optional_string(payload.get("session_id"))
    target = normalize_optional_string(payload.get("target"))
    if session_id is None and target:
        session_id = target
    if session_id is None:
        raise ValueError(f"{effect_type} with container_worker_vm requires session_id or target")

    repository = runtime_session_repository(context, config)
    session = repository.get_session(session_id)
    if session is None:
        raise ValueError(f"Unknown session: {session_id}")
    ensure_session_not_expired(session)
    return session_id, session


def resolve_active_session_if_available(
    context: EffectRuntimeContext,
    target: str,
    *,
    config: GatewayConfig,
) -> tuple[str | None, SessionManifest | None]:
    repository = runtime_session_repository(context, config)
    normalized_target = normalize_optional_string(target)
    if normalized_target is None:
        return None, None

    session = repository.get_session(normalized_target)
    if session is None:
        try:
            session = repository.resolve_session_target(normalized_target)
        except Exception:
            session = None
    if session is None:
        return None, None
    ensure_session_not_expired(session)
    session_id = (
        normalize_optional_string(getattr(session, "session_id", None)) or normalized_target
    )
    return session_id, session


def resolve_vm_target(
    context: EffectRuntimeContext,
    payload: Mapping[str, Any],
    *,
    effect_type: str,
) -> tuple[str, str]:
    resolved = _resolve_vm_target_canonical(context, payload, effect_type=effect_type)
    return resolved.vm_name, resolved.session_id


def resolve_vm_name_from_session(context: EffectRuntimeContext, session_id: str) -> str:
    return _resolve_vm_name_from_session_canonical(context, session_id)


def resolve_session_id_from_vm_name(context: EffectRuntimeContext, vm_name: str) -> str:
    return _resolve_session_id_from_vm_name_canonical(context, vm_name)


def read_all_sessions(context: EffectRuntimeContext) -> Mapping[str, Any]:
    return _read_all_sessions_canonical(context)


def normalize_allowed_tools(value: list[str] | None) -> list[McpToolName]:
    tools: list[McpToolName] = []
    seen: set[str] = set()
    for raw_tool in value or []:
        tool = normalize_tool_name(raw_tool, field_name="allowed_tools")
        tool_value = tool_name_value(tool)
        if tool_value in seen:
            continue
        seen.add(tool_value)
        tools.append(tool)
    return tools


def allowed_tools_for_profile(capability_profile: str | None) -> list[str]:
    return [tool.value for tool in allowed_tools_for_capability_profile(capability_profile)]


def normalize_allowed_script_names(value: list[str] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_name in value or []:
        script_name = str(raw_name).strip()
        if not script_name or script_name in seen:
            continue
        seen.add(script_name)
        normalized.append(script_name)
    return normalized


def build_container_session_manifest(
    *,
    config: GatewayConfig,
    session_id: str,
    directory_path: str,
    model: str | None,
    codex_auth_mode: CodexAuthMode | None = None,
    ttl_hours: int | None,
    allowed_tools: list[str] | None,
    connector_grants: dict[str, list[str]] | None = None,
    tool_argument_policies: dict[str, list[Any]] | None,
    allowed_script_names: list[str] | None,
    allowed_contact_ids: list[str] | None,
    discord_channels: list[str] | None,
    teams_channel_allowlist: list[str] | None = None,
    teams_chat_allowlist: list[str] | None = None,
    teams_user_allowlist: list[str] | None = None,
    communication_target_ids: list[str] | None = None,
    email_mailbox_grants: list[EmailMailboxGrant] | None = None,
    email_mailboxes: list[str] | None,
    email_folders: list[str] | None,
    communication_file_receive_enabled: bool | None = None,
    communication_file_send_enabled: bool | None = None,
    communication_file_max_bytes: int | None = None,
    communication_file_require_approval_for_send: bool | None = None,
    web_search_enabled: bool = False,
    metadata: dict[str, Any],
) -> SessionManifest:
    expires_delta = (
        timedelta(hours=ttl_hours)
        if ttl_hours is not None
        else timedelta(seconds=config.default_session_ttl_seconds)
    )
    resolved_tools = normalize_allowed_tools(allowed_tools)
    if ToolName.STAGE_ARTIFACT not in resolved_tools:
        resolved_tools.append(ToolName.STAGE_ARTIFACT)
    if discord_channels:
        for tool in (
            ToolName.DISCORD_GET_COMMANDS,
            ToolName.DISCORD_POST_STATUS,
            ToolName.ADD_MESSAGE_REACTION,
            ToolName.HAS_PENDING_DISCORD_MESSAGES,
            ToolName.LIST_UNREAD_DISCORD_MESSAGES,
            ToolName.LIST_ALL_DISCORD_MESSAGES,
        ):
            if tool not in resolved_tools:
                resolved_tools.append(tool)
    email_requested = (
        bool(email_mailbox_grants)
        if email_mailbox_grants is not None
        else bool(email_mailboxes or email_folders)
    )
    if email_requested:
        for tool in (
            ToolName.EMAIL_LIST_UNREAD,
            ToolName.EMAIL_GET_MESSAGE,
        ):
            if tool not in resolved_tools:
                resolved_tools.append(tool)
        if email_mailbox_grants is None or any(
            grant.access in {EmailMailboxAccess.DRAFT, EmailMailboxAccess.SEND}
            for grant in email_mailbox_grants
        ):
            if ToolName.EMAIL_CREATE_DRAFT not in resolved_tools:
                resolved_tools.append(ToolName.EMAIL_CREATE_DRAFT)
        if config.email_send_enabled and (
            email_mailbox_grants is None
            or any(grant.access == EmailMailboxAccess.SEND for grant in email_mailbox_grants)
        ):
            for tool in (ToolName.EMAIL_SEND, ToolName.EMAIL_SEND_APPROVED):
                if tool not in resolved_tools:
                    resolved_tools.append(tool)

    resolved_teams_channel_allowlist = normalize_teams_channel_allowlist(teams_channel_allowlist)
    resolved_teams_chat_allowlist = normalize_teams_chat_allowlist(teams_chat_allowlist)
    resolved_teams_user_allowlist = normalize_teams_user_allowlist(teams_user_allowlist)
    resolved_email_mailboxes = (
        [grant.mailbox_id for grant in email_mailbox_grants]
        if email_mailbox_grants is not None
        else email_mailboxes or (config.email_mailbox_allowlist if email_requested else [])
    )
    resolved_email_folder_map = (
        {grant.mailbox_id: list(grant.folders) for grant in email_mailbox_grants}
        if email_mailbox_grants is not None
        else config.email_folder_allowlist_by_mailbox
    )
    resolved_email_folders = (
        sorted({folder for grant in email_mailbox_grants for folder in grant.folders})
        if email_mailbox_grants is not None
        else email_folders
        or (
            sorted(
                {
                    folder
                    for folders in config.email_folder_allowlist_by_mailbox.values()
                    for folder in folders
                }
            )
            if email_requested
            else []
        )
    )
    return SessionManifest(
        session_id=session_id,
        vm_id=None,
        vm_provider=ExecutionBackendKind.CONTAINER_WORKER_VM.value,
        model=model,
        codex_auth_mode=codex_auth_mode,
        directory_path=directory_path,
        allowed_tools=resolved_tools,
        connector_grants=connector_grants or {},
        approval_required_tools=list(config.approval_required_tools),
        tool_argument_policies=tool_argument_policies or {},
        allowed_script_names=normalize_allowed_script_names(allowed_script_names),
        allowed_contact_ids=allowed_contact_ids,
        discord_channel_allowlist=discord_channels or [],
        teams_channel_allowlist=resolved_teams_channel_allowlist,
        teams_chat_allowlist=resolved_teams_chat_allowlist,
        teams_user_allowlist=resolved_teams_user_allowlist,
        communication_target_ids=communication_target_ids,
        email_mailbox_grants=email_mailbox_grants,
        email_folder_allowlist=resolved_email_folders,
        email_mailbox_allowlist=resolved_email_mailboxes,
        email_folder_allowlist_by_mailbox=resolved_email_folder_map,
        email_draft_recipient_allowlist=config.email_draft_recipient_allowlist,
        email_draft_domain_allowlist=config.email_draft_domain_allowlist,
        email_send_enabled=config.email_send_enabled,
        communication_file_receive_enabled=(
            config.communication_file_receive_enabled
            if communication_file_receive_enabled is None
            else communication_file_receive_enabled
        ),
        communication_file_send_enabled=(
            config.communication_file_send_enabled
            if communication_file_send_enabled is None
            else communication_file_send_enabled
        ),
        communication_file_max_bytes=(
            config.communication_file_max_bytes
            if communication_file_max_bytes is None
            else communication_file_max_bytes
        ),
        communication_file_allowed_mime_types=list(config.communication_file_allowed_mime_types),
        communication_file_allowed_extensions=list(config.communication_file_allowed_extensions),
        communication_file_blocked_extensions=list(config.communication_file_blocked_extensions),
        communication_file_require_approval_for_send=(
            config.communication_file_require_approval_for_send
            if communication_file_require_approval_for_send is None
            else communication_file_require_approval_for_send
        ),
        web_search_enabled=web_search_enabled,
        expires_at=datetime.now(UTC) + expires_delta,
        metadata=metadata,
    )


def wait_for_container_execution(
    backend: Any,
    execution_id: str,
    *,
    timeout_seconds: float,
    poll_interval_seconds: float = 2.0,
) -> Any:
    terminal = {
        ExecutionStatusKind.STOPPED,
        ExecutionStatusKind.FAILED,
        ExecutionStatusKind.DESTROYED,
    }
    deadline = time.monotonic() + max(0.1, timeout_seconds)
    status = backend.inspect(execution_id)
    while status.status not in terminal and time.monotonic() < deadline:
        time.sleep(max(0.1, poll_interval_seconds))
        status = backend.inspect(execution_id)
    return status


def container_worker_guest_gateway_url(config: GatewayConfig) -> str:
    return (
        normalize_optional_string(config.container_worker_guest_gateway_url) or config.gateway_url
    )


def container_worker_guest_openai_base_url(config: GatewayConfig) -> str:
    configured = normalize_optional_string(config.container_worker_guest_openai_base_url)
    if configured:
        return configured
    return _service_url_from_gateway_url(
        config.gateway_url, port=config.openai_relay_port, path="/v1"
    )


def container_worker_guest_anthropic_base_url(config: GatewayConfig) -> str:
    configured = normalize_optional_string(config.container_worker_guest_anthropic_base_url)
    if configured:
        return configured
    return _service_url_from_gateway_url(config.gateway_url, port=config.openai_relay_port, path="")


def container_worker_guest_nuget_relay_base_url(config: GatewayConfig) -> str:
    configured = normalize_optional_string(config.container_worker_guest_nuget_relay_base_url)
    if configured:
        return configured
    return _service_url_from_gateway_url(config.gateway_url, port=config.nuget_relay_port, path="")


def container_worker_guest_http_target_relay_base_url(config: GatewayConfig) -> str:
    configured = normalize_optional_string(config.container_worker_guest_http_target_relay_base_url)
    if configured:
        return configured
    return _service_url_from_gateway_url(
        config.gateway_url, port=config.http_target_relay_port, path=""
    )


def container_worker_guest_pypi_index_url(config: GatewayConfig) -> str:
    configured = normalize_optional_string(config.container_worker_guest_pypi_index_url)
    if configured:
        return configured
    return _service_url_from_gateway_url(
        config.gateway_url,
        port=NEXUS_PACKAGE_CACHE_PORT,
        path=f"/repository/{NEXUS_PYPI_GROUP_REPOSITORY}/simple",
    )


def container_worker_guest_npm_registry_url(config: GatewayConfig) -> str:
    configured = normalize_optional_http_url(
        config.container_worker_guest_npm_registry_url,
        field_name="container_worker_guest_npm_registry_url",
    )
    if configured:
        return configured
    return _service_url_from_gateway_url(
        config.gateway_url,
        port=NEXUS_PACKAGE_CACHE_PORT,
        path=f"/repository/{NEXUS_NPM_GROUP_REPOSITORY}/",
    )


def relay_base_url_with_mcp_path_token(
    base_url: str, *, mcp_session_token: str | None = None
) -> str:
    token = normalize_optional_string(mcp_session_token)
    if not token:
        return base_url
    parts = urlsplit(base_url)
    path = f"{parts.path.rstrip('/')}/auth/{quote(token, safe='')}"
    return urlunsplit(parts._replace(path=path))


def container_session_openai_base_url(
    config: GatewayConfig,
    session_id: str,
    *,
    mcp_session_token: str | None = None,
) -> str:
    base_url = f"{container_worker_guest_openai_base_url(config).rstrip('/')}/session/{session_id}"
    return relay_base_url_with_mcp_path_token(
        base_url,
        mcp_session_token=mcp_session_token,
    )


def container_session_anthropic_base_url(
    config: GatewayConfig,
    session_id: str,
) -> str:
    return f"{container_worker_guest_anthropic_base_url(config).rstrip('/')}/session/{session_id}"


def container_http_target_relay_base_url(
    config: GatewayConfig, *, mcp_session_token: str | None = None
) -> str:
    return relay_base_url_with_mcp_path_token(
        container_worker_guest_http_target_relay_base_url(config),
        mcp_session_token=mcp_session_token,
    )


def container_session_gateway_url(
    config: GatewayConfig,
    session_id: str,
    *,
    mcp_session_token: str | None = None,
) -> str:
    return gateway_url_with_session_context(
        container_worker_guest_gateway_url(config),
        session_id=session_id,
        mcp_session_token=mcp_session_token,
    )


def gateway_url_with_session_context(
    base_gateway_url: str,
    *,
    session_id: str,
    workflow_run_id: str | None = None,
    mcp_tool_approval_lease_id: str | None = None,
    mcp_session_token: str | None = None,
) -> str:
    parts = urlsplit(base_gateway_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["session_id"] = session_id
    if mcp_session_token:
        query[MCP_SESSION_TOKEN_QUERY_PARAM_NAMES[0]] = mcp_session_token
    if workflow_run_id:
        query["workflow_run_id"] = workflow_run_id
    if mcp_tool_approval_lease_id:
        query["mcp_tool_approval_lease_id"] = mcp_tool_approval_lease_id
    return urlunsplit(parts._replace(query=urlencode(query)))


def build_container_session_files(
    *,
    config: GatewayConfig,
    session_id: str,
    model: str | None,
    agent_provider: str | None = None,
    web_search_enabled: bool = False,
    dotnet: bool,
    nuget: bool,
    playwright: bool,
    npm_registry_url: str | None = None,
    mcp_session_token: str | None = None,
) -> list[dict[str, Any]]:
    resolved_agent_provider = normalize_optional_string(
        agent_provider
    ) or default_agent_provider_for_config(config)
    resolved_model = model or default_agent_model_for_provider(resolved_agent_provider, config)
    openai_base_url = container_worker_guest_openai_base_url(config)
    anthropic_base_url = container_worker_guest_anthropic_base_url(config)
    gateway_url = container_worker_guest_gateway_url(config)
    http_target_relay_base_url = container_http_target_relay_base_url(
        config, mcp_session_token=mcp_session_token
    )
    nuget_relay_base_url = container_worker_guest_nuget_relay_base_url(config)
    pypi_index_url = container_worker_guest_pypi_index_url(config)
    resolved_npm_registry_url = normalize_optional_http_url(
        npm_registry_url, field_name="npm_registry_url"
    )
    if resolved_npm_registry_url is None:
        resolved_npm_registry_url = container_worker_guest_npm_registry_url(config)
    session_openai_base_url = container_session_openai_base_url(
        config,
        session_id,
        mcp_session_token=mcp_session_token,
    )
    session_anthropic_base_url = container_session_anthropic_base_url(
        config,
        session_id,
    )
    session_gateway_url = container_session_gateway_url(
        config,
        session_id,
        mcp_session_token=mcp_session_token,
    )
    codex_config_content = _codex_config_content(
        model=resolved_model,
        openai_base_url=session_openai_base_url,
        gateway_url=session_gateway_url,
        web_search_enabled=web_search_enabled,
    )
    session_env_values = {
        "EVANDER_SESSION_ID": session_id,
        "AGENT_PROVIDER": resolved_agent_provider,
        "EVANDER_GATEWAY_URL": gateway_url,
        "EVANDER_HTTP_TARGET_RELAY_BASE_URL": http_target_relay_base_url,
        "EVANDER_LLM_BASE_URL": openai_base_url,
        "EVANDER_ANTHROPIC_BASE_URL": anthropic_base_url,
        "OPENAI_BASE_URL": openai_base_url,
        "ANTHROPIC_BASE_URL": session_anthropic_base_url,
        "HTTP_TARGET_RELAY_BASE_URL": http_target_relay_base_url,
        "EVANDER_ARTIFACTS_DIR": "/artifacts",
        "NUGET_RELAY_BASE_URL": nuget_relay_base_url,
        "EVANDER_NUGET_FALLBACK_PACKAGES": ("/opt/evander/nuget-cache-ro"),
        "GIT_HTTP_LOW_SPEED_LIMIT": "1",
        "GIT_HTTP_LOW_SPEED_TIME": "15",
        "GIT_TERMINAL_PROMPT": "0",
        "NUGET_PACKAGES": "/home/agent/.nuget/packages-local",
        "NUGET_HTTP_CACHE_PATH": "/home/agent/.nuget/http-cache",
        "PIP_INDEX_URL": pypi_index_url,
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "NPM_CONFIG_REGISTRY": resolved_npm_registry_url,
    }
    if mcp_session_token:
        session_env_values["EVANDER_MCP_SESSION_TOKEN"] = mcp_session_token
    pip_trusted_host = _pip_trusted_host(pypi_index_url)
    if pip_trusted_host:
        session_env_values["PIP_TRUSTED_HOST"] = pip_trusted_host
    files: list[dict[str, Any]] = [
        {
            "destination_path": "/home/agent/.evander/session.env",
            "mode": "0600",
            "content": _session_env_content(session_env_values),
        },
        {
            "destination_path": "/home/agent/.config/pip/pip.conf",
            "mode": "0644",
            "content": _pip_config_content(pypi_index_url),
        },
        {
            "destination_path": "/home/agent/.npmrc",
            "mode": "0644",
            "content": _npmrc_content(
                resolved_npm_registry_url,
                replace_registry_host_always=npm_registry_url is not None,
            ),
        },
        {
            "destination_path": "/home/agent/.profile",
            "mode": "0644",
            "content": _container_profile_content(dotnet=dotnet, playwright=playwright),
        },
        {
            "destination_path": "/home/agent/.bashrc",
            "mode": "0644",
            "content": ('if [ -f "$HOME/.profile" ]; then\n  . "$HOME/.profile"\nfi\n'),
        },
        {
            "destination_path": "/home/agent/.bash_profile",
            "mode": "0644",
            "content": ('if [ -f "$HOME/.profile" ]; then\n  . "$HOME/.profile"\nfi\n'),
        },
        {
            "destination_path": "/home/agent/.codex/config.toml",
            "mode": "0600",
            "content": codex_config_content,
        },
        {
            "destination_path": "/home/agent/.config/evander/codex/config.toml",
            "mode": "0600",
            "content": codex_config_content,
        },
        {
            "destination_path": "/home/agent/.config/agent-sandbox/codex/config.toml",
            "mode": "0600",
            "content": codex_config_content,
        },
        {
            "destination_path": "/home/agent/.claude/settings.json",
            "mode": "0600",
            "content": _claude_settings_content(
                anthropic_base_url=session_anthropic_base_url,
                mcp_session_token=mcp_session_token,
            ),
        },
        {
            "destination_path": "/home/agent/.claude/mcp-config.json",
            "mode": "0600",
            "content": _claude_mcp_config_content(gateway_url=session_gateway_url),
        },
    ]
    if dotnet:
        files.extend(
            [
                {
                    "destination_path": "/home/agent/.dotnet/tools/.keep",
                    "content": "",
                    "mode": "0644",
                },
            ]
        )
    if nuget:
        files.extend(
            [
                {
                    "destination_path": "/home/agent/.nuget/NuGet/NuGet.Config",
                    "mode": "0644",
                    "content": _nuget_config_content(nuget_relay_base_url),
                },
                {
                    "destination_path": "/home/agent/.nuget/packages-local/.keep",
                    "content": "",
                    "mode": "0644",
                },
                {
                    "destination_path": "/home/agent/.nuget/http-cache/.keep",
                    "content": "",
                    "mode": "0644",
                },
            ]
        )
    return files


def container_shell_preamble() -> str:
    return (
        "set -euo pipefail\n"
        "export HOME=/home/agent\n"
        "if [ -f /home/agent/.profile ]; then\n"
        "  . /home/agent/.profile\n"
        "fi\n"
        'export OPENAI_API_KEY="${OPENAI_API_KEY:-${EVANDER_MCP_SESSION_TOKEN:-relay-placeholder}}"\n'
        'export ANTHROPIC_AUTH_TOKEN="${ANTHROPIC_AUTH_TOKEN:-${EVANDER_MCP_SESSION_TOKEN:-relay-placeholder}}"\n'
    )


def map_install_roots_to_container_repo(
    *,
    host_checkout: str,
    install_roots: Sequence[str],
) -> list[str]:
    normalized_checkout = str(Path(host_checkout).expanduser().resolve())
    repo_name = Path(normalized_checkout).name or "repo"
    container_checkout = f"/home/agent/repos/{repo_name}"
    mapped: list[str] = []
    for raw_root in install_roots:
        root = str(raw_root).strip()
        if not root:
            continue
        try:
            resolved_root = str(Path(root).expanduser().resolve())
        except OSError:
            resolved_root = root
        if resolved_root == normalized_checkout:
            mapped.append(container_checkout)
        elif resolved_root.startswith(f"{normalized_checkout}/"):
            relative = resolved_root[len(normalized_checkout) + 1 :]
            mapped.append(f"{container_checkout}/{relative}")
        elif root.startswith("/home/agent/repos"):
            mapped.append(root)
    return list(dict.fromkeys(mapped))


def frontend_bootstrap_command(install_roots: Sequence[str]) -> str:
    quoted_roots = " ".join(shlex.quote(root) for root in install_roots)
    return (
        "set -euo pipefail; "
        f"for dir in {quoted_roots}; do "
        'if [ -f "$dir/package-lock.json" ]; then '
        '(cd "$dir" && npm ci); '
        'elif [ -f "$dir/package.json" ]; then '
        '(cd "$dir" && npm install); '
        "else "
        "printf 'Skipping frontend bootstrap for %s (no package.json)\\n' \"$dir\"; "
        "fi; "
        "done"
    )


def container_prompt_command(
    *,
    prompt: str,
    model: str,
    resume_thread_id: str | None,
    agent_provider: str | None = None,
    resume_mode: str | None = None,
    mcp_gateway_url: str | None = None,
    web_search_enabled: bool = False,
) -> str:
    provider = normalize_optional_string(agent_provider) or "codex"
    if provider == "claude":
        return _container_claude_prompt_command(
            prompt=prompt,
            model=model,
            resume_thread_id=resume_thread_id,
            resume_mode=resume_mode,
            mcp_gateway_url=mcp_gateway_url,
        )
    if provider != "codex":
        raise ValueError(f"Unsupported container agent_provider: {provider}")
    effective_resume_thread_id = _container_resume_thread_id(resume_thread_id)
    effective_resume_mode = normalize_optional_string(resume_mode)
    common_args = [
        "--json",
        "--dangerously-bypass-approvals-and-sandbox",
        "--skip-git-repo-check",
        _CODEX_OPTIONAL_FEATURE_ARGS_MARKER,
        "--model",
        model,
        "-c",
        f"web_search={_toml_string('live' if web_search_enabled else 'disabled')}",
    ]
    if mcp_gateway_url:
        common_args.extend(
            [
                "-c",
                f"mcp_servers.evander.url={_toml_string(mcp_gateway_url)}",
            ]
        )
    if effective_resume_thread_id:
        args = ["codex", "exec", "resume", *common_args, effective_resume_thread_id, prompt]
    elif effective_resume_mode == "same_context":
        args = ["codex", "exec", "resume", "--last", *common_args, prompt]
    else:
        args = [
            "codex",
            "exec",
            *common_args,
            "-s",
            "danger-full-access",
            "-C",
            "/home/agent",
            prompt,
        ]
    return (
        container_shell_preamble()
        + "cd /home/agent\n"
        + _codex_json_log_filter_shell()
        + _codex_optional_feature_args_shell()
        + _shell_join_with_codex_optional_feature_args(args)
        + " | evander_codex_json_to_log\n"
    )


def container_codex_turn_spec(
    *,
    prompt: str,
    model: str,
    resume_thread_id: str | None,
    session: object | None,
    config: GatewayConfig,
    agent_provider: str | None = None,
    resume_mode: str | None = None,
    mcp_gateway_url: str | None = None,
    web_search_enabled: bool = False,
) -> CodexTurnSpec | None:
    provider = normalize_optional_string(agent_provider) or "codex"
    if provider != "codex":
        # Directory Codex defaults do not apply to other agent harnesses. A
        # session-level value is an explicit override and remains invalid.
        explicit_auth_mode = normalize_codex_auth_mode(
            getattr(session, "codex_auth_mode", None) if session is not None else None
        )
        if explicit_auth_mode is CodexAuthMode.CHATGPT_SUBSCRIPTION:
            raise ValueError("chatgpt_subscription is supported only for Codex agents")
        return None
    auth_mode = effective_codex_auth_mode(session=session, config=config)
    metadata = getattr(session, "metadata", {}) if session is not None else {}
    model_provider_mode = (
        normalize_optional_string(metadata.get("model_provider_mode"))
        if isinstance(metadata, Mapping)
        else None
    ) or normalize_optional_string(getattr(config, "model_provider_mode", None))
    if model_provider_mode != "openai_public":
        # Subscription tokens are valid only for the public OpenAI route. Keep
        # local/Azure traffic on its configured private endpoint and API-key mode.
        auth_mode = CodexAuthMode.METERED_API
    return CodexTurnSpec(
        auth_mode=auth_mode,
        prompt=prompt,
        model=model,
        resume_thread_id=_container_resume_thread_id(resume_thread_id),
        resume_mode=("same_context" if resume_mode == "same_context" else "new_context"),
        mcp_gateway_url=mcp_gateway_url,
        web_search_enabled=web_search_enabled,
    )


def _container_claude_prompt_command(
    *,
    prompt: str,
    model: str,
    resume_thread_id: str | None,
    resume_mode: str | None = None,
    mcp_gateway_url: str | None = None,
) -> str:
    effective_resume_thread_id = _container_resume_thread_id(resume_thread_id)
    effective_resume_mode = normalize_optional_string(resume_mode)
    common_args = [
        "--dangerously-skip-permissions",
        "--model",
        model,
        "--output-format",
        "stream-json",
        "--verbose",
        "--mcp-config",
        "/home/agent/.claude/mcp-config.json",
    ]
    if effective_resume_thread_id:
        args = ["claude", "--resume", effective_resume_thread_id, "-p", *common_args]
    elif effective_resume_mode == "same_context":
        args = ["claude", "--continue", "-p", *common_args]
    else:
        args = ["claude", "-p", *common_args]
    preamble = container_shell_preamble()
    if mcp_gateway_url:
        preamble += _write_claude_mcp_config_shell(mcp_gateway_url)
    preamble += _sync_claude_mcp_server_shell()
    return (
        preamble
        + "cd /home/agent\n"
        + _claude_json_log_filter_shell()
        + "printf '%s' "
        + shlex.quote(prompt)
        + " | "
        + shlex.join(args)
        + " | evander_claude_json_to_log\n"
    )


def _container_resume_thread_id(resume_thread_id: str | None) -> str | None:
    normalized = normalize_optional_string(resume_thread_id)
    if normalized is None:
        return None
    if normalized.startswith("dispatch-"):
        return None
    if normalized.startswith("chat-thread-"):
        return None
    return normalized


def container_thread_id_from_logs(logs: str) -> str | None:
    match = _CONTAINER_THREAD_ID_LOG_PATTERN.search(logs)
    if match is None:
        return None
    return match.group("thread_id").strip() or None


def _write_claude_mcp_config_shell(gateway_url: str) -> str:
    return (
        "mkdir -p /home/agent/.claude\n"
        + "cat > /home/agent/.claude/mcp-config.json <<'EOF_EVANDER_CLAUDE_MCP'\n"
        + _claude_mcp_config_content(gateway_url=gateway_url)
        + "EOF_EVANDER_CLAUDE_MCP\n"
    )


def _sync_claude_mcp_server_shell() -> str:
    return r"""evander_sync_claude_mcp_server() {
  local config_path="${1:-/home/agent/.claude/mcp-config.json}"
  if [ ! -f "${config_path}" ]; then
    return 0
  fi
  if ! command -v claude >/dev/null 2>&1; then
    return 0
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    echo "warning: python3 is unavailable; skipping Claude MCP registration" >&2
    return 0
  fi
  if ! claude mcp add --help >/dev/null 2>&1 </dev/null; then
    return 0
  fi
  local mcp_url
  if ! mcp_url="$(python3 - "${config_path}" <<'PY_EVANDER_CLAUDE_MCP_URL'
import json
import sys
from pathlib import Path

try:
    config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    url = config.get("mcpServers", {}).get("evander", {}).get("url", "")
except Exception:
    url = ""
if isinstance(url, str):
    print(url)
PY_EVANDER_CLAUDE_MCP_URL
)"; then
    echo "warning: unable to read Claude MCP config at ${config_path}" >&2
    return 0
  fi
  if [ -z "${mcp_url}" ]; then
    return 0
  fi
  claude mcp remove -s user evander >/dev/null 2>&1 </dev/null || true
  if ! claude mcp add --transport http -s user evander "${mcp_url}" >/dev/null 2>&1 </dev/null; then
    echo "warning: unable to register Claude MCP server evander" >&2
  fi
}
evander_sync_claude_mcp_server /home/agent/.claude/mcp-config.json
"""


def _codex_json_log_filter_script() -> str:
    return r"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

TEXT_PART_TYPES = {"text", "output_text", "assistant_message"}
ASSISTANT_ITEM_TYPES = {"assistant_message", "message", "final_message"}
try:
    FILTER_STARTED_AT = float(os.environ.get("EVANDER_CODEX_JSON_FILTER_STARTED_AT", ""))
except ValueError:
    FILTER_STARTED_AT = 0.0
if FILTER_STARTED_AT <= 0:
    FILTER_STARTED_AT = time.time()
thread_id_printed = False
current_thread_id = ""
printed_messages = set()
last_token_usage = {}
assistant_text_seen = False
failure_printed = False
task_complete_without_final_message = False


def first_string(*values):
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def first_text(*values):
    for value in values:
        if isinstance(value, str) and value:
            return value
    return ""


def event_timestamp_seconds(event):
    if not isinstance(event, dict):
        return None
    timestamp = event.get("timestamp")
    if not isinstance(timestamp, str) or not timestamp.strip():
        return None
    text = timestamp.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def event_is_from_current_invocation(event):
    timestamp = event_timestamp_seconds(event)
    if timestamp is None:
        return False
    return timestamp >= FILTER_STARTED_AT


def content_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for part in value:
            if isinstance(part, str):
                if part:
                    parts.append(part)
                continue
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type") or "")
            if part_type and part_type not in TEXT_PART_TYPES:
                continue
            text = first_text(part.get("text"), part.get("content"), part.get("message"))
            if text:
                parts.append(text)
        return "\n".join(parts)
    if isinstance(value, dict):
        return first_text(value.get("text"), value.get("content"), value.get("message"))
    return ""


def print_message(message):
    if not isinstance(message, str) or not message:
        return
    if message in printed_messages:
        return
    printed_messages.add(message)
    if message.endswith("\n"):
        print(message, end="", flush=True)
    else:
        print(message, flush=True)


def print_assistant_message(message):
    global assistant_text_seen
    if isinstance(message, str) and message.strip():
        assistant_text_seen = True
    print_message(message)


def print_failure(message):
    global failure_printed
    failure_printed = True
    print_message(message)


def remember_thread_id(thread_id):
    global current_thread_id, thread_id_printed
    if not thread_id:
        return
    current_thread_id = thread_id
    if not thread_id_printed:
        print(f"session id: {thread_id}", flush=True)
        thread_id_printed = True


def mark_task_complete_without_final_message():
    global task_complete_without_final_message
    task_complete_without_final_message = True


def int_value(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def update_token_usage(event):
    global last_token_usage
    usage = {}
    candidates = []
    info = event.get("info")
    if isinstance(info, dict):
        candidates.append(info)
    payload = event.get("payload")
    if isinstance(payload, dict):
        candidates.append(payload)
        payload_info = payload.get("info")
        if isinstance(payload_info, dict):
            candidates.append(payload_info)
    for candidate in candidates:
        raw_usage = candidate.get("last_token_usage") or candidate.get("usage")
        if not isinstance(raw_usage, dict):
            continue
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            value = int_value(raw_usage.get(key))
            if value is not None:
                usage[key] = value
    if usage:
        last_token_usage = usage


def no_final_message_diagnostic():
    total_tokens = last_token_usage.get("total_tokens")
    suffix = f" (last_total_tokens={total_tokens})" if total_tokens is not None else ""
    return "codex failed: task_complete did not include a final agent message" + suffix


def no_agent_message_diagnostic():
    return "codex failed: no agent message was emitted"


def thread_id_from_event(event):
    thread = event.get("thread")
    payload = event.get("payload")
    return first_string(
        event.get("thread_id"),
        thread.get("id") if isinstance(thread, dict) else None,
        payload.get("id") if isinstance(payload, dict) else None,
    )


def text_from_response_item(event):
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return ""
    if str(payload.get("type") or "") != "message":
        return ""
    if str(payload.get("role") or "") != "assistant":
        return ""
    return first_text(
        payload.get("text"),
        payload.get("message"),
        content_text(payload.get("content")),
        content_text(payload.get("formatted")),
    )


def text_from_item_completed(event):
    item = event.get("item")
    if not isinstance(item, dict):
        return ""
    item_type = str(item.get("type") or item.get("item_type") or "")
    role = str(item.get("role") or "")
    if item_type and item_type not in ASSISTANT_ITEM_TYPES and role != "assistant":
        return ""
    return first_text(
        item.get("text"),
        item.get("message"),
        content_text(item.get("content")),
        content_text(item.get("formatted")),
    )


def codex_session_roots():
    roots = []
    codex_home = first_string(os.environ.get("CODEX_HOME"))
    if codex_home:
        roots.append(Path(codex_home) / "sessions")
    home = Path(os.environ.get("HOME") or "/home/agent")
    roots.extend(
        [
            home / ".config" / "agent-sandbox" / "codex" / "sessions",
            home / ".config" / "evander" / "codex" / "sessions",
            home / ".codex" / "sessions",
        ]
    )
    unique = []
    seen = set()
    for root in roots:
        text = str(root)
        if text in seen:
            continue
        seen.add(text)
        unique.append(root)
    return unique


def session_jsonl_paths(thread_id):
    if not thread_id:
        return []
    paths = {}
    for root in codex_session_roots():
        if not root.is_dir():
            continue
        try:
            for path in root.rglob(f"*{thread_id}*.jsonl"):
                if path.is_file():
                    paths[path] = None
        except OSError:
            continue
    return sorted(paths, key=lambda path: (path.stat().st_mtime, str(path)))


def current_invocation_session_jsonl_paths(thread_id):
    recent = []
    for path in session_jsonl_paths(thread_id):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime >= FILTER_STARTED_AT:
            recent.append((mtime, str(path), path))
    if not recent:
        return []
    recent.sort(reverse=True)
    return [recent[0][2]]


def replay_session_jsonl_messages():
    for path in current_invocation_session_jsonl_paths(current_thread_id):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for raw_line in lines:
            try:
                event = json.loads(raw_line)
            except Exception:
                continue
            if not isinstance(event, dict):
                continue
            if not event_is_from_current_invocation(event):
                continue
            event_type = str(event.get("type") or "")
            if event_type == "token_count":
                update_token_usage(event)
                continue
            if event_type == "event_msg":
                payload = event.get("payload")
                if not isinstance(payload, dict):
                    continue
                payload_type = str(payload.get("type") or "")
                if payload_type == "agent_message":
                    print_assistant_message(first_text(payload.get("message")))
                elif payload_type == "token_count":
                    update_token_usage(event)
                elif payload_type == "task_complete":
                    message = first_text(payload.get("last_agent_message"))
                    if message:
                        print_assistant_message(message)
                    elif not assistant_text_seen:
                        mark_task_complete_without_final_message()
                continue
            if event_type == "response_item":
                print_assistant_message(text_from_response_item(event))
                continue
            if event_type == "item.completed":
                print_assistant_message(text_from_item_completed(event))


for raw_line in sys.stdin:
    raw_line = raw_line.rstrip("\n")
    try:
        event = json.loads(raw_line)
    except Exception:
        print(raw_line, flush=True)
        continue
    if not isinstance(event, dict):
        continue

    event_type = str(event.get("type") or "")
    if event_type == "session_meta":
        payload = event.get("payload")
        thread_id = first_string(payload.get("id") if isinstance(payload, dict) else None)
        remember_thread_id(thread_id)
        continue

    if event_type == "thread.started":
        thread_id = thread_id_from_event(event)
        remember_thread_id(thread_id)
        continue

    if event_type == "token_count":
        update_token_usage(event)
        continue

    if event_type == "event_msg":
        payload = event.get("payload")
        if isinstance(payload, dict):
            payload_type = str(payload.get("type") or "")
            if payload_type == "agent_message":
                print_assistant_message(first_text(payload.get("message")))
            elif payload_type == "token_count":
                update_token_usage(event)
            elif payload_type == "task_complete":
                message = first_text(payload.get("last_agent_message"))
                if message:
                    print_assistant_message(message)
                elif not assistant_text_seen:
                    mark_task_complete_without_final_message()
        continue

    if event_type == "response_item":
        print_assistant_message(text_from_response_item(event))
        continue

    if event_type == "item.completed":
        print_assistant_message(text_from_item_completed(event))
        continue

    if event_type in {"error", "turn.failed"}:
        message = first_string(event.get("message"), event.get("error"))
        if message:
            print_failure(f"codex {event_type}: {message}")

if not assistant_text_seen:
    replay_session_jsonl_messages()
if not assistant_text_seen:
    if task_complete_without_final_message:
        print_failure(no_final_message_diagnostic())
    elif not failure_printed:
        print_failure(no_agent_message_diagnostic())
    sys.exit(1)
if failure_printed:
    sys.exit(1)
"""


def _codex_json_log_filter_shell() -> str:
    script = _codex_json_log_filter_script()
    return (
        "evander_codex_json_to_log() {\n"
        "  if command -v python3 >/dev/null 2>&1; then\n"
        f"    python3 -u -c {shlex.quote(script)}\n"
        "  else\n"
        "    cat\n"
        "  fi\n"
        "}\n\n"
        "EVANDER_CODEX_JSON_FILTER_STARTED_AT=$(date +%s.%N 2>/dev/null || date +%s)\n"
        "export EVANDER_CODEX_JSON_FILTER_STARTED_AT\n"
    )


def _claude_json_log_filter_script() -> str:
    return r"""
import json
import sys

thread_id_printed = False
printed_messages = set()


def first_string(*values):
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def content_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                if item:
                    parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            text = first_string(item.get("text"), item.get("content"), item.get("message"))
            if text:
                parts.append(text)
        return "\n".join(parts)
    if isinstance(value, dict):
        return first_string(value.get("text"), value.get("content"), value.get("message"))
    return ""


def print_message(message):
    if not isinstance(message, str) or not message:
        return
    if message in printed_messages:
        return
    printed_messages.add(message)
    if message.endswith("\n"):
        print(message, end="", flush=True)
    else:
        print(message, flush=True)


for line in sys.stdin:
    stripped = line.strip()
    if not stripped:
        continue
    try:
        event = json.loads(stripped)
    except json.JSONDecodeError:
        print(stripped, flush=True)
        continue
    if not isinstance(event, dict):
        continue

    thread_id = first_string(
        event.get("session_id"),
        event.get("sessionId"),
        event.get("conversation_id"),
        event.get("conversationId"),
    )
    if thread_id and not thread_id_printed:
        print(f"session id: {thread_id}", flush=True)
        thread_id_printed = True

    event_type = first_string(event.get("type"))
    if event_type in {"assistant", "assistant_message", "message"}:
        message = event.get("message")
        if isinstance(message, dict):
            print_message(content_text(message.get("content")))
        else:
            print_message(content_text(event.get("content")))
        continue
    if event_type in {"result", "final"}:
        print_message(first_string(event.get("result"), event.get("text"), event.get("content")))
        continue
    if event_type in {"error", "failed"}:
        message = event.get("error")
        if isinstance(message, dict):
            message = first_string(message.get("message"), message.get("type"))
        print_message("claude error: " + first_string(message, event.get("message")))
"""


def _claude_json_log_filter_shell() -> str:
    script = _claude_json_log_filter_script()
    return (
        "evander_claude_json_to_log() {\n"
        "  if command -v python3 >/dev/null 2>&1; then\n"
        f"    python3 -u -c {shlex.quote(script)}\n"
        "  else\n"
        "    cat\n"
        "  fi\n"
        "}\n\n"
    )


def safe_effect_execution_id(prefix: str, session_id: str) -> str:
    safe_session = "".join(char if char.isalnum() or char in "._-" else "-" for char in session_id)
    return f"{prefix}-{safe_session}-{uuid.uuid4().hex[:12]}"


def _session_env_content(values: Mapping[str, str]) -> str:
    return "".join(f"{key}={shlex.quote(value)}\n" for key, value in sorted(values.items()))


def _container_profile_content(*, dotnet: bool, playwright: bool) -> str:
    lines = [
        'export HOME="${HOME:-/home/agent}"',
        'export PATH="$HOME/bin:$PATH"',
        'export CODEX_HOME="${CODEX_HOME:-$HOME/.config/evander/codex}"',
        'if [ -f "$HOME/.evander/session.env" ]; then',
        "  set -a",
        '  . "$HOME/.evander/session.env"',
        "  set +a",
        "fi",
        'export OPENAI_API_KEY="${OPENAI_API_KEY:-${EVANDER_MCP_SESSION_TOKEN:-relay-placeholder}}"',
        'export ANTHROPIC_AUTH_TOKEN="${ANTHROPIC_AUTH_TOKEN:-${EVANDER_MCP_SESSION_TOKEN:-relay-placeholder}}"',
        'export EVANDER_NODE_ROOT="/opt/evander/toolchains/node-v24.14.0"',
        'export PATH="$EVANDER_NODE_ROOT/bin:$PATH"',
    ]
    if dotnet:
        lines.extend(
            [
                'export DOTNET_ROOT="/opt/evander/toolchains/dotnet"',
                'export EVANDER_DOTNET_TOOLS_ROOT="/opt/evander/toolchains/dotnet-tools"',
                'export DOTNET_CLI_HOME="$HOME"',
                'export DOTNET_CLI_TELEMETRY_OPTOUT="1"',
                'export DOTNET_SKIP_FIRST_TIME_EXPERIENCE="1"',
                'export DOTNET_SKIP_WORKLOAD_MANIFEST_UPDATE="1"',
                'export DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE="1"',
                'export DOTNET_NOLOGO="1"',
                'export PATH="$DOTNET_ROOT:$HOME/.dotnet/tools:$EVANDER_DOTNET_TOOLS_ROOT:$PATH"',
            ]
        )
    if playwright:
        lines.extend(
            [
                'export EVANDER_PLAYWRIGHT_RUNTIME="/opt/evander/playwright-runtime/node_modules"',
                'export NODE_PATH="$EVANDER_PLAYWRIGHT_RUNTIME"',
                'export PLAYWRIGHT_BROWSERS_PATH="/opt/evander/ms-playwright"',
                'export PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD="1"',
                'export PLAYWRIGHT_MCP_SANDBOX="0"',
                'if [ -z "${PLAYWRIGHT_MCP_EXECUTABLE_PATH:-}" ] && command -v node >/dev/null 2>&1; then',
                '  if _evander_playwright_chromium="$(node -e \'const fs = require("fs"); const path = require("path"); const runtime = process.env.EVANDER_PLAYWRIGHT_RUNTIME; const { chromium } = require(path.join(runtime, "playwright")); const executable = chromium.executablePath(); if (!fs.existsSync(executable)) process.exit(1); process.stdout.write(executable);\' 2>/dev/null)" && [ -n "$_evander_playwright_chromium" ]; then',
                '    export PLAYWRIGHT_MCP_EXECUTABLE_PATH="$_evander_playwright_chromium"',
                "  fi",
                "  unset _evander_playwright_chromium",
                "fi",
                'if [ -n "${PLAYWRIGHT_MCP_EXECUTABLE_PATH:-}" ]; then',
                '  export CHROME_BIN="${CHROME_BIN:-$PLAYWRIGHT_MCP_EXECUTABLE_PATH}"',
                '  export CHROMIUM_BIN="${CHROMIUM_BIN:-$PLAYWRIGHT_MCP_EXECUTABLE_PATH}"',
                "fi",
            ]
        )
    return "\n".join(lines) + "\n"


def _codex_config_content(
    *,
    model: str,
    openai_base_url: str,
    gateway_url: str,
    web_search_enabled: bool = False,
) -> str:
    return (
        f"model = {_toml_string(model)}\n"
        'model_provider = "evander_openai_relay"\n'
        'model_reasoning_effort = "high"\n'
        'personality = "pragmatic"\n'
        f"web_search = {_toml_string('live' if web_search_enabled else 'disabled')}\n"
        "\n"
        "[model_providers.evander_openai_relay]\n"
        'name = "Evander OpenAI Relay"\n'
        f"base_url = {_toml_string(openai_base_url)}\n"
        'env_key = "OPENAI_API_KEY"\n'
        'wire_api = "responses"\n'
        "requires_openai_auth = false\n"
        "supports_websockets = false\n"
        "\n"
        "[mcp_servers.evander]\n"
        f"url = {_toml_string(gateway_url)}\n"
        "enabled = true\n"
        "\n"
        '[projects."/home/agent"]\n'
        'trust_level = "trusted"\n'
    )


def _codex_optional_feature_args_shell() -> str:
    feature = shlex.quote(_CODEX_NON_PREFIXED_MCP_TOOL_NAMES_FEATURE)
    return (
        "codex_feature_args=()\n"
        "if codex features list 2>/dev/null "
        f"| awk -v feature={feature} "
        "'$1 == feature && $2 != \"removed\" { found=1 } END { exit found ? 0 : 1 }'; then\n"
        f"  codex_feature_args+=(--enable {feature})\n"
        "fi\n"
    )


def _shell_join_with_codex_optional_feature_args(args: Sequence[str]) -> str:
    parts = [
        '"${codex_feature_args[@]}"'
        if arg == _CODEX_OPTIONAL_FEATURE_ARGS_MARKER
        else shlex.quote(arg)
        for arg in args
    ]
    return " ".join(parts)


def _claude_settings_content(
    *, anthropic_base_url: str, mcp_session_token: str | None = None
) -> str:
    return (
        json.dumps(
            {
                "attribution": {
                    "commit": "",
                },
                "env": {
                    "ANTHROPIC_AUTH_TOKEN": mcp_session_token or "relay-placeholder",
                    "ANTHROPIC_BASE_URL": anthropic_base_url,
                    "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "1",
                    "DISABLE_TELEMETRY": "1",
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def _claude_mcp_config_content(*, gateway_url: str) -> str:
    return (
        json.dumps(
            {
                "mcpServers": {
                    "evander": {
                        "type": "http",
                        "url": gateway_url,
                        "tools": ["*"],
                    }
                }
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def _nuget_config_content(nuget_relay_base_url: str) -> str:
    escaped_base = html.escape(nuget_relay_base_url.rstrip("/"), quote=True)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<configuration>\n"
        "  <config>\n"
        '    <add key="globalPackagesFolder" value="/home/agent/.nuget/packages-local" />\n'
        '    <add key="httpCacheFolder" value="/home/agent/.nuget/http-cache" />\n'
        "  </config>\n"
        "  <packageSources>\n"
        "    <clear />\n"
        f'    <add key="sandbox-nuget" value="{escaped_base}/nuget/nuget-org/v3/index.json" allowInsecureConnections="true" />\n'
        f'    <add key="sandbox-azure" value="{escaped_base}/nuget/azure/v3/index.json" allowInsecureConnections="true" />\n'
        "  </packageSources>\n"
        "  <packageSourceMapping>\n"
        '    <packageSource key="sandbox-nuget">\n'
        '      <package pattern="*" />\n'
        "    </packageSource>\n"
        '    <packageSource key="sandbox-azure">\n'
        '      <package pattern="ExampleIndustry.*" />\n'
        "    </packageSource>\n"
        "  </packageSourceMapping>\n"
        "  <fallbackPackageFolders>\n"
        '    <add key="shared-readonly" value="/opt/evander/nuget-cache-ro" />\n'
        "  </fallbackPackageFolders>\n"
        "</configuration>\n"
    )


def _pip_config_content(pypi_index_url: str) -> str:
    lines = [
        "[global]",
        f"index-url = {pypi_index_url}",
        "disable-pip-version-check = true",
    ]
    trusted_host = _pip_trusted_host(pypi_index_url)
    if trusted_host:
        lines.append(f"trusted-host = {trusted_host}")
    return "\n".join(lines) + "\n"


def _npmrc_content(npm_registry_url: str, *, replace_registry_host_always: bool = False) -> str:
    lines = [f"registry={_ensure_trailing_slash(npm_registry_url)}"]
    if replace_registry_host_always:
        lines.append("replace-registry-host=always")
    return "\n".join(lines) + "\n"


def _pip_trusted_host(index_url: str) -> str | None:
    parts = urlsplit(index_url)
    if parts.scheme != "http":
        return None
    return parts.hostname or None


def _ensure_trailing_slash(value: str) -> str:
    return value if value.endswith("/") else f"{value}/"


def _toml_string(value: str) -> str:
    return json.dumps(value)


def _service_url_from_gateway_url(gateway_url: str, *, port: int, path: str) -> str:
    parts = urlsplit(gateway_url)
    hostname = parts.hostname or "127.0.0.1"
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname
    if parts.username:
        credentials = parts.username
        if parts.password:
            credentials = f"{credentials}:{parts.password}"
        netloc = f"{credentials}@{netloc}"
    netloc = f"{netloc}:{port}"
    normalized_path = path if path.startswith("/") or not path else f"/{path}"
    return urlunsplit((parts.scheme or "http", netloc, normalized_path, "", ""))
