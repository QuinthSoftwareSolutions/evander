"""Team migrations membership."""

from __future__ import annotations

import sqlite3

from evander.models import utc_now

from .sqlite_codec import (
    _backfill_position_values,
    _ensure_column,
    _json_loads,
)


def _ensure_team_agents_nullable_membership(connection: sqlite3.Connection) -> None:
    columns = {
        str(row["name"]): row for row in connection.execute("PRAGMA table_info(team_agents)")
    }
    if not columns:
        return
    team_column = columns.get("team_id")
    role_column = columns.get("role_id")
    if (
        team_column is not None
        and role_column is not None
        and not int(team_column["notnull"] or 0)
        and not int(role_column["notnull"] or 0)
    ):
        return
    connection.execute("ALTER TABLE team_agents RENAME TO team_agents_notnull_legacy")
    connection.execute(
        """
        CREATE TABLE team_agents (
            id TEXT PRIMARY KEY,
            team_id TEXT,
            project_id TEXT,
            display_name TEXT,
            avatar_json TEXT,
            voice_profile_json TEXT,
            role_id TEXT,
            session_id TEXT NOT NULL,
            reports_to_agent_id TEXT,
            retired_team_id TEXT,
            max_ephemeral_concurrent INTEGER NOT NULL,
            status TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        INSERT INTO team_agents(
            id, team_id, project_id, display_name, avatar_json, voice_profile_json,
            role_id, session_id, reports_to_agent_id, retired_team_id,
            max_ephemeral_concurrent, status
        )
        SELECT
            id, team_id, project_id, display_name, avatar_json, voice_profile_json,
            role_id, session_id, reports_to_agent_id, NULL, max_ephemeral_concurrent, status
        FROM team_agents_notnull_legacy
        """
    )
    connection.execute("DROP TABLE team_agents_notnull_legacy")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_team_agents_status_scope "
        "ON team_agents(status, team_id, id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_team_agents_session ON team_agents(session_id)"
    )


def _ensure_project_assignments_from_legacy(connection: sqlite3.Connection) -> None:
    project_assignment_count = connection.execute(
        "SELECT COUNT(*) AS count FROM team_project_assignments"
    ).fetchone()["count"]
    if int(project_assignment_count or 0) > 0:
        return
    now = utc_now().isoformat()
    agent_columns = {
        str(row["name"]) for row in connection.execute("PRAGMA table_info(team_agents)")
    }
    has_agent_project_id = "project_id" in agent_columns
    for row in connection.execute(
        """
        SELECT p.id AS project_id, p.team_id, p.lead_agent_id, a.role_id
        FROM team_projects p
        LEFT JOIN team_agents a ON a.id = p.lead_agent_id
        ORDER BY p.id
        """
    ):
        assignment_id = f"{row['project_id']}__{row['lead_agent_id']}__project_lead"
        connection.execute(
            """
            INSERT OR IGNORE INTO team_project_assignments(
                id, team_id, project_id, agent_id, role_id, kind, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 'project_lead', ?, ?)
            """,
            (
                assignment_id,
                row["team_id"],
                row["project_id"],
                row["lead_agent_id"],
                row["role_id"],
                now,
                now,
            ),
        )
    if has_agent_project_id:
        for row in connection.execute(
            """
            SELECT id, team_id, project_id, role_id
            FROM team_agents
            WHERE project_id IS NOT NULL AND trim(project_id) <> ''
            ORDER BY id
            """
        ):
            assignment_id = f"{row['project_id']}__{row['id']}__project_member"
            connection.execute(
                """
                INSERT OR IGNORE INTO team_project_assignments(
                    id, team_id, project_id, agent_id, role_id, kind, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 'project_member', ?, ?)
                """,
                (
                    assignment_id,
                    row["team_id"],
                    row["project_id"],
                    row["id"],
                    row["role_id"],
                    now,
                    now,
                ),
            )


def _ensure_team_conversations_operator_scope(connection: sqlite3.Connection) -> None:
    columns = {
        str(row["name"]): row for row in connection.execute("PRAGMA table_info(team_conversations)")
    }
    if not columns:
        return
    has_operator_columns = "operator_user_id" in columns and "operator_display_name" in columns
    needs_rebuild = not has_operator_columns or _team_conversations_has_agent_unique_index(
        connection
    )
    if not needs_rebuild:
        connection.execute(
            """
            UPDATE team_conversations
            SET operator_user_id = lower(trim(
                COALESCE(NULLIF(trim(operator_user_id), ''), 'admin')
            ))
            WHERE operator_user_id IS NULL
               OR trim(operator_user_id) = ''
               OR operator_user_id <> lower(trim(operator_user_id))
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_team_conversations_agent_operator
            ON team_conversations(agent_id, operator_user_id)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_team_conversations_operator_recent
            ON team_conversations(operator_user_id, last_interaction_at DESC, id)
            """
        )
        return

    connection.execute("ALTER TABLE team_conversations RENAME TO team_conversations_legacy")
    connection.execute(
        """
        CREATE TABLE team_conversations (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            operator_user_id TEXT NOT NULL,
            operator_display_name TEXT,
            created_by TEXT,
            last_message_preview TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_interaction_at TEXT NOT NULL
        )
        """
    )
    legacy_columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(team_conversations_legacy)")
    }
    legacy_operator_user_expr = (
        "COALESCE(NULLIF(trim(operator_user_id), ''), "
        "CASE "
        "WHEN trim(created_by) LIKE 'operator:%' THEN substr(trim(created_by), 10) "
        "WHEN created_by IS NOT NULL AND trim(created_by) <> '' THEN trim(created_by) "
        "ELSE 'admin' END)"
        if "operator_user_id" in legacy_columns
        else (
            "CASE "
            "WHEN trim(created_by) LIKE 'operator:%' THEN substr(trim(created_by), 10) "
            "WHEN created_by IS NOT NULL AND trim(created_by) <> '' THEN trim(created_by) "
            "ELSE 'admin' END"
        )
    )
    operator_user_expr = f"lower(trim({legacy_operator_user_expr}))"
    operator_display_expr = (
        "operator_display_name" if "operator_display_name" in legacy_columns else "NULL"
    )
    connection.execute(
        f"""
        INSERT OR IGNORE INTO team_conversations(
            id, agent_id, operator_user_id, operator_display_name, created_by,
            last_message_preview, created_at, updated_at, last_interaction_at
        )
        SELECT
            id,
            agent_id,
            {operator_user_expr},
            {operator_display_expr},
            created_by,
            last_message_preview,
            created_at,
            updated_at,
            last_interaction_at
        FROM team_conversations_legacy
        """
    )
    connection.execute("DROP TABLE team_conversations_legacy")
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_team_conversations_recent
        ON team_conversations(last_interaction_at DESC, id)
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_team_conversations_agent_operator
        ON team_conversations(agent_id, operator_user_id)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_team_conversations_operator_recent
        ON team_conversations(operator_user_id, last_interaction_at DESC, id)
        """
    )


def _team_conversations_has_agent_unique_index(connection: sqlite3.Connection) -> bool:
    for index in connection.execute("PRAGMA index_list(team_conversations)"):
        if not int(index["unique"] or 0):
            continue
        index_name = str(index["name"])
        columns = [
            str(row["name"]) for row in connection.execute(f"PRAGMA index_info({index_name})")
        ]
        if columns == ["agent_id"]:
            return True
    return False


def _migrate_human_review_relationships(connection: sqlite3.Connection) -> None:
    """Backfill normalized human-review relationships from the v33 payload rows."""

    for row in connection.execute(
        "SELECT id, payload_json FROM team_human_review_requests ORDER BY id"
    ):
        request = _json_loads(row["payload_json"])
        requirement = request.get("requirement_snapshot") or {}
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
            _backfill_position_values(
                connection,
                table_name=table_name,
                owner_column="human_review_request_id",
                owner_id=row["id"],
                value_column=value_column,
                values=values,
            )

    for row in connection.execute(
        "SELECT id, payload_json FROM team_human_review_decisions ORDER BY id"
    ):
        decision = _json_loads(row["payload_json"])
        _backfill_position_values(
            connection,
            table_name="team_human_review_decision_confirmed_findings",
            owner_column="human_review_decision_id",
            owner_id=row["id"],
            value_column="finding_id",
            values=decision.get("confirmed_finding_ids", []),
        )

    for row in connection.execute(
        """
        SELECT id, resolved_by_human_review_request_id, payload_json
        FROM team_human_review_findings
        ORDER BY id
        """
    ):
        finding = _json_loads(row["payload_json"])
        if row["resolved_by_human_review_request_id"] is None:
            resolved_by_request_id = finding.get("resolved_by_human_review_request_id")
            if resolved_by_request_id:
                connection.execute(
                    """
                    UPDATE team_human_review_findings
                    SET resolved_by_human_review_request_id = ?
                    WHERE id = ?
                    """,
                    (resolved_by_request_id, row["id"]),
                )
        for table_name, values in (
            (
                "team_human_review_finding_targets",
                finding.get("target_deliverable_ids", []),
            ),
            (
                "team_human_review_finding_proposals",
                finding.get("proposed_by_deliverable_ids", []),
            ),
        ):
            _backfill_position_values(
                connection,
                table_name=table_name,
                owner_column="finding_id",
                owner_id=row["id"],
                value_column="deliverable_id",
                values=values,
            )


def _migrate_role_task_queue(connection: sqlite3.Connection) -> None:
    columns = {row["name"]: row for row in connection.execute("PRAGMA table_info(team_tasks)")}
    if not columns["owner_agent_id"]["notnull"] and {
        "assignment_mode",
        "assignment_role_id",
        "assignment_started_at",
    }.issubset(columns):
        return
    connection.execute("SAVEPOINT role_task_queue_migration")
    try:
        _migrate_role_task_queue_columns(connection)
    except Exception:
        connection.execute("ROLLBACK TO role_task_queue_migration")
        raise
    finally:
        connection.execute("RELEASE role_task_queue_migration")


def _migrate_role_task_queue_columns(connection: sqlite3.Connection) -> None:
    columns = list(connection.execute("PRAGMA table_info(team_tasks)"))
    owner = next(row for row in columns if row["name"] == "owner_agent_id")
    if owner["notnull"]:
        sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='team_tasks'"
        ).fetchone()[0]
        indexes = [
            row[0]
            for row in connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='team_tasks' AND sql IS NOT NULL"
            )
        ]
        sql = sql.replace("team_tasks", "team_tasks_role_queue", 1)
        sql = sql.replace("owner_agent_id TEXT NOT NULL", "owner_agent_id TEXT")
        connection.execute(sql)
        connection.execute("INSERT INTO team_tasks_role_queue SELECT * FROM team_tasks")
        connection.execute("DROP TABLE team_tasks")
        connection.execute("ALTER TABLE team_tasks_role_queue RENAME TO team_tasks")
        for index in indexes:
            connection.execute(index)
    for name, definition in (
        ("assignment_mode", "TEXT NOT NULL DEFAULT 'agent'"),
        ("assignment_role_id", "TEXT"),
        ("assignment_started_at", "TEXT"),
    ):
        _ensure_column(connection, table_name="team_tasks", column_name=name, definition=definition)
