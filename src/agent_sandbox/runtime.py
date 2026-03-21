from __future__ import annotations

import os
from dataclasses import dataclass

from .audit import AuditLogger
from .config import GatewayConfig, MailboxConfig
from .email.cursor_store import DurableCursorStore
from .email.gmail_provider import GmailEmailProvider
from .email.graph_provider import GraphEmailProvider
from .email.providers import JsonEmailProvider, MultiMailboxEmailProvider
from .models import ApprovalRequest, DiscordCommandEnvelope, DraftMessage, EmailMessage, SessionManifest
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
    email_cursors = DurableCursorStore(config.email_cursor_store_path)
    discord_commands = JsonStore(config.discord_command_store_path, DiscordCommandEnvelope)
    discord_service = _build_discord_service(config, discord_commands)

    return RuntimeContext(
        config=config,
        audit=AuditLogger(config.audit_log_path),
        policy=PolicyEngine(config, sessions, approvals),
        email_service=EmailService(_build_email_provider(config, emails, drafts, email_cursors)),
        discord_service=discord_service,
        sessions=sessions,
        approvals=approvals,
    )


def _build_email_provider(
    config: GatewayConfig,
    emails: JsonStore[EmailMessage],
    drafts: JsonStore[DraftMessage],
    email_cursors: DurableCursorStore,
) -> JsonEmailProvider | MultiMailboxEmailProvider:
    mailbox_configs = config.email_mailboxes or [
        MailboxConfig(mailbox_id="default", provider="json_store", account_identifier="default")
    ]

    if all(mailbox.provider == "json_store" for mailbox in mailbox_configs):
        return JsonEmailProvider(emails, drafts)

    providers_by_mailbox: dict[str, JsonEmailProvider | GmailEmailProvider | GraphEmailProvider] = {}
    for mailbox in mailbox_configs:
        if mailbox.provider == "json_store":
            providers_by_mailbox[mailbox.mailbox_id] = JsonEmailProvider(emails, drafts)
            continue
        if mailbox.provider == "gmail":
            _validate_mailbox_access_token_env_var(mailbox)
            providers_by_mailbox[mailbox.mailbox_id] = GmailEmailProvider(
                account_identifier=mailbox.account_identifier,
                folder_mapping=mailbox.folder_mapping,
                auth_secret_refs=mailbox.auth_secret_refs,
                timeout_seconds=config.email_provider_timeout_seconds,
                cursor_store=email_cursors,
            )
            continue
        if mailbox.provider == "graph":
            _validate_mailbox_access_token_env_var(mailbox)
            providers_by_mailbox[mailbox.mailbox_id] = GraphEmailProvider(
                account_identifier=mailbox.account_identifier,
                folder_mapping=mailbox.folder_mapping,
                auth_secret_refs=mailbox.auth_secret_refs,
                timeout_seconds=config.email_provider_timeout_seconds,
                cursor_store=email_cursors,
            )
            continue
        raise RuntimeError(f"Unsupported email provider: {mailbox.provider!r}")

    return MultiMailboxEmailProvider(providers_by_mailbox)


def _validate_mailbox_access_token_env_var(mailbox: MailboxConfig) -> None:
    env_var_name = mailbox.auth_secret_refs.get("access_token", "").strip()
    if not env_var_name:
        raise RuntimeError(
            f"email mailbox {mailbox.mailbox_id!r} using provider {mailbox.provider!r} "
            "requires auth_secret_refs.access_token to be configured"
        )

    access_token = os.environ.get(env_var_name, "").strip()
    if not access_token:
        raise RuntimeError(
            f"email mailbox {mailbox.mailbox_id!r} using provider {mailbox.provider!r} "
            f"requires a non-empty access token in environment variable {env_var_name!r}"
        )


def _build_discord_service(
    config: GatewayConfig, command_store: JsonStore[DiscordCommandEnvelope]
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
        )
    raise RuntimeError(
        f"Unsupported discord backend {config.discord_backend!r}. Expected one of: 'stub', 'bot_api'."
    )
