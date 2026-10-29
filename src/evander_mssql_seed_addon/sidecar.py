from __future__ import annotations

import argparse
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import uvicorn
from pydantic import BaseModel, ConfigDict, field_validator
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from evander.worker.api import WorkerAuthMiddleware
from evander.worker.models import SAFE_ID_PATTERN
from evander.worker.security import safe_subprocess_error


class MssqlSeedRestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    logical_name: str
    database_name: str
    login_name: str
    backup_path: str

    @field_validator("session_id", "logical_name", mode="before")
    @classmethod
    def normalize_safe_id(cls, value: object, info) -> str:
        return _safe_id(value, info.field_name)

    @field_validator("database_name", mode="before")
    @classmethod
    def normalize_database_name(cls, value: object) -> str:
        text = _safe_id(value, "database_name")
        if not text.startswith("asb_"):
            raise ValueError("database_name must be an Evander worker MSSQL database")
        return text

    @field_validator("login_name", mode="before")
    @classmethod
    def normalize_login_name(cls, value: object) -> str:
        text = _safe_id(value, "login_name")
        if not text.startswith("asb_login_"):
            raise ValueError("login_name must be an Evander worker MSSQL login")
        return text

    @field_validator("backup_path", mode="before")
    @classmethod
    def normalize_backup_path(cls, value: object) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("backup_path is required")
        if "\x00" in text:
            raise ValueError("backup_path must not contain NUL bytes")
        if not Path(text).is_absolute():
            raise ValueError("backup_path must be absolute")
        return text


@dataclass(frozen=True)
class MssqlSeedRestoreService:
    admin_user: str
    admin_password: str
    admin_host: str = "127.0.0.1"
    port: int = 1433
    sqlcmd_path: str = "sqlcmd"
    data_dir: str = "/var/opt/mssql/data"

    def restore_database(self, request: MssqlSeedRestoreRequest) -> dict[str, Any]:
        if not self.admin_password:
            raise ValueError("MSSQL admin password is not configured")
        if self.port <= 0 or self.port > 65535:
            raise ValueError("MSSQL port must be between 1 and 65535")
        files = self._read_backup_filelist(request.backup_path)
        script = self._restore_script(request, files)
        self._run_sql(script)
        return {
            "status": "restored",
            "session_id": request.session_id,
            "logical_name": request.logical_name,
            "database_name": request.database_name,
            "login_name": request.login_name,
            "file_count": len(files),
        }

    def _read_backup_filelist(self, backup_path: str) -> list[tuple[str, str]]:
        output = self._run_sql_output(
            f"SET NOCOUNT ON; RESTORE FILELISTONLY FROM DISK = N{_sql_literal(backup_path)};"
        )
        files = _parse_filelist_output(output)
        if not files:
            raise ValueError("RESTORE FILELISTONLY did not return any database files")
        if not any(file_type == "D" for _, file_type in files):
            raise ValueError("backup does not contain a data file")
        if not any(file_type == "L" for _, file_type in files):
            raise ValueError("backup does not contain a log file")
        return files

    def _restore_script(
        self,
        request: MssqlSeedRestoreRequest,
        files: list[tuple[str, str]],
    ) -> str:
        database = _quote_identifier(request.database_name)
        login = _quote_identifier(request.login_name)
        data_dir = self.data_dir.rstrip("/")
        moves: list[str] = []
        data_index = 0
        log_index = 0
        other_index = 0
        for logical_name, file_type in files:
            if file_type == "L":
                log_index += 1
                suffix = "_log.ldf" if log_index == 1 else f"_log{log_index}.ldf"
            elif file_type == "D":
                data_index += 1
                suffix = ".mdf" if data_index == 1 else f"_data{data_index}.ndf"
            else:
                other_index += 1
                suffix = f"_file{other_index}.ndf"
            target_path = f"{data_dir}/{request.database_name}{suffix}"
            moves.append(f"    MOVE N{_sql_literal(logical_name)} TO N{_sql_literal(target_path)}")
        move_clause = ",\n".join(moves)
        return f"""
SET NOCOUNT ON;
IF SUSER_ID(N{_sql_literal(request.login_name)}) IS NULL
BEGIN
    THROW 51000, 'Evander MSSQL session login does not exist', 1;
END;
IF DB_ID(N{_sql_literal(request.database_name)}) IS NOT NULL
BEGIN
    ALTER DATABASE {database} SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
END;
RESTORE DATABASE {database}
FROM DISK = N{_sql_literal(request.backup_path)}
WITH REPLACE, RECOVERY,
{move_clause};
ALTER DATABASE {database} SET MULTI_USER;
DECLARE @evander_sql nvarchar(max);
SET @evander_sql = N'USE {database};
IF USER_ID(N''{_escape_sql_string(request.login_name)}'') IS NULL
BEGIN
    CREATE USER {login} FOR LOGIN {login};
END;
IF ISNULL(IS_ROLEMEMBER(N''db_owner'', N''{_escape_sql_string(request.login_name)}''), 0) <> 1
BEGIN
    ALTER ROLE [db_owner] ADD MEMBER {login};
END;';
EXEC(@evander_sql);
"""

    def _run_sql(self, script: str) -> None:
        env = os.environ.copy()
        env["SQLCMDPASSWORD"] = self.admin_password
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

    def _run_sql_output(self, script: str) -> str:
        env = os.environ.copy()
        env["SQLCMDPASSWORD"] = self.admin_password
        try:
            result = subprocess.run(
                [
                    self.sqlcmd_path,
                    "-S",
                    f"{self.admin_host},{self.port}",
                    "-U",
                    self.admin_user,
                    "-C",
                    "-b",
                    "-h",
                    "-1",
                    "-W",
                    "-s",
                    "\t",
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
        return result.stdout


def create_app(*, service: MssqlSeedRestoreService, token: str | None) -> Starlette:
    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def restore_database(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            restore_request = MssqlSeedRestoreRequest.model_validate(payload)
            result = service.restore_database(restore_request)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        return JSONResponse(result, status_code=201)

    app = Starlette(
        routes=[
            Route("/healthz", health, methods=["GET"]),
            Route("/v1/databases/restore", restore_database, methods=["POST"]),
        ]
    )
    app.add_middleware(WorkerAuthMiddleware, token=token)
    return app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the Evander MSSQL seed restore sidecar.")
    parser.add_argument(
        "--host", default=os.environ.get("EVANDER_MSSQL_SEED_SIDECAR_HOST", "127.0.0.1")
    )
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("EVANDER_MSSQL_SEED_SIDECAR_PORT", "11990"))
    )
    parser.add_argument(
        "--token-env",
        default=os.environ.get(
            "EVANDER_MSSQL_SEED_SIDECAR_TOKEN_ENV", "EVANDER_MSSQL_SEED_SIDECAR_TOKEN"
        ),
    )
    parser.add_argument(
        "--mssql-admin-user", default=os.environ.get("EVANDER_WORKER_MSSQL_ADMIN_USER", "sa")
    )
    parser.add_argument(
        "--mssql-admin-password-env",
        default=os.environ.get(
            "EVANDER_WORKER_MSSQL_ADMIN_PASSWORD_ENV", "EVANDER_WORKER_MSSQL_ADMIN_PASSWORD"
        ),
    )
    parser.add_argument(
        "--mssql-admin-host", default=os.environ.get("EVANDER_WORKER_MSSQL_ADMIN_HOST", "127.0.0.1")
    )
    parser.add_argument(
        "--mssql-port", type=int, default=int(os.environ.get("EVANDER_WORKER_MSSQL_PORT", "1433"))
    )
    parser.add_argument(
        "--mssql-sqlcmd-path", default=os.environ.get("EVANDER_WORKER_MSSQL_SQLCMD_PATH", "sqlcmd")
    )
    parser.add_argument(
        "--mssql-data-dir",
        default=os.environ.get("EVANDER_MSSQL_SEED_DATA_DIR", "/var/opt/mssql/data"),
    )
    args = parser.parse_args(argv)
    token = os.environ.get(args.token_env, "").strip() or None
    admin_password = os.environ.get(args.mssql_admin_password_env, "")
    service = MssqlSeedRestoreService(
        admin_user=args.mssql_admin_user,
        admin_password=admin_password,
        admin_host=args.mssql_admin_host,
        port=args.mssql_port,
        sqlcmd_path=args.mssql_sqlcmd_path,
        data_dir=args.mssql_data_dir,
    )
    uvicorn.run(create_app(service=service, token=token), host=args.host, port=args.port)


def _parse_filelist_output(output: str) -> list[tuple[str, str]]:
    files: list[tuple[str, str]] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("-") or "rows affected" in line:
            continue
        parts = [part.strip() for part in raw_line.split("\t")]
        if len(parts) < 3:
            continue
        logical_name = parts[0]
        file_type = parts[2].upper()
        if not logical_name or file_type not in {"D", "L", "S", "F"}:
            continue
        files.append((logical_name, file_type))
    return files


def _safe_id(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    if not SAFE_ID_PATTERN.fullmatch(text):
        raise ValueError(
            f"{field_name} must contain only letters, numbers, dots, underscores, or dashes"
        )
    if len(text) > 128:
        raise ValueError(f"{field_name} must be 128 characters or fewer")
    return text


def _quote_identifier(value: str) -> str:
    return "[" + value.replace("]", "]]") + "]"


def _sql_literal(value: str) -> str:
    return "'" + _escape_sql_string(value) + "'"


def _escape_sql_string(value: str) -> str:
    return value.replace("'", "''")


__all__ = ["MssqlSeedRestoreRequest", "MssqlSeedRestoreService", "create_app", "main"]
