from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Iterable, Literal, Mapping

from pydantic import BaseModel, Field, field_validator

from evander.models import utc_now
from evander.admin.web.rbac import (
    ADMIN_OWNER_PERMISSION,
    BUILTIN_GROUP_DEFINITIONS,
    DIRECT_WORKFLOW_PERMISSIONS,
    OWNER_GROUP_ID,
    OPERATOR_GROUP_ID,
    PERMISSION_CATALOG,
    can as rbac_can,
    effective_permissions as rbac_effective_permissions,
    normalize_admin_web_directory_path,
    normalize_admin_web_group_id,
    normalize_admin_web_permission,
)

_UNSET = object()
SQLITE_BUSY_TIMEOUT_MS = 30_000
LOCAL_PASSWORD_ALGORITHM = "pbkdf2_sha256"
LOCAL_PASSWORD_HASH_NAME = "sha256"
LOCAL_PASSWORD_ITERATIONS = 200_000
LOCAL_PASSWORD_SALT_BYTES = 32


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


class AdminWebLocalPasswordCredentialRecord(BaseModel):
    login: str
    algorithm: str
    iterations: int
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("login", mode="before")
    @classmethod
    def normalize_login(cls, value: object) -> str:
        login = normalize_admin_web_login(value)
        if login is None:
            raise ValueError("login is required")
        return login


class AdminWebGroupRecord(BaseModel):
    group_id: str
    display_name: str
    description: str | None = None
    is_required: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    permissions: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("group_id", mode="before")
    @classmethod
    def normalize_group_id(cls, value: object) -> str:
        group_id = normalize_admin_web_group_id(value)
        if group_id is None:
            raise ValueError("group_id is required")
        return group_id

    @field_validator("display_name", mode="before")
    @classmethod
    def normalize_display_name(cls, value: object) -> str:
        display_name = _normalize_display_name(value)
        if display_name is None:
            raise ValueError("display_name is required")
        return display_name

    @field_validator("permissions", mode="before")
    @classmethod
    def normalize_permissions(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, tuple):
            values = value
        elif isinstance(value, list):
            values = tuple(value)
        else:
            values = (value,)
        normalized: list[str] = []
        seen: set[str] = set()
        for entry in values:
            permission = normalize_admin_web_permission(entry)
            if permission is None or permission in seen:
                continue
            if permission not in PERMISSION_CATALOG:
                raise ValueError(f"unknown permission: {permission}")
            seen.add(permission)
            normalized.append(permission)
        return tuple(sorted(normalized))


class AdminWebGroupPermissionRecord(BaseModel):
    group_id: str
    permission: str
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("group_id", mode="before")
    @classmethod
    def normalize_group_id(cls, value: object) -> str:
        group_id = normalize_admin_web_group_id(value)
        if group_id is None:
            raise ValueError("group_id is required")
        return group_id

    @field_validator("permission", mode="before")
    @classmethod
    def normalize_permission(cls, value: object) -> str:
        permission = normalize_admin_web_permission(value)
        if permission is None:
            raise ValueError("permission is required")
        if permission not in PERMISSION_CATALOG:
            raise ValueError(f"unknown permission: {permission}")
        return permission


class AdminWebUserGroupAssignmentRecord(BaseModel):
    login: str
    group_id: str
    directory_path: str = "root"
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("login", mode="before")
    @classmethod
    def normalize_login(cls, value: object) -> str:
        login = normalize_admin_web_login(value)
        if login is None:
            raise ValueError("login is required")
        return login

    @field_validator("group_id", mode="before")
    @classmethod
    def normalize_group_id(cls, value: object) -> str:
        group_id = normalize_admin_web_group_id(value)
        if group_id is None:
            raise ValueError("group_id is required")
        return group_id

    @field_validator("directory_path", mode="before")
    @classmethod
    def normalize_directory_path(cls, value: object) -> str:
        return normalize_admin_web_directory_path(value)


class AdminWebWorkflowGrantRecord(BaseModel):
    login: str
    permission: Literal["workflows.read", "workflows.write"]
    directory_path: str = "root"
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("login", mode="before")
    @classmethod
    def normalize_login(cls, value: object) -> str:
        login = normalize_admin_web_login(value)
        if login is None:
            raise ValueError("login is required")
        return login

    @field_validator("permission", mode="before")
    @classmethod
    def normalize_permission(cls, value: object) -> str:
        permission = normalize_admin_web_permission(value)
        if permission is None:
            raise ValueError("permission is required")
        if permission not in DIRECT_WORKFLOW_PERMISSIONS:
            raise ValueError(f"unknown direct workflow permission: {permission}")
        return permission  # type: ignore[return-value]

    @field_validator("directory_path", mode="before")
    @classmethod
    def normalize_directory_path(cls, value: object) -> str:
        return normalize_admin_web_directory_path(value)


class AdminWebUserStore:
    def __init__(self, path: Path) -> None:
        if path.suffix == ".json":
            self.path = path.with_name("admin-web.db")
        else:
            self.path = path
        self._lock = RLock()
        self._initialized = False

    def list_users(self) -> list[AdminWebUserRecord]:
        self._ensure_ready()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT login, display_name, created_at, updated_at, last_login_at
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
                SELECT login, display_name, created_at, updated_at, last_login_at
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
                    created_at=now,
                    updated_at=now,
                )
                self._insert_or_replace_user(connection, record)
                self._store_user_group_assignment(
                    connection,
                    AdminWebUserGroupAssignmentRecord(
                        login=login,
                        group_id=OWNER_GROUP_ID,
                        directory_path="root",
                        created_at=now,
                    ),
                )
                records.append(record)
            if records:
                self.ensure_effective_owner_exists(connection)
            return sorted(records, key=lambda user: user.login)

    def bootstrap_local_password_admin(
        self, *, login: object, password: object
    ) -> AdminWebUserRecord | None:
        normalized = normalize_admin_web_login(login)
        if normalized is None:
            raise ValueError("login is required")
        self._validate_local_password(password)
        self._ensure_ready()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if self._user_count(connection) > 0:
                return None
            now = utc_now()
            record = AdminWebUserRecord(
                login=normalized,
                display_name=_normalize_display_name(login),
                created_at=now,
                updated_at=now,
            )
            self._insert_or_replace_user(connection, record)
            self._store_user_group_assignment(
                connection,
                AdminWebUserGroupAssignmentRecord(
                    login=normalized,
                    group_id=OWNER_GROUP_ID,
                    directory_path="root",
                    created_at=now,
                ),
            )
            self._store_local_password(connection, login=normalized, password=str(password))
            self.ensure_effective_owner_exists(connection)
            return record

    def upsert_user(
        self,
        *,
        login: object,
        display_name: object = _UNSET,
    ) -> AdminWebUserRecord:
        normalized = normalize_admin_web_login(login)
        if normalized is None:
            raise ValueError("login is required")
        self._ensure_ready()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._get_user(connection, normalized)
            first_user = existing is None and self._user_count(connection) == 0
            now = utc_now()
            record = AdminWebUserRecord(
                login=normalized,
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
            if existing is None:
                self._store_user_group_assignment(
                    connection,
                    AdminWebUserGroupAssignmentRecord(
                        login=normalized,
                        group_id=OWNER_GROUP_ID if first_user else OPERATOR_GROUP_ID,
                        directory_path="root",
                        created_at=now,
                    ),
                )
            self.ensure_effective_owner_exists(connection)
            return record

    def update_user(
        self,
        *,
        login: object,
        display_name: object = _UNSET,
    ) -> AdminWebUserRecord | None:
        normalized = normalize_admin_web_login(login)
        if normalized is None:
            raise ValueError("login is required")
        self._ensure_ready()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._get_user(connection, normalized)
            if existing is None:
                return None
            record = AdminWebUserRecord(
                login=existing.login,
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
            self.ensure_effective_owner_exists(connection)
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
            self._delete_user_related_rows(connection, normalized)
            connection.execute("DELETE FROM admin_web_users WHERE login = ?", (normalized,))
            self.ensure_effective_owner_exists(connection)
            return existing

    def set_local_password(self, *, login: object, password: object) -> AdminWebUserRecord:
        normalized = normalize_admin_web_login(login)
        if normalized is None:
            raise ValueError("login is required")
        self._validate_local_password(password)
        self._ensure_ready()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._get_user(connection, normalized)
            if existing is None:
                raise ValueError("Admin web user not found")
            self._store_local_password(connection, login=normalized, password=str(password))
            return existing

    def verify_local_password(
        self, *, login: object, password: object
    ) -> AdminWebUserRecord | None:
        normalized = normalize_admin_web_login(login)
        if normalized is None or not isinstance(password, str):
            return None
        self._ensure_ready()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT algorithm, password_hash, salt, iterations
                FROM admin_web_local_password_credentials
                WHERE login = ?
                """,
                (normalized,),
            ).fetchone()
        if row is None or str(row["algorithm"]) != LOCAL_PASSWORD_ALGORITHM:
            return None
        try:
            iterations = int(row["iterations"])
            salt = bytes.fromhex(str(row["salt"]))
            expected_hash = str(row["password_hash"])
        except (TypeError, ValueError):
            return None
        actual_hash = _hash_local_password(
            password=password,
            salt=salt,
            iterations=iterations,
        )
        if not hmac.compare_digest(actual_hash, expected_hash):
            return None
        return self.record_login(login=normalized)

    def get_local_password_credential(
        self, login: object
    ) -> AdminWebLocalPasswordCredentialRecord | None:
        normalized = normalize_admin_web_login(login)
        if normalized is None:
            return None
        self._ensure_ready()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT login, algorithm, iterations, created_at, updated_at
                FROM admin_web_local_password_credentials
                WHERE login = ?
                """,
                (normalized,),
            ).fetchone()
        if row is None:
            return None
        return AdminWebLocalPasswordCredentialRecord.model_validate(
            {
                "login": row["login"],
                "algorithm": row["algorithm"],
                "iterations": row["iterations"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )

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
                display_name=existing.display_name or _normalize_display_name(display_name),
                created_at=existing.created_at,
                updated_at=now,
                last_login_at=now,
            )
            self._insert_or_replace_user(connection, record)
            return record

    def list_groups(self) -> list[AdminWebGroupRecord]:
        self._ensure_ready()
        with self._connect() as connection:
            return self._list_groups(connection)

    def get_group(self, group_id: object) -> AdminWebGroupRecord | None:
        normalized = normalize_admin_web_group_id(group_id)
        if normalized is None:
            return None
        self._ensure_ready()
        with self._connect() as connection:
            return self._get_group(connection, normalized)

    def list_group_permissions(
        self, group_id: object | None = None
    ) -> list[AdminWebGroupPermissionRecord]:
        self._ensure_ready()
        with self._connect() as connection:
            if group_id is None:
                rows = connection.execute(
                    """
                    SELECT group_id, permission, created_at
                    FROM admin_web_group_permissions
                    ORDER BY group_id, permission
                    """
                ).fetchall()
            else:
                normalized_group_id = normalize_admin_web_group_id(group_id)
                if normalized_group_id is None:
                    return []
                rows = connection.execute(
                    """
                    SELECT group_id, permission, created_at
                    FROM admin_web_group_permissions
                    WHERE group_id = ?
                    ORDER BY permission
                    """,
                    (normalized_group_id,),
                ).fetchall()
        return [self._row_to_group_permission(row) for row in rows]

    def list_user_group_assignments(
        self, login: object | None = None
    ) -> list[AdminWebUserGroupAssignmentRecord]:
        self._ensure_ready()
        with self._connect() as connection:
            if login is None:
                rows = connection.execute(
                    """
                    SELECT login, group_id, directory_path, created_at
                    FROM admin_web_user_group_assignments
                    ORDER BY login, group_id, directory_path
                    """
                ).fetchall()
            else:
                normalized_login = normalize_admin_web_login(login)
                if normalized_login is None:
                    return []
                rows = connection.execute(
                    """
                    SELECT login, group_id, directory_path, created_at
                    FROM admin_web_user_group_assignments
                    WHERE login = ?
                    ORDER BY group_id, directory_path
                    """,
                    (normalized_login,),
                ).fetchall()
        return [self._row_to_user_group_assignment(row) for row in rows]

    def list_user_workflow_grants(
        self, login: object | None = None
    ) -> list[AdminWebWorkflowGrantRecord]:
        self._ensure_ready()
        with self._connect() as connection:
            if login is None:
                rows = connection.execute(
                    """
                    SELECT login, permission, directory_path, created_at
                    FROM admin_web_user_workflow_grants
                    ORDER BY login, permission, directory_path
                    """
                ).fetchall()
            else:
                normalized_login = normalize_admin_web_login(login)
                if normalized_login is None:
                    return []
                rows = connection.execute(
                    """
                    SELECT login, permission, directory_path, created_at
                    FROM admin_web_user_workflow_grants
                    WHERE login = ?
                    ORDER BY permission, directory_path
                    """,
                    (normalized_login,),
                ).fetchall()
        return [self._row_to_workflow_grant(row) for row in rows]

    def effective_permissions(
        self, login: object, directory_path: object | None = None
    ) -> frozenset[str]:
        normalized_login = normalize_admin_web_login(login)
        if normalized_login is None:
            return frozenset()
        self._ensure_ready()
        with self._connect() as connection:
            assignments = self._user_group_assignments_for_effective_permissions(
                connection, normalized_login
            )
            group_permissions = self._group_permissions_map(connection)
            direct_workflow_grants = self._user_workflow_grants_for_effective_permissions(
                connection, normalized_login
            )
        return rbac_effective_permissions(
            assignments=assignments,
            group_permissions=group_permissions,
            direct_workflow_grants=direct_workflow_grants,
            directory_path=directory_path,
        )

    def can(self, login: object, permission: object, directory_path: object | None = None) -> bool:
        normalized_login = normalize_admin_web_login(login)
        if normalized_login is None:
            return False
        self._ensure_ready()
        with self._connect() as connection:
            assignments = self._user_group_assignments_for_effective_permissions(
                connection, normalized_login
            )
            group_permissions = self._group_permissions_map(connection)
            direct_workflow_grants = self._user_workflow_grants_for_effective_permissions(
                connection, normalized_login
            )
        return rbac_can(
            assignments=assignments,
            group_permissions=group_permissions,
            direct_workflow_grants=direct_workflow_grants,
            permission=permission,
            directory_path=directory_path,
        )

    def set_group_permissions(
        self, group_id: object, permissions: Iterable[object]
    ) -> AdminWebGroupRecord:
        normalized_group_id = normalize_admin_web_group_id(group_id)
        if normalized_group_id is None:
            raise ValueError("group_id is required")
        normalized_permissions = self._normalize_group_permissions(permissions)
        self._ensure_ready()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            group = self._get_group(connection, normalized_group_id)
            if group is None:
                raise ValueError(f"Unknown admin web group: {normalized_group_id}")
            connection.execute(
                "DELETE FROM admin_web_group_permissions WHERE group_id = ?",
                (normalized_group_id,),
            )
            now = _datetime_to_storage(utc_now())
            for permission in normalized_permissions:
                connection.execute(
                    """
                    INSERT INTO admin_web_group_permissions(
                        group_id,
                        permission,
                        created_at
                    )
                    VALUES (?, ?, ?)
                    """,
                    (normalized_group_id, permission, now),
                )
            updated = self._store_group_record(
                connection,
                group_id=normalized_group_id,
                display_name=group.display_name,
                description=group.description,
                is_required=group.is_required,
                created_at=group.created_at,
                updated_at=utc_now(),
            )
            self.ensure_effective_owner_exists(connection)
            return updated

    def assign_user_group(
        self, *, login: object, group_id: object, directory_path: object | None = None
    ) -> AdminWebUserGroupAssignmentRecord:
        normalized_login = normalize_admin_web_login(login)
        if normalized_login is None:
            raise ValueError("login is required")
        normalized_group_id = normalize_admin_web_group_id(group_id)
        if normalized_group_id is None:
            raise ValueError("group_id is required")
        normalized_directory_path = normalize_admin_web_directory_path(directory_path)
        self._ensure_ready()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if self._get_user(connection, normalized_login) is None:
                raise ValueError(f"Unknown admin web user: {normalized_login}")
            if self._get_group(connection, normalized_group_id) is None:
                raise ValueError(f"Unknown admin web group: {normalized_group_id}")
            record = AdminWebUserGroupAssignmentRecord(
                login=normalized_login,
                group_id=normalized_group_id,
                directory_path=normalized_directory_path,
                created_at=utc_now(),
            )
            self._store_user_group_assignment(connection, record)
            return record

    def remove_user_group_assignment(
        self, *, login: object, group_id: object, directory_path: object | None = None
    ) -> None:
        normalized_login = normalize_admin_web_login(login)
        if normalized_login is None:
            raise ValueError("login is required")
        normalized_group_id = normalize_admin_web_group_id(group_id)
        if normalized_group_id is None:
            raise ValueError("group_id is required")
        normalized_directory_path = normalize_admin_web_directory_path(directory_path)
        self._ensure_ready()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if self._get_user(connection, normalized_login) is None:
                raise ValueError(f"Unknown admin web user: {normalized_login}")
            connection.execute(
                """
                DELETE FROM admin_web_user_group_assignments
                WHERE login = ? AND group_id = ? AND directory_path = ?
                """,
                (normalized_login, normalized_group_id, normalized_directory_path),
            )
            self.ensure_effective_owner_exists(connection)

    def set_user_group_assignments(
        self,
        *,
        login: object,
        assignments: Iterable[
            AdminWebUserGroupAssignmentRecord
            | Mapping[str, Any]
            | tuple[object, object]
            | tuple[object, object, object]
        ],
    ) -> list[AdminWebUserGroupAssignmentRecord]:
        normalized_login = normalize_admin_web_login(login)
        if normalized_login is None:
            raise ValueError("login is required")
        normalized_assignments = [
            self._coerce_user_group_assignment(normalized_login, assignment)
            for assignment in assignments
        ]
        self._ensure_ready()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if self._get_user(connection, normalized_login) is None:
                raise ValueError(f"Unknown admin web user: {normalized_login}")
            connection.execute(
                "DELETE FROM admin_web_user_group_assignments WHERE login = ?",
                (normalized_login,),
            )
            for record in normalized_assignments:
                if self._get_group(connection, record.group_id) is None:
                    raise ValueError(f"Unknown admin web group: {record.group_id}")
                self._store_user_group_assignment(connection, record)
            self.ensure_effective_owner_exists(connection)
        return normalized_assignments

    def grant_user_workflow_permission(
        self, *, login: object, permission: object, directory_path: object | None = None
    ) -> AdminWebWorkflowGrantRecord:
        normalized_login = normalize_admin_web_login(login)
        if normalized_login is None:
            raise ValueError("login is required")
        normalized_permission = normalize_admin_web_permission(permission)
        if normalized_permission is None:
            raise ValueError("permission is required")
        if normalized_permission not in DIRECT_WORKFLOW_PERMISSIONS:
            raise ValueError(f"unknown direct workflow permission: {normalized_permission}")
        normalized_directory_path = normalize_admin_web_directory_path(directory_path)
        self._ensure_ready()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if self._get_user(connection, normalized_login) is None:
                raise ValueError(f"Unknown admin web user: {normalized_login}")
            record = AdminWebWorkflowGrantRecord(
                login=normalized_login,
                permission=normalized_permission,  # type: ignore[arg-type]
                directory_path=normalized_directory_path,
                created_at=utc_now(),
            )
            self._store_workflow_grant(connection, record)
            return record

    def revoke_user_workflow_permission(
        self, *, login: object, permission: object, directory_path: object | None = None
    ) -> None:
        normalized_login = normalize_admin_web_login(login)
        if normalized_login is None:
            raise ValueError("login is required")
        normalized_permission = normalize_admin_web_permission(permission)
        if normalized_permission is None:
            raise ValueError("permission is required")
        normalized_directory_path = normalize_admin_web_directory_path(directory_path)
        self._ensure_ready()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if self._get_user(connection, normalized_login) is None:
                raise ValueError(f"Unknown admin web user: {normalized_login}")
            connection.execute(
                """
                DELETE FROM admin_web_user_workflow_grants
                WHERE login = ? AND permission = ? AND directory_path = ?
                """,
                (normalized_login, normalized_permission, normalized_directory_path),
            )

    def set_user_workflow_grants(
        self,
        *,
        login: object,
        grants: Iterable[
            AdminWebWorkflowGrantRecord
            | Mapping[str, Any]
            | tuple[object, object]
            | tuple[object, object, object]
        ],
    ) -> list[AdminWebWorkflowGrantRecord]:
        normalized_login = normalize_admin_web_login(login)
        if normalized_login is None:
            raise ValueError("login is required")
        normalized_grants = [
            self._coerce_workflow_grant(normalized_login, grant) for grant in grants
        ]
        self._ensure_ready()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if self._get_user(connection, normalized_login) is None:
                raise ValueError(f"Unknown admin web user: {normalized_login}")
            connection.execute(
                "DELETE FROM admin_web_user_workflow_grants WHERE login = ?",
                (normalized_login,),
            )
            for record in normalized_grants:
                self._store_workflow_grant(connection, record)
        return normalized_grants

    def upsert_group(
        self,
        *,
        group_id: object,
        display_name: object,
        description: object = _UNSET,
        is_required: bool | None = None,
    ) -> AdminWebGroupRecord:
        normalized_group_id = normalize_admin_web_group_id(group_id)
        if normalized_group_id is None:
            raise ValueError("group_id is required")
        normalized_display_name = _normalize_display_name(display_name)
        if normalized_display_name is None:
            raise ValueError("display_name is required")
        normalized_description = (
            None if description is _UNSET else _normalize_display_name(description)
        )
        self._ensure_ready()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._get_group(connection, normalized_group_id)
            now = utc_now()
            record = self._store_group_record(
                connection,
                group_id=normalized_group_id,
                display_name=normalized_display_name,
                description=(
                    existing.description
                    if description is _UNSET and existing is not None
                    else normalized_description
                ),
                is_required=existing.is_required
                if is_required is None and existing
                else bool(is_required),
                created_at=existing.created_at if existing is not None else now,
                updated_at=now,
            )
            return record

    def delete_group(self, group_id: object) -> AdminWebGroupRecord | None:
        normalized_group_id = normalize_admin_web_group_id(group_id)
        if normalized_group_id is None:
            raise ValueError("group_id is required")
        self._ensure_ready()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._get_group(connection, normalized_group_id)
            if existing is None:
                return None
            if existing.is_required:
                raise ValueError(f"Cannot delete required admin web group: {normalized_group_id}")
            connection.execute(
                "DELETE FROM admin_web_group_permissions WHERE group_id = ?",
                (normalized_group_id,),
            )
            connection.execute(
                "DELETE FROM admin_web_user_group_assignments WHERE group_id = ?",
                (normalized_group_id,),
            )
            connection.execute(
                "DELETE FROM admin_web_groups WHERE group_id = ?",
                (normalized_group_id,),
            )
            self.ensure_effective_owner_exists(connection)
            return existing

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

    def revoke_session(self, session_id: str, expires_at: float) -> None:
        """Revoke a signed login across Admin Web and streaming processes."""
        if not session_id:
            return
        self._ensure_ready()
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM admin_web_revoked_sessions WHERE expires_at <= ?",
                (utc_now().timestamp(),),
            )
            connection.execute(
                "INSERT OR REPLACE INTO admin_web_revoked_sessions(sid, expires_at) VALUES (?, ?)",
                (session_id, float(expires_at)),
            )

    def session_is_revoked(self, session_id: str) -> bool:
        self._ensure_ready()
        with self._connect() as connection:
            return connection.execute(
                "SELECT 1 FROM admin_web_revoked_sessions WHERE sid = ? AND expires_at > ?",
                (session_id, utc_now().timestamp()),
            ).fetchone() is not None

    def ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS admin_web_users (
                    login TEXT PRIMARY KEY,
                    display_name TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_login_at TEXT
                );

                CREATE TABLE IF NOT EXISTS admin_web_revoked_sessions (
                    sid TEXT PRIMARY KEY,
                    expires_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS admin_web_user_preferences (
                    login TEXT NOT NULL,
                    preference_key TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (login, preference_key)
                );

                CREATE TABLE IF NOT EXISTS admin_web_local_password_credentials (
                    login TEXT PRIMARY KEY,
                    algorithm TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    salt TEXT NOT NULL,
                    iterations INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS admin_web_groups (
                    group_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    description TEXT,
                    is_required INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS admin_web_group_permissions (
                    group_id TEXT NOT NULL,
                    permission TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (group_id, permission)
                );

                CREATE TABLE IF NOT EXISTS admin_web_user_group_assignments (
                    login TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    directory_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (login, group_id, directory_path)
                );

                CREATE TABLE IF NOT EXISTS admin_web_user_workflow_grants (
                    login TEXT NOT NULL,
                    permission TEXT NOT NULL,
                    directory_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (login, permission, directory_path)
                );

                CREATE INDEX IF NOT EXISTS idx_admin_web_user_preferences_login
                    ON admin_web_user_preferences(login, preference_key);

                CREATE INDEX IF NOT EXISTS idx_admin_web_local_password_credentials_login
                    ON admin_web_local_password_credentials(login);

                CREATE INDEX IF NOT EXISTS idx_admin_web_group_permissions_group
                    ON admin_web_group_permissions(group_id, permission);

                CREATE INDEX IF NOT EXISTS idx_admin_web_user_group_assignments_login
                    ON admin_web_user_group_assignments(login, directory_path);

                CREATE INDEX IF NOT EXISTS idx_admin_web_user_workflow_grants_login
                    ON admin_web_user_workflow_grants(login, directory_path);
                """
            )

    def _ensure_ready(self) -> None:
        with self._lock:
            if self._initialized:
                return
            self.ensure_schema()
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._seed_builtin_groups(connection)
            self._initialized = True

    def _seed_builtin_groups(self, connection: sqlite3.Connection) -> None:
        now = _datetime_to_storage(utc_now())
        for group_id, definition in BUILTIN_GROUP_DEFINITIONS.items():
            connection.execute(
                """
                INSERT OR IGNORE INTO admin_web_groups(
                    group_id,
                    display_name,
                    description,
                    is_required,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    group_id,
                    definition["display_name"],
                    definition["description"],
                    1 if definition["is_required"] else 0,
                    now,
                    now,
                ),
            )
            for permission in definition["permissions"]:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO admin_web_group_permissions(
                        group_id,
                        permission,
                        created_at
                    )
                    VALUES (?, ?, ?)
                    """,
                    (group_id, permission, now),
                )

    def ensure_effective_owner_exists(self, connection: sqlite3.Connection) -> None:
        if not self._has_effective_owner(connection):
            raise ValueError("Cannot leave the system without an effective owner")

    def _has_effective_owner(self, connection: sqlite3.Connection) -> bool:
        group_permissions = self._group_permissions_map(connection)
        rows = connection.execute("SELECT login FROM admin_web_users ORDER BY login").fetchall()
        for row in rows:
            assignments = self._user_group_assignments_for_effective_permissions(
                connection, str(row["login"])
            )
            direct_workflow_grants = self._user_workflow_grants_for_effective_permissions(
                connection, str(row["login"])
            )
            if rbac_can(
                assignments=assignments,
                group_permissions=group_permissions,
                direct_workflow_grants=direct_workflow_grants,
                permission=ADMIN_OWNER_PERMISSION,
                directory_path="root",
            ):
                return True
        return False

    def _group_permissions_map(self, connection: sqlite3.Connection) -> dict[str, tuple[str, ...]]:
        rows = connection.execute(
            """
            SELECT group_id, permission
            FROM admin_web_group_permissions
            ORDER BY group_id, permission
            """
        ).fetchall()
        permissions: dict[str, list[str]] = {}
        for row in rows:
            permissions.setdefault(str(row["group_id"]), []).append(str(row["permission"]))
        return {group_id: tuple(values) for group_id, values in permissions.items()}

    def _list_groups(self, connection: sqlite3.Connection) -> list[AdminWebGroupRecord]:
        rows = connection.execute(
            """
            SELECT group_id, display_name, description, is_required, created_at, updated_at
            FROM admin_web_groups
            ORDER BY group_id
            """
        ).fetchall()
        return [self._row_to_group(row, connection) for row in rows]

    def _get_group(
        self, connection: sqlite3.Connection, group_id: str
    ) -> AdminWebGroupRecord | None:
        row = connection.execute(
            """
            SELECT group_id, display_name, description, is_required, created_at, updated_at
            FROM admin_web_groups
            WHERE group_id = ?
            """,
            (group_id,),
        ).fetchone()
        return self._row_to_group(row, connection) if row is not None else None

    def _store_group_record(
        self,
        connection: sqlite3.Connection,
        *,
        group_id: str,
        display_name: str,
        description: str | None,
        is_required: bool,
        created_at: datetime,
        updated_at: datetime,
    ) -> AdminWebGroupRecord:
        connection.execute(
            """
            INSERT OR REPLACE INTO admin_web_groups(
                group_id,
                display_name,
                description,
                is_required,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                group_id,
                display_name,
                description,
                1 if is_required else 0,
                _datetime_to_storage(created_at),
                _datetime_to_storage(updated_at),
            ),
        )
        return AdminWebGroupRecord(
            group_id=group_id,
            display_name=display_name,
            description=description,
            is_required=is_required,
            created_at=created_at,
            updated_at=updated_at,
            permissions=self._group_permissions_map(connection).get(group_id, ()),
        )

    def _normalize_group_permissions(self, permissions: Iterable[object]) -> tuple[str, ...]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in permissions:
            permission = normalize_admin_web_permission(value)
            if permission is None:
                continue
            if permission not in PERMISSION_CATALOG:
                raise ValueError(f"unknown permission: {permission}")
            if permission in seen:
                continue
            seen.add(permission)
            normalized.append(permission)
        return tuple(sorted(normalized))

    def _row_to_group(
        self, row: sqlite3.Row | None, connection: sqlite3.Connection
    ) -> AdminWebGroupRecord | None:
        if row is None:
            return None
        permissions = self._group_permissions_map(connection).get(str(row["group_id"]), ())
        return AdminWebGroupRecord.model_validate(
            {
                "group_id": row["group_id"],
                "display_name": row["display_name"],
                "description": row["description"],
                "is_required": bool(row["is_required"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "permissions": permissions,
            }
        )

    def _row_to_group_permission(self, row: sqlite3.Row) -> AdminWebGroupPermissionRecord:
        return AdminWebGroupPermissionRecord.model_validate(
            {
                "group_id": row["group_id"],
                "permission": row["permission"],
                "created_at": row["created_at"],
            }
        )

    def _row_to_user_group_assignment(self, row: sqlite3.Row) -> AdminWebUserGroupAssignmentRecord:
        return AdminWebUserGroupAssignmentRecord.model_validate(
            {
                "login": row["login"],
                "group_id": row["group_id"],
                "directory_path": row["directory_path"],
                "created_at": row["created_at"],
            }
        )

    def _row_to_workflow_grant(self, row: sqlite3.Row) -> AdminWebWorkflowGrantRecord:
        return AdminWebWorkflowGrantRecord.model_validate(
            {
                "login": row["login"],
                "permission": row["permission"],
                "directory_path": row["directory_path"],
                "created_at": row["created_at"],
            }
        )

    def _store_user_group_assignment(
        self, connection: sqlite3.Connection, record: AdminWebUserGroupAssignmentRecord
    ) -> None:
        connection.execute(
            """
            INSERT OR REPLACE INTO admin_web_user_group_assignments(
                login,
                group_id,
                directory_path,
                created_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                record.login,
                record.group_id,
                record.directory_path,
                _datetime_to_storage(record.created_at),
            ),
        )

    def _store_workflow_grant(
        self, connection: sqlite3.Connection, record: AdminWebWorkflowGrantRecord
    ) -> None:
        connection.execute(
            """
            INSERT OR REPLACE INTO admin_web_user_workflow_grants(
                login,
                permission,
                directory_path,
                created_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                record.login,
                record.permission,
                record.directory_path,
                _datetime_to_storage(record.created_at),
            ),
        )

    def _user_group_assignments_for_effective_permissions(
        self, connection: sqlite3.Connection, login: str
    ) -> list[tuple[str, str]]:
        rows = connection.execute(
            """
            SELECT group_id, directory_path
            FROM admin_web_user_group_assignments
            WHERE login = ?
            ORDER BY group_id, directory_path
            """,
            (login,),
        ).fetchall()
        return [(str(row["group_id"]), str(row["directory_path"])) for row in rows]

    def _user_workflow_grants_for_effective_permissions(
        self, connection: sqlite3.Connection, login: str
    ) -> list[tuple[str, str]]:
        rows = connection.execute(
            """
            SELECT permission, directory_path
            FROM admin_web_user_workflow_grants
            WHERE login = ?
            ORDER BY permission, directory_path
            """,
            (login,),
        ).fetchall()
        return [(str(row["permission"]), str(row["directory_path"])) for row in rows]

    def _delete_user_related_rows(self, connection: sqlite3.Connection, login: str) -> None:
        connection.execute(
            "DELETE FROM admin_web_local_password_credentials WHERE login = ?",
            (login,),
        )
        connection.execute(
            "DELETE FROM admin_web_user_preferences WHERE login = ?",
            (login,),
        )
        connection.execute(
            "DELETE FROM admin_web_user_group_assignments WHERE login = ?",
            (login,),
        )
        connection.execute(
            "DELETE FROM admin_web_user_workflow_grants WHERE login = ?",
            (login,),
        )

    def _coerce_user_group_assignment(
        self,
        login: str,
        assignment: AdminWebUserGroupAssignmentRecord
        | Mapping[str, Any]
        | tuple[object, object]
        | tuple[object, object, object],
    ) -> AdminWebUserGroupAssignmentRecord:
        if isinstance(assignment, AdminWebUserGroupAssignmentRecord):
            if assignment.login != login:
                return assignment.model_copy(update={"login": login})
            return assignment
        if isinstance(assignment, Mapping):
            payload = dict(assignment)
            payload["login"] = login
            return AdminWebUserGroupAssignmentRecord.model_validate(payload)
        if len(assignment) == 2:
            group_id, directory_path = assignment
            return AdminWebUserGroupAssignmentRecord(
                login=login,
                group_id=group_id,
                directory_path=directory_path,
            )
        group_id, directory_path, created_at = assignment
        return AdminWebUserGroupAssignmentRecord(
            login=login,
            group_id=group_id,
            directory_path=directory_path,
            created_at=created_at if isinstance(created_at, datetime) else utc_now(),
        )

    def _coerce_workflow_grant(
        self,
        login: str,
        grant: AdminWebWorkflowGrantRecord
        | Mapping[str, Any]
        | tuple[object, object]
        | tuple[object, object, object],
    ) -> AdminWebWorkflowGrantRecord:
        if isinstance(grant, AdminWebWorkflowGrantRecord):
            if grant.login != login:
                return grant.model_copy(update={"login": login})
            return grant
        if isinstance(grant, Mapping):
            payload = dict(grant)
            payload["login"] = login
            return AdminWebWorkflowGrantRecord.model_validate(payload)
        if len(grant) == 2:
            permission, directory_path = grant
            return AdminWebWorkflowGrantRecord(
                login=login,
                permission=permission,
                directory_path=directory_path,
            )
        permission, directory_path, created_at = grant
        return AdminWebWorkflowGrantRecord(
            login=login,
            permission=permission,
            directory_path=directory_path,
            created_at=created_at if isinstance(created_at, datetime) else utc_now(),
        )

    def _user_count(self, connection: sqlite3.Connection) -> int:
        row = connection.execute("SELECT COUNT(*) AS count FROM admin_web_users").fetchone()
        return int(row["count"] if row is not None else 0)

    def _get_user(self, connection: sqlite3.Connection, login: str) -> AdminWebUserRecord | None:
        row = connection.execute(
            """
            SELECT login, display_name, created_at, updated_at, last_login_at
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
                display_name,
                created_at,
                updated_at,
                last_login_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                record.login,
                record.display_name,
                _datetime_to_storage(record.created_at),
                _datetime_to_storage(record.updated_at),
                _datetime_to_storage(record.last_login_at),
            ),
        )

    def _store_local_password(
        self, connection: sqlite3.Connection, *, login: str, password: str
    ) -> None:
        now = _datetime_to_storage(utc_now())
        existing = connection.execute(
            """
            SELECT created_at
            FROM admin_web_local_password_credentials
            WHERE login = ?
            """,
            (login,),
        ).fetchone()
        salt = secrets.token_bytes(LOCAL_PASSWORD_SALT_BYTES)
        password_hash = _hash_local_password(
            password=password,
            salt=salt,
            iterations=LOCAL_PASSWORD_ITERATIONS,
        )
        connection.execute(
            """
            INSERT OR REPLACE INTO admin_web_local_password_credentials(
                login,
                algorithm,
                password_hash,
                salt,
                iterations,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                login,
                LOCAL_PASSWORD_ALGORITHM,
                password_hash,
                salt.hex(),
                LOCAL_PASSWORD_ITERATIONS,
                str(existing["created_at"]) if existing is not None else now,
                now,
            ),
        )

    def _validate_local_password(self, password: object) -> None:
        if not isinstance(password, str) or password == "":
            raise ValueError("password is required")

    def _row_to_user(self, row: sqlite3.Row) -> AdminWebUserRecord:
        return AdminWebUserRecord.model_validate(
            {
                "login": row["login"],
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


def _hash_local_password(*, password: str, salt: bytes, iterations: int) -> str:
    return hashlib.pbkdf2_hmac(
        LOCAL_PASSWORD_HASH_NAME,
        password.encode("utf-8"),
        salt,
        iterations,
    ).hex()


__all__ = [
    "AdminWebGroupPermissionRecord",
    "AdminWebGroupRecord",
    "AdminWebLocalPasswordCredentialRecord",
    "AdminWebUserRecord",
    "AdminWebUserGroupAssignmentRecord",
    "AdminWebUserStore",
    "AdminWebWorkflowGrantRecord",
    "normalize_admin_web_login",
]
