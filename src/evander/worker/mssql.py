from __future__ import annotations

import hashlib
import os
import re
import secrets
import subprocess
from pathlib import Path

from evander.storage import JsonStore

from .models import (
    WorkerMssqlDatabaseDeleteRecord,
    WorkerMssqlDatabaseListRecord,
    WorkerMssqlDatabaseRecord,
    WorkerMssqlDatabaseRequest,
    WorkerMssqlDatabaseState,
    WorkerMssqlSessionDeleteRecord,
    WorkerMssqlSessionState,
    validate_worker_component_id,
)
from .security import safe_subprocess_error


class WorkerMssqlStore:
    def __init__(self, path: Path) -> None:
        self._store = JsonStore(path, WorkerMssqlSessionState, file_mode=0o600)

    def get(self, session_id: str) -> WorkerMssqlSessionState | None:
        return self._store.get(session_id)

    def put(self, state: WorkerMssqlSessionState) -> None:
        self._store.put(state.session_id, state)

    def delete(self, session_id: str) -> None:
        self._store.delete(session_id)


class WorkerMssqlProvisioner:
    def __init__(
        self,
        *,
        enabled: bool = False,
        store: WorkerMssqlStore,
        admin_user: str = "sa",
        admin_password: str | None = None,
        admin_host: str = "127.0.0.1",
        port: int = 1433,
        container_host: str = "172.17.0.1",
        sqlcmd_path: str = "sqlcmd",
    ) -> None:
        self.enabled = enabled
        self.store = store
        self.admin_user = str(admin_user or "sa").strip() or "sa"
        self.admin_password = str(admin_password or "").strip() or None
        self.admin_host = str(admin_host or "127.0.0.1").strip() or "127.0.0.1"
        self.port = int(port)
        self.container_host = str(container_host or self.admin_host).strip() or self.admin_host
        self.sqlcmd_path = str(sqlcmd_path or "sqlcmd").strip() or "sqlcmd"

    def provision_database(
        self, session_id: str, request: WorkerMssqlDatabaseRequest
    ) -> WorkerMssqlDatabaseRecord:
        normalized_session_id = _validate_session_id(session_id)
        self._ensure_enabled()
        state = self.store.get(normalized_session_id) or self._new_session_state(
            normalized_session_id
        )
        database = state.databases.get(request.logical_name)
        if database is None:
            database = WorkerMssqlDatabaseState(
                logical_name=request.logical_name,
                database_name=_database_name(normalized_session_id, request.logical_name),
            )
        self._apply_database(state, database)
        state.databases[request.logical_name] = database
        self.store.put(state)
        return self._database_record(state, database)

    def list_databases(self, session_id: str) -> WorkerMssqlDatabaseListRecord:
        normalized_session_id = _validate_session_id(session_id)
        self._ensure_enabled()
        state = self.store.get(normalized_session_id)
        databases = (
            []
            if state is None
            else sorted(state.databases.values(), key=lambda item: item.logical_name)
        )
        return WorkerMssqlDatabaseListRecord(
            session_id=normalized_session_id,
            databases=databases,
        )

    def drop_database(self, session_id: str, logical_name: str) -> WorkerMssqlDatabaseDeleteRecord:
        normalized_session_id = _validate_session_id(session_id)
        request = WorkerMssqlDatabaseRequest(logical_name=logical_name)
        self._ensure_enabled()
        state = self.store.get(normalized_session_id)
        if state is None or request.logical_name not in state.databases:
            return WorkerMssqlDatabaseDeleteRecord(
                session_id=normalized_session_id,
                logical_name=request.logical_name,
                deleted=False,
            )
        database = state.databases.pop(request.logical_name)
        self._drop_database(database.database_name)
        if state.databases:
            self.store.put(state)
        else:
            self._drop_login(state.login_name)
            self.store.delete(normalized_session_id)
        return WorkerMssqlDatabaseDeleteRecord(
            session_id=normalized_session_id,
            logical_name=request.logical_name,
            database_name=database.database_name,
            deleted=True,
        )

    def drop_session(self, session_id: str) -> WorkerMssqlSessionDeleteRecord:
        normalized_session_id = _validate_session_id(session_id)
        state = self.store.get(normalized_session_id)
        if state is None:
            return WorkerMssqlSessionDeleteRecord(session_id=normalized_session_id)
        self._ensure_enabled()
        deleted_databases: list[str] = []
        for database in sorted(state.databases.values(), key=lambda item: item.database_name):
            self._drop_database(database.database_name)
            deleted_databases.append(database.database_name)
        self._drop_login(state.login_name)
        self.store.delete(normalized_session_id)
        return WorkerMssqlSessionDeleteRecord(
            session_id=normalized_session_id,
            deleted_databases=deleted_databases,
            deleted_login=True,
        )

    def _new_session_state(self, session_id: str) -> WorkerMssqlSessionState:
        return WorkerMssqlSessionState(
            session_id=session_id,
            login_name=f"asb_login_{_hash_part(session_id, length=20)}",
            password=secrets.token_urlsafe(32),
        )

    def _database_record(
        self,
        state: WorkerMssqlSessionState,
        database: WorkerMssqlDatabaseState,
    ) -> WorkerMssqlDatabaseRecord:
        connection_string = (
            f"Server=tcp:{self.container_host},{self.port};"
            f"Database={database.database_name};"
            f"User ID={state.login_name};"
            f"Password={state.password};"
            "Encrypt=True;TrustServerCertificate=True"
        )
        return WorkerMssqlDatabaseRecord(
            session_id=state.session_id,
            logical_name=database.logical_name,
            database_name=database.database_name,
            server=self.container_host,
            port=self.port,
            username=state.login_name,
            password=state.password,
            connection_string=connection_string,
        )

    def _ensure_enabled(self) -> None:
        if not self.enabled:
            raise ValueError("worker MSSQL support is disabled")
        if not self.admin_password:
            raise ValueError("worker MSSQL admin password is not configured")
        if self.port <= 0 or self.port > 65535:
            raise ValueError("worker MSSQL port must be between 1 and 65535")

    def _apply_database(
        self,
        state: WorkerMssqlSessionState,
        database: WorkerMssqlDatabaseState,
    ) -> None:
        login = _quote_identifier(state.login_name)
        database_name = _quote_identifier(database.database_name)
        login_literal = _sql_literal(state.login_name)
        database_literal = _sql_literal(database.database_name)
        password_literal = _sql_literal(state.password)
        script = f"""
SET NOCOUNT ON;
DENY VIEW ANY DATABASE TO public;
IF SUSER_ID(N{login_literal}) IS NULL
BEGIN
    CREATE LOGIN {login} WITH PASSWORD = N{password_literal}, CHECK_POLICY = OFF, CHECK_EXPIRATION = OFF;
END
ELSE
BEGIN
    ALTER LOGIN {login} WITH PASSWORD = N{password_literal}, CHECK_POLICY = OFF, CHECK_EXPIRATION = OFF;
END
IF DB_ID(N{database_literal}) IS NULL
BEGIN
    CREATE DATABASE {database_name};
END
DECLARE @evander_sql nvarchar(max);
SET @evander_sql = N'USE {database_name};
IF USER_ID(N''{_escape_sql_string(state.login_name)}'') IS NULL
BEGIN
    CREATE USER {login} FOR LOGIN {login};
END;
IF ISNULL(IS_ROLEMEMBER(N''db_owner'', N''{_escape_sql_string(state.login_name)}''), 0) <> 1
BEGIN
    ALTER ROLE [db_owner] ADD MEMBER {login};
END;';
EXEC(@evander_sql);
"""
        self._run_sql(script)

    def _drop_database(self, database_name: str) -> None:
        database = _quote_identifier(database_name)
        database_literal = _sql_literal(database_name)
        script = f"""
SET NOCOUNT ON;
IF DB_ID(N{database_literal}) IS NOT NULL
BEGIN
    ALTER DATABASE {database} SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
    DROP DATABASE {database};
END;
"""
        self._run_sql(script)

    def _drop_login(self, login_name: str) -> None:
        login = _quote_identifier(login_name)
        login_literal = _sql_literal(login_name)
        script = f"""
SET NOCOUNT ON;
IF SUSER_ID(N{login_literal}) IS NOT NULL
BEGIN
    DROP LOGIN {login};
END;
"""
        self._run_sql(script)

    def _run_sql(self, script: str) -> None:
        env = os.environ.copy()
        env["SQLCMDPASSWORD"] = self.admin_password or ""
        try:
            subprocess.run(
                [
                    self.sqlcmd_path,
                    "-S",
                    f"{self.admin_host},{self.port}",
                    "-U",
                    self.admin_user,
                    "-C",
                    "-b",
                    "-Q",
                    script,
                ],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(safe_subprocess_error(exc)) from exc


def _validate_session_id(value: str) -> str:
    return str(validate_worker_component_id(value, field_name="session_id"))


def _database_name(session_id: str, logical_name: str) -> str:
    session_hash = _hash_part(session_id, length=16)
    logical_hash = _hash_part(f"{session_id}:{logical_name}", length=10)
    suffix = re.sub(r"[^A-Za-z0-9_]+", "_", logical_name).strip("_") or "db"
    suffix = suffix[:48]
    return f"asb_{session_hash}_{suffix}_{logical_hash}"


def _hash_part(value: str, *, length: int) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def _quote_identifier(value: str) -> str:
    return "[" + value.replace("]", "]]") + "]"


def _sql_literal(value: str) -> str:
    return "'" + _escape_sql_string(value) + "'"


def _escape_sql_string(value: str) -> str:
    return value.replace("'", "''")
