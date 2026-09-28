"""Team git."""

from __future__ import annotations

import sqlite3
from typing import Any

from .sqlite_codec import (
    _grouped_values,
    _insert_position_values,
    _json_dumps,
    _json_loads,
)


class GitStoreMixin:
    """Read/write git rows on a caller-owned transaction."""

    @staticmethod
    def _read_dispatches(
        connection: sqlite3.Connection,
        payload: dict[str, Any],
    ) -> None:
        scope_values: dict[str, dict[str, list[str]]] = {}
        for field_name, table_name, value_column in (
            ("task_ids", "team_dispatch_tasks", "task_id"),
            ("blocker_ids", "team_dispatch_blockers", "blocker_id"),
            ("review_request_ids", "team_dispatch_reviews", "review_request_id"),
            ("inbox_item_ids", "team_dispatch_inbox", "inbox_item_id"),
            ("project_ids", "team_dispatch_projects", "project_id"),
            ("managed_repo_ids", "team_dispatch_repositories", "managed_repo_id"),
        ):
            scope_values[field_name] = _grouped_values(
                connection,
                (
                    f"SELECT dispatch_id AS owner_id, {value_column} AS value "
                    f"FROM {table_name} ORDER BY dispatch_id, position"
                ),
            )
        for row in connection.execute("SELECT * FROM team_dispatches ORDER BY id"):
            dispatch_id = row["id"]
            payload["dispatches"][dispatch_id] = {
                "id": dispatch_id,
                "agent_id": row["agent_id"],
                "session_id": row["session_id"],
                "cause_kind": row["cause_kind"],
                "cause_id": row["cause_id"],
                "work_lineage_id": row["work_lineage_id"],
                "native_thread_id": row["native_thread_id"],
                "native_thread_observed_at": row["native_thread_observed_at"],
                "task_ids": scope_values["task_ids"].get(dispatch_id, []),
                "claimed_role_task_ids": _json_loads(row["claimed_role_task_ids_json"] or "[]"),
                "blocker_ids": scope_values["blocker_ids"].get(dispatch_id, []),
                "review_request_ids": scope_values["review_request_ids"].get(dispatch_id, []),
                "inbox_item_ids": scope_values["inbox_item_ids"].get(dispatch_id, []),
                "project_ids": scope_values["project_ids"].get(dispatch_id, []),
                "managed_repo_ids": scope_values["managed_repo_ids"].get(dispatch_id, []),
                "source_evidence_snapshot_ids": _json_loads(
                    row["source_evidence_snapshot_ids_json"] or "[]"
                ),
                "chat_message_ids": _json_loads(row["chat_message_ids_json"] or "[]"),
                "review_routing_authorities": _json_loads(
                    row["review_routing_authorities_json"] or "[]"
                ),
                "initial_release_decision_deliverable_ids": _json_loads(
                    row["initial_release_decision_deliverable_ids_json"] or "[]"
                ),
                "mcp_capability_receipt": _json_loads(row["mcp_capability_receipt_json"] or "null"),
                "status": row["status"],
                "created_at": row["created_at"],
                "preparation_expires_at": row["preparation_expires_at"],
                "queued_at": row["queued_at"],
                "requested_lease_seconds": row["requested_lease_seconds"],
                "activation_completed_at": row["activation_completed_at"],
                "session_metadata_mirrored_at": row["session_metadata_mirrored_at"],
                "role_description_fingerprint": row["role_description_fingerprint"],
                "wake_work_fingerprint": row["wake_work_fingerprint"],
                "wake_work_record_fingerprints": _json_loads(
                    row["wake_work_record_fingerprints_json"] or "{}"
                ),
                "action_feedback_dispatch_ids": _json_loads(
                    row["action_feedback_dispatch_ids_json"] or "[]"
                ),
                "prepared_at": row["prepared_at"],
                "leased_at": row["leased_at"],
                "lease_expires_at": row["lease_expires_at"],
                "completed_at": row["completed_at"],
                "error": row["error"],
            }

    @staticmethod
    def _write_dispatches(
        connection: sqlite3.Connection,
        dispatches: dict[str, Any],
    ) -> None:
        for dispatch in dispatches.values():
            connection.execute(
                """
                INSERT INTO team_dispatches(
                    id, agent_id, session_id, cause_kind, cause_id, work_lineage_id,
                    claimed_role_task_ids_json,
                    chat_message_ids_json,
                    review_routing_authorities_json,
                    initial_release_decision_deliverable_ids_json,
                    mcp_capability_receipt_json, status,
                    created_at, preparation_expires_at, queued_at,
                    requested_lease_seconds, activation_completed_at,
                    session_metadata_mirrored_at, role_description_fingerprint,
                    wake_work_fingerprint,
                    wake_work_record_fingerprints_json,
                    action_feedback_dispatch_ids_json,
                    source_evidence_snapshot_ids_json,
                    prepared_at, leased_at, lease_expires_at,
                    completed_at, error, native_thread_id, native_thread_observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dispatch["id"],
                    dispatch["agent_id"],
                    dispatch["session_id"],
                    dispatch["cause_kind"],
                    dispatch.get("cause_id"),
                    dispatch.get("work_lineage_id"),
                    _json_dumps(dispatch.get("claimed_role_task_ids", [])),
                    _json_dumps(dispatch.get("chat_message_ids", [])),
                    _json_dumps(dispatch.get("review_routing_authorities", [])),
                    _json_dumps(dispatch.get("initial_release_decision_deliverable_ids", [])),
                    _json_dumps(dispatch.get("mcp_capability_receipt")),
                    dispatch["status"],
                    dispatch["created_at"],
                    dispatch.get("preparation_expires_at"),
                    dispatch.get("queued_at"),
                    dispatch.get("requested_lease_seconds", 900),
                    dispatch.get("activation_completed_at"),
                    dispatch.get("session_metadata_mirrored_at"),
                    dispatch.get("role_description_fingerprint"),
                    dispatch.get("wake_work_fingerprint"),
                    _json_dumps(dispatch.get("wake_work_record_fingerprints", {})),
                    _json_dumps(dispatch.get("action_feedback_dispatch_ids", [])),
                    _json_dumps(dispatch.get("source_evidence_snapshot_ids", [])),
                    dispatch.get("prepared_at"),
                    dispatch.get("leased_at"),
                    dispatch.get("lease_expires_at"),
                    dispatch.get("completed_at"),
                    dispatch.get("error"),
                    dispatch.get("native_thread_id"),
                    dispatch.get("native_thread_observed_at"),
                ),
            )
            for field_name, table_name, value_column in (
                ("task_ids", "team_dispatch_tasks", "task_id"),
                ("blocker_ids", "team_dispatch_blockers", "blocker_id"),
                ("review_request_ids", "team_dispatch_reviews", "review_request_id"),
                ("inbox_item_ids", "team_dispatch_inbox", "inbox_item_id"),
                ("project_ids", "team_dispatch_projects", "project_id"),
                (
                    "managed_repo_ids",
                    "team_dispatch_repositories",
                    "managed_repo_id",
                ),
            ):
                _insert_position_values(
                    connection,
                    table_name,
                    "dispatch_id",
                    dispatch["id"],
                    value_column,
                    dispatch.get(field_name, []),
                )

    @staticmethod
    def _read_git_recovery_incidents(connection, payload):
        for row in connection.execute("SELECT id, payload_json FROM team_git_recovery_incidents ORDER BY id"):
            payload["git_recovery_incidents"][row["id"]] = _json_loads(row["payload_json"])

    @staticmethod
    def _write_git_recovery_incidents(connection, incidents):
        for incident in incidents.values():
            connection.execute(
                "INSERT INTO team_git_recovery_incidents(id, blocker_id, task_id, state, payload_json) VALUES (?, ?, ?, ?, ?)",
                (incident["id"], incident["blocker_id"], incident["task_id"], incident["state"], _json_dumps(incident)),
            )

    @staticmethod
    def _read_git_preparation_attempts(
        connection: sqlite3.Connection,
        payload: dict[str, Any],
    ) -> None:
        for row in connection.execute(
            "SELECT id, payload_json FROM team_git_preparation_attempts ORDER BY id"
        ):
            payload["git_preparation_attempts"][row["id"]] = _json_loads(row["payload_json"])

    @staticmethod
    def _write_git_preparation_attempts(
        connection: sqlite3.Connection,
        attempts: dict[str, Any],
    ) -> None:
        for attempt in attempts.values():
            connection.execute(
                """
                INSERT INTO team_git_preparation_attempts(
                    id, task_id, agent_id, state, updated_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt["id"],
                    attempt["task_id"],
                    attempt["agent_id"],
                    attempt["state"],
                    attempt["updated_at"],
                    _json_dumps(attempt),
                ),
            )

    @staticmethod
    def _read_worktree_cleanup_candidates(
        connection: sqlite3.Connection,
        payload: dict[str, Any],
    ) -> None:
        for row in connection.execute(
            "SELECT id, payload_json FROM team_worktree_cleanup_candidates ORDER BY id"
        ):
            payload["worktree_cleanup_candidates"][row["id"]] = _json_loads(row["payload_json"])

    @staticmethod
    def _write_worktree_cleanup_candidates(
        connection: sqlite3.Connection,
        candidates: dict[str, Any],
    ) -> None:
        for candidate in candidates.values():
            connection.execute(
                """
                INSERT INTO team_worktree_cleanup_candidates(
                    id, agent_id, session_id, task_id, project_id, git_work_context_id,
                    managed_repo_id, worktree_path, state, eligible_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate["id"],
                    candidate["agent_id"],
                    candidate["session_id"],
                    candidate["task_id"],
                    candidate["project_id"],
                    candidate["git_work_context_id"],
                    candidate["managed_repo_id"],
                    candidate["worktree_path"],
                    candidate["state"],
                    candidate["eligible_at"],
                    _json_dumps(candidate),
                ),
            )

    @staticmethod
    def _read_ephemeral_assignments(
        connection: sqlite3.Connection, payload: dict[str, Any]
    ) -> None:
        artifacts = _grouped_values(
            connection,
            "SELECT assignment_id AS owner_id, artifact_ref AS value FROM team_ephemeral_assignment_artifacts ORDER BY assignment_id, position",
        )
        tools = _grouped_values(
            connection,
            "SELECT assignment_id AS owner_id, tool AS value FROM team_ephemeral_assignment_allowed_tools ORDER BY assignment_id, position",
        )
        for row in connection.execute("SELECT * FROM team_ephemeral_assignments ORDER BY id"):
            payload["ephemeral_assignments"][row["id"]] = {
                "id": row["id"],
                "parent_agent_id": row["parent_agent_id"],
                "project_id": row["project_id"],
                "task_id": row["task_id"],
                "session_id": row["session_id"],
                "title": row["title"],
                "prompt": row["prompt"],
                "state": row["state"],
                "artifact_refs": artifacts.get(row["id"], []),
                "agent_branch": row["agent_branch"],
                "commit_sha": row["commit_sha"],
                "summary": row["summary"],
                "error": row["error"],
                "model": row["model"],
                "agent_provider": row["agent_provider"],
                "execution_backend": row["execution_backend"],
                "container_image": row["container_image"],
                "allowed_tools": tools.get(row["id"], []),
                "ttl_hours": row["ttl_hours"],
                "directory_path": row["directory_path"],
                "metadata": _json_loads(row["metadata_json"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_ephemeral_assignments(
        connection: sqlite3.Connection, assignments: dict[str, Any]
    ) -> None:
        for assignment in assignments.values():
            connection.execute(
                """
                INSERT INTO team_ephemeral_assignments(
                    id, parent_agent_id, project_id, task_id, session_id, title, prompt,
                    state, agent_branch, commit_sha, summary, error, model, agent_provider,
                    execution_backend, container_image, ttl_hours, directory_path, metadata_json,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    assignment["id"],
                    assignment["parent_agent_id"],
                    assignment["project_id"],
                    assignment["task_id"],
                    assignment["session_id"],
                    assignment["title"],
                    assignment["prompt"],
                    assignment.get("state", "requested"),
                    assignment.get("agent_branch"),
                    assignment.get("commit_sha"),
                    assignment.get("summary"),
                    assignment.get("error"),
                    assignment.get("model"),
                    assignment.get("agent_provider"),
                    assignment.get("execution_backend"),
                    assignment.get("container_image"),
                    assignment.get("ttl_hours"),
                    assignment.get("directory_path"),
                    _json_dumps(assignment.get("metadata", {})),
                    assignment["created_at"],
                    assignment["updated_at"],
                ),
            )
            _insert_position_values(
                connection,
                "team_ephemeral_assignment_artifacts",
                "assignment_id",
                assignment["id"],
                "artifact_ref",
                assignment.get("artifact_refs", []),
            )
            _insert_position_values(
                connection,
                "team_ephemeral_assignment_allowed_tools",
                "assignment_id",
                assignment["id"],
                "tool",
                assignment.get("allowed_tools", []),
            )

    @staticmethod
    def _read_git_promotions(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        for row in connection.execute("SELECT * FROM team_git_promotions ORDER BY id"):
            payload["git_promotions"][row["id"]] = {
                "id": row["id"],
                "project_id": row["project_id"],
                "actor_agent_id": row["actor_agent_id"],
                "source_branch": row["source_branch"],
                "sources": _json_loads(row["sources_json"]),
                "target_branch": row["target_branch"],
                "promotion_kind": row["promotion_kind"],
                "task_id": row["task_id"],
                "deliverable_id": row["deliverable_id"],
                "release_package_id": row["release_package_id"],
                "git_base_branch": row["git_base_branch"],
                "base_commit_sha": row["base_commit_sha"],
                "state": row["state"],
                "commit_sha": row["commit_sha"],
                "outputs": _json_loads(row["outputs_json"]),
                "error": row["error"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_git_promotions(connection: sqlite3.Connection, promotions: dict[str, Any]) -> None:
        for promotion in promotions.values():
            connection.execute(
                """
                INSERT INTO team_git_promotions(
                    id, project_id, actor_agent_id, source_branch, sources_json, target_branch,
                    promotion_kind, task_id, deliverable_id, release_package_id, state,
                    git_base_branch, base_commit_sha, commit_sha, outputs_json, error,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    promotion["id"],
                    promotion["project_id"],
                    promotion["actor_agent_id"],
                    promotion["source_branch"],
                    _json_dumps(promotion.get("sources", [])),
                    promotion["target_branch"],
                    promotion["promotion_kind"],
                    promotion.get("task_id"),
                    promotion.get("deliverable_id"),
                    promotion.get("release_package_id"),
                    promotion.get("state", "pending"),
                    promotion.get("git_base_branch"),
                    promotion.get("base_commit_sha"),
                    promotion.get("commit_sha"),
                    _json_dumps(promotion.get("outputs", {})),
                    promotion.get("error"),
                    promotion["created_at"],
                    promotion["updated_at"],
                ),
            )

    @staticmethod
    def _read_dependency_composition_recoveries(
        connection: sqlite3.Connection,
        payload: dict[str, Any],
    ) -> None:
        for row in connection.execute(
            "SELECT * FROM team_dependency_composition_recoveries ORDER BY id"
        ):
            payload["dependency_composition_recoveries"][row["id"]] = {
                "id": row["id"],
                "blocker_id": row["blocker_id"],
                "target_task_id": row["target_task_id"],
                "project_id": row["project_id"],
                "managed_repo_id": row["managed_repo_id"],
                "directory_path": row["directory_path"],
                "base_branch": row["base_branch"],
                "base_commit_sha": row["base_commit_sha"],
                "base_tree_hash": row["base_tree_hash"],
                "dependency_git_sources": _json_loads(row["dependency_git_sources_json"]),
                "source_fingerprint": row["source_fingerprint"],
                "preparation_generation": row["preparation_generation"],
                "failed_dependency_task_id": row["failed_dependency_task_id"],
                "failed_deliverable_id": row["failed_deliverable_id"],
                "conflict_paths": _json_loads(row["conflict_paths_json"]),
                "recovery_task_id": row["recovery_task_id"],
                "verified_deliverable_id": row["verified_deliverable_id"],
                "verified_commit_sha": row["verified_commit_sha"],
                "verified_at": row["verified_at"],
                "accepted_deliverable_id": row["accepted_deliverable_id"],
                "resolved_commit_sha": row["resolved_commit_sha"],
                "state": row["state"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "resolved_at": row["resolved_at"],
            }

    @staticmethod
    def _write_dependency_composition_recoveries(
        connection: sqlite3.Connection,
        recoveries: dict[str, Any],
    ) -> None:
        for recovery in recoveries.values():
            connection.execute(
                """
                INSERT INTO team_dependency_composition_recoveries(
                    id, blocker_id, target_task_id, project_id, managed_repo_id,
                    directory_path,
                    base_branch, base_commit_sha, base_tree_hash,
                    dependency_git_sources_json, source_fingerprint,
                    preparation_generation,
                    failed_dependency_task_id, failed_deliverable_id,
                    conflict_paths_json, recovery_task_id,
                    verified_deliverable_id, verified_commit_sha, verified_at,
                    accepted_deliverable_id, resolved_commit_sha, state,
                    created_at, updated_at, resolved_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    recovery["id"],
                    recovery["blocker_id"],
                    recovery["target_task_id"],
                    recovery["project_id"],
                    recovery["managed_repo_id"],
                    recovery["directory_path"],
                    recovery["base_branch"],
                    recovery["base_commit_sha"],
                    recovery["base_tree_hash"],
                    _json_dumps(recovery.get("dependency_git_sources", [])),
                    recovery["source_fingerprint"],
                    recovery["preparation_generation"],
                    recovery["failed_dependency_task_id"],
                    recovery["failed_deliverable_id"],
                    _json_dumps(recovery.get("conflict_paths", [])),
                    recovery.get("recovery_task_id"),
                    recovery.get("verified_deliverable_id"),
                    recovery.get("verified_commit_sha"),
                    recovery.get("verified_at"),
                    recovery.get("accepted_deliverable_id"),
                    recovery.get("resolved_commit_sha"),
                    recovery.get("state", "awaiting_assignment"),
                    recovery["created_at"],
                    recovery["updated_at"],
                    recovery.get("resolved_at"),
                ),
            )

    @staticmethod
    def _read_git_work_contexts(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        for row in connection.execute("SELECT * FROM team_git_work_contexts ORDER BY id"):
            payload["git_work_contexts"][row["id"]] = {
                "id": row["id"],
                "agent_id": row["agent_id"],
                "task_id": row["task_id"],
                "dispatch_id": row["dispatch_id"],
                "worktree_path": row["worktree_path"],
                "base_branch": row["base_branch"],
                "base_commit": row["base_commit"],
                "agent_branch": row["agent_branch"],
                "commit_sha": row["commit_sha"],
                "dependency_git_sources": _json_loads(row["dependency_git_sources_json"]),
                "dependency_composition_recovery_id": row["dependency_composition_recovery_id"],
                "manual_dependency_git_sources": _json_loads(
                    row["manual_dependency_git_sources_json"]
                ),
                "dependency_composition_manifest": (
                    _json_loads(row["dependency_composition_manifest_json"])
                    if row["dependency_composition_manifest_json"] is not None
                    else None
                ),
                "preparation_generation": row["preparation_generation"],
                "state": row["state"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_git_work_contexts(connection: sqlite3.Connection, contexts: dict[str, Any]) -> None:
        for context in contexts.values():
            connection.execute(
                """
                INSERT INTO team_git_work_contexts(
                    id, agent_id, task_id, dispatch_id, worktree_path, base_branch,
                    base_commit, agent_branch, commit_sha,
                    dependency_git_sources_json,
                    dependency_composition_recovery_id,
                    manual_dependency_git_sources_json,
                    dependency_composition_manifest_json,
                    preparation_generation, state, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    context["id"],
                    context["agent_id"],
                    context["task_id"],
                    context.get("dispatch_id"),
                    context.get("worktree_path"),
                    context.get("base_branch"),
                    context.get("base_commit"),
                    context["agent_branch"],
                    context.get("commit_sha"),
                    _json_dumps(context.get("dependency_git_sources", [])),
                    context.get("dependency_composition_recovery_id"),
                    _json_dumps(context.get("manual_dependency_git_sources", [])),
                    (
                        _json_dumps(context.get("dependency_composition_manifest"))
                        if context.get("dependency_composition_manifest") is not None
                        else None
                    ),
                    context.get("preparation_generation"),
                    context.get("state", "active"),
                    context["created_at"],
                    context["updated_at"],
                ),
            )
