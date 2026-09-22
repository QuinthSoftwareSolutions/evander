"""Team reviews."""

from __future__ import annotations

import sqlite3
from typing import Any

from .sqlite_codec import (
    _bool,
    _grouped_rows,
    _grouped_values,
    _insert_position_values,
    _int_bool,
    _json_dumps,
    _json_loads,
)


class ReviewStoreMixin:
    """Read/write reviews rows on a caller-owned transaction."""

    @staticmethod
    def _read_review_profiles(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        roles = _grouped_values(
            connection,
            "SELECT profile_id AS owner_id, role_id AS value FROM team_review_profile_roles ORDER BY profile_id, position",
        )
        criteria = _grouped_rows(
            connection,
            """
            SELECT profile_id AS owner_id, criterion_id, label, description, required
            FROM team_review_profile_criteria
            ORDER BY profile_id, position
            """,
            lambda row: {
                "id": row["criterion_id"],
                "label": row["label"],
                "description": row["description"],
                "required": _bool(row["required"]),
            },
        )
        for row in connection.execute("SELECT * FROM team_review_profiles ORDER BY id"):
            payload["review_profiles"][row["id"]] = {
                "id": row["id"],
                "team_id": row["team_id"],
                "project_id": row["project_id"],
                "name": row["name"],
                "description": row["description"],
                "applicable_role_ids": roles.get(row["id"], []),
                "criteria": criteria.get(row["id"], []),
                "required_outcome_kinds": _json_loads(row["required_outcome_kinds_json"]),
                "archived": _bool(row["archived"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _read_outcome_kind_definitions(
        connection: sqlite3.Connection,
        payload: dict[str, Any],
    ) -> None:
        for row in connection.execute("SELECT * FROM team_outcome_kind_definitions ORDER BY id"):
            payload["outcome_kind_definitions"][row["id"]] = {
                "id": row["id"],
                "team_id": row["team_id"],
                "project_id": row["project_id"],
                "kind": row["kind"],
                "name": row["name"],
                "description": row["description"],
                "reference_requirements": row["reference_requirements"],
                "evidence_requirements": row["evidence_requirements"],
                "archived": _bool(row["archived"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_outcome_kind_definitions(
        connection: sqlite3.Connection,
        definitions: dict[str, Any],
    ) -> None:
        for definition in definitions.values():
            connection.execute(
                """
                INSERT INTO team_outcome_kind_definitions(
                    id, team_id, project_id, kind, name, description,
                    reference_requirements, evidence_requirements, archived,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    definition["id"],
                    definition["team_id"],
                    definition.get("project_id"),
                    definition["kind"],
                    definition["name"],
                    definition.get("description", ""),
                    definition.get("reference_requirements", ""),
                    definition.get("evidence_requirements", ""),
                    _int_bool(definition.get("archived")),
                    definition["created_at"],
                    definition["updated_at"],
                ),
            )

    @staticmethod
    def _write_review_profiles(connection: sqlite3.Connection, profiles: dict[str, Any]) -> None:
        for profile in profiles.values():
            connection.execute(
                """
                INSERT INTO team_review_profiles(
                    id, team_id, project_id, name, description, archived,
                    required_outcome_kinds_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile["id"],
                    profile["team_id"],
                    profile.get("project_id"),
                    profile["name"],
                    profile.get("description", ""),
                    _int_bool(profile.get("archived")),
                    _json_dumps(profile.get("required_outcome_kinds", [])),
                    profile["created_at"],
                    profile["updated_at"],
                ),
            )
            _insert_position_values(
                connection,
                "team_review_profile_roles",
                "profile_id",
                profile["id"],
                "role_id",
                profile.get("applicable_role_ids", []),
            )
            for position, criterion in enumerate(profile.get("criteria", [])):
                connection.execute(
                    """
                    INSERT INTO team_review_profile_criteria(
                        profile_id, position, criterion_id, label, description, required
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        profile["id"],
                        position,
                        criterion["id"],
                        criterion["label"],
                        criterion.get("description", ""),
                        _int_bool(criterion.get("required", True)),
                    ),
                )

    @staticmethod
    def _read_review_requests(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        findings = _grouped_values(
            connection,
            "SELECT review_request_id AS owner_id, finding AS value FROM team_review_request_findings ORDER BY review_request_id, position",
        )
        criteria = _grouped_rows(
            connection,
            """
            SELECT review_request_id AS owner_id, criterion_id, state, finding,
                   evidence_refs_json
            FROM team_review_request_criteria
            ORDER BY review_request_id, position
            """,
            lambda row: {
                "criterion_id": row["criterion_id"],
                "state": row["state"],
                "finding": row["finding"],
                "evidence_refs": _json_loads(row["evidence_refs_json"]),
            },
        )
        for row in connection.execute("SELECT * FROM team_review_requests ORDER BY id"):
            payload["review_requests"][row["id"]] = {
                "id": row["id"],
                "deliverable_id": row["deliverable_id"] or None,
                "release_package_id": row["release_package_id"],
                "candidate_commit_sha": row["candidate_commit_sha"],
                "reviewer_agent_id": row["reviewer_agent_id"],
                "requested_by_agent_id": row["requested_by_agent_id"],
                "requested_by_actor": row["requested_by_actor"],
                "state": row["state"],
                "findings": findings.get(row["id"], []),
                "review_profile_id": row["review_profile_id"],
                "criteria_snapshot": (
                    _json_loads(row["criteria_snapshot_json"])
                    if row["criteria_snapshot_json"] is not None
                    else None
                ),
                "required_outcome_kind_definitions": _json_loads(
                    row["required_outcome_kind_definitions_json"]
                ),
                "instructions": row["instructions"],
                "criteria": criteria.get(row["id"], []),
                "reviewed_commit_sha": row["reviewed_commit_sha"],
                "repository_snapshot": (
                    _json_loads(row["repository_snapshot_json"])
                    if row["repository_snapshot_json"] is not None
                    else None
                ),
                "review_cohort_id": row["review_cohort_id"],
                "supersedes_review_request_id": row["supersedes_review_request_id"],
                "superseded_by_review_request_id": row["superseded_by_review_request_id"],
                "retry_reason": row["retry_reason"],
                "retry_requested_by": row["retry_requested_by"],
                "block_kind": row["block_kind"],
                "retired_at": row["retired_at"],
                "retired_by": row["retired_by"],
                "retirement_reason": row["retirement_reason"],
                "created_at": row["created_at"],
                "decision_at": row["decision_at"],
            }

    @staticmethod
    def _write_review_requests(connection: sqlite3.Connection, requests: dict[str, Any]) -> None:
        for request in requests.values():
            connection.execute(
                """
                INSERT INTO team_review_requests(
                    id, deliverable_id, release_package_id, candidate_commit_sha,
                    reviewer_agent_id, requested_by_agent_id, requested_by_actor,
                    state, review_profile_id, criteria_snapshot_json,
                    required_outcome_kind_definitions_json, instructions,
                    reviewed_commit_sha, repository_snapshot_json, review_cohort_id,
                    supersedes_review_request_id,
                    superseded_by_review_request_id, retry_reason,
                    retry_requested_by, block_kind, retired_at, retired_by,
                    retirement_reason, created_at, decision_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request["id"],
                    request.get("deliverable_id") or "",
                    request.get("release_package_id"),
                    request.get("candidate_commit_sha"),
                    request["reviewer_agent_id"],
                    request.get("requested_by_agent_id"),
                    request.get("requested_by_actor"),
                    request.get("state", "pending"),
                    request.get("review_profile_id"),
                    (
                        _json_dumps(request.get("criteria_snapshot"))
                        if request.get("criteria_snapshot") is not None
                        else None
                    ),
                    _json_dumps(request.get("required_outcome_kind_definitions", [])),
                    request.get("instructions"),
                    request.get("reviewed_commit_sha"),
                    (
                        _json_dumps(request.get("repository_snapshot"))
                        if request.get("repository_snapshot") is not None
                        else None
                    ),
                    request.get("review_cohort_id"),
                    request.get("supersedes_review_request_id"),
                    request.get("superseded_by_review_request_id"),
                    request.get("retry_reason"),
                    request.get("retry_requested_by"),
                    request.get("block_kind"),
                    request.get("retired_at"),
                    request.get("retired_by"),
                    request.get("retirement_reason"),
                    request.get("created_at"),
                    request.get("decision_at"),
                ),
            )
            _insert_position_values(
                connection,
                "team_review_request_findings",
                "review_request_id",
                request["id"],
                "finding",
                request.get("findings", []),
            )
            for position, criterion in enumerate(request.get("criteria", [])):
                connection.execute(
                    """
                    INSERT INTO team_review_request_criteria(
                        review_request_id, position, criterion_id, state, finding,
                        evidence_refs_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        request["id"],
                        position,
                        criterion["criterion_id"],
                        criterion["state"],
                        criterion.get("finding", ""),
                        _json_dumps(criterion.get("evidence_refs", [])),
                    ),
                )
