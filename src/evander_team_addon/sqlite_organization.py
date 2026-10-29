"""Team organization."""

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
    _optional_text,
)


class OrganizationStoreMixin:
    """Read/write organization rows on a caller-owned transaction."""

    @staticmethod
    def _read_teams(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        project_ids = _grouped_values(
            connection,
            "SELECT team_id AS owner_id, project_id AS value FROM team_team_projects ORDER BY team_id, position",
        )
        for row in connection.execute("SELECT * FROM team_teams ORDER BY id"):
            payload["teams"][row["id"]] = {
                "id": row["id"],
                "name": row["name"],
                "manager_agent_id": row["manager_agent_id"],
                "operator_communication_target_id": row["operator_communication_target_id"],
                "max_cost_usd": row["max_cost_usd"],
                "cost_warning_ratio": row["cost_warning_ratio"],
                "cost_budget_renewal_mode": row["cost_budget_renewal_mode"],
                "cost_budget_windows": _json_loads(row["cost_budget_windows_json"] or "[]"),
                "escalate_to_governance": _bool(row["escalate_to_governance"]),
                "project_ids": project_ids.get(row["id"], []),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "archived": _bool(row["archived"]),
            }

    @staticmethod
    def _write_teams(connection: sqlite3.Connection, teams: dict[str, Any]) -> None:
        for team in teams.values():
            connection.execute(
                """
                INSERT INTO team_teams(
                    id, name, manager_agent_id, operator_communication_target_id,
                    max_cost_usd, cost_warning_ratio, cost_budget_renewal_mode,
                    cost_budget_windows_json, escalate_to_governance, created_at, updated_at, archived
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    team["id"],
                    team["name"],
                    team["manager_agent_id"],
                    team.get("operator_communication_target_id"),
                    _optional_text(team.get("max_cost_usd")),
                    _optional_text(team.get("cost_warning_ratio")) or "0.8",
                    _optional_text(team.get("cost_budget_renewal_mode")) or "manual",
                    _json_dumps(team.get("cost_budget_windows") or []),
                    _int_bool(team.get("escalate_to_governance")),
                    team["created_at"],
                    team["updated_at"],
                    _int_bool(team.get("archived")),
                ),
            )
            _insert_position_values(
                connection,
                "team_team_projects",
                "team_id",
                team["id"],
                "project_id",
                team.get("project_ids", []),
            )

    @staticmethod
    def _read_governance_groups(
        connection: sqlite3.Connection,
        payload: dict[str, Any],
    ) -> None:
        member_team_ids = _grouped_values(
            connection,
            """
            SELECT governance_group_id AS owner_id, team_id AS value
            FROM team_governance_group_members
            ORDER BY governance_group_id, position
            """,
        )
        goals = _grouped_rows(
            connection,
            """
            SELECT governance_group_id AS owner_id, goal_id, goal_name, description, state
            FROM team_governance_group_goals
            ORDER BY governance_group_id, position
            """,
            lambda row: {
                "id": row["goal_id"],
                "name": row["goal_name"] or row["goal_id"],
                "description": row["description"],
                "state": row["state"],
            },
        )
        for row in connection.execute("SELECT * FROM team_governance_groups ORDER BY id"):
            payload["governance_groups"][row["id"]] = {
                "id": row["id"],
                "name": row["name"],
                "manager_agent_id": row["manager_agent_id"],
                "member_team_ids": member_team_ids.get(row["id"], []),
                "goals": goals.get(row["id"], []),
                "max_cost_usd": row["max_cost_usd"],
                "archived": _bool(row["archived"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_governance_groups(
        connection: sqlite3.Connection,
        groups: dict[str, Any],
    ) -> None:
        for group in groups.values():
            connection.execute(
                """
                INSERT INTO team_governance_groups(
                    id, name, manager_agent_id, max_cost_usd, archived,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    group["id"],
                    group["name"],
                    group["manager_agent_id"],
                    _optional_text(group.get("max_cost_usd")),
                    _int_bool(group.get("archived")),
                    group["created_at"],
                    group["updated_at"],
                ),
            )
            _insert_position_values(
                connection,
                "team_governance_group_members",
                "governance_group_id",
                group["id"],
                "team_id",
                group.get("member_team_ids", []),
            )
            for position, goal in enumerate(group.get("goals", [])):
                connection.execute(
                    """
                    INSERT INTO team_governance_group_goals(
                        governance_group_id, position, goal_id, goal_name, description, state
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        group["id"],
                        position,
                        goal["id"],
                        goal["name"],
                        goal["description"],
                        goal.get("state", "pending"),
                    ),
                )

    @staticmethod
    def _read_governance_agenda(
        connection: sqlite3.Connection,
        payload: dict[str, Any],
    ) -> None:
        for row in connection.execute("SELECT * FROM team_governance_agenda ORDER BY id"):
            payload["governance_agenda"][row["id"]] = {
                "id": row["id"],
                "governance_group_id": row["governance_group_id"],
                "item_type": row["item_type"],
                "title": row["title"],
                "summary": row["summary"],
                "team_id": row["team_id"],
                "project_id": row["project_id"],
                "agent_id": row["agent_id"],
                "payload": _json_loads(row["payload_json"]) if row["payload_json"] else {},
                "state": row["state"],
                "priority": row["priority"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_governance_agenda(
        connection: sqlite3.Connection,
        agenda: dict[str, Any],
    ) -> None:
        for item in agenda.values():
            connection.execute(
                """
                INSERT INTO team_governance_agenda(
                    id, governance_group_id, item_type, title, summary,
                    team_id, project_id, agent_id, payload_json, state, priority,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["id"],
                    item["governance_group_id"],
                    item["item_type"],
                    item["title"],
                    item["summary"],
                    item.get("team_id"),
                    item.get("project_id"),
                    item.get("agent_id"),
                    _json_dumps(item.get("payload") or {}),
                    item.get("state", "pending"),
                    item.get("priority", "normal"),
                    item["created_at"],
                    item["updated_at"],
                ),
            )

    @staticmethod
    def _read_governance_runtimes(
        connection: sqlite3.Connection,
        payload: dict[str, Any],
    ) -> None:
        for row in connection.execute(
            "SELECT * FROM team_governance_runtimes ORDER BY governance_group_id"
        ):
            payload["governance_runtimes"][row["governance_group_id"]] = {
                "governance_group_id": row["governance_group_id"],
                "wake_leased_until": row["wake_leased_until"],
                "active_dispatch_id": row["active_dispatch_id"],
                "preparation_expires_at": row["preparation_expires_at"],
                "queued_at": row["queued_at"],
                "requested_lease_seconds": row["requested_lease_seconds"],
                "wake_reason": row["wake_reason"],
                "last_wake_at": row["last_wake_at"],
                "next_wake_at": row["next_wake_at"],
                "last_cost_signal_fingerprints": (
                    _json_loads(row["last_cost_signal_fingerprints_json"])
                    if row["last_cost_signal_fingerprints_json"]
                    else []
                ),
                "last_blocked_signal_fingerprints": (
                    _json_loads(row["last_blocked_signal_fingerprints_json"])
                    if row["last_blocked_signal_fingerprints_json"]
                    else []
                ),
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_governance_runtimes(
        connection: sqlite3.Connection,
        runtimes: dict[str, Any],
    ) -> None:
        for runtime in runtimes.values():
            connection.execute(
                """
                INSERT INTO team_governance_runtimes(
                    governance_group_id, wake_leased_until, active_dispatch_id,
                    preparation_expires_at, queued_at, requested_lease_seconds,
                    wake_reason, last_wake_at, next_wake_at,
                    last_cost_signal_fingerprints_json,
                    last_blocked_signal_fingerprints_json,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    runtime["governance_group_id"],
                    runtime.get("wake_leased_until"),
                    runtime.get("active_dispatch_id"),
                    runtime.get("preparation_expires_at"),
                    runtime.get("queued_at"),
                    runtime.get("requested_lease_seconds", 900),
                    runtime.get("wake_reason"),
                    runtime.get("last_wake_at"),
                    runtime.get("next_wake_at"),
                    _json_dumps(runtime.get("last_cost_signal_fingerprints") or []),
                    _json_dumps(runtime.get("last_blocked_signal_fingerprints") or []),
                    runtime["updated_at"],
                ),
            )

    @staticmethod
    def _read_roles(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        permissions = _grouped_values(
            connection,
            "SELECT role_id AS owner_id, permission AS value FROM team_role_permissions ORDER BY role_id, position",
        )
        for row in connection.execute("SELECT * FROM team_roles ORDER BY id"):
            payload["roles"][row["id"]] = {
                "id": row["id"],
                "team_id": row["team_id"],
                "project_id": row["project_id"],
                "name": row["name"],
                "description": row["description"],
                "permissions": permissions.get(row["id"], []),
                "ephemeral_policy": {"max_concurrent": row["max_ephemeral_concurrent"]},
                "archived": _bool(row["archived"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_roles(connection: sqlite3.Connection, roles: dict[str, Any]) -> None:
        for role in roles.values():
            connection.execute(
                """
                INSERT INTO team_roles(
                    id, team_id, project_id, name, description, max_ephemeral_concurrent,
                    archived, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    role["id"],
                    role["team_id"],
                    role.get("project_id"),
                    role["name"],
                    role.get("description", ""),
                    int(role.get("ephemeral_policy", {}).get("max_concurrent", 0)),
                    _int_bool(role.get("archived")),
                    role["created_at"],
                    role["updated_at"],
                ),
            )
            _insert_position_values(
                connection,
                "team_role_permissions",
                "role_id",
                role["id"],
                "permission",
                role.get("permissions", []),
            )

    @staticmethod
    def _read_projects(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        goals = _grouped_rows(
            connection,
            """
            SELECT project_id AS owner_id, goal_id, description, state
            FROM team_project_goals
            ORDER BY project_id, position
            """,
            lambda row: {
                "id": row["goal_id"],
                "description": row["description"],
                "state": row["state"],
            },
        )
        for row in connection.execute("SELECT * FROM team_projects ORDER BY id"):
            payload["projects"][row["id"]] = {
                "id": row["id"],
                "team_id": row["team_id"],
                "name": row["name"],
                "lead_agent_id": row["lead_agent_id"],
                "directory_path": row["directory_path"],
                "goals": goals.get(row["id"], []),
                "git": _json_loads(row["git_json"]) if row["git_json"] is not None else None,
                "release_policy": (
                    _json_loads(row["release_policy_json"])
                    if row["release_policy_json"] is not None
                    else None
                ),
                "status": row["status"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_projects(connection: sqlite3.Connection, projects: dict[str, Any]) -> None:
        for project in projects.values():
            connection.execute(
                """
                INSERT INTO team_projects(
                    id, team_id, name, lead_agent_id, directory_path, git_json,
                    release_policy_json, status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project["id"],
                    project["team_id"],
                    project["name"],
                    project["lead_agent_id"],
                    project.get("directory_path", "root"),
                    _json_dumps(project.get("git")) if project.get("git") is not None else None,
                    (
                        _json_dumps(project.get("release_policy"))
                        if project.get("release_policy") is not None
                        else None
                    ),
                    project.get("status", "active"),
                    project["created_at"],
                    project["updated_at"],
                ),
            )
            for position, goal in enumerate(project.get("goals", [])):
                connection.execute(
                    """
                    INSERT INTO team_project_goals(project_id, position, goal_id, description, state)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        project["id"],
                        position,
                        goal["id"],
                        goal["description"],
                        goal.get("state", "pending"),
                    ),
                )

    @staticmethod
    def _read_agents(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        subordinates = _grouped_values(
            connection,
            "SELECT agent_id AS owner_id, subordinate_agent_id AS value FROM team_agent_subordinates ORDER BY agent_id, position",
        )
        for row in connection.execute("SELECT * FROM team_agents ORDER BY id"):
            payload["agents"][row["id"]] = {
                "id": row["id"],
                "team_id": row["team_id"],
                "display_name": row["display_name"],
                "avatar": (
                    _json_loads(row["avatar_json"]) if row["avatar_json"] is not None else None
                ),
                "voice_profile": (
                    _json_loads(row["voice_profile_json"])
                    if row["voice_profile_json"] is not None
                    else None
                ),
                "role_id": row["role_id"],
                "session_id": row["session_id"],
                "reports_to_agent_id": row["reports_to_agent_id"],
                "retired_team_id": row["retired_team_id"],
                "subordinate_agent_ids": subordinates.get(row["id"], []),
                "ephemeral_policy": {"max_concurrent": row["max_ephemeral_concurrent"]},
                "status": row["status"],
            }

    @staticmethod
    def _write_agents(connection: sqlite3.Connection, agents: dict[str, Any]) -> None:
        for agent in agents.values():
            connection.execute(
                """
                INSERT INTO team_agents(
                    id, team_id, display_name, avatar_json, voice_profile_json,
                    role_id, session_id, reports_to_agent_id, retired_team_id,
                    max_ephemeral_concurrent, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent["id"],
                    agent["team_id"],
                    agent.get("display_name"),
                    _json_dumps(agent.get("avatar")) if agent.get("avatar") is not None else None,
                    (
                        _json_dumps(agent.get("voice_profile"))
                        if agent.get("voice_profile") is not None
                        else None
                    ),
                    agent["role_id"],
                    agent["session_id"],
                    agent.get("reports_to_agent_id"),
                    agent.get("retired_team_id"),
                    int(agent.get("ephemeral_policy", {}).get("max_concurrent", 0)),
                    agent.get("status", "active"),
                ),
            )
            _insert_position_values(
                connection,
                "team_agent_subordinates",
                "agent_id",
                agent["id"],
                "subordinate_agent_id",
                agent.get("subordinate_agent_ids", []),
            )

    @staticmethod
    def _read_project_assignments(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        for row in connection.execute("SELECT * FROM team_project_assignments ORDER BY id"):
            payload["project_assignments"][row["id"]] = {
                "id": row["id"],
                "team_id": row["team_id"],
                "project_id": row["project_id"],
                "agent_id": row["agent_id"],
                "role_id": row["role_id"],
                "kind": row["kind"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_project_assignments(
        connection: sqlite3.Connection,
        assignments: dict[str, Any],
    ) -> None:
        for assignment in assignments.values():
            connection.execute(
                """
                INSERT INTO team_project_assignments(
                    id, team_id, project_id, agent_id, role_id, kind, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    assignment["id"],
                    assignment["team_id"],
                    assignment["project_id"],
                    assignment["agent_id"],
                    assignment.get("role_id"),
                    assignment.get("kind", "project_member"),
                    assignment["created_at"],
                    assignment["updated_at"],
                ),
            )

    @staticmethod
    def _read_stand_downs(
        connection: sqlite3.Connection,
        payload: dict[str, Any],
    ) -> None:
        for row in connection.execute("SELECT * FROM team_agent_stand_downs ORDER BY id"):
            payload["stand_downs"][row["id"]] = {
                "id": row["id"],
                "agent_id": row["agent_id"],
                "scope": row["scope"],
                "reason": row["reason"],
                "source": row["source"],
                "set_by": row["set_by"],
                "set_at": row["set_at"],
                "cleared_by": row["cleared_by"],
                "cleared_at": row["cleared_at"],
            }

    @staticmethod
    def _write_stand_downs(
        connection: sqlite3.Connection,
        stand_downs: dict[str, Any],
    ) -> None:
        for stand_down in stand_downs.values():
            connection.execute(
                """
                INSERT INTO team_agent_stand_downs(
                    id, agent_id, scope, reason, source, set_by, set_at,
                    cleared_by, cleared_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stand_down["id"],
                    stand_down["agent_id"],
                    stand_down.get("scope", "subtree"),
                    stand_down["reason"],
                    stand_down.get("source", "operator"),
                    stand_down["set_by"],
                    stand_down["set_at"],
                    stand_down.get("cleared_by"),
                    stand_down.get("cleared_at"),
                ),
            )

    @staticmethod
    def _read_agent_runtimes(
        connection: sqlite3.Connection,
        payload: dict[str, Any],
        *,
        agent_ids: tuple[str, ...] | None = None,
    ) -> None:
        parameters = agent_ids or ()
        placeholders = ",".join("?" for _ in parameters)
        where = f" WHERE agent_id IN ({placeholders})" if agent_ids is not None else ""
        for row in connection.execute(
            f"SELECT * FROM team_agent_runtimes{where} ORDER BY agent_id", parameters
        ):
            payload["agent_runtimes"][row["agent_id"]] = {
                "agent_id": row["agent_id"],
                "wake_leased_until": row["wake_leased_until"],
                "active_dispatch_id": row["active_dispatch_id"],
                "wake_reason": row["wake_reason"],
                "last_wake_at": row["last_wake_at"],
                "next_wake_at": row["next_wake_at"],
                "no_progress_count": row["no_progress_count"],
                "last_progress_at": row["last_progress_at"],
                "low_priority_batch_started_at": row["low_priority_batch_started_at"],
                "low_priority_batch_cycle_count": row["low_priority_batch_cycle_count"],
                "low_priority_batch_due_after_cycles": row["low_priority_batch_due_after_cycles"],
                "context_clearing_policy": row["context_clearing_policy"],
                "context_clearing_idle_grace_seconds": row["context_clearing_idle_grace_seconds"],
                "context_idle_since_at": row["context_idle_since_at"],
                "context_idle_rotation_due_at": row["context_idle_rotation_due_at"],
                "auto_context_clearing_enabled": _bool(row["auto_context_clearing_enabled"]),
                "context_rotation_pending": _bool(row["context_rotation_pending"]),
                "context_rotation_requested_by": row["context_rotation_requested_by"],
                "context_rotation_requested_at": row["context_rotation_requested_at"],
                "context_rotation_reason": row["context_rotation_reason"],
                "context_rotation_request_note": row["context_rotation_request_note"],
                "last_handoff_summary": row["last_handoff_summary"],
                "context_handoff_preparation_pending": _bool(
                    row["context_handoff_preparation_pending"]
                ),
                "context_handoff_preparation_status": row["context_handoff_preparation_status"],
                "last_agent_handoff_summary": row["last_agent_handoff_summary"],
                "last_agent_handoff_at": row["last_agent_handoff_at"],
                "last_context_rotated_at": row["last_context_rotated_at"],
                "last_thread_id": row["last_thread_id"],
                "work_lineage_thread_ids": _json_loads(row["work_lineage_thread_ids_json"] or "{}"),
                "work_lineage_thread_dispatch_ids": _json_loads(
                    row["work_lineage_thread_dispatch_ids_json"] or "{}"
                ),
                "last_delivered_role_description_fingerprint": row[
                    "last_delivered_role_description_fingerprint"
                ],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_agent_runtimes(connection: sqlite3.Connection, runtimes: dict[str, Any]) -> None:
        for runtime in runtimes.values():
            connection.execute(
                """
                INSERT INTO team_agent_runtimes(
                    agent_id, wake_leased_until, active_dispatch_id, wake_reason,
                    last_wake_at, next_wake_at, no_progress_count, last_progress_at,
                    low_priority_batch_started_at, low_priority_batch_cycle_count,
                    low_priority_batch_due_after_cycles,
                    context_clearing_policy, context_clearing_idle_grace_seconds,
                    context_idle_since_at, context_idle_rotation_due_at,
                    auto_context_clearing_enabled, context_rotation_pending,
                    context_rotation_requested_by, context_rotation_requested_at,
                    context_rotation_reason, context_rotation_request_note,
                    last_handoff_summary, context_handoff_preparation_pending,
                    context_handoff_preparation_status, last_agent_handoff_summary,
                    last_agent_handoff_at, last_context_rotated_at, last_thread_id,
                    last_delivered_role_description_fingerprint,
                    work_lineage_thread_ids_json, work_lineage_thread_dispatch_ids_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    runtime["agent_id"],
                    runtime.get("wake_leased_until"),
                    runtime.get("active_dispatch_id"),
                    runtime.get("wake_reason"),
                    runtime.get("last_wake_at"),
                    runtime.get("next_wake_at"),
                    int(runtime.get("no_progress_count", 0)),
                    runtime.get("last_progress_at"),
                    runtime.get("low_priority_batch_started_at"),
                    int(runtime.get("low_priority_batch_cycle_count", 0)),
                    runtime.get("low_priority_batch_due_after_cycles"),
                    runtime.get("context_clearing_policy", "off"),
                    int(runtime.get("context_clearing_idle_grace_seconds", 0)),
                    runtime.get("context_idle_since_at"),
                    runtime.get("context_idle_rotation_due_at"),
                    _int_bool(runtime.get("auto_context_clearing_enabled")),
                    _int_bool(runtime.get("context_rotation_pending")),
                    runtime.get("context_rotation_requested_by"),
                    runtime.get("context_rotation_requested_at"),
                    runtime.get("context_rotation_reason"),
                    runtime.get("context_rotation_request_note"),
                    runtime.get("last_handoff_summary"),
                    _int_bool(runtime.get("context_handoff_preparation_pending")),
                    runtime.get("context_handoff_preparation_status"),
                    runtime.get("last_agent_handoff_summary"),
                    runtime.get("last_agent_handoff_at"),
                    runtime.get("last_context_rotated_at"),
                    runtime.get("last_thread_id"),
                    runtime.get("last_delivered_role_description_fingerprint"),
                    _json_dumps(runtime.get("work_lineage_thread_ids", {})),
                    _json_dumps(runtime.get("work_lineage_thread_dispatch_ids", {})),
                    runtime["updated_at"],
                ),
            )
