from datetime import UTC, datetime
import time
from typing import Literal

from pydantic import Field

from evander.effects.providers.base import EffectProviderBase
from evander.events.models import ConfigValue, TriggerEvent, WorkflowNodeEnvelope
from evander.triggers.providers.base import TriggerProviderBase
from evander.workflow.catalog import WorkflowEditorFieldDefinition, WorkflowEditorNodeCatalogEntry
from evander.workflow.contracts import register_node_output_schema
from evander.workflow.plugins import ProviderPluginRegistration

from .models import Contract, TERMINAL
from .service import Service
from .state import Store

TRIGGER = "trigger:test-fixtures:pending"
EFFECT = "effect:test-fixtures:reconcile"


class Pending(Contract):
    id: str
    display_name: str
    type: Literal["trigger:test-fixtures:pending"]
    interval_seconds: int = Field(default=30, ge=5, le=60)
    outputs: dict | None = None


class Reconcile(Contract):
    id: str
    display_name: str
    type: Literal["effect:test-fixtures:reconcile"]
    lease_id: ConfigValue
    outputs: dict | None = None


def materialize(node, model):
    return (
        model.model_validate(
            dict(
                id=node.id,
                display_name=node.display_name,
                type=node.type,
                outputs=node.outputs,
                **node.config,
            )
        )
        if isinstance(node, WorkflowNodeEnvelope)
        else node
    )


def directory(context):
    from evander.models import DirectoryPath

    return DirectoryPath(segments=context.metadata.get("directory_path", "root")).as_posix()


class PendingProvider(TriggerProviderBase):
    @classmethod
    def validate_definition(cls, definition):
        return materialize(definition, Pending)

    def poll(self):
        context = self.runtime_context
        definition = self.trigger_definition
        slot = int(time.time() // definition.interval_seconds)
        leases = Store(
            context.resolved_config.runtime_dir / "addons" / "test-fixtures-addon"
        ).list()
        return [
            TriggerEvent(
                event_id=f"{definition.id}:{lease['lease_id']}:{slot}",
                trigger_id=definition.id,
                trigger_type=TRIGGER,
                occurred_at=datetime.fromtimestamp(slot * definition.interval_seconds, UTC),
                payload={"lease_id": lease["lease_id"]},
            )
            for lease in leases
            if lease["state"] not in TERMINAL and lease["scope"]["directory"] == directory(context)
        ]


class ReconcileProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, definition):
        return materialize(definition, Reconcile)

    def execute(self, *, resolved_config=None):
        context = self.runtime_context
        payload = self.effect_definition.model_dump()
        payload.update(resolved_config or {})
        service = Service(context.resolved_config.runtime_dir, context.admin_service)
        lease = service.store.get(payload["lease_id"])
        if lease["scope"]["directory"] != directory(context):
            raise ValueError("Fixture lease is outside the workflow directory")
        result = service.reconcile(payload["lease_id"])
        return {"result": result, "lease_id": result["lease_id"], "state": result["state"]}


def register_providers():
    register_node_output_schema(node_type=TRIGGER, outputs={"lease_id": {"type": "string"}})
    register_node_output_schema(
        node_type=EFFECT,
        outputs={
            "result": {"type": "object"},
            "lease_id": {"type": "string"},
            "state": {"type": "string"},
        },
    )
    return ProviderPluginRegistration(
        trigger_providers={TRIGGER: PendingProvider},
        effect_providers={EFFECT: ReconcileProvider},
        node_definition_resolvers={
            TRIGGER: lambda node: materialize(node, Pending),
            EFFECT: lambda node: materialize(node, Reconcile),
        },
        workflow_editor_catalog_entries=[
            WorkflowEditorNodeCatalogEntry(
                kind="trigger",
                type=TRIGGER,
                label="Test fixtures pending",
                description="Reconcile task fixture readiness, renewal and cleanup, including while allocation is disabled.",
                fields=[],
            ),
            WorkflowEditorNodeCatalogEntry(
                kind="effect",
                type=EFFECT,
                label="Reconcile test fixture lease",
                description="Revalidate task ownership, reconcile worker resources and issue a task-bound readiness continuation.",
                fields=[
                    WorkflowEditorFieldDefinition(
                        key="lease_id", label="Lease ID", kind="text", required=True, bindable=True
                    )
                ],
            ),
        ],
    )
