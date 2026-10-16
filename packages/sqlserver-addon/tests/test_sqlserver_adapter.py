from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evander_sqlserver_addon.adapter import Adapter, Options, ident
from evander.resources.models import Lease, Owner, Profile, ResourceError


def test_identifiers_are_quoted_and_aliases_are_configurable():
    assert ident("name];DROP TABLE app;--") == "[name]];DROP TABLE app;--]"
    assert Options(databases=["app", "audit", "search"]).databases == ["app", "audit", "search"]
    with pytest.raises(ValueError):
        Options(databases=["same", "same"])


class Cursor:
    def __init__(self):
        self.calls = []
        self.description = [("value",)]
        self.rows = iter([(1,), (2,), (3,)])

    def execute(self, *args):
        self.calls.append(args)
        self.permission_check = args[0].startswith("SELECT CASE WHEN IS_SRVROLEMEMBER")
        return self

    def fetchone(self):
        if getattr(self, "permission_check", False):
            return (0,)
        return next(self.rows, None)

    def fetchall(self):
        return []

    def nextset(self):
        return None


class Database:
    def __init__(self):
        self.cursor_value = Cursor()
        self.commits = self.rollbacks = 0

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


class Credentials:
    def __init__(self):
        self.purposes = []

    def resolve(self, connection_id, raw, purpose, directory):
        self.purposes.append(purpose)
        return {"username": purpose, "password": "synthetic-secret"}


def test_query_binds_parameters_bounds_results_and_uses_read_identity():
    database, credentials = Database(), Credentials()
    adapter = Adapter(connect=lambda *a, **kw: database)
    raw = {"host": "sql.example.invalid", "operations": ["query", "execute"]}
    result = adapter.call(
        "local",
        raw,
        "query",
        {"sql": "SELECT ?", "parameters": ["';DELETE"], "max_rows": 2},
        "root",
        credentials,
        None,
        None,
    )
    assert database.cursor_value.calls[-1] == ("SELECT ?", ["';DELETE"])
    assert result == {"columns": ["value"], "rows": [[1], [2]], "truncated": True}
    assert credentials.purposes == ["read"] and database.rollbacks == 1 and database.commits == 0


def test_execute_commits_only_through_write_identity():
    database, credentials = Database(), Credentials()
    adapter = Adapter(connect=lambda *a, **kw: database)
    adapter.call(
        "local",
        {"host": "sql.example.invalid"},
        "execute",
        {"sql": "UPDATE app SET value=?", "parameters": [3]},
        "root",
        credentials,
        None,
        None,
    )
    assert credentials.purposes == ["write"] and database.commits == 1


def test_foreign_database_marker_prevents_drop():
    adapter = Adapter()
    adapter.marker = lambda cursor, name: ("foreign", True)
    cursor = Cursor()
    with pytest.raises(ResourceError, match="ownership"):
        adapter.drop_database(cursor, "existing", "ours")
    assert cursor.calls == []


def test_private_sql_connections_use_application_address_and_never_admin():
    lease = Lease(
        lease_id="rs-" + "1" * 32,
        owner=Owner(key="s", directory="root"),
        profile_id="p",
        profile=Profile(connection_id="db"),
        created_at=0,
        expires_at=7200,
        connection={"host": "127.0.0.1", "application_host": "192.0.2.1"},
        resources={
            "databases": {"app": "unique_app"},
            "login": "lease-user",
            "password": "lease-secret",
        },
    )
    value = Adapter().private_connections(lease)["app"]
    assert value["host"] == "192.0.2.1" and value["username"] == "lease-user"
    assert "192.0.2.1" in value["connection_string"]
