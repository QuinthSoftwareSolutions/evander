from __future__ import annotations

import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from evander.repositories.orchestration_db import (
    SCHEMA_VERSION,
    connect_orchestration_db,
    connect_orchestration_db_read_only,
    ensure_orchestration_schema,
)
from evander.repositories.sqlite_runtime import (
    SqliteOperationDeadlineExceeded,
    close_sqlite_runtimes,
    ensure_sqlite_initialized,
    foreground_storage_budget,
    is_storage_busy,
    open_sqlite_connection,
    storage_lock,
)


def test_last_operation_close_keeps_wal_without_pinning_a_read_snapshot(tmp_path):
    path = tmp_path / "orchestration.db"
    ensure_orchestration_schema(path)
    with connect_orchestration_db(path) as writer:
        assert writer.execute("PRAGMA wal_autocheckpoint").fetchone()[0] == 0
        writer.execute("INSERT INTO orchestration_metadata VALUES ('probe', '{}')")
    wal = Path(str(path) + "-wal")
    assert wal.stat().st_size > 0
    # A lifetime lease must not pin old frames: PASSIVE can checkpoint every
    # frame while the anchor survives between independent repository calls.
    with sqlite3.connect(path) as checkpointer:
        busy, frames, copied = checkpointer.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        assert busy == 0 and frames == copied
    with connect_orchestration_db(path) as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE orchestration_metadata SET value_json='new' WHERE key='probe'")
        with connect_orchestration_db_read_only(path) as reader:
            assert (
                reader.execute(
                    "SELECT value_json FROM orchestration_metadata WHERE key='probe'"
                ).fetchone()[0]
                == "{}"
            )
    # Final anchor is read-only, so shutdown does not trigger destructive WAL
    # cleanup/checkpoint. The next initialized process can resume normally.
    before = wal.stat().st_size
    close_sqlite_runtimes(path)
    assert wal.stat().st_size == before


def test_warmed_and_cold_schema_checks_do_not_require_a_writer_lock(tmp_path):
    path = tmp_path / "orchestration.db"
    ensure_orchestration_schema(path)
    writer = sqlite3.connect(path)
    try:
        writer.execute("BEGIN IMMEDIATE")
        ensure_orchestration_schema(path)
        close_sqlite_runtimes(path)
        # Simulates a fresh repository/process with current migration metadata.
        ensure_orchestration_schema(path)
    finally:
        writer.rollback()
        writer.close()


def test_schema_failure_is_not_cached_and_database_replacement_reinitializes(tmp_path):
    path = tmp_path / "state.db"
    attempts = []

    def ready(connection):
        return connection.execute("SELECT 1 FROM readiness").fetchone() is not None

    def initialize(connection):
        attempts.append(1)
        connection.execute("CREATE TABLE IF NOT EXISTS readiness(value INTEGER)")
        if len(attempts) == 1:
            raise ValueError("migration failed")
        connection.execute("INSERT INTO readiness VALUES (1)")

    with pytest.raises(ValueError, match="migration failed"):
        ensure_sqlite_initialized(
            path, schema_key="test:1", is_current=ready, initialize=initialize
        )
    ensure_sqlite_initialized(path, schema_key="test:1", is_current=ready, initialize=initialize)
    ensure_sqlite_initialized(path, schema_key="test:1", is_current=ready, initialize=initialize)
    assert len(attempts) == 2
    # Move all database files together, as an offline restore would do.
    close_sqlite_runtimes(path)
    path.rename(tmp_path / "previous.db")
    for suffix in ("-wal", "-shm"):
        old = Path(str(path) + suffix)
        if old.exists():
            old.rename(tmp_path / ("previous.db" + suffix))
    ensure_sqlite_initialized(path, schema_key="test:1", is_current=ready, initialize=initialize)
    assert len(attempts) == 3


@pytest.mark.parametrize("deadline_seconds", [None, 5.0])
def test_busy_writer_uses_one_budget_and_does_not_commit_partial_writes(tmp_path, deadline_seconds):
    path = tmp_path / "orchestration.db"
    ensure_orchestration_schema(path)
    writer = sqlite3.connect(path)
    try:
        writer.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        with (
            pytest.raises(sqlite3.OperationalError) as caught,
            connect_orchestration_db(
                path, busy_timeout_ms=30, deadline_seconds=deadline_seconds,
            ) as connection,
        ):
            connection.execute("INSERT INTO orchestration_metadata VALUES ('blocked', '{}')")
        assert is_storage_busy(caught.value)
        assert time.monotonic() - started < 1
    finally:
        writer.rollback()
        writer.close()
    with connect_orchestration_db_read_only(path) as reader:
        assert (
            reader.execute("SELECT 1 FROM orchestration_metadata WHERE key='blocked'").fetchone()
            is None
        )


def test_sql_deadline_interrupts_query_and_remains_retryable(tmp_path):
    path = tmp_path / "orchestration.db"
    ensure_orchestration_schema(path)
    with (
        pytest.raises(SqliteOperationDeadlineExceeded) as caught,
        open_sqlite_connection(path, read_only=True, timeout_ms=10) as connection,
    ):
        connection.execute("""WITH RECURSIVE numbers(x) AS (
                VALUES(1) UNION ALL SELECT x+1 FROM numbers WHERE x<100000000
            ) SELECT sum(x) FROM numbers""").fetchone()
    assert is_storage_busy(caught.value)
    with connect_orchestration_db_read_only(path) as connection:
        assert connection.execute("SELECT 1").fetchone()[0] == 1


def test_repository_lock_and_sql_share_foreground_budget(tmp_path):
    path = tmp_path / "orchestration.db"
    ensure_orchestration_schema(path)
    lock = threading.RLock()
    with foreground_storage_budget(0.02), storage_lock(lock):
        # Spend the operation's budget before opening the connection.
        time.sleep(0.03)
        with (
            pytest.raises(SqliteOperationDeadlineExceeded),
            connect_orchestration_db_read_only(path) as connection,
        ):
            connection.execute("SELECT 1")
    assert lock.acquire(blocking=False)
    lock.release()


def test_read_only_missing_database_does_not_create_it(tmp_path):
    path = tmp_path / "absent.db"
    with pytest.raises(sqlite3.OperationalError):
        open_sqlite_connection(path, read_only=True)
    assert not path.exists()


def test_concurrent_process_first_initialization_publishes_one_complete_schema(tmp_path):
    path = tmp_path / "orchestration.db"
    script = """import sys
from pathlib import Path
from evander.repositories.orchestration_db import ensure_orchestration_schema, connect_orchestration_db_read_only
sys.stdin.read(1)
path = Path(sys.argv[1])
ensure_orchestration_schema(path)
with connect_orchestration_db_read_only(path) as connection:
    print(connection.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0])
"""
    children = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    try:
        for child in children:
            child.stdin.write("x")
            child.stdin.flush()
        for child in children:
            stdout, stderr = child.communicate(timeout=10)
            assert child.returncode == 0, stderr
            assert stdout.strip() == str(SCHEMA_VERSION)
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
