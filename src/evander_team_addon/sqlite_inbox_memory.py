"""Team inbox memory."""

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


class InboxMemoryStoreMixin:
    """Read/write inbox memory rows on a caller-owned transaction."""

    @staticmethod
    def _read_inbox(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        for row in connection.execute("SELECT * FROM team_inbox ORDER BY id"):
            payload["inbox"][row["id"]] = {
                "id": row["id"],
                "recipient_agent_id": row["recipient_agent_id"],
                "item_type": row["item_type"],
                "sender_agent_id": row["sender_agent_id"],
                "task_id": row["task_id"],
                "response_requested": bool(row["response_requested"])
                if row["response_requested"] is not None
                else True,
                "priority": row["priority"] or "normal",
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
                    response_requested, priority, payload_json, state, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["id"],
                    item["recipient_agent_id"],
                    item["item_type"],
                    item.get("sender_agent_id"),
                    item.get("task_id"),
                    1 if item.get("response_requested", True) else 0,
                    item.get("priority", "normal"),
                    _json_dumps(item.get("payload", {})),
                    item.get("state", "pending"),
                    item["created_at"],
                    item["updated_at"],
                ),
            )

    @staticmethod
    def _read_blockers(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        affected_tasks = _grouped_values(
            connection,
            "SELECT blocker_id AS owner_id, task_id AS value "
            "FROM team_blocker_affected_tasks ORDER BY blocker_id, position",
        )
        for row in connection.execute("SELECT * FROM team_blockers ORDER BY id"):
            payload["blockers"][row["id"]] = {
                "id": row["id"],
                "team_id": row["team_id"],
                "project_id": row["project_id"],
                "task_id": row["task_id"],
                "source_kind": row["source_kind"],
                "source_id": row["source_id"],
                "mcp_capability_identity": _json_loads(
                    row["mcp_capability_identity_json"] or "null"
                ),
                "reporter_agent_id": row["reporter_agent_id"],
                "reporter_actor": row["reporter_actor"],
                "reason_code": row["reason_code"],
                "resolution_scope": row["resolution_scope"],
                "affected_task_ids": affected_tasks.get(row["id"], []),
                "owner_agent_id": row["owner_agent_id"],
                "delegate_agent_id": row["delegate_agent_id"],
                "state": row["state"],
                "route": row["route"],
                "summary": row["summary"],
                "details": row["details"],
                "priority": row["priority"],
                "escalation_depth": row["escalation_depth"],
                "active_dispatch_id": row["active_dispatch_id"],
                "follow_up_at": row["follow_up_at"],
                "last_progress_at": row["last_progress_at"],
                "terminal_reference_ids": _json_loads(row["terminal_reference_ids_json"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "resolved_at": row["resolved_at"],
            }

    @staticmethod
    def _write_blockers(connection: sqlite3.Connection, blockers: dict[str, Any]) -> None:
        for blocker in blockers.values():
            connection.execute(
                """
                INSERT INTO team_blockers(
                    id, team_id, project_id, task_id, source_kind, source_id,
                    mcp_capability_identity_json,
                    reporter_agent_id, reporter_actor, reason_code, resolution_scope,
                    owner_agent_id, delegate_agent_id, state, route,
                    summary, details, priority, escalation_depth, active_dispatch_id,
                    follow_up_at, last_progress_at, terminal_reference_ids_json,
                    created_at, updated_at, resolved_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    blocker["id"],
                    blocker["team_id"],
                    blocker.get("project_id"),
                    blocker.get("task_id"),
                    blocker["source_kind"],
                    blocker["source_id"],
                    _json_dumps(blocker.get("mcp_capability_identity")),
                    blocker.get("reporter_agent_id"),
                    blocker.get("reporter_actor"),
                    blocker.get("reason_code", "unspecified"),
                    blocker.get("resolution_scope", "task"),
                    blocker.get("owner_agent_id"),
                    blocker.get("delegate_agent_id"),
                    blocker.get("state", "open"),
                    blocker.get("route", "owned"),
                    blocker["summary"],
                    blocker.get("details", ""),
                    blocker.get("priority", "normal"),
                    blocker.get("escalation_depth", 0),
                    blocker.get("active_dispatch_id"),
                    blocker.get("follow_up_at"),
                    blocker.get("last_progress_at"),
                    _json_dumps(blocker.get("terminal_reference_ids", [])),
                    blocker["created_at"],
                    blocker["updated_at"],
                    blocker.get("resolved_at"),
                ),
            )
            _insert_position_values(
                connection,
                "team_blocker_affected_tasks",
                "blocker_id",
                blocker["id"],
                "task_id",
                blocker.get("affected_task_ids", []),
            )

    @staticmethod
    def _read_blocker_events(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        for row in connection.execute("SELECT * FROM team_blocker_events ORDER BY created_at, id"):
            payload["blocker_events"][row["id"]] = {
                "id": row["id"],
                "blocker_id": row["blocker_id"],
                "event_type": row["event_type"],
                "actor_agent_id": row["actor_agent_id"],
                "from_agent_id": row["from_agent_id"],
                "to_agent_id": row["to_agent_id"],
                "dispatch_id": row["dispatch_id"],
                "note": row["note"],
                "reference_ids": _json_loads(row["reference_ids_json"]),
                "metadata": _json_loads(row["metadata_json"]),
                "created_at": row["created_at"],
            }

    @staticmethod
    def _write_blocker_events(connection: sqlite3.Connection, events: dict[str, Any]) -> None:
        for event in events.values():
            connection.execute(
                """
                INSERT INTO team_blocker_events(
                    id, blocker_id, event_type, actor_agent_id, from_agent_id,
                    to_agent_id, dispatch_id, note, reference_ids_json,
                    metadata_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["id"],
                    event["blocker_id"],
                    event["event_type"],
                    event.get("actor_agent_id"),
                    event.get("from_agent_id"),
                    event.get("to_agent_id"),
                    event.get("dispatch_id"),
                    event.get("note", ""),
                    _json_dumps(event.get("reference_ids", [])),
                    _json_dumps(event.get("metadata", {})),
                    event["created_at"],
                ),
            )

    @staticmethod
    def _read_action_records(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
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
                "feedback_delivered_at": row["feedback_delivered_at"],
                "feedback_delivered_by_dispatch_id": row["feedback_delivered_by_dispatch_id"],
            }

    @staticmethod
    def _write_action_records(connection: sqlite3.Connection, records: dict[str, Any]) -> None:
        for record in records.values():
            connection.execute(
                """
                INSERT INTO team_action_records(
                    id, agent_id, dispatch_id, action_json, success, error, created_at,
                    dismissed_at, dismissed_by, dismissed_reason,
                    feedback_delivered_at, feedback_delivered_by_dispatch_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    record.get("feedback_delivered_at"),
                    record.get("feedback_delivered_by_dispatch_id"),
                ),
            )

    @staticmethod
    def _read_memories(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        tags = _grouped_values(
            connection,
            "SELECT memory_id AS owner_id, tag AS value FROM team_memory_tags ORDER BY memory_id, position",
        )
        source_refs = _grouped_values(
            connection,
            "SELECT memory_id AS owner_id, source_ref AS value FROM team_memory_source_refs ORDER BY memory_id, position",
        )
        artifact_refs = _grouped_values(
            connection,
            "SELECT memory_id AS owner_id, artifact_ref AS value FROM team_memory_artifact_refs ORDER BY memory_id, position",
        )
        for row in connection.execute("SELECT * FROM team_memories ORDER BY id"):
            payload["memories"][row["id"]] = {
                "id": row["id"],
                "team_id": row["team_id"],
                "project_id": row["project_id"],
                "codebase_id": row["codebase_id"],
                "agent_id": row["agent_id"],
                "scope": row["scope"],
                "kind": row["kind"],
                "title": row["title"],
                "summary": row["summary"],
                "body": row["body"],
                "tags": tags.get(row["id"], []),
                "source_refs": source_refs.get(row["id"], []),
                "artifact_refs": artifact_refs.get(row["id"], []),
                "status": row["status"],
                "confidence": row["confidence"],
                "supersedes_memory_id": row["supersedes_memory_id"],
                "superseded_by_memory_id": row["superseded_by_memory_id"],
                "created_by_agent_id": row["created_by_agent_id"],
                "created_by": row["created_by"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_memories(connection: sqlite3.Connection, memories: dict[str, Any]) -> None:
        for memory in memories.values():
            connection.execute(
                """
                INSERT INTO team_memories(
                    id, team_id, project_id, codebase_id, agent_id, scope, kind,
                    title, summary, body, status, confidence, supersedes_memory_id,
                    superseded_by_memory_id, created_by_agent_id, created_by,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory["id"],
                    memory["team_id"],
                    memory.get("project_id"),
                    memory.get("codebase_id"),
                    memory.get("agent_id"),
                    memory.get("scope", "team_global"),
                    memory.get("kind", "other"),
                    memory["title"],
                    memory["summary"],
                    memory.get("body"),
                    memory.get("status", "active"),
                    memory.get("confidence"),
                    memory.get("supersedes_memory_id"),
                    memory.get("superseded_by_memory_id"),
                    memory.get("created_by_agent_id"),
                    memory.get("created_by"),
                    memory["created_at"],
                    memory["updated_at"],
                ),
            )
            _insert_position_values(
                connection,
                "team_memory_tags",
                "memory_id",
                memory["id"],
                "tag",
                memory.get("tags", []),
            )
            _insert_position_values(
                connection,
                "team_memory_source_refs",
                "memory_id",
                memory["id"],
                "source_ref",
                memory.get("source_refs", []),
            )
            _insert_position_values(
                connection,
                "team_memory_artifact_refs",
                "memory_id",
                memory["id"],
                "artifact_ref",
                memory.get("artifact_refs", []),
            )
