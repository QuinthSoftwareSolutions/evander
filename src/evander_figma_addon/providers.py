from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from evander.effects.providers.base import EffectProviderBase
from evander.events.models import ConfigValue, TriggerEvent, WorkflowNodeEnvelope
from evander.models import DirectoryPath
from evander.policy import PolicyError
from evander.queries.providers.base import QueryProviderBase
from evander.triggers.providers.base import TriggerProviderBase
from evander.workflow.catalog import WorkflowEditorFieldDefinition, WorkflowEditorNodeCatalogEntry
from evander.workflow.contracts import register_node_output_schema
from evander.workflow.plugins import ProviderPluginRegistration

from .mcp import QUERY_OPERATIONS, session_directory
from .models import DesignRequest, FigmaError, digest
from .service import Actor, FigmaService


class OperationDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    display_name: str
    type: Literal["query:figma:read", "effect:figma:execute"]
    session_id: ConfigValue
    request: ConfigValue
    outputs: dict[str, Any] | None = None


class ChangeDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    display_name: str
    type: Literal["trigger:figma:changed"]
    target_id: str = Field(min_length=1)
    origin: Literal["external", "evander", "any"] = "external"
    kinds: list[
        Literal["design.changed", "comment.created", "snapshot.created", "version.created"]
    ] = Field(default_factory=lambda: ["comment.created", "version.created"])
    poll_external: bool = False
    poll_interval_seconds: int = Field(default=300, ge=60, le=86400)
    outputs: dict[str, Any] | None = None


def materialize(node, model):
    return model.model_validate(
        {
            "id": node.id,
            "display_name": node.display_name,
            "type": node.type,
            "outputs": node.outputs,
            **node.config,
        }
    )


def workflow_service(context, session_id=None, *, tool_name="figma_execute", polling=False):
    if context is None:
        raise FigmaError("Figma provider requires runtime context")
    directory = DirectoryPath(
        segments=str(context.metadata.get("directory_path") or "root")
    ).as_posix()
    runtime_dir = context.resolved_config.runtime_dir
    if polling:
        return FigmaService(runtime_dir, Actor(directory, "figma-poller", operator=True))
    if not isinstance(session_id, str) or not session_id:
        raise FigmaError("Figma workflow operations require an explicit session_id")
    admin = getattr(context, "admin_service", None)
    runtime = getattr(admin, "runtime", None) or admin
    policy = getattr(runtime, "policy", None)
    if not callable(getattr(policy, "require_tool", None)):
        raise FigmaError("Figma workflow operations require runtime session tool policy")
    try:
        session = policy.require_tool(session_id, tool_name)
    except PolicyError as exc:
        raise FigmaError(str(exc)) from None
    if session_directory(session) != directory:
        raise FigmaError("Session must belong to the workflow's Figma directory")
    return FigmaService(
        runtime_dir,
        Actor(directory, session_id, str(context.metadata.get("workflow_trace_id") or "") or None),
    )


class _Operation:
    @classmethod
    def validate_definition(cls, definition):
        return (
            materialize(definition, OperationDefinition)
            if isinstance(definition, WorkflowNodeEnvelope)
            else definition
        )

    def _execute(self, resolved_config, *, read_only):
        definition = getattr(self, "effect_definition", None) or getattr(
            self, "query_definition", None
        )
        payload = (
            dict(definition.config)
            if isinstance(definition, WorkflowNodeEnvelope)
            else definition.model_dump()
        )
        payload.update(resolved_config or {})
        request = DesignRequest.model_validate(payload["request"])
        if read_only and request.operation not in QUERY_OPERATIONS:
            raise FigmaError("Figma queries cannot perform effects")
        result = workflow_service(
            self.runtime_context,
            payload.get("session_id"),
            tool_name="figma_query" if read_only else "figma_execute",
        ).execute(request)
        if "status" in result and result["status"] != "succeeded":
            raise FigmaError(
                f"Figma operation {result['operation_id']} needs attention ({result['status']})"
            )
        return {"result": result, **result}


class FigmaQueryProvider(_Operation, QueryProviderBase):
    def execute(self, *, resolved_config=None):
        return self._execute(resolved_config, read_only=True)


class FigmaEffectProvider(_Operation, EffectProviderBase):
    def execute(self, *, resolved_config=None):
        return self._execute(resolved_config, read_only=False)


class FigmaChangedProvider(TriggerProviderBase):
    @classmethod
    def validate_definition(cls, definition):
        return (
            materialize(definition, ChangeDefinition)
            if isinstance(definition, WorkflowNodeEnvelope)
            else definition
        )

    def _context(self):
        definition = self.trigger_definition
        service = workflow_service(self.runtime_context, polling=True)
        key = digest([service.actor.directory, definition.id, definition.target_id])
        return definition, service, key

    def poll(self):
        definition, service, key = self._context()
        target, _, _, _ = service._target(definition.target_id, "read")
        if definition.poll_external:
            service.poll_external(definition.target_id, definition.poll_interval_seconds)
        events = []
        for row in service.store.events(key, service.actor.directory, definition.target_id):
            payload = row["payload"]
            if (
                payload.get("file_key") != target.file_key
                or payload["kind"] not in definition.kinds
                or (definition.origin != "any" and payload["origin"] != definition.origin)
            ):
                service.store.acknowledge(key, row["seq"])
                continue
            events.append(
                TriggerEvent(
                    event_id=f"{definition.id}:{row['id']}",
                    trigger_id=definition.id,
                    trigger_type=definition.type,
                    occurred_at=datetime.fromisoformat(payload["created_at"]),
                    payload={"target_id": definition.target_id, "sequence": row["seq"], **payload},
                )
            )
        return events

    def acknowledge_published_event(self, event):
        definition, service, key = self._context()
        if event.trigger_id == definition.id and event.trigger_type == definition.type:
            service.store.acknowledge(key, event.payload["sequence"])


def register_providers():
    outputs = {
        "result": {"type": "object"},
        "operation_id": {"type": "string"},
        "status": {"type": "string"},
        "node_id": {"type": "string"},
        "revision": {"type": "string"},
        "nodes": {"type": "array"},
        "pages": {"type": "array"},
        "comments": {"type": "array"},
        "operations": {"type": "array"},
        "snapshots": {"type": "array"},
        "variables_and_styles": {},
        "artifacts": {"type": "array"},
        "snapshot": {"type": "object"},
        "next_step": {"type": "string"},
    }
    for node_type in ("query:figma:read", "effect:figma:execute"):
        register_node_output_schema(node_type=node_type, outputs=outputs)
    register_node_output_schema(
        node_type="trigger:figma:changed",
        outputs={
            "event_id": {"type": "string"},
            "payload": {"type": "object"},
            "occurred_at": {"type": "string"},
        },
    )
    models = {
        "query:figma:read": OperationDefinition,
        "effect:figma:execute": OperationDefinition,
        "trigger:figma:changed": ChangeDefinition,
    }
    catalogs = []
    for kind, type_name, label in [
        ("query", "query:figma:read", "Read Figma"),
        ("effect", "effect:figma:execute", "Work on a Figma design"),
    ]:
        catalogs.append(
            WorkflowEditorNodeCatalogEntry(
                kind=kind,
                type=type_name,
                label=label,
                description="Figma file operations using the named session's normal tool permissions.",
                fields=[
                    WorkflowEditorFieldDefinition(
                        key="session_id", label="Session", kind="text", required=True, bindable=True
                    ),
                    WorkflowEditorFieldDefinition(
                        key="request",
                        label="Design request",
                        kind="json",
                        required=True,
                        bindable=True,
                    ),
                ],
            )
        )
    catalogs.append(
        WorkflowEditorNodeCatalogEntry(
            kind="trigger",
            type="trigger:figma:changed",
            label="Figma activity",
            description="Durable design events and optional external comment/version polling.",
            fields=[
                WorkflowEditorFieldDefinition(
                    key="target_id", label="Figma target", kind="text", required=True
                ),
                WorkflowEditorFieldDefinition(
                    key="origin", label="Origin", kind="text", default_value="external"
                ),
                WorkflowEditorFieldDefinition(key="kinds", label="Event kinds", kind="json"),
                WorkflowEditorFieldDefinition(
                    key="poll_external", label="Poll Figma", kind="boolean", default_value=False
                ),
                WorkflowEditorFieldDefinition(
                    key="poll_interval_seconds",
                    label="Poll interval (seconds)",
                    kind="number",
                    default_value=300,
                ),
            ],
        )
    )
    return ProviderPluginRegistration(
        query_providers={"query:figma:read": FigmaQueryProvider},
        effect_providers={"effect:figma:execute": FigmaEffectProvider},
        trigger_providers={"trigger:figma:changed": FigmaChangedProvider},
        node_definition_resolvers={
            key: lambda node, model=model: materialize(node, model) for key, model in models.items()
        },
        workflow_editor_catalog_entries=catalogs,
    )
