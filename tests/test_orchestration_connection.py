from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from evander.repositories import orchestration_db


class _Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class _Connection:
    def __init__(self, clock, outcomes):
        self.clock = clock
        self.outcomes = iter(outcomes)
        self.commands = []
        self.functions = []
        self.closed = False
        self.entered = False

    def execute(self, statement):
        self.commands.append(statement)
        if statement == "PRAGMA journal_mode=WAL":
            elapsed, error = next(self.outcomes)
            self.clock.now += elapsed
            if error is not None:
                raise error

    def create_function(self, *args):
        self.functions.append(args)

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *_):
        pass

    def close(self):
        self.closed = True


def _sqlite_error(code):
    error = sqlite3.OperationalError("synthetic SQLite failure")
    if code is not None:
        error.sqlite_errorcode = code
    return error


def _connection_fixture(monkeypatch, outcomes, *, connect_elapsed=0.0):
    clock = _Clock()
    connection = _Connection(clock, outcomes)
    calls = []

    def connect(path, *, timeout):
        calls.append((path, timeout))
        clock.now += connect_elapsed
        return connection

    # Patch the repository's imports, leaving process-wide sqlite/time untouched.
    monkeypatch.setattr(orchestration_db, "time", clock)
    monkeypatch.setattr(
        orchestration_db,
        "sqlite3",
        SimpleNamespace(
            connect=connect,
            Row=sqlite3.Row,
            OperationalError=sqlite3.OperationalError,
            SQLITE_BUSY=sqlite3.SQLITE_BUSY,
        ),
    )
    return connection, clock, calls


@pytest.mark.parametrize(
    "code", [sqlite3.SQLITE_BUSY, sqlite3.SQLITE_BUSY_RECOVERY, sqlite3.SQLITE_BUSY_SNAPSHOT]
)
def test_connection_retries_busy_wal_transition_within_original_budget(tmp_path, monkeypatch, code):
    connection, clock, calls = _connection_fixture(
        monkeypatch,
        [(0.25, _sqlite_error(code)), (0.0, None)],
        connect_elapsed=0.5,
    )
    path = tmp_path / "orchestration.db"

    with orchestration_db.connect_orchestration_db(path) as opened:
        assert opened is connection
        assert not connection.closed

    assert calls == [(path, 30.0)]
    assert connection.commands == [
        "PRAGMA busy_timeout=29500",
        "PRAGMA journal_mode=WAL",
        "PRAGMA busy_timeout=29240",
        "PRAGMA journal_mode=WAL",
        "PRAGMA busy_timeout=30000",
        "PRAGMA foreign_keys=ON",
    ]
    assert clock.sleeps == [0.01]
    assert connection.row_factory is sqlite3.Row
    assert connection.functions[0][:2] == ("execution_history_numeric_compare", 2)
    assert connection.entered
    assert connection.closed


@pytest.mark.parametrize("busy_seconds", [30.0, 29.995])
def test_connection_closes_and_propagates_busy_when_total_budget_expires(
    tmp_path, monkeypatch, busy_seconds
):
    error = _sqlite_error(sqlite3.SQLITE_BUSY)
    connection, clock, _ = _connection_fixture(monkeypatch, [(busy_seconds, error)])

    with pytest.raises(sqlite3.OperationalError) as caught:
        with orchestration_db.connect_orchestration_db(tmp_path / "orchestration.db"):
            pytest.fail("An exhausted WAL negotiation must not yield the connection")

    assert caught.value is error
    assert clock.now == 30.0
    assert sum(clock.sleeps) == pytest.approx(30.0 - busy_seconds)
    assert connection.commands == ["PRAGMA busy_timeout=30000", "PRAGMA journal_mode=WAL"]
    assert connection.closed
    assert not connection.entered
    assert connection.functions == []


@pytest.mark.parametrize("code", [sqlite3.SQLITE_ERROR, sqlite3.SQLITE_LOCKED, None])
def test_connection_propagates_non_busy_wal_errors_without_retry(tmp_path, monkeypatch, code):
    error = _sqlite_error(code)
    connection, clock, _ = _connection_fixture(monkeypatch, [(0.0, error)])

    with pytest.raises(sqlite3.OperationalError) as caught:
        with orchestration_db.connect_orchestration_db(tmp_path / "orchestration.db"):
            pytest.fail("A failed WAL negotiation must not yield the connection")

    assert caught.value is error
    assert clock.now == 0
    assert clock.sleeps == []
    assert connection.commands == ["PRAGMA busy_timeout=30000", "PRAGMA journal_mode=WAL"]
    assert connection.closed
    assert not connection.entered
    assert connection.functions == []
