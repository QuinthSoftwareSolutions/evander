"""Deterministic Teams edit effect; discovery never resolves provider credentials."""

from __future__ import annotations

from typing import Any, Literal
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict

from evander.effects.providers.base import EffectProviderBase, scoped_effect_directory_path
from evander.events.models import ConfigValue
from evander.models import ToolName
from evander.workflow.catalog import (
    WorkflowEditorFieldDefinition,
    WorkflowEditorNodeCatalogEntry,
    WorkflowInternalEventCatalogEntry,
)
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
    image_artifact_id: ConfigValue = None
    clear_image: ConfigValue = False
    request_id: ConfigValue = None
    outputs: dict[str, Any] | None = None


def materialize(node):
    models = {
        "effect:teams:update-message": TeamsUpdateMessageDefinition,
        "effect:teams:post-file": TeamsPostFileDefinition,
        "effect:teams:render-progress-image": TeamsRenderProgressImageDefinition,
        "effect:teams:sync-progress-message": TeamsSyncProgressMessageDefinition,
    }
    return models[node.type].model_validate(
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
                    "image_artifact_id",
                    "clear_image",
                    "request_id",
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
            "image_sha256": {"type": ["string", "null"]},
            "image_expires_at": {"type": ["string", "null"]},
            "image_renew_at": {"type": ["string", "null"]},
        },
    )
    entry = WorkflowEditorNodeCatalogEntry(
        kind="effect",
        type=kind,
        label="Update Teams message",
        description="Edit session-owned text and an opt-in progress PNG. Image publication additionally requires file-send permission and approval; preserves other rich content.",
        fields=[
            WorkflowEditorFieldDefinition(
                key=key,
                label=key.replace("_", " ").title(),
                kind="number"
                if key == "expected_revision"
                else "boolean"
                if key == "clear_image"
                else "string",
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
                "image_artifact_id",
                "clear_image",
                "request_id",
            )
        ],
    )
    providers = {kind: TeamsUpdateMessageEffect}
    entries = [entry]
    for effect_type, provider, model, label, description, required in (
        (
            "effect:teams:post-file",
            TeamsPostFileEffect,
            TeamsPostFileDefinition,
            "Post Teams files",
            "Post files with a real owning session and durable creation key.",
            {"session_id", "channel_target", "message", "artifact_ids", "idempotency_key"},
        ),
        (
            "effect:teams:render-progress-image",
            TeamsRenderProgressImageEffect,
            TeamsRenderProgressImageDefinition,
            "Render progress image",
            "Stage a deterministic PNG and accessible status without inference.",
            {"session_id", "snapshot"},
        ),
        (
            "effect:teams:sync-progress-message",
            TeamsSyncProgressMessageEffect,
            TeamsSyncProgressMessageDefinition,
            "Sync Teams progress",
            "Opt-in, durable single-message progress, coalescing and expiry renewal. Notification failures stay isolated.",
            {"session_id", "channel_target"},
        ),
    ):
        providers[effect_type] = provider
        outputs = (
            {
                "artifact_id": {"type": "string"},
                "sha256": {"type": "string"},
                "expires_at": {"type": "string"},
                "status_text": {"type": "string"},
            }
            if provider is TeamsRenderProgressImageEffect
            else {
                "status": {"type": "string"},
                "message_id": {"type": ["string", "null"]},
                "message_revision": {"type": ["integer", "null"]},
                "image_renew_at": {"type": ["string", "null"]},
                "image_expires_at": {"type": ["string", "null"]},
            }
        )
        if provider is TeamsPostFileEffect:
            outputs = {
                **outputs,
                "provider": {"type": "string"},
                "artifact_ids": {"type": "array", "items": {"type": "string"}},
                "published_links": {"type": "array", "items": {"type": "object"}},
                "result": {"type": "object"},
                "image_sha256": {"type": ["string", "null"]},
                "image_editable": {"type": "boolean"},
            }
        elif provider is TeamsSyncProgressMessageEffect:
            outputs = {
                **outputs,
                "source_version": {"type": ["string", "null"]},
                "generation_id": {"type": "string"},
                "phase": {"type": ["string", "null"]},
                "error": {"type": ["string", "null"]},
            }
        register_node_output_schema(node_type=effect_type, outputs=outputs)
        entries.append(
            WorkflowEditorNodeCatalogEntry(
                kind="effect",
                type=effect_type,
                label=label,
                description=description,
                fields=[
                    WorkflowEditorFieldDefinition(
                        key=key,
                        label=key.replace("_", " ").title(),
                        bindable=True,
                        kind="json"
                        if key in {"artifact_ids", "snapshot", "delivery"}
                        else "boolean"
                        if key == "enabled"
                        else "number"
                        if key == "minimum_interval_seconds"
                        else "string",
                        required=key in required,
                        multiline=key == "message",
                        default_value=False
                        if key == "enabled"
                        else 30
                        if key == "minimum_interval_seconds"
                        else None,
                    )
                    for key in model.model_fields
                    if key not in {"id", "type", "display_name", "outputs"}
                ],
            )
        )
    return ProviderPluginRegistration(
        effect_providers=providers,
        node_definition_resolvers={key: materialize for key in providers},
        workflow_editor_catalog_entries=tuple(entries),
        internal_event_catalog_entries=tuple(
            WorkflowInternalEventCatalogEntry(
                event_type="internal.teams.progress-image." + action,
                label="Renew progress image" if action == "renew" else "Flush progress updates",
                source="teams-graph",
                schema_revision="1",
                description="Notification-only timer for a session-owned progress message; contains identifiers, never image bearer URLs.",
                payload_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "session_id",
                        "channel_target",
                        "generation_id",
                        "outline_sha256",
                        "action",
                    ],
                    "properties": {
                        **{
                            key: {"type": "string"}
                            for key in (
                                "session_id",
                                "channel_target",
                                "generation_id",
                                "outline_sha256",
                                "directory_path",
                            )
                        },
                        "thread_id": {"type": ["string", "null"]},
                        "source_version": {"type": ["string", "null"]},
                        "action": {"type": "string", "enum": [action]},
                    },
                },
            )
            for action in ("renew", "flush")
        ),
    )


class TeamsPostFileDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    display_name: str
    type: Literal["effect:teams:post-file"]
    session_id: ConfigValue
    channel_target: ConfigValue
    message: ConfigValue
    artifact_ids: ConfigValue
    idempotency_key: ConfigValue
    thread_id: ConfigValue = None
    request_id: ConfigValue = None
    editable_image_artifact_id: ConfigValue = None
    outputs: dict[str, Any] | None = None


class TeamsRenderProgressImageDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    display_name: str
    type: Literal["effect:teams:render-progress-image"]
    session_id: ConfigValue
    snapshot: ConfigValue
    outputs: dict[str, Any] | None = None


class TeamsSyncProgressMessageDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    display_name: str
    type: Literal["effect:teams:sync-progress-message"]
    session_id: ConfigValue
    channel_target: ConfigValue
    enabled: ConfigValue = False
    snapshot: ConfigValue = None
    thread_id: ConfigValue = None
    generation_id: ConfigValue = None
    outline_sha256: ConfigValue = None
    source_version: ConfigValue = None
    action: ConfigValue = "update"
    delivery: ConfigValue = None
    request_id: ConfigValue = None
    minimum_interval_seconds: ConfigValue = 30
    outputs: dict[str, Any] | None = None


def _host(provider, resolved_config, execution_context):
    context = provider.runtime_context
    if context is None or context.admin_service is None:
        raise RuntimeError("Teams effect requires host runtime services")
    values = provider.effect_definition.model_dump(
        exclude={"id", "type", "display_name", "outputs"}
    )
    values.update(dict(resolved_config or {}))
    workflow = {
        k: v
        for k, v in context.metadata.items()
        if k
        in {
            "directory_path",
            "graph_id",
            "workflow_run_id",
            "execution_id",
            "correlation_id",
            "node_id",
        }
    }
    if execution_context is not None:
        for k in ("workflow_run_id", "execution_id", "correlation_id"):
            v = (
                execution_context.get(k)
                if isinstance(execution_context, Mapping)
                else getattr(execution_context, k, None)
            )
            if v is not None:
                workflow[k] = str(v)
    workflow["node_id"] = provider.effect_definition.id
    runtime = context.admin_service.runtime

    def authorize(session):
        return scoped_effect_directory_path(context, session.directory_path)

    return runtime, values, TeamsMessageCommands(runtime, workflow_context=workflow), authorize


class TeamsPostFileEffect(TeamsUpdateMessageEffect):
    def execute(self, *, resolved_config=None, execution_context=None):
        runtime, values, commands, authorize = _host(self, resolved_config, execution_context)
        if not isinstance(values["idempotency_key"], str) or not values["idempotency_key"].strip():
            raise ValueError("Workflow file posts require an idempotency_key")
        return commands.post_file(**values, authorize_session=authorize)


class TeamsRenderProgressImageEffect(TeamsUpdateMessageEffect):
    def execute(self, *, resolved_config=None, execution_context=None):
        from .progress_images import stage_progress_image

        runtime, values, commands, authorize = _host(self, resolved_config, execution_context)
        try:
            result = stage_progress_image(runtime, **values, authorize_session=authorize)
        except Exception as exc:
            commands._audit(
                values.get("session_id"),
                {"operation": "render_progress_image"},
                error=exc,
                tool=ToolName.TEAMS_POST_FILE,
            )
            raise
        commands._audit(
            values["session_id"],
            {"operation": "render_progress_image"},
            result=result,
            tool=ToolName.TEAMS_POST_FILE,
        )
        return result


class TeamsSyncProgressMessageEffect(TeamsUpdateMessageEffect):
    def execute(self, *, resolved_config=None, execution_context=None):
        from .progress_messages import ProgressMessageCoordinator

        runtime, values, commands, authorize = _host(self, resolved_config, execution_context)
        try:
            return ProgressMessageCoordinator(
                runtime,
                commands=commands,
                authorize_session=authorize,
                runtime_metadata=self.runtime_context.metadata,
            ).sync(**values)
        except Exception as exc:
            import re

            commands._audit(
                values.get("session_id"), {"operation": "sync_progress_message"}, error=exc
            )
            return {
                "status": "notification_failed",
                "error": re.sub(r"https?://\S+", "[redacted]", str(exc))[:500],
            }
