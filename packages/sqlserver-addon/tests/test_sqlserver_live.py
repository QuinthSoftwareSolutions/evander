"""Opt-in native SQL tests; only an explicitly selected preseeded image is used."""

import os
from contextlib import closing
from pathlib import Path
import secrets
import subprocess
import sys
import time
import tracemalloc
import uuid

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evander_sqlserver_addon.adapter import Adapter
from evander.resources.models import Owner, Profile
from evander.resources.state import Store

IMAGE = os.environ.get("EVANDER_SQLSERVER_SMOKE_IMAGE")
pytestmark = pytest.mark.skipif(
    not IMAGE, reason="Select a preseeded SQL image for disposable native tests"
)


@pytest.fixture
def sql_instance(tmp_path):
    pytest.importorskip("pyodbc")
    name = "evander-sql-smoke-" + uuid.uuid4().hex[:16]
    password = secrets.token_urlsafe(32) + "A1!"

    def docker(*args, env=None):
        return (
            subprocess.run(["docker", *args], check=True, capture_output=True, timeout=45, env=env)
            .stdout.decode()
            .strip()
        )

    docker("network", "create", "--internal", "--label", "evander.smoke=" + name, name)
    try:
        docker(
            "run",
            "--detach",
            "--pull=never",
            "--name",
            name,
            "--label",
            "evander.smoke=" + name,
            "--network",
            name,
            "--memory",
            "3g",
            "--cpus",
            "2",
            "--env",
            "ACCEPT_EULA=Y",
            "--env",
            "MSSQL_PID=Developer",
            "--env",
            "MSSQL_MEMORY_LIMIT_MB=1024",
            "--env",
            "MSSQL_SA_PASSWORD",
            IMAGE,
            env={**os.environ, "MSSQL_SA_PASSWORD": password},
        )
        host = docker(
            "inspect", "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", name
        )
        raw, values = (
            {"host": host, "port": 1433, "encrypt": False},
            {"username": "sa", "password": password},
        )
        adapter = Adapter()
        deadline = time.monotonic() + 90
        while True:
            try:
                with adapter.connection({**raw, "timeout_seconds": 2}, values, "master") as conn:
                    conn.cursor().execute("SELECT 1").fetchone()
                break
            except Exception:
                if time.monotonic() >= deadline:
                    pytest.fail("Disposable SQL Server did not become ready within 90 seconds")
                time.sleep(1)

        class Credentials:
            def resolve(self, *args):
                return values

        yield raw, Credentials()
    finally:
        subprocess.run(["docker", "rm", "--force", name], capture_output=True, timeout=30)
        docker("network", "rm", name)


def test_native_sql_controller_reload_permissions_reset_and_persistent_lifetime(
    sql_instance, tmp_path
):
    import pyodbc
    from evander.addons import AddonContext
    from evander.resources.service import ResourceService
    from evander.resources.models import Settings, ResourceError

    raw, credentials = sql_instance

    class Authority:
        active = True

        def owns(self, owner):
            return self.active

        def notify(self, owner, value):
            pass

    authority = Authority()
    context = AddonContext("sqlserver-addon", tmp_path, tmp_path / "addons/sqlserver-addon")
    domain = ResourceService(context, Adapter(), authority=authority, credentials=credentials)
    configured = {**raw, "operations": ["manage_database", "manage_principal"]}
    domain.store.save_settings(
        "root",
        0,
        Settings(
            allocations_enabled=True,
            workflow_operations=[
                "manage_database",
                "manage_principal",
                "acquire",
                "query",
                "execute",
                "reset",
                "release",
            ],
            connections={"test": configured},
            profiles={
                "three": Profile(
                    connection_id="test", options={"databases": ["app", "audit", "search"]}
                )
            },
        ),
        "smoke",
    )
    owners = [
        Owner(
            provider="workflow",
            key=f"graph:run-{i}",
            directory="root",
            valid_until=time.time() + 7200,
        )
        for i in range(2)
    ]
    receipts = []
    persistent = domain.call(
        owners[0],
        "test",
        "manage_database",
        {"action": "create", "name": "persistent_" + uuid.uuid4().hex},
        "persistent-create",
    )
    assert persistent["state"] == "completed"
    resource_id = persistent["result"]["resource_id"]
    try:
        for i, owner in enumerate(owners):
            receipt = domain.acquire(owner, "three", "acquire")
            assert receipt["state"] == "ready"
            receipts.append(receipt)
            lease = domain.store.get(receipt["lease_id"])
            application = domain.adapter.private_connections(lease)["app"]
            with closing(
                pyodbc.connect(application["odbc_connection_string"], timeout=5)
            ) as scoped:
                assert (
                    scoped.cursor().execute("SELECT DB_NAME()").fetchone()[0]
                    == lease.resources["databases"]["app"]
                )
            if i == 0:
                database = persistent["result"]["database"]
                principal = {"database": database, "name": "application_reader"}
                for action, extra, expected_role in (
                    ("create", {"login": lease.resources["login"]}, 0),
                    ("grant", {"role": "db_datareader"}, 1),
                    ("revoke", {"role": "db_datareader"}, 0),
                    ("drop", {}, None),
                ):
                    managed = domain.call(
                        owner,
                        "test",
                        "manage_principal",
                        {**principal, "action": action, **extra},
                        "principal-" + action,
                    )
                    assert managed["state"] == "completed"
                    with domain.adapter.connection(raw, credentials.resolve(), database) as admin:
                        role = (
                            admin.cursor()
                            .execute(
                                "SELECT IS_ROLEMEMBER('db_datareader', ?)", [principal["name"]]
                            )
                            .fetchone()[0]
                        )
                        assert role == expected_role
            result = domain.lease_call(
                owner,
                receipt["lease_id"],
                "execute",
                {"database_alias": "app", "sql": "CREATE TABLE dbo.Probe(value int NOT NULL)"},
                "create-table",
            )
            assert result["state"] == "completed"
            assert (
                domain.lease_call(
                    owner,
                    receipt["lease_id"],
                    "execute",
                    {
                        "database_alias": "app",
                        "sql": "INSERT INTO dbo.Probe(value) VALUES (?)",
                        "parameters": [17],
                    },
                    "insert",
                )["state"]
                == "completed"
            )
        domain.store = Store(domain.store.root)
        first_id = receipts[0]["lease_id"]
        assert domain.acquire(owners[0], "three", "acquire")["lease_id"] == first_id
        assert domain.lease_call(
            owners[0],
            first_id,
            "query",
            {"database_alias": "app", "sql": "SELECT value FROM dbo.Probe"},
            "",
        )["rows"] == [[17]]
        # SQLGetData must not materialize a large MAX cell before applying the
        # configured result limit. Explicit server-side bounded casts still work.
        bounded = domain.store.get(first_id)
        bounded.connection["max_bytes"] = 1024
        for expression in (
            "REPLICATE(CAST('x' AS varchar(max)), 8388608)",
            "CONVERT(varbinary(max), REPLICATE(CAST('x' AS varchar(max)), 8388608))",
        ):
            tracemalloc.start()
            try:
                with pytest.raises(ResourceError, match="explicitly CAST"):
                    domain.adapter.lease_call(
                        bounded,
                        "query",
                        {
                            "database_alias": "app",
                            "sql": "SELECT " + expression + " AS payload",
                        },
                    )
                assert tracemalloc.get_traced_memory()[1] < 2_000_000
            finally:
                tracemalloc.stop()
        assert domain.adapter.lease_call(
            bounded,
            "query",
            {
                "database_alias": "app",
                "sql": "SELECT CAST(REPLICATE(CAST('x' AS varchar(max)), 8388608) AS varchar(32)) AS payload",
            },
        )["rows"] == [["x" * 32]]
        with pytest.raises(pyodbc.Error):
            domain.lease_call(
                owners[0],
                first_id,
                "query",
                {"database_alias": "app", "sql": "EXEC(N'DELETE FROM dbo.Probe')"},
                "",
            )
        domain.queue(owners[0], first_id, "reset", request_id="reset-one")
        assert domain.lease_call(
            owners[0],
            first_id,
            "query",
            {"database_alias": "app", "sql": "SELECT COUNT(*) FROM sys.tables"},
            "",
        )["rows"] == [[0]]
        domain.lease_call(
            owners[0],
            first_id,
            "execute",
            {"database_alias": "app", "sql": "CREATE TABLE dbo.AfterReset(value int)"},
            "post-reset-seed",
        )
        domain.queue(owners[0], first_id, "reset", request_id="reset-one")
        assert domain.lease_call(
            owners[0],
            first_id,
            "query",
            {"database_alias": "app", "sql": "SELECT COUNT(*) FROM sys.tables"},
            "",
        )["rows"] == [[1]]
        assert domain.lease_call(
            owners[1],
            receipts[1]["lease_id"],
            "query",
            {"database_alias": "app", "sql": "SELECT value FROM dbo.Probe"},
            "",
        )["rows"] == [[17]]
        # A read-labelled account must not silently be a production administrator.
        with pytest.raises(ResourceError, match="write or administration"):
            domain.adapter.call(
                "test", raw, "query", {"sql": "SELECT 1"}, "root", credentials, domain.store, None
            )
        authority.active = False
        for receipt in receipts:
            assert domain.reconcile(receipt["lease_id"])["state"] == "released"
            domain.adapter.verify_absent(
                domain.store.get(receipt["lease_id"]), {"test": configured}, credentials
            )
        with domain.adapter.connection(raw, credentials.resolve(), "master") as connection:
            assert (
                connection.cursor()
                .execute("SELECT DB_ID(?)", [persistent["result"]["database"]])
                .fetchone()[0]
                is not None
            )
    finally:
        authority.active = False
        for receipt in receipts:
            domain.reconcile(receipt["lease_id"])
        authority.active = True
        result = domain.call(
            owners[0],
            "test",
            "manage_database",
            {"action": "drop", "resource_id": resource_id},
            "persistent-drop",
        )
        assert result["state"] == "completed"
