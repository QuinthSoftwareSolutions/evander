from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from evander.effects.providers.base import EffectProviderBase
from evander.events.models import ConfigValue, TriggerDefinition, TriggerEvent, WorkflowNodeEnvelope
from evander.queries.providers.base import QueryProviderBase
from evander.triggers.providers.base import TriggerProviderBase
from evander.workflow.catalog import WorkflowEditorFieldDefinition, WorkflowEditorNodeCatalogEntry
from evander.workflow.contracts import register_node_output_schema
from evander.workflow.plugins import ProviderPluginRegistration

from .mcp import DESCRIPTIONS
from .models import OPERATIONS, READ_OPERATIONS, OperationInput
from .runtime import provider_service


# Required arguments and available optional inputs are shared by schema and editor.
FIELDS = {
    "list_boards": ([], []),
    "get_canvas_format": ([], ["format_name"]),
    "read_canvas": (["board_id"], ["frame_id", "item_ids"]),
    "read_comments": (["board_id"], ["frame_id", "item_ids"]),
    "get_snapshot": (["snapshot_id"], []),
    "get_operation": (["operation_id"], []),
    "create_board": (["title", "operation_key"], []),
    "create_canvas": (["board_id", "svg", "operation_key"], ["frame_id", "source_refs"]),
    "update_canvas": (
        ["board_id", "svg", "snapshot_id", "operation_key"],
        ["frame_id", "item_ids", "source_refs", "allow_delete"],
    ),
    "reply_comment": (
        ["board_id", "comment_id", "text", "snapshot_id", "operation_key"],
        ["frame_id", "item_ids"],
    ),
    "resolve_comment": (
        ["board_id", "comment_id", "snapshot_id", "operation_key"],
        ["frame_id", "item_ids", "resolved"],
    ),
}
FIELD_DESCRIPTIONS = {
    "connection_id": "Operator-configured Miro connection available to this workflow directory.",
    "board_id": "Exact approved Miro board ID.",
    "frame_id": "Optional frame to scope this operation.",
    "item_ids": "Optional list of item IDs within the approved board.",
    "snapshot_id": "Snapshot returned by a prior read of this exact selection.",
    "operation_id": "Durable operation ID returned by a Miro write.",
    "operation_key": "Stable business operation key; reuse on retries with identical arguments.",
    "svg": "Editable Miro canvas SVG authored using the current canvas-format query.",
    "title": "Title of the new board.",
    "text": "Reply text for an existing thread.",
    "comment_id": "Thread ID from the source comment snapshot.",
    "source_refs": "Repository commits, task references, or artifact references explaining provenance.",
    "allow_delete": "Explicitly permit deletion of omitted items within the source snapshot.",
    "resolved": "True resolves the thread; false reopens it.",
    "format_name": "Optional provider-advertised format to load, such as diagram guidance.",
}


def node_type(operation: str) -> str:
    return (
        ("query" if operation in READ_OPERATIONS else "effect")
        + ":miro:"
        + operation.replace("_", "-")
    )


class Definition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    type: str
    connection_id: ConfigValue
    outputs: dict[str, Any] | None = None


DEFINITIONS = {}
for _operation in OPERATIONS:
    _required, _optional = FIELDS[_operation]
    _fields = {key: (ConfigValue, ...) for key in _required}
    for _key in _optional:
        _field = OperationInput.model_fields[_key]
        _fields[_key] = (
            ConfigValue,
            Field(default_factory=_field.default_factory)
            if _field.default_factory
            else _field.default,
        )
    DEFINITIONS[node_type(_operation)] = create_model(
        "Miro" + "".join(part.title() for part in _operation.split("_")) + "Definition",
        __base__=Definition,
        **_fields,
    )


class ChangedDefinition(TriggerDefinition):
    type: Literal["trigger:miro:board-changed", "trigger:miro:comments-changed"]
    connection_id: str
    board_id: str
    frame_id: str | None = None
    item_ids: list[str] = Field(default_factory=list)
    poll_interval_seconds: int = Field(default=900, ge=60)
    include_self: bool = False
    emit_initial: bool = False


def materialize(node: WorkflowNodeEnvelope):
    if node.type.startswith("trigger:"):
        return ChangedDefinition.model_validate({**node.model_dump(), **node.config})
    return DEFINITIONS[node.type].model_validate(
        {**node.config, "id": node.id, "type": node.type, "outputs": node.outputs}
    )


class OperationMixin:
    operation: str

    @classmethod
    def validate_definition(cls, definition):
        return materialize(definition) if type(definition) is WorkflowNodeEnvelope else definition

    def execute(self, *, resolved_config=None) -> dict:
        definition = getattr(self, "query_definition", None) or getattr(
            self, "effect_definition", None
        )
        payload = definition.model_dump(exclude={"id", "type", "outputs"})
        payload.update(resolved_config or {})
        service = provider_service(self.runtime_context, node_id=definition.id)
        result = asyncio.run(service.execute(self.operation, payload))
        return {"result": result, **result}


class ChangedProvider(TriggerProviderBase):
    @classmethod
    def validate_definition(cls, definition):
        return materialize(definition) if type(definition) is WorkflowNodeEnvelope else definition

    def poll(self) -> list[TriggerEvent]:
        definition = self.trigger_definition
        service = provider_service(self.runtime_context, node_id=definition.id)
        arguments = {
            key: getattr(definition, key)
            for key in ("connection_id", "board_id", "frame_id", "item_ids")
        }
        events = asyncio.run(
            service.poll(
                definition.id,
                arguments,
                kind="comments" if "comments-changed" in definition.type else "canvas",
                interval=definition.poll_interval_seconds,
                include_self=definition.include_self,
                emit_initial=definition.emit_initial,
            )
        )
        return [
            TriggerEvent(
                event_id=event["event_id"],
                trigger_id=definition.id,
                trigger_type=definition.type,
                occurred_at=datetime.fromtimestamp(event["observed_at"], UTC),
                payload=event,
            )
            for event in events
        ]

    def acknowledge_published_event(self, event: TriggerEvent) -> None:
        service = provider_service(self.runtime_context, node_id=self.trigger_definition.id)
        asyncio.run(service.acknowledge(event.payload["subscription_key"], event.event_id))


def field(key: str, required=False, *, bindable=True):
    kind = (
        "json"
        if key in {"source_refs", "item_ids"}
        else "boolean"
        if key in {"resolved", "allow_delete"}
        else "string"
    )
    return WorkflowEditorFieldDefinition(
        key=key,
        label=key.replace("_", " ").capitalize(),
        kind=kind,
        required=required,
        bindable=bindable,
        multiline=key in {"svg", "text"},
        description=FIELD_DESCRIPTIONS[key],
    )


def register_providers() -> ProviderPluginRegistration:
    queries, effects, catalog = {}, {}, []
    resolvers = {}
    for operation in OPERATIONS:
        node = node_type(operation)
        base = QueryProviderBase if operation in READ_OPERATIONS else EffectProviderBase
        provider = type(
            "Miro" + operation.title().replace("_", "") + "Provider",
            (OperationMixin, base),
            {"operation": operation},
        )
        (queries if operation in READ_OPERATIONS else effects)[node] = provider
        resolvers[node] = materialize
        required, optional = FIELDS[operation]
        catalog.append(
            WorkflowEditorNodeCatalogEntry(
                kind="query" if operation in READ_OPERATIONS else "effect",
                type=node,
                label="Miro " + operation.replace("_", " "),
                description=DESCRIPTIONS[operation],
                fields=[field("connection_id", True)]
                + [field(key, True) for key in required]
                + [field(key) for key in optional],
            )
        )
        register_node_output_schema(
            node_type=node,
            outputs={
                "result": {"type": "object"},
                "snapshot_id": {"type": "string"},
                "operation_id": {"type": "string"},
                "status": {"type": "string"},
                "url": {"type": "string"},
                "data": {"type": "object"},
                "boards": {"type": "array"},
                "result_snapshot_id": {"type": "string"},
            },
        )
    triggers = {
        "trigger:miro:board-changed": ChangedProvider,
        "trigger:miro:comments-changed": ChangedProvider,
    }
    for node in triggers:
        resolvers[node] = materialize
        catalog.append(
            WorkflowEditorNodeCatalogEntry(
                kind="trigger",
                type=node,
                label="Miro " + node.rsplit(":", 1)[1].replace("-", " "),
                description="Poll an approved selection for observed changes; routing and agent wakes remain workflow-owned.",
                fields=[
                    field("connection_id", True, bindable=False),
                    field("board_id", True, bindable=False),
                    field("frame_id", bindable=False),
                    field("item_ids", bindable=False),
                    WorkflowEditorFieldDefinition(
                        key="poll_interval_seconds",
                        label="Polling interval",
                        kind="number",
                        default_value=900,
                        description="Seconds between observations; minimum 60. Every page consumes a Miro call.",
                    ),
                    WorkflowEditorFieldDefinition(
                        key="include_self",
                        label="Include Evander changes",
                        kind="boolean",
                        default_value=False,
                        description="Include observed results of Evander writes; use workflow guards to prevent loops.",
                    ),
                    WorkflowEditorFieldDefinition(
                        key="emit_initial",
                        label="Emit initial observation",
                        kind="boolean",
                        default_value=False,
                        description="Emit the first snapshot instead of silently establishing a baseline.",
                    ),
                ],
            )
        )
        register_node_output_schema(
            node_type=node,
            outputs={
                key: {"type": "string"}
                for key in (
                    "connection_id",
                    "board_id",
                    "snapshot_id",
                    "previous_snapshot_id",
                    "url",
                    "origin",
                    "kind",
                )
            },
        )
    return ProviderPluginRegistration(
        query_providers=queries,
        effect_providers=effects,
        trigger_providers=triggers,
        node_definition_resolvers=resolvers,
        workflow_editor_catalog_entries=catalog,
    )
