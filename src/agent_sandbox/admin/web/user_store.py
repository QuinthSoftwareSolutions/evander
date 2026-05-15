from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from agent_sandbox.models import utc_now
from agent_sandbox.storage import JsonStore

AdminWebUserRole = Literal["admin", "regular"]

_UNSET = object()


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
    def __init__(self, path: Path) -> None:
        self.path = path
        self._store: JsonStore[AdminWebUserRecord] | None = None

    def _json_store(self) -> JsonStore[AdminWebUserRecord]:
        if self._store is None:
            self._store = JsonStore(self.path, AdminWebUserRecord)
        return self._store

    def list_users(self) -> list[AdminWebUserRecord]:
        return sorted(
            self._json_store().read_all().values(),
            key=lambda user: user.login,
        )

    def get_user(self, login: object) -> AdminWebUserRecord | None:
        normalized = normalize_admin_web_login(login)
        if normalized is None:
            return None
        return self._json_store().get(normalized)

    def bootstrap_admins(self, logins: set[str] | frozenset[str]) -> list[AdminWebUserRecord]:
        def update(items: dict[str, AdminWebUserRecord]) -> list[AdminWebUserRecord]:
            if items:
                return []
            now = utc_now()
            records: dict[str, AdminWebUserRecord] = {}
            for raw_login in sorted(logins):
                login = normalize_admin_web_login(raw_login)
                if login is None:
                    continue
                records[login] = AdminWebUserRecord(
                    login=login,
                    role="admin",
                    created_at=now,
                    updated_at=now,
                )
            items.update(records)
            return sorted(records.values(), key=lambda user: user.login)

        return self._json_store().update_all(update)

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

        def update(items: dict[str, AdminWebUserRecord]) -> AdminWebUserRecord:
            existing = items.get(normalized)
            if existing is None and not items and role != "admin":
                raise ValueError("First admin web user must have the admin role")
            self._ensure_not_last_admin_demotion(items, existing=existing, next_role=role)
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
            items[normalized] = record
            return record

        return self._json_store().update_all(update)

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

        def update(items: dict[str, AdminWebUserRecord]) -> AdminWebUserRecord | None:
            existing = items.get(normalized)
            if existing is None:
                return None
            next_role = role or existing.role
            self._ensure_not_last_admin_demotion(items, existing=existing, next_role=next_role)
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
            items[normalized] = record
            return record

        return self._json_store().update_all(update)

    def delete_user(self, login: object) -> AdminWebUserRecord | None:
        normalized = normalize_admin_web_login(login)
        if normalized is None:
            raise ValueError("login is required")

        def update(items: dict[str, AdminWebUserRecord]) -> AdminWebUserRecord | None:
            existing = items.get(normalized)
            if existing is None:
                return None
            if existing.role == "admin" and self._admin_count(items) <= 1:
                raise ValueError("Cannot remove the last admin user")
            del items[normalized]
            return existing

        return self._json_store().update_all(update)

    def record_login(
        self, *, login: object, display_name: object | None = None
    ) -> AdminWebUserRecord | None:
        normalized = normalize_admin_web_login(login)
        if normalized is None:
            return None

        def update(items: dict[str, AdminWebUserRecord]) -> AdminWebUserRecord | None:
            existing = items.get(normalized)
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
            items[normalized] = record
            return record

        return self._json_store().update_all(update)

    def _ensure_not_last_admin_demotion(
        self,
        items: dict[str, AdminWebUserRecord],
        *,
        existing: AdminWebUserRecord | None,
        next_role: AdminWebUserRole,
    ) -> None:
        if existing is None or existing.role != "admin" or next_role == "admin":
            return
        if self._admin_count(items) <= 1:
            raise ValueError("Cannot demote the last admin user")

    def _admin_count(self, items: dict[str, AdminWebUserRecord]) -> int:
        return sum(1 for user in items.values() if user.role == "admin")


__all__ = [
    "AdminWebUserRecord",
    "AdminWebUserRole",
    "AdminWebUserStore",
    "normalize_admin_web_login",
]
