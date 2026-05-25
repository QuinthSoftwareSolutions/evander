from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Literal, Mapping

from pydantic import BaseModel, Field, field_validator

from agent_sandbox.models import utc_now

AdminWebUserRole = Literal["admin", "regular"]

_UNSET = object()
SQLITE_BUSY_TIMEOUT_MS = 30_000


def normalize_admin_web_login(value: object) -> str | None:
    login = str(value or "").strip().lower()
    return login or None


def _normalize_display_name(value: object) -> str | None:
    if value is None:
        return None
    display_name = str(value).strip()
    return display_name or None


class AdminWebUserRecord(BaseModel):
    login: str
    role: AdminWebUserRole
    display_name: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    last_login_at: datetime | None = None

    @field_validator("login", mode="before")
    @classmethod
    def normalize_login(cls, value: object) -> str:
        login = normalize_admin_web_login(value)
        if login is None:
            raise ValueError("login is required")
        return login

    @field_validator("display_name", mode="before")
    @classmethod
    def normalize_display_name(cls, value: object) -> str | None:
        return _normalize_display_name(value)


class AdminWebUserStore:
    def __init__(self, path: Path, *, legacy_json_path: Path | None = None) -> None:
        if path.suffix == ".json":
            self.path = path.with_name("admin-web.db")
            self.legacy_json_path = legacy_json_path or path
        else:
            self.path = path
            self.legacy_json_path = legacy_json_path
        self._lock = RLock()
        self._initialized = False

    def list_users(self) -> list[AdminWebUserRecord]:
        self._ensure_ready()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT login, role, display_name, created_at, updated_at, last_login_at
                FROM admin_web_users
                ORDER BY login
                """
            ).fetchall()
        return [self._row_to_user(row) for row in rows]

    def get_user(self, login: object) -> AdminWebUserRecord | None:
        normalized = normalize_admin_web_login(login)
        if normalized is None:
            return None
        self._ensure_ready()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT login, role, display_name, created_at, updated_at, last_login_at
                FROM admin_web_users
                WHERE login = ?
                """,
                (normalized,),
            ).fetchone()
        return self._row_to_user(row) if row is not None else None

    def bootstrap_admins(self, logins: set[str] | frozenset[str]) -> list[AdminWebUserRecord]:
        self._ensure_ready()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if self._user_count(connection) > 0:
                return []
            now = utc_now()
            records: list[AdminWebUserRecord] = []
            for raw_login in sorted(logins):
                login = normalize_admin_web_login(raw_login)
                if login is None:
                    continue
                record = AdminWebUserRecord(
                    login=login,
                    role="admin",
                    created_at=now,
                    updated_at=now,
                )
                self._insert_or_replace_user(connection, record)
                records.append(record)
            return sorted(records, key=lambda user: user.login)

    def upsert_user(
        self,
        *,
        login: object,
        role: AdminWebUserRole,
        display_name: object = _UNSET,
    ) -> AdminWebUserRecord:
        normalized = normalize_admin_web_login(login)
        if normalized is None:
            raise ValueError("login is required")
        if role not in {"admin", "regular"}:
            raise ValueError("role must be admin or regular")
        self._ensure_ready()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._get_user(connection, normalized)
            if existing is None and self._user_count(connection) == 0 and role != "admin":
                raise ValueError("First admin web user must have the admin role")
            self._ensure_not_last_admin_demotion(connection, existing=existing, next_role=role)
            now = utc_now()
            record = AdminWebUserRecord(
                login=normalized,
                role=role,
                display_name=(
                    existing.display_name
                    if display_name is _UNSET and existing is not None
                    else None
                    if display_name is _UNSET
                    else _normalize_display_name(display_name)
                ),
                created_at=existing.created_at if existing is not None else now,
                updated_at=now,
                last_login_at=existing.last_login_at if existing is not None else None,
            )
            self._insert_or_replace_user(connection, record)
            return record

    def update_user(
        self,
        *,
        login: object,
        role: AdminWebUserRole | None = None,
        display_name: object = _UNSET,
    ) -> AdminWebUserRecord | None:
        normalized = normalize_admin_web_login(login)
        if normalized is None:
            raise ValueError("login is required")
        if role is not None and role not in {"admin", "regular"}:
            raise ValueError("role must be admin or regular")
        self._ensure_ready()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._get_user(connection, normalized)
            if existing is None:
                return None
            next_role = role or existing.role
            self._ensure_not_last_admin_demotion(
                connection, existing=existing, next_role=next_role
            )
            record = AdminWebUserRecord(
                login=existing.login,
                role=next_role,
                display_name=(
                    existing.display_name
                    if display_name is _UNSET
                    else _normalize_display_name(display_name)
                ),
                created_at=existing.created_at,
                updated_at=utc_now(),
                last_login_at=existing.last_login_at,
            )
            self._insert_or_replace_user(connection, record)
            return record

    def delete_user(self, login: object) -> AdminWebUserRecord | None:
        normalized = normalize_admin_web_login(login)
        if normalized is None:
            raise ValueError("login is required")
        self._ensure_ready()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._get_user(connection, normalized)
            if existing is None:
                return None
            if existing.role == "admin" and self._admin_count(connection) <= 1:
                raise ValueError("Cannot remove the last admin user")
            connection.execute("DELETE FROM admin_web_users WHERE login = ?", (normalized,))
            connection.execute(
                "DELETE FROM admin_web_user_preferences WHERE login = ?", (normalized,)
            )
            return existing

    def record_login(
        self, *, login: object, display_name: object | None = None
    ) -> AdminWebUserRecord | None:
        normalized = normalize_admin_web_login(login)
        if normalized is None:
            return None
        self._ensure_ready()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._get_user(connection, normalized)
            if existing is None:
                return None
            now = utc_now()
            record = AdminWebUserRecord(
                login=existing.login,
                role=existing.role,
                display_name=existing.display_name or _normalize_display_name(display_name),
                created_at=existing.created_at,
                updated_at=now,
                last_login_at=now,
            )
            self._insert_or_replace_user(connection, record)
            return record

    def get_user_preference(self, login: object, key: str) -> dict[str, Any] | None:
        normalized = normalize_admin_web_login(login)
        normalized_key = str(key or "").strip()
        if normalized is None or not normalized_key:
            return None
        self._ensure_ready()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT value_json
                FROM admin_web_user_preferences
                WHERE login = ? AND preference_key = ?
                """,
                (normalized, normalized_key),
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(str(row["value_json"]))
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def put_user_preference(
        self, login: object, key: str, value: Mapping[str, Any]
    ) -> dict[str, Any]:
        normalized = normalize_admin_web_login(login)
        normalized_key = str(key or "").strip()
        if normalized is None:
            raise ValueError("login is required")
        if not normalized_key:
            raise ValueError("preference key is required")
        payload = dict(value)
        now = _datetime_to_storage(utc_now())
        self._ensure_ready()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT created_at
                FROM admin_web_user_preferences
                WHERE login = ? AND preference_key = ?
                """,
                (normalized, normalized_key),
            ).fetchone()
            created_at = str(row["created_at"]) if row is not None else now
            connection.execute(
                """
                INSERT OR REPLACE INTO admin_web_user_preferences(
                    login,
                    preference_key,
                    value_json,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    normalized,
                    normalized_key,
                    _json_dumps(payload),
                    created_at,
                    now,
                ),
            )
        return payload

    def ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS admin_web_users (
                    login TEXT PRIMARY KEY,
                    role TEXT NOT NULL CHECK(role IN ('admin', 'regular')),
                    display_name TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_login_at TEXT
                );

                CREATE TABLE IF NOT EXISTS admin_web_user_preferences (
                    login TEXT NOT NULL,
                    preference_key TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (login, preference_key)
                );

                CREATE INDEX IF NOT EXISTS idx_admin_web_user_preferences_login
                    ON admin_web_user_preferences(login, preference_key);
                """
            )

    def _ensure_ready(self) -> None:
        with self._lock:
            if self._initialized:
                return
            self.ensure_schema()
            self._import_legacy_json_if_empty()
            self._initialized = True

    def _import_legacy_json_if_empty(self) -> None:
        if self.legacy_json_path is None or not self.legacy_json_path.exists():
            return
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if self._user_count(connection) > 0:
                return
            raw = json.loads(self.legacy_json_path.read_text(encoding="utf-8") or "{}")
            if not isinstance(raw, dict):
                raise ValueError(
                    f"legacy admin web user store {self.legacy_json_path} must contain a JSON object"
                )
            for value in raw.values():
                record = AdminWebUserRecord.model_validate(value)
                self._insert_or_replace_user(connection, record)

    def _ensure_not_last_admin_demotion(
        self,
        connection: sqlite3.Connection,
        *,
        existing: AdminWebUserRecord | None,
        next_role: AdminWebUserRole,
    ) -> None:
        if existing is None or existing.role != "admin" or next_role == "admin":
            return
        if self._admin_count(connection) <= 1:
            raise ValueError("Cannot demote the last admin user")

    def _admin_count(self, connection: sqlite3.Connection) -> int:
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM admin_web_users WHERE role = 'admin'"
        ).fetchone()
        return int(row["count"] if row is not None else 0)

    def _user_count(self, connection: sqlite3.Connection) -> int:
        row = connection.execute("SELECT COUNT(*) AS count FROM admin_web_users").fetchone()
        return int(row["count"] if row is not None else 0)

    def _get_user(
        self, connection: sqlite3.Connection, login: str
    ) -> AdminWebUserRecord | None:
        row = connection.execute(
            """
            SELECT login, role, display_name, created_at, updated_at, last_login_at
            FROM admin_web_users
            WHERE login = ?
            """,
            (login,),
        ).fetchone()
        return self._row_to_user(row) if row is not None else None

    def _insert_or_replace_user(
        self, connection: sqlite3.Connection, record: AdminWebUserRecord
    ) -> None:
        connection.execute(
            """
            INSERT OR REPLACE INTO admin_web_users(
                login,
                role,
                display_name,
                created_at,
                updated_at,
                last_login_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                record.login,
                record.role,
                record.display_name,
                _datetime_to_storage(record.created_at),
                _datetime_to_storage(record.updated_at),
                _datetime_to_storage(record.last_login_at),
            ),
        )

    def _row_to_user(self, row: sqlite3.Row) -> AdminWebUserRecord:
        return AdminWebUserRecord.model_validate(
            {
                "login": row["login"],
                "role": row["role"],
                "display_name": row["display_name"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "last_login_at": row["last_login_at"],
            }
        )

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection


def _datetime_to_storage(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _json_dumps(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


__all__ = [
    "AdminWebUserRecord",
    "AdminWebUserRole",
    "AdminWebUserStore",
    "normalize_admin_web_login",
]
