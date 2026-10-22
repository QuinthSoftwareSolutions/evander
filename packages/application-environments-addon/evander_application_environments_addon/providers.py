"""Workflow nodes share the MCP/Admin domain and directory checks."""

from typing import Any, Literal

from evander.effects.providers.base import EffectProviderBase
from evander.queries.providers.base import QueryProviderBase
from evander.events.models import ConfigValue, WorkflowNodeEnvelope
from evander.models import DirectoryPath
from evander.workflow.catalog import WorkflowEditorFieldDefinition, WorkflowEditorNodeCatalogEntry
from evander.workflow.contracts import register_node_output_schema
from evander.workflow.plugins import ProviderPluginRegistration

from .models import Contract, CreateRequest
from evander.resources.models import ResourceError
from .service import Service


class Definition(Contract):
    id: str
    display_name: str
    type: Literal[
        "effect:application-environment:ensure",
        "effect:application-environment:close",
        "query:application-environment:status",
    ]
    agent_session_id: ConfigValue
    environment_id: ConfigValue | None = None
    request: dict[str, Any] | None = None
    outputs: dict[str, Any] | None = None


def materialize(node):
    return Definition.model_validate(
        {
            "id": node.id,
            "display_name": node.display_name,
            "type": node.type,
            "outputs": node.outputs,
            **node.config,
        }
    )


class Operation:
    @classmethod
    def validate_definition(cls, definition):
        return (
            materialize(definition) if isinstance(definition, WorkflowNodeEnvelope) else definition
        )

    def execute(self, *, resolved_config=None):
        definition = getattr(self, "effect_definition", None) or self.query_definition
        payload = {**definition.model_dump(), **(resolved_config or {})}
        context = self.runtime_context
        service = Service(context.resolved_config.runtime_dir, context.admin_service)
        directory = DirectoryPath(
            segments=str(context.metadata.get("directory_path") or "root")
        ).as_posix()
        parent = service.parent(payload["agent_session_id"])
        if parent.directory != directory:
            raise ResourceError("Owning agent is outside the workflow directory")
        if definition.type == "effect:application-environment:ensure":
            result = service.create(
                parent.session_id, CreateRequest.model_validate(payload["request"]), tool=None
            )
        else:
            record = service.store.get(payload["environment_id"], directory)
            from types import SimpleNamespace

            session = SimpleNamespace(**record["session"])
            if session.agent_session_id != parent.session_id:
                raise ResourceError("Application belongs to another agent")
            result = (
                service.close(session.id, directory=directory)
                if definition.type.endswith(":close")
                else service.status(parent.session_id, session.id, tool=None)
            )
        return {"result": result, **result}


class ApplicationEffect(Operation, EffectProviderBase):
    pass


class ApplicationQuery(Operation, QueryProviderBase):
    pass


def register_providers():
    entries = []
    for kind, name in [
        ("effect", "effect:application-environment:ensure"),
        ("effect", "effect:application-environment:close"),
        ("query", "query:application-environment:status"),
    ]:
        register_node_output_schema(
            node_type=name,
            outputs={
                "result": {"type": "object"},
                "id": {"type": "string"},
                "state": {"type": "string"},
                "endpoints": {"type": "array", "items": {"type": "object"}},
                "expires_at": {"type": "number"},
            },
        )
        entries.append(
            WorkflowEditorNodeCatalogEntry(
                kind=kind,
                type=name,
                label={
                    "ensure": "Start shared application",
                    "close": "Close shared application",
                    "status": "Shared application status",
                }[name.rsplit(":", 1)[-1]],
                description="Directory-scoped application sessions on the owning agent's worker.",
                fields=[
                    WorkflowEditorFieldDefinition(
                        key="agent_session_id",
                        label="Agent session",
                        kind="text",
                        required=True,
                        bindable=True,
                    ),
                    WorkflowEditorFieldDefinition(
                        key="environment_id",
                        label="Application session",
                        kind="text",
                        bindable=True,
                    ),
                    WorkflowEditorFieldDefinition(
                        key="request", label="Launch request", kind="json", bindable=True
                    ),
                ],
            )
        )
    return ProviderPluginRegistration(
        effect_providers={
            "effect:application-environment:ensure": ApplicationEffect,
            "effect:application-environment:close": ApplicationEffect,
        },
        query_providers={"query:application-environment:status": ApplicationQuery},
        node_definition_resolvers={
            name: materialize
            for name in (
                "effect:application-environment:ensure",
                "effect:application-environment:close",
                "query:application-environment:status",
            )
        },
        workflow_editor_catalog_entries=entries,
    )
