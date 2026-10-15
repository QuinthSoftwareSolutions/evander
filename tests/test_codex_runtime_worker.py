from types import SimpleNamespace

import pytest

from evander.codex_auth import CodexRuntimeSpec
from evander.worker.codex_auth_broker import CodexExternalAuthTokens
from evander.worker.models import WorkerJobRequest
from evander.worker.service import WorkerDaemon


class Runtime:
    def __init__(self):
        self.started = []
        self.stopped = []

    def start_job(self, request, record, paths):
        self.started.append(request)
        return "container-" + record.job_id

    def stop_job(self, record):
        self.stopped.append(record.job_id)

    def codex_runtime_app_server_command(self, *args):
        return ["native-codex", "app-server", "--stdio"]


class Bridge:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.closed = False

    def start(self):
        pass

    def close(self):
        self.closed = True


@pytest.fixture
def worker(tmp_path, monkeypatch):
    monkeypatch.setattr("evander.worker.service.CodexRuntimeBridge", Bridge)
    tokens = CodexExternalAuthTokens("only-native-process-receives-this", "account", "plan")
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
    assert "only-native-process" not in record.model_dump_json()
    assert "only-native-process" not in runtime.started[0].model_dump_json()
    bridge = instance._codex_runtime_bridges[record.job_id]
    assert bridge.kwargs["tokens"].access_token == "only-native-process-receives-this"


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
