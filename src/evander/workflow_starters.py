"""Provider-independent starter building blocks. Outputs remain ordinary workflows."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from evander.addons import AddonContext
from evander.cron import normalize_cron_timezone
from evander.integration_contracts import IntegrationStarterVerification, IntegrationWorkflowStarter
from evander.workflow.packs import WorkflowPack


STARTER_FIELDS = {
    "name": {
        "type": "string",
        "title": "Workflow name",
        "minLength": 1,
        "maxLength": 60,
        "pattern": "^[a-z][a-z0-9-]*$",
        "default": "daily-task",
        "description": "A unique name, using lowercase letters, numbers and hyphens.",
    },
    "session_id": {
        "type": "string",
        "title": "Agent",
        "minLength": 1,
        "format": "evander-session",
        "description": "Uses this agent's existing tools and permissions.",
    },
}


def starter_node(node_id: str, node_type: str, label: str, **config: Any) -> dict[str, Any]:
    return {"node": {"id": node_id, "type": node_type, "display_name": label, "config": config}}


def starter_pack(
    context: AddonContext,
    values: Mapping[str, Any],
    nodes: list[dict[str, Any]],
    edges: list[dict[str, str]],
    *,
    description: str,
) -> WorkflowPack:
    pack_id = f"starter-{values['name']}"
    return WorkflowPack.model_validate(
        {
            "pack_id": pack_id,
            "pack_version": "v1",
            "description": description,
            "graphs": [
                {
                    "id": pack_id,
                    "name": values["name"],
                    "directory_path": context.directory_path,
                    "node_ids": [node["node"]["id"] for node in nodes],
                }
            ],
            "ir": {"version": "v1", "nodes": nodes, "edges": edges},
        }
    )


def completion_node(prefix: str) -> dict[str, Any]:
    return starter_node(
        f"{prefix}-completed",
        "effect:event:emit",
        "Record completed task",
        event_type="workflow.starter.completed",
        payload={"workflow": prefix},
    )


def _scheduled(context: AddonContext, values: Mapping[str, Any]) -> WorkflowPack:
    prefix = f"starter-{values['name']}"
    timezone = normalize_cron_timezone(values["timezone"])
    hour, minute = values["time"].split(":")
    expression = {
        "hourly": "0 * * * *",
        "daily": f"{int(minute)} {int(hour)} * * *",
        "weekdays": f"{int(minute)} {int(hour)} * * 1-5",
    }[values["schedule"]]
    if "${{" in values["prompt"]:
        raise ValueError(
            "Use plain task instructions here. Workflow expressions can be added in the editor."
        )
    nodes = [
        starter_node(
            f"{prefix}-schedule",
            "trigger:cron",
            "Scheduled task",
            expression=expression,
            timezone=timezone,
        ),
        starter_node(
            f"{prefix}-test",
            "trigger:event",
            "Run task now",
            event_type=f"starter.{values['name']}.test",
        ),
        starter_node(f"{prefix}-start", "gate:merge", "Scheduled or manual start"),
        starter_node(
            f"{prefix}-task",
            "effect:agent:enqueue-prompt",
            "Run agent task",
            session_id=values["session_id"],
            prompt=values["prompt"],
            resume_mode="new_context",
        ),
        completion_node(prefix),
    ]
    edges = [
        {"source_id": f"{prefix}-{source}", "target_id": f"{prefix}-start"}
        for source in ("schedule", "test")
    ] + [
        {"source_id": f"{prefix}-start", "target_id": f"{prefix}-task"},
        {
            "source_id": f"{prefix}-task",
            "source_port": "completed",
            "target_id": f"{prefix}-completed",
        },
    ]
    return starter_pack(
        context, values, nodes, edges, description="Run an agent task on a schedule."
    )


def _scheduled_verification(
    context: AddonContext, values: Mapping[str, Any]
) -> IntegrationStarterVerification:
    prefix = f"starter-{values['name']}"
    return IntegrationStarterVerification(
        trigger_node_ids=(f"{prefix}-schedule", f"{prefix}-test"),
        completion_node_id=f"{prefix}-completed",
        manual_trigger_node_id=f"{prefix}-test",
        instructions="Run the task once now, or wait for its schedule. Verification waits for the agent to finish. Review the result in the run report.",
    )


SCHEDULED_STARTER = IntegrationWorkflowStarter(
    id="scheduled-task",
    label="Run an agent task on a schedule",
    description="Schedule a report, a check, or any recurring task using an existing agent.",
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "session_id", "schedule", "time", "timezone", "prompt"],
        "properties": {
            **STARTER_FIELDS,
            "schedule": {
                "type": "string",
                "title": "Repeat",
                "enum": ["daily", "weekdays", "hourly"],
                "default": "daily",
            },
            "time": {
                "type": "string",
                "title": "Time (daily or weekdays)",
                "pattern": "^([01][0-9]|2[0-3]):[0-5][0-9]$",
                "default": "09:00",
            },
            "timezone": {
                "type": "string",
                "title": "Time zone",
                "minLength": 1,
                "default": "UTC",
                "description": "An IANA time zone, such as Europe/Stockholm.",
            },
            "prompt": {
                "type": "string",
                "title": "Task instructions",
                "minLength": 1,
                "maxLength": 16000,
                "format": "textarea",
            },
        },
    },
    build=_scheduled,
    verification=_scheduled_verification,
)
