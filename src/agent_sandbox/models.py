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
    HAS_PENDING_FEEDBACK = "has_pending_feedback"
    LIST_UNREAD_FEEDBACK = "list_unread_feedback"
    LIST_ALL_FEEDBACK = "list_all_feedback"
    POST_PR_FEEDBACK_REPLY = "post_pr_feedback_reply"


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
    email_mailbox_allowlist: list[str] = Field(default_factory=list)
    email_folder_allowlist_by_mailbox: dict[str, list[str]] = Field(default_factory=dict)
    email_draft_recipient_allowlist: list[str] = Field(default_factory=list)
    email_draft_domain_allowlist: list[str] = Field(default_factory=list)
    email_send_enabled: bool = False
    quotas: dict[str, int] = Field(default_factory=dict)
    expires_at: datetime
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionSummary(SessionManifest):
    issue_number: str | None = None
    is_expired: bool = False


class SessionActionResult(BaseModel):
    success: bool
    session_id: str
    action: str
    message: str


class VmSummary(BaseModel):
    vm_name: str
    issue_number: str | None = None
    session_ids: list[str] = Field(default_factory=list)
    libvirt_state: str
    ip_address: str | None = None
    agent_state: str
    agent_repo_names: list[str] = Field(default_factory=list)
    last_state_update_at: datetime | None = None


class VmActionResult(BaseModel):
    success: bool
    vm_name: str
    action: str
    message: str


class WorkerSummary(BaseModel):
    worker_id: str
    name: str
    unit: str
    active_state: str
    sub_state: str | None = None
    result: str | None = None
    unit_file_state: str | None = None
    description: str | None = None
    main_pid: int | None = None
    running: bool = False
    failed: bool = False
    detail: str | None = None


class WorkerActionResult(BaseModel):
    success: bool
    worker_id: str
    unit: str
    action: str
    message: str


class LocalIssueRecord(BaseModel):
    issue_number: str
    title: str
    body: str
    yaml_path: str
    repo_source: str | None = None
    source: str = "local_gui"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class EmailMessage(BaseModel):
    message_id: str
    mailbox_id: str = "default"
    provider: str = "json_store"
    folder: str
    thread_id: str | None = None
    sender: str
    recipients: list[str]
    subject: str
    body_text: str
    body_snippet: str = ""
    unread: bool = True
    received_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        if not self.body_snippet:
            self.body_snippet = self.body_text[:160]


class DraftMessage(BaseModel):
    draft_id: str
    mailbox_id: str = "default"
    provider: str = "json_store"
    thread_or_message_id: str | None = None
    subject: str | None = None
    body: str
    recipients: list[str] = Field(default_factory=list)
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


class DiscordCommandEnvelope(BaseModel):
    command_id: str
    author_id: str
    channel_id: str
    content: str
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AuditEvent(BaseModel):
    event_type: str
    session_id: str | None = None
    vm_id: str | None = None
    provider: str | None = None
    repo: str | None = None
    pr_id: str | None = None
    tool_name: str | None = None
    decision: str | None = None
    reason: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class PrFeedbackItem(BaseModel):
    feedback_id: str
    provider: str
    repo: str
    pr_id: str
    thread_id: str | None = None
    comment_id: str | None = None
    author: str
    body: str
    file_path: str | None = None
    line: int | None = None
    created_at: datetime
    in_reply_to: str | None = None
    resolved: bool | None = None


class HasPendingFeedbackRequest(BaseModel):
    session_id: str


class HasPendingFeedbackResponse(BaseModel):
    pending: bool
    next_cursor: str | None = None


class ListUnreadFeedbackRequest(BaseModel):
    session_id: str
    cursor: str | None = None
    limit: int | None = Field(default=None, ge=1)


class ListUnreadFeedbackResponse(BaseModel):
    feedback: list[PrFeedbackItem] = Field(default_factory=list)
    next_cursor: str | None = None


class PostPrFeedbackReplyRequest(BaseModel):
    session_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    pr_id: str = Field(min_length=1)
    body_text: str = Field(min_length=1)
    thread_id: str | None = None
    comment_id: str | None = None
    idempotency_key: str | None = None
    request_id: str | None = None


class PostPrFeedbackReplyResponse(BaseModel):
    status: str
    provider: str
    repo: str
    pr_id: str
    thread_id: str | None = None
    comment_id: str | None = None
    reply_id: str | None = None
    reply_url: str | None = None
    idempotency_key: str | None = None


def digest_payload(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return sha256(serialized.encode("utf-8")).hexdigest()
