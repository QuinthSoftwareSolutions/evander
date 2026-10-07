"""Team deliverables."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from .sqlite_codec import (
    _grouped_values,
    _insert_position_values,
    _json_dumps,
    _json_loads,
    _utc_datetime_value,
)


class DeliverableStoreMixin:
    """Read/write deliverables rows on a caller-owned transaction."""

    @staticmethod
    def _deliverable_trigger_sources(
        connection: sqlite3.Connection,
        *,
        team_id: str | None = None,
        project_id: str | None = None,
        producer_agent_id: str | None = None,
    ) -> list[tuple[str, datetime]]:
        conditions: list[str] = []
        parameters: list[str] = []
        for field, value in (
            ("p.team_id", team_id),
            ("t.project_id", project_id),
            ("d.producer_agent_id", producer_agent_id),
        ):
            if value is not None:
                conditions.append(f"{field} = ?")
                parameters.append(value)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        return [
            (row["id"], _utc_datetime_value(row["created_at"]))
            for row in connection.execute(
                "SELECT d.id, d.created_at FROM team_deliverables d "
                "LEFT JOIN team_tasks t ON t.id = d.task_id "
                "LEFT JOIN team_projects p ON p.id = t.project_id"
                + where + " ORDER BY d.id",
                parameters,
            )
        ]

    @staticmethod
    def _read_deliverables(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        artifacts = _grouped_values(
            connection,
            "SELECT deliverable_id AS owner_id, artifact_ref AS value FROM team_deliverable_artifacts ORDER BY deliverable_id, position",
        )
        reviews = _grouped_values(
            connection,
            "SELECT deliverable_id AS owner_id, review_request_id AS value FROM team_deliverable_reviews ORDER BY deliverable_id, position",
        )
        promotions = _grouped_values(
            connection,
            "SELECT deliverable_id AS owner_id, promotion_record_id AS value FROM team_deliverable_promotion_records ORDER BY deliverable_id, position",
        )
        sources = _grouped_values(
            connection,
            "SELECT deliverable_id AS owner_id, source_deliverable_id AS value "
            "FROM team_deliverable_sources ORDER BY deliverable_id, position",
        )
        for row in connection.execute("SELECT * FROM team_deliverables ORDER BY id"):
            payload["deliverables"][row["id"]] = {
                "id": row["id"],
                "task_id": row["task_id"],
                "producer_agent_id": row["producer_agent_id"],
                "artifact_refs": artifacts.get(row["id"], []),
                "case_note_ids": _json_loads(row["case_note_ids_json"]),
                "context_snapshot_ids": _json_loads(row["context_snapshot_ids_json"]),
                "summary": row["summary"],
                "state": row["state"],
                "review_request_ids": reviews.get(row["id"], []),
                "git_refs": {
                    "branch_context_id": row["branch_context_id"],
                    "agent_branch": row["agent_branch"],
                    "review_branch": row["review_branch"],
                    "feature_branch": row["feature_branch"],
                    "release_branch": row["release_branch"],
                    "commit_sha": row["commit_sha"],
                    "submitted_commit_sha": row["submitted_commit_sha"],
                    "reviewed_commit_sha": row["reviewed_commit_sha"],
                    "feature_commit_sha": row["feature_commit_sha"],
                    "release_commit_sha": row["release_commit_sha"],
                    "accepted_pinned_ref": row["accepted_pinned_ref"],
                    "promotion_record_ids": promotions.get(row["id"], []),
                },
                "verification": (
                    _json_loads(row["verification_json"])
                    if row["verification_json"] is not None
                    else None
                ),
                "outcomes": _json_loads(row["outcomes_json"]),
                "source_deliverable_ids": sources.get(row["id"], []),
                "completion": (
                    _json_loads(row["completion_json"])
                    if row["completion_json"] is not None
                    else None
                ),
                "carried_findings": _json_loads(row["carried_findings_json"]),
                "review_manifest": (
                    _json_loads(row["review_manifest_json"])
                    if row["review_manifest_json"] is not None
                    else None
                ),
                "dependency_composition_manifest": (
                    _json_loads(row["dependency_composition_manifest_json"])
                    if row["dependency_composition_manifest_json"] is not None
                    else None
                ),
                "acceptance_policy_version": int(row["acceptance_policy_version"] or 1),
                "acceptance_recipient_agent_id": row["acceptance_recipient_agent_id"],
                "acceptance_decision": (
                    _json_loads(row["acceptance_decision_json"])
                    if row["acceptance_decision_json"] is not None
                    else None
                ),
                "initial_release_disposition": (
                    _json_loads(row["initial_release_disposition_json"])
                    if row["initial_release_disposition_json"] is not None
                    else None
                ),
                "supersedes_deliverable_id": row["supersedes_deliverable_id"],
                "superseded_by_deliverable_id": row["superseded_by_deliverable_id"],
                "supersession_reason": row["supersession_reason"],
                "superseded_at": row["superseded_at"],
                "superseded_by_actor": row["superseded_by_actor"],
                "review_cohort_id": row["review_cohort_id"],
                "review_cohort_sealed_at": row["review_cohort_sealed_at"],
                "rework_requested_at": row["rework_requested_at"],
                "rework_requested_by_agent_id": row["rework_requested_by_agent_id"],
                "rework_reason": row["rework_reason"],
                "created_at": row["created_at"],
            }

    @staticmethod
    def _write_deliverables(connection: sqlite3.Connection, deliverables: dict[str, Any]) -> None:
        for deliverable in deliverables.values():
            git_refs = deliverable.get("git_refs", {})
            connection.execute(
                """
                INSERT INTO team_deliverables(
                    id, task_id, producer_agent_id, summary, state, branch_context_id,
                    agent_branch, review_branch, feature_branch, release_branch, commit_sha,
                    submitted_commit_sha, reviewed_commit_sha, feature_commit_sha,
                    release_commit_sha, accepted_pinned_ref, verification_json,
                    supersedes_deliverable_id,
                    superseded_by_deliverable_id, carried_findings_json,
                    review_manifest_json, dependency_composition_manifest_json,
                    acceptance_policy_version, acceptance_recipient_agent_id,
                    acceptance_decision_json, initial_release_disposition_json,
                    supersession_reason, superseded_at,
                    superseded_by_actor, review_cohort_id,
                    review_cohort_sealed_at, rework_requested_at,
                    rework_requested_by_agent_id, rework_reason, outcomes_json,
                    completion_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    deliverable["id"],
                    deliverable["task_id"],
                    deliverable["producer_agent_id"],
                    deliverable["summary"],
                    deliverable.get("state", "pending"),
                    git_refs.get("branch_context_id"),
                    git_refs.get("agent_branch"),
                    git_refs.get("review_branch"),
                    git_refs.get("feature_branch"),
                    git_refs.get("release_branch"),
                    git_refs.get("commit_sha"),
                    git_refs.get("submitted_commit_sha"),
                    git_refs.get("reviewed_commit_sha"),
                    git_refs.get("feature_commit_sha"),
                    git_refs.get("release_commit_sha"),
                    git_refs.get("accepted_pinned_ref"),
                    (
                        _json_dumps(deliverable.get("verification"))
                        if deliverable.get("verification") is not None
                        else None
                    ),
                    deliverable.get("supersedes_deliverable_id"),
                    deliverable.get("superseded_by_deliverable_id"),
                    _json_dumps(deliverable.get("carried_findings", [])),
                    (
                        _json_dumps(deliverable.get("review_manifest"))
                        if deliverable.get("review_manifest") is not None
                        else None
                    ),
                    (
                        _json_dumps(deliverable.get("dependency_composition_manifest"))
                        if deliverable.get("dependency_composition_manifest") is not None
                        else None
                    ),
                    int(deliverable.get("acceptance_policy_version", 1)),
                    deliverable.get("acceptance_recipient_agent_id"),
                    (
                        _json_dumps(deliverable.get("acceptance_decision"))
                        if deliverable.get("acceptance_decision") is not None
                        else None
                    ),
                    (
                        _json_dumps(deliverable.get("initial_release_disposition"))
                        if deliverable.get("initial_release_disposition") is not None
                        else None
                    ),
                    deliverable.get("supersession_reason"),
                    deliverable.get("superseded_at"),
                    deliverable.get("superseded_by_actor"),
                    deliverable.get("review_cohort_id"),
                    deliverable.get("review_cohort_sealed_at"),
                    deliverable.get("rework_requested_at"),
                    deliverable.get("rework_requested_by_agent_id"),
                    deliverable.get("rework_reason"),
                    _json_dumps(deliverable.get("outcomes", [])),
                    (
                        _json_dumps(deliverable.get("completion"))
                        if deliverable.get("completion") is not None
                        else None
                    ),
                    deliverable["created_at"],
                ),
            )
            _insert_position_values(
                connection,
                "team_deliverable_artifacts",
                "deliverable_id",
                deliverable["id"],
                "artifact_ref",
                deliverable.get("artifact_refs", []),
            )
            connection.execute(
                "UPDATE team_deliverables SET case_note_ids_json = ?, context_snapshot_ids_json = ? WHERE id = ?",
                (
                    _json_dumps(deliverable.get("case_note_ids", [])),
                    _json_dumps(deliverable.get("context_snapshot_ids", [])),
                    deliverable["id"],
                ),
            )
            _insert_position_values(
                connection,
                "team_deliverable_reviews",
                "deliverable_id",
                deliverable["id"],
                "review_request_id",
                deliverable.get("review_request_ids", []),
            )
            _insert_position_values(
                connection,
                "team_deliverable_promotion_records",
                "deliverable_id",
                deliverable["id"],
                "promotion_record_id",
                git_refs.get("promotion_record_ids", []),
            )
            _insert_position_values(
                connection,
                "team_deliverable_sources",
                "deliverable_id",
                deliverable["id"],
                "source_deliverable_id",
                deliverable.get("source_deliverable_ids", []),
            )
