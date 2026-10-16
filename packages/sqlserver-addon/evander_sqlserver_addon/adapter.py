"""SQL Server operations. Identifiers are quoted; data parameters are always bound."""

from __future__ import annotations

import base64
from datetime import date, datetime, time
from decimal import Decimal
from contextlib import contextmanager
import secrets
from threading import Timer
from contextlib import suppress
from typing import Literal

from pydantic import Field, field_validator

from evander.resources.models import Contract, Credential, ResourceError


class Connection(Contract):
    host: str = Field(min_length=1, max_length=253)
    application_host: str | None = None
    port: int = Field(default=1433, ge=1, le=65535)
    database: str = "master"
    driver: str = "ODBC Driver 18 for SQL Server"
    encrypt: bool = True
    trust_server_certificate: bool = False
    credentials: dict[str, Credential] = Field(default_factory=dict)
    operations: list[str] = Field(default_factory=list)
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    max_rows: int = Field(default=1000, ge=1, le=10000)
    max_bytes: int = Field(default=1048576, ge=1024, le=10485760)

    @field_validator("host", "database", "driver")
    @classmethod
    def safe(cls, value):
        if any(ord(c) < 32 for c in value):
            raise ValueError("Invalid connection component")
        return value


class Options(Contract):
    databases: list[str] = Field(default_factory=lambda: ["app"], min_length=1, max_length=32)
    collation: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_]+$")

    @field_validator("databases")
    @classmethod
    def aliases(cls, value):
        import re

        if len(set(value)) != len(value) or any(
            not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", a) for a in value
        ):
            raise ValueError("Database aliases must be unique lowercase identifiers")
        return value


class Call(Contract):
    database: str | None = None
    database_alias: str | None = None
    sql: str | None = Field(default=None, min_length=1, max_length=65536)
    parameters: list[str | int | float | bool | None] = Field(default_factory=list, max_length=1000)
    max_rows: int = Field(default=100, ge=1, le=10000)
    kind: Literal["databases", "schema", "health"] = "databases"
    action: Literal["create", "drop", "grant", "revoke"] | None = None
    name: str | None = None
    resource_id: str | None = None
    role: str | None = None
    login: str | None = None


def ident(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 128 or any(ord(c) < 32 for c in value):
        raise ResourceError("Invalid SQL identifier")
    return "[" + value.replace("]", "]]") + "]"


def literal(value):
    return "N'" + value.replace("'", "''") + "'"


def ddl(cursor, sql, parameters=()):
    cursor.execute(sql, parameters) if parameters else cursor.execute(sql)
    while cursor.nextset():
        pass


def odbc(value):
    return "{" + str(value).replace("}", "}}") + "}"


def sqlclient(value):
    return '"' + str(value).replace('"', '""') + '"'


class Adapter:
    argument_model = Call
    prefix = "sqlserver"
    read_operations = {"inspect", "query"}
    operations = {"inspect", "query", "execute", "manage_database", "manage_principal"}
    lease_operations = {"inspect", "query", "execute"}

    def __init__(self, connect=None):
        self._connect = connect

    def validate_connection(self, value):
        model = Connection.model_validate(value)
        if not set(model.operations) <= self.operations:
            raise ResourceError("Unsupported SQL operation grant")
        if "read" in model.credentials and any(
            model.credentials["read"] == model.credentials.get(p) for p in ("admin", "write")
        ):
            raise ResourceError(
                "Read credentials must be distinct from administration and write credentials"
            )
        return model

    def readiness(self, connection_id, raw, directory, credentials):
        purpose = "read" if "read" in raw.get("credentials", {}) else "admin"
        values = credentials.resolve(connection_id, raw, purpose, directory)
        with self.connection(
            {**raw, "timeout_seconds": min(raw.get("timeout_seconds", 30), 5)}, values, "master"
        ) as conn:
            if conn.cursor().execute("SELECT 1").fetchone()[0] != 1:
                raise ResourceError("SQL readiness check failed")

    def validate_profile(self, profile):
        if profile.connection_id is None:
            raise ResourceError("SQL profiles require an existing registered instance")
        return Options.model_validate(profile.options)

    def validate_control(self, profile, arguments):
        raise ResourceError("SQL profiles do not support broker controls")

    def validate_call(self, operation, raw):
        if operation not in self.operations:
            raise ResourceError("Unsupported SQL operation")
        args = Call.model_validate(raw)
        if args.database_alias is not None:
            raise ResourceError("database_alias requires a disposable lease_id")
        if operation in {"query", "execute"} and not args.sql:
            raise ResourceError("SQL text is required")
        if operation == "inspect" and args.kind not in {"databases", "schema", "health"}:
            raise ResourceError("Unsupported SQL inspection")
        if operation.startswith("manage_") and args.action not in {
            "create",
            "drop",
            "grant",
            "revoke",
        }:
            raise ResourceError("Unsupported SQL management action")
        return args

    def validate_request(self, connection, operation, arguments):
        args = self.validate_call(operation, arguments)
        if operation == "manage_database":
            if args.action not in {"create", "drop"}:
                raise ResourceError("Database management requires create/drop")
            if args.action == "create" and args.resource_id is not None:
                raise ResourceError("resource_id is only accepted when dropping a managed database")
            if (
                args.action == "create"
                and not args.name
                or args.action == "drop"
                and not args.resource_id
            ):
                raise ResourceError(
                    "Database creation needs a name; deletion needs a managed resource_id"
                )
        if operation == "manage_principal" and (not args.database or not args.name):
            raise ResourceError("Database principal management requires a database and name")
        return args

    @contextmanager
    def connection(self, raw, values, database=None, *, autocommit=False):
        config = Connection.model_validate(raw)
        if self._connect is None:
            import pyodbc

            connect = pyodbc.connect
        else:
            connect = self._connect
        parts = {
            "DRIVER": config.driver,
            "SERVER": f"{config.host},{config.port}",
            "DATABASE": database or config.database,
            "UID": values["username"],
            "PWD": values["password"],
            "Encrypt": "yes" if config.encrypt else "no",
            "TrustServerCertificate": "yes" if config.trust_server_certificate else "no",
        }
        conn = connect(
            ";".join(k + "=" + odbc(v) for k, v in parts.items()),
            timeout=config.timeout_seconds,
            autocommit=autocommit,
        )
        try:
            # A pooled ODBC handle may retain OFF while pyodbc assumes the default ON.
            # Set the attribute explicitly even when the constructor received True.
            conn.autocommit = autocommit
            conn.timeout = config.timeout_seconds
            yield conn
        finally:
            conn.close()

    def results(self, cursor, config, maximum):
        if not cursor.description:
            return {"columns": [], "rows": [], "affected_rows": cursor.rowcount, "truncated": False}
        import json

        columns = [c[0] for c in cursor.description]
        result = []
        size = len(json.dumps({"columns": columns, "rows": [], "truncated": False}).encode())
        if size > config.max_bytes:
            raise ResourceError("SQL result metadata exceeds the configured byte limit")
        # ODBC's declared column sizes bound a complete row before SQLGetData
        # materializes any cell. MAX/XML/unknown columns must be CAST by the
        # caller on the server; a session SET TEXTSIZE is not an enforceable cap.
        row_bound = 2
        fixed = {bool: 5, int: 22, float: 32, Decimal: 64, date: 32, datetime: 64, time: 48}
        for column in cursor.description:
            kind = column[1] if len(column) > 1 else None
            declared = column[3] if len(column) > 3 else None
            if kind in fixed:
                row_bound += fixed[kind] + 1
            elif kind in {str, bytes} and type(declared) is int and declared > 0:
                row_bound += (12 * declared if kind is str else 4 * ((declared + 2) // 3)) + 3
            else:
                raise ResourceError(
                    "SQL result column has no safe size bound; explicitly CAST it to a bounded type"
                )
            if row_bound > config.max_bytes - size:
                raise ResourceError(
                    "SQL result row may exceed max_bytes; explicitly CAST large columns to bounded types"
                )
        limit = min(maximum, config.max_rows)
        truncated = False
        for _ in range(limit + 1):
            row = cursor.fetchone()
            if row is None:
                break
            values = [
                base64.b64encode(v).decode()
                if isinstance(v, bytes)
                else v
                if v is None or isinstance(v, (str, int, float, bool))
                else str(v)
                for v in row
            ]
            size += len(json.dumps(values).encode()) + 1
            if len(result) == limit or size > config.max_bytes:
                truncated = True
                break
            result.append(values)
        return {"columns": columns, "rows": result, "truncated": truncated}

    def call(
        self, connection_id, raw, operation, arguments, directory, credentials, store, operation_id
    ):
        args = self.validate_request(raw, operation, arguments)
        config = self.validate_connection(raw)
        purpose = (
            "read"
            if operation in self.read_operations
            else "write"
            if operation == "execute"
            else "admin"
        )
        values = credentials.resolve(connection_id, raw, purpose, directory)
        with self.connection(
            raw, values, args.database, autocommit=operation.startswith("manage_")
        ) as conn:
            cursor = conn.cursor()
            if operation == "inspect":
                sql = {
                    "health": "SELECT CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(128)) AS version",
                    "databases": "SELECT name, state_desc FROM sys.databases ORDER BY name",
                    "schema": "SELECT TABLE_SCHEMA,TABLE_NAME,COLUMN_NAME,DATA_TYPE,IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS ORDER BY TABLE_SCHEMA,TABLE_NAME,ORDINAL_POSITION",
                }[args.kind]
                cursor.execute(sql)
            elif operation in {"query", "execute"}:
                if operation == "query":
                    cursor.execute(
                        "SELECT CASE WHEN IS_SRVROLEMEMBER('sysadmin')=1 OR IS_MEMBER('db_owner')=1 OR IS_MEMBER('db_datawriter')=1 OR IS_MEMBER('db_ddladmin')=1 OR HAS_PERMS_BY_NAME(DB_NAME(),'DATABASE','CONTROL')=1 OR HAS_PERMS_BY_NAME(DB_NAME(),'DATABASE','INSERT')=1 OR HAS_PERMS_BY_NAME(DB_NAME(),'DATABASE','UPDATE')=1 OR HAS_PERMS_BY_NAME(DB_NAME(),'DATABASE','DELETE')=1 THEN 1 ELSE 0 END"
                    )
                    unsafe = cursor.fetchone()[0]
                    cursor.fetchall()
                    if unsafe:
                        raise ResourceError(
                            "Query credentials have database write or administration permissions"
                        )

                def cancel():
                    with suppress(Exception):
                        cursor.cancel()

                timer = Timer(config.timeout_seconds, cancel)
                timer.daemon = True
                timer.start()
                try:
                    cursor.execute(args.sql, args.parameters)
                    result = self.results(cursor, config, args.max_rows)
                    while cursor.nextset():
                        pass
                    if operation == "execute":
                        conn.commit()
                    else:
                        conn.rollback()
                    return result
                finally:
                    timer.cancel()
            else:
                return self.manage(
                    cursor, connection_id, directory, operation, args, store, operation_id
                )
            result = self.results(cursor, config, args.max_rows)
            if operation == "execute":
                conn.commit()
            else:
                conn.rollback()
            return result

    def managed(self, store, key):
        value = store.operation(key)
        return value[1] if value else None

    def manage(self, cursor, connection_id, directory, operation, args, store, operation_id):
        if operation == "manage_database":
            identity = operation_id if args.action == "create" else args.resource_id
            key = "sql-managed:" + str(identity)
            record = self.managed(store, key)
            if args.action == "create":
                if not operation_id or args.resource_id is not None or record is not None:
                    raise ResourceError(
                        "Database creation requires a new durable operation identity"
                    )
                if not args.name:
                    raise ResourceError("Database name is required")
                database = ident(args.name)
                cursor.execute("SELECT DB_ID(?)", [args.name])
                if cursor.fetchone()[0] is not None:
                    raise ResourceError(
                        "Database already exists; existing databases are never adopted"
                    )
                record = {
                    "id": operation_id,
                    "directory": directory,
                    "connection_id": connection_id,
                    "database": args.name,
                    "state": "creating",
                    "marker": secrets.token_hex(32),
                }
                store.put_operation(key, operation_id, record)
                ddl(cursor, f"CREATE DATABASE {database}")
                ddl(
                    cursor,
                    f"EXEC {database}.sys.sp_addextendedproperty @name=N'evander_resource_owner', @value=?",
                    [record["marker"]],
                )
                record["state"] = "ready"
                store.put_operation(key, operation_id, record)
                return {"resource_id": record["id"], "database": args.name}
            if args.action != "drop" or record is None:
                raise ResourceError("Dropping a database requires its managed resource_id")
            self.check_record(record, connection_id, directory)
            self.drop_database(cursor, record["database"], record["marker"])
            record["state"] = "released"
            store.put_operation(key, operation_id, record)
            return {"resource_id": record["id"], "state": "released"}
        if not args.database or not args.name:
            raise ResourceError("A database and principal are required")
        # Server login lifecycle remains with the externally operated instance.
        principal = ident(args.name)
        if args.action == "create":
            mapping = "FOR LOGIN " + ident(args.login) if args.login else "WITHOUT LOGIN"
            ddl(cursor, f"CREATE USER {principal} {mapping}")
        elif args.action == "drop":
            ddl(cursor, f"DROP USER {principal}")
        elif args.action in {"grant", "revoke"} and args.role in {
            "db_datareader",
            "db_datawriter",
            "db_ddladmin",
        }:
            verb = "ADD" if args.action == "grant" else "DROP"
            ddl(cursor, f"ALTER ROLE {ident(args.role)} {verb} MEMBER {principal}")
        else:
            raise ResourceError("Unsupported database principal operation")
        return {"principal": args.name, "action": args.action}

    @staticmethod
    def check_record(record, connection_id, directory):
        if record["connection_id"] != connection_id or record["directory"] != directory:
            raise ResourceError("Managed resource belongs to a different connection or directory")

    def marker(self, cursor, name):
        cursor.execute("SELECT DB_ID(?)", [name])
        exists = cursor.fetchone()[0] is not None
        cursor.fetchall()  # Finish the ODBC result before nontransactional database DDL.
        if not exists:
            return None, False
        cursor.execute(
            f"SELECT CAST(value AS nvarchar(128)) FROM {ident(name)}.sys.extended_properties WHERE class=0 AND name=N'evander_resource_owner'"
        )
        row = cursor.fetchone()
        cursor.fetchall()
        return row[0] if row else None, True

    def drop_database(self, cursor, name, marker):
        actual, exists = self.marker(cursor, name)
        if not exists:
            return
        if actual != marker:
            raise ResourceError(
                "Database ownership marker does not match; preserved for operator reconciliation"
            )
        quoted = ident(name)
        ddl(cursor, f"ALTER DATABASE {quoted} SET SINGLE_USER WITH ROLLBACK IMMEDIATE")
        ddl(cursor, f"DROP DATABASE {quoted}")

    def provision(self, lease, save, credentials):
        options = self.validate_profile(lease.profile)
        raw = lease.connection
        values = credentials.resolve(
            lease.profile.connection_id, raw, "admin", lease.owner.directory
        )
        if not lease.resources:
            lease.resources = {
                "login": "evr_" + lease.lease_id[3:],
                "password": secrets.token_urlsafe(32),
                "sid": secrets.token_hex(16),
                "read_login": "evr_" + lease.lease_id[3:] + "_read",
                "read_password": secrets.token_urlsafe(32),
                "read_sid": secrets.token_hex(16),
                "marker": secrets.token_hex(32),
                "databases": {a: "evr_" + lease.lease_id[3:] + "_" + a for a in options.databases},
            }
            save()
        resources = lease.resources
        with self.connection(raw, values, "master", autocommit=True) as conn:
            cursor = conn.cursor()
            for login, password, sid in (
                (resources["login"], resources["password"], resources["sid"]),
                (resources["read_login"], resources["read_password"], resources["read_sid"]),
            ):
                cursor.execute(
                    "SELECT CONVERT(varchar(32),sid,2) FROM sys.sql_logins WHERE name=?", [login]
                )
                row = cursor.fetchone()
                if row is not None and row[0].lower() != sid:
                    raise ResourceError("SQL login identity does not match the resource intent")
                if row is None:
                    ddl(
                        cursor,
                        f"CREATE LOGIN {ident(login)} WITH PASSWORD={literal(password)}, SID=0x{sid}, CHECK_POLICY=ON",
                    )
            for name in resources["databases"].values():
                actual, exists = self.marker(cursor, name)
                if exists and actual != resources["marker"]:
                    raise ResourceError("Existing database lacks the expected ownership marker")
                if not exists:
                    collation = " COLLATE " + options.collation if options.collation else ""
                    ddl(cursor, f"CREATE DATABASE {ident(name)}{collation}")
                    ddl(
                        cursor,
                        f"EXEC {ident(name)}.sys.sp_addextendedproperty @name=N'evander_resource_owner', @value=?",
                        [resources["marker"]],
                    )
                ddl(
                    cursor,
                    f"USE {ident(name)}; IF USER_ID({literal(resources['login'])}) IS NULL CREATE USER {ident(resources['login'])} FOR LOGIN {ident(resources['login'])}; ALTER ROLE [db_owner] ADD MEMBER {ident(resources['login'])}; USE [master]",
                )
                ddl(
                    cursor,
                    f"USE {ident(name)}; IF USER_ID({literal(resources['read_login'])}) IS NULL CREATE USER {ident(resources['read_login'])} FOR LOGIN {ident(resources['read_login'])}; ALTER ROLE [db_datareader] ADD MEMBER {ident(resources['read_login'])}; USE [master]",
                )
        lease.state = "ready"
        save()

    def private_connections(self, lease):
        raw, resources = lease.connection, lease.resources
        host = raw.get("application_host") or raw["host"]
        return {
            alias: {
                "host": host,
                "port": raw.get("port", 1433),
                "database": name,
                "username": resources["login"],
                "password": resources["password"],
                "connection_string": ";".join(
                    f"{k}={sqlclient(v)}"
                    for k, v in {
                        "Server": f"{host},{raw.get('port', 1433)}",
                        "Database": name,
                        "User ID": resources["login"],
                        "Password": resources["password"],
                        "Encrypt": "true" if raw.get("encrypt", True) else "false",
                        "TrustServerCertificate": "true"
                        if raw.get("trust_server_certificate", False)
                        else "false",
                    }.items()
                ),
                "odbc_connection_string": ";".join(
                    f"{k}={odbc(v)}"
                    for k, v in {
                        "DRIVER": raw.get("driver", "ODBC Driver 18 for SQL Server"),
                        "SERVER": f"{host},{raw.get('port', 1433)}",
                        "DATABASE": name,
                        "UID": resources["login"],
                        "PWD": resources["password"],
                        "Encrypt": "yes" if raw.get("encrypt", True) else "no",
                        "TrustServerCertificate": "yes"
                        if raw.get("trust_server_certificate", False)
                        else "no",
                    }.items()
                ),
            }
            for alias, name in resources["databases"].items()
        }

    def release(self, lease, save, credentials):
        if not lease.resources:
            return
        raw, resources = lease.connection, lease.resources
        values = credentials.resolve(
            lease.profile.connection_id, raw, "admin", lease.owner.directory
        )
        with self.connection(raw, values, "master", autocommit=True) as conn:
            cursor = conn.cursor()
            for name in resources["databases"].values():
                self.drop_database(cursor, name, resources["marker"])
            for login, sid in (
                (resources["login"], resources["sid"]),
                (resources["read_login"], resources["read_sid"]),
            ):
                cursor.execute(
                    "SELECT CONVERT(varchar(32),sid,2) FROM sys.sql_logins WHERE name=?", [login]
                )
                row = cursor.fetchone()
                if row and row[0].lower() != sid:
                    raise ResourceError("SQL login identity changed; refusing cleanup")
                if row:
                    ddl(cursor, f"DROP LOGIN {ident(login)}")
        self.verify_absent(lease, {lease.profile.connection_id: raw}, credentials)
        save()

    def reset(self, lease, save, credentials):
        self.release(lease, save, credentials)

    def verify_absent(self, lease, connections, credentials):
        raw = connections[lease.profile.connection_id]
        values = credentials.resolve(
            lease.profile.connection_id, raw, "admin", lease.owner.directory
        )
        prefix = "evr_" + lease.lease_id[3:]
        with self.connection(raw, values, "master") as conn:
            cursor = conn.cursor()
            for alias in self.validate_profile(lease.profile).databases:
                cursor.execute("SELECT DB_ID(?)", [prefix + "_" + alias])
                if cursor.fetchone()[0] is not None:
                    raise ResourceError(
                        "Unjournaled database exists; explicit reconciliation is required"
                    )
            cursor.execute(
                "SELECT name FROM sys.sql_logins WHERE name IN (?,?)", [prefix, prefix + "_read"]
            )
            if cursor.fetchone() is not None:
                raise ResourceError("Unjournaled login exists; explicit reconciliation is required")

    def lease_call(self, lease, operation, raw_arguments):
        if operation not in self.lease_operations:
            raise ResourceError("Unsupported disposable SQL operation")
        arguments = dict(raw_arguments)
        alias = arguments.pop("database_alias", None)
        databases = lease.resources["databases"]
        if operation == "inspect" and arguments.get("kind", "databases") == "databases":
            return {"databases": databases}
        if alias is None and len(databases) == 1:
            alias = next(iter(databases))
        if alias not in databases or arguments.get("database") not in {None, databases.get(alias)}:
            raise ResourceError("Select a database_alias owned by this lease")
        arguments["database"] = databases[alias]
        resources = lease.resources

        class LeaseCredentials:
            def resolve(self, connection_id, connection, purpose, directory):
                if purpose not in {"read", "write"}:
                    raise ResourceError("Lease data calls cannot use administrative credentials")
                return {
                    "username": resources["read_login"]
                    if purpose == "read"
                    else resources["login"],
                    "password": resources["read_password"]
                    if purpose == "read"
                    else resources["password"],
                }

        return self.call(
            lease.profile.connection_id,
            lease.connection,
            operation,
            arguments,
            lease.owner.directory,
            LeaseCredentials(),
            None,
            None,
        )
