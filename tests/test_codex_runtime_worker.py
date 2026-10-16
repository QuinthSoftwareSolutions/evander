from types import SimpleNamespace
import json
from pathlib import Path
import shutil

import httpx
import pytest

from evander.codex_auth import CodexRuntimeSpec
from evander.worker.codex_auth_broker import CodexExternalAuthTokens
from evander.worker.models import WorkerJobRequest
from evander.worker.service import WorkerDaemon


class Runtime:
    def __init__(self):
        self.started = []
        self.stopped = []
        self.removed = []

    def start_job(self, request, record, paths):
        self.started.append(request)
        return "container-" + record.job_id

    def stop_job(self, record):
        self.stopped.append(record.job_id)

    def remove_job(self, record):
        self.removed.append(record.job_id)

    def codex_runtime_app_server_command(self, *args):
        return ["native-codex", "app-server", "--stdio"]

    def codex_runtime_http_client(self):
        return httpx.Client(
            transport=httpx.MockTransport(lambda request: pytest.fail("Unexpected upstream"))
        )


class Bridge:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.closed = False

    def start(self):
        pass

    def close(self):
        self.closed = True
        return True

    def wait_closed(self, *, timeout_seconds=0):
        return self.closed


@pytest.fixture
def worker(tmp_path, monkeypatch):
    monkeypatch.setattr("evander.worker.service.CodexRuntimeBridge", Bridge)
    tokens = CodexExternalAuthTokens("worker-only-subscription-token", "account", "plan")
    selected = []
    broker = SimpleNamespace(external_tokens=lambda: tokens)
    registry = SimpleNamespace(
        preflight=lambda profile: selected.append(profile),
        broker=lambda profile: broker,
        refresh_tokens=lambda profile, **kwargs: tokens,
        close=lambda: None,
    )
    runtime = Runtime()
    instance = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker",
        container_runtime=runtime,
        codex_auth_registry=registry,
        codex_runtime_profiles={"scan": {"command_prefix": ["scanner"]}},
    )
    yield instance, runtime, selected
    instance.close()


def job(**kwargs):
    return WorkerJobRequest(
        session_id="session-1",
        job_id="scan-1",
        image="runner",
        command=["scanner", "run"],
        codex_runtime=CodexRuntimeSpec(profile_id="scan", subscription_profile_id="review"),
        metadata={"idempotent_start": True},
        **kwargs,
    )


def test_runtime_uses_selected_profile_and_keeps_command_without_transferring_tokens(worker):
    instance, runtime, selected = worker
    record = instance.start_job(job())
    again = instance.start_job(job())
    assert record.job_id == again.job_id
    assert len(runtime.started) == 1
    assert selected == ["review"]
    assert runtime.started[0].command == ["scanner", "run"]
    assert "worker-only-subscription-token" not in record.model_dump_json()
    assert "worker-only-subscription-token" not in runtime.started[0].model_dump_json()
    bridge = instance._codex_runtime_bridges[record.job_id]
    assert bridge.kwargs["tokens"].access_token == "worker-only-subscription-token"


def test_runtime_rejects_unregistered_profiles_and_wrong_executables(worker):
    instance, runtime, _ = worker
    invalid = job().model_copy(update={"codex_runtime": CodexRuntimeSpec(profile_id="other")})
    with pytest.raises(ValueError, match="not installed"):
        instance.start_job(invalid)
    wrong = job().model_copy(update={"command": ["arbitrary-program"]})
    with pytest.raises(ValueError, match="does not match"):
        instance.start_job(wrong)
    assert runtime.started == []


def test_runtime_identity_cannot_be_reused_for_different_work(worker):
    instance, runtime, _ = worker
    instance.start_job(job())
    with pytest.raises(ValueError, match="different inputs"):
        instance.start_job(job().model_copy(update={"command": ["scanner", "different"]}))
    assert len(runtime.started) == 1


def test_shutdown_releases_runtime_broker(worker):
    instance, _, _ = worker
    record = instance.start_job(job())
    bridge = instance._codex_runtime_bridges[record.job_id]
    instance._shutdown_codex_subscription_runner(record.job_id)
    assert bridge.closed
    assert record.job_id not in instance._codex_runtime_bridges


def diagnostic_job():
    return job().model_copy(
        update={
            "metadata": {
                "idempotent_start": True,
                "operation_kind": "security_scan",
                "scan_id": "synthetic",
                "private_diagnostic_paths": ["security-scans/synthetic/diagnostics"],
            }
        }
    )


@pytest.mark.parametrize("action", ["stop_job", "remove_job"])
def test_timed_out_broker_finalizes_only_after_its_last_writer(worker, monkeypatch, action):
    instance, _, _ = worker

    class PendingBridge(Bridge):
        drained = False

        def close(self):
            self.closed = True
            return False

        def wait_closed(self, *, timeout_seconds=0):
            return self.drained

        def complete(self):
            capture = self.kwargs["diagnostics"]
            capture.event("native.stderr", {"line": "terminal private error"})
            capture.event("runtime.closed", {"native_exit_code": -15})
            self.drained = True
            self.kwargs["on_drained"]()

    monkeypatch.setattr("evander.worker.service.CodexRuntimeBridge", PendingBridge)
    record = instance.start_job(diagnostic_job())
    bridge = instance._codex_runtime_bridges[record.job_id]
    capture = bridge.kwargs["diagnostics"]
    getattr(instance, action)(record)
    assert not capture.finished
    assert "private_diagnostics_captured_at" not in instance.store.get(record.job_id).metadata
    assert record.job_id in instance._codex_runtime_bridges
    latest = instance.store.get(record.job_id)
    latest.metadata["concurrent_update"] = "preserved"
    instance.store.put(latest)
    bridge.complete()
    assert capture.finished and record.job_id not in instance._codex_runtime_bridges
    latest = instance.store.get(record.job_id)
    assert latest.metadata["private_diagnostics_captured_at"]
    assert latest.metadata["concurrent_update"] == "preserved"
    text = (capture.directory / "events.jsonl").read_text()
    assert "terminal private error" in text and '"native_exit_code": -15' in text
    assert "terminal private error" not in latest.model_dump_json()


def test_terminal_diagnostics_survive_session_cleanup_without_exposing_raw_data(
    worker, monkeypatch
):
    instance, runtime, _ = worker
    record = instance.start_job(diagnostic_job())
    capture = instance._codex_runtime_bridges[record.job_id].kwargs["diagnostics"]
    capture.event("native.stderr", {"text": "private diagnostic"})
    history = Path(record.artifacts_path) / "security-scans/synthetic/diagnostics/rollout.jsonl"
    history.parent.mkdir(parents=True)
    history.write_text("private native history")
    monkeypatch.setattr(
        runtime,
        "inspect_job",
        lambda record: {
            "status": "exited",
            "exit_code": 137,
            "oom_killed": True,
        },
        raising=False,
    )
    result = instance.inspect_job(record)
    shutil.rmtree(instance.worker_root / "sessions" / record.session_id)
    assert (
        capture.directory / "files/security-scans/synthetic/diagnostics/rollout.jsonl"
    ).read_text() == "private native history"
    summary = json.loads((capture.directory / "summary.json").read_text())
    assert summary["outcome"]["runtime"]["oom_killed"] is True
    assert summary["outcome"]["runtime"]["exit_code"] == 137
    assert "private diagnostic" not in result.model_dump_json()
    # Polling an old job must not recreate evidence removed by retention.
    shutil.rmtree(capture.directory)
    instance.inspect_job(result)
    assert not capture.directory.exists()


def test_restart_recovers_native_history_and_previous_events(worker):
    instance, runtime, _ = worker
    record = instance.start_job(diagnostic_job())
    capture = instance._codex_runtime_bridges[record.job_id].kwargs["diagnostics"]
    capture.event("native.notification", {"threadId": "before-restart"})
    history = Path(record.artifacts_path) / "security-scans/synthetic/diagnostics/rollout.jsonl"
    history.parent.mkdir(parents=True)
    history.write_text("before restart")
    instance.close()
    restarted = WorkerDaemon(
        runtime_dir=instance.runtime_dir,
        worker_root=instance.worker_root,
        container_runtime=runtime,
        codex_auth_registry=instance.codex_auth_profiles,
    )
    try:
        recovered = restarted.store.get(record.job_id)
        assert recovered.status == "failed"
        assert "before-restart" in (capture.directory / "events.jsonl").read_text()
        assert (
            capture.directory / "files/security-scans/synthetic/diagnostics/rollout.jsonl"
        ).read_text() == "before restart"
        assert json.loads((capture.directory / "summary.json").read_text())["finished"]
    finally:
        restarted.close()


def test_diagnostics_failure_does_not_block_scan_or_stop(worker, monkeypatch):
    instance, _, _ = worker

    def unavailable(*args, **kwargs):
        raise OSError("private storage unavailable")

    monkeypatch.setattr(instance._private_diagnostics, "begin", unavailable)
    record = instance.start_job(diagnostic_job())
    assert record.status == "running"
    assert instance.stop_job(record).status == "stopped"


def test_restart_captures_terminal_job_when_previous_worker_died_before_copying(worker):
    instance, runtime, _ = worker
    record = instance.start_job(diagnostic_job())
    history = Path(record.artifacts_path) / "security-scans/synthetic/diagnostics/rollout.jsonl"
    history.parent.mkdir(parents=True)
    history.write_text("terminal evidence before restart")
    record.status = "exited"
    instance.store.put(record)
    instance.close()
    restarted = WorkerDaemon(
        runtime_dir=instance.runtime_dir,
        worker_root=instance.worker_root,
        container_runtime=runtime,
        codex_auth_registry=instance.codex_auth_profiles,
    )
    try:
        assert restarted.store.get(record.job_id).metadata["private_diagnostics_captured_at"]
        target = restarted.worker_root / "private-job-diagnostics" / record.job_id
        assert (
            target / "files/security-scans/synthetic/diagnostics/rollout.jsonl"
        ).read_text() == "terminal evidence before restart"
    finally:
        restarted.close()


def test_broker_start_failure_stops_job_and_cannot_relaunch_it(worker, monkeypatch):
    instance, runtime, _ = worker

    def unavailable(self):
        raise OSError("socket unavailable")

    monkeypatch.setattr(Bridge, "start", unavailable)
    with pytest.raises(OSError, match="socket unavailable"):
        instance.start_job(job())
    assert runtime.stopped == ["scan-1"]
    assert instance.store.get("scan-1").status == "failed"
    assert instance._codex_runtime_bridges == {}
    assert instance.start_job(job()).status == "failed"
    assert len(runtime.started) == 1


def test_restart_reconciles_a_runtime_job_whose_container_is_already_gone(worker):
    instance, runtime, _ = worker
    instance.start_job(job())
    instance.close()

    class MissingContainer(Runtime):
        def stop_job(self, record):
            raise FileNotFoundError("container was already removed")

    restarted = WorkerDaemon(
        runtime_dir=instance.runtime_dir,
        worker_root=instance.worker_root,
        container_runtime=MissingContainer(),
        codex_auth_registry=instance.codex_auth_profiles,
        codex_runtime_profiles={},
    )
    try:
        assert restarted.store.get("scan-1").status == "failed"
        assert restarted._active_subscription_job_counts() == {}
        assert len(runtime.started) == 1
    finally:
        restarted.close()
