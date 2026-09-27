"""Team releases."""

from __future__ import annotations

import sqlite3
from typing import Any

from .sqlite_codec import (
    _grouped_values,
    _insert_position_values,
    _json_dumps,
    _json_loads,
)


class ReleaseStoreMixin:
    """Read/write releases rows on a caller-owned transaction."""

    @staticmethod
    def _read_release_packages(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        deliverables = _grouped_values(
            connection,
            "SELECT package_id AS owner_id, deliverable_id AS value FROM team_release_deliverables ORDER BY package_id, position",
        )
        artifacts = _grouped_values(
            connection,
            "SELECT package_id AS owner_id, artifact_ref AS value FROM team_release_artifacts ORDER BY package_id, position",
        )
        branches = _grouped_values(
            connection,
            "SELECT package_id AS owner_id, feature_branch AS value FROM team_release_feature_branches ORDER BY package_id, position",
        )
        promotions = _grouped_values(
            connection,
            "SELECT package_id AS owner_id, promotion_record_id AS value FROM team_release_git_promotions ORDER BY package_id, position",
        )
        for row in connection.execute("SELECT * FROM team_release_packages ORDER BY id"):
            payload["release_packages"][row["id"]] = {
                "id": row["id"],
                "project_id": row["project_id"],
                "owner_agent_id": row["owner_agent_id"],
                "work_lineage_id": row["work_lineage_id"],
                "summary": row["summary"],
                "supersedes_release_package_id": row["supersedes_release_package_id"],
                "lineage_root_release_package_id": row["lineage_root_release_package_id"],
                "lineage_sequence": row["lineage_sequence"],
                "deliverable_ids": deliverables.get(row["id"], []),
                "artifact_refs": artifacts.get(row["id"], []),
                "feature_branches": branches.get(row["id"], []),
                "release_branch": row["release_branch"],
                "canonical_branch": row["canonical_branch"],
                "git_base_branch": row["git_base_branch"],
                "published_commit_sha": row["published_commit_sha"],
                "base_commit_sha": row["base_commit_sha"],
                "canonical_base_commit_sha": row["canonical_base_commit_sha"],
                "candidate_commit_sha": row["candidate_commit_sha"],
                "candidate_tree_hash": row["candidate_tree_hash"],
                "approved_commit_sha": row["approved_commit_sha"],
                "review_correction_task_id": row["review_correction_task_id"],
                "verification_run_ids": _json_loads(row["verification_run_ids_json"]),
                "review_request_ids": _json_loads(row["review_request_ids_json"]),
                "review_cohort_id": row["review_cohort_id"],
                "review_cohort_sealed_at": row["review_cohort_sealed_at"],
                "manifest": _json_loads(row["manifest_json"]),
                "gate_failure": (
                    _json_loads(row["gate_failure_json"])
                    if row["gate_failure_json"] is not None
                    else None
                ),
                "git_promotion_record_ids": promotions.get(row["id"], []),
                "cancellation": (
                    _json_loads(row["cancellation_json"])
                    if row["cancellation_json"] is not None
                    else None
                ),
                "state": row["state"],
                "published_at": row["published_at"],
                "error": row["error"],
                "policy_version": row["policy_version"],
                "verification_waivers": _json_loads(row["verification_waivers_json"]),
                "gate_obligation_waivers": _json_loads(row["gate_obligation_waivers_json"]),
                "scope_validation": (
                    _json_loads(row["scope_validation_json"])
                    if row["scope_validation_json"] is not None
                    else None
                ),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_release_packages(connection: sqlite3.Connection, packages: dict[str, Any]) -> None:
        for package in packages.values():
            connection.execute(
                """
                INSERT INTO team_release_packages(
                    id, project_id, owner_agent_id, work_lineage_id, summary, supersedes_release_package_id,
                    lineage_root_release_package_id, lineage_sequence,
                    release_branch, canonical_branch, git_base_branch,
                    published_commit_sha,
                    base_commit_sha, canonical_base_commit_sha,
                    candidate_commit_sha, candidate_tree_hash,
                    approved_commit_sha, review_correction_task_id,
                    verification_run_ids_json, review_request_ids_json,
                    review_cohort_id, review_cohort_sealed_at,
                    manifest_json, gate_failure_json,
                    state, published_at, error, policy_version,
                    verification_waivers_json, gate_obligation_waivers_json,
                    scope_validation_json, created_at, updated_at, cancellation_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    package["id"],
                    package["project_id"],
                    package.get("owner_agent_id"),
                    package.get("work_lineage_id"),
                    package.get("summary"),
                    package.get("supersedes_release_package_id"),
                    package.get("lineage_root_release_package_id"),
                    int(package.get("lineage_sequence", 0)),
                    package.get("release_branch"),
                    package.get("canonical_branch"),
                    package.get("git_base_branch"),
                    package.get("published_commit_sha"),
                    package.get("base_commit_sha"),
                    package.get("canonical_base_commit_sha"),
                    package.get("candidate_commit_sha"),
                    package.get("candidate_tree_hash"),
                    package.get("approved_commit_sha"),
                    package.get("review_correction_task_id"),
                    _json_dumps(package.get("verification_run_ids", [])),
                    _json_dumps(package.get("review_request_ids", [])),
                    package.get("review_cohort_id"),
                    package.get("review_cohort_sealed_at"),
                    _json_dumps(package.get("manifest") or {}),
                    (
                        _json_dumps(package.get("gate_failure"))
                        if package.get("gate_failure") is not None
                        else None
                    ),
                    package.get("state", "pending"),
                    package.get("published_at"),
                    package.get("error"),
                    package.get("policy_version", 1),
                    _json_dumps(package.get("verification_waivers", [])),
                    _json_dumps(package.get("gate_obligation_waivers", [])),
                    (
                        _json_dumps(package.get("scope_validation"))
                        if package.get("scope_validation") is not None
                        else None
                    ),
                    package["created_at"],
                    package.get("updated_at"),
                    _json_dumps(package["cancellation"]) if package.get("cancellation") else None,
                ),
            )
            _insert_position_values(
                connection,
                "team_release_deliverables",
                "package_id",
                package["id"],
                "deliverable_id",
                package.get("deliverable_ids", []),
            )
            _insert_position_values(
                connection,
                "team_release_artifacts",
                "package_id",
                package["id"],
                "artifact_ref",
                package.get("artifact_refs", []),
            )
            _insert_position_values(
                connection,
                "team_release_feature_branches",
                "package_id",
                package["id"],
                "feature_branch",
                package.get("feature_branches", []),
            )
            _insert_position_values(
                connection,
                "team_release_git_promotions",
                "package_id",
                package["id"],
                "promotion_record_id",
                package.get("git_promotion_record_ids", []),
            )

    @staticmethod
    def _read_release_verification_runs(
        connection: sqlite3.Connection, payload: dict[str, Any]
    ) -> None:
        for row in connection.execute(
            "SELECT id, payload_json FROM team_release_verification_runs ORDER BY id"
        ):
            payload["release_verification_runs"][row["id"]] = _json_loads(row["payload_json"])

    @staticmethod
    def _write_release_verification_runs(
        connection: sqlite3.Connection, runs: dict[str, Any]
    ) -> None:
        for run in runs.values():
            connection.execute(
                "INSERT INTO team_release_verification_runs(id, payload_json) VALUES (?, ?)",
                (run["id"], _json_dumps(run)),
            )

    @staticmethod
    def _read_human_reviews(
        connection: sqlite3.Connection,
        payload: dict[str, Any],
    ) -> None:
        request_configured_groups = _grouped_values(
            connection,
            (
                "SELECT human_review_request_id AS owner_id, group_id AS value "
                "FROM team_human_review_request_configured_groups "
                "ORDER BY human_review_request_id, position"
            ),
        )
        request_initial_users = _grouped_values(
            connection,
            (
                "SELECT human_review_request_id AS owner_id, user_id AS value "
                "FROM team_human_review_request_initial_users "
                "ORDER BY human_review_request_id, position"
            ),
        )
        request_current_users = _grouped_values(
            connection,
            (
                "SELECT human_review_request_id AS owner_id, user_id AS value "
                "FROM team_human_review_request_current_users "
                "ORDER BY human_review_request_id, position"
            ),
        )
        request_explicit_users = _grouped_values(
            connection,
            (
                "SELECT human_review_request_id AS owner_id, user_id AS value "
                "FROM team_human_review_request_explicit_users "
                "ORDER BY human_review_request_id, position"
            ),
        )
        for row in connection.execute("SELECT * FROM team_human_review_requests ORDER BY id"):
            request = _json_loads(row["payload_json"])
            requirement = dict(request.get("requirement_snapshot") or {})
            requirement["configured_group_ids"] = request_configured_groups.get(row["id"], [])
            requirement["initially_eligible_user_ids"] = request_initial_users.get(row["id"], [])
            request["requirement_snapshot"] = requirement
            assignment = dict(request.get("current_assignment") or {})
            assignment["eligible_user_ids"] = request_current_users.get(row["id"], [])
            assignment["explicitly_assigned_user_ids"] = request_explicit_users.get(row["id"], [])
            request["current_assignment"] = assignment
            request.update(
                {
                    "release_package_id": row["release_package_id"],
                    "state": row["state"],
                    "candidate_commit_sha": row["candidate_commit_sha"],
                    "candidate_tree_hash": row["candidate_tree_hash"],
                    "assignment_generation": row["assignment_generation"],
                    "preparation_generation": row["preparation_generation"],
                }
            )
            payload["human_review_requests"][row["id"]] = request

        decision_confirmed_findings = _grouped_values(
            connection,
            (
                "SELECT human_review_decision_id AS owner_id, finding_id AS value "
                "FROM team_human_review_decision_confirmed_findings "
                "ORDER BY human_review_decision_id, position"
            ),
        )
        for row in connection.execute("SELECT * FROM team_human_review_decisions ORDER BY id"):
            decision = _json_loads(row["payload_json"])
            decision.update(
                {
                    "human_review_request_id": row["human_review_request_id"],
                    "reviewer_user_id": row["reviewer_user_id"],
                    "decision": row["decision"],
                    "request_id": row["request_id"],
                    "decided_at": row["decided_at"],
                    "confirmed_finding_ids": decision_confirmed_findings.get(row["id"], []),
                }
            )
            payload["human_review_decisions"][row["id"]] = decision

        finding_targets = _grouped_values(
            connection,
            (
                "SELECT finding_id AS owner_id, deliverable_id AS value "
                "FROM team_human_review_finding_targets "
                "ORDER BY finding_id, position"
            ),
        )
        finding_proposals = _grouped_values(
            connection,
            (
                "SELECT finding_id AS owner_id, deliverable_id AS value "
                "FROM team_human_review_finding_proposals "
                "ORDER BY finding_id, position"
            ),
        )
        for row in connection.execute("SELECT * FROM team_human_review_findings ORDER BY id"):
            finding = _json_loads(row["payload_json"])
            finding.update(
                {
                    "source_human_review_request_id": row["source_human_review_request_id"],
                    "correction_task_id": row["correction_task_id"],
                    "state": row["state"],
                    "resolved_by_human_review_request_id": row[
                        "resolved_by_human_review_request_id"
                    ],
                }
            )
            finding["target_deliverable_ids"] = finding_targets.get(row["id"], [])
            finding["proposed_by_deliverable_ids"] = finding_proposals.get(
                row["id"],
                [],
            )
            payload["human_review_findings"][row["id"]] = finding

    @staticmethod
    def _write_human_reviews(
        connection: sqlite3.Connection,
        *,
        requests: dict[str, Any],
        decisions: dict[str, Any],
        findings: dict[str, Any],
    ) -> None:
        for request in requests.values():
            connection.execute(
                """
                INSERT INTO team_human_review_requests(
                    id, release_package_id, state, candidate_commit_sha,
                    candidate_tree_hash, assignment_generation,
                    preparation_generation, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request["id"],
                    request["release_package_id"],
                    request["state"],
                    request["candidate_commit_sha"],
                    request["candidate_tree_hash"],
                    request.get("assignment_generation", 1),
                    request.get("preparation_generation", 1),
                    _json_dumps(request),
                ),
            )
            requirement = request["requirement_snapshot"]
            assignment = request.get("current_assignment") or {}
            for table_name, value_column, values in (
                (
                    "team_human_review_request_configured_groups",
                    "group_id",
                    requirement.get("configured_group_ids", []),
                ),
                (
                    "team_human_review_request_initial_users",
                    "user_id",
                    requirement.get("initially_eligible_user_ids", []),
                ),
                (
                    "team_human_review_request_current_users",
                    "user_id",
                    assignment.get("eligible_user_ids", []),
                ),
                (
                    "team_human_review_request_explicit_users",
                    "user_id",
                    assignment.get("explicitly_assigned_user_ids", []),
                ),
            ):
                _insert_position_values(
                    connection,
                    table_name,
                    "human_review_request_id",
                    request["id"],
                    value_column,
                    values,
                )
        for decision in decisions.values():
            connection.execute(
                """
                INSERT INTO team_human_review_decisions(
                    id, human_review_request_id, reviewer_user_id,
                    decision, request_id, decided_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision["id"],
                    decision["human_review_request_id"],
                    decision["reviewer_user_id"],
                    decision["decision"],
                    decision["request_id"],
                    decision["decided_at"],
                    _json_dumps(decision),
                ),
            )
            _insert_position_values(
                connection,
                "team_human_review_decision_confirmed_findings",
                "human_review_decision_id",
                decision["id"],
                "finding_id",
                decision.get("confirmed_finding_ids", []),
            )
        for finding in findings.values():
            connection.execute(
                """
                INSERT INTO team_human_review_findings(
                    id, source_human_review_request_id, correction_task_id,
                    state, resolved_by_human_review_request_id, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    finding["id"],
                    finding["source_human_review_request_id"],
                    finding["correction_task_id"],
                    finding["state"],
                    finding.get("resolved_by_human_review_request_id"),
                    _json_dumps(finding),
                ),
            )
            _insert_position_values(
                connection,
                "team_human_review_finding_targets",
                "finding_id",
                finding["id"],
                "deliverable_id",
                finding.get("target_deliverable_ids", []),
            )
            _insert_position_values(
                connection,
                "team_human_review_finding_proposals",
                "finding_id",
                finding["id"],
                "deliverable_id",
                finding.get("proposed_by_deliverable_ids", []),
            )
