from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from threading import RLock
from typing import Any, Callable, TypeVar

from agent_sandbox.addons import addon_storage_path
from agent_sandbox.models import utc_now
from agent_sandbox.repositories.orchestration_db import SQLITE_BUSY_TIMEOUT_MS

from .models import SCHEMA_VERSION, TeamState

ADDON_ID = "team-addon"
STATE_DB_FILE = "state.db"
LEGACY_STATE_FILE = "state.json"
SQLITE_SCHEMA_VERSION = 1
_SQLITE_WAL_ENABLE_TIMEOUT_SECONDS = 5.0

T = TypeVar("T")


class TeamStateSqliteStore:
    def __init__(self, runtime_dir: str | Path) -> None:
        self.runtime_dir = Path(runtime_dir)
        self.path = addon_storage_path(self.runtime_dir, ADDON_ID, STATE_DB_FILE)
        self.legacy_json_path = addon_storage_path(
            self.runtime_dir, ADDON_ID, LEGACY_STATE_FILE
        )
        self._lock = RLock()
        self.ensure_schema()
        self._import_legacy_json_or_initialize()

    def read_object(self) -> dict[str, Any]:
        with self._lock:
            with self._connect() as connection:
                return self._read_payload(connection)

    def write_object(self, payload: dict[str, Any]) -> None:
        state = TeamState.model_validate(payload)
        with self._lock:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._write_state(connection, state)

    def update_object(
        self,
        updater: Callable[[dict[str, Any]], T | None],
    ) -> T | None:
        with self._lock:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                payload = self._read_payload(connection)
                result = updater(payload)
                state = TeamState.model_validate(payload)
                self._write_state(connection, state)
                return result

    def ensure_schema(self) -> None:
        with self._lock:
            with self._connect() as connection:
                connection.executescript(_SCHEMA_SQL)
                connection.execute(
                    """
                    INSERT OR IGNORE INTO team_state_metadata(key, value)
                    VALUES ('sqlite_schema_version', ?)
                    """,
                    (str(SQLITE_SCHEMA_VERSION),),
                )

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        self._enable_wal(connection)
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _enable_wal(connection: sqlite3.Connection) -> None:
        deadline = time.monotonic() + _SQLITE_WAL_ENABLE_TIMEOUT_SECONDS
        while True:
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).casefold() or time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)

    def _import_legacy_json_or_initialize(self) -> None:
        with self._lock:
            with self._connect() as connection:
                if self._has_state(connection):
                    return
                connection.execute("BEGIN IMMEDIATE")
                if self.legacy_json_path.is_file():
                    text = self.legacy_json_path.read_text(encoding="utf-8")
                    state = TeamState.model_validate(json.loads(text))
                    self._write_state(connection, state)
                    now = utc_now().isoformat()
                    connection.executemany(
                        """
                        INSERT INTO team_state_metadata(key, value)
                        VALUES (?, ?)
                        ON CONFLICT(key) DO UPDATE SET value = excluded.value
                        """,
                        (
                            ("legacy_json_imported_at", now),
                            ("legacy_json_path", str(self.legacy_json_path)),
                            (
                                "legacy_json_sha256",
                                hashlib.sha256(text.encode("utf-8")).hexdigest(),
                            ),
                        ),
                    )
                    return
                self._write_state(connection, TeamState.empty())

    @staticmethod
    def _has_state(connection: sqlite3.Connection) -> bool:
        row = connection.execute(
            "SELECT 1 FROM team_state_metadata WHERE key = 'revision'"
        ).fetchone()
        return row is not None

    def _read_payload(self, connection: sqlite3.Connection) -> dict[str, Any]:
        metadata = {
            str(row["key"]): str(row["value"])
            for row in connection.execute("SELECT key, value FROM team_state_metadata")
        }
        if "revision" not in metadata:
            return TeamState.empty().model_dump(mode="json")

        payload: dict[str, Any] = {
            "schema_version": int(metadata.get("state_schema_version", SCHEMA_VERSION)),
            "revision": int(metadata.get("revision", "0")),
            "updated_at": metadata.get("updated_at") or utc_now().isoformat(),
            "teams": {},
            "projects": {},
            "roles": {},
            "agents": {},
            "agent_runtimes": {},
            "tasks": {},
            "deliverables": {},
            "review_requests": {},
            "review_profiles": {},
            "inbox": {},
            "action_records": {},
            "release_packages": {},
            "ephemeral_assignments": {},
            "git_promotions": {},
        }
        self._read_teams(connection, payload)
        self._read_roles(connection, payload)
        self._read_projects(connection, payload)
        self._read_agents(connection, payload)
        self._read_agent_runtimes(connection, payload)
        self._read_tasks(connection, payload)
        self._read_deliverables(connection, payload)
        self._read_review_profiles(connection, payload)
        self._read_review_requests(connection, payload)
        self._read_inbox(connection, payload)
        self._read_action_records(connection, payload)
        self._read_release_packages(connection, payload)
        self._read_ephemeral_assignments(connection, payload)
        self._read_git_promotions(connection, payload)
        return TeamState.model_validate(payload).model_dump(mode="json")

    def _write_state(self, connection: sqlite3.Connection, state: TeamState) -> None:
        payload = state.model_dump(mode="json")
        self._clear_state_tables(connection)
        self._write_teams(connection, payload["teams"])
        self._write_roles(connection, payload["roles"])
        self._write_projects(connection, payload["projects"])
        self._write_agents(connection, payload["agents"])
        self._write_agent_runtimes(connection, payload["agent_runtimes"])
        self._write_tasks(connection, payload["tasks"])
        self._write_deliverables(connection, payload["deliverables"])
        self._write_review_profiles(connection, payload["review_profiles"])
        self._write_review_requests(connection, payload["review_requests"])
        self._write_inbox(connection, payload["inbox"])
        self._write_action_records(connection, payload["action_records"])
        self._write_release_packages(connection, payload["release_packages"])
        self._write_ephemeral_assignments(connection, payload["ephemeral_assignments"])
        self._write_git_promotions(connection, payload["git_promotions"])
        connection.executemany(
            """
            INSERT INTO team_state_metadata(key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (
                ("sqlite_schema_version", str(SQLITE_SCHEMA_VERSION)),
                ("state_schema_version", str(payload["schema_version"])),
                ("revision", str(payload["revision"])),
                ("updated_at", str(payload["updated_at"])),
            ),
        )

    @staticmethod
    def _clear_state_tables(connection: sqlite3.Connection) -> None:
        for table in _STATE_TABLES:
            connection.execute(f"DELETE FROM {table}")

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
                    max_cost_usd, created_at, updated_at, archived
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    team["id"],
                    team["name"],
                    team["manager_agent_id"],
                    team.get("operator_communication_target_id"),
                    _optional_text(team.get("max_cost_usd")),
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
                "goals": goals.get(row["id"], []),
                "git": _json_loads(row["git_json"]) if row["git_json"] is not None else None,
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
                    id, team_id, name, lead_agent_id, git_json, status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project["id"],
                    project["team_id"],
                    project["name"],
                    project["lead_agent_id"],
                    _json_dumps(project.get("git")) if project.get("git") is not None else None,
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
                "project_id": row["project_id"],
                "display_name": row["display_name"],
                "role_id": row["role_id"],
                "session_id": row["session_id"],
                "reports_to_agent_id": row["reports_to_agent_id"],
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
                    id, team_id, project_id, display_name, role_id, session_id,
                    reports_to_agent_id, max_ephemeral_concurrent, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent["id"],
                    agent["team_id"],
                    agent.get("project_id"),
                    agent.get("display_name"),
                    agent["role_id"],
                    agent["session_id"],
                    agent.get("reports_to_agent_id"),
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
    def _read_agent_runtimes(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        for row in connection.execute("SELECT * FROM team_agent_runtimes ORDER BY agent_id"):
            payload["agent_runtimes"][row["agent_id"]] = {
                "agent_id": row["agent_id"],
                "wake_leased_until": row["wake_leased_until"],
                "active_dispatch_id": row["active_dispatch_id"],
                "wake_reason": row["wake_reason"],
                "last_wake_at": row["last_wake_at"],
                "next_wake_at": row["next_wake_at"],
                "no_progress_count": row["no_progress_count"],
                "last_progress_at": row["last_progress_at"],
                "auto_context_clearing_enabled": _bool(row["auto_context_clearing_enabled"]),
                "context_rotation_pending": _bool(row["context_rotation_pending"]),
                "context_rotation_requested_by": row["context_rotation_requested_by"],
                "context_rotation_requested_at": row["context_rotation_requested_at"],
                "context_rotation_reason": row["context_rotation_reason"],
                "context_rotation_request_note": row["context_rotation_request_note"],
                "last_handoff_summary": row["last_handoff_summary"],
                "last_context_rotated_at": row["last_context_rotated_at"],
                "last_thread_id": row["last_thread_id"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_agent_runtimes(
        connection: sqlite3.Connection, runtimes: dict[str, Any]
    ) -> None:
        for runtime in runtimes.values():
            connection.execute(
                """
                INSERT INTO team_agent_runtimes(
                    agent_id, wake_leased_until, active_dispatch_id, wake_reason,
                    last_wake_at, next_wake_at, no_progress_count, last_progress_at,
                    auto_context_clearing_enabled, context_rotation_pending,
                    context_rotation_requested_by, context_rotation_requested_at,
                    context_rotation_reason, context_rotation_request_note,
                    last_handoff_summary, last_context_rotated_at, last_thread_id, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    _int_bool(runtime.get("auto_context_clearing_enabled")),
                    _int_bool(runtime.get("context_rotation_pending")),
                    runtime.get("context_rotation_requested_by"),
                    runtime.get("context_rotation_requested_at"),
                    runtime.get("context_rotation_reason"),
                    runtime.get("context_rotation_request_note"),
                    runtime.get("last_handoff_summary"),
                    runtime.get("last_context_rotated_at"),
                    runtime.get("last_thread_id"),
                    runtime["updated_at"],
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
                "project_id": row["project_id"],
                "priority": row["priority"],
                "state": row["state"],
                "title": row["title"],
                "description": row["description"],
                "created_by_agent_id": row["created_by_agent_id"],
                "created_by_actor": row["created_by_actor"],
                "parent_task_id": row["parent_task_id"],
                "dependency_task_ids": dependencies.get(row["id"], []),
                "deliverable_ids": deliverables.get(row["id"], []),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_tasks(connection: sqlite3.Connection, tasks: dict[str, Any]) -> None:
        for task in tasks.values():
            connection.execute(
                """
                INSERT INTO team_tasks(
                    id, owner_agent_id, project_id, priority, state, title, description,
                    created_by_agent_id, created_by_actor, parent_task_id, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task["id"],
                    task["owner_agent_id"],
                    task["project_id"],
                    int(task["priority"]),
                    task.get("state", "pending"),
                    task["title"],
                    task["description"],
                    task.get("created_by_agent_id"),
                    task.get("created_by_actor"),
                    task.get("parent_task_id"),
                    task["created_at"],
                    task["updated_at"],
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
        for row in connection.execute("SELECT * FROM team_deliverables ORDER BY id"):
            payload["deliverables"][row["id"]] = {
                "id": row["id"],
                "task_id": row["task_id"],
                "producer_agent_id": row["producer_agent_id"],
                "artifact_refs": artifacts.get(row["id"], []),
                "summary": row["summary"],
                "state": row["state"],
                "review_request_ids": reviews.get(row["id"], []),
                "git_refs": {
                    "agent_branch": row["agent_branch"],
                    "review_branch": row["review_branch"],
                    "feature_branch": row["feature_branch"],
                    "release_branch": row["release_branch"],
                    "commit_sha": row["commit_sha"],
                    "promotion_record_ids": promotions.get(row["id"], []),
                },
                "created_at": row["created_at"],
            }

    @staticmethod
    def _write_deliverables(
        connection: sqlite3.Connection, deliverables: dict[str, Any]
    ) -> None:
        for deliverable in deliverables.values():
            git_refs = deliverable.get("git_refs", {})
            connection.execute(
                """
                INSERT INTO team_deliverables(
                    id, task_id, producer_agent_id, summary, state, agent_branch,
                    review_branch, feature_branch, release_branch, commit_sha, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    deliverable["id"],
                    deliverable["task_id"],
                    deliverable["producer_agent_id"],
                    deliverable["summary"],
                    deliverable.get("state", "pending"),
                    git_refs.get("agent_branch"),
                    git_refs.get("review_branch"),
                    git_refs.get("feature_branch"),
                    git_refs.get("release_branch"),
                    git_refs.get("commit_sha"),
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

    @staticmethod
    def _read_review_profiles(
        connection: sqlite3.Connection, payload: dict[str, Any]
    ) -> None:
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
                "archived": _bool(row["archived"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_review_profiles(
        connection: sqlite3.Connection, profiles: dict[str, Any]
    ) -> None:
        for profile in profiles.values():
            connection.execute(
                """
                INSERT INTO team_review_profiles(
                    id, team_id, project_id, name, description, archived, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile["id"],
                    profile["team_id"],
                    profile.get("project_id"),
                    profile["name"],
                    profile.get("description", ""),
                    _int_bool(profile.get("archived")),
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
    def _read_review_requests(
        connection: sqlite3.Connection, payload: dict[str, Any]
    ) -> None:
        findings = _grouped_values(
            connection,
            "SELECT review_request_id AS owner_id, finding AS value FROM team_review_request_findings ORDER BY review_request_id, position",
        )
        criteria = _grouped_rows(
            connection,
            """
            SELECT review_request_id AS owner_id, criterion_id, state, finding
            FROM team_review_request_criteria
            ORDER BY review_request_id, position
            """,
            lambda row: {
                "criterion_id": row["criterion_id"],
                "state": row["state"],
                "finding": row["finding"],
            },
        )
        for row in connection.execute("SELECT * FROM team_review_requests ORDER BY id"):
            payload["review_requests"][row["id"]] = {
                "id": row["id"],
                "deliverable_id": row["deliverable_id"],
                "reviewer_agent_id": row["reviewer_agent_id"],
                "state": row["state"],
                "findings": findings.get(row["id"], []),
                "review_profile_id": row["review_profile_id"],
                "instructions": row["instructions"],
                "criteria": criteria.get(row["id"], []),
                "created_at": row["created_at"],
                "decision_at": row["decision_at"],
            }

    @staticmethod
    def _write_review_requests(
        connection: sqlite3.Connection, requests: dict[str, Any]
    ) -> None:
        for request in requests.values():
            connection.execute(
                """
                INSERT INTO team_review_requests(
                    id, deliverable_id, reviewer_agent_id, state, review_profile_id,
                    instructions, created_at, decision_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request["id"],
                    request["deliverable_id"],
                    request["reviewer_agent_id"],
                    request.get("state", "pending"),
                    request.get("review_profile_id"),
                    request.get("instructions"),
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
                        review_request_id, position, criterion_id, state, finding
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        request["id"],
                        position,
                        criterion["criterion_id"],
                        criterion["state"],
                        criterion.get("finding", ""),
                    ),
                )

    @staticmethod
    def _read_inbox(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        for row in connection.execute("SELECT * FROM team_inbox ORDER BY id"):
            payload["inbox"][row["id"]] = {
                "id": row["id"],
                "recipient_agent_id": row["recipient_agent_id"],
                "item_type": row["item_type"],
                "sender_agent_id": row["sender_agent_id"],
                "task_id": row["task_id"],
                "payload": _json_loads(row["payload_json"]),
                "state": row["state"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_inbox(connection: sqlite3.Connection, inbox: dict[str, Any]) -> None:
        for item in inbox.values():
            connection.execute(
                """
                INSERT INTO team_inbox(
                    id, recipient_agent_id, item_type, sender_agent_id, task_id,
                    payload_json, state, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["id"],
                    item["recipient_agent_id"],
                    item["item_type"],
                    item.get("sender_agent_id"),
                    item.get("task_id"),
                    _json_dumps(item.get("payload", {})),
                    item.get("state", "pending"),
                    item["created_at"],
                    item["updated_at"],
                ),
            )

    @staticmethod
    def _read_action_records(
        connection: sqlite3.Connection, payload: dict[str, Any]
    ) -> None:
        for row in connection.execute("SELECT * FROM team_action_records ORDER BY id"):
            payload["action_records"][row["id"]] = {
                "id": row["id"],
                "agent_id": row["agent_id"],
                "dispatch_id": row["dispatch_id"],
                "action": _json_loads(row["action_json"]),
                "success": _bool(row["success"]),
                "error": row["error"],
                "created_at": row["created_at"],
                "dismissed_at": row["dismissed_at"],
                "dismissed_by": row["dismissed_by"],
                "dismissed_reason": row["dismissed_reason"],
            }

    @staticmethod
    def _write_action_records(
        connection: sqlite3.Connection, records: dict[str, Any]
    ) -> None:
        for record in records.values():
            connection.execute(
                """
                INSERT INTO team_action_records(
                    id, agent_id, dispatch_id, action_json, success, error, created_at,
                    dismissed_at, dismissed_by, dismissed_reason
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["id"],
                    record["agent_id"],
                    record["dispatch_id"],
                    _json_dumps(record.get("action", {})),
                    _int_bool(record.get("success", True)),
                    record.get("error"),
                    record["created_at"],
                    record.get("dismissed_at"),
                    record.get("dismissed_by"),
                    record.get("dismissed_reason"),
                ),
            )

    @staticmethod
    def _read_release_packages(
        connection: sqlite3.Connection, payload: dict[str, Any]
    ) -> None:
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
                "deliverable_ids": deliverables.get(row["id"], []),
                "artifact_refs": artifacts.get(row["id"], []),
                "feature_branches": branches.get(row["id"], []),
                "release_branch": row["release_branch"],
                "canonical_branch": row["canonical_branch"],
                "published_commit_sha": row["published_commit_sha"],
                "git_promotion_record_ids": promotions.get(row["id"], []),
                "state": row["state"],
                "published_at": row["published_at"],
                "error": row["error"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_release_packages(
        connection: sqlite3.Connection, packages: dict[str, Any]
    ) -> None:
        for package in packages.values():
            connection.execute(
                """
                INSERT INTO team_release_packages(
                    id, project_id, release_branch, canonical_branch, published_commit_sha,
                    state, published_at, error, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    package["id"],
                    package["project_id"],
                    package.get("release_branch"),
                    package.get("canonical_branch"),
                    package.get("published_commit_sha"),
                    package.get("state", "pending"),
                    package.get("published_at"),
                    package.get("error"),
                    package["created_at"],
                    package.get("updated_at"),
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
    def _read_git_promotions(
        connection: sqlite3.Connection, payload: dict[str, Any]
    ) -> None:
        for row in connection.execute("SELECT * FROM team_git_promotions ORDER BY id"):
            payload["git_promotions"][row["id"]] = {
                "id": row["id"],
                "project_id": row["project_id"],
                "actor_agent_id": row["actor_agent_id"],
                "source_branch": row["source_branch"],
                "target_branch": row["target_branch"],
                "promotion_kind": row["promotion_kind"],
                "task_id": row["task_id"],
                "deliverable_id": row["deliverable_id"],
                "release_package_id": row["release_package_id"],
                "state": row["state"],
                "commit_sha": row["commit_sha"],
                "outputs": _json_loads(row["outputs_json"]),
                "error": row["error"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_git_promotions(
        connection: sqlite3.Connection, promotions: dict[str, Any]
    ) -> None:
        for promotion in promotions.values():
            connection.execute(
                """
                INSERT INTO team_git_promotions(
                    id, project_id, actor_agent_id, source_branch, target_branch,
                    promotion_kind, task_id, deliverable_id, release_package_id, state,
                    commit_sha, outputs_json, error, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    promotion["id"],
                    promotion["project_id"],
                    promotion["actor_agent_id"],
                    promotion["source_branch"],
                    promotion["target_branch"],
                    promotion["promotion_kind"],
                    promotion.get("task_id"),
                    promotion.get("deliverable_id"),
                    promotion.get("release_package_id"),
                    promotion.get("state", "pending"),
                    promotion.get("commit_sha"),
                    _json_dumps(promotion.get("outputs", {})),
                    promotion.get("error"),
                    promotion["created_at"],
                    promotion["updated_at"],
                ),
            )


def _json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _json_loads(value: str | None) -> Any:
    if value is None or value == "":
        return {}
    return json.loads(value)


def _bool(value: Any) -> bool:
    return bool(int(value or 0))


def _int_bool(value: Any) -> int:
    return 1 if bool(value) else 0


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _insert_position_values(
    connection: sqlite3.Connection,
    table: str,
    owner_column: str,
    owner_id: str,
    value_column: str,
    values: list[Any],
) -> None:
    for position, value in enumerate(values):
        connection.execute(
            f"""
            INSERT INTO {table}({owner_column}, position, {value_column})
            VALUES (?, ?, ?)
            """,
            (owner_id, position, value),
        )


def _grouped_values(connection: sqlite3.Connection, query: str) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for row in connection.execute(query):
        grouped.setdefault(row["owner_id"], []).append(row["value"])
    return grouped


def _grouped_rows(
    connection: sqlite3.Connection,
    query: str,
    mapper: Callable[[sqlite3.Row], dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in connection.execute(query):
        grouped.setdefault(row["owner_id"], []).append(mapper(row))
    return grouped


_STATE_TABLES = (
    "team_team_projects",
    "team_role_permissions",
    "team_project_goals",
    "team_agent_subordinates",
    "team_task_dependencies",
    "team_task_deliverables",
    "team_deliverable_artifacts",
    "team_deliverable_reviews",
    "team_deliverable_promotion_records",
    "team_review_profile_roles",
    "team_review_profile_criteria",
    "team_review_request_findings",
    "team_review_request_criteria",
    "team_release_deliverables",
    "team_release_artifacts",
    "team_release_feature_branches",
    "team_release_git_promotions",
    "team_ephemeral_assignment_artifacts",
    "team_ephemeral_assignment_allowed_tools",
    "team_git_promotions",
    "team_ephemeral_assignments",
    "team_release_packages",
    "team_action_records",
    "team_inbox",
    "team_review_requests",
    "team_review_profiles",
    "team_deliverables",
    "team_tasks",
    "team_agent_runtimes",
    "team_agents",
    "team_projects",
    "team_roles",
    "team_teams",
)


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS team_state_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS team_teams (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    manager_agent_id TEXT NOT NULL,
    operator_communication_target_id TEXT,
    max_cost_usd TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_teams_archived ON team_teams(archived, id);

CREATE TABLE IF NOT EXISTS team_team_projects (
    team_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    project_id TEXT NOT NULL,
    PRIMARY KEY(team_id, position)
);
CREATE INDEX IF NOT EXISTS idx_team_team_projects_project ON team_team_projects(project_id);

CREATE TABLE IF NOT EXISTS team_roles (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL,
    project_id TEXT,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    max_ephemeral_concurrent INTEGER NOT NULL,
    archived INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_roles_team_project ON team_roles(team_id, project_id, archived, id);

CREATE TABLE IF NOT EXISTS team_role_permissions (
    role_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    permission TEXT NOT NULL,
    PRIMARY KEY(role_id, position)
);

CREATE TABLE IF NOT EXISTS team_projects (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL,
    name TEXT NOT NULL,
    lead_agent_id TEXT NOT NULL,
    git_json TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_projects_team_status ON team_projects(team_id, status, id);

CREATE TABLE IF NOT EXISTS team_project_goals (
    project_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    goal_id TEXT NOT NULL,
    description TEXT NOT NULL,
    state TEXT NOT NULL,
    PRIMARY KEY(project_id, position)
);

CREATE TABLE IF NOT EXISTS team_agents (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL,
    project_id TEXT,
    display_name TEXT,
    role_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    reports_to_agent_id TEXT,
    max_ephemeral_concurrent INTEGER NOT NULL,
    status TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_agents_status_scope ON team_agents(status, team_id, project_id, id);
CREATE INDEX IF NOT EXISTS idx_team_agents_session ON team_agents(session_id);

CREATE TABLE IF NOT EXISTS team_agent_subordinates (
    agent_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    subordinate_agent_id TEXT NOT NULL,
    PRIMARY KEY(agent_id, position)
);

CREATE TABLE IF NOT EXISTS team_agent_runtimes (
    agent_id TEXT PRIMARY KEY,
    wake_leased_until TEXT,
    active_dispatch_id TEXT,
    wake_reason TEXT,
    last_wake_at TEXT,
    next_wake_at TEXT,
    no_progress_count INTEGER NOT NULL,
    last_progress_at TEXT,
    auto_context_clearing_enabled INTEGER NOT NULL,
    context_rotation_pending INTEGER NOT NULL,
    context_rotation_requested_by TEXT,
    context_rotation_requested_at TEXT,
    context_rotation_reason TEXT,
    context_rotation_request_note TEXT,
    last_handoff_summary TEXT,
    last_context_rotated_at TEXT,
    last_thread_id TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_agent_runtimes_wake
    ON team_agent_runtimes(wake_leased_until, next_wake_at, active_dispatch_id);

CREATE TABLE IF NOT EXISTS team_tasks (
    id TEXT PRIMARY KEY,
    owner_agent_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    priority INTEGER NOT NULL,
    state TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    created_by_agent_id TEXT,
    created_by_actor TEXT,
    parent_task_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_tasks_owner_state_priority
    ON team_tasks(owner_agent_id, state, priority, created_at, id);
CREATE INDEX IF NOT EXISTS idx_team_tasks_project_state
    ON team_tasks(project_id, state, priority, created_at, id);
CREATE INDEX IF NOT EXISTS idx_team_tasks_parent ON team_tasks(parent_task_id, id);

CREATE TABLE IF NOT EXISTS team_task_dependencies (
    task_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    dependency_task_id TEXT NOT NULL,
    PRIMARY KEY(task_id, position)
);
CREATE INDEX IF NOT EXISTS idx_team_task_dependencies_dependency
    ON team_task_dependencies(dependency_task_id);

CREATE TABLE IF NOT EXISTS team_task_deliverables (
    task_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    deliverable_id TEXT NOT NULL,
    PRIMARY KEY(task_id, position)
);

CREATE TABLE IF NOT EXISTS team_deliverables (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    producer_agent_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    state TEXT NOT NULL,
    agent_branch TEXT,
    review_branch TEXT,
    feature_branch TEXT,
    release_branch TEXT,
    commit_sha TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_deliverables_task_state ON team_deliverables(task_id, state, id);
CREATE INDEX IF NOT EXISTS idx_team_deliverables_producer ON team_deliverables(producer_agent_id, created_at, id);

CREATE TABLE IF NOT EXISTS team_deliverable_artifacts (
    deliverable_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    artifact_ref TEXT NOT NULL,
    PRIMARY KEY(deliverable_id, position)
);

CREATE TABLE IF NOT EXISTS team_deliverable_reviews (
    deliverable_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    review_request_id TEXT NOT NULL,
    PRIMARY KEY(deliverable_id, position)
);

CREATE TABLE IF NOT EXISTS team_deliverable_promotion_records (
    deliverable_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    promotion_record_id TEXT NOT NULL,
    PRIMARY KEY(deliverable_id, position)
);

CREATE TABLE IF NOT EXISTS team_review_profiles (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL,
    project_id TEXT,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    archived INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_review_profiles_scope
    ON team_review_profiles(team_id, project_id, archived, id);

CREATE TABLE IF NOT EXISTS team_review_profile_roles (
    profile_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    role_id TEXT NOT NULL,
    PRIMARY KEY(profile_id, position)
);

CREATE TABLE IF NOT EXISTS team_review_profile_criteria (
    profile_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    criterion_id TEXT NOT NULL,
    label TEXT NOT NULL,
    description TEXT NOT NULL,
    required INTEGER NOT NULL,
    PRIMARY KEY(profile_id, position)
);

CREATE TABLE IF NOT EXISTS team_review_requests (
    id TEXT PRIMARY KEY,
    deliverable_id TEXT NOT NULL,
    reviewer_agent_id TEXT NOT NULL,
    state TEXT NOT NULL,
    review_profile_id TEXT,
    instructions TEXT,
    created_at TEXT,
    decision_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_team_review_requests_deliverable_state
    ON team_review_requests(deliverable_id, state, id);
CREATE INDEX IF NOT EXISTS idx_team_review_requests_reviewer_state
    ON team_review_requests(reviewer_agent_id, state, id);

CREATE TABLE IF NOT EXISTS team_review_request_findings (
    review_request_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    finding TEXT NOT NULL,
    PRIMARY KEY(review_request_id, position)
);

CREATE TABLE IF NOT EXISTS team_review_request_criteria (
    review_request_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    criterion_id TEXT NOT NULL,
    state TEXT NOT NULL,
    finding TEXT NOT NULL,
    PRIMARY KEY(review_request_id, position)
);

CREATE TABLE IF NOT EXISTS team_inbox (
    id TEXT PRIMARY KEY,
    recipient_agent_id TEXT NOT NULL,
    item_type TEXT NOT NULL,
    sender_agent_id TEXT,
    task_id TEXT,
    payload_json TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_inbox_recipient_state_created
    ON team_inbox(recipient_agent_id, state, created_at, id);
CREATE INDEX IF NOT EXISTS idx_team_inbox_task_state ON team_inbox(task_id, state, id);

CREATE TABLE IF NOT EXISTS team_action_records (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    dispatch_id TEXT NOT NULL,
    action_json TEXT NOT NULL,
    success INTEGER NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    dismissed_at TEXT,
    dismissed_by TEXT,
    dismissed_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_team_action_records_agent_dispatch
    ON team_action_records(agent_id, dispatch_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_team_action_records_attention
    ON team_action_records(success, dismissed_at, created_at, id);

CREATE TABLE IF NOT EXISTS team_release_packages (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    release_branch TEXT,
    canonical_branch TEXT,
    published_commit_sha TEXT,
    state TEXT NOT NULL,
    published_at TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_team_release_packages_project_state
    ON team_release_packages(project_id, state, created_at, id);

CREATE TABLE IF NOT EXISTS team_release_deliverables (
    package_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    deliverable_id TEXT NOT NULL,
    PRIMARY KEY(package_id, position)
);

CREATE TABLE IF NOT EXISTS team_release_artifacts (
    package_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    artifact_ref TEXT NOT NULL,
    PRIMARY KEY(package_id, position)
);

CREATE TABLE IF NOT EXISTS team_release_feature_branches (
    package_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    feature_branch TEXT NOT NULL,
    PRIMARY KEY(package_id, position)
);

CREATE TABLE IF NOT EXISTS team_release_git_promotions (
    package_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    promotion_record_id TEXT NOT NULL,
    PRIMARY KEY(package_id, position)
);

CREATE TABLE IF NOT EXISTS team_ephemeral_assignments (
    id TEXT PRIMARY KEY,
    parent_agent_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    title TEXT NOT NULL,
    prompt TEXT NOT NULL,
    state TEXT NOT NULL,
    agent_branch TEXT,
    commit_sha TEXT,
    summary TEXT,
    error TEXT,
    model TEXT,
    agent_provider TEXT,
    execution_backend TEXT,
    container_image TEXT,
    ttl_hours INTEGER,
    directory_path TEXT,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_ephemeral_assignments_parent_state
    ON team_ephemeral_assignments(parent_agent_id, state, created_at, id);
CREATE INDEX IF NOT EXISTS idx_team_ephemeral_assignments_session
    ON team_ephemeral_assignments(session_id);
CREATE INDEX IF NOT EXISTS idx_team_ephemeral_assignments_project_task
    ON team_ephemeral_assignments(project_id, task_id, state, id);

CREATE TABLE IF NOT EXISTS team_ephemeral_assignment_artifacts (
    assignment_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    artifact_ref TEXT NOT NULL,
    PRIMARY KEY(assignment_id, position)
);

CREATE TABLE IF NOT EXISTS team_ephemeral_assignment_allowed_tools (
    assignment_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    tool TEXT NOT NULL,
    PRIMARY KEY(assignment_id, position)
);

CREATE TABLE IF NOT EXISTS team_git_promotions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    actor_agent_id TEXT NOT NULL,
    source_branch TEXT NOT NULL,
    target_branch TEXT NOT NULL,
    promotion_kind TEXT NOT NULL,
    task_id TEXT,
    deliverable_id TEXT,
    release_package_id TEXT,
    state TEXT NOT NULL,
    commit_sha TEXT,
    outputs_json TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_git_promotions_project_state
    ON team_git_promotions(project_id, state, created_at, id);
CREATE INDEX IF NOT EXISTS idx_team_git_promotions_deliverable
    ON team_git_promotions(deliverable_id, promotion_kind, state, id);
CREATE INDEX IF NOT EXISTS idx_team_git_promotions_release
    ON team_git_promotions(release_package_id, promotion_kind, state, id);
"""


__all__ = [
    "LEGACY_STATE_FILE",
    "STATE_DB_FILE",
    "TeamStateSqliteStore",
]
