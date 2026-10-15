from types import SimpleNamespace

import httpx
import pytest

from evander.codex_auth import CodexAuthMode
from evander.execution.container_worker_client import ContainerWorkerClientError
from evander.security_scans import (
    SecurityScanHandle,
    SecurityScanRequest,
    SecurityScanRetryableError,
)
from evander_codex_security_addon import provider as implementation


@pytest.fixture
def launching(monkeypatch):
    config = SimpleNamespace(model_provider_mode="openai_public")
    session = SimpleNamespace(directory_path="root/project", metadata={})
    prepared = []
    backend = SimpleNamespace(
        prepare=lambda spec: prepared.append(spec) or spec,
        start=lambda spec: SimpleNamespace(execution_id=spec.target_name),
    )
    provider = implementation.CodexSecurityScanProvider()
    monkeypatch.setattr(provider, "_config", lambda *_: (config, session))
    monkeypatch.setattr(implementation, "container_backend", lambda *_: backend)
    monkeypatch.setattr(
        implementation, "execution_backend_for_session", lambda *a, **k: "container_worker_vm"
    )
    monkeypatch.setattr(implementation, "container_worker_image_for_session", lambda *_: "runner")
    monkeypatch.setattr(
        implementation, "container_worker_resource_limits_for_session", lambda *_: {}
    )
    monkeypatch.setattr(
        implementation, "effective_codex_auth_mode", lambda **_: CodexAuthMode.CHATGPT_SUBSCRIPTION
    )
    monkeypatch.setattr(implementation, "effective_subscription_profile_id", lambda **_: "review")
    return provider, config, session, backend, prepared


def launch(provider):
    return provider.start(
        scan_id="1" * 32,
        request=SecurityScanRequest(session_id="session-1", repo_path="/home/agent/repo"),
        runtime=None,
    )


@pytest.mark.parametrize("route", ["openai_public", "azure", "local"])
def test_only_public_openai_route_uses_selected_subscription(launching, route):
    provider, config, _, _, prepared = launching
    config.model_provider_mode = route
    launch(provider)
    runtime = prepared[0].codex_runtime
    if route == "openai_public":
        assert runtime.subscription_profile_id == "review"
    else:
        assert runtime is None


def test_session_route_takes_precedence_over_directory_default(launching):
    provider, _, session, _, prepared = launching
    session.metadata["model_provider_mode"] = "azure"
    launch(provider)
    assert prepared[0].codex_runtime is None


def test_lost_worker_response_reuses_the_same_job_identity(launching):
    provider, _, _, backend, prepared = launching

    def unavailable(spec):
        try:
            raise httpx.ReadTimeout("lost response")
        except httpx.ReadTimeout as cause:
            raise ContainerWorkerClientError("transport failed") from cause

    backend.start = unavailable
    with pytest.raises(SecurityScanRetryableError):
        launch(provider)
    backend.start = lambda spec: SimpleNamespace(execution_id=spec.target_name)
    recovered = launch(provider)
    assert prepared[0].target_name == prepared[1].target_name == recovered.execution_id
    assert prepared[0].metadata["idempotent_start"] is True


def test_interrupted_worker_preserves_diagnostics_and_destroys_job(launching, monkeypatch):
    from evander.execution import ExecutionStatusKind

    provider, _, _, backend, _ = launching
    commands = []
    destroyed = []
    preserved = []
    monkeypatch.setattr(
        provider,
        "_command",
        lambda runtime, session, args: commands.append(args) or {"status": "running"},
    )
    monkeypatch.setattr(
        provider, "_preserve_result", lambda result, *args: preserved.append(result) or result
    )
    backend.inspect = lambda target: SimpleNamespace(status=ExecutionStatusKind.STOPPED)
    backend.destroy = destroyed.append
    result = provider.poll(
        handle=SecurityScanHandle(
            execution_id="job",
            metadata={
                "backend": "container_worker_vm",
                "session_id": "session-1",
                "scan_id": "1" * 32,
            },
        ),
        runtime=None,
    )
    assert result.status == "failed"
    assert "sealed result" in result.error
    assert preserved == [result]
    assert destroyed == ["job"]
    assert commands == [["status", "1" * 32]]


def test_missing_runtime_in_exited_job_fails_without_waiting_for_timeout(launching, monkeypatch):
    from evander.execution import ExecutionStatusKind

    provider, _, _, backend, _ = launching

    def unavailable(*args):
        raise RuntimeError("Security scan runtime is missing")

    monkeypatch.setattr(provider, "_command", unavailable)
    backend.inspect = lambda target: SimpleNamespace(status=ExecutionStatusKind.FAILED)
    destroyed = []
    backend.destroy = destroyed.append
    result = provider.poll(
        handle=SecurityScanHandle(
            execution_id="job",
            metadata={
                "backend": "container_worker_vm",
                "session_id": "session-1",
                "scan_id": "1" * 32,
            },
        ),
        runtime=None,
    )
    assert result.status == "failed"
    assert result.error == "Security scan runtime is missing"
    assert destroyed == ["job"]


def test_cancel_lost_launch_only_reads_and_stops_existing_job(launching, monkeypatch):
    provider, _, _, backend, _ = launching
    backend.config_for_session = lambda session: SimpleNamespace(
        container_worker_base_url="http://worker.invalid",
        container_worker_token_env="WORKER_TOKEN",
        container_worker_request_timeout_seconds=5,
    )
    calls = []
    client = SimpleNamespace(
        get=lambda job: (
            calls.append(("get", job)) or SimpleNamespace(session_id="session-1", status="running")
        ),
        stop=lambda job: calls.append(("stop", job)),
        delete=lambda job: calls.append(("delete", job)),
    )
    monkeypatch.setattr(implementation, "ContainerWorkerClient", lambda **kwargs: client)
    provider.cancel(
        scan_id="1" * 32,
        request=SecurityScanRequest(
            session_id="session-1",
            repo_path="/home/agent/repo",
        ),
        handle=None,
        runtime=None,
    )
    assert calls == [(action, "scan-" + "1" * 32) for action in ("get", "stop", "delete")]
