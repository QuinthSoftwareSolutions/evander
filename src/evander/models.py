from __future__ import annotations

import json
import re
from collections import Counter
from datetime import UTC, datetime
from enum import Enum
from hashlib import sha256
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import (
    AliasChoices,
    AnyHttpUrl,
    BaseModel,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from evander.reasoning_effort import ReasoningEffort, ReasoningEffortSource
from evander.ado_query_mode import AdoQueryMode, normalize_ado_query_mode, validate_ado_query_mode
from evander.approval_notification_targets import (
    normalize_approval_notification_target_ids,
)
from evander.codex_auth import (
    CodexAuthMode,
    normalize_codex_auth_mode,
    normalize_subscription_profile_id,
)

_HTTP_URL_ADAPTER = TypeAdapter(AnyHttpUrl)
ADO_DAILY_STATUS_REPORT_DEFAULT_MAX_ITEMS = 1000
ADO_DAILY_STATUS_REPORT_MAX_ITEMS_LIMIT = 5000


def utc_now() -> datetime:
    return datetime.now(UTC)


def normalize_ado_mention_refs(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise ValueError("mentions must be a list of strings")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item).strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(text)
    return normalized


class ToolName(str, Enum):
    DOCUMENTATION_LIST = "list_documentation"
    DOCUMENTATION_SEARCH = "search_documentation"
    DOCUMENTATION_READ = "read_documentation"
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
    GET_ADO_DAILY_STATUS_REPORT_DATA = "get_ado_daily_status_report_data"
    GET_ADO_BACKLOG_ITEM = "get_ado_backlog_item"
    POST_ADO_BACKLOG_COMMENT = "post_ado_backlog_comment"
    DELETE_ADO_BACKLOG_ITEM = "delete_ado_backlog_item"
    UPDATE_ADO_BACKLOG_TEXT = "update_ado_backlog_text"
    UPDATE_ADO_BACKLOG_TITLE = "update_ado_backlog_title"
    UPDATE_ADO_BACKLOG_STATE = "update_ado_backlog_state"
    UPDATE_ADO_BACKLOG_TAGS = "update_ado_backlog_tags"
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
    LIST_INLINE_MEDIA = "list_inline_media"
    DOWNLOAD_INLINE_MEDIA = "download_inline_media"
    STAGE_ARTIFACT = "stage_artifact"
    COMMUNICATION_COPY_ARTIFACT_TO_WORKSPACE = "communication_copy_artifact_to_workspace"
    COMMUNICATION_STAGE_WORKSPACE_FILE = "communication_stage_workspace_file"
    DISCORD_POST_FILE = "discord_post_file"
    TEAMS_GET_MESSAGES = "teams_get_messages"
    TEAMS_POST_STATUS = "teams_post_status"
    TEAMS_POST_FILE = "teams_post_file"
    TEAMS_MEETING_GET_EVENTS = "teams_meeting_get_events"
    TEAMS_MEETING_SPEAK = "teams_meeting_speak"
    TEAMS_MEETING_STOP = "teams_meeting_stop"
    ADD_MESSAGE_REACTION = "add_message_reaction"
    LIST_CONTACTS = "list_contacts"
    MAKE_PHONE_CALL = "make_phone_call"
    GET_PHONE_CALL_RESULT = "get_phone_call_result"
    SUBMIT_VOICE_QUERY_RESPONSE = "submit_voice_query_response"
    EMAIL_LIST_MAILBOXES = "email_list_mailboxes"
    EMAIL_LIST_UNREAD = "email_list_unread"
    EMAIL_LIST_MESSAGES = "email_list_messages"
    EMAIL_GET_MESSAGE = "email_get_message"
    EMAIL_CREATE_DRAFT = "email_create_draft"
    EMAIL_CREATE_DRAFT_WITH_ATTACHMENTS = "email_create_draft_with_attachments"
    EMAIL_SEND = "email_send"
    EMAIL_SEND_WITH_ATTACHMENTS = "email_send_with_attachments"
    APPROVAL_SUBMIT = "approval_submit"
    APPROVAL_CHECK = "approval_check"
    EMAIL_SEND_APPROVED = "email_send_approved"
    EMAIL_SEND_APPROVED_WITH_ATTACHMENTS = "email_send_approved_with_attachments"
    HAS_PENDING_FEEDBACK = "has_pending_feedback"
    LIST_UNREAD_FEEDBACK = "list_unread_feedback"
    LIST_ALL_FEEDBACK = "list_all_feedback"
    LIST_PR_FEEDBACK_ATTACHMENTS = "list_pr_feedback_attachments"
    DOWNLOAD_PR_FEEDBACK_ATTACHMENT = "download_pr_feedback_attachment"
    POST_PR_FEEDBACK_REPLY = "post_pr_feedback_reply"
    LIST_PULL_REQUESTS = "list_pull_requests"
    GET_PULL_REQUEST = "get_pull_request"
    UPDATE_PULL_REQUEST = "update_pull_request"
    RESOLVE_ADO_IDENTITY = "resolve_ado_identity"
    LIST_ADO_PR_COMMENT_ACTIVITY = "list_ado_pr_comment_activity"
    LIST_ADO_PR_COMMIT_ACTIVITY = "list_ado_pr_commit_activity"
    QUERY_ADO_WORK_ITEM_ACTIVITY = "query_ado_work_item_activity"
    REQUEST_PR_REVIEW = "request_pr_review"
    POST_PR_COMMENT = "post_pr_comment"
    POST_PR_INLINE_COMMENT = "post_pr_inline_comment"
    SUBMIT_PR_REVIEW = "submit_pr_review"
    HAS_PENDING_DISCORD_MESSAGES = "has_pending_discord_messages"
    LIST_UNREAD_DISCORD_MESSAGES = "list_unread_discord_messages"
    LIST_ALL_DISCORD_MESSAGES = "list_all_discord_messages"
    SCHEDULE_RESUME = "schedule_resume"
    CANCEL_SCHEDULED_WAKE = "cancel_scheduled_wake"
    PROMPT_AGENT = "prompt_agent"
    LIST_PENDING_AGENT_PROMPTS = "list_pending_agent_prompts"
    RESPOND_TO_AGENT = "respond_to_agent"
    CONSULT_CODEBASE = "consult_codebase"
    GET_CODEBASE_CONSULTATION = "get_codebase_consultation"
    CANCEL_CODEBASE_CONSULTATION = "cancel_codebase_consultation"
    SUBMIT_CODEBASE_CONSULTATION_RESULT = "submit_codebase_consultation_result"
    PREPARE_REFERENCE_REPOSITORY = "prepare_reference_repository"
    RUN_SCRIPT = "run_script"
    GET_WORKFLOW_SNAPSHOT = "get_workflow_snapshot"
    SEMANTIC_UPSERT_TEXT = "semantic_upsert_text"
    SEMANTIC_SEARCH = "semantic_search"
    CLASSIFICATION_EVALUATE = "classification_evaluate"
    CLASSIFICATION_LIST_PROFILES = "classification_list_profiles"
    CODE_INDEX_STATUS = "code_index_status"
    CODE_SEARCH = "code_search"
    CODE_NAVIGATE = "code_navigate"
    SEMANTIC_GET_POINTS = "semantic_get_points"
    SEMANTIC_DELETE_POINTS = "semantic_delete_points"
    MSSQL_PROVISION_DATABASE = "mssql_provision_database"
    MSSQL_LIST_DATABASES = "mssql_list_databases"
    MSSQL_DROP_DATABASE = "mssql_drop_database"
    COPILOT_HOST_AUTH = "copilot_host_auth"
    LIST_CONNECTORS = "list_connectors"
    CALL_CONNECTOR = "call_connector"
    HOST_DIRECTORY_LIST = "host_directory_list"
    HOST_DIRECTORY_IMPORT = "host_directory_import"
    HOST_DIRECTORY_DELETE = "host_directory_delete"


McpToolName = ToolName | str
_MCP_TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$")


def tool_name_value(value: McpToolName) -> str:
    return value.value if isinstance(value, ToolName) else str(value).strip()


def normalize_tool_name(value: object, *, field_name: str = "tool_name") -> McpToolName:
    candidate = value.value if isinstance(value, ToolName) else str(value).strip()
    if not candidate:
        raise ValueError(f"{field_name} must be a non-empty MCP tool name")
    builtin = ToolName._value2member_map_.get(candidate)
    if builtin is not None:
        return builtin
    if not _MCP_TOOL_NAME_PATTERN.fullmatch(candidate):
        raise ValueError(
            f"{field_name} must be a valid MCP tool name using letters, numbers, "
            "underscore, dot, colon, or hyphen"
        )
    return candidate


def normalize_tool_names(value: object, *, field_name: str) -> list[McpToolName]:
    if value is None:
        return []
    if isinstance(value, (str, ToolName)):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a JSON array of tool names")
    normalized: list[McpToolName] = []
    seen: set[str] = set()
    for item in value:
        if (item.value if isinstance(item, ToolName) else str(item).strip()) == "":
            continue
        tool = normalize_tool_name(item, field_name=field_name)
        tool_value = tool_name_value(tool)
        if tool_value in seen:
            continue
        seen.add(tool_value)
        normalized.append(tool)
    return normalized


class ToolArgumentConstraintOperator(str, Enum):
    REQUIRED = "required"
    ALLOWLIST = "allowlist"
    DENYLIST = "denylist"
    FIXED = "fixed"
    PATTERN = "pattern"
    ENUM = "enum"


def normalize_tool_argument_policies(value: object, *, field_name: str) -> dict[str, list[object]]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object mapping tool names to rule lists")
    normalized: dict[str, list[object]] = {}
    seen_tools: set[str] = set()
    for raw_tool_name, raw_rules in value.items():
        tool_name = tool_name_value(
            normalize_tool_name(raw_tool_name, field_name=f"{field_name} tool name")
        )
        if tool_name in seen_tools:
            raise ValueError(f"duplicate {field_name} entry for tool: {tool_name}")
        seen_tools.add(tool_name)
        if not isinstance(raw_rules, (list, tuple)):
            raise ValueError(f"{field_name}[{tool_name!r}] must be a JSON array of rules")
        normalized[tool_name] = list(raw_rules)
    return normalized


class ToolArgumentConstraint(BaseModel):
    id: str
    path: str
    operator: ToolArgumentConstraintOperator
    value: Any | None = None
    values: list[Any] | None = None
    pattern: str | None = None
    case_sensitive: bool = True

    @field_validator("id", "path", mode="before")
    @classmethod
    def normalize_required_text(cls, value: object, info) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return text

    @field_validator("operator", mode="before")
    @classmethod
    def normalize_operator(cls, value: object) -> object:
        if isinstance(value, ToolArgumentConstraintOperator):
            return value.value
        candidate = getattr(value, "value", value)
        text = str(candidate or "").strip().lower()
        if not text:
            raise ValueError("operator must be a non-empty string")
        if text.startswith("toolargumentconstraintoperator."):
            text = text.split(".", 1)[1]
        return text

    @field_validator("values", mode="before")
    @classmethod
    def normalize_values(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            raise ValueError("values must be a JSON array")
        return list(value)

    @field_validator("pattern", mode="before")
    @classmethod
    def normalize_pattern(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value)
        if not text.strip():
            raise ValueError("pattern must be a non-empty string")
        return text

    @field_validator("case_sensitive", mode="before")
    @classmethod
    def normalize_case_sensitive(cls, value: object) -> bool:
        if value is None:
            return True
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            text = value.strip().lower()
            if text in {"true", "1", "yes", "on"}:
                return True
            if text in {"false", "0", "no", "off"}:
                return False
            raise ValueError("case_sensitive must be a boolean")
        return value

    @model_validator(mode="after")
    def validate_operator_specific_fields(self) -> "ToolArgumentConstraint":
        if self.operator in {
            ToolArgumentConstraintOperator.ALLOWLIST,
            ToolArgumentConstraintOperator.DENYLIST,
            ToolArgumentConstraintOperator.ENUM,
        }:
            if not self.values:
                raise ValueError(
                    f"values must be a non-empty JSON array when operator is {self.operator.value}"
                )
        elif self.operator == ToolArgumentConstraintOperator.FIXED:
            if "value" not in self.model_fields_set:
                raise ValueError("value is required when operator is fixed")
        elif self.operator == ToolArgumentConstraintOperator.PATTERN:
            if self.pattern is None:
                raise ValueError("pattern is required when operator is pattern")
            flags = 0 if self.case_sensitive else re.IGNORECASE
            try:
                re.compile(self.pattern, flags=flags)
            except re.error as exc:
                raise ValueError(f"pattern is not a valid regular expression: {exc}") from exc
        return self


class ToolArgumentPolicyRule(BaseModel):
    id: str
    name: str | None = None
    constraints: list[ToolArgumentConstraint]

    @field_validator("id", mode="before")
    @classmethod
    def normalize_id(cls, value: object) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("id must be a non-empty string")
        return text

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("constraints", mode="before")
    @classmethod
    def normalize_constraints(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, tuple):
            value = list(value)
        if not isinstance(value, list):
            raise ValueError("constraints must be a JSON array")
        if not value:
            raise ValueError("constraints must contain at least one constraint")
        return value

    @model_validator(mode="after")
    def validate_unique_constraint_ids(self) -> "ToolArgumentPolicyRule":
        duplicates = sorted(
            constraint_id
            for constraint_id, count in Counter(
                constraint.id for constraint in self.constraints
            ).items()
            if count > 1
        )
        if duplicates:
            duplicate_list = ", ".join(duplicates)
            raise ValueError(
                f"duplicate tool argument constraint ids are not allowed in rule {self.id!r}: {duplicate_list}"
            )
        return self


def validate_tool_argument_policy_map(
    value: dict[str, list[Any]], *, field_name: str
) -> dict[str, list[Any]]:
    duplicate_rules: list[str] = []
    for tool_name, rules in value.items():
        rule_ids = [rule.id for rule in rules if hasattr(rule, "id")]
        duplicates = sorted(rule_id for rule_id, count in Counter(rule_ids).items() if count > 1)
        if duplicates:
            duplicate_rules.append(f"{tool_name}: {', '.join(duplicates)}")
    if duplicate_rules:
        raise ValueError(
            f"duplicate tool argument policy rule ids are not allowed for {field_name}: "
            + "; ".join(duplicate_rules)
        )
    return value


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
    EXHAUSTED = "exhausted"


class ApprovalPayloadMode(str, Enum):
    EXACT = "exact"
    ANY = "any"


class ApprovalGrantSettings(BaseModel):
    ttl_seconds: int = Field(default=3600, ge=1)
    max_uses: int | None = Field(default=1, ge=1)
    payload_mode: ApprovalPayloadMode = ApprovalPayloadMode.EXACT

    @field_validator("max_uses", mode="before")
    @classmethod
    def normalize_max_uses(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip().lower()
            if not normalized or normalized in {"unlimited", "none", "null"}:
                return None
        return value


class McpToolPolicyRule(BaseModel):
    denied: bool = Field(default=False, validation_alias=AliasChoices("denied", "deny"))
    require_approval: bool = False
    default_settings: ApprovalGrantSettings | None = None
    max_ttl_seconds: int | None = Field(default=None, ge=1)
    max_uses: int | None = Field(default=None, ge=1)
    require_exact_payload: bool = False

    @field_validator("max_uses", mode="before")
    @classmethod
    def normalize_max_uses(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip().lower()
            if not normalized or normalized in {"unlimited", "none", "null"}:
                return None
        return value


def normalize_approval_tool_settings(value: object, *, field_name: str) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object mapping tool names to settings")
    normalized: dict[str, object] = {}
    seen_tools: set[str] = set()
    for raw_tool_name, raw_settings in value.items():
        tool_name = tool_name_value(
            normalize_tool_name(raw_tool_name, field_name=f"{field_name} tool name")
        )
        if tool_name in seen_tools:
            raise ValueError(f"duplicate {field_name} entry for tool: {tool_name}")
        seen_tools.add(tool_name)
        normalized[tool_name] = raw_settings
    return normalized


def normalize_mcp_tool_policies(value: object, *, field_name: str) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object mapping tool names to policies")
    normalized: dict[str, object] = {}
    seen_tools: set[str] = set()
    for raw_tool_name, raw_policy in value.items():
        tool_name = tool_name_value(
            normalize_tool_name(raw_tool_name, field_name=f"{field_name} tool name")
        )
        if tool_name in seen_tools:
            raise ValueError(f"duplicate {field_name} entry for tool: {tool_name}")
        seen_tools.add(tool_name)
        normalized[tool_name] = raw_policy
    return normalized


class ApprovalNotificationStatus(str, Enum):
    DISABLED = "disabled"
    QUEUED = "queued"
    DELIVERED = "delivered"
    PARTIAL = "partial"
    FAILED = "failed"


class ApprovalNotificationDeliveryStatus(str, Enum):
    SENT = "sent"
    FAILED = "failed"


class EmailMailboxAccess(str, Enum):
    READ = "read"
    DRAFT = "draft"
    SEND = "send"


class EmailMailboxGrant(BaseModel):
    mailbox_id: str
    access: EmailMailboxAccess = EmailMailboxAccess.READ
    folders: list[str] = Field(default_factory=list)

    @field_validator("mailbox_id", mode="before")
    @classmethod
    def normalize_mailbox_id(cls, value: object) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("mailbox_id must be a non-empty string")
        return normalized

    @field_validator("folders", mode="before")
    @classmethod
    def normalize_folders(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            raise ValueError("folders must be a list of strings")
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            folder = str(item or "").strip()
            if not folder or folder in seen:
                continue
            seen.add(folder)
            normalized.append(folder)
        return normalized

    @model_validator(mode="after")
    def require_drafts_folder_for_writes(self) -> "EmailMailboxGrant":
        if self.access in {EmailMailboxAccess.DRAFT, EmailMailboxAccess.SEND}:
            if "drafts" not in self.folders:
                raise ValueError("draft and send mailbox grants require the 'drafts' folder")
        return self


class SessionManifest(BaseModel):
    session_id: str
    vm_id: str | None = None
    vm_provider: str | None = None
    vm_provider_id: str | None = None
    model: str | None = None
    reasoning_effort: ReasoningEffort | None = None
    codex_auth_mode: CodexAuthMode | None = None
    subscription_profile_id: str | None = None
    directory_path: str = "root"
    allowed_tools: list[McpToolName]
    connector_grants: dict[str, list[str]] = Field(default_factory=dict)
    host_directory_grants: dict[str, list[str]] = Field(default_factory=dict)
    approval_required_tools: list[McpToolName] = Field(default_factory=list)
    approval_tool_settings: dict[str, ApprovalGrantSettings] = Field(default_factory=dict)
    approval_notification_target_ids: list[str] = Field(default_factory=list)
    allowed_contact_ids: list[str] | None = None
    allowed_script_names: list[str] = Field(default_factory=list)
    communication_target_ids: list[str] | None = None
    discord_channel_allowlist: list[str] = Field(default_factory=list)
    teams_channel_allowlist: list[str] = Field(default_factory=list)
    teams_chat_allowlist: list[str] = Field(default_factory=list)
    teams_user_allowlist: list[str] = Field(default_factory=list)
    email_folder_allowlist: list[str] = Field(default_factory=list)
    email_mailbox_allowlist: list[str] = Field(default_factory=list)
    email_folder_allowlist_by_mailbox: dict[str, list[str]] = Field(default_factory=dict)
    email_mailbox_grants: list[EmailMailboxGrant] | None = None
    email_draft_recipient_allowlist: list[str] = Field(default_factory=list)
    email_draft_domain_allowlist: list[str] = Field(default_factory=list)
    email_send_enabled: bool = False
    tool_argument_policies: dict[str, list[ToolArgumentPolicyRule]] = Field(default_factory=dict)
    communication_file_receive_enabled: bool = False
    communication_file_send_enabled: bool = False
    communication_file_max_bytes: int | None = Field(default=None, ge=0)
    communication_file_allowed_mime_types: list[str] = Field(default_factory=list)
    communication_file_allowed_extensions: list[str] = Field(default_factory=list)
    communication_file_blocked_extensions: list[str] = Field(default_factory=list)
    communication_file_require_approval_for_send: bool = False
    web_search_enabled: bool = False
    image_generation_enabled: bool = False
    quotas: dict[str, int] = Field(default_factory=dict)
    expires_at: datetime
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("directory_path")
    @classmethod
    def validate_directory_path(cls, value: str) -> str:
        return DirectoryPath(segments=value).as_posix()

    @field_validator("codex_auth_mode", mode="before")
    @classmethod
    def normalize_codex_auth_mode_field(cls, value: object) -> CodexAuthMode | None:
        return normalize_codex_auth_mode(value)

    @field_validator("subscription_profile_id", mode="before")
    @classmethod
    def normalize_subscription_profile_id_field(cls, value: object) -> str | None:
        return normalize_subscription_profile_id(value)

    @model_validator(mode="after")
    def validate_subscription_profile_mode(self) -> "SessionManifest":
        if (
            self.subscription_profile_id is not None
            and self.codex_auth_mode is not CodexAuthMode.CHATGPT_SUBSCRIPTION
        ):
            raise ValueError(
                "subscription_profile_id requires codex_auth_mode='chatgpt_subscription'"
            )
        return self

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

    @field_validator("approval_required_tools", mode="before")
    @classmethod
    def normalize_approval_required_tools(cls, value: object) -> list[McpToolName]:
        return normalize_tool_names(value, field_name="approval_required_tools")

    @field_validator("allowed_tools", mode="before")
    @classmethod
    def normalize_allowed_tools(cls, value: object) -> list[McpToolName]:
        return normalize_tool_names(value, field_name="allowed_tools")

    @field_validator("connector_grants", mode="before")
    @classmethod
    def normalize_connector_grants_field(cls, value: object) -> dict[str, list[str]]:
        from evander.connectors.models import normalize_connector_grants

        return normalize_connector_grants(value)

    @field_validator("host_directory_grants", mode="before")
    @classmethod
    def normalize_host_directory_grants_field(cls, value: object) -> dict[str, list[str]]:
        from evander.host_directories.models import normalize_host_directory_grants

        return normalize_host_directory_grants(value)

    @field_validator("approval_tool_settings", mode="before")
    @classmethod
    def normalize_approval_tool_settings_field(cls, value: object) -> dict[str, object]:
        return normalize_approval_tool_settings(value, field_name="approval_tool_settings")

    @field_validator("approval_notification_target_ids", mode="before")
    @classmethod
    def normalize_approval_notification_target_ids_field(cls, value: object) -> list[str]:
        return normalize_approval_notification_target_ids(
            value, field_name="approval_notification_target_ids"
        )

    @field_validator("communication_target_ids", mode="before")
    @classmethod
    def normalize_communication_target_ids(cls, value: object) -> list[str] | None:
        if value is None:
            return None
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            raise ValueError("communication_target_ids must be a list of strings or null")
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            target_id = str(item or "").strip().lower()
            if not target_id or target_id in seen:
                continue
            seen.add(target_id)
            normalized.append(target_id)
        return normalized

    @model_validator(mode="after")
    def validate_unique_email_mailbox_grants(self) -> "SessionManifest":
        if self.email_mailbox_grants is None:
            return self
        mailbox_ids = [grant.mailbox_id for grant in self.email_mailbox_grants]
        duplicates = sorted(
            mailbox_id for mailbox_id, count in Counter(mailbox_ids).items() if count > 1
        )
        if duplicates:
            raise ValueError(
                "email_mailbox_grants contains duplicate mailbox IDs: " + ", ".join(duplicates)
            )
        return self

    @field_validator("allowed_contact_ids", mode="before")
    @classmethod
    def normalize_allowed_contact_ids(cls, value: object) -> list[str] | None:
        if value is None:
            return None
        if not isinstance(value, list):
            raise ValueError("allowed_contact_ids must be a JSON array of strings or null")
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            contact_id = str(item or "").strip()
            if not contact_id:
                raise ValueError("allowed_contact_ids cannot contain empty values")
            if contact_id in seen:
                continue
            seen.add(contact_id)
            normalized.append(contact_id)
        return normalized

    @field_validator("tool_argument_policies", mode="before")
    @classmethod
    def normalize_tool_argument_policies_field(cls, value: object) -> dict[str, list[object]]:
        return normalize_tool_argument_policies(value, field_name="tool_argument_policies")

    @field_validator("tool_argument_policies")
    @classmethod
    def validate_tool_argument_policies_field(
        cls, value: dict[str, list[ToolArgumentPolicyRule]]
    ) -> dict[str, list[ToolArgumentPolicyRule]]:
        return validate_tool_argument_policy_map(value, field_name="tool_argument_policies")

    def http_targets_metadata(self) -> "SessionHttpTargetsMetadata":
        raw_metadata = self.metadata.get("http_targets")
        if raw_metadata is None:
            raw_metadata = {}
        try:
            return SessionHttpTargetsMetadata.model_validate(raw_metadata)
        except ValidationError as exc:
            raise ValueError("session metadata field 'http_targets' is invalid") from exc

    def http_target(self, alias: str) -> "HttpTargetMetadata":
        return self.http_targets_metadata().require_target(alias)

class SessionToolApprovalLease(BaseModel):
    lease_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    workflow_run_id: str = Field(min_length=1)
    tools: list[McpToolName]
    effect_execution_id: str | None = None
    approval_settings: dict[str, ApprovalGrantSettings] = Field(default_factory=dict)
    approval_notification_target_ids: list[str] = Field(default_factory=list)
    used_counts: dict[str, int] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime
    revoked_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tools", mode="before")
    @classmethod
    def normalize_tools(cls, value: object) -> list[McpToolName]:
        return normalize_tool_names(value, field_name="tools")

    @field_validator("approval_settings", mode="before")
    @classmethod
    def normalize_approval_settings(cls, value: object) -> dict[str, object]:
        return normalize_approval_tool_settings(value, field_name="approval_settings")

    @field_validator("approval_notification_target_ids", mode="before")
    @classmethod
    def normalize_approval_notification_target_ids_field(cls, value: object) -> list[str]:
        return normalize_approval_notification_target_ids(
            value, field_name="approval_notification_target_ids"
        )

    @field_validator("used_counts", mode="before")
    @classmethod
    def normalize_used_counts(cls, value: object) -> dict[str, int]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("used_counts must be a JSON object")
        normalized: dict[str, int] = {}
        for raw_tool_name, raw_count in value.items():
            tool_name = tool_name_value(
                normalize_tool_name(raw_tool_name, field_name="used_counts tool name")
            )
            count = int(raw_count)
            if count < 0:
                raise ValueError("used_counts values must be non-negative")
            normalized[tool_name] = count
        return normalized


class HttpTargetMetadata(BaseModel):
    base_url: str = Field(validation_alias=AliasChoices("base_url", "allowed_base_url"))
    managed_website_session: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
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
    container_lifecycle: "ContainerSessionLifecycle | None" = None


class ContainerSessionLifecycleState(str, Enum):
    PREPARED = "prepared"
    STARTING = "starting"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"
    ABANDONED = "abandoned"
    CLEANUP_FAILED = "cleanup_failed"
    CLEANED_UP = "cleaned_up"
    UNKNOWN = "unknown"


class ContainerSessionLifecycleAction(BaseModel):
    supported: bool = False
    kind: str | None = None
    label: str | None = None
    reason: str | None = None


class ContainerSessionLifecycleActions(BaseModel):
    cleanup: ContainerSessionLifecycleAction = Field(
        default_factory=ContainerSessionLifecycleAction
    )
    recovery: ContainerSessionLifecycleAction = Field(
        default_factory=ContainerSessionLifecycleAction
    )


class ContainerSessionLifecycle(BaseModel):
    state: ContainerSessionLifecycleState = ContainerSessionLifecycleState.UNKNOWN
    last_heartbeat_at: datetime | None = None
    last_completion_at: datetime | None = None
    last_signal_at: datetime | None = None
    cleanup_attempted_at: datetime | None = None
    cleanup_completed_at: datetime | None = None
    cleanup_error: str | None = None
    actions: ContainerSessionLifecycleActions = Field(
        default_factory=ContainerSessionLifecycleActions
    )


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
    queued: bool = False
    queue_key: str | None = None
    queue_reason: str | None = None
    dispatch_id: str | None = None
    session_id: str | None = None
    model: str | None = None
    reasoning_effort: ReasoningEffort | None = None
    reasoning_effort_source: ReasoningEffortSource | None = None
    model_provider_mode: str | None = None
    local_inference_capacity_enabled: bool = False
    local_inference_active_count: int = 0
    local_inference_max_concurrency: int | None = None
    local_inference_slot_id: str | None = None
    target_dir: str | None = None
    guest_repo_name: str | None = None
    remote_url: str | None = None


class AgentPromptQueueRecord(BaseModel):
    reasoning_effort: ReasoningEffort | None = None
    reasoning_effort_source: ReasoningEffortSource | None = None
    queue_key: str
    effect_id: str
    dispatch_id: str | None = None
    target: str
    vm_name: str
    session_id: str | None = None
    created_at: datetime
    updated_at: datetime
    coalesced_count: int = 1
    retry_count: int = 1
    max_retries: int | None = None
    subscription_profile_id: str | None = None
    queue_reason: str | None = None
    retry_not_before: datetime | None = None
    prompt_preview: str | None = None
    prompt_length: int = 0
    metadata: dict[str, str] = Field(default_factory=dict)


class AgentPromptQueueSummary(BaseModel):
    record_count: int = 0
    coalesced_count: int = 0
    oldest_created_at: datetime | None = None
    newest_updated_at: datetime | None = None
    records: list[AgentPromptQueueRecord] = Field(default_factory=list)
    truncated_count: int = 0
    read_error: str | None = None


class AgentRecord(BaseModel):
    agent_id: str
    backend: str
    target_name: str
    session_id: str | None = None
    directory_path: str = "root"
    subscription_profile_id: str | None = None
    status: str = "unknown"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime | None = None
    effective_tool_policy: dict[str, Any] | None = None
    host: dict[str, Any] = Field(default_factory=dict)
    capabilities: dict[str, bool] = Field(default_factory=dict)
    prompt_queue: AgentPromptQueueSummary = Field(default_factory=AgentPromptQueueSummary)


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
    managed_repo_id: str | None = None
    source_type: str = "local_manual"
    remote_reference: "RemoteIssueReference | None" = None
    tags: list[str] = Field(default_factory=list)
    state: str | None = None
    url: str | None = None
    comments: list[dict[str, Any]] = Field(default_factory=list)
    forwarding_policy: "IssueForwardingPolicy | None" = None
    source: str = "local_gui"
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("directory_path")
    @classmethod
    def validate_directory_path(cls, value: str) -> str:
        return DirectoryPath(segments=value).as_posix()

    @field_validator("managed_repo_id")
    @classmethod
    def validate_managed_repo_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if not re.fullmatch(r"mr_[0-9a-f]{20}", normalized):
            raise ValueError("managed_repo_id must match mr_<20-hex>")
        return normalized

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
    provider_draft_id: str | None = None
    thread_or_message_id: str | None = None
    subject: str | None = None
    body: str
    body_digest: str | None = None
    recipients: list[str] = Field(default_factory=list)
    attachment_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    sent_at: datetime | None = None
    send_status: str | None = None
    provider_message_id: str | None = None


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


class ApprovalNotificationDeliveryRecord(BaseModel):
    target_id: str
    status: ApprovalNotificationDeliveryStatus
    attempted_at: datetime = Field(default_factory=utc_now)
    provider: str | None = None
    message_id: str | None = None
    result_summary: str | None = None
    error: str | None = None

    @field_validator("target_id", mode="before")
    @classmethod
    def normalize_target_id(cls, value: object) -> str:
        text = str(value or "").strip().lower()
        if not text:
            raise ValueError("target_id cannot be empty")
        return text

    @field_validator("provider", "message_id", "result_summary", "error", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


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
    intended_effect: str | None = None
    status: ApprovalStatus = ApprovalStatus.PENDING
    approval_settings: ApprovalGrantSettings | None = None
    used_count: int = Field(default=0, ge=0)
    last_used_at: datetime | None = None
    notification_status: ApprovalNotificationStatus = ApprovalNotificationStatus.DISABLED
    notification_target_ids: list[str] = Field(default_factory=list)
    notification_attempted_at: datetime | None = None
    notification_error: str | None = None
    notification_deliveries: list[ApprovalNotificationDeliveryRecord] = Field(default_factory=list)
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

    @field_validator("notification_target_ids", mode="before")
    @classmethod
    def normalize_notification_target_ids(cls, value: object) -> list[str]:
        return normalize_approval_notification_target_ids(
            value, field_name="notification_target_ids"
        )

    @field_validator("intended_effect", mode="before")
    @classmethod
    def normalize_intended_effect(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class ApprovalQueueHealth(BaseModel):
    total: int = 0
    pending: int = 0
    approved: int = 0
    denied: int = 0
    expired: int = 0
    exhausted: int = 0
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
    thread_id: str | None = None
    parent_channel_id: str | None = None
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


class InlineMediaRef(BaseModel):
    source_kind: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    comment_id: str | None = None
    media_id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    mime_type: str | None = None
    byte_count: int | None = Field(default=None, ge=0)
    url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PrFeedbackAttachmentRef(BaseModel):
    provider: str = Field(min_length=1)
    feedback_id: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    pr_id: str = Field(min_length=1)
    thread_id: str | None = None
    comment_id: str | None = None
    attachment_id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    mime_type: str | None = None
    byte_count: int | None = Field(default=None, ge=0)
    url: str = Field(min_length=1, exclude=True, repr=False)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ListPrFeedbackAttachmentsRequest(BaseModel):
    session_id: str = Field(min_length=1)
    feedback_id: str = Field(min_length=1)


class ListPrFeedbackAttachmentsResponse(BaseModel):
    provider: str = Field(min_length=1)
    feedback_id: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    pr_id: str = Field(min_length=1)
    thread_id: str | None = None
    comment_id: str | None = None
    attachments: list[PrFeedbackAttachmentRef] = Field(default_factory=list)


class DownloadPrFeedbackAttachmentRequest(BaseModel):
    session_id: str = Field(min_length=1)
    feedback_id: str = Field(min_length=1)
    attachment_id: str = Field(min_length=1)


class DownloadPrFeedbackAttachmentResponse(BaseModel):
    artifact: "CommunicationFileArtifact"


class ListInlineMediaRequest(BaseModel):
    session_id: str = Field(min_length=1)
    source_kind: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    comment_id: str | None = None
    project: str | None = None
    mailbox_id: str | None = None
    channel_id: str | None = None
    channel_target: str | None = None


class ListInlineMediaResponse(BaseModel):
    source_kind: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    media: list[InlineMediaRef] = Field(default_factory=list)


class DownloadInlineMediaRequest(BaseModel):
    session_id: str = Field(min_length=1)
    source_kind: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    media_id: str = Field(min_length=1)
    comment_id: str | None = None
    project: str | None = None
    mailbox_id: str | None = None
    channel_id: str | None = None
    channel_target: str | None = None


class DownloadInlineMediaResponse(BaseModel):
    artifact: "CommunicationFileArtifact"


class CommunicationFileArtifact(BaseModel):
    artifact_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    source_message_id: str | None = None
    source_attachment_id: str | None = None
    source_channel: str | None = None
    source_mailbox: str | None = None
    original_filename: str | None = None
    normalized_safe_filename: str | None = None
    filename: str = Field(min_length=1)
    mime_type: str | None = None
    byte_count: int = Field(ge=0)
    sha256: str
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime | None = None
    staged_at: datetime = Field(default_factory=utc_now)
    session_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CommunicationCopyArtifactToWorkspaceRequest(BaseModel):
    session_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    destination_filename: str | None = None
    overwrite: bool = True


class CommunicationCopyArtifactToWorkspaceResponse(BaseModel):
    artifact: CommunicationFileArtifact
    destination_path: str
    copied_count: int
    copied_paths: list[str] = Field(default_factory=list)
    result: dict[str, Any] = Field(default_factory=dict)


class StageArtifactRequest(BaseModel):
    session_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    destination_path: str | None = None


class StageArtifactResponse(BaseModel):
    success: bool = True
    message: str = ""
    session_id: str | None = None
    vm_name: str | None = None
    execution_backend: str | None = None
    source_path: str = ""
    artifact_path: str = ""
    artifacts_root: str = ""
    copied_count: int = Field(default=0, ge=0)
    byte_count: int = Field(default=0, ge=0)
    result: dict[str, Any] = Field(default_factory=dict)


class CommunicationStageWorkspaceFileRequest(BaseModel):
    session_id: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    filename: str | None = None
    mime_type: str | None = None


class CommunicationStageWorkspaceFileResponse(BaseModel):
    artifact: CommunicationFileArtifact
    source_path: str


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
    published_links: list[dict[str, Any]] = Field(default_factory=list)
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
    directory_path: str | None = None
    agent_role: str | None = None
    provider: str | None = None
    repo: str | None = None
    pr_id: str | None = None
    tool_name: str | None = None
    connector_id: str | None = None
    connector_operation: str | None = None
    actor_user_id: str | None = None
    request_id: str | None = None
    correlation_id: str | None = None
    approval_request_id: str | None = None
    mcp_tool_approval_lease_id: str | None = None
    workflow_run_id: str | None = None
    trace_id: str | None = None
    duration_ms: int | None = None
    decision: str | None = None
    reason: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class PrFeedbackItem(BaseModel):
    feedback_id: str
    feedback_kind: str | None = None
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
    inline_media: list[InlineMediaRef] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


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
    feedback_kind: str | None = None
    mentions: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    idempotency_key: str | None = None
    request_id: str | None = None

    @field_validator("mentions", mode="before")
    @classmethod
    def normalize_mentions(cls, value: object) -> list[str]:
        return normalize_ado_mention_refs(value)

    @field_validator("artifact_ids", mode="before")
    @classmethod
    def normalize_artifact_ids(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("artifact_ids must be a list of strings")
        return [str(item).strip() for item in value if str(item).strip()]


class PostPrFeedbackReplyResponse(BaseModel):
    status: str
    provider: str
    repo: str
    pr_id: str
    thread_id: str | None = None
    comment_id: str | None = None
    feedback_kind: str | None = None
    reply_id: str | None = None
    reply_url: str | None = None
    uploaded_attachments: list[dict[str, Any]] = Field(default_factory=list)
    resolved_mentions: list[dict[str, str]] = Field(default_factory=list)
    idempotency_key: str | None = None


class PullRequestItem(BaseModel):
    provider: str
    repo: str
    pr_id: str
    title: str | None = None
    status: str | None = None
    description: str | None = None
    source_branch: str | None = None
    target_branch: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    closed_at: datetime | None = None
    created_by: str | None = None
    url: str | None = None
    tags: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class ListPullRequestsRequest(BaseModel):
    session_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    organization_url: str | None = None
    project: str | None = None
    status: str | None = None
    statuses: list[str] = Field(default_factory=list)
    creator_id: str | None = None
    reviewer_id: str | None = None
    source_ref_name: str | None = None
    target_ref_name: str | None = None
    source_repository_id: str | None = None
    repository_id: str | None = None
    min_time: str | None = None
    max_time: str | None = None
    query_time_range_type: str | None = None
    include_links: bool | None = None
    max_comment_length: int | None = Field(default=None, ge=0)
    tags: str | None = None
    top: int | None = Field(default=None, ge=1)
    skip: int | None = Field(default=None, ge=0)
    cursor: str | None = None

    @field_validator("statuses", mode="before")
    @classmethod
    def normalize_statuses(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            raise ValueError("statuses must be a list of strings")
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = str(item).strip()
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(text)
        return normalized

    @field_validator("tags", mode="before")
    @classmethod
    def normalize_tag_expression(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("tags must be a string")
        return value.strip() or None


class ListPullRequestsResponse(BaseModel):
    provider: str | None = None
    repo: str | None = None
    project: str | None = None
    pull_requests: list[PullRequestItem] = Field(default_factory=list)
    count: int | None = None
    paging: dict[str, Any] = Field(default_factory=dict)
    next_cursor: str | None = None


class UpdatePullRequestRequest(BaseModel):
    session_id: str = Field(min_length=1)
    provider: Literal["azure_devops"]
    repo: str = Field(min_length=1)
    pr_id: str = Field(min_length=1)
    project: str | None = None
    title: str | None = None
    description: str | None = None
    status: str | None = None
    add_tags: list[str] = Field(default_factory=list)
    remove_tags: list[str] = Field(default_factory=list)
    expected_statuses: list[str] = Field(default_factory=list)

    @field_validator("add_tags", "remove_tags", "expected_statuses", mode="before")
    @classmethod
    def normalize_string_lists(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            raise ValueError("pull request tag/status fields must be a list of strings")
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = str(item).strip()
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(text)
        return normalized

    @model_validator(mode="after")
    def validate_update(self) -> UpdatePullRequestRequest:
        if (
            self.title is None
            and self.description is None
            and self.status is None
            and not self.add_tags
            and not self.remove_tags
        ):
            raise ValueError("at least one pull request update field is required")
        tag_overlap = {tag.casefold() for tag in self.add_tags} & {
            tag.casefold() for tag in self.remove_tags
        }
        if tag_overlap:
            raise ValueError("the same pull request tag cannot be added and removed")
        return self


class UpdatePullRequestResponse(BaseModel):
    success: bool = True
    updated: bool = False
    skipped: bool = False
    skipped_reason: str | None = None
    provider: str
    repo: str
    pr_id: str
    status: str | None = None
    title: str | None = None
    description: str | None = None
    url: str | None = None
    tags: list[str] = Field(default_factory=list)
    added_tags: list[str] = Field(default_factory=list)
    removed_tags: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


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


class AdoIdentitySummary(BaseModel):
    id: str | None = None
    descriptor: str | None = None
    display_name: str | None = None
    unique_name: str | None = None
    email: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class PullRequestWorkItem(BaseModel):
    work_item_id: str
    title: str | None = None
    url: str | None = None
    assignee: AdoIdentitySummary | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class PullRequestWorkItemsResponse(BaseModel):
    provider: str
    repo: str
    pr_id: str
    work_items: list[PullRequestWorkItem] = Field(default_factory=list)
    count: int = 0
    raw: dict[str, Any] = Field(default_factory=dict)


class ResolveAdoIdentityRequest(BaseModel):
    session_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    project: str | None = None

    @field_validator("query", "project", mode="before")
    @classmethod
    def trim_optional_strings(cls, value: object, info: Any) -> str | None:
        if value is None:
            if info.field_name == "query":
                raise ValueError("query is required")
            return None
        normalized = str(value).strip()
        if not normalized and info.field_name == "query":
            raise ValueError("query is required")
        return normalized or None


class ResolveAdoIdentityResponse(BaseModel):
    provider: str = "azure_devops"
    query: str
    confidence: str
    id: str | None = None
    descriptor: str | None = None
    display_name: str | None = None
    unique_name: str | None = None
    email: str | None = None
    matches: list[AdoIdentitySummary] = Field(default_factory=list)


class AdoPrCommentActivityRequest(BaseModel):
    session_id: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    author: str | None = None
    since: datetime
    until: datetime | None = None
    project: str | None = None
    statuses: list[str] = Field(default_factory=list)
    include_system_comments: bool = False
    top: int = Field(default=100, ge=1, le=200)
    skip: int = Field(default=0, ge=0)
    exhaustive: bool = False
    max_candidates: int = Field(default=50, ge=1, le=1000)

    @field_validator("repo", "author", "project", mode="before")
    @classmethod
    def trim_optional_strings(cls, value: object, info: Any) -> str | None:
        if value is None:
            if info.field_name == "repo":
                raise ValueError("repo is required")
            return None
        normalized = str(value).strip()
        if not normalized and info.field_name == "repo":
            raise ValueError("repo is required")
        return normalized or None

    @field_validator("statuses", mode="before")
    @classmethod
    def normalize_statuses(cls, value: object) -> list[str]:
        return ListPullRequestsRequest.normalize_statuses(value)


class AdoPrCommentActivityItem(BaseModel):
    provider: str = "azure_devops"
    repo: str
    project: str | None = None
    pr_id: str
    pr_title: str | None = None
    pr_status: str | None = None
    pr_url: str | None = None
    thread_id: str | None = None
    comment_id: str | None = None
    author: str | None = None
    author_identity: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    body: str | None = None
    body_snippet: str | None = None
    file_path: str | None = None
    line: int | None = None
    resolved: bool | None = None
    url: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class AdoPrCommentActivityResponse(BaseModel):
    provider: str = "azure_devops"
    repo: str
    project: str | None = None
    comments: list[AdoPrCommentActivityItem] = Field(default_factory=list)
    count: int = 0
    paging: dict[str, Any] = Field(default_factory=dict)


class AdoPrCommitActivityRequest(BaseModel):
    session_id: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    author: str | None = None
    since: datetime
    until: datetime | None = None
    project: str | None = None
    statuses: list[str] = Field(default_factory=list)
    top: int = Field(default=100, ge=1, le=200)
    skip: int = Field(default=0, ge=0)
    exhaustive: bool = False
    max_candidates: int = Field(default=50, ge=1, le=1000)

    @field_validator("repo", "author", "project", mode="before")
    @classmethod
    def trim_optional_strings(cls, value: object, info: Any) -> str | None:
        return AdoPrCommentActivityRequest.trim_optional_strings(value, info)

    @field_validator("statuses", mode="before")
    @classmethod
    def normalize_statuses(cls, value: object) -> list[str]:
        return ListPullRequestsRequest.normalize_statuses(value)


class AdoPrCommitActivityItem(BaseModel):
    provider: str = "azure_devops"
    repo: str
    project: str | None = None
    pr_id: str
    pr_title: str | None = None
    pr_status: str | None = None
    pr_url: str | None = None
    commit_id: str
    comment: str | None = None
    url: str | None = None
    author: dict[str, Any] = Field(default_factory=dict)
    committer: dict[str, Any] = Field(default_factory=dict)
    authored_at: datetime | None = None
    committed_at: datetime | None = None
    unverified_date: bool = False
    raw: dict[str, Any] = Field(default_factory=dict)


class AdoPrCommitActivityResponse(BaseModel):
    provider: str = "azure_devops"
    repo: str
    project: str | None = None
    commits: list[AdoPrCommitActivityItem] = Field(default_factory=list)
    count: int = 0
    paging: dict[str, Any] = Field(default_factory=dict)


class AdoBacklogCreateRequest(BaseModel):
    type: AdoBacklogItemType
    project: str = Field(min_length=1)
    iteration: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str | None = None
    acceptance_criteria: str | None = None
    description_artifact_ids: list[str] = Field(default_factory=list)
    acceptance_criteria_artifact_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    assigned_user: str | None = None
    parent_backlog_item_id: int | str | None = None
    request_id: str | None = None

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
        "request_id",
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

    @field_validator(
        "description_artifact_ids",
        "acceptance_criteria_artifact_ids",
        mode="before",
    )
    @classmethod
    def normalize_artifact_ids(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("artifact IDs must be a list of strings")
        return [str(item).strip() for item in value if str(item).strip()]

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
    uploaded_attachments: list[dict[str, Any]] = Field(default_factory=list)


class AdoBacklogCommentRequest(BaseModel):
    id: int | str
    project: str | None = None
    comment: str = Field(min_length=1)
    mentions: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    request_id: str | None = None

    @field_validator("id", mode="before")
    @classmethod
    def normalize_id(cls, value: object) -> str:
        if value is None:
            raise ValueError("id is required")
        if isinstance(value, bool):
            raise ValueError("id must be an integer")
        normalized = str(value).strip()
        if not normalized or not normalized.isdigit():
            raise ValueError("id must be a numeric backlog item id")
        return normalized

    @field_validator("project", "comment", "request_id", mode="before")
    @classmethod
    def trim_optional_strings(cls, value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @field_validator("mentions", mode="before")
    @classmethod
    def normalize_mentions(cls, value: object) -> list[str]:
        return normalize_ado_mention_refs(value)

    @field_validator("artifact_ids", mode="before")
    @classmethod
    def normalize_artifact_ids(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("artifact_ids must be a list of strings")
        return [str(item).strip() for item in value if str(item).strip()]


class AdoBacklogTextUpdateRequest(BaseModel):
    id: int | str
    project: str | None = None
    description: str | None = None
    acceptance_criteria: str | None = None
    description_artifact_ids: list[str] = Field(default_factory=list)
    acceptance_criteria_artifact_ids: list[str] = Field(default_factory=list)
    request_id: str | None = None

    @field_validator("id", mode="before")
    @classmethod
    def normalize_id(cls, value: object) -> str:
        if value is None:
            raise ValueError("id is required")
        if isinstance(value, bool):
            raise ValueError("id must be an integer")
        normalized = str(value).strip()
        if not normalized or not normalized.isdigit():
            raise ValueError("id must be a numeric backlog item id")
        return normalized

    @field_validator(
        "project",
        "description",
        "acceptance_criteria",
        "request_id",
        mode="before",
    )
    @classmethod
    def trim_optional_strings(cls, value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @field_validator(
        "description_artifact_ids",
        "acceptance_criteria_artifact_ids",
        mode="before",
    )
    @classmethod
    def normalize_artifact_ids(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("artifact IDs must be a list of strings")
        return [str(item).strip() for item in value if str(item).strip()]

    def model_post_init(self, __context: Any) -> None:
        if (
            self.description is None
            and self.acceptance_criteria is None
            and not self.description_artifact_ids
            and not self.acceptance_criteria_artifact_ids
        ):
            raise ValueError("description or acceptance_criteria update is required")


class AdoBacklogTitleUpdateRequest(BaseModel):
    id: int | str
    project: str | None = None
    title: str

    @field_validator("id", mode="before")
    @classmethod
    def normalize_id(cls, value: object) -> str:
        if value is None:
            raise ValueError("id is required")
        if isinstance(value, bool):
            raise ValueError("id must be an integer")
        normalized = str(value).strip()
        if not normalized or not normalized.isdigit():
            raise ValueError("id must be a numeric backlog item id")
        return normalized

    @field_validator("project", mode="before")
    @classmethod
    def trim_project(cls, value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @field_validator("title", mode="before")
    @classmethod
    def trim_and_validate_title(cls, value: object) -> str:
        if value is None:
            normalized = ""
        else:
            normalized = str(value).strip()
        if not normalized:
            raise ValueError("title cannot be empty")
        return normalized


class AdoBacklogStateUpdateRequest(BaseModel):
    id: int | str
    project: str | None = None
    state: str
    expected_state: str | None = None

    @field_validator("id", mode="before")
    @classmethod
    def normalize_id(cls, value: object) -> str:
        if value is None:
            raise ValueError("id is required")
        if isinstance(value, bool):
            raise ValueError("id must be an integer")
        normalized = str(value).strip()
        if not normalized or not normalized.isascii() or not normalized.isdigit():
            raise ValueError("id must be a numeric backlog item id")
        if int(normalized) <= 0:
            raise ValueError("id must be positive")
        return normalized

    @field_validator("project", mode="before")
    @classmethod
    def trim_project(cls, value: object) -> str | None:
        if value is None:
            return None
        return str(value).strip() or None

    @field_validator("state", "expected_state", mode="before")
    @classmethod
    def trim_state(cls, value: object, info) -> str | None:
        if value is None and info.field_name == "expected_state":
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return value.strip()


class AdoBacklogTagsUpdateRequest(BaseModel):
    id: int | str
    project: str | None = None
    add_tags: list[str] = Field(default_factory=list)
    remove_tags: list[str] = Field(default_factory=list)

    @field_validator("id", mode="before")
    @classmethod
    def normalize_id(cls, value: object) -> str:
        if value is None:
            raise ValueError("id is required")
        if isinstance(value, bool):
            raise ValueError("id must be an integer")
        normalized = str(value).strip()
        if not normalized or not normalized.isdigit():
            raise ValueError("id must be a numeric backlog item id")
        return normalized

    @field_validator("project", mode="before")
    @classmethod
    def trim_optional_strings(cls, value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @field_validator("add_tags", "remove_tags", mode="before")
    @classmethod
    def normalize_tag_list(cls, value: object) -> list[str]:
        if value is None:
            return []
        raw_values = [value] if isinstance(value, str) else value
        if not isinstance(raw_values, list):
            raise ValueError("tags must be a list of strings")
        normalized: list[str] = []
        seen: set[str] = set()
        for raw_tag in raw_values:
            tag = str(raw_tag).strip()
            if not tag:
                continue
            key = tag.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(tag)
        return normalized

    def model_post_init(self, __context: Any) -> None:
        if not self.add_tags and not self.remove_tags:
            raise ValueError("add_tags or remove_tags is required")


class AdoBacklogQueryRequest(BaseModel):
    project: str | None = None
    query_mode: AdoQueryMode | None = None
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

    @field_validator("query_mode", mode="before")
    @classmethod
    def normalize_query_mode(cls, value: object) -> AdoQueryMode | None:
        return normalize_ado_query_mode(value)

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

    @model_validator(mode="after")
    def validate_query_mode_contract(self) -> "AdoBacklogQueryRequest":
        self.query_mode = validate_ado_query_mode(
            query_mode=self.query_mode,
            wiql=self.wiql,
            context="ADO backlog query",
        )
        return self


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


class AdoDailyStatusReportRequest(BaseModel):
    project: str | None = None
    team: str | None = None
    area_path: str | None = None
    states: list[str] = Field(default_factory=list)
    types: list[AdoBacklogItemType] = Field(
        default_factory=lambda: [AdoBacklogItemType.PRODUCT_BACKLOG_ITEM, AdoBacklogItemType.BUG]
    )
    exclude_types: list[AdoBacklogItemType] = Field(
        default_factory=lambda: [AdoBacklogItemType.TASK]
    )
    tags_any: list[str] = Field(default_factory=list)
    text_search: str | None = None
    title: str = "Daily Sprint Status"
    report_date: str | None = None
    sprint_label: str | None = None
    sprint_length: str | None = None
    elapsed_sprint_percent: float | None = Field(default=None, ge=0, le=100)
    elapsed_time_mode: str = "swedish_work_time"
    workday_start: str = "08:00"
    workday_end: str = "17:00"
    include_common_swedish_non_working_days: bool = True
    done_states: list[str] = Field(default_factory=list)
    in_progress_states: list[str] = Field(default_factory=list)
    effort_fields: list[str] = Field(default_factory=list)
    max_items: int = Field(
        default=ADO_DAILY_STATUS_REPORT_DEFAULT_MAX_ITEMS,
        ge=1,
        le=ADO_DAILY_STATUS_REPORT_MAX_ITEMS_LIMIT,
    )

    @field_validator(
        "project",
        "team",
        "area_path",
        "text_search",
        "title",
        "report_date",
        "sprint_label",
        "sprint_length",
        "elapsed_time_mode",
        "workday_start",
        "workday_end",
        mode="before",
    )
    @classmethod
    def trim_optional_strings(cls, value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @field_validator(
        "states",
        "tags_any",
        "done_states",
        "in_progress_states",
        "effort_fields",
        mode="before",
    )
    @classmethod
    def normalize_string_lists(cls, value: object) -> list[str]:
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

    @classmethod
    def _normalize_type_list(cls, value: object) -> list[AdoBacklogItemType]:
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

    @field_validator("types", mode="before")
    @classmethod
    def normalize_report_types(cls, value: object) -> list[AdoBacklogItemType]:
        normalized = cls._normalize_type_list(value)
        return normalized or [AdoBacklogItemType.PRODUCT_BACKLOG_ITEM, AdoBacklogItemType.BUG]

    @field_validator("exclude_types", mode="before")
    @classmethod
    def normalize_exclude_types(cls, value: object) -> list[AdoBacklogItemType]:
        return cls._normalize_type_list(value)

    @field_validator("elapsed_time_mode")
    @classmethod
    def validate_elapsed_time_mode(cls, value: str | None) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"calendar", "calendar_time", "raw"}:
            return "calendar_time"
        if normalized in {"swedish_work_time", "sweden_work_time", ""}:
            return "swedish_work_time"
        if normalized in {"manual", "manual_override"}:
            return "manual_override"
        raise ValueError(
            "elapsed_time_mode must be calendar_time, swedish_work_time, or manual_override"
        )

    @field_validator("max_items")
    @classmethod
    def normalize_max_items(cls, value: int) -> int:
        return max(value, ADO_DAILY_STATUS_REPORT_DEFAULT_MAX_ITEMS)

    @field_validator("workday_start", "workday_end")
    @classmethod
    def validate_workday_time(cls, value: str | None) -> str:
        normalized = str(value or "").strip()
        if not re.match(r"^[0-2]?\d:[0-5]\d$", normalized):
            raise ValueError("workday time must use HH:MM format")
        hour = int(normalized.split(":", 1)[0])
        if hour > 23:
            raise ValueError("workday time must use HH:MM format")
        return f"{hour:02d}:{normalized.split(':', 1)[1]}"


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
    inline_media: list[InlineMediaRef] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AdoWorkItemActivityRequest(BaseModel):
    session_id: str = Field(min_length=1)
    project: str | None = None
    actor: str | None = None
    since: datetime
    until: datetime | None = None
    activity_types: list[str] = Field(default_factory=lambda: ["created", "changed", "commented"])
    states: list[str] = Field(default_factory=list)
    types: list[AdoBacklogItemType] = Field(default_factory=list)
    tags_any: list[str] = Field(default_factory=list)
    top: int = Field(default=100, ge=1, le=200)
    skip: int = Field(default=0, ge=0)
    exhaustive: bool = False
    max_candidates: int = Field(default=50, ge=1, le=1000)

    @field_validator("project", "actor", mode="before")
    @classmethod
    def trim_optional_strings(cls, value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @field_validator("activity_types", mode="before")
    @classmethod
    def normalize_activity_types(cls, value: object) -> list[str]:
        if value is None:
            return ["created", "changed", "commented"]
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            raise ValueError("activity_types must be a list of strings")
        allowed = {"created", "changed", "commented"}
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = str(item).strip().casefold()
            if not text:
                continue
            if text not in allowed:
                raise ValueError(
                    "activity_types values must be one of: created, changed, commented"
                )
            if text in seen:
                continue
            seen.add(text)
            normalized.append(text)
        return normalized or ["created", "changed", "commented"]

    @field_validator("states", "tags_any", mode="before")
    @classmethod
    def normalize_string_filters(cls, value: object) -> list[str]:
        return AdoBacklogQueryRequest.normalize_string_filters(value)

    @field_validator("types", mode="before")
    @classmethod
    def normalize_types(cls, value: object) -> list[AdoBacklogItemType]:
        return AdoBacklogQueryRequest.normalize_types(value)


class AdoWorkItemActivityRecord(BaseModel):
    activity_type: str
    provenance: str
    occurred_at: datetime
    actor: str | None = None
    actor_identity: dict[str, Any] = Field(default_factory=dict)
    summary: str | None = None
    comment_id: str | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)


class AdoWorkItemActivityItem(BaseModel):
    provider: str = "azure_devops"
    id: str
    project: str | None = None
    title: str | None = None
    state: str | None = None
    type: str | None = None
    url: str | None = None
    web_url: str | None = None
    activities: list[AdoWorkItemActivityRecord] = Field(default_factory=list)
    fields: dict[str, Any] = Field(default_factory=dict)


class AdoWorkItemActivityResponse(BaseModel):
    provider: str = "azure_devops"
    project: str | None = None
    items: list[AdoWorkItemActivityItem] = Field(default_factory=list)
    count: int = 0
    paging: dict[str, Any] = Field(default_factory=dict)


class AdoBacklogDeleteRequest(BaseModel):
    id: int | str
    project: str | None = None
    destroy: bool = False

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


class AdoBacklogDeleteResponse(BaseModel):
    provider: str
    work_item_id: str
    project: str | None = None
    deleted: bool
    destroy: bool
    delete_kind: Literal["soft_delete", "permanent_destroy"]


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
    mentions: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    idempotency_key: str | None = None
    request_id: str | None = None

    @field_validator("mentions", mode="before")
    @classmethod
    def normalize_mentions(cls, value: object) -> list[str]:
        return normalize_ado_mention_refs(value)

    @field_validator("artifact_ids", mode="before")
    @classmethod
    def normalize_artifact_ids(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("artifact_ids must be a list of strings")
        return [str(item).strip() for item in value if str(item).strip()]


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
    mentions: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)

    @field_validator("mentions", mode="before")
    @classmethod
    def normalize_mentions(cls, value: object) -> list[str]:
        return normalize_ado_mention_refs(value)

    @field_validator("side")
    @classmethod
    def validate_side(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if normalized not in {"LEFT", "RIGHT"}:
            raise ValueError("side must be LEFT or RIGHT")
        return normalized

    @field_validator("artifact_ids", mode="before")
    @classmethod
    def normalize_artifact_ids(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("artifact_ids must be a list of strings")
        return [str(item).strip() for item in value if str(item).strip()]


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
    mentions: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    idempotency_key: str | None = None
    request_id: str | None = None

    @field_validator("mentions", mode="before")
    @classmethod
    def normalize_mentions(cls, value: object) -> list[str]:
        return normalize_ado_mention_refs(value)

    @field_validator("side")
    @classmethod
    def validate_side(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if normalized not in {"LEFT", "RIGHT"}:
            raise ValueError("side must be LEFT or RIGHT")
        return normalized

    @field_validator("artifact_ids", mode="before")
    @classmethod
    def normalize_artifact_ids(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("artifact_ids must be a list of strings")
        return [str(item).strip() for item in value if str(item).strip()]


class SubmitPrReviewRequest(BaseModel):
    session_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    pr_id: str = Field(min_length=1)
    body_text: str = Field(min_length=1)
    event: str | None = None
    comments: list[SubmitPrReviewComment] = Field(default_factory=list)
    mentions: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    idempotency_key: str | None = None
    request_id: str | None = None

    @field_validator("mentions", mode="before")
    @classmethod
    def normalize_mentions(cls, value: object) -> list[str]:
        return normalize_ado_mention_refs(value)

    @field_validator("artifact_ids", mode="before")
    @classmethod
    def normalize_artifact_ids(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("artifact_ids must be a list of strings")
        return [str(item).strip() for item in value if str(item).strip()]


class SubmitPrReviewResponse(BaseModel):
    status: str
    provider: str
    repo: str
    pr_id: str
    review_id: str | None = None
    review_url: str | None = None
    comment_ids: list[str] = Field(default_factory=list)
    idempotency_key: str | None = None


class MakePhoneCallRequest(BaseModel):
    session_id: str = Field(min_length=1)
    contact: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    request_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("session_id", "contact", "prompt", "request_id", mode="before")
    @classmethod
    def normalize_phone_call_strings(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("metadata", mode="before")
    @classmethod
    def normalize_phone_call_metadata(cls, value: object) -> object:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("metadata must be an object")
        return value


class MakePhoneCallResponse(BaseModel):
    request_id: str = Field(min_length=1)
    call_id: str | None = None
    contact: str = Field(min_length=1)
    status: str = Field(min_length=1)


class ListContactsRequest(BaseModel):
    session_id: str = Field(min_length=1)

    @field_validator("session_id", mode="before")
    @classmethod
    def normalize_session_id(cls, value: object) -> object:
        text = str(value or "").strip()
        return text or None


class ContactSummary(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    email_address_count: int = Field(default=0, ge=0)
    phone_number_count: int = Field(default=0, ge=0)
    primary_phone_label: str | None = None
    callable: bool = False


class ListContactsResponse(BaseModel):
    directory_path: str = Field(min_length=1)
    contacts: list[ContactSummary] = Field(default_factory=list)


class GetPhoneCallResultRequest(BaseModel):
    session_id: str = Field(min_length=1)
    request_id: str | None = None
    call_id: str | None = None

    @field_validator("session_id", "request_id", "call_id", mode="before")
    @classmethod
    def normalize_phone_call_result_strings(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def model_post_init(self, __context: Any) -> None:
        if not self.request_id and not self.call_id:
            raise ValueError("request_id or call_id is required")


class SubmitVoiceQueryResponseRequest(BaseModel):
    session_id: str = Field(min_length=1)
    query_id: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    status: str = "answered"
    citations: list[str] = Field(default_factory=list)
    confidence: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("session_id", "query_id", "answer", "status", "confidence", mode="before")
    @classmethod
    def normalize_voice_query_strings(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("citations", mode="before")
    @classmethod
    def normalize_voice_query_citations(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            raise ValueError("citations must be a list of strings")
        return [str(item).strip() for item in value if str(item).strip()]

    @field_validator("metadata", mode="before")
    @classmethod
    def normalize_voice_query_metadata(cls, value: object) -> object:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("metadata must be an object")
        return value


class SubmitVoiceQueryResponseResponse(BaseModel):
    query_id: str = Field(min_length=1)
    voice_session_id: str = Field(min_length=1)
    status: str = Field(min_length=1)


class CancelScheduledWakeRequest(BaseModel):
    session_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("session_id", "request_id", mode="before")
    @classmethod
    def normalize_required_identifier(cls, value: object, info) -> str:
        text = str(value).strip() if value is not None else ""
        if not text:
            raise ValueError(f"{info.field_name} must be non-empty")
        return text

    @field_validator("reason", mode="before")
    @classmethod
    def normalize_reason(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        return text[:1024]

    @field_validator("metadata", mode="before")
    @classmethod
    def normalize_metadata(cls, value: object) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("metadata must be an object")
        return value


class CancelScheduledWakeResponse(BaseModel):
    session_id: str = Field(min_length=1)
    request_id: str | None = None
    status: WakeRequestCancellationStatus
    request: WakeScheduledRequest | None = None


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
    directory_path: str = "root"
    schedule: WakeScheduleDefinition
    resume_prompt: WakeResumePromptPayload
    resume_mode: str = Field(default="same_context")
    status: str = Field(default="pending", min_length=1)
    enabled: bool = True
    status_reason: str | None = None
    status_updated_at: datetime | None = None
    last_dispatch_id: str | None = None
    last_dispatch_status: str | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None
    completed_at: datetime | None = None
    completion_evidence: dict[str, Any] = Field(default_factory=dict)
    source: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    last_dispatched_at: datetime | None = None
    next_run_at: datetime | None = None
    auto_dispatch_attempts: int = Field(default=0, ge=0)
    auto_retry_started_at: datetime | None = None
    auto_retry_last_backoff_seconds: float | None = Field(default=None, ge=0)
    auto_retry_exhausted_at: datetime | None = None
    cancelled_at: datetime | None = None
    cancelled_by_session_id: str | None = None
    cancel_reason: str | None = None
    cancel_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("status", mode="before")
    @classmethod
    def validate_status(cls, value: object) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            return "pending"
        return normalized

    @field_validator("directory_path")
    @classmethod
    def validate_directory_path(cls, value: str) -> str:
        return DirectoryPath(segments=value).as_posix()

    @field_validator("resume_mode")
    @classmethod
    def validate_resume_mode(cls, value: str) -> str:
        normalized = value.strip()
        if normalized not in {"same_context", "new_context"}:
            raise ValueError("resume_mode must be one of: same_context, new_context")
        return normalized


WakeRequestCancellationStatus = Literal[
    "cancelled",
    "already_cancelled",
    "already_dispatched",
    "already_completed",
    "not_found",
    "missing_request_id",
    "permission_denied",
]


class WakeRequestCancellationResult(BaseModel):
    status: WakeRequestCancellationStatus
    request_id: str | None = None
    request: WakeScheduledRequest | None = None


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
