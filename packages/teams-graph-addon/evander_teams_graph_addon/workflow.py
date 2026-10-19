"""Deterministic Teams edit effect; discovery never resolves provider credentials."""

from __future__ import annotations

from typing import Any, Literal
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict

from evander.effects.providers.base import EffectProviderBase, scoped_effect_directory_path
from evander.events.models import ConfigValue
from evander.workflow.catalog import WorkflowEditorFieldDefinition, WorkflowEditorNodeCatalogEntry
from evander.workflow.contracts import register_node_output_schema
from evander.workflow.plugins import ProviderPluginRegistration

from .commands import TeamsMessageCommands


class TeamsUpdateMessageDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    display_name: str
    type: Literal["effect:teams:update-message"]
    session_id: ConfigValue
    channel_target: ConfigValue
    message_id: ConfigValue
    message: ConfigValue
    thread_id: ConfigValue = None
    expected_revision: ConfigValue = None
    outputs: dict[str, Any] | None = None


def materialize(node):
    return TeamsUpdateMessageDefinition.model_validate(
        {
            "id": node.id,
            "display_name": node.display_name,
            "type": node.type,
            "outputs": node.outputs,
            **node.config,
        }
    )


class TeamsUpdateMessageEffect(EffectProviderBase):
    @classmethod
    def validate_definition(cls, node):
        return materialize(node)

    def execute(self, *, resolved_config=None, execution_context=None):
        context = self.runtime_context
        if context is None or context.admin_service is None:
            raise RuntimeError("Teams update effect requires host runtime services")
        runtime = context.admin_service.runtime
        payload = self.effect_definition.model_dump()
        payload.update(dict(resolved_config or {}))
        workflow = {
            key: str(value)
            for key, value in context.metadata.items()
            if key
            in {
                "directory_path",
                "graph_id",
                "workflow_run_id",
                "execution_id",
                "correlation_id",
                "node_id",
            }
        }
        workflow["node_id"] = self.effect_definition.id
        if execution_context is not None:
            for key in ("workflow_run_id", "execution_id", "correlation_id"):
                value = (
                    execution_context.get(key)
                    if isinstance(execution_context, Mapping)
                    else getattr(execution_context, key, None)
                )
                if value is not None:
                    workflow[key] = str(value)
        return TeamsMessageCommands(runtime, workflow_context=workflow).update_message(
            **{
                key: payload[key]
                for key in (
                    "session_id",
                    "channel_target",
                    "message_id",
                    "message",
                    "thread_id",
                    "expected_revision",
                )
            },
            authorize_session=lambda session: scoped_effect_directory_path(
                context, session.directory_path
            ),
        )


def register_providers():
    kind = "effect:teams:update-message"
    register_node_output_schema(
        node_type=kind,
        outputs={
            "success": {"type": "boolean"},
            "status": {"type": "string", "enum": ["updated", "unchanged", "stubbed"]},
            "session_id": {"type": "string"},
            "channel_target": {"type": "string"},
            "message_id": {"type": "string"},
            "thread_id": {"type": ["string", "null"]},
            "message_revision": {"type": "integer", "minimum": 1},
            "web_url": {"type": ["string", "null"]},
        },
    )
    entry = WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type=kind,
        label="Update Teams message",
        description="Replace text in a message sent by an active agent session. Preserves existing rich content and requires its edit grant and target capability.",
        fields=[
            WorkflowEditorFieldDefinition(
                key=key,
                label=key.replace("_", " ").title(),
                kind="number" if key == "expected_revision" else "string",
                multiline=key == "message",
                required=key in {"session_id", "channel_target", "message_id", "message"},
                bindable=True,
            )
            for key in (
                "session_id",
                "channel_target",
                "message_id",
                "message",
                "thread_id",
                "expected_revision",
            )
        ],
    )
    return ProviderPluginRegistration(
        effect_providers={kind: TeamsUpdateMessageEffect},
        node_definition_resolvers={kind: materialize},
        workflow_editor_catalog_entries=(entry,),
    )
