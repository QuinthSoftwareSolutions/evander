from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from multiprocessing import get_context
from pathlib import Path

import pytest

from evander.worker.models import WorkerJobRecord
from evander.worker.store import WorkerJobStore

_CREATED_AT = datetime(2026, 5, 26, 8, 0, tzinfo=UTC)
_STARTED_AT = datetime(2026, 5, 26, 9, 0, tzinfo=UTC)
_FINISHED_AT = datetime(2026, 5, 26, 10, 0, tzinfo=UTC)
_EARLIER_STARTED_AT = _STARTED_AT - timedelta(hours=1)
_LATER_STARTED_AT = _STARTED_AT + timedelta(hours=1)


def _record(
    job_id: str,
    *,
    session_id: str = "session-a",
    status: str = "running",
    started_at: datetime | None = _STARTED_AT,
    finished_at: datetime | None = None,
    cleanup_status: str = "unknown",
) -> WorkerJobRecord:
    session_root = Path("/worker-root") / "sessions" / session_id
    return WorkerJobRecord(
        job_id=job_id,
        session_id=session_id,
        image="codex:latest",
        command=["true"],
        container_name=f"agent-worker-{job_id}",
        status=status,
        created_at=_CREATED_AT,
        started_at=started_at,
        finished_at=finished_at,
        agent_home_path=str(session_root / "agent_home"),
        repo_root_path=str(session_root / "repos"),
        artifacts_path=str(session_root / "artifacts"),
        logs_path=str(session_root / "logs"),
        metadata={},
        cleanup_status=cleanup_status,
    )


def _legacy_payload(record: WorkerJobRecord) -> dict[str, object]:
    payload = record.model_dump(mode="json")
    payload["workspace_path"] = payload.pop("repo_root_path")
    return payload


def _store_path(tmp_path: Path, name: str = "worker-jobs.db") -> Path:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(exist_ok=True)
    return runtime_dir / name


def _process_put_worker_job(path: str, payload: dict[str, object], barrier, errors) -> None:
    try:
        barrier.wait(timeout=5)
        store = WorkerJobStore(Path(path))
        store.put(WorkerJobRecord.model_validate(payload))
    except BaseException as exc:
        errors.put(repr(exc))
        raise


@pytest.mark.parametrize(
    "name",
    ["worker-jobs.db", "worker-jobs.sqlite", "worker-jobs.sqlite3"],
)
def test_worker_job_store_preserves_explicit_sqlite_paths(tmp_path: Path, name: str) -> None:
    store = WorkerJobStore(_store_path(tmp_path, name))

    assert store.path == tmp_path / "runtime" / name


def test_worker_job_store_uses_expected_sqlite_schema(tmp_path: Path) -> None:
    store = WorkerJobStore(tmp_path / "runtime" / "worker-jobs.db")
    store.ensure_schema()

    with sqlite3.connect(store.path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        columns = [row[1] for row in connection.execute("PRAGMA table_info(worker_jobs)")]
        indexes = {
            row[1]: tuple(
                info_row[2] for info_row in connection.execute(f"PRAGMA index_info({row[1]!r})")
            )
            for row in connection.execute("PRAGMA index_list(worker_jobs)")
            if not str(row[1]).startswith("sqlite_autoindex")
        }

    assert "worker_jobs" in tables
    assert columns == [
        "job_id",
        "session_id",
        "container_name",
        "status",
        "cleanup_status",
        "started_at",
        "finished_at",
        "payload_json",
    ]
    assert set(indexes.values()) == {
        ("session_id", "status", "started_at", "job_id"),
        ("session_id", "started_at", "job_id"),
        ("status", "started_at", "job_id"),
        ("cleanup_status", "started_at", "job_id"),
        ("status", "cleanup_status"),
    }


def test_worker_job_store_put_get_delete_and_read_all_round_trip(tmp_path: Path) -> None:
    store = WorkerJobStore(tmp_path / "runtime" / "worker-jobs.db")
    store.ensure_schema()
    record = _record("job-1", started_at=_STARTED_AT)

    assert store.get("") is None
    assert store.get("   ") is None
    assert store.get("missing") is None
    assert store.read_all() == {}

    store.put(record)

    assert store.get(record.job_id) == record
    assert store.read_all() == {record.job_id: record}

    store.delete(record.job_id)

    assert store.get(record.job_id) is None
    assert store.read_all() == {}

    store.delete(record.job_id)
    assert store.get(record.job_id) is None


def test_worker_job_store_orders_reads_by_started_at_and_job_id(tmp_path: Path) -> None:
    store = WorkerJobStore(tmp_path / "runtime" / "worker-jobs.db")
    store.ensure_schema()
    first = _record("job-b", started_at=_STARTED_AT)
    second = _record("job-a", started_at=_STARTED_AT)
    third = _record("job-c", started_at=None)

    for record in [first, second, third]:
        store.put(record)

    assert list(store.read_all()) == ["job-a", "job-b", "job-c"]
    assert [record.job_id for record in store.list_all_ordered()] == [
        "job-a",
        "job-b",
        "job-c",
    ]


def test_worker_job_store_lists_jobs_by_session_and_filters_active_jobs(tmp_path: Path) -> None:
    store = WorkerJobStore(tmp_path / "runtime" / "worker-jobs.db")
    store.ensure_schema()

    for record in [
        _record("job-created", status="created", started_at=_STARTED_AT),
        _record("job-starting", status="starting", started_at=_STARTED_AT),
        _record("job-running", status="running", started_at=_STARTED_AT),
        _record("job-stopped", status="stopped", started_at=_STARTED_AT),
        _record("job-failed", status="failed", started_at=_STARTED_AT),
        _record("job-exited", status="exited", started_at=_STARTED_AT),
        _record("job-cleanup-failed", status="cleanup_failed", started_at=_STARTED_AT),
        _record("job-removed", status="removed", started_at=_STARTED_AT),
        _record(
            "job-other-session",
            session_id="session-b",
            status="running",
            started_at=_STARTED_AT,
        ),
    ]:
        store.put(record)

    assert [record.job_id for record in store.list_by_session("session-a")] == [
        "job-cleanup-failed",
        "job-created",
        "job-exited",
        "job-failed",
        "job-removed",
        "job-running",
        "job-starting",
        "job-stopped",
    ]
    assert [record.job_id for record in store.list_active_by_session("session-a")] == [
        "job-created",
        "job-running",
        "job-starting",
    ]


def test_worker_job_store_orders_jobs_by_started_at_then_job_id_within_a_session(
    tmp_path: Path,
) -> None:
    store = WorkerJobStore(tmp_path / "runtime" / "worker-jobs.db")
    store.ensure_schema()

    for record in [
        _record("job-late", started_at=_LATER_STARTED_AT),
        _record("job-a", started_at=_STARTED_AT),
        _record("job-b", started_at=_STARTED_AT),
        _record("job-none", started_at=None),
        _record("job-other-session", session_id="session-b", started_at=_STARTED_AT),
    ]:
        store.put(record)

    assert [record.job_id for record in store.list_by_session("session-a")] == [
        "job-a",
        "job-b",
        "job-late",
        "job-none",
    ]


def test_worker_job_store_lists_created_jobs_as_active(tmp_path: Path) -> None:
    store = WorkerJobStore(tmp_path / "runtime" / "worker-jobs.db")
    store.ensure_schema()

    for record in [
        _record("job-created", status="created", started_at=_STARTED_AT),
        _record("job-starting", status="starting", started_at=_STARTED_AT),
        _record("job-running", status="running", started_at=_STARTED_AT),
        _record("job-stopped", status="stopped", started_at=_STARTED_AT),
    ]:
        store.put(record)

    assert [record.job_id for record in store.list_active_by_session("session-a")] == [
        "job-created",
        "job-running",
        "job-starting",
    ]


def test_worker_job_store_ignores_retired_json_input(
    tmp_path: Path,
) -> None:
    legacy_path = _store_path(tmp_path, "worker-jobs.json")
    legacy_record = _record(
        "legacy-job",
        session_id="legacy-session",
        status="exited",
        started_at=_STARTED_AT,
        finished_at=_FINISHED_AT,
    )
    legacy_payload = {"legacy-job": _legacy_payload(legacy_record)}
    legacy_path.write_text(json.dumps(legacy_payload), encoding="utf-8")
    store = WorkerJobStore(_store_path(tmp_path))

    assert store.get(legacy_record.job_id) is None
    assert store.read_all() == {}


def test_worker_job_store_preserves_concurrent_puts_from_separate_processes(tmp_path: Path) -> None:
    store_path = tmp_path / "runtime" / "worker-jobs.db"
    store = WorkerJobStore(store_path)
    store.ensure_schema()

    context = get_context("spawn")
    start = context.Barrier(3)
    errors = context.Queue()
    records = [
        _record("job-1", session_id="session-a", started_at=_STARTED_AT),
        _record("job-2", session_id="session-b", started_at=_STARTED_AT),
    ]
    processes = [
        context.Process(
            target=_process_put_worker_job,
            args=(str(store_path), record.model_dump(mode="json"), start, errors),
        )
        for record in records
    ]

    for process in processes:
        process.start()
    start.wait(timeout=5)
    for process in processes:
        process.join(timeout=5)

    for process in processes:
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)
        assert process.exitcode == 0
    assert errors.empty()

    reloaded = WorkerJobStore(store_path)

    assert set(reloaded.read_all()) == {"job-1", "job-2"}
    assert [record.job_id for record in reloaded.list_all_ordered()] == ["job-1", "job-2"]
