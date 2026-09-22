"""Team chat models."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from evander.models import DirectoryPath, utc_now

from .model_common import (
    TEAM_CHAT_ATTACHMENT_DEFAULT_BLOCKED_EXTENSIONS,
    TEAM_CHAT_ATTACHMENT_DEFAULT_EXTENSIONS,
    TEAM_CHAT_ATTACHMENT_DEFAULT_MAX_BYTES,
    TEAM_CHAT_ATTACHMENT_DEFAULT_MAX_FILES,
    TEAM_CHAT_ATTACHMENT_DEFAULT_MIME_TYPES,
    TeamModel,
    _optional_text,
    _required_text,
    _unique_text_list,
)


class TeamConversation(TeamModel):
    id: str
    agent_id: str
    operator_user_id: str = "admin"
    operator_display_name: str | None = None
    created_by: str | None = None
    last_message_preview: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    last_interaction_at: datetime = Field(default_factory=utc_now)

    @field_validator("id", "agent_id", "operator_user_id", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("operator_display_name", "created_by", "last_message_preview", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)


class WorkLineageChatSourceRef(TeamModel):
    conversation_id: str
    message_ids: list[str] = Field(default_factory=list)

    @field_validator("conversation_id", mode="before")
    @classmethod
    def _conversation_id(cls, value: object) -> str:
        return _required_text(value, "conversation_id")

    @field_validator("message_ids", mode="before")
    @classmethod
    def _message_ids(cls, value: object) -> list[str]:
        return _unique_text_list(value or [], "message_ids")

    @model_validator(mode="after")
    def _require_messages(self) -> "WorkLineageChatSourceRef":
        if not self.message_ids:
            raise ValueError("work lineage Chat source reference requires message_ids")
        return self


class TeamWorkLineage(TeamModel):
    id: str
    chat_source_refs: list[WorkLineageChatSourceRef] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("id", mode="before")
    @classmethod
    def _id(cls, value: object) -> str:
        return _required_text(value, "id")

    @model_validator(mode="after")
    def _unique_conversations(self) -> "TeamWorkLineage":
        conversation_ids = [ref.conversation_id for ref in self.chat_source_refs]
        if len(conversation_ids) != len(set(conversation_ids)):
            raise ValueError("work lineage Chat source conversations must be unique")
        return self


def filter_work_lineage_payloads(
    value: object,
    *,
    visible_conversation_ids: set[str],
    visible_lineage_ids: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Return lineage metadata without disclosing out-of-scope Chat anchors.

    When ``visible_lineage_ids`` is omitted, Chat-backed lineages are visible
    only when at least one of their source conversations is visible. Lineages
    without Chat anchors remain visible because they carry no Chat metadata.
    Scoped state projections retain only structurally referenced lineages while
    still removing any source refs outside their Chat scope.
    """

    if not isinstance(value, Mapping):
        return {}
    filtered: dict[str, dict[str, Any]] = {}
    for lineage_id, raw_lineage in value.items():
        if not isinstance(lineage_id, str) or not isinstance(raw_lineage, Mapping):
            continue
        raw_refs = raw_lineage.get("chat_source_refs")
        refs = raw_refs if isinstance(raw_refs, list) else []
        visible_refs = [
            dict(ref)
            for ref in refs
            if isinstance(ref, Mapping) and ref.get("conversation_id") in visible_conversation_ids
        ]
        if visible_lineage_ids is None:
            include_lineage = not refs or bool(visible_refs)
        else:
            include_lineage = lineage_id in visible_lineage_ids
        if include_lineage:
            filtered[lineage_id] = {
                **dict(raw_lineage),
                "chat_source_refs": visible_refs,
            }
    return filtered


def filter_work_lineage_references(
    payload: dict[str, Any],
    *,
    visible_lineage_ids: set[str],
) -> None:
    """Remove lineage references that are absent from a filtered state payload."""

    for collection_name in ("tasks", "release_packages", "dispatches"):
        collection = payload.get(collection_name)
        if not isinstance(collection, Mapping):
            continue
        for row in collection.values():
            if not isinstance(row, dict):
                continue
            lineage_id = row.get("work_lineage_id")
            if lineage_id is not None and lineage_id not in visible_lineage_ids:
                row["work_lineage_id"] = None
    runtimes = payload.get("agent_runtimes")
    if not isinstance(runtimes, Mapping):
        return
    for runtime in runtimes.values():
        if not isinstance(runtime, dict):
            continue
        for field in ("work_lineage_thread_ids", "work_lineage_thread_dispatch_ids"):
            bindings = runtime.get(field)
            if field in runtime:
                runtime[field] = {
                    lineage_id: value
                    for lineage_id, value in (
                        bindings.items() if isinstance(bindings, Mapping) else ()
                    )
                    if lineage_id in visible_lineage_ids
                }


class TeamChatMutationAuthorization(TeamModel):
    """Server-issued mutation scope snapshot for one operator chat message."""

    version: Literal[1] = 1
    mode: Literal["reply_only", "project_scoped"] = "reply_only"
    source: Literal["admin_rbac", "external_bridge", "internal"]
    principal_id: str
    recipient_directory_path: str | None = None
    project_ids: list[str] = Field(default_factory=list)
    managed_repo_ids: list[str] = Field(default_factory=list)
    authorized_at: datetime = Field(default_factory=utc_now)
    reason: str | None = None

    @field_validator("principal_id", mode="before")
    @classmethod
    def _principal_id(cls, value: object) -> str:
        return _required_text(value, "principal_id")

    @field_validator("recipient_directory_path", mode="before")
    @classmethod
    def _recipient_directory_path(cls, value: object) -> str | None:
        text = _optional_text(value)
        return DirectoryPath(segments=text).as_posix() if text is not None else None

    @field_validator("project_ids", "managed_repo_ids", mode="before")
    @classmethod
    def _scope_ids(cls, value: object, info) -> list[str]:
        return _unique_text_list(value or [], info.field_name)

    @field_validator("reason", mode="before")
    @classmethod
    def _reason(cls, value: object) -> str | None:
        return _optional_text(value)

    @model_validator(mode="after")
    def _validate_scope(self) -> "TeamChatMutationAuthorization":
        if self.mode == "reply_only":
            if self.project_ids or self.managed_repo_ids:
                raise ValueError("reply-only Team chat authorization cannot contain scope ids")
            return self
        if self.recipient_directory_path is None:
            raise ValueError(
                "project-scoped Team chat authorization requires recipient_directory_path"
            )
        if not self.project_ids:
            raise ValueError("project-scoped Team chat authorization requires project_ids")
        return self


class TeamConversationMessage(TeamModel):
    id: str
    conversation_id: str
    sender: Literal["operator", "agent", "system"]
    message_type: Literal["text", "voice_call", "system"] = "text"
    body: str | None = None
    status: Literal[
        "created",
        "dispatching",
        "queued",
        "running",
        "seen",
        "delivered_in_dispatch",
        "acknowledged",
        "replied",
        "failed",
        "sent",
        "ended",
    ] = "sent"
    dispatch_id: str | None = None
    voice_session_id: str | None = None
    source_message_id: str | None = None
    source_message_ids: list[str] = Field(default_factory=list)
    seen_at: datetime | None = None
    seen_dispatch_id: str | None = None
    error: str | None = None
    attachment_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("id", "conversation_id", "sender", mode="before")
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator(
        "body",
        "dispatch_id",
        "voice_session_id",
        "source_message_id",
        "seen_dispatch_id",
        "error",
        mode="before",
    )
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator("source_message_ids", mode="before")
    @classmethod
    def _source_message_ids(cls, value: object) -> list[str]:
        return _unique_text_list(value or [], "source_message_ids")

    @field_validator("metadata", mode="before")
    @classmethod
    def _metadata(cls, value: object) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("metadata must be a JSON object")
        return dict(value)

    @model_validator(mode="after")
    def _validate_body(self) -> "TeamConversationMessage":
        if self.message_type == "text" and not self.body and not self.attachment_ids:
            raise ValueError("text conversation messages require body or attachments")
        return self


class ExternalChatIngressRecord(TeamModel):
    """Durable external-message provenance and ingress idempotency record."""

    id: str
    provider: str
    communication_target_id: str
    source_message_id: str
    source_thread_id: str | None = None
    source_author_id: str
    source_author_name: str | None = None
    operator_user_id: str
    conversation_id: str
    message_id: str
    dispatch_id: str
    allow_team_mutations: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator(
        "id",
        "provider",
        "communication_target_id",
        "source_message_id",
        "source_author_id",
        "operator_user_id",
        "conversation_id",
        "message_id",
        "dispatch_id",
        mode="before",
    )
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("source_thread_id", "source_author_name", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)


class ExternalChatDeliveryRecord(TeamModel):
    """A persisted request to return one Team Chat reply through its source target."""

    id: str
    agent_reply_message_id: str
    operator_message_id: str
    provider: str
    communication_target_id: str
    source_thread_id: str | None = None
    status: Literal["pending", "sending", "sent", "failed", "unknown"] = "pending"
    provider_message_id: str | None = None
    attempt_count: int = Field(default=0, ge=0)
    last_error: str | None = None
    attempted_at: datetime | None = None
    delivered_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator(
        "id",
        "agent_reply_message_id",
        "operator_message_id",
        "provider",
        "communication_target_id",
        mode="before",
    )
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("source_thread_id", "provider_message_id", "last_error", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)


class TeamChatAttachmentExternalSource(TeamModel):
    provider: Literal["teams", "discord", "email"]
    communication_target_id: str
    source_message_id: str
    source_attachment_id: str

    @field_validator(
        "provider",
        "communication_target_id",
        "source_message_id",
        "source_attachment_id",
        mode="before",
    )
    @classmethod
    def _required_source_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)


class TeamChatAttachment(TeamModel):
    id: str
    message_id: str
    conversation_id: str
    filename: str
    mime_type: str | None = None
    byte_count: int = Field(ge=0)
    sha256: str
    sender: Literal["operator", "agent"]
    created_by_session_id: str | None = None
    external_source: TeamChatAttachmentExternalSource | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator(
        "id",
        "message_id",
        "conversation_id",
        "filename",
        "sha256",
        "sender",
        mode="before",
    )
    @classmethod
    def _required_text(cls, value: object, info) -> str:
        return _required_text(value, info.field_name)

    @field_validator("mime_type", "created_by_session_id", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)


class TeamChatAttachmentPolicy(TeamModel):
    id: str
    directory_path: str
    operator_to_agent_enabled: bool | None = None
    agent_to_operator_enabled: bool | None = None
    agent_send_requires_approval: bool | None = None
    max_attachment_bytes: int | None = Field(default=None, ge=0)
    max_attachments_per_message: int | None = Field(default=None, ge=1, le=20)
    allowed_mime_types: list[str] | None = None
    allowed_extensions: list[str] | None = None
    blocked_extensions: list[str] | None = None
    updated_by: str | None = None
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("id", "directory_path", mode="before")
    @classmethod
    def _directory_text(cls, value: object, info) -> str:
        normalized = DirectoryPath(segments=str(value or "").strip()).as_posix()
        return _required_text(normalized, info.field_name)

    @field_validator("updated_by", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        return _optional_text(value)

    @field_validator(
        "allowed_mime_types",
        "allowed_extensions",
        "blocked_extensions",
        mode="before",
    )
    @classmethod
    def _optional_text_list(cls, value: object) -> list[str] | None:
        if value is None:
            return None
        if not isinstance(value, (list, tuple, set)):
            raise ValueError("attachment policy lists must be arrays")
        return sorted(
            {str(item or "").strip().lower() for item in value if str(item or "").strip()}
        )

    @model_validator(mode="after")
    def _matching_directory_id(self) -> "TeamChatAttachmentPolicy":
        if self.id != self.directory_path:
            raise ValueError("attachment policy id must match directory_path")
        return self

    @classmethod
    def root_defaults(cls) -> "TeamChatAttachmentPolicy":
        return cls(
            id="root",
            directory_path="root",
            operator_to_agent_enabled=True,
            agent_to_operator_enabled=True,
            agent_send_requires_approval=True,
            max_attachment_bytes=TEAM_CHAT_ATTACHMENT_DEFAULT_MAX_BYTES,
            max_attachments_per_message=TEAM_CHAT_ATTACHMENT_DEFAULT_MAX_FILES,
            allowed_mime_types=list(TEAM_CHAT_ATTACHMENT_DEFAULT_MIME_TYPES),
            allowed_extensions=list(TEAM_CHAT_ATTACHMENT_DEFAULT_EXTENSIONS),
            blocked_extensions=list(TEAM_CHAT_ATTACHMENT_DEFAULT_BLOCKED_EXTENSIONS),
        )
