from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import Enum
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class ToolName(str, Enum):
    DISCORD_GET_COMMANDS = "discord_get_commands"
    DISCORD_POST_STATUS = "discord_post_status"
    EMAIL_LIST_UNREAD = "email_list_unread"
    EMAIL_GET_MESSAGE = "email_get_message"
    EMAIL_CREATE_DRAFT = "email_create_draft"
    APPROVAL_SUBMIT = "approval_submit"
    APPROVAL_CHECK = "approval_check"
    EMAIL_SEND_APPROVED = "email_send_approved"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"


class SessionManifest(BaseModel):
    session_id: str
    vm_id: str | None = None
    allowed_tools: list[ToolName]
    discord_channel_allowlist: list[str] = Field(default_factory=list)
    email_folder_allowlist: list[str] = Field(default_factory=list)
    quotas: dict[str, int] = Field(default_factory=dict)
    expires_at: datetime
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EmailMessage(BaseModel):
    message_id: str
    folder: str
    sender: str
    recipients: list[str]
    subject: str
    body_text: str
    unread: bool = True
    received_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DraftMessage(BaseModel):
    draft_id: str
    thread_or_message_id: str | None = None
    subject: str | None = None
    body: str
    created_at: datetime = Field(default_factory=utc_now)


class ApprovalRequest(BaseModel):
    request_id: str
    session_id: str
    action_type: str
    payload_digest: str
    human_summary: str
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime
    approver: str | None = None
    decision_at: datetime | None = None


class AuditEvent(BaseModel):
    event_type: str
    session_id: str | None = None
    vm_id: str | None = None
    tool_name: str | None = None
    decision: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


def digest_payload(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return sha256(serialized.encode("utf-8")).hexdigest()
