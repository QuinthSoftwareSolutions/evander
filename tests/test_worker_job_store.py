from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Thread

import pytest

from agent_sandbox.worker import WorkerJobRecord, WorkerJobStore


BASE_TIME = datetime(2026, 5, 3, 20, 0, tzinfo=UTC)


def _store(tmp_path: Path) -> WorkerJobStore:
    return WorkerJobStore(tmp_path / "worker-jobs.json")


def _record(
    tmp_path: Path,
    *,
    job_id: str = "job-1",
    session_id: str = "session-1",
    status: str = "created",
    started_at: datetime | None = BASE_TIME,
    finished_at: datetime | None = None,
    cleanup_status: str = "unknown",
    cleanup_attempted_at: datetime | None = None,
    cleanup_completed_at: datetime | None = None,
    cleanup_error: str | None = None,
    error: str | None = None,
    metadata: dict[str, object] | None = None,
) -> WorkerJobRecord:
    session_root = tmp_path / "worker-root" / "sessions" / session_id
    return WorkerJobRecord(
        job_id=job_id,
        session_id=session_id,
        image="codex:latest",
        command=["true"],
        container_name=f"agent-worker-{job_id}",
        status=status,
        created_at=BASE_TIME,
        started_at=started_at,
        finished_at=finished_at,
        agent_home_path=str(session_root / "agent_home"),
        repo_root_path=str(session_root / "repos"),
        artifacts_path=str(session_root / "artifacts"),
        logs_path=str(session_root / "logs"),
        metadata=metadata or {},
        error=error,
        cleanup_status=cleanup_status,
        cleanup_attempted_at=cleanup_attempted_at,
        cleanup_completed_at=cleanup_completed_at,
        cleanup_error=cleanup_error,
    )


def _bootstrap_worker_jobs_db(db_path: Path, record: WorkerJobRecord) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(record.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE worker_jobs (
                job_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                container_name TEXT NOT NULL,
                status TEXT NOT NULL,
                cleanup_status TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                payload_json TEXT NOT NULL
            );
            """
        )
        connection.execute(
            """
            INSERT INTO worker_jobs (
                job_id, session_id, container_name, status, cleanup_status,
                started_at, finished_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.job_id,
                record.session_id,
                record.container_name,
                record.status,
                record.cleanup_status,
                record.started_at.isoformat().replace("+00:00", "Z") if record.started_at else None,
                record.finished_at.isoformat().replace("+00:00", "Z") if record.finished_at else None,
                payload,
            ),
        )
        connection.commit()


def test_worker_job_store_resolves_json_path_to_sqlite_db(tmp_path: Path) -> None:
    store = _store(tmp_path)

    assert store.path == tmp_path / "worker-jobs.db"


def test_worker_job_store_initializes_schema_and_indexes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.put(_record(tmp_path))

    with sqlite3.connect(store.path) as connection:
        table_columns = [
            (row[1], row[2])
            for row in connection.execute("PRAGMA table_info(worker_jobs)")
        ]
        indexed_columns = {
            tuple(index_row[2] for index_row in connection.execute(f"PRAGMA index_info('{name}')"))
            for _, name, _, origin, _ in connection.execute("PRAGMA index_list(worker_jobs)")
            if origin != "pk"
        }
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )
        }

    assert "worker_jobs" in tables
    assert table_columns == [
        ("job_id", "TEXT"),
        ("session_id", "TEXT"),
        ("container_name", "TEXT"),
        ("status", "TEXT"),
        ("cleanup_status", "TEXT"),
        ("started_at", "TEXT"),
        ("finished_at", "TEXT"),
        ("payload_json", "TEXT"),
    ]
    assert indexed_columns == {
        ("session_id", "status", "started_at", "job_id"),
        ("session_id", "started_at", "job_id"),
        ("status", "started_at", "job_id"),
        ("cleanup_status", "started_at", "job_id"),
    }


def test_worker_job_store_put_get_delete_and_read_all_round_trip_sqlite(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    record = _record(
        tmp_path,
        metadata={"purpose": "test", "attempt": 1},
        cleanup_status="cleanup_failed",
        cleanup_error="permission denied",
        error="boom",
        finished_at=BASE_TIME + timedelta(minutes=5),
        cleanup_attempted_at=BASE_TIME + timedelta(minutes=6),
        cleanup_completed_at=BASE_TIME + timedelta(minutes=7),
    )

    store.put(record)

    assert store.get(record.job_id) == record
    assert store.read_all() == {record.job_id: record}

    with sqlite3.connect(store.path) as connection:
        row = connection.execute(
            "SELECT payload_json FROM worker_jobs WHERE job_id = ?",
            (record.job_id,),
        ).fetchone()

    assert row is not None
    assert row[0] == json.dumps(
        record.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )

    store.delete(record.job_id)

    assert store.get(record.job_id) is None
    assert store.read_all() == {}


def test_worker_job_store_imports_legacy_json_into_empty_db_without_rewriting_source(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    legacy_path = runtime_dir / "worker-jobs.json"
    legacy_text = json.dumps(
        {
            "legacy-job": {
                "job_id": "legacy-job",
                "session_id": "legacy-session",
                "image": "codex:latest",
                "command": ["true"],
                "container_name": "agent-worker-legacy-job",
                "status": "exited",
                "created_at": "2026-05-03T20:00:00Z",
                "started_at": "2026-05-03T20:01:00Z",
                "workspace_path": str(
                    tmp_path / "worker-root" / "sessions" / "legacy-session" / "workspace"
                ),
                "agent_home_path": str(
                    tmp_path / "worker-root" / "sessions" / "legacy-session" / "agent_home"
                ),
                "artifacts_path": str(
                    tmp_path / "worker-root" / "sessions" / "legacy-session" / "artifacts"
                ),
                "logs_path": str(
                    tmp_path / "worker-root" / "sessions" / "legacy-session" / "logs"
                ),
                "metadata": {"source": "legacy"},
            }
        },
        indent=2,
        sort_keys=True,
    )
    legacy_path.write_text(legacy_text, encoding="utf-8")
    store = WorkerJobStore(legacy_path)

    record = store.get("legacy-job")

    assert store.path == runtime_dir / "worker-jobs.db"
    assert legacy_path.read_text(encoding="utf-8") == legacy_text
    assert record is not None
    assert record.repo_root_path == str(
        tmp_path / "worker-root" / "sessions" / "legacy-session" / "workspace"
    )
    assert not hasattr(record, "workspace_path")

    with sqlite3.connect(store.path) as connection:
        rows = connection.execute("SELECT job_id, payload_json FROM worker_jobs").fetchall()

    assert [row[0] for row in rows] == ["legacy-job"]
    assert "workspace_path" not in rows[0][1]


def test_worker_job_store_skips_legacy_import_when_db_already_has_rows(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    db_path = runtime_dir / "worker-jobs.db"
    existing = _record(tmp_path, job_id="sqlite-job", session_id="sqlite-session")
    _bootstrap_worker_jobs_db(db_path, existing)
    legacy_path = runtime_dir / "worker-jobs.json"
    legacy_text = json.dumps(
        {
            "legacy-job": {
                "job_id": "legacy-job",
                "session_id": "legacy-session",
                "image": "codex:latest",
                "command": ["true"],
                "container_name": "agent-worker-legacy-job",
                "status": "exited",
                "created_at": "2026-05-03T20:00:00Z",
                "started_at": "2026-05-03T20:01:00Z",
                "workspace_path": str(
                    tmp_path / "worker-root" / "sessions" / "legacy-session" / "workspace"
                ),
                "agent_home_path": str(
                    tmp_path / "worker-root" / "sessions" / "legacy-session" / "agent_home"
                ),
                "artifacts_path": str(
                    tmp_path / "worker-root" / "sessions" / "legacy-session" / "artifacts"
                ),
                "logs_path": str(
                    tmp_path / "worker-root" / "sessions" / "legacy-session" / "logs"
                ),
                "metadata": {"source": "legacy"},
            }
        },
        indent=2,
        sort_keys=True,
    )
    legacy_path.write_text(legacy_text, encoding="utf-8")
    store = WorkerJobStore(legacy_path)

    records = store.list_all_ordered()

    assert store.path == db_path
    assert [record.job_id for record in records] == ["sqlite-job"]
    assert records[0] == existing
    assert legacy_path.read_text(encoding="utf-8") == legacy_text


@pytest.mark.parametrize("legacy_text", ["[]", "not json"])
def test_worker_job_store_rejects_invalid_legacy_json_payloads(
    tmp_path: Path,
    legacy_text: str,
) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    legacy_path = runtime_dir / "worker-jobs.json"
    legacy_path.write_text(legacy_text, encoding="utf-8")
    store = WorkerJobStore(legacy_path)

    with pytest.raises(ValueError):
        store.read_all()


def test_worker_job_store_list_by_session_orders_rows(tmp_path: Path) -> None:
    store = _store(tmp_path)
    records = [
        _record(
            tmp_path,
            job_id="job-b",
            session_id="session-a",
            status="running",
            started_at=BASE_TIME + timedelta(minutes=1),
        ),
        _record(
            tmp_path,
            job_id="job-a",
            session_id="session-a",
            status="created",
            started_at=BASE_TIME + timedelta(minutes=1),
        ),
        _record(
            tmp_path,
            job_id="job-c",
            session_id="session-b",
            status="running",
            started_at=BASE_TIME - timedelta(minutes=1),
        ),
    ]
    for record in records:
        store.put(record)

    session_rows = store.list_by_session("session-a")

    assert [record.job_id for record in session_rows] == ["job-a", "job-b"]
    assert [record.session_id for record in session_rows] == ["session-a", "session-a"]


def test_worker_job_store_list_active_by_session_excludes_terminal_rows(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    active_records = [
        _record(
            tmp_path,
            job_id="job-created",
            session_id="session-a",
            status="created",
            started_at=BASE_TIME,
        ),
        _record(
            tmp_path,
            job_id="job-starting",
            session_id="session-a",
            status="starting",
            started_at=BASE_TIME + timedelta(minutes=1),
        ),
        _record(
            tmp_path,
            job_id="job-running",
            session_id="session-a",
            status="running",
            started_at=BASE_TIME + timedelta(minutes=2),
        ),
    ]
    terminal_records = [
        _record(
            tmp_path,
            job_id="job-exited",
            session_id="session-a",
            status="exited",
            started_at=BASE_TIME + timedelta(minutes=3),
        ),
        _record(
            tmp_path,
            job_id="job-removed",
            session_id="session-a",
            status="removed",
            started_at=BASE_TIME + timedelta(minutes=4),
        ),
        _record(
            tmp_path,
            job_id="job-other-session",
            session_id="session-b",
            status="running",
            started_at=BASE_TIME + timedelta(minutes=5),
        ),
    ]
    for record in [*active_records, *terminal_records]:
        store.put(record)

    active_rows = store.list_active_by_session("session-a")

    assert [record.job_id for record in active_rows] == [
        "job-created",
        "job-starting",
        "job-running",
    ]
    assert [record.status for record in active_rows] == [
        "created",
        "starting",
        "running",
    ]


def test_worker_job_store_list_all_ordered_places_null_started_at_last(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    records = [
        _record(
            tmp_path,
            job_id="job-b",
            session_id="session-b",
            started_at=BASE_TIME,
        ),
        _record(
            tmp_path,
            job_id="job-a",
            session_id="session-a",
            started_at=BASE_TIME,
        ),
        _record(
            tmp_path,
            job_id="job-null",
            session_id="session-c",
            started_at=None,
        ),
    ]
    for record in records:
        store.put(record)

    ordered = store.list_all_ordered()

    assert [record.job_id for record in ordered] == ["job-a", "job-b", "job-null"]


def test_worker_job_store_concurrent_puts_preserve_all_rows(tmp_path: Path) -> None:
    store = _store(tmp_path)
    barrier = Barrier(6)
    records = [
        _record(
            tmp_path,
            job_id=f"job-{index}",
            session_id="session-a",
            started_at=BASE_TIME + timedelta(minutes=index),
        )
        for index in range(5)
    ]

    def _put(record: WorkerJobRecord) -> None:
        barrier.wait(timeout=5)
        store.put(record)

    threads = [Thread(target=_put, args=(record,)) for record in records]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert {record.job_id for record in store.list_all_ordered()} == {
        record.job_id for record in records
    }
