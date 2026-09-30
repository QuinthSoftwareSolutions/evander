from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import time
from typing import Any, Literal

from pydantic import Field

from evander.effects.providers.base import EffectProviderBase
from evander.events.models import ConfigValue, TriggerEvent, WorkflowNodeEnvelope
from evander.models import DirectoryPath
from evander.queries.providers.base import QueryProviderBase
from evander.triggers.providers.base import TriggerProviderBase
from evander.workflow.catalog import WorkflowEditorFieldDefinition, WorkflowEditorNodeCatalogEntry
from evander.workflow.contracts import register_node_output_schema
from evander.workflow.plugins import ProviderPluginRegistration

from .models import Contract, WordPressError, owns_browser
from .service import Actor, Service
from .state import Store


class OperationDefinition(Contract):
    id: str
    display_name: str
    type: Literal["effect:wordpress:execute-change", "query:wordpress:change"]
    site_id: ConfigValue
    change_id: ConfigValue
    outputs: dict[str, Any] | None = None


class ReadyDefinition(Contract):
    id: str
    display_name: str
    type: Literal["trigger:wordpress:change-ready"]
    site_id: str | None = None
    retry_interval_seconds: int = Field(default=60, ge=30, le=3600)
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


def scope(context):
    if context is None:
        raise WordPressError("WordPress providers require runtime context")
    return DirectoryPath(segments=str(context.metadata.get("directory_path") or "root")).as_posix()


def background_service(context, change):
    directory = scope(context)
    admin = getattr(context, "admin_service", None)
    if not admin or not getattr(admin, "connector_service", None):
        raise WordPressError("WordPress providers require the composed admin runtime")
    host = admin.connector_service
    actor = Actor(directory, change["actor"], change.get("operator", False))

    def authorize():
        if actor.operator:
            return
        store = Store(context.resolved_config.runtime_dir)
        site = store.settings(directory)[1].sites[change["site_id"]]
        preparing = change["state"] == "prepared" and not change.get("ready")
        if preparing or owns_browser(change):
            for request in store.preparation_requests(change):
                host.authorize_current(
                    actor.session_id, site.connector_id, "prepare_change", request
                )
            if preparing:
                return
        host.authorize_current(
            actor.session_id,
            site.connector_id,
            "apply_change",
            change.get("publication_request")
            or {
                "site_id": change["site_id"],
                "change_id": change["change_id"],
                "expected_revision": change["revision"],
                "idempotency_key": "worker:" + change["change_id"],
            },
        )

    return Service(context.resolved_config.runtime_dir, actor, host, authorize=authorize)


class _Operation:
    @classmethod
    def validate_definition(cls, definition):
        return (
            materialize(definition, OperationDefinition)
            if isinstance(definition, WorkflowNodeEnvelope)
            else definition
        )

    def arguments(self, resolved):
        definition = getattr(self, "effect_definition", None) or getattr(
            self, "query_definition", None
        )
        payload = definition.model_dump()
        payload.update(resolved or {})
        if not isinstance(payload.get("site_id"), str) or not isinstance(
            payload.get("change_id"), str
        ):
            raise WordPressError("Resolved site_id and change_id must be strings")
        return payload


class WordPressEffectProvider(_Operation, EffectProviderBase):
    def execute(self, *, resolved_config=None):
        payload = self.arguments(resolved_config)
        change = Store(self.runtime_context.resolved_config.runtime_dir).get(
            payload["change_id"], scope(self.runtime_context), payload["site_id"]
        )
        result = asyncio.run(
            background_service(self.runtime_context, change).execute(
                payload["site_id"], payload["change_id"]
            )
        )
        return {"result": result, **result}


class WordPressQueryProvider(_Operation, QueryProviderBase):
    def execute(self, *, resolved_config=None):
        payload = self.arguments(resolved_config)
        result = Service.receipt(
            Store(self.runtime_context.resolved_config.runtime_dir).get(
                payload["change_id"], scope(self.runtime_context), payload["site_id"]
            )
        )
        return {"result": result, **result}


class WordPressReadyProvider(TriggerProviderBase):
    @classmethod
    def validate_definition(cls, definition):
        return (
            materialize(definition, ReadyDefinition)
            if isinstance(definition, WorkflowNodeEnvelope)
            else definition
        )

    def poll(self):
        definition = self.trigger_definition
        store = Store(self.runtime_context.resolved_config.runtime_dir)
        now = time.time()
        slot = int(now // definition.retry_interval_seconds)
        result = []
        admin = getattr(self.runtime_context, "admin_service", None)
        host = getattr(admin, "connector_service", None)
        managed = getattr(host, "host_services", {}).get("managed_git")

        def release(change, pin):
            managed().release_retained_snapshot(
                pin["managed_repo_id"],
                change["directory"],
                binding_kind="wordpress",
                binding_id=change["change_id"],
                pin_ref=pin["pin_ref"],
                expected_commit_sha=pin["commit_sha"],
            )

        settings = store.settings(scope(self.runtime_context))[1]
        retention = {site_id: site.retention_days for site_id, site in settings.sites.items()}
        store.prune(
            scope(self.runtime_context),
            retention,
            release_pin=release if callable(managed) else None,
        )
        for change in store.list(scope(self.runtime_context), definition.site_id, pending=True):
            # Prepared browser sessions also need regular authorization checks.
            # The store excludes inactive drafts before selecting pending work.
            # A durable slot ID suppresses duplicates within a poll interval. Pending
            # operations receive another event after a worker crash or unknown result.
            result.append(
                TriggerEvent(
                    event_id=f"{definition.id}:{change['change_id']}:{slot}",
                    trigger_id=definition.id,
                    trigger_type=definition.type,
                    occurred_at=datetime.fromtimestamp(
                        slot * definition.retry_interval_seconds, UTC
                    ),
                    payload={
                        "site_id": change["site_id"],
                        "change_id": change["change_id"],
                        "revision": change["revision"],
                        "state": change["state"],
                    },
                )
            )
        return result


def register_providers():
    entries = []
    for kind, type_name, label, model in [
        ("trigger", "trigger:wordpress:change-ready", "Website changes ready", ReadyDefinition),
        (
            "effect",
            "effect:wordpress:execute-change",
            "Publish or recover a website change",
            OperationDefinition,
        ),
        ("query", "query:wordpress:change", "Website change receipt", OperationDefinition),
    ]:
        register_node_output_schema(
            node_type=type_name,
            outputs={
                "payload": {"type": "object"},
                "event_id": {"type": "string"},
                "occurred_at": {"type": "string"},
            }
            if kind == "trigger"
            else {
                "result": {"type": "object"},
                "change_id": {"type": "string"},
                "site_id": {"type": "string"},
                "state": {"type": "string"},
                "revision": {"type": "integer"},
                "undo_id": {"type": "string"},
            },
        )
        entries.append(
            WorkflowEditorNodeCatalogEntry(
                kind=kind,
                type=type_name,
                label=label,
                description="Directory-scoped prepared website changes with durable verification and recovery.",
                fields=[
                    WorkflowEditorFieldDefinition(
                        key="site_id",
                        label="Site ID",
                        kind="text",
                        required=kind != "trigger",
                        bindable=kind != "trigger",
                    )
                ]
                + (
                    [
                        WorkflowEditorFieldDefinition(
                            key="change_id",
                            label="Change ID",
                            kind="text",
                            required=True,
                            bindable=True,
                        )
                    ]
                    if kind != "trigger"
                    else []
                ),
            )
        )
    return ProviderPluginRegistration(
        trigger_providers={"trigger:wordpress:change-ready": WordPressReadyProvider},
        effect_providers={"effect:wordpress:execute-change": WordPressEffectProvider},
        query_providers={"query:wordpress:change": WordPressQueryProvider},
        node_definition_resolvers={
            "trigger:wordpress:change-ready": lambda node: materialize(node, ReadyDefinition),
            "effect:wordpress:execute-change": lambda node: materialize(node, OperationDefinition),
            "query:wordpress:change": lambda node: materialize(node, OperationDefinition),
        },
        workflow_editor_catalog_entries=entries,
    )
