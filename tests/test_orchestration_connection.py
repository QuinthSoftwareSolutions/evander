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
    foreground_storage_wait_budget,
    is_storage_busy,
    is_storage_contention,
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


def test_current_schema_delete_journal_is_repaired_before_readiness_is_cached(tmp_path):
    path = tmp_path / "converted.db"
    ensure_orchestration_schema(path)
    close_sqlite_runtimes(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0] == "delete"
        connection.execute("INSERT INTO orchestration_metadata VALUES ('probe', 'old')")

    ensure_sqlite_initialized(
        path, schema_key="converted-current", is_current=lambda _connection: True,
        initialize=lambda _connection: pytest.fail("current schema must not be migrated"),
    )
    with connect_orchestration_db_read_only(path) as reader:
        assert reader.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        reader.execute("BEGIN")
        assert reader.execute(
            "SELECT value_json FROM orchestration_metadata WHERE key='probe'"
        ).fetchone()[0] == "old"
        with open_sqlite_connection(path, timeout_ms=20) as writer:
            writer.execute("UPDATE orchestration_metadata SET value_json='new' WHERE key='probe'")
        assert reader.execute(
            "SELECT value_json FROM orchestration_metadata WHERE key='probe'"
        ).fetchone()[0] == "old"


def test_failed_journal_repair_is_bounded_and_not_cached(tmp_path):
    path = tmp_path / "locked.db"
    with sqlite3.connect(path) as setup:
        setup.execute("CREATE TABLE readiness(value INTEGER)")
        setup.execute("INSERT INTO readiness VALUES (1)")
    reader = sqlite3.connect(path)
    reader.execute("BEGIN")
    reader.execute("SELECT * FROM readiness").fetchall()
    kwargs = dict(
        schema_key="locked-current", is_current=lambda _connection: True,
        initialize=lambda _connection: pytest.fail("unexpected migration"), timeout_ms=30,
    )
    started = time.monotonic()
    try:
        with pytest.raises(sqlite3.OperationalError):
            ensure_sqlite_initialized(path, **kwargs)
        assert time.monotonic() - started < 1
    finally:
        reader.rollback()
        reader.close()
    ensure_sqlite_initialized(path, **kwargs)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_concurrent_processes_repair_current_schema_journal_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    path = tmp_path / "converted.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE readiness(value INTEGER)")
        connection.execute("INSERT INTO readiness VALUES (1)")
    script = """
import sys
from evander.repositories.sqlite_runtime import ensure_sqlite_initialized, open_sqlite_connection
def unexpected(connection):
    raise AssertionError('current schema must not be migrated')
ensure_sqlite_initialized(sys.argv[1], schema_key='current',
    is_current=lambda c: c.execute('SELECT value FROM readiness').fetchone()[0] == 1,
    initialize=unexpected, timeout_ms=3000)
with open_sqlite_connection(sys.argv[1], read_only=True) as connection:
    print(connection.execute('PRAGMA journal_mode').fetchone()[0])
"""

    def initialize(_index):
        return subprocess.run([sys.executable, "-c", script, str(path)],
                              capture_output=True, text=True, timeout=10, check=True).stdout.strip()

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert list(executor.map(initialize, range(4))) == ["wal"] * 4


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


@pytest.mark.parametrize("deadline_seconds", [None, 0.01, 5.0])
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
        assert is_storage_contention(caught.value)
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
        open_sqlite_connection(path, read_only=True, deadline_seconds=0.01) as connection,
    ):
        connection.execute("""WITH RECURSIVE numbers(x) AS (
                VALUES(1) UNION ALL SELECT x+1 FROM numbers WHERE x<100000000
            ) SELECT sum(x) FROM numbers""").fetchone()
    assert is_storage_busy(caught.value)
    assert not is_storage_contention(caught.value)
    with connect_orchestration_db_read_only(path) as connection:
        assert connection.execute("SELECT 1").fetchone()[0] == 1


@pytest.mark.parametrize("read_only", [False, True])
def test_short_lock_timeout_does_not_limit_query_or_row_processing(tmp_path, read_only):
    path = tmp_path / "orchestration.db"
    ensure_orchestration_schema(path)
    with open_sqlite_connection(path, read_only=read_only, timeout_ms=10) as connection:
        connection.execute("BEGIN")

        def slow_sum(value):
            time.sleep(0.03)
            return value + 1

        connection.create_function("slow_sum", 1, slow_sum)
        assert connection.execute("SELECT slow_sum(41)").fetchone()[0] == 42
        # Subsequent SQL and commit must still work after query and Python time
        # have consumed the lock-wait allowance.
        time.sleep(0.02)
        assert connection.execute("SELECT 6 * 7").fetchone()[0] == 42
        if not read_only:
            connection.execute("INSERT INTO orchestration_metadata VALUES ('completed', '42')")
    if not read_only:
        with connect_orchestration_db_read_only(path) as connection:
            assert connection.execute(
                "SELECT value_json FROM orchestration_metadata WHERE key='completed'"
            ).fetchone()[0] == "42"


def test_normal_repository_lock_can_wait_beyond_foreground_budget(tmp_path):
    path = tmp_path / "orchestration.db"
    ensure_orchestration_schema(path)
    lock = threading.Lock()
    lock.acquire()
    release = threading.Timer(0.15, lock.release)
    release.start()
    try:
        with storage_lock(lock), connect_orchestration_db_read_only(path) as connection:
            assert connection.execute("SELECT 6 * 7").fetchone()[0] == 42
    finally:
        release.join(timeout=2)


def test_waiting_page_hydration_survives_foreground_lock_budget(tmp_path, monkeypatch):
    from evander.daemons.effect_orchestrator import SqliteExecutionStore
    from evander.repositories import orchestration
    from test_operational_payload_storage import record

    store = SqliteExecutionStore(tmp_path / "orchestration.db")
    store.put(record(status="waiting", outputs={"large": "data" * 6000}))
    load_record = orchestration.load_record

    def delayed_hydration(*args, **kwargs):
        time.sleep(0.12)
        return load_record(*args, **kwargs)

    monkeypatch.setattr(orchestration, "load_record", delayed_hydration)
    with foreground_storage_wait_budget(), storage_lock(store._lock):
        records = store.list_waiting_records_page(node_type="effect:test", limit=10)
    assert len(records) == 1
    assert records[0].status == "waiting"
    assert len(records[0].outputs["large"]) == 24000


def test_expired_foreground_wait_budget_allows_uncontended_sql(tmp_path):
    path = tmp_path / "orchestration.db"
    ensure_orchestration_schema(path)
    lock = threading.RLock()
    blocker = sqlite3.connect(path)
    with foreground_storage_wait_budget(0.02), storage_lock(lock):
        # Row processing can consume the wait allowance. Uncontended reads
        # remain valid while a subsequent contested write still fails quickly.
        time.sleep(0.03)
        with connect_orchestration_db_read_only(path) as connection:
            assert connection.execute("SELECT 1").fetchone()[0] == 1
        try:
            blocker.execute("BEGIN IMMEDIATE")
            started = time.monotonic()
            with pytest.raises(sqlite3.OperationalError) as caught, connect_orchestration_db(path) as connection:
                connection.execute("INSERT INTO orchestration_metadata VALUES ('blocked', '{}')")
            assert is_storage_busy(caught.value)
            assert time.monotonic() - started < 1
        finally:
            blocker.close()
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


def test_reused_reader_finalizes_cursors_releases_wal_and_sees_new_commits(tmp_path):
    path = tmp_path / "orchestration.db"
    ensure_orchestration_schema(path)
    with connect_orchestration_db(path) as writer:
        writer.executemany(
            "INSERT INTO orchestration_metadata VALUES (?,?)", [("a", "old"), ("b", "other")]
        )
    with connect_orchestration_db_read_only(path, reuse_connection=True) as first:
        cursor = first.execute("SELECT value_json FROM orchestration_metadata ORDER BY key")
        assert cursor.fetchone()[0] == "old"
        # Leave the cursor unfinished, without an explicit BEGIN.
    with pytest.raises(sqlite3.ProgrammingError):
        cursor.fetchone()
    with connect_orchestration_db(path) as writer:
        writer.execute("UPDATE orchestration_metadata SET value_json='new' WHERE key='a'")
    with sqlite3.connect(path) as checkpointer:
        busy, frames, copied = checkpointer.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        assert busy == 0 and frames == copied
    with connect_orchestration_db_read_only(path, reuse_connection=True) as second:
        assert second is first  # The report reuses its parsed schema and page cache.
        assert not second.in_transaction
        assert second.execute("SELECT value_json FROM orchestration_metadata WHERE key='a'").fetchone()[0] == "new"
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            second.execute("DELETE FROM orchestration_metadata")
    close_sqlite_runtimes(path)


def test_reused_reader_gets_fresh_snapshot_deadline_and_callbacks(tmp_path):
    path = tmp_path / "orchestration.db"
    ensure_orchestration_schema(path)
    statements = []
    with connect_orchestration_db(path) as writer:
        writer.execute("INSERT INTO orchestration_metadata VALUES ('probe','old')")
    with connect_orchestration_db_read_only(path, reuse_connection=True, deadline_seconds=0.1) as first:
        first.set_trace_callback(statements.append)
        first.execute("BEGIN")
        assert first.execute("SELECT value_json FROM orchestration_metadata WHERE key='probe'").fetchone()[0] == "old"
        with connect_orchestration_db(path) as writer:
            writer.execute("UPDATE orchestration_metadata SET value_json='new' WHERE key='probe'")
        assert first.execute("SELECT value_json FROM orchestration_metadata WHERE key='probe'").fetchone()[0] == "old"
    prior_statements = list(statements)
    time.sleep(0.11)
    with connect_orchestration_db_read_only(path, reuse_connection=True, deadline_seconds=1) as second:
        assert second is first
        second.execute("BEGIN")
        assert second.execute("SELECT value_json FROM orchestration_metadata WHERE key='probe'").fetchone()[0] == "new"
    assert statements == prior_statements
    close_sqlite_runtimes(path)


def test_reused_reader_deadline_failure_does_not_poison_next_report(tmp_path):
    path = tmp_path / "orchestration.db"
    ensure_orchestration_schema(path)
    with pytest.raises(SqliteOperationDeadlineExceeded), connect_orchestration_db_read_only(
        path, reuse_connection=True, deadline_seconds=0.01,
    ) as first:
        first.execute("BEGIN")
        first.execute("""WITH RECURSIVE numbers(x) AS (
            VALUES(1) UNION ALL SELECT x+1 FROM numbers WHERE x<100000000
        ) SELECT sum(x) FROM numbers""").fetchone()
    with connect_orchestration_db_read_only(path, reuse_connection=True, deadline_seconds=1) as second:
        assert second is not first
        assert second.execute("SELECT 6*7").fetchone()[0] == 42
    close_sqlite_runtimes(path)


def test_reused_reader_detects_database_replacement_and_runtime_retirement(tmp_path):
    path = tmp_path / "state.db"
    replacement = tmp_path / "replacement.db"
    for database, value in ((path, 41), (replacement, 42)):
        with sqlite3.connect(database) as writer:
            writer.execute("CREATE TABLE values_table(value INTEGER)")
            writer.execute("INSERT INTO values_table VALUES (?)", (value,))
    with open_sqlite_connection(path, read_only=True, reuse_read_only=True) as first:
        assert first.execute("SELECT value FROM values_table").fetchone()[0] == 41
    replacement.replace(path)
    with open_sqlite_connection(path, read_only=True, reuse_read_only=True) as second:
        assert second is not first
        assert second.execute("SELECT value FROM values_table").fetchone()[0] == 42
        # An active lease retired by shutdown must not return to the idle pool.
        close_sqlite_runtimes(path)
    with open_sqlite_connection(path, read_only=True, reuse_read_only=True) as third:
        assert third is not second
        assert third.execute("SELECT value FROM values_table").fetchone()[0] == 42
    close_sqlite_runtimes(path)


def test_reused_readers_do_not_share_active_connections_across_threads(tmp_path):
    path = tmp_path / "orchestration.db"
    ensure_orchestration_schema(path)
    barrier = threading.Barrier(2)
    readers = []
    failures = []

    def read():
        try:
            with connect_orchestration_db_read_only(path, reuse_connection=True) as first:
                first.execute("BEGIN")
                readers.append(first)
                barrier.wait(timeout=3)
                assert first.execute("SELECT 6*7").fetchone()[0] == 42
            with connect_orchestration_db_read_only(path, reuse_connection=True) as second:
                assert second is first
                assert not second.in_transaction
                assert second.execute("SELECT 40+2").fetchone()[0] == 42
        except BaseException as error:
            failures.append(error)

    threads = [threading.Thread(target=read) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()
    assert not failures
    assert len(readers) == 2 and readers[0] is not readers[1]
    close_sqlite_runtimes(path)


def test_reused_reader_pool_bounds_idle_handles(tmp_path, monkeypatch):
    from evander.repositories import sqlite_runtime

    close_sqlite_runtimes()
    monkeypatch.setattr(sqlite_runtime, "_MAX_IDLE_READERS", 2)
    paths = [tmp_path / f"state-{i}.db" for i in range(3)]
    first = None
    for path in paths:
        with sqlite3.connect(path) as writer:
            writer.execute("CREATE TABLE values_table(value INTEGER)")
        with open_sqlite_connection(path, read_only=True, reuse_read_only=True) as reader:
            reader.execute("SELECT * FROM values_table").fetchall()
            if first is None:
                first = reader
    # Check physical disposal, bypassing the lease's logical-close guard.
    with pytest.raises(sqlite3.ProgrammingError):
        sqlite3.Connection.execute(first, "SELECT 1")
    close_sqlite_runtimes()
