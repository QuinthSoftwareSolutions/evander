from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

WorkflowNodeKind = Literal["trigger", "gate", "effect", "query"]


class WorkflowEditorFieldOption(BaseModel):
    value: str
    label: str
    description: str | None = Field(default=None, exclude_if=lambda value: value is None)
    metadata: dict[str, Any] = Field(default_factory=dict, exclude_if=lambda value: not value)


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
