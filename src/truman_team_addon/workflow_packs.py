from __future__ import annotations

from copy import deepcopy
from typing import Any


_TEAM_RUNTIME_V1_LEGACY: dict[str, Any] = {
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
                "team-chat-message-queued",
                "team-wake-due-agents",
                "team-wake-queued-chat-message",
                "team-wake-governance-managers",
                "team-ephemeral-requested",
                "team-create-ephemeral-agent",
                "team-mark-ephemeral-spawned",
                "team-ephemeral-spawned",
                "team-prompt-ephemeral-agent",
                "team-agent-run-completed",
                "team-route-completed-session",
                "team-completed-persistent",
                "team-finalize-persistent-dispatch",
                "team-completed-ephemeral",
                "team-destroy-ephemeral-agent",
                "team-mark-ephemeral-cleaned-up",
                "team-operator-escalation",
                "team-notify-operator",
                "team-git-promotion-requested",
                "team-git-promotion-review",
                "team-git-promote-review-branch",
                "team-record-review-promotion",
                "team-git-promotion-feature",
                "team-consolidate-feature-branches",
                "team-record-feature-promotion",
                "team-git-promotion-release",
                "team-consolidate-release-branches",
                "team-record-release-promotion",
                "team-git-release-recovery-requested",
                "team-promote-release-recovery-branch",
                "team-record-release-recovery-promotion",
                "team-git-promotion-canonical",
                "team-push-canonical-branch",
                "team-record-canonical-promotion",
                "team-mark-release-published",
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
                "id": "team-chat-message-queued",
                "display_name": "Team chat message queued",
                "type": "trigger:event",
                "event_type": "team.chat.message_queued",
            },
            {
                "id": "team-ephemeral-requested",
                "display_name": "Team ephemeral assignment requested",
                "type": "trigger:event",
                "event_type": "team.ephemeral.requested",
            },
            {
                "id": "team-operator-escalation",
                "display_name": "Team operator escalation requested",
                "type": "trigger:event",
                "event_type": "team.operator.escalation_requested",
            },
            {
                "id": "team-git-promotion-requested",
                "display_name": "Team Git promotion requested",
                "type": "trigger:event",
                "event_type": "team.git.promotion_requested",
            },
            {
                "id": "team-git-release-recovery-requested",
                "display_name": "Team Git release recovery requested",
                "type": "trigger:event",
                "event_type": "team.git.release_recovery_requested",
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
            {
                "id": "team-ephemeral-spawned",
                "display_name": "Ephemeral team agent spawned",
                "type": "gate:filter",
                "when": {
                    "all": [
                        {
                            "equals": True,
                            "left": "${{team-mark-ephemeral-spawned.success}}",
                        }
                    ]
                },
            },
            {
                "id": "team-git-promotion-review",
                "display_name": "Git promotion is a review promotion",
                "type": "gate:filter",
                "when": {
                    "all": [
                        {
                            "equals": "review",
                            "left": "${{team-git-promotion-requested.payload.promotion_kind}}",
                        }
                    ]
                },
            },
            {
                "id": "team-git-promotion-feature",
                "display_name": "Git promotion is a feature consolidation",
                "type": "gate:filter",
                "when": {
                    "all": [
                        {
                            "equals": "feature",
                            "left": "${{team-git-promotion-requested.payload.promotion_kind}}",
                        }
                    ]
                },
            },
            {
                "id": "team-git-promotion-release",
                "display_name": "Git promotion is a release consolidation",
                "type": "gate:filter",
                "when": {
                    "all": [
                        {
                            "equals": "release",
                            "left": "${{team-git-promotion-requested.payload.promotion_kind}}",
                        }
                    ]
                },
            },
            {
                "id": "team-git-promotion-canonical",
                "display_name": "Git promotion is a canonical push",
                "type": "gate:filter",
                "when": {
                    "all": [
                        {
                            "equals": "canonical_push",
                            "left": "${{team-git-promotion-requested.payload.promotion_kind}}",
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
                "dispatch_id": "${{team-agent-run-completed.dispatch_id ?? null}}",
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
                "low_priority_wake_after_cycles": 4,
                "resume_mode": "same_context",
            },
            {
                "id": "team-wake-queued-chat-message",
                "display_name": "Wake queued chat message agent",
                "type": "effect:team:wake-due-agents",
                "agent_id": "${{team-chat-message-queued.payload.agent_id}}",
                "limit": 1,
                "lease_seconds": 900,
                "wake_interval_seconds": 900,
                "max_no_progress_count": 3,
                "low_priority_wake_after_cycles": 4,
                "resume_mode": "same_context",
            },
            {
                "id": "team-wake-governance-managers",
                "display_name": "Wake governance managers",
                "type": "effect:team:wake-governance-managers",
                "limit": 10,
                "lease_seconds": 900,
                "wake_interval_seconds": 3600,
                "cost_warning_ratio": 0.8,
                "resume_mode": "same_context",
            },
            {
                "id": "team-create-ephemeral-agent",
                "display_name": "Create ephemeral team agent",
                "type": "effect:agent:create",
                "session_id": "${{team-ephemeral-requested.payload.session_id}}",
                "vm_name": "${{team-ephemeral-requested.payload.vm_name ?? team-ephemeral-requested.payload.session_id}}",
                "existing_agent_behavior": "noop",
                "execution_backend": "${{team-ephemeral-requested.payload.execution_backend}}",
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
                "create_result": {
                    "success": "${{team-create-ephemeral-agent.success}}",
                    "message": "${{team-create-ephemeral-agent.message ?? null}}",
                    "error": "${{team-create-ephemeral-agent.error ?? null}}",
                    "execution_backend": "${{team-create-ephemeral-agent.execution_backend ?? null}}",
                    "vm_name": "${{team-create-ephemeral-agent.vm_name ?? null}}",
                },
            },
            {
                "id": "team-prompt-ephemeral-agent",
                "display_name": "Prompt ephemeral team agent",
                "type": "effect:agent:prompt",
                "session_id": "${{team-ephemeral-requested.payload.session_id}}",
                "prompt": "${{team-ephemeral-requested.payload.prompt}}",
            },
            {
                "id": "team-finalize-persistent-dispatch",
                "display_name": "Finalize persistent Team dispatch",
                "type": "effect:team:finalize-agent-dispatch",
                "session_id": "${{team-route-completed-session.session_id}}",
                "dispatch_id": "${{team-route-completed-session.dispatch_id}}",
                "agent_id": "${{team-route-completed-session.agent_id ?? null}}",
            },
            {
                "id": "team-destroy-ephemeral-agent",
                "display_name": "Destroy ephemeral team agent",
                "type": "effect:agent:destroy",
                "session_id": "${{team-route-completed-session.session_id}}",
            },
            {
                "id": "team-mark-ephemeral-cleaned-up",
                "display_name": "Mark ephemeral team agent cleaned up",
                "type": "effect:team:mark-ephemeral-cleaned-up",
                "assignment_id": "${{team-route-completed-session.assignment_id}}",
                "session_id": "${{team-route-completed-session.session_id}}",
                "destroy_result": {
                    "success": "${{team-destroy-ephemeral-agent.success}}",
                    "message": "${{team-destroy-ephemeral-agent.message ?? null}}",
                    "status": "${{team-destroy-ephemeral-agent.status ?? null}}",
                },
            },
            {
                "id": "team-notify-operator",
                "display_name": "Notify operator of escalation",
                "type": "effect:communication:send-message",
                "communication_target_id": (
                    "${{team-operator-escalation.payload.communication_target_id}}"
                ),
                "message": (
                    "[Escalation: ${{team-operator-escalation.payload.urgency}}] "
                    "${{team-operator-escalation.payload.title}}\n\n"
                    "${{team-operator-escalation.payload.message}}"
                ),
            },
            {
                "id": "team-git-promote-review-branch",
                "display_name": "Publish Team review branch",
                "type": "effect:team:publish-review-branch",
                "host_repo_path": "${{team-git-promotion-requested.payload.host_repo_path}}",
                "bare_repo_name": (
                    "${{team-git-promotion-requested.payload.bare_repo_name ?? null}}"
                ),
                "bare_repo_path": (
                    "${{team-git-promotion-requested.payload.bare_repo_path ?? null}}"
                ),
                "source_branch": "${{team-git-promotion-requested.payload.source_branch ?? null}}",
                "commit_sha": "${{team-git-promotion-requested.payload.commit_sha ?? null}}",
                "target_branch": "${{team-git-promotion-requested.payload.target_branch}}",
            },
            {
                "id": "team-record-review-promotion",
                "display_name": "Record review promotion result",
                "type": "effect:team:record-git-promotion",
                "promotion_id": "${{team-git-promotion-requested.payload.promotion_id}}",
                "success": "${{team-git-promote-review-branch.success}}",
                "commit_sha": "${{team-git-promote-review-branch.commit_sha ?? null}}",
                "error": "${{team-git-promote-review-branch.error ?? null}}",
                "result_outputs": {
                    "review_branch": (
                        "${{team-git-promote-review-branch.review_branch ?? null}}"
                    ),
                    "host_repo_path": (
                        "${{team-git-promote-review-branch.host_repo_path ?? null}}"
                    ),
                    "message": "${{team-git-promote-review-branch.message ?? null}}",
                },
            },
            {
                "id": "team-consolidate-feature-branches",
                "display_name": "Consolidate review branches into feature branch",
                "type": "effect:team:consolidate-git-branches",
                "host_repo_path": "${{team-git-promotion-requested.payload.host_repo_path}}",
                "source_branches": "${{team-git-promotion-requested.payload.source_branches}}",
                "target_branch": "${{team-git-promotion-requested.payload.target_branch}}",
                "base_branch": "${{team-git-promotion-requested.payload.base_branch ?? null}}",
            },
            {
                "id": "team-record-feature-promotion",
                "display_name": "Record feature promotion result",
                "type": "effect:team:record-git-promotion",
                "promotion_id": "${{team-git-promotion-requested.payload.promotion_id}}",
                "success": "${{team-consolidate-feature-branches.success}}",
                "commit_sha": "${{team-consolidate-feature-branches.commit_sha ?? null}}",
                "error": "${{team-consolidate-feature-branches.error ?? null}}",
                "result_outputs": {
                    "merged_branches": (
                        "${{team-consolidate-feature-branches.merged_branches ?? null}}"
                    ),
                    "conflict_branch": (
                        "${{team-consolidate-feature-branches.conflict_branch ?? null}}"
                    ),
                    "output": "${{team-consolidate-feature-branches.output ?? null}}",
                },
            },
            {
                "id": "team-consolidate-release-branches",
                "display_name": "Consolidate feature branches into release branch",
                "type": "effect:team:consolidate-git-branches",
                "host_repo_path": "${{team-git-promotion-requested.payload.host_repo_path}}",
                "source_branches": "${{team-git-promotion-requested.payload.source_branches}}",
                "target_branch": "${{team-git-promotion-requested.payload.target_branch}}",
                "base_branch": "${{team-git-promotion-requested.payload.base_branch ?? null}}",
            },
            {
                "id": "team-record-release-promotion",
                "display_name": "Record release promotion result",
                "type": "effect:team:record-git-promotion",
                "promotion_id": "${{team-git-promotion-requested.payload.promotion_id}}",
                "success": "${{team-consolidate-release-branches.success}}",
                "commit_sha": "${{team-consolidate-release-branches.commit_sha ?? null}}",
                "error": "${{team-consolidate-release-branches.error ?? null}}",
                "result_outputs": {
                    "merged_branches": (
                        "${{team-consolidate-release-branches.merged_branches ?? null}}"
                    ),
                    "conflict_branch": (
                        "${{team-consolidate-release-branches.conflict_branch ?? null}}"
                    ),
                    "output": "${{team-consolidate-release-branches.output ?? null}}",
                },
            },
            {
                "id": "team-promote-release-recovery-branch",
                "display_name": "Promote resolved release recovery branch",
                "type": "effect:git:promote-agent-branch",
                "host_repo_path": (
                    "${{team-git-release-recovery-requested.payload.host_repo_path}}"
                ),
                "bare_repo_name": (
                    "${{team-git-release-recovery-requested.payload.bare_repo_name ?? null}}"
                ),
                "bare_repo_path": (
                    "${{team-git-release-recovery-requested.payload.bare_repo_path ?? null}}"
                ),
                "agent_branch": (
                    "${{team-git-release-recovery-requested.payload.agent_branch}}"
                ),
                "target_branch": (
                    "${{team-git-release-recovery-requested.payload.target_branch}}"
                ),
                "expected_commit_sha": (
                    "${{team-git-release-recovery-requested.payload.expected_commit_sha ?? null}}"
                ),
            },
            {
                "id": "team-record-release-recovery-promotion",
                "display_name": "Record release recovery promotion result",
                "type": "effect:team:record-git-promotion",
                "promotion_id": (
                    "${{team-git-release-recovery-requested.payload.promotion_id}}"
                ),
                "success": "${{team-promote-release-recovery-branch.success}}",
                "commit_sha": (
                    "${{team-promote-release-recovery-branch.commit_sha ?? null}}"
                ),
                "error": "${{team-promote-release-recovery-branch.error ?? null}}",
                "result_outputs": {
                    "agent_branch": (
                        "${{team-promote-release-recovery-branch.agent_branch ?? null}}"
                    ),
                    "target_branch": (
                        "${{team-promote-release-recovery-branch.target_branch ?? null}}"
                    ),
                    "message": "${{team-promote-release-recovery-branch.message ?? null}}",
                },
            },
            {
                "id": "team-push-canonical-branch",
                "display_name": "Push release branch to canonical remote",
                "type": "effect:git:push-branch",
                "host_repo_path": "${{team-git-promotion-requested.payload.host_repo_path}}",
                "local_branch": "${{team-git-promotion-requested.payload.source_branch}}",
                "origin_branch": "${{team-git-promotion-requested.payload.target_branch}}",
                "canonical_origin_url": (
                    "${{team-git-promotion-requested.payload.canonical_origin_url ?? null}}"
                ),
                "provider": "${{team-git-promotion-requested.payload.provider ?? null}}",
                "base_branch": "${{team-git-promotion-requested.payload.base_branch ?? null}}",
            },
            {
                "id": "team-record-canonical-promotion",
                "display_name": "Record canonical promotion result",
                "type": "effect:team:record-git-promotion",
                "promotion_id": "${{team-git-promotion-requested.payload.promotion_id}}",
                "success": "${{team-push-canonical-branch.success}}",
                "commit_sha": "${{team-push-canonical-branch.destination_commit ?? null}}",
                "error": "${{team-push-canonical-branch.error ?? null}}",
                "result_outputs": {
                    "origin_branch": (
                        "${{team-push-canonical-branch.origin_branch ?? null}}"
                    ),
                    "message": "${{team-push-canonical-branch.message ?? null}}",
                },
            },
            {
                "id": "team-mark-release-published",
                "display_name": "Mark release package published",
                "type": "effect:team:mark-release-published",
                "release_package_id": (
                    "${{team-git-promotion-requested.payload.release_package_id}}"
                ),
                "success": "${{team-push-canonical-branch.success}}",
                "canonical_branch": "${{team-git-promotion-requested.payload.target_branch}}",
                "published_commit_sha": (
                    "${{team-push-canonical-branch.destination_commit ?? null}}"
                ),
                "error": "${{team-push-canonical-branch.error ?? null}}",
                "result_outputs": {
                    "origin_branch": (
                        "${{team-push-canonical-branch.origin_branch ?? null}}"
                    ),
                    "message": "${{team-push-canonical-branch.message ?? null}}",
                },
            },
        ],
        "edges": [
            {"source_id": "team-cron", "target_id": "team-wake-due-agents"},
            {
                "source_id": "team-chat-message-queued",
                "target_id": "team-wake-queued-chat-message",
            },
            {"source_id": "team-cron", "target_id": "team-wake-governance-managers"},
            {"source_id": "team-ephemeral-requested", "target_id": "team-create-ephemeral-agent"},
            {"source_id": "team-create-ephemeral-agent", "target_id": "team-mark-ephemeral-spawned"},
            {"source_id": "team-mark-ephemeral-spawned", "target_id": "team-ephemeral-spawned"},
            {"source_id": "team-ephemeral-spawned", "target_id": "team-prompt-ephemeral-agent"},
            {"source_id": "team-agent-run-completed", "target_id": "team-route-completed-session"},
            {"source_id": "team-route-completed-session", "target_id": "team-completed-persistent"},
            {"source_id": "team-completed-persistent", "target_id": "team-finalize-persistent-dispatch"},
            {"source_id": "team-route-completed-session", "target_id": "team-completed-ephemeral"},
            {"source_id": "team-completed-ephemeral", "target_id": "team-destroy-ephemeral-agent"},
            {"source_id": "team-destroy-ephemeral-agent", "target_id": "team-mark-ephemeral-cleaned-up"},
            {"source_id": "team-operator-escalation", "target_id": "team-notify-operator"},
            {"source_id": "team-git-promotion-requested", "target_id": "team-git-promotion-review"},
            {"source_id": "team-git-promotion-requested", "target_id": "team-git-promotion-feature"},
            {"source_id": "team-git-promotion-requested", "target_id": "team-git-promotion-release"},
            {"source_id": "team-git-promotion-requested", "target_id": "team-git-promotion-canonical"},
            {"source_id": "team-git-promotion-review", "target_id": "team-git-promote-review-branch"},
            {"source_id": "team-git-promote-review-branch", "target_id": "team-record-review-promotion"},
            {"source_id": "team-git-promotion-feature", "target_id": "team-consolidate-feature-branches"},
            {"source_id": "team-consolidate-feature-branches", "target_id": "team-record-feature-promotion"},
            {"source_id": "team-git-promotion-release", "target_id": "team-consolidate-release-branches"},
            {"source_id": "team-consolidate-release-branches", "target_id": "team-record-release-promotion"},
            {
                "source_id": "team-git-release-recovery-requested",
                "target_id": "team-promote-release-recovery-branch",
            },
            {
                "source_id": "team-promote-release-recovery-branch",
                "target_id": "team-record-release-recovery-promotion",
            },
            {"source_id": "team-git-promotion-canonical", "target_id": "team-push-canonical-branch"},
            {"source_id": "team-push-canonical-branch", "target_id": "team-record-canonical-promotion"},
            {"source_id": "team-record-canonical-promotion", "target_id": "team-mark-release-published"},
        ],
    },
}


def _pack_from_legacy_config(source: dict[str, Any]) -> dict[str, Any]:
    config = source["config"]
    nodes: list[dict[str, Any]] = []
    for section_name in ("triggers", "gates", "queries", "effects"):
        for node in config.get(section_name, []):
            nodes.append({"node": _ir_node_from_legacy_node(node)})
    return {
        "pack_id": "team-runtime",
        "pack_version": "v1",
        "description": source.get("description"),
        "graphs": deepcopy(source.get("graphs", [])),
        "ir": {
            "version": "v1",
            "nodes": nodes,
            "edges": deepcopy(config.get("edges", [])),
        },
    }


def _ir_node_from_legacy_node(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": node["id"],
        "display_name": node["display_name"],
        "type": node["type"],
        "config": {
            key: deepcopy(value)
            for key, value in node.items()
            if key not in {"id", "display_name", "type"}
        },
    }


TEAM_RUNTIME_V1: dict[str, Any] = _pack_from_legacy_config(_TEAM_RUNTIME_V1_LEGACY)


def team_runtime_v1() -> dict[str, Any]:
    return deepcopy(TEAM_RUNTIME_V1)
