"""Ordinary workflow providers over the same resource domain services as MCP."""

from datetime import UTC, datetime
import time

from pydantic import create_model

from evander.effects.providers.base import EffectProviderBase
from evander.events.models import ConfigValue, TriggerEvent, WorkflowNodeEnvelope
from evander.queries.providers.base import QueryProviderBase
from evander.triggers.providers.base import TriggerProviderBase
from evander.workflow.catalog import WorkflowEditorFieldDefinition, WorkflowEditorNodeCatalogEntry
from evander.workflow.contracts import register_node_output_schema
from evander.workflow.plugins import ProviderPluginRegistration

from .models import Contract, Owner, ResourceError, TERMINAL
from .service import fingerprint


def register_providers(service_factory, prefix, read_operations, write_operations):
    query_type, effect_type, trigger_type = (
        f"query:{prefix}:read",
        f"effect:{prefix}:operate",
        f"trigger:{prefix}:pending",
    )
    Definition = create_model(
        prefix.title() + "ResourceNode",
        __base__=Contract,
        id=(str, ...),
        display_name=(str, ...),
        type=(str, ...),
        outputs=(dict | None, None),
        operation=(ConfigValue, "discover"),
        arguments=(ConfigValue, {}),
        connection_id=(ConfigValue, ""),
        request_id=(ConfigValue, ""),
    )
    Pending = create_model(
        prefix.title() + "PendingNode",
        __base__=Contract,
        id=(str, ...),
        display_name=(str, ...),
        type=(str, ...),
        outputs=(dict | None, None),
    )

    def materialize(node, model):
        return (
            model.model_validate(
                {
                    "id": node.id,
                    "display_name": node.display_name,
                    "type": node.type,
                    "outputs": node.outputs,
                    **node.config,
                }
            )
            if isinstance(node, WorkflowNodeEnvelope)
            else node
        )

    def directory(context):
        from evander.models import DirectoryPath

        return DirectoryPath(segments=context.metadata.get("directory_path", "root")).as_posix()

    def service(context):
        from evander.addons import AddonContext

        return service_factory(
            AddonContext(
                addon_id=prefix + "-addon",
                runtime_dir=context.resolved_config.runtime_dir,
                addon_runtime_dir=context.resolved_config.runtime_dir
                / "addons"
                / (prefix + "-addon"),
                service=context.admin_service,
                directory_path=directory(context),
            )
        )

    def execute(provider, payload, execution_context, read):
        context = provider.runtime_context
        domain = service(context)
        operation = payload["operation"]
        allowed = (
            {"discover", "get", *read_operations}
            if read
            else {"acquire", "reset", "release", "reconcile", "control", *write_operations}
        )
        if operation not in allowed:
            raise ResourceError("Operation does not match the workflow node's capability")
        arguments = payload.get("arguments") or {}
        _, settings = domain.store.settings(directory(context))
        if operation == "reconcile":
            lease = domain.store.get(arguments["lease_id"])
            if lease.owner.directory != directory(context):
                raise ResourceError("Lease is outside the workflow directory")
            return {"result": domain.reconcile(lease.lease_id)}
        if operation not in settings.workflow_operations:
            raise ResourceError("Workflow operation is not enabled in this directory")
        execution_context = execution_context or {}
        trace = execution_context.get("trace_id") or execution_context.get("workflow_run_id")
        graph_id = execution_context.get("graph_id") or context.metadata.get("graph_id")
        if not trace or not graph_id:
            raise ResourceError("Resource workflow requires a trusted graph and execution trace")
        owner = Owner(
            provider="workflow",
            key=f"{graph_id}:{trace}",
            directory=directory(context),
            reference={"graph_id": graph_id},
            valid_until=time.time() + 7200,
        )
        node = getattr(provider, "query_definition", None) or getattr(
            provider, "effect_definition", None
        )
        request_id = payload.get("request_id") or fingerprint(
            ["workflow", execution_context.get("idempotency_key") or [graph_id, trace, node.id]]
        )
        if operation == "discover":
            value = domain.discover(owner)
        elif operation == "acquire":
            value = domain.acquire(owner, arguments["profile_id"], request_id)
        elif operation == "get":
            value = domain.get(owner, arguments["lease_id"])
        elif operation in {"reset", "release", "control"}:
            arguments = dict(arguments)
            lease_id = arguments.pop("lease_id")
            value = domain.queue(owner, lease_id, operation, arguments, request_id=request_id)
        else:
            if "lease_id" in arguments:
                arguments = dict(arguments)
                lease_id = arguments.pop("lease_id")
                value = domain.lease_call(owner, lease_id, operation, arguments, request_id)
            else:
                value = domain.call(
                    owner, payload["connection_id"], operation, arguments, request_id
                )
        return {"result": value}

    class Read(QueryProviderBase):
        @classmethod
        def validate_definition(cls, node):
            return materialize(node, Definition)

        def execute(self, *, resolved_config=None, execution_context=None):
            return execute(
                self,
                {**self.query_definition.model_dump(), **(resolved_config or {})},
                execution_context,
                True,
            )

    class Operate(EffectProviderBase):
        @classmethod
        def validate_definition(cls, node):
            return materialize(node, Definition)

        def execute(self, *, resolved_config=None, execution_context=None):
            return execute(
                self,
                {**self.effect_definition.model_dump(), **(resolved_config or {})},
                execution_context,
                False,
            )

    class Poll(TriggerProviderBase):
        @classmethod
        def validate_definition(cls, node):
            return materialize(node, Pending)

        def poll(self):
            domain = service(self.runtime_context)
            slot = int(time.time() // 30)
            return [
                TriggerEvent(
                    event_id=f"{self.trigger_definition.id}:{lease.lease_id}:{slot}",
                    trigger_id=self.trigger_definition.id,
                    trigger_type=trigger_type,
                    occurred_at=datetime.fromtimestamp(slot * 30, UTC),
                    payload={"lease_id": lease.lease_id},
                )
                for lease in domain.store.leases(active_only=True)
                if lease.state not in TERMINAL
                and lease.owner.directory == directory(self.runtime_context)
            ]

    for node_type in (query_type, effect_type):
        register_node_output_schema(node_type=node_type, outputs={"result": {"type": "object"}})
    register_node_output_schema(node_type=trigger_type, outputs={"lease_id": {"type": "string"}})
    fields = [
        WorkflowEditorFieldDefinition(
            key=k,
            label=k.replace("_", " ").title(),
            kind="json" if k == "arguments" else "text",
            required=k in {"operation", "arguments"},
            bindable=True,
        )
        for k in ("operation", "connection_id", "arguments", "request_id")
    ]
    return ProviderPluginRegistration(
        query_providers={query_type: Read},
        effect_providers={effect_type: Operate},
        trigger_providers={trigger_type: Poll},
        node_definition_resolvers={
            query_type: lambda node: materialize(node, Definition),
            effect_type: lambda node: materialize(node, Definition),
            trigger_type: lambda node: materialize(node, Pending),
        },
        workflow_editor_catalog_entries=[
            WorkflowEditorNodeCatalogEntry(
                kind="query", type=query_type, label=prefix + " read", fields=fields
            ),
            WorkflowEditorNodeCatalogEntry(
                kind="effect", type=effect_type, label=prefix + " operate", fields=fields
            ),
            WorkflowEditorNodeCatalogEntry(
                kind="trigger", type=trigger_type, label=prefix + " pending leases", fields=[]
            ),
        ],
    )
