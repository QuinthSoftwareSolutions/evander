"""Team migrations worktrees."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

from evander.models import DirectoryPath, utc_now

from .models import WORKTREE_CLEANUP_TRANSIENT_FAILURE_CHECKS, summarize_worktree_cleanup_inventory
from .sqlite_codec import (
    _json_dumps,
    _json_loads,
)


def _migrate_worktree_cleanup_evidence(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        "SELECT id, payload_json FROM team_worktree_cleanup_candidates ORDER BY id"
    ).fetchall()
    for row in rows:
        payload = _json_loads(row["payload_json"])
        raw_inventory = payload.get("ignored_file_inventory") or []
        if not isinstance(raw_inventory, list):
            raw_inventory = []
        inventory = [
            item.strip() for item in raw_inventory if isinstance(item, str) and item.strip()
        ]
        sample, count, digest, truncated = summarize_worktree_cleanup_inventory(inventory)
        payload["ignored_file_inventory"] = sample
        payload["ignored_file_count"] = count
        payload["ignored_file_inventory_sha256"] = digest
        payload["ignored_file_inventory_truncated"] = truncated
        connection.execute(
            "UPDATE team_worktree_cleanup_candidates SET payload_json = ? WHERE id = ?",
            (_json_dumps(payload), row["id"]),
        )


def _migrate_worktree_cleanup_directory_scopes(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        """
        SELECT candidate.id,
               candidate.payload_json,
               project.directory_path AS project_directory_path,
               project.git_json AS project_git_json
        FROM team_worktree_cleanup_candidates AS candidate
        LEFT JOIN team_projects AS project ON project.id = candidate.project_id
        ORDER BY candidate.id
        """
    ).fetchall()
    for row in rows:
        payload = _json_loads(row["payload_json"])
        raw_directory_path = str(payload.get("directory_path") or "").strip()
        if not raw_directory_path:
            raw_directory_path = str(row["project_directory_path"] or "").strip()
        if not raw_directory_path and row["project_git_json"] is not None:
            git_payload = _json_loads(row["project_git_json"])
            if isinstance(git_payload, dict):
                raw_directory_path = str(git_payload.get("directory_path") or "").strip()
        payload["directory_path"] = DirectoryPath(segments=raw_directory_path or "root").as_posix()
        connection.execute(
            "UPDATE team_worktree_cleanup_candidates SET payload_json = ? WHERE id = ?",
            (_json_dumps(payload), row["id"]),
        )


def _migrate_worktree_cleanup_context_ids(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        """
        SELECT id, git_work_context_id, payload_json
        FROM team_worktree_cleanup_candidates
        ORDER BY id
        """
    ).fetchall()
    for row in rows:
        payload = _json_loads(row["payload_json"])
        context_id = str(
            payload.get("git_work_context_id") or row["git_work_context_id"] or ""
        ).strip()
        if not context_id:
            context_id = _legacy_cleanup_candidate_context_id(connection, payload)
        payload["git_work_context_id"] = context_id
        connection.execute(
            """
            UPDATE team_worktree_cleanup_candidates
            SET git_work_context_id = ?, payload_json = ?
            WHERE id = ?
            """,
            (context_id, _json_dumps(payload), row["id"]),
        )


def _legacy_cleanup_candidate_context_id(
    connection: sqlite3.Connection,
    candidate: Mapping[str, Any],
) -> str:
    rows = connection.execute(
        """
        SELECT id, agent_id, task_id, dispatch_id, worktree_path, agent_branch
        FROM team_git_work_contexts
        WHERE task_id = ?
          AND agent_id = ?
          AND worktree_path = ?
          AND agent_branch = ?
        ORDER BY id
        """,
        (
            candidate.get("task_id"),
            candidate.get("agent_id"),
            candidate.get("worktree_path"),
            candidate.get("branch"),
        ),
    ).fetchall()
    matching_ids: list[str] = []
    for row in rows:
        if row["dispatch_id"] is not None:
            dispatch = connection.execute(
                "SELECT agent_id, session_id FROM team_dispatches WHERE id = ?",
                (row["dispatch_id"],),
            ).fetchone()
            references_task = connection.execute(
                """
                SELECT 1
                FROM team_dispatch_tasks
                WHERE dispatch_id = ? AND task_id = ?
                LIMIT 1
                """,
                (row["dispatch_id"], row["task_id"]),
            ).fetchone()
            session_id = (
                dispatch["session_id"]
                if dispatch is not None
                and dispatch["agent_id"] == row["agent_id"]
                and references_task is not None
                else None
            )
        else:
            agent = connection.execute(
                "SELECT session_id FROM team_agents WHERE id = ?",
                (row["agent_id"],),
            ).fetchone()
            session_id = agent["session_id"] if agent is not None else None
        if session_id == candidate.get("session_id"):
            matching_ids.append(str(row["id"]))
    if len(matching_ids) == 1:
        return matching_ids[0]
    return f"legacy:{str(candidate.get('id') or 'unknown').strip() or 'unknown'}"


def _migrate_worktree_cleanup_retry_dispositions(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        """
        SELECT id, state, payload_json
        FROM team_worktree_cleanup_candidates
        WHERE state = 'recovery_required'
        ORDER BY id
        """
    ).fetchall()
    for row in rows:
        payload = _json_loads(row["payload_json"])
        failed_check = str(payload.get("last_safety_check") or "").strip()
        if failed_check in WORKTREE_CLEANUP_TRANSIENT_FAILURE_CHECKS:
            next_state = "pending"
            payload["automatic_retry_count"] = 0
        else:
            next_state = "operator_action_required"
            if payload.get("operator_action_required_at") is None:
                payload["operator_action_required_at"] = (
                    payload.get("updated_at") or payload.get("eligible_at") or utc_now().isoformat()
                )
            if payload.get("operator_action_reason") is None:
                payload["operator_action_reason"] = failed_check or "legacy_recovery_required"
        payload["state"] = next_state
        connection.execute(
            """
            UPDATE team_worktree_cleanup_candidates
            SET state = ?, payload_json = ?
            WHERE id = ?
            """,
            (next_state, _json_dumps(payload), row["id"]),
        )
