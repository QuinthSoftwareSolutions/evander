from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from .config import GatewayConfig
from .models import (
    ApprovalRequest,
    ApprovalStatus,
    DiscordCommandEnvelope,
    SessionManifest,
    ToolName,
    digest_payload,
)
from .storage import JsonStore


class PolicyError(RuntimeError):
    """Raised when a tool action violates the session policy."""


class PolicyEngine:
    def __init__(
        self,
        config: GatewayConfig,
        session_store: JsonStore[SessionManifest],
        approval_store: JsonStore[ApprovalRequest],
    ) -> None:
        self.config = config
        self.session_store = session_store
        self.approval_store = approval_store

    def get_session(self, session_id: str) -> SessionManifest:
        session = self.session_store.get(session_id)
        if session is None:
            raise PolicyError(f"Unknown session: {session_id}")
        if session.expires_at <= datetime.now(UTC):
            raise PolicyError(f"Expired session: {session_id}")
        return session

    def require_tool(self, session_id: str, tool_name: ToolName) -> SessionManifest:
        session = self.get_session(session_id)
        if tool_name not in session.allowed_tools:
            raise PolicyError(f"Tool {tool_name.value} is not allowed for session {session_id}")
        return session

    def require_discord_channel(self, session: SessionManifest, channel_id: str) -> None:
        if channel_id not in session.discord_channel_allowlist:
            raise PolicyError(f"Discord channel is not allowlisted for session {session.session_id}")

    def require_discord_command_source(
        self,
        session: SessionManifest,
        command: DiscordCommandEnvelope,
    ) -> None:
        source = str(command.metadata.get("source", ""))
        if source != "discord_bot_api":
            raise PolicyError(f"Discord command {command.command_id} has untrusted source {source!r}")

        source_channel_id = str(command.metadata.get("channel_id", command.channel_id)).strip()
        source_author_id = str(command.metadata.get("author_id", command.author_id)).strip()
        if not source_channel_id or not source_author_id:
            raise PolicyError(
                f"Discord command {command.command_id} is missing required source metadata"
            )

        allowlisted_channel = source_channel_id in session.discord_channel_allowlist
        allowlisted_operator = source_author_id in self.config.discord_operator_user_allowlist
        if not (allowlisted_channel or allowlisted_operator):
            raise PolicyError(
                f"Discord command {command.command_id} source is not allowlisted for session "
                f"{session.session_id}"
            )

    def require_email_folder(self, session: SessionManifest, folder: str) -> None:
        if folder not in session.email_folder_allowlist:
            raise PolicyError(f"Email folder is not allowlisted for session {session.session_id}")

    def create_approval(
        self,
        session_id: str,
        action_type: str,
        payload: dict[str, Any],
        human_summary: str,
    ) -> ApprovalRequest:
        request = ApprovalRequest(
            request_id=f"apr_{digest_payload({'session_id': session_id, **payload})[:16]}",
            session_id=session_id,
            action_type=action_type,
            payload_digest=digest_payload(payload),
            human_summary=human_summary,
            expires_at=datetime.now(UTC) + timedelta(seconds=self.config.approval_ttl_seconds),
        )
        self.approval_store.put(request.request_id, request)
        return request

    def get_approval(self, request_id: str) -> ApprovalRequest:
        request = self.approval_store.get(request_id)
        if request is None:
            raise PolicyError(f"Unknown approval request: {request_id}")
        if request.status == ApprovalStatus.APPROVED and request.expires_at <= datetime.now(UTC):
            request.status = ApprovalStatus.EXPIRED
            self.approval_store.put(request.request_id, request)
        return request

    def require_approved(self, request_id: str, payload: dict[str, Any]) -> ApprovalRequest:
        request = self.get_approval(request_id)
        if request.status != ApprovalStatus.APPROVED:
            raise PolicyError(f"Approval request {request_id} is not approved")
        if request.payload_digest != digest_payload(payload):
            raise PolicyError(f"Approval payload mismatch for request {request_id}")
        if request.expires_at <= datetime.now(UTC):
            raise PolicyError(f"Approval request {request_id} has expired")
        return request
