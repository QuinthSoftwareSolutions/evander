"""Team tasks."""

from __future__ import annotations

import sqlite3
from typing import Any

from .sqlite_codec import (
    _bool,
    _grouped_values,
    _insert_position_values,
    _int_bool,
    _json_dumps,
    _json_loads,
)


class TaskStoreMixin:
    """Read/write tasks rows on a caller-owned transaction."""

    @staticmethod
    def _read_work_lineages(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        for row in connection.execute(
            "SELECT id, payload_json FROM team_work_lineages ORDER BY id"
        ):
            payload["work_lineages"][row["id"]] = _json_loads(row["payload_json"])

    @staticmethod
    def _write_work_lineages(connection: sqlite3.Connection, lineages: dict[str, Any]) -> None:
        for lineage in lineages.values():
            connection.execute(
                "INSERT INTO team_work_lineages(id, payload_json) VALUES (?, ?)",
                (lineage["id"], _json_dumps(lineage)),
            )

    @staticmethod
    def _read_external_source_evidence_snapshots(
        connection: sqlite3.Connection,
        payload: dict[str, Any],
    ) -> None:
        for row in connection.execute(
            "SELECT id, payload_json FROM team_external_source_evidence_snapshots ORDER BY id"
        ):
            payload["external_source_evidence_snapshots"][row["id"]] = _json_loads(
                row["payload_json"]
            )

    @staticmethod
    def _write_external_source_evidence_snapshots(
        connection: sqlite3.Connection,
        snapshots: dict[str, Any],
    ) -> None:
        for snapshot in snapshots.values():
            connection.execute(
                """
                INSERT INTO team_external_source_evidence_snapshots(
                    id, team_id, project_id, provider_type, source_type,
                    external_identifier, external_version, fetched_at, expires_at,
                    freshness_status, access_classification, content_digest, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot["id"],
                    snapshot["team_id"],
                    snapshot.get("project_id"),
                    snapshot["provider_type"],
                    snapshot["source_type"],
                    snapshot["external_identifier"],
                    snapshot["external_version"],
                    snapshot["fetched_at"],
                    snapshot.get("expires_at"),
                    snapshot["freshness_status"],
                    snapshot["access_classification"],
                    snapshot["content_digest"],
                    _json_dumps(snapshot),
                ),
            )

    @staticmethod
    def _read_cases(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        for row in connection.execute("SELECT * FROM team_cases ORDER BY id"):
            payload["cases"][row["id"]] = {
                "id": row["id"],
                "team_id": row["team_id"],
                "project_id": row["project_id"],
                "title": row["title"],
                "description": row["description"],
                "status": row["status"],
                "priority": row["priority"],
                "owner_agent_id": row["owner_agent_id"],
                "source": row["source"],
                "source_ref": row["source_ref"],
                "tags": _json_loads(row["tags_json"]),
                "metadata": _json_loads(row["metadata_json"]),
                "task_ids": _json_loads(row["task_ids_json"]),
                "source_evidence_snapshot_ids": _json_loads(
                    row["source_evidence_snapshot_ids_json"] or "[]"
                ),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "closed_at": row["closed_at"],
            }

    @staticmethod
    def _write_cases(connection: sqlite3.Connection, cases: dict[str, Any]) -> None:
        for case in cases.values():
            connection.execute(
                """
                INSERT INTO team_cases(
                    id, team_id, project_id, title, description, status, priority,
                    owner_agent_id, source, source_ref, tags_json, metadata_json,
                    task_ids_json, source_evidence_snapshot_ids_json,
                    created_at, updated_at, closed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case["id"],
                    case["team_id"],
                    case.get("project_id"),
                    case["title"],
                    case.get("description", ""),
                    case.get("status", "open"),
                    case.get("priority", "normal"),
                    case.get("owner_agent_id"),
                    case.get("source"),
                    case.get("source_ref"),
                    _json_dumps(case.get("tags", [])),
                    _json_dumps(case.get("metadata", {})),
                    _json_dumps(case.get("task_ids", [])),
                    _json_dumps(case.get("source_evidence_snapshot_ids", [])),
                    case["created_at"],
                    case["updated_at"],
                    case.get("closed_at"),
                ),
            )

    @staticmethod
    def _read_tasks(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        dependencies = _grouped_values(
            connection,
            "SELECT task_id AS owner_id, dependency_task_id AS value FROM team_task_dependencies ORDER BY task_id, position",
        )
        deliverables = _grouped_values(
            connection,
            "SELECT task_id AS owner_id, deliverable_id AS value FROM team_task_deliverables ORDER BY task_id, position",
        )
        for row in connection.execute("SELECT * FROM team_tasks ORDER BY id"):
            payload["tasks"][row["id"]] = {
                "id": row["id"],
                "owner_agent_id": row["owner_agent_id"],
                "assignment_mode": row["assignment_mode"],
                "assignment_role_id": row["assignment_role_id"],
                "assignment_started_at": row["assignment_started_at"],
                "project_id": row["project_id"],
                "case_id": row["case_id"],
                "priority": row["priority"],
                "state": row["state"],
                "task_kind": row["task_kind"],
                "outcome_contract": (
                    _json_loads(row["outcome_contract_json"])
                    if row["outcome_contract_json"] is not None
                    else None
                ),
                "integration_boundary": _bool(row["integration_boundary"]),
                "deliverable_contract": (
                    _json_loads(row["deliverable_contract_json"])
                    if row["deliverable_contract_json"] is not None
                    else None
                ),
                "completion": (
                    _json_loads(row["completion_json"])
                    if row["completion_json"] is not None
                    else None
                ),
                "state_entered_at": row["state_entered_at"]
                or row["updated_at"]
                or row["created_at"],
                "title": row["title"],
                "description": row["description"],
                "created_by_agent_id": row["created_by_agent_id"],
                "created_by_actor": row["created_by_actor"],
                "parent_task_id": row["parent_task_id"],
                "work_item_attributions": (
                    _json_loads(row["work_item_attributions_json"])
                    if row["work_item_attributions_json"] is not None
                    else None
                ),
                "authored_by": (
                    _json_loads(row["authored_by_json"])
                    if row["authored_by_json"] is not None
                    else None
                ),
                "originating_task_id": row["originating_task_id"],
                "release_base_package_id": row["release_base_package_id"],
                "work_lineage_id": row["work_lineage_id"],
                "git_base_branch": row["git_base_branch"],
                "git_base_commit_sha": row["git_base_commit_sha"],
                "git_constraints": _json_loads(row["git_constraints_json"] or "{}"),
                "mutation_policy": (
                    _json_loads(row["mutation_policy_json"])
                    if row["mutation_policy_json"] is not None
                    else None
                ),
                "cancellation": (
                    _json_loads(row["cancellation_json"])
                    if row["cancellation_json"] is not None
                    else None
                ),
                "dependency_task_ids": dependencies.get(row["id"], []),
                "deliverable_ids": deliverables.get(row["id"], []),
                "source_evidence_snapshot_ids": _json_loads(
                    row["source_evidence_snapshot_ids_json"] or "[]"
                ),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_tasks(connection: sqlite3.Connection, tasks: dict[str, Any]) -> None:
        for task in tasks.values():
            connection.execute(
                """
                INSERT INTO team_tasks(
                    id, owner_agent_id, project_id, case_id, priority, state, title, description,
                    state_entered_at, created_by_agent_id, created_by_actor, parent_task_id,
                    authored_by_json, work_item_attributions_json, originating_task_id, release_base_package_id,
                    work_lineage_id,
                    git_base_branch, git_base_commit_sha, git_constraints_json,
                    mutation_policy_json, cancellation_json, task_kind,
                    outcome_contract_json, integration_boundary,
                    deliverable_contract_json, completion_json, created_at, updated_at,
                    source_evidence_snapshot_ids_json, assignment_mode, assignment_role_id, assignment_started_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task["id"],
                    task["owner_agent_id"],
                    task["project_id"],
                    task.get("case_id"),
                    int(task["priority"]),
                    task.get("state", "pending"),
                    task["title"],
                    task["description"],
                    task.get("state_entered_at") or task.get("updated_at") or task["created_at"],
                    task.get("created_by_agent_id"),
                    task.get("created_by_actor"),
                    task.get("parent_task_id"),
                    _json_dumps(task["authored_by"])
                    if task.get("authored_by") is not None
                    else None,
                    _json_dumps(task["work_item_attributions"])
                    if task.get("work_item_attributions") is not None
                    else None,
                    task.get("originating_task_id"),
                    task.get("release_base_package_id"),
                    task.get("work_lineage_id"),
                    task.get("git_base_branch"),
                    task.get("git_base_commit_sha"),
                    _json_dumps(task.get("git_constraints", {})),
                    (
                        _json_dumps(task.get("mutation_policy"))
                        if task.get("mutation_policy") is not None
                        else None
                    ),
                    (
                        _json_dumps(task.get("cancellation"))
                        if task.get("cancellation") is not None
                        else None
                    ),
                    task.get("task_kind", "support"),
                    (
                        _json_dumps(task.get("outcome_contract"))
                        if task.get("outcome_contract") is not None
                        else None
                    ),
                    _int_bool(task.get("integration_boundary")),
                    (
                        _json_dumps(task.get("deliverable_contract"))
                        if task.get("deliverable_contract") is not None
                        else None
                    ),
                    (
                        _json_dumps(task.get("completion"))
                        if task.get("completion") is not None
                        else None
                    ),
                    task["created_at"],
                    task["updated_at"],
                    _json_dumps(task.get("source_evidence_snapshot_ids", [])),
                    task.get("assignment_mode", "agent"),
                    task.get("assignment_role_id"),
                    task.get("assignment_started_at"),
                ),
            )
            _insert_position_values(
                connection,
                "team_task_dependencies",
                "task_id",
                task["id"],
                "dependency_task_id",
                task.get("dependency_task_ids", []),
            )
            _insert_position_values(
                connection,
                "team_task_deliverables",
                "task_id",
                task["id"],
                "deliverable_id",
                task.get("deliverable_ids", []),
            )
