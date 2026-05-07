from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import Enum
from hashlib import sha256
from pathlib import PurePosixPath
from typing import Any

from pydantic import (
    AliasChoices,
    AnyHttpUrl,
    BaseModel,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
)

_HTTP_URL_ADAPTER = TypeAdapter(AnyHttpUrl)


def utc_now() -> datetime:
    return datetime.now(UTC)


class ToolName(str, Enum):
    ASSISTANT_READ_STATUS = "assistant_read_status"
    ASSISTANT_LIST_AGENTS = "assistant_list_agents"
    ASSISTANT_INSPECT_WORKFLOWS = "assistant_inspect_workflows"
    ASSISTANT_READ_CONFIG = "assistant_read_config"
    ASSISTANT_UPSERT_CONFIG = "assistant_upsert_config"
    ASSISTANT_CREATE_WORKFLOW = "assistant_create_workflow"
    ASSISTANT_RUN_SAFE_OPERATION = "assistant_run_safe_operation"
    ASSISTANT_POST_CHAT_REPLY = "assistant_post_chat_reply"
    CREATE_ADO_BACKLOG_ITEM = "create_ado_backlog_item"
    QUERY_ADO_BACKLOG_ITEMS = "query_ado_backlog_items"
    LIST_ADO_CURRENT_ITERATION_BACKLOG_ITEMS = "list_ado_current_iteration_backlog_items"
    GET_ADO_BACKLOG_ITEM = "get_ado_backlog_item"
    CREATE_LOCAL_ISSUE = "create_local_issue"
    LIST_ISSUES = "list_issues"
    GET_ISSUE = "get_issue"
    SET_ISSUE_PLAN = "set_issue_plan"
    APPROVE_ISSUE = "approve_issue"
    COMMIT_ISSUE = "commit_issue"
    REJECT_ISSUE = "reject_issue"
    CLAIM_NEXT_ISSUE = "claim_next_issue"
    COMPLETE_ISSUE = "complete_issue"
    GET_AGENT_STATUS = "get_agent_status"
    GET_AGENT_LOGS = "get_agent_logs"
    DISCORD_GET_COMMANDS = "discord_get_commands"
    DISCORD_POST_STATUS = "discord_post_status"
    COMMUNICATION_LIST_ATTACHMENTS = "communication_list_attachments"
    COMMUNICATION_DOWNLOAD_ATTACHMENT = "communication_download_attachment"
    DISCORD_POST_FILE = "discord_post_file"
    TEAMS_GET_MESSAGES = "teams_get_messages"
    TEAMS_POST_STATUS = "teams_post_status"
    TEAMS_POST_FILE = "teams_post_file"
    TEAMS_MEETING_GET_EVENTS = "teams_meeting_get_events"
    TEAMS_MEETING_SPEAK = "teams_meeting_speak"
    TEAMS_MEETING_STOP = "teams_meeting_stop"
    ADD_MESSAGE_REACTION = "add_message_reaction"
    EMAIL_LIST_UNREAD = "email_list_unread"
    EMAIL_GET_MESSAGE = "email_get_message"
    EMAIL_CREATE_DRAFT = "email_create_draft"
    EMAIL_CREATE_DRAFT_WITH_ATTACHMENTS = "email_create_draft_with_attachments"
    APPROVAL_SUBMIT = "approval_submit"
    APPROVAL_CHECK = "approval_check"
    EMAIL_SEND_APPROVED = "email_send_approved"
    EMAIL_SEND_APPROVED_WITH_ATTACHMENTS = "email_send_approved_with_attachments"
    HAS_PENDING_FEEDBACK = "has_pending_feedback"
    LIST_UNREAD_FEEDBACK = "list_unread_feedback"
    LIST_ALL_FEEDBACK = "list_all_feedback"
    POST_PR_FEEDBACK_REPLY = "post_pr_feedback_reply"
    LIST_PULL_REQUESTS = "list_pull_requests"
    GET_PULL_REQUEST = "get_pull_request"
    REQUEST_PR_REVIEW = "request_pr_review"
    POST_PR_COMMENT = "post_pr_comment"
    POST_PR_INLINE_COMMENT = "post_pr_inline_comment"
    SUBMIT_PR_REVIEW = "submit_pr_review"
    HAS_PENDING_DISCORD_MESSAGES = "has_pending_discord_messages"
    LIST_UNREAD_DISCORD_MESSAGES = "list_unread_discord_messages"
    LIST_ALL_DISCORD_MESSAGES = "list_all_discord_messages"
    SCHEDULE_RESUME = "schedule_resume"
    PROMPT_AGENT = "prompt_agent"
    LIST_PENDING_AGENT_PROMPTS = "list_pending_agent_prompts"
    RESPOND_TO_AGENT = "respond_to_agent"
    RUN_SCRIPT = "run_script"
    COPILOT_HOST_AUTH = "copilot_host_auth"


class AdoBacklogItemType(str, Enum):
    EPIC = "Epic"
    FEATURE = "Feature"
    ISSUE = "Issue"
    PRODUCT_BACKLOG_ITEM = "Product Backlog Item"
    REQUIREMENT = "Requirement"
    TASK = "Task"
    USER_STORY = "User Story"
    BUG = "Bug"

    @classmethod
    def from_raw(cls, raw_value: str) -> "AdoBacklogItemType":
        normalized = raw_value.strip().casefold()
        lookup = {
            "epic": cls.EPIC,
            "feature": cls.FEATURE,
            "issue": cls.ISSUE,
            "product backlog item": cls.PRODUCT_BACKLOG_ITEM,
            "pbi": cls.PRODUCT_BACKLOG_ITEM,
            "requirement": cls.REQUIREMENT,
            "task": cls.TASK,
            "user story": cls.USER_STORY,
            "user_story": cls.USER_STORY,
            "user-story": cls.USER_STORY,
            "story": cls.USER_STORY,
            "bug": cls.BUG,
        }
        if normalized not in lookup:
            raise ValueError(
                "type must be one of: Epic, Feature, Issue, Product Backlog Item, Requirement, Task, User Story, Bug"
            )
        return lookup[normalized]


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"


class SessionManifest(BaseModel):
    session_id: str
    vm_id: str | None = None
    vm_provider: str | None = None
    vm_provider_id: str | None = None
    model: str | None = None
    directory_path: str = "root"
    allowed_tools: list[ToolName]
    allowed_script_names: list[str] = Field(default_factory=list)
    discord_channel_allowlist: list[str] = Field(default_factory=list)
    teams_channel_allowlist: list[str] = Field(default_factory=list)
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

    @field_validator("allowed_script_names", mode="before")
    @classmethod
    def normalize_allowed_script_names(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            raise ValueError("allowed_script_names must be a list of strings")
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            candidate = str(item).strip()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            normalized.append(candidate)
        return normalized

    def http_targets_metadata(self) -> "SessionHttpTargetsMetadata":
        raw_metadata = self.metadata.get("http_targets")
        if raw_metadata is None and "example_app" in self.metadata:
            app_metadata = self.metadata.get("example_app", {})
            if app_metadata is None:
                app_metadata = {}
            if isinstance(app_metadata, dict):
                raw_metadata = {"targets": app_metadata.get("environments", {})}
        if raw_metadata is None:
            raw_metadata = {}
        try:
            return SessionHttpTargetsMetadata.model_validate(raw_metadata)
        except ValidationError as exc:
            raise ValueError("session metadata field 'http_targets' is invalid") from exc

    def http_target(self, alias: str) -> "HttpTargetMetadata":
        return self.http_targets_metadata().require_target(alias)

    def app_metadata(self) -> "SessionMesMetadata":
        raw_metadata = self.metadata.get("example_app", {})
        if raw_metadata is None:
            raw_metadata = {}
        try:
            return SessionMesMetadata.model_validate(raw_metadata)
        except ValidationError as exc:
            raise ValueError("session metadata field 'example_app' is invalid") from exc

    def app_environment(self, alias: str) -> "MesEnvironmentMetadata":
        return self.app_metadata().require_environment(alias)


class HttpTargetMetadata(BaseModel):
    base_url: str = Field(validation_alias=AliasChoices("base_url", "allowed_base_url"))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("base_url", mode="before")
    @classmethod
    def normalize_base_url(cls, value: object) -> str:
        text = str(value).strip() if value is not None else ""
        if not text:
            raise ValueError("base_url must be non-empty")
        return text.rstrip("/")

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        try:
            _HTTP_URL_ADAPTER.validate_python(value)
        except ValidationError as exc:
            raise ValueError(f"base_url must be a valid http(s) URL, got {value!r}") from exc
        return value

    @property
    def allowed_base_url(self) -> str:
        return self.base_url


class SessionHttpTargetsMetadata(BaseModel):
    targets: dict[str, HttpTargetMetadata] = Field(default_factory=dict)

    @field_validator("targets", mode="before")
    @classmethod
    def normalize_targets(cls, value: object) -> dict[str, object]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("targets must be an object")
        normalized: dict[str, object] = {}
        for raw_alias, target in value.items():
            alias = str(raw_alias).strip()
            if not alias:
                raise ValueError("target aliases must be non-empty")
            if alias in normalized:
                raise ValueError(f"duplicate HTTP target alias: {alias}")
            normalized[alias] = target
        return normalized

    def require_target(self, alias: str) -> HttpTargetMetadata:
        normalized_alias = str(alias).strip()
        if not normalized_alias:
            raise ValueError("target alias must be non-empty")
        target = self.targets.get(normalized_alias)
        if target is None:
            raise ValueError(f"unknown HTTP target alias: {normalized_alias}")
        return target


class MesEnvironmentMetadata(HttpTargetMetadata):
    pass


class SessionMesMetadata(BaseModel):
    environments: dict[str, MesEnvironmentMetadata] = Field(default_factory=dict)

    @field_validator("environments", mode="before")
    @classmethod
    def normalize_environments(cls, value: object) -> dict[str, object]:
        return SessionHttpTargetsMetadata.normalize_targets(value)

    def require_environment(self, alias: str) -> MesEnvironmentMetadata:
        normalized_alias = str(alias).strip()
        if not normalized_alias:
            raise ValueError("environment alias must be non-empty")
        environment = self.environments.get(normalized_alias)
        if environment is None:
            raise ValueError(f"unknown APP environment alias: {normalized_alias}")
        return environment


class DirectoryPath(BaseModel):
    segments: tuple[str, ...] = ("root",)

    @field_validator("segments", mode="before")
    @classmethod
    def normalize_segments(cls, value: object) -> tuple[str, ...]:
        if isinstance(value, str):
            pieces = tuple(
                part for part in PurePosixPath(value.strip()).parts if part not in {"", "."}
            )
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
    provider: str = "libvirt"
    issue_number: str | None = None
    session_ids: list[str] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)
    directory_paths: list[str] = Field(default_factory=list)
    power_state: str = Field(validation_alias=AliasChoices("power_state", "libvirt_state"))
    provider_state: str | None = None
    provider_details: dict[str, Any] = Field(default_factory=dict)
    ip_address: str | None = None
    agent_state: str
    agent_pid: int | None = None
    agent_repo_names: list[str] = Field(default_factory=list)
    last_state_update_at: datetime | None = None
    dispatch_id: str | None = None

    @property
    def libvirt_state(self) -> str:
        return self.power_state


class VmActionResult(BaseModel):
    success: bool
    vm_name: str
    action: str
    message: str
    provider: str = "libvirt"
    provider_operation_id: str | None = None
    provider_details: dict[str, Any] = Field(default_factory=dict)
    thread_id: str | None = None
    target_dir: str | None = None
    guest_repo_name: str | None = None
    remote_url: str | None = None


class AgentRecord(BaseModel):
    agent_id: str
    backend: str
    target_name: str
    session_id: str | None = None
    directory_path: str = "root"
    status: str = "unknown"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime | None = None
    host: dict[str, Any] = Field(default_factory=dict)
    capabilities: dict[str, bool] = Field(default_factory=dict)


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
    reason: str | None = None
    rejection_reason: str | None = None
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
        self.sync_derived_fields()

    @staticmethod
    def _normalize_reason_value(value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def sync_derived_fields(self) -> None:
        metadata_fields = self.metadata.get("fields") if isinstance(self.metadata, dict) else None
        metadata_reason = None
        if isinstance(metadata_fields, dict):
            metadata_reason = self._normalize_reason_value(metadata_fields.get("Reason"))
        normalized_reason = (
            metadata_reason
            or self._normalize_reason_value(self.reason)
            or self._normalize_reason_value(self.rejection_reason)
        )
        self.reason = normalized_reason
        if str(self.state or "").strip().lower() == "closed":
            self.rejection_reason = normalized_reason
        else:
            self.rejection_reason = None


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


class TeamsDelegatedAuthBinding(BaseModel):
    identity_id: str | None = None
    account_identifier: str | None = None
    tenant_id: str | None = None

    @field_validator("identity_id", "account_identifier", "tenant_id", mode="before")
    @classmethod
    def normalize_optional_strings(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class TeamsDelegatedCredential(BaseModel):
    identity_id: str
    account_identifier: str | None = None
    tenant_id: str | None = None
    access_token: str
    refresh_token: str | None = None
    expires_at: datetime | None = None
    scopes: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("identity_id", "access_token", mode="before")
    @classmethod
    def normalize_required_strings(cls, value: object) -> object:
        text = str(value).strip() if value is not None else ""
        if not text:
            raise ValueError("value cannot be empty")
        return text

    @field_validator("account_identifier", "tenant_id", "refresh_token", mode="before")
    @classmethod
    def normalize_optional_strings(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


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


class TeamsMessageEnvelope(BaseModel):
    cursor: str
    message_id: str
    author_id: str
    team_id: str = ""
    channel_id: str = ""
    chat_id: str | None = None
    thread_id: str | None = None
    content: str
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DiscordMessageEnvelope(BaseModel):
    message_id: str
    channel_id: str
    author_id: str
    author_name: str
    content: str
    created_at: datetime
    in_reply_to: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class CommunicationAttachmentRef(BaseModel):
    provider: str = Field(min_length=1)
    message_id: str = Field(min_length=1)
    attachment_id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    mime_type: str | None = None
    byte_count: int | None = Field(default=None, ge=0)
    mailbox_id: str | None = None
    channel_id: str | None = None
    channel_target: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CommunicationFileArtifact(BaseModel):
    artifact_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    source_message_id: str | None = None
    source_attachment_id: str | None = None
    filename: str = Field(min_length=1)
    mime_type: str | None = None
    byte_count: int = Field(ge=0)
    sha256: str
    staged_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CommunicationListAttachmentsRequest(BaseModel):
    session_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    message_id: str = Field(min_length=1)
    mailbox_id: str | None = None
    channel_id: str | None = None
    channel_target: str | None = None


class CommunicationListAttachmentsResponse(BaseModel):
    provider: str = Field(min_length=1)
    message_id: str = Field(min_length=1)
    attachments: list[CommunicationAttachmentRef] = Field(default_factory=list)


class CommunicationDownloadAttachmentRequest(BaseModel):
    session_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    message_id: str = Field(min_length=1)
    attachment_id: str = Field(min_length=1)
    mailbox_id: str | None = None
    channel_id: str | None = None
    channel_target: str | None = None


class CommunicationDownloadAttachmentResponse(BaseModel):
    artifact: CommunicationFileArtifact


class CommunicationSendMessageWithAttachmentsRequest(BaseModel):
    session_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    message: str = Field(min_length=1)
    artifact_ids: list[str] = Field(default_factory=list)
    mailbox_id: str | None = None
    channel_id: str | None = None
    channel_target: str | None = None
    thread_id: str | None = None
    subject: str | None = None
    recipients: list[str] = Field(default_factory=list)
    request_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class CommunicationSendMessageWithAttachmentsResponse(BaseModel):
    provider: str = Field(min_length=1)
    status: str = Field(min_length=1)
    message_id: str | None = None
    draft_id: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    result: dict[str, Any] = Field(default_factory=dict)


class PromptAgentRequest(BaseModel):
    session_id: str = Field(min_length=1)
    target_vm_name: str | None = None
    target_session_id: str | None = None
    prompt: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None

    def model_post_init(self, __context: Any) -> None:
        has_target_vm = bool((self.target_vm_name or "").strip())
        has_target_session = bool((self.target_session_id or "").strip())
        if has_target_vm == has_target_session:
            raise ValueError(
                "exactly one target must be provided: target_vm_name or target_session_id"
            )


class PromptAgentResponse(BaseModel):
    message_id: str = Field(min_length=1)
    target_session_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    response_token: str = Field(min_length=1)


class ListPendingAgentPromptsRequest(BaseModel):
    session_id: str = Field(min_length=1)
    limit: int | None = Field(default=None, ge=1, le=100)


class PendingAgentPrompt(BaseModel):
    message_id: str = Field(min_length=1)
    sender_session_id: str = Field(min_length=1)
    target_session_id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    status: str = Field(min_length=1)
    created_at: datetime
    expires_at: datetime | None = None
    response_token: str = Field(min_length=1)
    response_token_claims: dict[str, Any] = Field(default_factory=dict)


class ListPendingAgentPromptsResponse(BaseModel):
    prompts: list[PendingAgentPrompt] = Field(default_factory=list)


class RespondToAgentRequest(BaseModel):
    session_id: str = Field(min_length=1)
    message_id: str = Field(min_length=1)
    response_token: str = Field(min_length=1)
    response_text: str = Field(min_length=1)


class RespondToAgentResponse(BaseModel):
    status: str = Field(min_length=1)
    message_id: str = Field(min_length=1)
    delivered_at: datetime | None = None


class AgentPromptEnvelope(BaseModel):
    message_id: str = Field(min_length=1)
    sender_session_id: str = Field(min_length=1)
    target_session_id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    response_token_claims: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AuditEvent(BaseModel):
    event_type: str
    session_id: str | None = None
    vm_id: str | None = None
    provider: str | None = None
    repo: str | None = None
    pr_id: str | None = None
    tool_name: str | None = None
    actor_user_id: str | None = None
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


class HasPendingDiscordMessagesRequest(BaseModel):
    session_id: str


class HasPendingDiscordMessagesResponse(BaseModel):
    pending: bool
    next_cursor: str | None = None


class ListUnreadDiscordMessagesRequest(BaseModel):
    session_id: str
    cursor: str | None = None
    limit: int | None = Field(default=None, ge=1)


class ListUnreadDiscordMessagesResponse(BaseModel):
    messages: list[DiscordMessageEnvelope] = Field(default_factory=list)
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


class PullRequestItem(BaseModel):
    provider: str
    repo: str
    pr_id: str
    title: str | None = None
    status: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: str | None = None
    url: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class ListPullRequestsRequest(BaseModel):
    session_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    status: str | None = None
    creator_id: str | None = None
    reviewer_id: str | None = None
    top: int | None = Field(default=None, ge=1)
    cursor: str | None = None


class ListPullRequestsResponse(BaseModel):
    pull_requests: list[PullRequestItem] = Field(default_factory=list)
    next_cursor: str | None = None


class PullRequestReviewer(BaseModel):
    reviewer_id: str | None = None
    display_name: str | None = None
    vote: int | None = None
    status: str | None = None
    is_required: bool | None = None


class PullRequestCommit(BaseModel):
    commit_id: str
    url: str | None = None
    comment: str | None = None


class GetPullRequestRequest(BaseModel):
    session_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    pr_id: str = Field(min_length=1)
    include_reviewers: bool | None = None
    include_commits: bool | None = None


class GetPullRequestResponse(BaseModel):
    provider: str
    repo: str
    pr_id: str
    title: str | None = None
    status: str | None = None
    url: str | None = None
    source_branch: str | None = None
    target_branch: str | None = None
    source_commit: str | None = None
    target_commit: str | None = None
    created_by: str | None = None
    reviewers: list[PullRequestReviewer] = Field(default_factory=list)
    commits: list[PullRequestCommit] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class AdoBacklogCreateRequest(BaseModel):
    type: AdoBacklogItemType
    project: str = Field(min_length=1)
    iteration: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str | None = None
    acceptance_criteria: str | None = None
    tags: list[str] = Field(default_factory=list)
    assigned_user: str | None = None
    parent_backlog_item_id: int | str | None = None

    @field_validator("type", mode="before")
    @classmethod
    def validate_type(cls, value: AdoBacklogItemType | str) -> AdoBacklogItemType:
        if isinstance(value, AdoBacklogItemType):
            return value
        return AdoBacklogItemType.from_raw(str(value))

    @field_validator(
        "project",
        "iteration",
        "title",
        "description",
        "acceptance_criteria",
        "assigned_user",
        mode="before",
    )
    @classmethod
    def trim_string_fields(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return str(value).strip()

    @field_validator("project", "iteration", "title")
    @classmethod
    def validate_required_strings(cls, value: str) -> str:
        if not value:
            raise ValueError("field cannot be empty")
        return value

    @field_validator("tags", mode="before")
    @classmethod
    def normalize_tags(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("tags must be a list of strings")
        deduped: list[str] = []
        seen: set[str] = set()
        for item in value:
            normalized = str(item).strip()
            if not normalized:
                continue
            dedupe_key = normalized.casefold()
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            deduped.append(normalized)
        return deduped

    @field_validator("parent_backlog_item_id", mode="before")
    @classmethod
    def normalize_parent_backlog_item_id(cls, value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, bool):
            raise ValueError("parent_backlog_item_id must be int-like")
        if isinstance(value, int):
            return str(value)
        normalized = str(value).strip()
        if not normalized:
            return None
        try:
            int(normalized)
        except ValueError as exc:
            raise ValueError("parent_backlog_item_id must be int-like") from exc
        return normalized


class AdoBacklogCreateResponse(BaseModel):
    created_item_id: str
    url: str
    provider: str
    project: str
    type: AdoBacklogItemType
    iteration: str
    title: str
    description: str | None = None
    acceptance_criteria: str | None = None
    tags: list[str] = Field(default_factory=list)
    assigned_user: str | None = None
    parent_backlog_item_id: str | None = None


class AdoBacklogQueryRequest(BaseModel):
    project: str | None = None
    wiql: str | None = None
    iteration: str | None = None
    area_path: str | None = None
    states: list[str] = Field(default_factory=list)
    types: list[AdoBacklogItemType] = Field(default_factory=list)
    tags_any: list[str] = Field(default_factory=list)
    text_search: str | None = None
    top: int = Field(default=50, ge=1, le=200)
    skip: int = Field(default=0, ge=0)

    @field_validator("project", "wiql", "iteration", "area_path", "text_search", mode="before")
    @classmethod
    def trim_optional_strings(cls, value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @field_validator("states", "tags_any", mode="before")
    @classmethod
    def normalize_string_filters(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("filter must be a list of strings")
        deduped: list[str] = []
        seen: set[str] = set()
        for item in value:
            normalized = str(item).strip()
            if not normalized:
                continue
            dedupe_key = normalized.casefold()
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            deduped.append(normalized)
        return deduped

    @field_validator("types", mode="before")
    @classmethod
    def normalize_types(cls, value: object) -> list[AdoBacklogItemType]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("types must be a list of item types")
        normalized: list[AdoBacklogItemType] = []
        seen: set[AdoBacklogItemType] = set()
        for item in value:
            resolved = (
                item
                if isinstance(item, AdoBacklogItemType)
                else AdoBacklogItemType.from_raw(str(item))
            )
            if resolved in seen:
                continue
            seen.add(resolved)
            normalized.append(resolved)
        return normalized


class AdoBacklogQueryItem(BaseModel):
    id: str
    title: str
    state: str | None = None
    type: str | None = None
    iteration: str | None = None
    area: str | None = None
    assigned_to: str | None = None
    url: str | None = None
    fields: dict[str, Any] = Field(default_factory=dict)


class AdoBacklogQueryPaging(BaseModel):
    skip: int = 0
    top: int = 0
    returned_count: int = 0
    total_matches: int = 0
    has_more: bool = False
    next_skip: int | None = None


class AdoBacklogQueryResponse(BaseModel):
    items: list[AdoBacklogQueryItem] = Field(default_factory=list)
    count: int = 0
    paging: AdoBacklogQueryPaging


class AdoCurrentIterationBacklogRequest(BaseModel):
    project: str | None = None
    team: str | None = None
    area_path: str | None = None
    states: list[str] = Field(default_factory=list)
    types: list[AdoBacklogItemType] = Field(default_factory=list)
    tags_any: list[str] = Field(default_factory=list)
    text_search: str | None = None
    top: int = Field(default=50, ge=1, le=200)
    skip: int = Field(default=0, ge=0)

    @field_validator("project", "team", "area_path", "text_search", mode="before")
    @classmethod
    def trim_optional_strings(cls, value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @field_validator("states", "tags_any", mode="before")
    @classmethod
    def normalize_string_filters(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("filter must be a list of strings")
        deduped: list[str] = []
        seen: set[str] = set()
        for item in value:
            normalized = str(item).strip()
            if not normalized:
                continue
            dedupe_key = normalized.casefold()
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            deduped.append(normalized)
        return deduped

    @field_validator("types", mode="before")
    @classmethod
    def normalize_types(cls, value: object) -> list[AdoBacklogItemType]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("types must be a list of item types")
        normalized: list[AdoBacklogItemType] = []
        seen: set[AdoBacklogItemType] = set()
        for item in value:
            resolved = (
                item
                if isinstance(item, AdoBacklogItemType)
                else AdoBacklogItemType.from_raw(str(item))
            )
            if resolved in seen:
                continue
            seen.add(resolved)
            normalized.append(resolved)
        return normalized


class AdoCurrentIterationBacklogResponse(BaseModel):
    project: str | None = None
    team: str | None = None
    iteration_path: str
    items: list[AdoBacklogQueryItem] = Field(default_factory=list)
    count: int = 0


class AdoBacklogGetRequest(BaseModel):
    id: int | str
    project: str | None = None
    include_comments: bool = True
    include_relations: bool = True
    include_updates: bool = False

    @field_validator("id", mode="before")
    @classmethod
    def normalize_id(cls, value: object) -> str:
        if value is None:
            raise ValueError("id is required")
        if isinstance(value, bool):
            raise ValueError("id must be an integer")
        if isinstance(value, int):
            normalized = str(value)
        else:
            normalized = str(value).strip()
        if not normalized or not normalized.isdigit():
            raise ValueError("id must be a numeric backlog item id")
        return normalized

    @field_validator("project", mode="before")
    @classmethod
    def normalize_project(cls, value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None


class AdoBacklogGetResponse(BaseModel):
    provider: str
    id: str
    project: str | None = None
    rev: int | None = None
    url: str | None = None
    web_url: str | None = None
    title: str | None = None
    state: str | None = None
    type: str | None = None
    created_at: str | None = None
    changed_at: str | None = None
    closed_at: str | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    relations: list[dict[str, Any]] = Field(default_factory=list)
    comments: list[dict[str, Any]] = Field(default_factory=list)
    updates: list[dict[str, Any]] = Field(default_factory=list)


class RequestPrReviewRequest(BaseModel):
    session_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    pr_id: str = Field(min_length=1)
    requested_by: str | None = None
    request_channel: str | None = None
    review_type: str | None = None
    instructions: str | None = None
    directory_path: str | None = None
    model: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RequestPrReviewResponse(BaseModel):
    status: str
    request_id: str
    provider: str
    repo: str
    pr_id: str
    canonical_url: str | None = None
    title: str | None = None
    body: str | None = None
    base_branch: str | None = None
    head_branch: str | None = None
    base_commit: str | None = None
    head_commit: str | None = None
    requested_at: datetime = Field(default_factory=utc_now)
    requested_by: str | None = None
    request_channel: str | None = None
    review_type: str | None = None
    instructions: str | None = None
    directory_path: str | None = None
    model: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PostPrCommentRequest(BaseModel):
    session_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    pr_id: str = Field(min_length=1)
    body_text: str = Field(min_length=1)
    idempotency_key: str | None = None
    request_id: str | None = None


class PostPrCommentResponse(BaseModel):
    status: str
    provider: str
    repo: str
    pr_id: str
    comment_id: str | None = None
    comment_url: str | None = None
    idempotency_key: str | None = None


class SubmitPrReviewComment(BaseModel):
    path: str = Field(
        min_length=1,
        description="Repository-relative file path for an inline PR comment.",
    )
    body: str = Field(
        min_length=1,
        description="Inline review comment body. Do not prefix this with the file path or line number.",
    )
    line: int | None = Field(
        default=None,
        ge=1,
        description="1-based line number to attach the inline comment to on the new/right side of the diff.",
    )
    start_line: int | None = Field(
        default=None,
        ge=1,
        description="Optional 1-based first line for a multi-line inline comment range.",
    )
    side: str | None = Field(
        default=None,
        description="Diff side for the inline comment. Use RIGHT for added/changed lines, LEFT for removed lines.",
    )

    @field_validator("side")
    @classmethod
    def validate_side(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if normalized not in {"LEFT", "RIGHT"}:
            raise ValueError("side must be LEFT or RIGHT")
        return normalized


class PostPrInlineCommentRequest(BaseModel):
    session_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    pr_id: str = Field(min_length=1)
    path: str = Field(
        min_length=1,
        description="Repository-relative file path to attach the PR comment to.",
    )
    line: int = Field(
        ge=1,
        description="1-based PR diff line number to attach the comment to.",
    )
    body_text: str = Field(
        min_length=1,
        description="Inline comment body. Do not prefix with file path or line number; the tool attaches it.",
    )
    start_line: int | None = Field(
        default=None,
        ge=1,
        description="Optional 1-based first line for a multi-line inline comment range.",
    )
    side: str | None = Field(
        default="RIGHT",
        description="Diff side for the inline comment. Use RIGHT for added/changed lines, LEFT for removed lines.",
    )
    idempotency_key: str | None = None
    request_id: str | None = None

    @field_validator("side")
    @classmethod
    def validate_side(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if normalized not in {"LEFT", "RIGHT"}:
            raise ValueError("side must be LEFT or RIGHT")
        return normalized


class SubmitPrReviewRequest(BaseModel):
    session_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    pr_id: str = Field(min_length=1)
    body_text: str = Field(min_length=1)
    event: str | None = None
    comments: list[SubmitPrReviewComment] = Field(default_factory=list)
    idempotency_key: str | None = None
    request_id: str | None = None


class SubmitPrReviewResponse(BaseModel):
    status: str
    provider: str
    repo: str
    pr_id: str
    review_id: str | None = None
    review_url: str | None = None
    comment_ids: list[str] = Field(default_factory=list)
    idempotency_key: str | None = None


class ScheduleResumeRequest(BaseModel):
    session_id: str = Field(min_length=1)
    resume_at: datetime | None = None
    cron: str | None = None
    prompt: str = Field(min_length=1)
    resume_mode: str = Field(default="same_context")
    enabled: bool = True
    request_id: str | None = None
    source: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("resume_at")
    @classmethod
    def validate_resume_at_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("resume_at must include timezone information")
        return value.astimezone(UTC)

    @field_validator("resume_mode")
    @classmethod
    def validate_resume_mode(cls, value: str) -> str:
        normalized = value.strip()
        if normalized not in {"same_context", "new_context"}:
            raise ValueError("resume_mode must be one of: same_context, new_context")
        return normalized

    def model_post_init(self, __context: Any) -> None:
        has_resume_at = self.resume_at is not None
        has_cron = bool((self.cron or "").strip())
        if has_resume_at == has_cron:
            raise ValueError("exactly one schedule definition must be provided: resume_at or cron")


class RunScriptRequest(BaseModel):
    session_id: str = Field(min_length=1)
    script_name: str = Field(min_length=1)
    parameters: list[str] = Field(default_factory=list)

    @field_validator("script_name", mode="before")
    @classmethod
    def normalize_script_name(cls, value: object) -> str:
        text = str(value).strip() if value is not None else ""
        if not text:
            raise ValueError("script_name must be non-empty")
        return text

    @field_validator("parameters", mode="before")
    @classmethod
    def normalize_parameters(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if not isinstance(value, (list, tuple)):
            raise ValueError("parameters must be a list of scalar values")
        normalized: list[str] = []
        for item in value:
            if isinstance(item, (dict, list, tuple, set)):
                raise ValueError("parameters entries must be scalar values")
            normalized.append("" if item is None else str(item))
        return normalized


class RunScriptResponse(BaseModel):
    request_id: str = Field(min_length=1)
    script_name: str = Field(min_length=1)
    parameters: list[str] = Field(default_factory=list)
    queued: bool = True


class ScriptRunRequestRecord(BaseModel):
    request_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    directory_path: str = "root"
    script_name: str = Field(min_length=1)
    parameters: list[str] = Field(default_factory=list)
    source: str = "mcp_tool"
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("directory_path")
    @classmethod
    def validate_script_request_directory_path(cls, value: str) -> str:
        return DirectoryPath(segments=value).as_posix()

    @field_validator("script_name", mode="before")
    @classmethod
    def normalize_script_request_name(cls, value: object) -> str:
        text = str(value).strip() if value is not None else ""
        if not text:
            raise ValueError("script_name must be non-empty")
        return text

    @field_validator("parameters", mode="before")
    @classmethod
    def normalize_script_request_parameters(cls, value: object) -> list[str]:
        return RunScriptRequest.normalize_parameters(value)


class WakeScheduleDefinition(BaseModel):
    resume_at: datetime | None = None
    cron: str | None = None

    @field_validator("resume_at")
    @classmethod
    def validate_resume_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("resume_at must include timezone information")
        return value.astimezone(UTC)

    def model_post_init(self, __context: Any) -> None:
        has_resume_at = self.resume_at is not None
        has_cron = bool((self.cron or "").strip())
        if has_resume_at == has_cron:
            raise ValueError("exactly one schedule definition must be provided: resume_at or cron")


class WakeResumePromptPayload(BaseModel):
    prompt: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WakeScheduledRequest(BaseModel):
    request_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    schedule: WakeScheduleDefinition
    resume_prompt: WakeResumePromptPayload
    resume_mode: str = Field(default="same_context")
    enabled: bool = True
    source: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    last_dispatched_at: datetime | None = None
    next_run_at: datetime | None = None

    @field_validator("resume_mode")
    @classmethod
    def validate_resume_mode(cls, value: str) -> str:
        normalized = value.strip()
        if normalized not in {"same_context", "new_context"}:
            raise ValueError("resume_mode must be one of: same_context, new_context")
        return normalized


class WakeDispatchRecord(BaseModel):
    dispatch_id: str = Field(min_length=1)
    request_id: str | None = None
    session_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    resume_prompt: WakeResumePromptPayload
    resume_mode: str = Field(default="same_context")
    scheduled_for: datetime | None = None
    dispatched_at: datetime = Field(default_factory=utc_now)
    source: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class WakeMessageEnvelope(BaseModel):
    message_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    request_id: str | None = None
    dispatch_id: str | None = None
    status: str = Field(min_length=1)
    resume_prompt: WakeResumePromptPayload
    resume_mode: str = Field(default="same_context")
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScheduleResumeResponse(BaseModel):
    session_id: str
    resume_at: datetime | None = None
    cron: str | None = None
    scheduled: bool = True
    request_id: str | None = None


def digest_payload(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return sha256(serialized.encode("utf-8")).hexdigest()
