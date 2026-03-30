from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import Enum
from hashlib import sha256
from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel, Field, field_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class ToolName(str, Enum):
    CREATE_LOCAL_ISSUE = "create_local_issue"
    LIST_ISSUES = "list_issues"
    APPROVE_ISSUE = "approve_issue"
    REJECT_ISSUE = "reject_issue"
    CLAIM_NEXT_ISSUE = "claim_next_issue"
    COMPLETE_ISSUE = "complete_issue"
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
    directory_path: str = "root"
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

    @field_validator("directory_path")
    @classmethod
    def validate_directory_path(cls, value: str) -> str:
        return DirectoryPath(segments=value).as_posix()


class DirectoryPath(BaseModel):
    segments: tuple[str, ...] = ("root",)

    @field_validator("segments", mode="before")
    @classmethod
    def normalize_segments(cls, value: object) -> tuple[str, ...]:
        if isinstance(value, str):
            pieces = tuple(part for part in PurePosixPath(value.strip()).parts if part not in {"", "."})
            return pieces
        if isinstance(value, (list, tuple)):
            return tuple(str(part).strip() for part in value)
        return ("root",)

    @field_validator("segments")
    @classmethod
    def validate_segments(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("directory path must include at least one segment")
        if value[0] != "root":
            raise ValueError("directory path must begin with 'root'")
        for segment in value:
            if not segment or "/" in segment or segment in {".", ".."}:
                raise ValueError(f"invalid directory path segment: {segment!r}")
        return value

    def as_posix(self) -> str:
        return "/".join(self.segments)


class Directory(BaseModel):
    id: str
    name: str
    parent_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("directory name cannot be empty")
        if "/" in normalized:
            raise ValueError("directory name cannot contain '/'")
        if normalized in {".", ".."}:
            raise ValueError("directory name cannot be '.' or '..'")
        return normalized


class DirectoryInfo(BaseModel):
    id: str
    name: str
    parent_id: str | None = None
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)
    path: str

    @classmethod
    def from_directory(cls, directory: Directory, *, path: str) -> "DirectoryInfo":
        return cls(
            id=directory.id,
            name=directory.name,
            parent_id=directory.parent_id,
            created_at=directory.created_at,
            metadata=directory.metadata,
            path=path,
        )


class DirectoryDetails(BaseModel):
    directory: DirectoryInfo
    ancestry: list[DirectoryInfo] = Field(default_factory=list)


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
    directory_paths: list[str] = Field(default_factory=list)
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
    issue_id: str = ""
    issue_number: str
    title: str
    body: str
    description: str | None = None
    yaml_path: str
    directory_path: str = "root"
    repo_source: str | None = None
    source_type: str = "local_manual"
    remote_reference: "RemoteIssueReference | None" = None
    tags: list[str] = Field(default_factory=list)
    state: str | None = None
    url: str | None = None
    comments: list[dict[str, str]] = Field(default_factory=list)
    forwarding_policy: "IssueForwardingPolicy | None" = None
    source: str = "local_gui"
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("directory_path")
    @classmethod
    def validate_directory_path(cls, value: str) -> str:
        return DirectoryPath(segments=value).as_posix()

    def model_post_init(self, __context: Any) -> None:
        if not self.issue_id.strip():
            self.issue_id = self.issue_number
        if self.description is None:
            self.description = self.body
        if not self.body and self.description:
            self.body = self.description


class RemoteIssueReference(BaseModel):
    provider: str
    organization: str | None = None
    project: str | None = None
    work_item_id: str | None = None
    work_item_url: str | None = None
    last_synced_at: datetime | None = None
    last_remote_updated_at: datetime | None = None
    last_known_remote_state: str | None = None


class IssueForwardingPolicy(BaseModel):
    forward_comments: bool = False
    forward_state_updates: bool = False


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


class ApprovalDecisionRecord(BaseModel):
    status: ApprovalStatus
    approver: str | None = None
    reason: str | None = None
    decided_at: datetime = Field(default_factory=utc_now)


class ApprovalRequest(BaseModel):
    request_id: str
    session_id: str
    action_type: str
    payload_digest: str
    human_summary: str
    payload: dict[str, Any] = Field(default_factory=dict)
    directory_path: str = "root"
    vm_id: str | None = None
    tool_name: str | None = None
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime
    approver: str | None = None
    decision_at: datetime | None = None
    decision_reason: str | None = None
    decision_history: list[ApprovalDecisionRecord] = Field(default_factory=list)

    @field_validator("directory_path")
    @classmethod
    def validate_directory_path(cls, value: str) -> str:
        return DirectoryPath(segments=value).as_posix()


class ApprovalQueueHealth(BaseModel):
    total: int = 0
    pending: int = 0
    approved: int = 0
    denied: int = 0
    expired: int = 0
    oldest_pending_age_seconds: int | None = None


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
