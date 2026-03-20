from __future__ import annotations

import os
from dataclasses import dataclass

from .audit import AuditLogger
from .config import GatewayConfig
from .models import ApprovalRequest, DraftMessage, EmailMessage, SessionManifest
from .policy import PolicyEngine
from .services import (
    DiscordBotApiService,
    DiscordCommandEnvelope,
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
    discord_service = _build_discord_service(config, discord_commands)

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
    config: GatewayConfig, command_store: JsonStore[DiscordCommandEnvelope]
) -> DiscordService:
    if config.discord_backend == "stub":
        return StubDiscordService(command_store)
    if config.discord_backend == "bot_api":
        token = os.environ.get(config.discord_bot_token_env, "").strip()
        if not token:
            raise RuntimeError(
                "discord backend 'bot_api' requires a bot token in environment variable "
                f"{config.discord_bot_token_env!r}"
            )
        return DiscordBotApiService(
            command_store,
            token=token,
            api_base_url=config.discord_api_base_url,
            poll_limit=config.discord_poll_limit,
            timeout_seconds=config.discord_timeout_seconds,
        )
    raise RuntimeError(
        f"Unsupported discord backend {config.discord_backend!r}. Expected one of: 'stub', 'bot_api'."
    )
