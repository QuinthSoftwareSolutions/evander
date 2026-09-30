from __future__ import annotations

import json
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from pydantic import BaseModel, Field, field_validator

_TEAMS_MESSAGE_RETENTION = timedelta(days=90)
_COMPLETED_NOTIFICATION_RETENTION = timedelta(days=7)
_FAILED_NOTIFICATION_RETENTION = timedelta(days=30)
_SQLITE_TEAMS_INBOX_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
_SQLITE_BUSY_TIMEOUT_MS = 30_000
_SQLITE_WAL_ENABLE_TIMEOUT_SECONDS = 5.0


def teams_inbox_db_path(path: Path) -> Path:
    if path.suffix.lower() in _SQLITE_TEAMS_INBOX_SUFFIXES:
        return path
    return path.with_suffix(".db")


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


class TeamsInboxCursorState(BaseModel):
    cursor: str
    created_at: datetime | None = None


class TeamsInboxPollHealth(BaseModel):
    target_key: str
    consecutive_failures: int = Field(default=0, ge=0)
    next_attempt_at: datetime | None = None
    last_error_class: str | None = None
    last_error_message: str | None = None
    last_failed_at: datetime | None = None
    last_success_at: datetime | None = None


class TeamsInboxMessage(BaseModel):
    cursor: str
    message_id: str
    team_id: str = ""
    channel_id: str = ""
    chat_id: str | None = None
    thread_id: str | None = None
    author_id: str
    author_name: str = ""
    content: str
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class TeamsOwnedSubscription(BaseModel):
    subscription_id: str
    owner_id: str
    target: str
    resource: str
    directory_path: str
    provider_identity_key: str | None = None
    consumer_directory_paths: list[str] = Field(default_factory=list)
    tenant_id: str
    expires_at: datetime
    health: str = "active"
    notification_url: str | None = None
    lifecycle_notification_url: str | None = None
    client_state_sha256: str
    created_at: datetime
    updated_at: datetime
    last_reconciled_at: datetime | None = None
    last_error: str | None = None
    cleanup_attempted: bool = False
    cleanup_attempt_count: int = Field(default=0, ge=0)
    cleanup_next_attempt_at: datetime | None = None

    @field_validator("consumer_directory_paths", mode="before")
    @classmethod
    def normalize_consumer_directory_paths(cls, value: object) -> list[str]:
        if not isinstance(value, (list, tuple, set, frozenset)):
            return []
        return sorted({str(item or "").strip() for item in value if str(item or "").strip()})


class TeamsSubscriptionReconciliationState(BaseModel):
    directory_path: str
    target: str
    consecutive_failures: int = Field(default=0, ge=0)
    next_attempt_at: datetime
    last_error: str | None = None
    updated_at: datetime


class TeamsNotificationQueueEntry(BaseModel):
    idempotency_key: str
    notification_kind: str
    subscription_id: str
    tenant_id: str
    resource: str
    change_type: str | None = None
    lifecycle_event: str | None = None
    received_at: datetime
    state: str = "pending"
    attempt_count: int = 0
    next_attempt_at: datetime
    leased_until: datetime | None = None
    completed_at: datetime | None = None
    last_error: str | None = None


class TeamsInboxStore:
    """SQLite-backed store for Teams inbox messages and per-channel cursors."""

    def __init__(self, path: Path) -> None:
        self.path = teams_inbox_db_path(Path(path))
        self._lock = RLock()
        self.ensure_schema()

    def get_cursor(self, channel_target: str) -> str | None:
        state = self.get_cursor_state(channel_target)
        return state.cursor if state is not None else None

    def get_cursor_state(self, channel_target: str) -> TeamsInboxCursorState | None:
        normalized_target = str(channel_target or "").strip().strip("/")
        if not normalized_target:
            return None
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM teams_inbox_cursors
                WHERE channel_target = ?
                """,
                (normalized_target,),
            ).fetchone()
        if row is None:
            return None
        return TeamsInboxCursorState.model_validate_json(row["payload_json"])

    def set_cursor(
        self,
        channel_target: str,
        cursor: str | None,
        *,
        created_at: datetime | None = None,
    ) -> None:
        normalized_target = str(channel_target or "").strip().strip("/")
        if not normalized_target:
            return
        with self._lock, self._connect() as connection:
            if cursor is None:
                connection.execute(
                    "DELETE FROM teams_inbox_cursors WHERE channel_target = ?",
                    (normalized_target,),
                )
            else:
                state = TeamsInboxCursorState(cursor=cursor, created_at=created_at)
                connection.execute("BEGIN IMMEDIATE")
                current_row = connection.execute(
                    "SELECT payload_json FROM teams_inbox_cursors WHERE channel_target = ?",
                    (normalized_target,),
                ).fetchone()
                if current_row is not None:
                    current = TeamsInboxCursorState.model_validate_json(current_row["payload_json"])
                    if self._cursor_state_sort_key(state) < self._cursor_state_sort_key(current):
                        return
                connection.execute(
                    """
                    INSERT INTO teams_inbox_cursors(
                        channel_target,
                        cursor,
                        created_at,
                        payload_json
                    )
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(channel_target) DO UPDATE SET
                        cursor = excluded.cursor,
                        created_at = excluded.created_at,
                        payload_json = excluded.payload_json
                    """,
                    self._cursor_row(normalized_target, state),
                )
            self._compact_messages(connection)

    def notification_owner_id(self) -> str:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT value FROM teams_inbox_notification_meta WHERE key = 'owner_id'"
            ).fetchone()
            if row is not None:
                return str(row["value"])
            owner_id = secrets.token_hex(16)
            connection.execute(
                "INSERT INTO teams_inbox_notification_meta(key, value) VALUES ('owner_id', ?)",
                (owner_id,),
            )
            return owner_id

    def record_notification_ingestion(self, *, ingested_at: datetime) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO teams_inbox_notification_meta(key, value)
                VALUES ('last_ingestion_at', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (self._datetime_to_storage(ingested_at),),
            )

    def get_owned_subscription(self, subscription_id: str) -> TeamsOwnedSubscription | None:
        normalized_id = str(subscription_id or "").strip()
        if not normalized_id:
            return None
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM teams_inbox_subscriptions WHERE subscription_id = ?",
                (normalized_id,),
            ).fetchone()
        if row is None:
            return None
        return TeamsOwnedSubscription.model_validate_json(row["payload_json"])

    def get_owned_subscriptions(
        self,
        subscription_ids: set[str],
    ) -> dict[str, TeamsOwnedSubscription]:
        normalized_ids = {
            str(subscription_id or "").strip()
            for subscription_id in subscription_ids
            if str(subscription_id or "").strip()
        }
        if not normalized_ids:
            return {}
        placeholders = ", ".join("?" for _ in normalized_ids)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT subscription_id, payload_json
                FROM teams_inbox_subscriptions
                WHERE subscription_id IN ({placeholders})
                """,
                sorted(normalized_ids),
            ).fetchall()
        return {
            str(row["subscription_id"]): TeamsOwnedSubscription.model_validate_json(
                row["payload_json"]
            )
            for row in rows
        }

    def list_owned_subscriptions(
        self,
        *,
        directory_path: str | None = None,
        include_terminal: bool = False,
    ) -> list[TeamsOwnedSubscription]:
        where: list[str] = []
        params: list[Any] = []
        if directory_path is not None:
            where.append("directory_path = ?")
            params.append(str(directory_path).strip())
        if not include_terminal:
            where.append("health NOT IN ('deleted', 'expired', 'abandoned')")
        sql = "SELECT payload_json FROM teams_inbox_subscriptions"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY directory_path, target, subscription_id"
        with self._lock, self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [TeamsOwnedSubscription.model_validate_json(row["payload_json"]) for row in rows]

    def find_owned_subscription(
        self,
        *,
        directory_path: str,
        tenant_id: str,
        target: str,
    ) -> TeamsOwnedSubscription | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM teams_inbox_subscriptions
                WHERE directory_path = ? AND tenant_id = ? AND target = ?
                  AND health NOT IN ('deleted', 'expired', 'abandoned')
                ORDER BY expires_at DESC
                LIMIT 1
                """,
                (directory_path, tenant_id, target),
            ).fetchone()
        if row is None:
            return None
        return TeamsOwnedSubscription.model_validate_json(row["payload_json"])

    def upsert_owned_subscription(self, subscription: TeamsOwnedSubscription) -> None:
        state = TeamsOwnedSubscription.model_validate(subscription)
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO teams_inbox_subscriptions(
                    subscription_id, owner_id, target, resource, directory_path,
                    tenant_id, expires_at, health, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(subscription_id) DO UPDATE SET
                    owner_id = excluded.owner_id,
                    target = excluded.target,
                    resource = excluded.resource,
                    directory_path = excluded.directory_path,
                    tenant_id = excluded.tenant_id,
                    expires_at = excluded.expires_at,
                    health = excluded.health,
                    payload_json = excluded.payload_json
                """,
                (
                    state.subscription_id,
                    state.owner_id,
                    state.target,
                    state.resource,
                    state.directory_path,
                    state.tenant_id,
                    self._datetime_to_storage(state.expires_at),
                    state.health,
                    _json_dumps(state.model_dump(mode="json")),
                ),
            )

    def get_subscription_reconciliation_state(
        self,
        *,
        directory_path: str,
        target: str,
    ) -> TeamsSubscriptionReconciliationState | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM teams_inbox_subscription_reconciliation
                WHERE directory_path = ? AND target = ?
                """,
                (directory_path, target),
            ).fetchone()
        if row is None:
            return None
        return TeamsSubscriptionReconciliationState.model_validate_json(row["payload_json"])

    def record_subscription_reconciliation_failure(
        self,
        *,
        directory_path: str,
        target: str,
        failed_at: datetime,
        error: str,
        initial_backoff_seconds: float,
        max_backoff_seconds: float,
    ) -> TeamsSubscriptionReconciliationState:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT payload_json
                FROM teams_inbox_subscription_reconciliation
                WHERE directory_path = ? AND target = ?
                """,
                (directory_path, target),
            ).fetchone()
            previous = (
                TeamsSubscriptionReconciliationState.model_validate_json(row["payload_json"])
                if row is not None
                else None
            )
            failure_count = (previous.consecutive_failures if previous is not None else 0) + 1
            exponent = min(failure_count - 1, 30)
            backoff_seconds = min(
                max(0.0, float(max_backoff_seconds)),
                max(0.0, float(initial_backoff_seconds)) * (2**exponent),
            )
            state = TeamsSubscriptionReconciliationState(
                directory_path=directory_path,
                target=target,
                consecutive_failures=failure_count,
                next_attempt_at=failed_at + timedelta(seconds=backoff_seconds),
                last_error=self._sanitize_error(error) or None,
                updated_at=failed_at,
            )
            connection.execute(
                """
                INSERT INTO teams_inbox_subscription_reconciliation(
                    directory_path, target, next_attempt_at, payload_json
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(directory_path, target) DO UPDATE SET
                    next_attempt_at = excluded.next_attempt_at,
                    payload_json = excluded.payload_json
                """,
                (
                    directory_path,
                    target,
                    self._datetime_to_storage(state.next_attempt_at),
                    _json_dumps(state.model_dump(mode="json")),
                ),
            )
        return state

    def clear_subscription_reconciliation_state(
        self,
        *,
        directory_path: str,
        target: str,
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                DELETE FROM teams_inbox_subscription_reconciliation
                WHERE directory_path = ? AND target = ?
                """,
                (directory_path, target),
            )

    def clear_subscription_reconciliation_states(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM teams_inbox_subscription_reconciliation")

    def replace_notification_target_health(
        self,
        *,
        directory_path: str,
        desired_targets: set[str],
        healthy_targets: set[str],
        errors_by_target: dict[str, str | None],
        updated_at: datetime,
    ) -> None:
        desired = {str(target).strip() for target in desired_targets if str(target).strip()}
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if desired:
                placeholders = ", ".join("?" for _ in desired)
                connection.execute(
                    f"""
                    DELETE FROM teams_inbox_notification_targets
                    WHERE directory_path = ? AND target NOT IN ({placeholders})
                    """,
                    (directory_path, *sorted(desired)),
                )
                connection.execute(
                    f"""
                    DELETE FROM teams_inbox_subscription_reconciliation
                    WHERE directory_path = ? AND target NOT IN ({placeholders})
                    """,
                    (directory_path, *sorted(desired)),
                )
            else:
                connection.execute(
                    "DELETE FROM teams_inbox_notification_targets WHERE directory_path = ?",
                    (directory_path,),
                )
                connection.execute(
                    """
                    DELETE FROM teams_inbox_subscription_reconciliation
                    WHERE directory_path = ?
                    """,
                    (directory_path,),
                )
            connection.executemany(
                """
                INSERT INTO teams_inbox_notification_targets(
                    directory_path, target, health, last_error, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(directory_path, target) DO UPDATE SET
                    health = excluded.health,
                    last_error = excluded.last_error,
                    updated_at = excluded.updated_at
                """,
                [
                    (
                        directory_path,
                        target,
                        "active" if target in healthy_targets else "fallback",
                        self._sanitize_error(errors_by_target.get(target) or "") or None,
                        self._datetime_to_storage(updated_at),
                    )
                    for target in sorted(desired)
                ],
            )

    def enqueue_notification(self, entry: TeamsNotificationQueueEntry) -> bool:
        queued = TeamsNotificationQueueEntry.model_validate(entry)
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO teams_inbox_notification_queue(
                    idempotency_key, notification_kind, subscription_id, tenant_id,
                    resource, state, received_at, next_attempt_at, leased_until,
                    completed_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._notification_queue_row(queued),
            )
            self._compact_notifications(connection)
            return cursor.rowcount > 0

    def has_ready_notifications(self, *, now: datetime) -> bool:
        """Check readiness without acquiring leases or preparing ingestion configuration."""

        stored_now = self._datetime_to_storage(now)
        with self._lock, self._connect() as connection:
            return connection.execute(
                """
                SELECT 1 FROM teams_inbox_notification_queue
                WHERE state IN ('pending', 'retry', 'leased')
                  AND next_attempt_at <= ?
                  AND (leased_until IS NULL OR leased_until <= ?)
                LIMIT 1
                """,
                (stored_now, stored_now),
            ).fetchone() is not None

    def lease_notifications(
        self,
        *,
        now: datetime,
        limit: int = 100,
        lease_seconds: float = 60.0,
    ) -> list[TeamsNotificationQueueEntry]:
        if limit <= 0:
            return []
        lease_until = now + timedelta(seconds=max(1.0, lease_seconds))
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._compact_notifications(connection)
            rows = connection.execute(
                """
                SELECT idempotency_key, payload_json
                FROM teams_inbox_notification_queue
                WHERE state IN ('pending', 'retry', 'leased')
                  AND next_attempt_at <= ?
                  AND (leased_until IS NULL OR leased_until <= ?)
                ORDER BY received_at, idempotency_key
                LIMIT ?
                """,
                (
                    self._datetime_to_storage(now),
                    self._datetime_to_storage(now),
                    limit,
                ),
            ).fetchall()
            entries: list[TeamsNotificationQueueEntry] = []
            for row in rows:
                entry = TeamsNotificationQueueEntry.model_validate_json(row["payload_json"])
                leased = entry.model_copy(
                    update={
                        "state": "leased",
                        "attempt_count": entry.attempt_count + 1,
                        "leased_until": lease_until,
                    }
                )
                connection.execute(
                    """
                    UPDATE teams_inbox_notification_queue
                    SET state = 'leased', leased_until = ?, payload_json = ?
                    WHERE idempotency_key = ?
                    """,
                    (
                        self._datetime_to_storage(lease_until),
                        _json_dumps(leased.model_dump(mode="json")),
                        leased.idempotency_key,
                    ),
                )
                entries.append(leased)
        return entries

    def complete_notification(
        self,
        entry: TeamsNotificationQueueEntry,
        *,
        completed_at: datetime,
    ) -> None:
        completed = entry.model_copy(
            update={
                "state": "completed",
                "completed_at": completed_at,
                "leased_until": None,
                "last_error": None,
            }
        )
        self._update_notification_queue(completed)

    def retry_notification(
        self,
        entry: TeamsNotificationQueueEntry,
        *,
        next_attempt_at: datetime,
        error: str,
    ) -> None:
        retried = entry.model_copy(
            update={
                "state": "retry",
                "next_attempt_at": next_attempt_at,
                "leased_until": None,
                "last_error": self._sanitize_error(error),
            }
        )
        self._update_notification_queue(retried)

    def fail_notification(
        self,
        entry: TeamsNotificationQueueEntry,
        *,
        failed_at: datetime,
        error: str,
    ) -> None:
        failed = entry.model_copy(
            update={
                "state": "failed",
                "completed_at": failed_at,
                "leased_until": None,
                "last_error": self._sanitize_error(error),
            }
        )
        self._update_notification_queue(failed)

    def notification_status(
        self,
        *,
        enabled: bool,
        renewal_margin: timedelta,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = now or datetime.now(timezone.utc)
        owner_id = self.notification_owner_id()
        with self._lock, self._connect() as connection:
            subscriptions = [
                TeamsOwnedSubscription.model_validate_json(row["payload_json"])
                for row in connection.execute(
                    "SELECT payload_json FROM teams_inbox_subscriptions WHERE owner_id = ?",
                    (owner_id,),
                ).fetchall()
            ]
            queue_rows = connection.execute(
                """
                SELECT payload_json
                FROM teams_inbox_notification_queue
                WHERE state IN ('pending', 'retry', 'leased')
                ORDER BY received_at
                """
            ).fetchall()
            last_received = connection.execute(
                "SELECT MAX(received_at) AS value FROM teams_inbox_notification_queue"
            ).fetchone()["value"]
            last_ingestion_row = connection.execute(
                "SELECT value FROM teams_inbox_notification_meta WHERE key = 'last_ingestion_at'"
            ).fetchone()
            target_rows = connection.execute(
                """
                SELECT target, health, last_error
                FROM teams_inbox_notification_targets
                ORDER BY directory_path, target
                """
            ).fetchall()
        active = [
            item
            for item in subscriptions
            if item.health == "active" and self._ensure_utc(item.expires_at) > current
        ]
        expiring = [
            item for item in active if self._ensure_utc(item.expires_at) <= current + renewal_margin
        ]
        fallback_targets = sorted(
            {
                item.target
                for item in subscriptions
                if item.health not in {"deleted", "expired", "abandoned"}
                and (item.health != "active" or self._ensure_utc(item.expires_at) <= current)
            }
            | {
                str(row["target"])
                for row in target_rows
                if not enabled or str(row["health"]) != "active"
            }
        )
        queue = [
            TeamsNotificationQueueEntry.model_validate_json(row["payload_json"])
            for row in queue_rows
        ]
        degraded_reasons = sorted(
            {
                self._sanitize_error(item.last_error or item.health)
                for item in subscriptions
                if item.health not in {"active", "deleted", "expired", "abandoned"}
            }
            | {
                self._sanitize_error(str(row["last_error"] or "subscription unavailable"))
                for row in target_rows
                if enabled and str(row["health"]) != "active"
            }
        )
        return {
            "enabled": enabled,
            "active_subscriptions": len(active),
            "expiring_subscriptions": len(expiring),
            "targets_on_fast_fallback": fallback_targets,
            "queue_depth": len(queue),
            "oldest_queue_age_seconds": (
                max(0.0, (current - self._ensure_utc(queue[0].received_at)).total_seconds())
                if queue
                else None
            ),
            "last_notification_at": last_received,
            "last_ingestion_at": (
                str(last_ingestion_row["value"]) if last_ingestion_row is not None else None
            ),
            "degraded_reasons": degraded_reasons,
        }

    def get_poll_health(self, target_key: str) -> TeamsInboxPollHealth | None:
        normalized_key = str(target_key or "").strip()
        if not normalized_key:
            return None
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM teams_inbox_poll_health WHERE target_key = ?",
                (normalized_key,),
            ).fetchone()
        if row is None:
            return None
        return TeamsInboxPollHealth.model_validate_json(row["payload_json"])

    def record_poll_failure(
        self,
        target_key: str,
        *,
        next_attempt_at: datetime,
        error_class: str,
        error_message: str,
        failed_at: datetime,
    ) -> TeamsInboxPollHealth:
        normalized_key = str(target_key or "").strip()
        if not normalized_key:
            raise ValueError("target_key is required")
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM teams_inbox_poll_health WHERE target_key = ?",
                (normalized_key,),
            ).fetchone()
            previous = (
                TeamsInboxPollHealth.model_validate_json(row["payload_json"])
                if row is not None
                else None
            )
            state = TeamsInboxPollHealth(
                target_key=normalized_key,
                consecutive_failures=(previous.consecutive_failures if previous else 0) + 1,
                next_attempt_at=next_attempt_at,
                last_error_class=str(error_class or "").strip() or None,
                last_error_message=str(error_message or "").strip()[:1000] or None,
                last_failed_at=failed_at,
                last_success_at=previous.last_success_at if previous else None,
            )
            connection.execute(
                """
                INSERT INTO teams_inbox_poll_health(target_key, next_attempt_at, payload_json)
                VALUES (?, ?, ?)
                ON CONFLICT(target_key) DO UPDATE SET
                    next_attempt_at = excluded.next_attempt_at,
                    payload_json = excluded.payload_json
                """,
                (
                    normalized_key,
                    self._datetime_to_storage(next_attempt_at),
                    _json_dumps(state.model_dump(mode="json")),
                ),
            )
        return state

    def record_poll_success(
        self,
        target_key: str,
        *,
        succeeded_at: datetime,
    ) -> TeamsInboxPollHealth:
        normalized_key = str(target_key or "").strip()
        if not normalized_key:
            raise ValueError("target_key is required")
        state = TeamsInboxPollHealth(
            target_key=normalized_key,
            consecutive_failures=0,
            last_success_at=succeeded_at,
        )
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO teams_inbox_poll_health(target_key, next_attempt_at, payload_json)
                VALUES (?, NULL, ?)
                ON CONFLICT(target_key) DO UPDATE SET
                    next_attempt_at = NULL,
                    payload_json = excluded.payload_json
                """,
                (normalized_key, _json_dumps(state.model_dump(mode="json"))),
            )
        return state

    def get_discovered_chat_targets(self, scope_key: str) -> set[str]:
        normalized_scope = str(scope_key or "").strip()
        if not normalized_scope:
            return set()
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT channel_target
                FROM teams_inbox_discovered_chats
                WHERE scope_key = ?
                ORDER BY channel_target ASC
                """,
                (normalized_scope,),
            ).fetchall()
        return {str(row["channel_target"]) for row in rows}

    def replace_discovered_chat_targets(
        self,
        scope_key: str,
        channel_targets: set[str],
        *,
        discovered_at: datetime,
    ) -> None:
        normalized_scope = str(scope_key or "").strip()
        if not normalized_scope:
            raise ValueError("scope_key is required")
        normalized_targets = {
            str(target or "").strip().strip("/")
            for target in channel_targets
            if str(target or "").strip()
        }
        with self._lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM teams_inbox_discovered_chats WHERE scope_key = ?",
                (normalized_scope,),
            )
            connection.executemany(
                """
                INSERT INTO teams_inbox_discovered_chats(
                    scope_key, channel_target, discovered_at
                ) VALUES (?, ?, ?)
                """,
                [
                    (
                        normalized_scope,
                        target,
                        self._datetime_to_storage(discovered_at),
                    )
                    for target in sorted(normalized_targets)
                ],
            )

    def add_message_if_new(self, message: TeamsInboxMessage) -> bool:
        validated = TeamsInboxMessage.model_validate(message)
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO teams_inbox_messages(
                    cursor,
                    message_id,
                    channel_target,
                    team_id,
                    channel_id,
                    chat_id,
                    thread_id,
                    author_id,
                    created_at,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._message_row(validated),
            )
            if cursor.rowcount == 0:
                return False
            self._compact_messages(connection)
            return True

    def get_message(self, cursor: str) -> TeamsInboxMessage | None:
        normalized_cursor = str(cursor or "").strip()
        if not normalized_cursor:
            return None
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM teams_inbox_messages
                WHERE cursor = ?
                """,
                (normalized_cursor,),
            ).fetchone()
        if row is None:
            return None
        return TeamsInboxMessage.model_validate_json(row["payload_json"])

    def find_message(
        self,
        message_id: str,
        *,
        channel_target: str | None = None,
    ) -> TeamsInboxMessage | None:
        normalized_message_id = str(message_id or "").strip()
        if not normalized_message_id:
            return None
        where = ["message_id = ?"]
        params: list[Any] = [normalized_message_id]
        if channel_target is not None:
            normalized_target = str(channel_target or "").strip().strip("/")
            if not normalized_target:
                return None
            where.append("channel_target = ?")
            params.append(normalized_target)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                f"""
                SELECT payload_json
                FROM teams_inbox_messages
                WHERE {" AND ".join(where)}
                ORDER BY created_at DESC, cursor DESC
                LIMIT 1
                """,
                params,
            ).fetchone()
        if row is None:
            return None
        return TeamsInboxMessage.model_validate_json(row["payload_json"])

    def list_all_messages(
        self,
        *,
        channel_targets: list[str] | None = None,
        created_at_from: datetime | None = None,
        direct_messages_only: bool = False,
        limit: int | None = None,
    ) -> list[TeamsInboxMessage]:
        allowed_targets = None
        if channel_targets is not None:
            allowed_targets = {
                target.strip().strip("/") for target in channel_targets if target.strip()
            }
            if not allowed_targets:
                return []

        where: list[str] = []
        params: list[Any] = []
        if allowed_targets is not None:
            placeholders = ", ".join("?" for _ in allowed_targets)
            where.append(f"channel_target IN ({placeholders})")
            params.extend(sorted(allowed_targets))
        if created_at_from is not None:
            where.append("created_at >= ?")
            params.append(self._datetime_to_storage(created_at_from))
        if direct_messages_only:
            where.append("chat_id IS NOT NULL AND chat_id != ''")

        sql = "SELECT payload_json FROM teams_inbox_messages"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at ASC, cursor ASC"
        if limit is not None:
            if limit <= 0:
                return []
            sql += " LIMIT ?"
            params.append(limit)

        with self._lock, self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [TeamsInboxMessage.model_validate_json(row["payload_json"]) for row in rows]

    def read_raw(self) -> dict[str, Any]:
        with self._lock, self._connect() as connection:
            cursor_rows = connection.execute(
                """
                SELECT channel_target, payload_json
                FROM teams_inbox_cursors
                ORDER BY channel_target ASC
                """
            ).fetchall()
            message_rows = connection.execute(
                """
                SELECT cursor, payload_json
                FROM teams_inbox_messages
                ORDER BY created_at ASC, cursor ASC
                """
            ).fetchall()
            health_rows = connection.execute(
                """
                SELECT target_key, payload_json
                FROM teams_inbox_poll_health
                ORDER BY target_key ASC
                """
            ).fetchall()
        return {
            "cursors": {
                str(row["channel_target"]): TeamsInboxCursorState.model_validate_json(
                    row["payload_json"]
                ).model_dump(mode="json")
                for row in cursor_rows
            },
            "messages": {
                str(row["cursor"]): TeamsInboxMessage.model_validate_json(
                    row["payload_json"]
                ).model_dump(mode="json")
                for row in message_rows
            },
            "poll_health": {
                str(row["target_key"]): TeamsInboxPollHealth.model_validate_json(
                    row["payload_json"]
                ).model_dump(mode="json")
                for row in health_rows
            },
        }

    def ensure_schema(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS teams_inbox_messages (
                    cursor TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL,
                    channel_target TEXT NOT NULL,
                    team_id TEXT NOT NULL DEFAULT '',
                    channel_id TEXT NOT NULL DEFAULT '',
                    chat_id TEXT,
                    thread_id TEXT,
                    author_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_teams_inbox_messages_target_created
                    ON teams_inbox_messages(channel_target, created_at, cursor);
                CREATE INDEX IF NOT EXISTS idx_teams_inbox_messages_created
                    ON teams_inbox_messages(created_at, cursor);
                CREATE TABLE IF NOT EXISTS teams_inbox_cursors (
                    channel_target TEXT PRIMARY KEY,
                    cursor TEXT NOT NULL,
                    created_at TEXT,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS teams_inbox_poll_health (
                    target_key TEXT PRIMARY KEY,
                    next_attempt_at TEXT,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_teams_inbox_poll_health_next_attempt
                    ON teams_inbox_poll_health(next_attempt_at, target_key);
                CREATE TABLE IF NOT EXISTS teams_inbox_discovered_chats (
                    scope_key TEXT NOT NULL,
                    channel_target TEXT NOT NULL,
                    discovered_at TEXT NOT NULL,
                    PRIMARY KEY(scope_key, channel_target)
                );
                CREATE TABLE IF NOT EXISTS teams_inbox_notification_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS teams_inbox_subscriptions (
                    subscription_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    target TEXT NOT NULL,
                    resource TEXT NOT NULL,
                    directory_path TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    health TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_teams_inbox_subscriptions_target
                    ON teams_inbox_subscriptions(directory_path, tenant_id, target, expires_at);
                CREATE TABLE IF NOT EXISTS teams_inbox_subscription_reconciliation (
                    directory_path TEXT NOT NULL,
                    target TEXT NOT NULL,
                    next_attempt_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY(directory_path, target)
                );
                CREATE INDEX IF NOT EXISTS idx_teams_inbox_subscription_reconciliation_attempt
                    ON teams_inbox_subscription_reconciliation(next_attempt_at, directory_path, target);
                CREATE TABLE IF NOT EXISTS teams_inbox_notification_targets (
                    directory_path TEXT NOT NULL,
                    target TEXT NOT NULL,
                    health TEXT NOT NULL,
                    last_error TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(directory_path, target)
                );
                CREATE TABLE IF NOT EXISTS teams_inbox_notification_queue (
                    idempotency_key TEXT PRIMARY KEY,
                    notification_kind TEXT NOT NULL,
                    subscription_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    resource TEXT NOT NULL,
                    state TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    next_attempt_at TEXT NOT NULL,
                    leased_until TEXT,
                    completed_at TEXT,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_teams_inbox_notification_queue_lease
                    ON teams_inbox_notification_queue(state, next_attempt_at, leased_until);
                CREATE INDEX IF NOT EXISTS idx_teams_inbox_notification_queue_retention
                    ON teams_inbox_notification_queue(state, completed_at);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
        self._enable_wal(connection)
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

    def _compact_messages(self, connection: sqlite3.Connection) -> None:
        cutoff = self._datetime_to_storage(datetime.now(timezone.utc) - _TEAMS_MESSAGE_RETENTION)
        connection.execute(
            """
            DELETE FROM teams_inbox_messages
            WHERE created_at < ?
              AND EXISTS (
                  SELECT 1
                  FROM teams_inbox_cursors AS cursors
                  WHERE cursors.channel_target = teams_inbox_messages.channel_target
                    AND cursors.created_at IS NOT NULL
                    AND cursors.created_at >= ?
                    AND (
                        teams_inbox_messages.created_at < cursors.created_at
                        OR (
                            teams_inbox_messages.created_at = cursors.created_at
                            AND teams_inbox_messages.cursor <= cursors.cursor
                        )
                    )
              )
            """,
            (cutoff, cutoff),
        )

    def _compact_notifications(self, connection: sqlite3.Connection) -> None:
        now = datetime.now(timezone.utc)
        connection.execute(
            "DELETE FROM teams_inbox_notification_queue WHERE state = 'completed' AND completed_at < ?",
            (self._datetime_to_storage(now - _COMPLETED_NOTIFICATION_RETENTION),),
        )
        connection.execute(
            "DELETE FROM teams_inbox_notification_queue WHERE state = 'failed' AND completed_at < ?",
            (self._datetime_to_storage(now - _FAILED_NOTIFICATION_RETENTION),),
        )

    def _update_notification_queue(self, entry: TeamsNotificationQueueEntry) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE teams_inbox_notification_queue
                SET state = ?, next_attempt_at = ?, leased_until = ?, completed_at = ?,
                    payload_json = ?
                WHERE idempotency_key = ?
                """,
                (
                    entry.state,
                    self._datetime_to_storage(entry.next_attempt_at),
                    (
                        self._datetime_to_storage(entry.leased_until)
                        if entry.leased_until is not None
                        else None
                    ),
                    (
                        self._datetime_to_storage(entry.completed_at)
                        if entry.completed_at is not None
                        else None
                    ),
                    _json_dumps(entry.model_dump(mode="json")),
                    entry.idempotency_key,
                ),
            )
            self._compact_notifications(connection)

    def _notification_queue_row(
        self, entry: TeamsNotificationQueueEntry
    ) -> tuple[str, str, str, str, str, str, str, str, str | None, str | None, str]:
        return (
            entry.idempotency_key,
            entry.notification_kind,
            entry.subscription_id,
            entry.tenant_id,
            entry.resource,
            entry.state,
            self._datetime_to_storage(entry.received_at),
            self._datetime_to_storage(entry.next_attempt_at),
            (
                self._datetime_to_storage(entry.leased_until)
                if entry.leased_until is not None
                else None
            ),
            (
                self._datetime_to_storage(entry.completed_at)
                if entry.completed_at is not None
                else None
            ),
            _json_dumps(entry.model_dump(mode="json")),
        )

    def _message_row(
        self, message: TeamsInboxMessage
    ) -> tuple[str, str, str, str, str, str | None, str | None, str, str, str]:
        return (
            message.cursor,
            message.message_id,
            self._channel_target(message),
            message.team_id,
            message.channel_id,
            message.chat_id,
            message.thread_id,
            message.author_id,
            self._datetime_to_storage(message.created_at),
            _json_dumps(message.model_dump(mode="json")),
        )

    def _cursor_row(
        self, channel_target: str, state: TeamsInboxCursorState
    ) -> tuple[str, str, str | None, str]:
        return (
            channel_target,
            state.cursor,
            self._datetime_to_storage(state.created_at) if state.created_at is not None else None,
            _json_dumps(state.model_dump(mode="json")),
        )

    @staticmethod
    def _channel_target(message: TeamsInboxMessage) -> str:
        if message.chat_id:
            return f"chat:{message.chat_id}"
        return f"{message.team_id}/{message.channel_id}"

    @staticmethod
    def _message_sort_key(message: TeamsInboxMessage) -> tuple[datetime, str]:
        return (message.created_at, message.cursor)

    @staticmethod
    def _cursor_state_sort_key(state: TeamsInboxCursorState) -> tuple[datetime, str]:
        if state.created_at is None:
            return (datetime.min.replace(tzinfo=timezone.utc), state.cursor)
        return (state.created_at, state.cursor)

    @staticmethod
    def _ensure_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _sanitize_error(error: str) -> str:
        text = " ".join(str(error or "").split())
        for marker in ("Bearer ", "access_token", "refresh_token", "clientState"):
            if marker.casefold() in text.casefold():
                return "redacted notification error"
        return text[:500]

    @staticmethod
    def _datetime_to_storage(value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return (
            value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
        )
