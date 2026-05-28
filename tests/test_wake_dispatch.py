from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_sandbox.config import GatewayConfig
from agent_sandbox.execution import ExecutionBackendKind, ExecutionStatusKind
from agent_sandbox.messaging.agent_prompt_queue import AgentPromptQueueStore
from agent_sandbox.messaging.wake_dispatch import HostWakeDispatcher
from agent_sandbox.messaging.wake_store import WakeRequestStore
from agent_sandbox.models import SessionManifest, ToolName
from agent_sandbox.repositories.session_manifests import JsonSessionManifestRepository


class _ContainerBackendStub:
    def __init__(
        self,
        *,
        active: bool = False,
        start_error: Exception | None = None,
    ) -> None:
        self.active = active
        self.start_error = start_error
        self.prepared_specs: list[object] = []
        self.started_specs: list[object] = []
        self.inspect_calls: list[str] = []
        self.repository = SimpleNamespace(list_active_by_session=self.list_active_by_session)

    def list_active_by_session(self, session_id: str, active_statuses: set[str]) -> list[object]:
        del active_statuses
        if not self.active:
            return []
        return [
            SimpleNamespace(
                session_id=session_id,
                execution_id=f"execution:{session_id}",
                last_status=ExecutionStatusKind.RUNNING.value,
            )
        ]

    def prepare(self, spec):
        self.prepared_specs.append(spec)
        return spec

    def start(self, spec):
        self.started_specs.append(spec)
        if self.start_error is not None:
            raise self.start_error
        return SimpleNamespace(execution_id=f"execution:{spec.target_name}")

    def inspect(self, execution_id: str):
        self.inspect_calls.append(execution_id)
        return SimpleNamespace(status=ExecutionStatusKind.RUNNING)


def _build_dispatcher(tmp_path: Path) -> tuple[
    HostWakeDispatcher,
    GatewayConfig,
    JsonSessionManifestRepository,
    WakeRequestStore,
]:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        gateway_url="http://gateway.test",
        container_worker_base_url="http://worker.test",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    wake_store = WakeRequestStore(tmp_path / "wake-requests-store.json")
    runtime = SimpleNamespace(
        session_repository=session_repository,
        wake_store=wake_store,
        admin_service=SimpleNamespace(),
    )
    dispatcher = HostWakeDispatcher(config=config, runtime=runtime)
    return dispatcher, config, session_repository, wake_store


def _upsert_container_session(
    session_repository: JsonSessionManifestRepository,
    *,
    session_id: str,
    metadata: dict[str, object] | None = None,
    vm_provider_id: str | None = None,
) -> SessionManifest:
    session = SessionManifest(
        session_id=session_id,
        vm_id=None,
        vm_provider="container_worker_vm",
        vm_provider_id=vm_provider_id,
        directory_path="root/team-a",
        model="gpt-5.4-mini",
        allowed_tools=[ToolName.SCHEDULE_RESUME],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        metadata=metadata or {},
    )
    session_repository.upsert_session(session)
    return session


def test_container_target_name_prefers_metadata_then_provider_then_session_id() -> None:
    session = SessionManifest(
        session_id="session-1",
        vm_id=None,
        vm_provider="container_worker_vm",
        vm_provider_id="provider-target",
        allowed_tools=[ToolName.SCHEDULE_RESUME],
        metadata={"target_name": "metadata-target"},
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    assert HostWakeDispatcher._container_target_name(session=session, session_id="session-1") == (
        "metadata-target"
    )
    assert HostWakeDispatcher._container_target_name(
        session=SessionManifest(
            session_id="session-2",
            vm_id=None,
            vm_provider="container_worker_vm",
            vm_provider_id="provider-target",
            allowed_tools=[ToolName.SCHEDULE_RESUME],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
        session_id="session-2",
    ) == "provider-target"
    assert HostWakeDispatcher._container_target_name(session=None, session_id="session-3") == (
        "session-3"
    )


def test_resume_container_binding_dispatches_idle_session_and_records_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher, config, session_repository, wake_store = _build_dispatcher(tmp_path)
    session = _upsert_container_session(
        session_repository,
        session_id="session-1",
        vm_provider_id="provider-target",
    )
    backend = _ContainerBackendStub(active=False)
    captured: dict[str, object] = {}

    def build_backend(resolved_config, *, admin_service):
        captured["config"] = resolved_config
        captured["admin_service"] = admin_service
        return backend

    monkeypatch.setattr("agent_sandbox.messaging.wake_dispatch.build_execution_backend", build_backend)

    result = dispatcher.resume_container_binding(
        session,
        worker_config=config,
        prompt="Resume work.",
        resume_mode="same_context",
        dispatch_source="scheduled_once",
        request_id="wake-1",
        scheduled_for=datetime(2026, 4, 1, 10, 30, tzinfo=UTC),
        wake_metadata={"schedule_cron": ""},
    )

    assert result is not None
    assert captured["config"].execution_backend == "container_worker_vm"
    assert captured["admin_service"] is not None
    assert len(backend.prepared_specs) == 1
    assert len(backend.started_specs) == 1
    spec = backend.prepared_specs[0]
    assert spec.backend == ExecutionBackendKind.CONTAINER_WORKER_VM
    assert spec.session_id == "session-1"
    assert spec.directory_path == "root/team-a"
    assert spec.target_name.startswith("agent-session-1-")
    assert "codex exec resume --last" in str(spec.command)
    assert "--model gpt-5.4-mini" in str(spec.command)
    assert "agent_sandbox_codex_json_to_log" in str(spec.command)
    assert spec.metadata["source"] == "scheduled_once"
    assert spec.metadata["resume_mode"] == "same_context"

    latest = wake_store.latest_dispatch_by_request_id()["wake-1"]
    assert latest.status == "dispatched"
    assert latest.request_id == "wake-1"
    assert latest.resume_mode == "same_context"
    assert latest.metadata["backend"] == "container_worker_vm"
    assert latest.metadata["target_name"].startswith("agent-session-1-")
    assert latest.metadata["source"] == "scheduled_once"
    assert latest.metadata["execution_id"].startswith("execution:agent-session-1-")


def test_resume_container_binding_queues_busy_session_and_records_queue_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher, config, session_repository, wake_store = _build_dispatcher(tmp_path)
    session = _upsert_container_session(
        session_repository,
        session_id="session-1",
        metadata={"target_name": "metadata-target"},
        vm_provider_id="provider-target",
    )
    backend = _ContainerBackendStub(active=True)

    monkeypatch.setattr(
        "agent_sandbox.messaging.wake_dispatch.build_execution_backend",
        lambda resolved_config, *, admin_service: backend,
    )

    result = dispatcher.resume_container_binding(
        session,
        worker_config=config,
        prompt="Resume work.",
        resume_mode="same_context",
        dispatch_source="scheduled_cron",
        request_id="wake-busy",
        scheduled_for=datetime(2026, 4, 1, 10, 30, tzinfo=UTC),
        wake_metadata={"schedule_cron": "* * * * *"},
    )

    assert result is not None
    assert backend.prepared_specs == []
    assert backend.started_specs == []
    queue_store = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json")
    queued = queue_store.get("scheduled-wake:wake-busy")
    assert queued is not None
    assert queued.queue_key == "scheduled-wake:wake-busy"
    assert queued.target == "session-1"
    assert queued.vm_name == "metadata-target"
    assert queued.resume_mode == "same_context"
    assert queued.metadata["source"] == "scheduled_cron"
    assert queued.metadata["resume_mode"] == "same_context"
    assert queued.metadata["request_id"] == "wake-busy"

    latest = wake_store.latest_dispatch_by_request_id()["wake-busy"]
    assert latest.status == "queued"
    assert latest.request_id == "wake-busy"
    assert latest.resume_mode == "same_context"
    assert latest.metadata["backend"] == "container_worker_vm"
    assert latest.metadata["target_name"] == "metadata-target"
    assert latest.metadata["queue_key"] == "scheduled-wake:wake-busy"
    assert latest.metadata["source"] == "scheduled_cron"
    assert latest.resume_prompt.metadata["schedule_cron"] == "* * * * *"


def test_resume_container_binding_records_failed_dispatch_when_queueing_busy_session_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher, config, session_repository, wake_store = _build_dispatcher(tmp_path)
    session = _upsert_container_session(
        session_repository,
        session_id="session-1",
        metadata={"target_name": "metadata-target"},
        vm_provider_id="provider-target",
    )
    backend = _ContainerBackendStub(active=True)

    monkeypatch.setattr(
        "agent_sandbox.messaging.wake_dispatch.build_execution_backend",
        lambda resolved_config, *, admin_service: backend,
    )
    monkeypatch.setattr(
        "agent_sandbox.messaging.wake_dispatch.enqueue_agent_prompt",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("queue write failed")),
    )

    result = dispatcher.resume_container_binding(
        session,
        worker_config=config,
        prompt="Resume work.",
        resume_mode="same_context",
        dispatch_source="scheduled_cron",
        request_id="wake-queue-failed",
        scheduled_for=datetime(2026, 4, 1, 10, 30, tzinfo=UTC),
        wake_metadata={"schedule_cron": "* * * * *"},
    )

    assert result is not None
    assert backend.prepared_specs == []
    assert backend.started_specs == []
    assert not (tmp_path / "queued-agent-prompts.json").exists()

    latest = wake_store.latest_dispatch_by_request_id()["wake-queue-failed"]
    assert latest.status == "failed"
    assert latest.request_id == "wake-queue-failed"
    assert latest.resume_mode == "same_context"
    assert latest.error == "queue write failed"
    assert latest.metadata["backend"] == "container_worker_vm"
    assert latest.metadata["target_name"] == "metadata-target"
    assert latest.metadata["source"] == "scheduled_cron"
    assert latest.resume_prompt.metadata["schedule_cron"] == "* * * * *"


def test_resume_container_binding_records_failed_dispatch_when_backend_start_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher, config, session_repository, wake_store = _build_dispatcher(tmp_path)
    session = _upsert_container_session(
        session_repository,
        session_id="session-1",
        vm_provider_id="provider-target",
    )
    backend = _ContainerBackendStub(active=False, start_error=RuntimeError("boom"))

    monkeypatch.setattr(
        "agent_sandbox.messaging.wake_dispatch.build_execution_backend",
        lambda resolved_config, *, admin_service: backend,
    )

    result = dispatcher.resume_container_binding(
        session,
        worker_config=config,
        prompt="Resume work.",
        resume_mode="new_context",
        dispatch_source="scheduled_once",
        request_id="wake-failed",
        scheduled_for=datetime(2026, 4, 1, 10, 30, tzinfo=UTC),
        wake_metadata={"schedule_cron": ""},
    )

    assert result is not None
    assert backend.prepared_specs and backend.started_specs
    assert not (tmp_path / "queued-agent-prompts.json").exists()

    latest = wake_store.latest_dispatch_by_request_id()["wake-failed"]
    assert latest.status == "failed"
    assert latest.request_id == "wake-failed"
    assert latest.resume_mode == "new_context"
    assert latest.error == "boom"
    assert latest.metadata["backend"] == "container_worker_vm"
    assert latest.metadata["target_name"].startswith("agent-session-1-")
    assert latest.metadata["source"] == "scheduled_once"
