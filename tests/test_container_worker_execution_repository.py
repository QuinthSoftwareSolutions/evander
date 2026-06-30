from __future__ import annotations

import concurrent.futures
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from evander.execution.container_worker_repository import (
    ContainerWorkerExecutionRecord,
    ContainerWorkerExecutionRepository,
)


def _repo(tmp_path: Path) -> ContainerWorkerExecutionRepository:
    return ContainerWorkerExecutionRepository(tmp_path / "container-worker-executions.json")


def _record(
    execution_id: str,
    *,
    session_id: str,
    worker_job_id: str | None = None,
    target_name: str | None = None,
    directory_path: str = "root",
    last_status: str = "running",
    cleanup_status: str = "unknown",
    created_at: datetime,
    updated_at: datetime | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    cleanup_attempted_at: datetime | None = None,
    cleanup_completed_at: datetime | None = None,
    cleanup_error: str | None = None,
    metadata: dict[str, object] | None = None,
) -> ContainerWorkerExecutionRecord:
    return ContainerWorkerExecutionRecord(
        execution_id=execution_id,
        session_id=session_id,
        worker_job_id=worker_job_id or f"job-{execution_id}",
        worker_url="http://worker.test",
        target_name=target_name or f"target-{execution_id}",
        created_at=created_at,
        updated_at=updated_at or created_at,
        started_at=started_at,
        finished_at=finished_at,
        last_status=last_status,
        cleanup_status=cleanup_status,
        cleanup_attempted_at=cleanup_attempted_at,
        cleanup_completed_at=cleanup_completed_at,
        cleanup_error=cleanup_error,
        directory_path=directory_path,
        metadata=metadata or {},
    )


def _utc_at(minutes: int) -> datetime:
    return datetime(2026, 5, 24, 12, minutes, tzinfo=UTC)


def test_repository_put_get_delete_and_read_all_round_trip(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    created_at = _utc_at(0)
    started_at = _utc_at(1)
    finished_at = _utc_at(2)
    cleanup_attempted_at = _utc_at(3)
    cleanup_completed_at = _utc_at(4)

    repo.put(
        _record(
            "exec-1",
            session_id="session-1",
            worker_job_id="worker-1",
            target_name="target-1",
            directory_path="root/team",
            created_at=created_at,
            started_at=started_at,
            finished_at=finished_at,
            cleanup_status="failed",
            cleanup_attempted_at=cleanup_attempted_at,
            cleanup_completed_at=cleanup_completed_at,
            cleanup_error="cleanup failed",
            metadata={
                "purpose": "round-trip",
                "authorization": "Bearer should-not-leak",
                "nested": {"api_key": "hidden"},
            },
        )
    )

    stored = repo.get("exec-1")
    assert stored is not None
    assert stored.execution_id == "exec-1"
    assert stored.session_id == "session-1"
    assert stored.worker_job_id == "worker-1"
    assert stored.target_name == "target-1"
    assert stored.created_at == created_at
    assert stored.started_at == started_at
    assert stored.finished_at == finished_at
    assert stored.cleanup_status == "failed"
    assert stored.cleanup_attempted_at == cleanup_attempted_at
    assert stored.cleanup_completed_at == cleanup_completed_at
    assert stored.cleanup_error == "cleanup failed"
    assert stored.metadata["purpose"] == "round-trip"
    assert stored.metadata["authorization"] == "[redacted]"
    assert stored.metadata["nested"]["api_key"] == "[redacted]"
    assert stored.updated_at >= stored.created_at
    assert repo.get(None) is None
    assert repo.get("   ") is None

    all_records = repo.read_all()
    assert list(all_records) == ["exec-1"]
    assert all_records["exec-1"].created_at == created_at
    assert all_records["exec-1"].finished_at == finished_at
    assert all_records["exec-1"].cleanup_completed_at == cleanup_completed_at
    assert all_records["exec-1"].metadata["authorization"] == "[redacted]"

    repo.delete("exec-1")
    repo.delete("exec-1")
    assert repo.get("exec-1") is None
    assert repo.read_all() == {}


def test_repository_json_constructor_uses_sibling_db_and_imports_legacy_json_once(
    tmp_path: Path,
) -> None:
    legacy_json_path = tmp_path / "runtime" / "container-worker-executions.json"
    legacy_json_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_payload = {
        "exec-1": {
            "execution_id": "exec-1",
            "session_id": "session-1",
            "worker_job_id": "worker-1",
            "worker_url": "http://worker.test",
            "target_name": "target-1",
            "created_at": "2026-05-24T12:00:00Z",
            "updated_at": "2026-05-24T12:00:00Z",
            "started_at": "2026-05-24T12:01:00Z",
            "finished_at": None,
            "last_status": "running",
            "cleanup_status": "unknown",
            "cleanup_attempted_at": None,
            "cleanup_completed_at": None,
            "cleanup_error": None,
            "directory_path": "root/team",
            "metadata": {"purpose": "legacy"},
        }
    }
    legacy_json_path.write_text(json.dumps(legacy_payload), encoding="utf-8")
    original_text = legacy_json_path.read_text(encoding="utf-8")

    repo = ContainerWorkerExecutionRepository(legacy_json_path)

    assert repo.path == legacy_json_path.with_name("container-worker-executions.db")
    assert repo.legacy_json_path == legacy_json_path

    imported = repo.read_all()
    assert list(imported) == ["exec-1"]
    assert imported["exec-1"].worker_job_id == "worker-1"
    assert legacy_json_path.read_text(encoding="utf-8") == original_text

    with sqlite3.connect(repo.path) as connection:
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        index_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }

    assert "container_worker_executions" in table_names
    assert {
        "idx_container_worker_executions_session_status_updated",
        "idx_container_worker_executions_session_created_execution",
        "idx_container_worker_executions_worker_job_id",
        "idx_container_worker_executions_target_name",
        "idx_container_worker_executions_directory_created",
    }.issubset(index_names)

    legacy_payload["exec-2"] = {
        "execution_id": "exec-2",
        "session_id": "session-2",
        "worker_job_id": "worker-2",
        "worker_url": "http://worker.test",
        "target_name": "target-2",
        "created_at": "2026-05-24T12:05:00Z",
        "updated_at": "2026-05-24T12:05:00Z",
        "started_at": None,
        "finished_at": None,
        "last_status": "running",
        "cleanup_status": "unknown",
        "cleanup_attempted_at": None,
        "cleanup_completed_at": None,
        "cleanup_error": None,
        "directory_path": "root/other",
        "metadata": {"purpose": "should-not-import"},
    }
    legacy_json_path.write_text(json.dumps(legacy_payload), encoding="utf-8")

    second_repo = ContainerWorkerExecutionRepository(legacy_json_path)
    assert list(second_repo.read_all()) == ["exec-1"]


def test_repository_resolve_and_targeted_listings_are_ordered(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    repo.put(
        _record(
            "exec-b",
            session_id="session-shared",
            worker_job_id="job-b",
            target_name="target-b",
            directory_path="root/team",
            last_status="running",
            created_at=_utc_at(0),
            metadata={"kind": "b"},
        )
    )
    repo.put(
        _record(
            "exec-a",
            session_id="session-shared",
            worker_job_id="job-a",
            target_name="target-a",
            directory_path="root/team",
            last_status="running",
            created_at=_utc_at(0),
            metadata={"kind": "a"},
        )
    )
    repo.put(
        _record(
            "exec-d",
            session_id="session-shared",
            worker_job_id="job-d",
            target_name="target-d",
            directory_path="root/team",
            last_status="stopped",
            created_at=_utc_at(5),
            metadata={"kind": "d"},
        )
    )
    repo.put(
        _record(
            "exec-c",
            session_id="session-unique",
            worker_job_id="job-c",
            target_name="target-c",
            directory_path="root/other",
            last_status="running",
            created_at=_utc_at(10),
            metadata={"kind": "c"},
        )
    )

    assert repo.resolve("exec-a").execution_id == "exec-a"
    assert repo.resolve("job-b").execution_id == "exec-b"
    assert repo.resolve("target-c").execution_id == "exec-c"
    assert repo.resolve("session-unique").execution_id == "exec-c"

    with pytest.raises(
        ValueError,
        match="Ambiguous container worker session target session-shared: exec-a, exec-b, exec-d",
    ):
        repo.resolve("session-shared")
    with pytest.raises(
        ValueError, match="Unknown container worker execution target: missing-target"
    ):
        repo.resolve("missing-target")
    with pytest.raises(ValueError, match="container worker execution target is required"):
        repo.resolve("   ")

    assert [record.execution_id for record in repo.list_all_ordered()] == [
        "exec-a",
        "exec-b",
        "exec-d",
        "exec-c",
    ]
    assert list(repo.read_all()) == ["exec-a", "exec-b", "exec-d", "exec-c"]
    assert [record.execution_id for record in repo.list_by_session("session-shared")] == [
        "exec-a",
        "exec-b",
        "exec-d",
    ]
    assert [record.execution_id for record in repo.list_active_by_session("session-shared", ["running"])] == [
        "exec-a",
        "exec-b",
    ]
    assert repo.list_active_by_session("", ["running"]) == []
    assert repo.list_active_by_session("session-shared", []) == []
    assert [record.execution_id for record in repo.list_by_directory(None)] == [
        "exec-a",
        "exec-b",
        "exec-d",
        "exec-c",
    ]
    assert [record.execution_id for record in repo.list_by_directory("root/team")] == [
        "exec-a",
        "exec-b",
        "exec-d",
    ]


def test_repository_concurrent_puts_from_separate_instances_preserve_all_rows(
    tmp_path: Path,
) -> None:
    execution_ids = [f"exec-{index}" for index in range(8)]

    def _write(execution_id: str) -> None:
        repo = _repo(tmp_path)
        repo.put(
            _record(
                execution_id,
                session_id="session-shared",
                worker_job_id=f"worker-{execution_id}",
                target_name=f"target-{execution_id}",
                directory_path="root/concurrent",
                created_at=_utc_at(int(execution_id.split("-")[1])),
                metadata={"execution_id": execution_id},
            )
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(_write, execution_ids))

    shared_repo = _repo(tmp_path)
    stored = shared_repo.read_all()

    assert set(stored) == set(execution_ids)
    assert [record.execution_id for record in shared_repo.list_all_ordered()] == execution_ids
