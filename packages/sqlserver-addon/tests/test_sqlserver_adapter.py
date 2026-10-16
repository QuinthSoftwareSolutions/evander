from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evander_sqlserver_addon.adapter import Adapter, Connection, Options, ident
from evander.resources.models import Lease, Owner, Profile, ResourceError


def test_identifiers_are_quoted_and_aliases_are_configurable():
    assert ident("name];DROP TABLE app;--") == "[name]];DROP TABLE app;--]"
    assert Options(databases=["app", "audit", "search"]).databases == ["app", "audit", "search"]
    with pytest.raises(ValueError):
        Options(databases=["same", "same"])


class Cursor:
    def __init__(self):
        self.calls = []
        self.description = [("value", int, None, 10, 10, 0, True)]
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


@pytest.mark.parametrize(
    "kind, declared", [(str, 8_388_608), (bytes, 8_388_608), (str, 0), (bytes, -1), (object, 32)]
)
def test_sql_rejects_unsafe_columns_before_fetching_cells(kind, declared):
    class UnsafeCursor:
        description = [("payload", kind, None, declared, declared, 0, True)]

        def fetchone(self):
            pytest.fail("Oversized or unknown columns must never be materialized")

    with pytest.raises(ResourceError, match="explicitly CAST"):
        Adapter().results(
            UnsafeCursor(), Connection(host="sql.example.invalid", max_bytes=1024), 10
        )


def test_sql_bounds_a_whole_row_before_fetch_and_encodes_bounded_binary_unicode():
    class BoundedCursor:
        description = [
            ("text", str, None, 32, 32, 0, True),
            ("binary", bytes, None, 32, 32, 0, True),
        ]
        rows = iter([("\U0001f600" * 32, b"\xff" * 32)])

        def fetchone(self):
            return next(self.rows, None)

    adapter, config = Adapter(), Connection(host="sql.example.invalid", max_bytes=1024)
    result = adapter.results(BoundedCursor(), config, 10)
    assert result["rows"] == [["\U0001f600" * 32, "/" * 42 + "8="]]
    assert not result["truncated"]

    class WideCursor:
        # Each column fits individually; their combined declared sizes do not.
        description = [(name, str, None, 64, 64, 0, True) for name in ("a", "b")]

        def fetchone(self):
            pytest.fail("An oversized complete row must never be materialized")

    with pytest.raises(ResourceError, match="explicitly CAST"):
        adapter.results(WideCursor(), config, 10)


def test_database_creation_rejects_supplied_identity_without_touching_foreign_records(tmp_path):
    from evander.addons import AddonContext
    from evander.resources.models import Settings
    from evander.resources.service import ResourceService

    def unexpected(*args, **kwargs):
        pytest.fail("An invalid create request must not access SQL or credentials")

    domain = ResourceService(
        AddonContext("sqlserver-addon", tmp_path, tmp_path / "addons/sqlserver-addon"),
        Adapter(connect=unexpected),
        credentials=type("NoCredentials", (), {"resolve": unexpected})(),
    )
    domain.store.save_settings(
        "root/second",
        0,
        Settings(
            connections={
                "registered": {
                    "host": "sql.example.invalid",
                    "operations": ["manage_database"],
                }
            }
        ),
        "operator",
    )
    original = {
        "id": "original",
        "directory": "root/first",
        "connection_id": "different",
        "database": "existing",
        "marker": "original-marker",
        "state": "ready",
    }
    domain.store.put_operation("sql-managed:original", "original", original)
    for supplied in ("original", "untracked-id", ""):
        with pytest.raises(ResourceError, match="resource_id"):
            domain.call(
                Owner(key="caller", directory="root/second"),
                "registered",
                "manage_database",
                {"action": "create", "name": "new_database", "resource_id": supplied},
                "create",
            )
        assert domain.store.operation("sql-managed:original")[1] == original
        assert domain.store.operations("root/second") == []
