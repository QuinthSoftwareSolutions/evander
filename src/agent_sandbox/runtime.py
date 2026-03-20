from __future__ import annotations

import os
from datetime import UTC, datetime
from dataclasses import dataclass

from .audit import AuditLogger
from .config import GatewayConfig
from .models import (
    ApprovalRequest,
    DiscordCommandEnvelope,
    DraftMessage,
    EmailMessage,
    SessionManifest,
    ToolName,
)
from .policy import PolicyEngine
from .services import (
    DiscordBotApiService,
    DiscordService,
    EmailService,
    StubDiscordService,
)
from .storage import JsonStore


@dataclass(slots=True)
class RuntimeContext:
    config: GatewayConfig
    audit: AuditLogger
    policy: PolicyEngine
    email_service: EmailService
    discord_service: DiscordService
    sessions: JsonStore[SessionManifest]
    approvals: JsonStore[ApprovalRequest]


def build_runtime(config: GatewayConfig) -> RuntimeContext:
    sessions = JsonStore(config.session_store_path, SessionManifest)
    approvals = JsonStore(config.approval_store_path, ApprovalRequest)
    drafts = JsonStore(config.draft_store_path, DraftMessage)
    emails = JsonStore(config.email_store_path, EmailMessage)
    discord_commands = JsonStore(config.discord_command_store_path, DiscordCommandEnvelope)
    discord_service = _build_discord_service(config, discord_commands, sessions)

    return RuntimeContext(
        config=config,
        audit=AuditLogger(config.audit_log_path),
        policy=PolicyEngine(config, sessions, approvals),
        email_service=EmailService(emails, drafts),
        discord_service=discord_service,
        sessions=sessions,
        approvals=approvals,
    )


def _build_discord_service(
    config: GatewayConfig,
    command_store: JsonStore[DiscordCommandEnvelope],
    sessions: JsonStore[SessionManifest],
) -> DiscordService:
    if config.discord_backend == "stub":
        return StubDiscordService(command_store)
    if config.discord_backend == "bot_api":
        env_var_name = config.discord_bot_token_env.strip()
        if not env_var_name:
            raise RuntimeError(
                "discord backend 'bot_api' requires config.discord_bot_token_env to be set"
            )
        token = os.environ.get(env_var_name, "").strip()
        if not token:
            raise RuntimeError(
                "discord backend 'bot_api' requires a bot token in environment variable "
                f"{env_var_name!r}"
            )
        return DiscordBotApiService(
            command_store,
            token=token,
            api_base_url=config.discord_api_base_url,
            poll_limit=config.discord_poll_limit,
            timeout_seconds=config.discord_timeout_seconds,
            allowed_channel_ids_provider=lambda: _resolve_allowed_discord_channel_ids(
                sessions, config
            ),
            operator_user_ids_provider=lambda: list(config.discord_operator_user_allowlist),
            include_operator_dms=config.discord_poll_operator_dms,
        )
    raise RuntimeError(
        f"Unsupported discord backend {config.discord_backend!r}. Expected one of: 'stub', 'bot_api'."
    )


def _resolve_allowed_discord_channel_ids(
    session_store: JsonStore[SessionManifest], config: GatewayConfig
) -> list[str]:
    now = datetime.now(UTC)
    channel_ids: set[str] = set(config.discord_operator_channel_allowlist)
    for session in session_store.read_all().values():
        if session.expires_at <= now:
            continue
        if ToolName.DISCORD_GET_COMMANDS not in session.allowed_tools:
            continue
        channel_ids.update(session.discord_channel_allowlist)
    return sorted({channel_id.strip() for channel_id in channel_ids if channel_id.strip()})
