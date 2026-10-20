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
from .models import OPERATIONS, READ_OPERATIONS, CanvasScope, OperationInput
from .runtime import provider_service

# Required arguments and available optional inputs are shared by schema and editor.
FIELDS = {
    "list_boards": ([], []),
    "get_canvas_format": ([], ["step", "format_name", "notation", "is_repository"]),
    "search_canvas": (["board_id"], ["result_mode", "patterns", "target_id", "cursor"]),
    "read_canvas": (["board_id"], ["frame_id", "item_ids", "canvas_scope"]),
    "read_comments": (["board_id"], ["frame_id", "item_ids"]),
    "get_snapshot": (["snapshot_id"], []),
    "get_operation": (["operation_id"], []),
    "create_board": (["title", "operation_key"], []),
    "create_canvas": (
        ["board_id", "svg", "operation_key"],
        ["frame_id", "canvas_scope", "source_refs"],
    ),
    "update_canvas": (
        ["board_id", "svg", "snapshot_id", "operation_key"],
        ["frame_id", "item_ids", "canvas_scope", "source_refs", "allow_delete"],
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
    "session_id": "Existing agent session whose board grants authorize this operation.",
    "board_id": "Exact approved Miro board ID.",
    "frame_id": "Optional frame to scope this operation.",
    "item_ids": "Optional list of item IDs within the approved board.",
    "canvas_scope": "Canvas area {x,y,width,height} from search, with positive integer dimensions. Choose this or frame/item IDs; reuse the exact selection for updates. For creation, cover the new content.",
    "result_mode": "overview for board navigation; areas or matches with patterns for search results. Overviews can be sampled and are not full-board snapshots.",
    "patterns": "Up to 50 search patterns, each up to 500 characters; required for areas and matches.",
    "target_id": "Optional container or region ID to narrow areas/matches searches.",
    "cursor": "Optional next_cursor from a previous areas/matches search page.",
    "snapshot_id": "Snapshot returned by a prior read of this exact selection.",
    "operation_id": "Durable operation ID returned by a Miro write.",
    "operation_key": "Stable business operation key; reuse on retries with identical arguments.",
    "svg": "Miro canvas SVG authored using the current canvas-format query. Collaborative diagrams use separate editable shapes, text and connectors; Mermaid widgets require an explicit request for a code-based diagram.",
    "title": "Title of the new board.",
    "text": "Reply text for an existing thread.",
    "comment_id": "Thread ID from the source comment snapshot.",
    "source_refs": "Repository commits, task references, or artifact references explaining provenance.",
    "allow_delete": 'Permit deleting snapshot items explicitly marked data-deleted="true"; omitted items remain unchanged.',
    "resolved": "True resolves the thread; false reopens it.",
    "step": "Canvas-composer workflow step returned by the previous guidance call: design, edit, or dsl.",
    "format_name": "Provider-advertised composition format. Use diagramming for diagrams.",
    "notation": "Use free_form for individually editable collaborative diagrams, including flowcharts (default for diagramming when supported). Other notations: flowchart, entity_relationship, uml_class, uml_sequence; use these for explicitly requested code-based diagrams.",
    "is_repository": "Whether the agent is working from a source-control repository.",
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
    session_id: ConfigValue
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
    session_id: str
    board_id: str
    frame_id: str | None = None
    item_ids: list[str] = Field(default_factory=list)
    canvas_scope: CanvasScope | None = None
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
        session_id = payload.pop("session_id")
        service = provider_service(
            self.runtime_context, node_id=definition.id, session_id=session_id
        )
        result = asyncio.run(service.execute(self.operation, payload))
        return {"result": result, **result}


class ChangedProvider(TriggerProviderBase):
    @classmethod
    def validate_definition(cls, definition):
        return materialize(definition) if type(definition) is WorkflowNodeEnvelope else definition

    def poll(self) -> list[TriggerEvent]:
        definition = self.trigger_definition
        service = provider_service(
            self.runtime_context, node_id=definition.id, session_id=definition.session_id
        )
        arguments = {
            key: getattr(definition, key)
            for key in ("connection_id", "board_id", "frame_id", "item_ids")
        }
        if definition.canvas_scope is not None:
            arguments["canvas_scope"] = definition.canvas_scope.model_dump()
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
        service = provider_service(
            self.runtime_context,
            node_id=self.trigger_definition.id,
            session_id=self.trigger_definition.session_id,
        )
        asyncio.run(service.acknowledge(event.payload["subscription_key"], event.event_id))


def field(key: str, required=False, *, bindable=True):
    kind = (
        "json"
        if key in {"source_refs", "item_ids", "canvas_scope", "patterns"}
        else "number"
        if key == "cursor"
        else "boolean"
        if key in {"resolved", "allow_delete", "is_repository"}
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
                fields=[field("connection_id", True), field("session_id", True)]
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
                "scope": {"type": "object"},
                "boards": {"type": "array"},
                "result_snapshot_id": {"type": "string"},
                "layout_review": {"type": "object"},
                **(
                    {
                        "format": {"type": "object"},
                        "format_specific": {"type": "object"},
                        "authoring_checks": {"type": "array", "items": {"type": "string"}},
                    }
                    if operation == "get_canvas_format"
                    else {}
                ),
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
                    field("session_id", True, bindable=False),
                    field("board_id", True, bindable=False),
                    field("frame_id", bindable=False),
                    field("item_ids", bindable=False),
                    *(
                        [field("canvas_scope", bindable=False)]
                        if node.endswith("board-changed")
                        else []
                    ),
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
            }
            | {"scope": {"type": "object"}},
        )
    from .access_workflow import register_access_nodes

    register_access_nodes(queries, effects, catalog, resolvers)
    return ProviderPluginRegistration(
        query_providers=queries,
        effect_providers=effects,
        trigger_providers=triggers,
        node_definition_resolvers=resolvers,
        workflow_editor_catalog_entries=catalog,
    )
