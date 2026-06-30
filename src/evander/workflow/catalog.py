from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

WorkflowNodeKind = Literal["trigger", "gate", "effect", "query"]


class WorkflowEditorFieldOption(BaseModel):
    value: str
    label: str
    description: str | None = Field(default=None, exclude_if=lambda value: value is None)
    metadata: dict[str, Any] = Field(default_factory=dict, exclude_if=lambda value: not value)


class WorkflowInternalEventCatalogEntry(BaseModel):
    event_type: str = Field(min_length=1)
    label: str = Field(min_length=1)
    description: str | None = Field(default=None, exclude_if=lambda value: value is None)
    source: str | None = Field(default=None, exclude_if=lambda value: value is None)
    metadata: dict[str, Any] = Field(default_factory=dict, exclude_if=lambda value: not value)

    @field_validator("event_type", "label", "description", "source", mode="before")
    @classmethod
    def normalize_text(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class WorkflowEditorFieldDefinition(BaseModel):
    key: str
    label: str
    kind: str
    required: bool = False
    bindable: bool = False
    read_only: bool = False
    multiline: bool = False
    description: str | None = None
    placeholder: str | None = None
    json_value_type: str | None = None
    default_value: Any = None
    options: list[WorkflowEditorFieldOption] = Field(default_factory=list)
    item_fields: list["WorkflowEditorFieldDefinition"] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict, exclude_if=lambda value: not value)


class WorkflowEditorNodePortDefinition(BaseModel):
    key: str
    label: str
    description: str | None = None


class WorkflowEditorNodeCatalogEntry(BaseModel):
    kind: WorkflowNodeKind
    type: str
    label: str
    description: str | None = None
    fields: list[WorkflowEditorFieldDefinition] = Field(default_factory=list)
    input_ports: list[WorkflowEditorNodePortDefinition] = Field(default_factory=list)
    output_ports: list[WorkflowEditorNodePortDefinition] = Field(default_factory=list)


class WorkflowEditorCatalogPayload(BaseModel):
    worker_id: str = "trigger-orchestrator"
    nodes: list[WorkflowEditorNodeCatalogEntry]
    internal_events: list[WorkflowInternalEventCatalogEntry] = Field(default_factory=list)
