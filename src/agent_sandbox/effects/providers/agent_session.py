from __future__ import annotations

import html
import json
import shlex
import time
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from agent_sandbox.config import GatewayConfig
from agent_sandbox.execution import (
    ExecutionBackendKind,
    ExecutionStatusKind,
    build_execution_backend,
)
from agent_sandbox.models import SessionManifest, ToolName
from agent_sandbox.repositories.session_manifests import (
    JsonSessionManifestRepository,
    as_session_manifest_repository,
)

from .base import EffectRuntimeContext


def normalize_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


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


def runtime_session_repository(context: EffectRuntimeContext, config: GatewayConfig):
    runtime = getattr(context.admin_service, "runtime", None)
    repository = getattr(runtime, "session_repository", None)
    if repository is not None:
        return as_session_manifest_repository(repository)
    return JsonSessionManifestRepository(config.session_store_path)


def effective_config_for_directory(
    context: EffectRuntimeContext,
    directory_path: str | None,
) -> GatewayConfig:
    runtime = getattr(context.admin_service, "runtime", None)
    resolver = getattr(runtime, "provider_config", None)
    resolve_for_directory = getattr(resolver, "resolve_for_directory", None)
    if callable(resolve_for_directory):
        return resolve_for_directory(directory_path)
    return GatewayConfig.model_validate(dict(context.provider_config))


def effective_config_for_session(
    context: EffectRuntimeContext,
    session_id: str,
    *,
    fallback_directory_path: str | None = None,
) -> GatewayConfig:
    runtime = getattr(context.admin_service, "runtime", None)
    resolver = getattr(runtime, "provider_config", None)
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
    return session_id, session


def resolve_vm_target(
    context: EffectRuntimeContext,
    payload: Mapping[str, Any],
    *,
    effect_type: str,
) -> tuple[str, str]:
    session_id = normalize_optional_string(payload.get("session_id"))
    vm_name = normalize_optional_string(payload.get("vm_name"))
    target = normalize_optional_string(payload.get("target"))

    if session_id:
        return resolve_vm_name_from_session(context, session_id), session_id
    if vm_name:
        return vm_name, resolve_session_id_from_vm_name(context, vm_name)
    if target:
        session = read_all_sessions(context).get(target)
        if session is not None:
            resolved_vm_name = normalize_optional_string(getattr(session, "vm_id", None))
            if not resolved_vm_name:
                raise RuntimeError(f"Session {target!r} does not have an associated VM")
            return resolved_vm_name, target
        return target, resolve_session_id_from_vm_name(context, target)
    raise RuntimeError(f"{effect_type} requires one of target, session_id, or vm_name")


def resolve_vm_name_from_session(context: EffectRuntimeContext, session_id: str) -> str:
    session = read_all_sessions(context).get(session_id)
    if session is None:
        raise RuntimeError(f"Unknown session: {session_id}")
    vm_name = normalize_optional_string(getattr(session, "vm_id", None))
    if not vm_name:
        raise RuntimeError(f"Session {session_id!r} does not have an associated VM")
    return vm_name


def resolve_session_id_from_vm_name(context: EffectRuntimeContext, vm_name: str) -> str:
    matches = [
        session_id
        for session_id, session in read_all_sessions(context).items()
        if normalize_optional_string(getattr(session, "vm_id", None)) == vm_name
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise RuntimeError(
            f"VM {vm_name!r} is associated with multiple sessions: {', '.join(sorted(matches))}"
        )
    return f"session-{vm_name}"


def read_all_sessions(context: EffectRuntimeContext) -> Mapping[str, Any]:
    if context.session_lookup is None:
        raise RuntimeError("agent effect requires session_lookup")
    return context.session_lookup.read_all_sessions()


def normalize_allowed_tools(value: list[str] | None) -> list[ToolName]:
    tools: list[ToolName] = [ToolName.APPROVAL_SUBMIT, ToolName.APPROVAL_CHECK]
    seen = set(tools)
    for raw_tool in value or []:
        tool = ToolName(str(raw_tool).strip())
        if tool in seen:
            continue
        seen.add(tool)
        tools.append(tool)
    return tools


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
    ttl_hours: int | None,
    allowed_tools: list[str] | None,
    allowed_script_names: list[str] | None,
    discord_channels: list[str] | None,
    email_mailboxes: list[str] | None,
    email_folders: list[str] | None,
    metadata: dict[str, Any],
) -> SessionManifest:
    expires_delta = (
        timedelta(hours=ttl_hours)
        if ttl_hours is not None
        else timedelta(seconds=config.default_session_ttl_seconds)
    )
    resolved_tools = normalize_allowed_tools(allowed_tools)
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
    email_requested = bool(email_mailboxes or email_folders)
    if email_requested:
        for tool in (
            ToolName.EMAIL_LIST_UNREAD,
            ToolName.EMAIL_GET_MESSAGE,
            ToolName.EMAIL_CREATE_DRAFT,
        ):
            if tool not in resolved_tools:
                resolved_tools.append(tool)
        if config.email_send_enabled and ToolName.EMAIL_SEND_APPROVED not in resolved_tools:
            resolved_tools.append(ToolName.EMAIL_SEND_APPROVED)

    resolved_email_mailboxes = email_mailboxes or (
        config.email_mailbox_allowlist if email_requested else []
    )
    resolved_email_folders = email_folders or (
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
    return SessionManifest(
        session_id=session_id,
        vm_id=None,
        vm_provider=ExecutionBackendKind.CONTAINER_WORKER_VM.value,
        model=model,
        directory_path=directory_path,
        allowed_tools=resolved_tools,
        allowed_script_names=normalize_allowed_script_names(allowed_script_names),
        discord_channel_allowlist=discord_channels or [],
        email_folder_allowlist=resolved_email_folders,
        email_mailbox_allowlist=resolved_email_mailboxes,
        email_folder_allowlist_by_mailbox=config.email_folder_allowlist_by_mailbox,
        email_draft_recipient_allowlist=config.email_draft_recipient_allowlist,
        email_draft_domain_allowlist=config.email_draft_domain_allowlist,
        email_send_enabled=config.email_send_enabled,
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
    return normalize_optional_string(config.container_worker_guest_gateway_url) or config.gateway_url


def container_worker_guest_openai_base_url(config: GatewayConfig) -> str:
    configured = normalize_optional_string(config.container_worker_guest_openai_base_url)
    if configured:
        return configured
    return _service_url_from_gateway_url(config.gateway_url, port=config.openai_relay_port, path="/v1")


def container_worker_guest_nuget_relay_base_url(config: GatewayConfig) -> str:
    configured = normalize_optional_string(config.container_worker_guest_nuget_relay_base_url)
    if configured:
        return configured
    return _service_url_from_gateway_url(config.gateway_url, port=config.nuget_relay_port, path="")


def container_session_openai_base_url(config: GatewayConfig, session_id: str) -> str:
    return f"{container_worker_guest_openai_base_url(config).rstrip('/')}/session/{session_id}"


def container_session_gateway_url(config: GatewayConfig, session_id: str) -> str:
    base = container_worker_guest_gateway_url(config)
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}session_id={quote(session_id, safe='')}"


def build_container_session_files(
    *,
    config: GatewayConfig,
    session_id: str,
    model: str | None,
    dotnet: bool,
    nuget: bool,
    playwright: bool,
) -> list[dict[str, Any]]:
    resolved_model = model or "gpt-5.4"
    openai_base_url = container_worker_guest_openai_base_url(config)
    gateway_url = container_worker_guest_gateway_url(config)
    nuget_relay_base_url = container_worker_guest_nuget_relay_base_url(config)
    session_openai_base_url = container_session_openai_base_url(config, session_id)
    session_gateway_url = container_session_gateway_url(config, session_id)
    files: list[dict[str, Any]] = [
        {
            "destination_path": "/home/agent/.agent-sandbox/session.env",
            "mode": "0600",
            "content": _session_env_content(
                {
                    "AGENT_SANDBOX_SESSION_ID": session_id,
                    "AGENT_SANDBOX_GATEWAY_URL": gateway_url,
                    "AGENT_SANDBOX_LLM_BASE_URL": openai_base_url,
                    "OPENAI_BASE_URL": openai_base_url,
                    "NUGET_RELAY_BASE_URL": nuget_relay_base_url,
                    "AGENT_SANDBOX_NUGET_FALLBACK_PACKAGES": (
                        "/opt/agent-sandbox/nuget-cache-ro"
                    ),
                    "NUGET_PACKAGES": "/home/agent/.nuget/packages-local",
                    "NUGET_HTTP_CACHE_PATH": "/home/agent/.nuget/http-cache",
                }
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
            "content": (
                'if [ -f "$HOME/.profile" ]; then\n'
                '  . "$HOME/.profile"\n'
                "fi\n"
            ),
        },
        {
            "destination_path": "/home/agent/.bash_profile",
            "mode": "0644",
            "content": (
                'if [ -f "$HOME/.profile" ]; then\n'
                '  . "$HOME/.profile"\n'
                "fi\n"
            ),
        },
        {
            "destination_path": "/home/agent/.codex/config.toml",
            "mode": "0600",
            "content": _codex_config_content(
                model=resolved_model,
                openai_base_url=session_openai_base_url,
                gateway_url=session_gateway_url,
            ),
        },
        {
            "destination_path": "/home/agent/.config/agent-sandbox/codex/config.toml",
            "mode": "0600",
            "content": _codex_config_content(
                model=resolved_model,
                openai_base_url=session_openai_base_url,
                gateway_url=session_gateway_url,
            ),
        },
    ]
    if dotnet:
        files.extend(
            [
                {
                    "destination_path": "/home/agent/.dotnet-cli/.keep",
                    "content": "",
                    "mode": "0644",
                },
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
        "export OPENAI_API_KEY=\"${OPENAI_API_KEY:-relay-placeholder}\"\n"
    )


def map_install_roots_to_workspace(
    *,
    host_checkout: str,
    install_roots: Sequence[str],
) -> list[str]:
    normalized_checkout = str(Path(host_checkout).expanduser().resolve())
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
            mapped.append("/workspace")
        elif resolved_root.startswith(f"{normalized_checkout}/"):
            relative = resolved_root[len(normalized_checkout) + 1 :]
            mapped.append(f"/workspace/{relative}")
        elif root.startswith("/workspace"):
            mapped.append(root)
    return list(dict.fromkeys(mapped))


def frontend_bootstrap_command(install_roots: Sequence[str]) -> str:
    quoted_roots = " ".join(shlex.quote(root) for root in install_roots)
    return (
        "set -euo pipefail; "
        f"for dir in {quoted_roots}; do "
        "if [ -f \"$dir/package-lock.json\" ]; then "
        "(cd \"$dir\" && npm ci); "
        "elif [ -f \"$dir/package.json\" ]; then "
        "(cd \"$dir\" && npm install); "
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
) -> str:
    common_args = [
        "--dangerously-bypass-approvals-and-sandbox",
        "--skip-git-repo-check",
        "--model",
        model,
    ]
    if resume_thread_id:
        args = ["codex", "exec", "resume", resume_thread_id, *common_args, prompt]
    else:
        args = [
            "codex",
            "exec",
            "-s",
            "danger-full-access",
            *common_args,
            "-C",
            "/workspace",
            prompt,
        ]
    return container_shell_preamble() + "cd /workspace\n" + shlex.join(args) + "\n"


def safe_effect_execution_id(prefix: str, session_id: str) -> str:
    safe_session = "".join(char if char.isalnum() or char in "._-" else "-" for char in session_id)
    return f"{prefix}-{safe_session}-{uuid.uuid4().hex[:12]}"


def _session_env_content(values: Mapping[str, str]) -> str:
    return "".join(f"{key}={shlex.quote(value)}\n" for key, value in sorted(values.items()))


def _container_profile_content(*, dotnet: bool, playwright: bool) -> str:
    lines = [
        'export HOME="${HOME:-/home/agent}"',
        'export PATH="$HOME/bin:$PATH"',
        'export CODEX_HOME="${CODEX_HOME:-$HOME/.config/agent-sandbox/codex}"',
        'if [ -f "$HOME/.agent-sandbox/session.env" ]; then',
        "  set -a",
        '  . "$HOME/.agent-sandbox/session.env"',
        "  set +a",
        "fi",
        'export OPENAI_API_KEY="${OPENAI_API_KEY:-relay-placeholder}"',
    ]
    if dotnet:
        lines.extend(
            [
                'export DOTNET_ROOT="/opt/agent-sandbox/toolchains/dotnet"',
                'export DOTNET_CLI_HOME="$HOME/.dotnet-cli"',
                'export PATH="$DOTNET_ROOT:$HOME/.dotnet/tools:$PATH"',
            ]
        )
    if playwright:
        lines.extend(
            [
                'export AGENT_SANDBOX_NODE_ROOT="/opt/agent-sandbox/toolchains/node-v24.14.0"',
                'export NODE_PATH="/opt/agent-sandbox/playwright-runtime/node_modules"',
                'export PLAYWRIGHT_BROWSERS_PATH="/opt/agent-sandbox/ms-playwright"',
                'export PATH="$AGENT_SANDBOX_NODE_ROOT/bin:$PATH"',
            ]
        )
    return "\n".join(lines) + "\n"


def _codex_config_content(*, model: str, openai_base_url: str, gateway_url: str) -> str:
    return (
        f"model = {_toml_string(model)}\n"
        'model_provider = "openai"\n'
        'model_reasoning_effort = "high"\n'
        'personality = "pragmatic"\n'
        f"openai_base_url = {_toml_string(openai_base_url)}\n"
        "\n"
        "[mcp_servers.agent_sandbox]\n"
        f"url = {_toml_string(gateway_url)}\n"
        "enabled = true\n"
        "\n"
        '[projects."/workspace"]\n'
        'trust_level = "trusted"\n'
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
        "  <fallbackPackageFolders>\n"
        '    <add key="shared-readonly" value="/opt/agent-sandbox/nuget-cache-ro" />\n'
        "  </fallbackPackageFolders>\n"
        "</configuration>\n"
    )


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
