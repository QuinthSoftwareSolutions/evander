from __future__ import annotations

from dataclasses import dataclass

from .audit import AuditLogger
from .config import GatewayConfig
from .models import ApprovalRequest, DraftMessage, EmailMessage, SessionManifest
from .policy import PolicyEngine
from .services import DiscordCommandEnvelope, DiscordService, EmailService
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

    return RuntimeContext(
        config=config,
        audit=AuditLogger(config.audit_log_path),
        policy=PolicyEngine(config, sessions, approvals),
        email_service=EmailService(emails, drafts),
        discord_service=DiscordService(discord_commands),
        sessions=sessions,
        approvals=approvals,
    )
