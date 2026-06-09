from __future__ import annotations

from typing import Any


TEAM_RUNTIME_V1: dict[str, Any] = {
    "id": "team-runtime.v1",
    "name": "Team Runtime v1",
    "description": "Optional team add-on runtime loop for persistent team agents.",
    "graphs": [
        {
            "id": "team-runtime",
            "name": "Team Runtime",
            "directory_path": "root",
            "node_ids": [
                "team-cron",
                "team-wake-due-agents",
                "team-ephemeral-requested",
                "team-create-ephemeral-agent",
                "team-mark-ephemeral-spawned",
                "team-prompt-ephemeral-agent",
                "team-agent-run-completed",
                "team-route-completed-session",
                "team-completed-persistent",
                "team-read-persistent-action",
                "team-apply-persistent-action",
                "team-completed-ephemeral",
                "team-read-ephemeral-action",
                "team-apply-ephemeral-action",
                "team-destroy-ephemeral-agent",
                "team-mark-ephemeral-cleaned-up",
            ],
        }
    ],
    "config": {
        "triggers": [
            {
                "id": "team-cron",
                "display_name": "Team runtime tick",
                "type": "trigger:cron",
                "expression": "*/15 * * * *",
            },
            {
                "id": "team-agent-run-completed",
                "display_name": "Team agent run completed",
                "type": "trigger:agent:run:completed",
            },
            {
                "id": "team-ephemeral-requested",
                "display_name": "Team ephemeral assignment requested",
                "type": "trigger:event",
                "event_type": "team.ephemeral.requested",
            },
        ],
        "gates": [
            {
                "id": "team-completed-persistent",
                "display_name": "Persistent team session completed",
                "type": "gate:filter",
                "when": {
                    "all": [
                        {
                            "equals": True,
                            "left": "${{team-route-completed-session.persistent}}",
                        }
                    ]
                },
            },
            {
                "id": "team-completed-ephemeral",
                "display_name": "Ephemeral team session completed",
                "type": "gate:filter",
                "when": {
                    "all": [
                        {
                            "equals": True,
                            "left": "${{team-route-completed-session.ephemeral}}",
                        }
                    ]
                },
            },
        ],
        "queries": [
            {
                "id": "team-route-completed-session",
                "display_name": "Route completed team session",
                "type": "query:team:session-routing",
                "session_id": "${{team-agent-run-completed.session_ids.0 ?? null}}",
            },
            {
                "id": "team-read-persistent-action",
                "display_name": "Read persistent team action artifact",
                "type": "query:team:agent-action-artifact",
                "session_id": "${{team-agent-run-completed.session_ids.0 ?? null}}",
                "path": "team-action.json",
            },
            {
                "id": "team-read-ephemeral-action",
                "display_name": "Read ephemeral team action artifact",
                "type": "query:team:agent-action-artifact",
                "session_id": "${{team-agent-run-completed.session_ids.0 ?? null}}",
                "path": "team-action.json",
            }
        ],
        "effects": [
            {
                "id": "team-wake-due-agents",
                "display_name": "Wake due team agents",
                "type": "effect:team:wake-due-agents",
                "limit": 10,
                "lease_seconds": 900,
                "wake_interval_seconds": 900,
                "max_no_progress_count": 3,
                "action_artifact_path": "team-action.json",
                "resume_mode": "same_context",
            },
            {
                "id": "team-create-ephemeral-agent",
                "display_name": "Create ephemeral team agent",
                "type": "effect:agent:create",
                "session_id": "${{team-ephemeral-requested.payload.session_id}}",
                "existing_agent_behavior": "noop",
                "execution_backend": "${{team-ephemeral-requested.payload.execution_backend ?? null}}",
                "agent_provider": "${{team-ephemeral-requested.payload.agent_provider ?? null}}",
                "model": "${{team-ephemeral-requested.payload.model ?? null}}",
                "container_image": "${{team-ephemeral-requested.payload.container_image ?? null}}",
                "ttl_hours": "${{team-ephemeral-requested.payload.ttl_hours ?? null}}",
                "directory_path": "${{team-ephemeral-requested.payload.directory_path ?? null}}",
                "metadata": "${{team-ephemeral-requested.payload.metadata}}",
            },
            {
                "id": "team-mark-ephemeral-spawned",
                "display_name": "Mark ephemeral team agent spawned",
                "type": "effect:team:mark-ephemeral-spawned",
                "assignment_id": "${{team-ephemeral-requested.payload.assignment_id}}",
                "session_id": "${{team-create-ephemeral-agent.session_id}}",
            },
            {
                "id": "team-prompt-ephemeral-agent",
                "display_name": "Prompt ephemeral team agent",
                "type": "effect:agent:prompt",
                "session_id": "${{team-ephemeral-requested.payload.session_id}}",
                "prompt": "${{team-ephemeral-requested.payload.prompt}}",
            },
            {
                "id": "team-apply-persistent-action",
                "display_name": "Apply persistent team agent action",
                "type": "effect:team:apply-agent-action",
                "session_id": "${{team-agent-run-completed.session_ids.0 ?? null}}",
                "dispatch_id": "${{team-agent-run-completed.dispatch_id ?? null}}",
                "valid": "${{team-read-persistent-action.valid}}",
                "error": "${{team-read-persistent-action.error}}",
                "action": "${{team-read-persistent-action.action}}",
            },
            {
                "id": "team-apply-ephemeral-action",
                "display_name": "Apply ephemeral team agent action",
                "type": "effect:team:apply-ephemeral-action",
                "session_id": "${{team-agent-run-completed.session_ids.0 ?? null}}",
                "valid": "${{team-read-ephemeral-action.valid}}",
                "error": "${{team-read-ephemeral-action.error}}",
                "action": "${{team-read-ephemeral-action.action}}",
            },
            {
                "id": "team-destroy-ephemeral-agent",
                "display_name": "Destroy ephemeral team agent",
                "type": "effect:agent:destroy",
                "session_id": "${{team-agent-run-completed.session_ids.0 ?? null}}",
            },
            {
                "id": "team-mark-ephemeral-cleaned-up",
                "display_name": "Mark ephemeral team agent cleaned up",
                "type": "effect:team:mark-ephemeral-cleaned-up",
                "assignment_id": "${{team-route-completed-session.assignment_id}}",
                "session_id": "${{team-agent-run-completed.session_ids.0 ?? null}}",
                "destroy_result": {
                    "success": "${{team-destroy-ephemeral-agent.success}}",
                    "message": "${{team-destroy-ephemeral-agent.message ?? null}}",
                    "status": "${{team-destroy-ephemeral-agent.status ?? null}}",
                },
            },
        ],
        "edges": [
            {"source_id": "team-cron", "target_id": "team-wake-due-agents"},
            {"source_id": "team-ephemeral-requested", "target_id": "team-create-ephemeral-agent"},
            {"source_id": "team-create-ephemeral-agent", "target_id": "team-mark-ephemeral-spawned"},
            {"source_id": "team-mark-ephemeral-spawned", "target_id": "team-prompt-ephemeral-agent"},
            {"source_id": "team-agent-run-completed", "target_id": "team-route-completed-session"},
            {"source_id": "team-route-completed-session", "target_id": "team-completed-persistent"},
            {"source_id": "team-completed-persistent", "target_id": "team-read-persistent-action"},
            {"source_id": "team-read-persistent-action", "target_id": "team-apply-persistent-action"},
            {"source_id": "team-route-completed-session", "target_id": "team-completed-ephemeral"},
            {"source_id": "team-completed-ephemeral", "target_id": "team-read-ephemeral-action"},
            {"source_id": "team-read-ephemeral-action", "target_id": "team-apply-ephemeral-action"},
            {"source_id": "team-apply-ephemeral-action", "target_id": "team-destroy-ephemeral-agent"},
            {"source_id": "team-destroy-ephemeral-agent", "target_id": "team-mark-ephemeral-cleaned-up"},
        ],
    },
}


def team_runtime_v1() -> dict[str, Any]:
    return dict(TEAM_RUNTIME_V1)
