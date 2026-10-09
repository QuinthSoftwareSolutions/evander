from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from evander.worker.models import WorkerJobRecord, WorkerSessionPaths
from evander.worker.service import WorkerDaemon
from evander.worker.store import WorkerJobStore
from read_scaling_probe import ReadProbe

NOW = datetime(2026, 10, 9, 10, tzinfo=UTC)


def job(root, job_id, *, status="running", metadata=None, started_at=NOW, cleanup="unknown"):
    paths = WorkerSessionPaths.resolve(root / "worker", "session-one")
    return WorkerJobRecord(
        job_id=job_id,
        session_id="session-one",
        image="runner:latest",
        command=["true"],
        container_name=f"container-{job_id}",
        status=status,
        created_at=NOW,
        started_at=started_at,
        agent_home_path=str(paths.agent_home),
        repo_root_path=str(paths.repo_root),
        artifacts_path=str(paths.artifacts),
        logs_path=str(paths.logs),
        metadata=metadata or {},
        cleanup_status=cleanup,
    )


def seed(root, history=500):
    store = WorkerJobStore(root / "runtime" / "worker-jobs.db")
    with store._connect() as db:
        db.executemany(
            "INSERT INTO worker_jobs VALUES (?,?,?,?,?,?,?,?)",
            [store._record_row(job(root, f"old-{i}", status="exited")) for i in range(history)],
        )
    return store


def service(store):
    daemon = WorkerDaemon.__new__(WorkerDaemon)
    daemon.store = store
    return daemon


def test_worker_telemetry_aggregates_statuses_without_historical_payloads(tmp_path: Path):
    store = seed(tmp_path)
    for record in (
        job(tmp_path, "running", cleanup="cleanup_failed"),
        job(tmp_path, "failed", status="failed"),
        job(tmp_path, "double-failure", status="cleanup_failed", cleanup="cleanup_failed"),
        job(tmp_path, "removed-failure", status="removed", cleanup="cleanup_failed"),
        job(tmp_path, "queued", status="created", started_at=None),
    ):
        store.put(record)
    with ReadProbe() as probe:
        telemetry = service(store)._job_telemetry()

    assert telemetry.total_jobs == 505
    assert telemetry.counts_by_status == {
        "exited": 500,
        "running": 1,
        "failed": 1,
        "cleanup_failed": 1,
        "removed": 1,
        "created": 1,
    }
    assert telemetry.running_job_count == telemetry.failed_start_count == 1
    assert telemetry.cleanup_failure_count == 3
    assert telemetry.health.value == "warning"
    assert probe.payload_rows["worker_jobs"] == 0
    assert probe.rows["worker_jobs"] <= 6


def test_subscription_restart_reads_only_inflight_jobs_and_fails_them_closed(tmp_path: Path):
    store = seed(tmp_path)
    subscription = {"codex_auth_mode": "chatgpt_subscription"}
    for status in ("created", "preparing", "starting", "running", "stopped", "removed"):
        store.put(job(tmp_path, f"sub-{status}", status=status, metadata=subscription))
    store.put(job(tmp_path, "metered"))
    with ReadProbe() as probe:
        daemon = WorkerDaemon(
            runtime_dir=tmp_path / "runtime",
            worker_root=tmp_path / "worker",
            container_runtime=SimpleNamespace(),
            token="synthetic",
        )
    try:
        for status in ("created", "preparing", "starting", "running"):
            record = daemon.store.get(f"sub-{status}")
            assert record.status == "failed"
            assert record.metadata["worker_terminal_kind"] == "infrastructure"
            marker = Path(record.logs_path) / "codex-turns" / f"sub-{status}.exit"
            assert marker.read_text() == "1"
        assert daemon.store.get("sub-stopped").status == "stopped"
        assert daemon.store.get("sub-removed").status == "removed"
        assert daemon.store.get("metered").status == "running"
        assert probe.payload_rows["worker_jobs"] == 5
    finally:
        daemon.close()


def test_profile_list_counts_one_active_snapshot_and_observes_next_read_mutations(tmp_path: Path):
    store = seed(tmp_path)
    for job_id, profile in (("default", None), ("a-one", "a"), ("a-two", "a"), ("b-one", "b")):
        store.put(
            job(
                tmp_path,
                job_id,
                metadata={
                    "codex_auth_mode": " CHATGPT_SUBSCRIPTION ",
                    "subscription_profile_id": profile,
                },
            )
        )
    store.put(job(tmp_path, "metered", metadata={"subscription_profile_id": "a"}))
    store.put(
        job(
            tmp_path,
            "finished",
            status="failed",
            metadata={"codex_auth_mode": "chatgpt_subscription", "subscription_profile_id": "a"},
        )
    )
    daemon = service(store)
    daemon._codex_auth_registry = SimpleNamespace(
        list_profiles=lambda: {
            "profiles": [{"profile_id": name} for name in ("default", "a", "b", "idle")]
        }
    )
    with ReadProbe() as probe:
        profiles = daemon.list_codex_auth_profiles()["profiles"]
    assert {p["profile_id"]: p["active_job_count"] for p in profiles} == {
        "default": 1,
        "a": 2,
        "b": 1,
        "idle": 0,
    }
    assert probe.selects["worker_jobs"] == 1
    assert probe.payload_rows["worker_jobs"] == 5

    record = store.get("a-two")
    record.status = "exited"
    store.put(record)
    assert daemon._active_subscription_jobs_for_profile("a") == 1
    assert daemon._active_subscription_jobs_for_profile("idle") == 0


def test_inflight_lookup_preserves_order_and_detached_records(tmp_path: Path):
    store = seed(tmp_path, history=0)
    for record in (
        job(tmp_path, "null", status="created", started_at=None),
        job(tmp_path, "b", status="starting"),
        job(tmp_path, "a", status="preparing"),
        job(tmp_path, "early", started_at=NOW - timedelta(hours=1)),
        job(tmp_path, "finished", status="exited"),
    ):
        store.put(record)
    records = store.list_inflight()
    assert [record.job_id for record in records] == ["early", "a", "b", "null"]
    records[0].status = "failed"
    assert store.get("early").status == "running"


@pytest.mark.parametrize("error", [FileNotFoundError, PermissionError, ValueError])
def test_telemetry_preserves_store_failure_status(error):
    def fail():
        raise error("synthetic failure")

    telemetry = service(SimpleNamespace(status_counts=fail))._job_telemetry()
    assert telemetry.health.value == "unknown"
    assert telemetry.error_count == 1


def test_empty_profile_list_does_not_query_jobs(tmp_path: Path):
    daemon = service(seed(tmp_path))
    daemon._codex_auth_registry = SimpleNamespace(list_profiles=lambda: {"profiles": []})
    with ReadProbe() as probe:
        assert daemon.list_codex_auth_profiles() == {"profiles": []}
    assert not probe.selects
