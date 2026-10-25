"""Cancellation must fence late admission, not interpret an absent job as cleanup."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
import subprocess
import threading

import pytest

from evander.worker.models import WorkerJobRequest
from evander.worker.service import WorkerDaemon
from test_worker_daemon import FakeContainerRuntime, _auth, _client


def job(session_id="session-1", job_id="job-1"):
    return WorkerJobRequest(
        session_id=session_id, job_id=job_id, image="runner", command=["true"],
        metadata={"idempotent_start": True},
    )


def test_missing_job_cancellation_survives_restart_and_blocks_delayed_launch(tmp_path):
    runtime = FakeContainerRuntime()
    with _client(tmp_path, runtime) as client:
        assert client.post("/v1/jobs/job-1/cancel", json={"session_id": "session-1"}).status_code == 401
        result = client.post("/v1/jobs/job-1/cancel", headers=_auth(), json={"session_id": "session-1"})
        assert result.status_code == 200
        assert result.json() == {
            "job_id": "job-1", "session_id": "session-1",
            "launch_cancelled": True, "cleanup_confirmed": True,
        }
        # A cancellation fence is not a fabricated admitted job.
        assert client.get("/v1/jobs/job-1", headers=_auth()).status_code == 404
        wrong_owner = client.post("/v1/jobs/job-1/cancel", headers=_auth(), json={"session_id": "other"})
        assert wrong_owner.status_code == 400
    with _client(tmp_path, runtime) as restarted:
        for session in ("session-1", "other"):
            response = restarted.post("/v1/jobs", headers=_auth(), json=job(session).model_dump())
            assert response.status_code == 400
            assert "cancelled" in response.json()["error"]
        assert restarted.get("/v1/jobs/job-1", headers=_auth()).status_code == 404
    assert runtime.started == runtime.removed == []


def test_cancel_checks_owner_preserves_artifacts_and_is_idempotent(tmp_path):
    runtime = FakeContainerRuntime()
    with _client(tmp_path, runtime) as client:
        first = client.post("/v1/jobs", headers=_auth(), json=job().model_dump()).json()
        client.post("/v1/jobs", headers=_auth(), json=job(job_id="other-job").model_dump()).raise_for_status()
        from pathlib import Path

        proof = Path(first["artifacts_path"]) / "proof.txt"
        proof.write_text("retained native evidence")
        assert client.post("/v1/jobs/job-1/cancel", headers=_auth(), json={"session_id": "wrong"}).status_code == 400
        assert runtime.removed == []
        for _ in range(2):
            response = client.post("/v1/jobs/job-1/cancel", headers=_auth(), json={"session_id": "session-1"})
            assert response.status_code == 200 and response.json()["cleanup_confirmed"]
        assert client.get("/v1/jobs/other-job", headers=_auth()).json()["status"] == "running"
        assert proof.read_text() == "retained native evidence"
        assert runtime.removed == ["job-1"]
        assert client.post("/v1/jobs", headers=_auth(), json=job().model_dump()).status_code == 400


def test_failed_cleanup_retains_fence_until_retry_confirms_removal(tmp_path, monkeypatch):
    runtime = FakeContainerRuntime()
    remove = runtime.remove_job

    def fail(record):
        raise subprocess.CalledProcessError(1, ["docker", "rm"], stderr="unavailable")

    with _client(tmp_path, runtime) as client:
        client.post("/v1/jobs", headers=_auth(), json=job().model_dump()).raise_for_status()
        monkeypatch.setattr(runtime, "remove_job", fail)
        response = client.post("/v1/jobs/job-1/cancel", headers=_auth(), json={"session_id": "session-1"})
        assert response.status_code == 502
        assert client.get("/v1/jobs/job-1", headers=_auth()).json()["cleanup_status"] == "cleanup_failed"
        assert client.post("/v1/jobs", headers=_auth(), json=job().model_dump()).status_code == 400
        monkeypatch.setattr(runtime, "remove_job", remove)
        response = client.post("/v1/jobs/job-1/cancel", headers=_auth(), json={"session_id": "session-1"})
        assert response.status_code == 200 and response.json()["cleanup_confirmed"]
    assert len(runtime.started) == 1 and runtime.removed == ["job-1"]


def test_cancel_waits_for_inflight_admission_across_daemon_instances(tmp_path):
    entered, release, cancelling = threading.Event(), threading.Event(), threading.Event()

    class SlowRuntime(FakeContainerRuntime):
        def start_job(self, *args):
            entered.set()
            assert release.wait(5)
            return super().start_job(*args)

    runtime = SlowRuntime()

    def daemon():
        return WorkerDaemon(
            runtime_dir=tmp_path / "runtime", worker_root=tmp_path / "worker",
            container_runtime=runtime, token="test-token",
        )

    first, second = daemon(), daemon()

    def cancel():
        cancelling.set()
        return second.cancel_job("job-1", "session-1")

    with ThreadPoolExecutor(max_workers=2) as pool:
        launching = pool.submit(first.start_job, job())
        try:
            assert entered.wait(5)
            cancelled = pool.submit(cancel)
            assert cancelling.wait(5)
            with pytest.raises(TimeoutError):
                cancelled.result(timeout=0.1)
        finally:
            release.set()
        launching.result(timeout=5)
        assert cancelled.result(timeout=5)["cleanup_confirmed"] is True
    assert runtime.removed == ["job-1"]
    assert first.store.get("job-1").cleanup_status == "cleanup_succeeded"
    # Deleting job history cannot remove the admission fence.
    second.store.delete("job-1")
    with pytest.raises(ValueError, match="cancelled"):
        first.start_job(job())
    assert len(runtime.started) == 1
