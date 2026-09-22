"""Team chat."""

from __future__ import annotations

import sqlite3
from typing import Any

from .sqlite_codec import (
    _json_dumps,
    _json_loads,
    _optional_bool_db,
    _optional_db_bool,
)


class ChatStoreMixin:
    """Read/write chat rows on a caller-owned transaction."""

    @staticmethod
    def _read_conversations(
        connection: sqlite3.Connection,
        payload: dict[str, Any],
    ) -> None:
        for row in connection.execute("SELECT * FROM team_conversations ORDER BY id"):
            payload["conversations"][row["id"]] = {
                "id": row["id"],
                "agent_id": row["agent_id"],
                "operator_user_id": row["operator_user_id"],
                "operator_display_name": row["operator_display_name"],
                "created_by": row["created_by"],
                "last_message_preview": row["last_message_preview"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "last_interaction_at": row["last_interaction_at"],
            }

    @staticmethod
    def _write_conversations(
        connection: sqlite3.Connection,
        conversations: dict[str, Any],
    ) -> None:
        for conversation in conversations.values():
            connection.execute(
                """
                INSERT INTO team_conversations(
                    id, agent_id, operator_user_id, operator_display_name,
                    created_by, last_message_preview,
                    created_at, updated_at, last_interaction_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation["id"],
                    conversation["agent_id"],
                    conversation.get("operator_user_id") or "admin",
                    conversation.get("operator_display_name"),
                    conversation.get("created_by"),
                    conversation.get("last_message_preview"),
                    conversation["created_at"],
                    conversation["updated_at"],
                    conversation["last_interaction_at"],
                ),
            )

    @staticmethod
    def _read_conversation_messages(
        connection: sqlite3.Connection,
        payload: dict[str, Any],
    ) -> None:
        for row in connection.execute(
            "SELECT * FROM team_conversation_messages ORDER BY created_at, id"
        ):
            payload["conversation_messages"][row["id"]] = {
                "id": row["id"],
                "conversation_id": row["conversation_id"],
                "sender": row["sender"],
                "message_type": row["message_type"],
                "body": row["body"],
                "status": row["status"],
                "dispatch_id": row["dispatch_id"],
                "voice_session_id": row["voice_session_id"],
                "source_message_id": row["source_message_id"],
                "source_message_ids": _json_loads(row["source_message_ids_json"] or "[]"),
                "seen_at": row["seen_at"],
                "seen_dispatch_id": row["seen_dispatch_id"],
                "error": row["error"],
                "metadata": _json_loads(row["metadata_json"]),
                "attachment_ids": _json_loads(row["attachment_ids_json"] or "[]"),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_conversation_messages(
        connection: sqlite3.Connection,
        messages: dict[str, Any],
    ) -> None:
        for message in messages.values():
            connection.execute(
                """
                INSERT INTO team_conversation_messages(
                    id, conversation_id, sender, message_type, body, status,
                    dispatch_id, voice_session_id, source_message_id, error,
                    metadata_json, created_at, updated_at, attachment_ids_json,
                    source_message_ids_json, seen_at, seen_dispatch_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message["id"],
                    message["conversation_id"],
                    message["sender"],
                    message.get("message_type", "text"),
                    message.get("body"),
                    message.get("status", "sent"),
                    message.get("dispatch_id"),
                    message.get("voice_session_id"),
                    message.get("source_message_id"),
                    message.get("error"),
                    _json_dumps(message.get("metadata", {})),
                    message["created_at"],
                    message["updated_at"],
                    _json_dumps(message.get("attachment_ids", [])),
                    _json_dumps(message.get("source_message_ids", [])),
                    message.get("seen_at"),
                    message.get("seen_dispatch_id"),
                ),
            )

    @staticmethod
    def _read_external_chat_ingress(
        connection: sqlite3.Connection, payload: dict[str, Any]
    ) -> None:
        for row in connection.execute(
            "SELECT * FROM team_external_chat_ingress ORDER BY created_at, id"
        ):
            payload["external_chat_ingress"][row["id"]] = {
                "id": row["id"],
                "provider": row["provider"],
                "communication_target_id": row["communication_target_id"],
                "source_message_id": row["source_message_id"],
                "source_thread_id": row["source_thread_id"],
                "source_author_id": row["source_author_id"],
                "source_author_name": row["source_author_name"],
                "operator_user_id": row["operator_user_id"],
                "conversation_id": row["conversation_id"],
                "message_id": row["message_id"],
                "dispatch_id": row["dispatch_id"],
                "allow_team_mutations": bool(row["allow_team_mutations"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_external_chat_ingress(
        connection: sqlite3.Connection,
        records: dict[str, Any],
    ) -> None:
        for record in records.values():
            connection.execute(
                """
                INSERT INTO team_external_chat_ingress(
                    id, provider, communication_target_id, source_message_id,
                    source_thread_id, source_author_id, source_author_name,
                    operator_user_id, conversation_id, message_id, dispatch_id,
                    allow_team_mutations, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["id"],
                    record["provider"],
                    record["communication_target_id"],
                    record["source_message_id"],
                    record.get("source_thread_id"),
                    record["source_author_id"],
                    record.get("source_author_name"),
                    record["operator_user_id"],
                    record["conversation_id"],
                    record["message_id"],
                    record["dispatch_id"],
                    int(bool(record.get("allow_team_mutations"))),
                    record["created_at"],
                    record["updated_at"],
                ),
            )

    @staticmethod
    def _read_external_chat_deliveries(
        connection: sqlite3.Connection,
        payload: dict[str, Any],
    ) -> None:
        for row in connection.execute(
            "SELECT * FROM team_external_chat_deliveries ORDER BY created_at, id"
        ):
            payload["external_chat_deliveries"][row["id"]] = {
                "id": row["id"],
                "agent_reply_message_id": row["agent_reply_message_id"],
                "operator_message_id": row["operator_message_id"],
                "provider": row["provider"],
                "communication_target_id": row["communication_target_id"],
                "source_thread_id": row["source_thread_id"],
                "status": row["status"],
                "provider_message_id": row["provider_message_id"],
                "attempt_count": row["attempt_count"],
                "last_error": row["last_error"],
                "attempted_at": row["attempted_at"],
                "delivered_at": row["delivered_at"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_external_chat_deliveries(
        connection: sqlite3.Connection,
        records: dict[str, Any],
    ) -> None:
        for record in records.values():
            connection.execute(
                """
                INSERT INTO team_external_chat_deliveries(
                    id, agent_reply_message_id, operator_message_id, provider,
                    communication_target_id, source_thread_id, status,
                    provider_message_id, attempt_count, last_error, attempted_at,
                    delivered_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["id"],
                    record["agent_reply_message_id"],
                    record["operator_message_id"],
                    record["provider"],
                    record["communication_target_id"],
                    record.get("source_thread_id"),
                    record.get("status", "pending"),
                    record.get("provider_message_id"),
                    record.get("attempt_count", 0),
                    record.get("last_error"),
                    record.get("attempted_at"),
                    record.get("delivered_at"),
                    record["created_at"],
                    record["updated_at"],
                ),
            )

    @staticmethod
    def _read_chat_attachments(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        for row in connection.execute(
            "SELECT * FROM team_chat_attachments ORDER BY created_at, id"
        ):
            payload["chat_attachments"][row["id"]] = {
                "id": row["id"],
                "message_id": row["message_id"],
                "conversation_id": row["conversation_id"],
                "filename": row["filename"],
                "mime_type": row["mime_type"],
                "byte_count": row["byte_count"],
                "sha256": row["sha256"],
                "sender": row["sender"],
                "created_by_session_id": row["created_by_session_id"],
                "external_source": (
                    _json_loads(row["external_source_json"])
                    if row["external_source_json"] is not None
                    else None
                ),
                "created_at": row["created_at"],
            }

    @staticmethod
    def _write_chat_attachments(
        connection: sqlite3.Connection, attachments: dict[str, Any]
    ) -> None:
        for attachment in attachments.values():
            connection.execute(
                """
                INSERT INTO team_chat_attachments(
                    id, message_id, conversation_id, filename, mime_type, byte_count,
                    sha256, sender, created_by_session_id, external_source_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attachment["id"],
                    attachment["message_id"],
                    attachment["conversation_id"],
                    attachment["filename"],
                    attachment.get("mime_type"),
                    attachment["byte_count"],
                    attachment["sha256"],
                    attachment["sender"],
                    attachment.get("created_by_session_id"),
                    (
                        _json_dumps(attachment["external_source"])
                        if attachment.get("external_source") is not None
                        else None
                    ),
                    attachment["created_at"],
                ),
            )

    @staticmethod
    def _read_chat_attachment_policies(
        connection: sqlite3.Connection, payload: dict[str, Any]
    ) -> None:
        for row in connection.execute(
            "SELECT * FROM team_chat_attachment_policies ORDER BY directory_path"
        ):
            payload["chat_attachment_policies"][row["directory_path"]] = {
                "id": row["directory_path"],
                "directory_path": row["directory_path"],
                "operator_to_agent_enabled": _optional_db_bool(row["operator_to_agent_enabled"]),
                "agent_to_operator_enabled": _optional_db_bool(row["agent_to_operator_enabled"]),
                "agent_send_requires_approval": _optional_db_bool(
                    row["agent_send_requires_approval"]
                ),
                "max_attachment_bytes": row["max_attachment_bytes"],
                "max_attachments_per_message": row["max_attachments_per_message"],
                "allowed_mime_types": _json_loads(row["allowed_mime_types_json"])
                if row["allowed_mime_types_json"] is not None
                else None,
                "allowed_extensions": _json_loads(row["allowed_extensions_json"])
                if row["allowed_extensions_json"] is not None
                else None,
                "blocked_extensions": _json_loads(row["blocked_extensions_json"])
                if row["blocked_extensions_json"] is not None
                else None,
                "updated_by": row["updated_by"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_chat_attachment_policies(
        connection: sqlite3.Connection, policies: dict[str, Any]
    ) -> None:
        for policy in policies.values():
            connection.execute(
                """
                INSERT INTO team_chat_attachment_policies(
                    directory_path, operator_to_agent_enabled, agent_to_operator_enabled,
                    agent_send_requires_approval, max_attachment_bytes,
                    max_attachments_per_message, allowed_mime_types_json,
                    allowed_extensions_json, blocked_extensions_json, updated_by, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    policy["directory_path"],
                    _optional_bool_db(policy.get("operator_to_agent_enabled")),
                    _optional_bool_db(policy.get("agent_to_operator_enabled")),
                    _optional_bool_db(policy.get("agent_send_requires_approval")),
                    policy.get("max_attachment_bytes"),
                    policy.get("max_attachments_per_message"),
                    _json_dumps(policy["allowed_mime_types"])
                    if policy.get("allowed_mime_types") is not None
                    else None,
                    _json_dumps(policy["allowed_extensions"])
                    if policy.get("allowed_extensions") is not None
                    else None,
                    _json_dumps(policy["blocked_extensions"])
                    if policy.get("blocked_extensions") is not None
                    else None,
                    policy.get("updated_by"),
                    policy["updated_at"],
                ),
            )
