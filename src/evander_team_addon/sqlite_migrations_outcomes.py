"""Team migrations outcomes."""

from __future__ import annotations

import hashlib
import sqlite3

from .sqlite_codec import (
    _json_dumps,
    _json_loads,
)

_KNOWN_OUTCOME_KIND_MIGRATIONS: dict[str, dict[str, str]] = {
    "evidence_match": {
        "name": "Evidence Match",
        "description": (
            "A durable source-to-Bokio match or an explicit unmatched or blocked disposition."
        ),
        "reference_requirements": (
            "Reference the source record identifiers and matching Bokio "
            "transaction or evidence identifiers. Unmatched work must reference "
            "its durable case or blocker."
        ),
        "evidence_requirements": (
            "Cite the amount, date, currency, counterparty, and account match "
            "basis together with durable Bokio duplicate-check evidence."
        ),
    },
    "bokio_prepared_action": {
        "name": "Bokio Prepared Action",
        "description": (
            "A prepared-only Bokio bookkeeping action ready for independent "
            "review and human approval; it does not assert that the action was "
            "approved or posted."
        ),
        "reference_requirements": (
            "Reference the prepared Bokio supplier invoice, journal entry, or "
            "bank payment identifier."
        ),
        "evidence_requirements": (
            "Cite supporting documents, Bokio account and counterparty mapping, "
            "tax treatment, and the applicable human approval boundary."
        ),
    },
}


def _migrate_legacy_outcome_kind_definitions(
    connection: sqlite3.Connection,
) -> None:
    profiles = connection.execute(
        """
        SELECT id, team_id, project_id, name, description,
               required_outcome_kinds_json, created_at, updated_at
        FROM team_review_profiles
        ORDER BY id
        """
    ).fetchall()
    for profile in profiles:
        kinds = _json_loads(profile["required_outcome_kinds_json"])
        criteria_rows = connection.execute(
            """
            SELECT label, description
            FROM team_review_profile_criteria
            WHERE profile_id = ?
            ORDER BY position
            """,
            (profile["id"],),
        ).fetchall()
        criteria_guidance = "; ".join(
            str(row["description"] or row["label"])
            for row in criteria_rows
            if row["description"] or row["label"]
        )
        for raw_kind in kinds:
            kind = str(raw_kind).strip()
            if not kind:
                continue
            existing = connection.execute(
                """
                SELECT id
                FROM team_outcome_kind_definitions
                WHERE team_id = ?
                  AND COALESCE(project_id, '') = COALESCE(?, '')
                  AND kind = ?
                """,
                (profile["team_id"], profile["project_id"], kind),
            ).fetchone()
            if existing is not None:
                continue
            known = _KNOWN_OUTCOME_KIND_MIGRATIONS.get(kind)
            name = (
                known["name"]
                if known is not None
                else kind.replace("_", " ").replace("-", " ").title()
            )
            description = (
                known["description"]
                if known is not None
                else str(profile["description"] or profile["name"])
            )
            reference_requirements = (
                known["reference_requirements"]
                if known is not None
                else f"Provide durable reference IDs for {name}."
            )
            evidence_requirements = (
                known["evidence_requirements"] if known is not None else criteria_guidance
            )
            digest = hashlib.sha256(
                (f"{profile['team_id']}:{profile['project_id'] or ''}:{kind}").encode("utf-8")
            ).hexdigest()[:16]
            connection.execute(
                """
                INSERT INTO team_outcome_kind_definitions(
                    id, team_id, project_id, kind, name, description,
                    reference_requirements, evidence_requirements, archived,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    f"outcome-kind-{digest}",
                    profile["team_id"],
                    profile["project_id"],
                    kind,
                    name,
                    description,
                    reference_requirements,
                    evidence_requirements,
                    profile["created_at"],
                    profile["updated_at"],
                ),
            )

    def ensure_generic_definition(
        *,
        team_id: str,
        project_id: str | None,
        kind: str,
        created_at: str,
        updated_at: str,
    ) -> None:
        existing = connection.execute(
            """
            SELECT id
            FROM team_outcome_kind_definitions
            WHERE team_id = ?
              AND kind = ?
              AND (project_id = ? OR project_id IS NULL)
            ORDER BY CASE WHEN project_id = ? THEN 0 ELSE 1 END, id
            LIMIT 1
            """,
            (team_id, kind, project_id, project_id),
        ).fetchone()
        if existing is not None:
            return
        known = _KNOWN_OUTCOME_KIND_MIGRATIONS.get(kind)
        name = (
            known["name"] if known is not None else kind.replace("_", " ").replace("-", " ").title()
        )
        description = (
            known["description"]
            if known is not None
            else f"Migrated definition for the {name} outcome kind."
        )
        reference_requirements = (
            known["reference_requirements"]
            if known is not None
            else f"Provide durable reference IDs for {name}."
        )
        evidence_requirements = (
            known["evidence_requirements"]
            if known is not None
            else "Provide evidence sufficient to verify the claimed outcome."
        )
        digest = hashlib.sha256(f"{team_id}:{project_id or ''}:{kind}".encode("utf-8")).hexdigest()[
            :16
        ]
        connection.execute(
            """
            INSERT INTO team_outcome_kind_definitions(
                id, team_id, project_id, kind, name, description,
                reference_requirements, evidence_requirements, archived,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                f"outcome-kind-{digest}",
                team_id,
                project_id,
                kind,
                name,
                description,
                reference_requirements,
                evidence_requirements,
                created_at,
                updated_at,
            ),
        )

    task_source_rows = connection.execute(
        """
        SELECT task.id, task.project_id, task.outcome_contract_json,
               task.created_at, task.updated_at, project.team_id
        FROM team_tasks AS task
        JOIN team_projects AS project ON project.id = task.project_id
        WHERE task.outcome_contract_json IS NOT NULL
        """
    ).fetchall()
    for row in task_source_rows:
        contract = _json_loads(row["outcome_contract_json"])
        kinds = list(contract.get("required_outcome_kinds", [])) + list(
            contract.get(
                "forbidden_outcome_kinds",
                contract.get("forbidden_effect_kinds", []),
            )
        )
        for raw_kind in kinds:
            kind = str(raw_kind).strip()
            if kind:
                ensure_generic_definition(
                    team_id=row["team_id"],
                    project_id=row["project_id"],
                    kind=kind,
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )

    deliverable_source_rows = connection.execute(
        """
        SELECT deliverable.id, deliverable.outcomes_json,
               deliverable.created_at, task.project_id, task.updated_at,
               project.team_id
        FROM team_deliverables AS deliverable
        JOIN team_tasks AS task ON task.id = deliverable.task_id
        JOIN team_projects AS project ON project.id = task.project_id
        WHERE deliverable.outcomes_json <> '[]'
        """
    ).fetchall()
    for row in deliverable_source_rows:
        for outcome in _json_loads(row["outcomes_json"]):
            if not isinstance(outcome, dict):
                continue
            kind = str(outcome.get("kind") or "").strip()
            if kind:
                ensure_generic_definition(
                    team_id=row["team_id"],
                    project_id=row["project_id"],
                    kind=kind,
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )

    def snapshot_for(
        *,
        team_id: str,
        project_id: str | None,
        kind: str,
    ) -> dict[str, str] | None:
        row = connection.execute(
            """
            SELECT kind, name, description, reference_requirements,
                   evidence_requirements
            FROM team_outcome_kind_definitions
            WHERE team_id = ?
              AND kind = ?
              AND (project_id = ? OR project_id IS NULL)
            ORDER BY CASE WHEN project_id = ? THEN 0 ELSE 1 END, id
            LIMIT 1
            """,
            (team_id, kind, project_id, project_id),
        ).fetchone()
        return (
            {
                "kind": row["kind"],
                "name": row["name"],
                "description": row["description"],
                "reference_requirements": row["reference_requirements"],
                "evidence_requirements": row["evidence_requirements"],
            }
            if row is not None
            else None
        )

    for row in task_source_rows:
        contract = _json_loads(row["outcome_contract_json"])
        if "forbidden_effect_kinds" in contract:
            contract["forbidden_outcome_kinds"] = contract.pop("forbidden_effect_kinds")
        kinds = list(contract.get("required_outcome_kinds", [])) + list(
            contract.get("forbidden_outcome_kinds", [])
        )
        contract["outcome_kind_definitions"] = [
            snapshot
            for kind in kinds
            if (
                snapshot := snapshot_for(
                    team_id=row["team_id"],
                    project_id=row["project_id"],
                    kind=str(kind),
                )
            )
            is not None
        ]
        connection.execute(
            "UPDATE team_tasks SET outcome_contract_json = ? WHERE id = ?",
            (_json_dumps(contract), row["id"]),
        )

    for row in deliverable_source_rows:
        outcomes = _json_loads(row["outcomes_json"])
        for outcome in outcomes:
            if not isinstance(outcome, dict):
                continue
            kind = str(outcome.get("kind") or "").strip()
            if not kind:
                continue
            outcome["definition_snapshot"] = snapshot_for(
                team_id=row["team_id"],
                project_id=row["project_id"],
                kind=kind,
            )
        connection.execute(
            "UPDATE team_deliverables SET outcomes_json = ? WHERE id = ?",
            (_json_dumps(outcomes), row["id"]),
        )

    review_rows = connection.execute(
        """
        SELECT review.id, profile.team_id,
               COALESCE(
                   task.project_id,
                   release.project_id,
                   profile.project_id
               ) AS reviewed_project_id,
               profile.required_outcome_kinds_json
        FROM team_review_requests AS review
        JOIN team_review_profiles AS profile
          ON profile.id = review.review_profile_id
        LEFT JOIN team_deliverables AS deliverable
          ON deliverable.id = review.deliverable_id
        LEFT JOIN team_tasks AS task
          ON task.id = deliverable.task_id
        LEFT JOIN team_release_packages AS release
          ON release.id = review.release_package_id
        """
    ).fetchall()
    for row in review_rows:
        snapshots = [
            snapshot
            for kind in _json_loads(row["required_outcome_kinds_json"])
            if (
                snapshot := snapshot_for(
                    team_id=row["team_id"],
                    project_id=row["reviewed_project_id"],
                    kind=str(kind),
                )
            )
            is not None
        ]
        connection.execute(
            """
            UPDATE team_review_requests
            SET required_outcome_kind_definitions_json = ?
            WHERE id = ?
            """,
            (_json_dumps(snapshots), row["id"]),
        )
