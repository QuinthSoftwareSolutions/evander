"""Workflow nodes share the MCP/Admin domain and directory checks."""

from typing import Any, Literal

from evander.effects.providers.base import EffectProviderBase
from evander.queries.providers.base import QueryProviderBase
from evander.events.models import ConfigValue, WorkflowNodeEnvelope
from evander.models import DirectoryPath
from evander.workflow.catalog import WorkflowEditorFieldDefinition, WorkflowEditorNodeCatalogEntry
from evander.workflow.contracts import register_node_output_schema
from evander.workflow.plugins import ProviderPluginRegistration

from .models import BrowserError, Contract, CreateRequest
from .service import Service


class Definition(Contract):
    id: str
    display_name: str
    type: Literal[
        "effect:browser-session:ensure",
        "effect:browser-session:close",
        "query:browser-session:status",
    ]
    agent_session_id: ConfigValue
    browser_session_id: ConfigValue | None = None
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
        parent = service.bindings.parent(payload["agent_session_id"])
        if parent.directory != directory:
            raise BrowserError("Owning agent is outside the workflow directory", "forbidden", 403)
        if definition.type == "effect:browser-session:ensure":
            result = service.create(
                parent.session_id, CreateRequest.model_validate(payload["request"]), tool=None
            )
        else:
            session = service.store.get(payload["browser_session_id"], directory)
            if session.agent_session_id != parent.session_id:
                raise BrowserError("Browser belongs to another agent", "forbidden", 403)
            result = (
                service.close(session.id, "workflow")
                if definition.type.endswith(":close")
                else service.receipt(session)
            )
        return {"result": result, **result}


class BrowserEffect(Operation, EffectProviderBase):
    pass


class BrowserQuery(Operation, QueryProviderBase):
    pass


def register_providers():
    entries = []
    for kind, name in [
        ("effect", "effect:browser-session:ensure"),
        ("effect", "effect:browser-session:close"),
        ("query", "query:browser-session:status"),
    ]:
        register_node_output_schema(
            node_type=name,
            outputs={
                "result": {"type": "object"},
                "id": {"type": "string"},
                "state": {"type": "string"},
                "viewer_url": {"type": ["string", "null"]},
                "revision": {"type": "integer"},
            },
        )
        entries.append(
            WorkflowEditorNodeCatalogEntry(
                kind=kind,
                type=name,
                label={
                    "ensure": "Start shared browser",
                    "close": "Close shared browser",
                    "status": "Shared browser status",
                }[name.rsplit(":", 1)[-1]],
                description="Directory-scoped browser sessions on the owning agent's worker.",
                fields=[
                    WorkflowEditorFieldDefinition(
                        key="agent_session_id",
                        label="Agent session",
                        kind="text",
                        required=True,
                        bindable=True,
                    ),
                    WorkflowEditorFieldDefinition(
                        key="browser_session_id",
                        label="Browser session",
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
            "effect:browser-session:ensure": BrowserEffect,
            "effect:browser-session:close": BrowserEffect,
        },
        query_providers={"query:browser-session:status": BrowserQuery},
        node_definition_resolvers={
            name: materialize
            for name in (
                "effect:browser-session:ensure",
                "effect:browser-session:close",
                "query:browser-session:status",
            )
        },
        workflow_editor_catalog_entries=entries,
    )
