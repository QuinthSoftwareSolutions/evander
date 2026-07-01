from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from evander.config import GatewayConfig
from evander.execution import ExecutionBackendKind, ExecutionStatusKind
from evander.messaging.agent_prompt_queue import AgentPromptQueueStore
from evander.messaging.wake_dispatch import HostWakeDispatcher, SessionWakeBinding
from evander.messaging.wake_store import WakeRequestStore
from evander.models import (
    SessionManifest,
    ToolName,
    WakeDispatchRecord,
    WakeResumePromptPayload,
    WakeScheduleDefinition,
)
from evander.repositories.session_manifests import JsonSessionManifestRepository


class _FrozenDateTime(datetime):
    frozen: datetime = datetime(2026, 4, 2, 12, 0, tzinfo=UTC)

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        if tz is None:
            return cls.frozen
        return cls.frozen.astimezone(tz)


@pytest.fixture(autouse=True)
def _freeze_wake_store_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    _FrozenDateTime.frozen = datetime(2026, 4, 2, 12, 0, tzinfo=UTC)
    monkeypatch.setattr("evander.messaging.wake_store.datetime", _FrozenDateTime)


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


@pytest.fixture(autouse=True)
def _disable_completion_watcher_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "evander.messaging.wake_dispatch.subprocess.Popen",
        lambda *args, **kwargs: SimpleNamespace(),
    )


def _upsert_container_session(
    session_repository: JsonSessionManifestRepository,
    *,
    session_id: str,
    metadata: dict[str, object] | None = None,
    model: str | None = "gpt-5.4-mini",
    vm_provider_id: str | None = None,
    web_search_enabled: bool = False,
    expires_at: datetime | None = None,
) -> SessionManifest:
    session = SessionManifest(
        session_id=session_id,
        vm_id=None,
        vm_provider="container_worker_vm",
        vm_provider_id=vm_provider_id,
        directory_path="root/team-a",
        model=model,
        allowed_tools=[ToolName.SCHEDULE_RESUME],
        web_search_enabled=web_search_enabled,
        expires_at=expires_at or datetime.now(UTC) + timedelta(hours=1),
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


def test_resume_binding_passes_session_web_search_mode_to_resume_script(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher, config, session_repository, wake_store = _build_dispatcher(tmp_path)
    del wake_store
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id="vm-a",
            directory_path="root/team-a",
            model="gpt-5.4-mini",
            allowed_tools=[ToolName.SCHEDULE_RESUME],
            web_search_enabled=True,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    resume_script = tmp_path / "resume.sh"
    resume_script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    worker_config = config.model_copy(update={"host_resume_script_path": resume_script})
    captured: dict[str, object] = {}
    watcher_calls: list[dict[str, object]] = []

    def fake_run(cmd: list[str], **kwargs: object):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("evander.messaging.wake_dispatch.subprocess.run", fake_run)
    monkeypatch.setattr(
        dispatcher,
        "_spawn_agent_completion_watcher",
        lambda **kwargs: watcher_calls.append(dict(kwargs)),
    )

    result = dispatcher.resume_binding(
        SessionWakeBinding(session_id="session-1", vm_id="vm-a", source="test"),
        worker_config=worker_config,
        prompt="Resume work.",
        dispatch_source="scheduled_once",
        request_id="wake-1",
    )

    assert result is not None
    assert result.status == "dispatched"
    assert "--dispatch-id" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--dispatch-id") + 1] == result.dispatch_id
    assert captured["env"]["AGENT_PROVIDER"] == "codex"
    assert captured["env"]["AGENT_MODEL"] == "gpt-5.4-mini"
    assert captured["env"]["AGENT_WEB_SEARCH_MODE"] == "live"
    assert watcher_calls == [
        {
            "vm_name": "vm-a",
            "dispatch_id": result.dispatch_id,
            "vm_user": worker_config.wake_vm_user,
            "session_id": "session-1",
            "directory_path": None,
        }
    ]


def test_resume_binding_uses_session_agent_provider_and_default_model_when_model_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher, config, session_repository, wake_store = _build_dispatcher(tmp_path)
    del wake_store
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-claude",
            vm_id="vm-claude",
            directory_path="root/team-a",
            model=None,
            allowed_tools=[ToolName.SCHEDULE_RESUME],
            web_search_enabled=False,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={"agent_provider": "claude"},
        )
    )
    resume_script = tmp_path / "resume.sh"
    resume_script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    worker_config = config.model_copy(update={"host_resume_script_path": resume_script})
    captured: dict[str, object] = {}

    def fake_run(cmd: list[str], **kwargs: object):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("evander.messaging.wake_dispatch.subprocess.run", fake_run)

    result = dispatcher.resume_binding(
        SessionWakeBinding(session_id="session-claude", vm_id="vm-claude", source="test"),
        worker_config=worker_config,
        prompt="Resume work.",
        dispatch_source="scheduled_once",
        request_id="wake-claude",
    )

    assert result is not None
    assert result.status == "dispatched"
    assert captured["env"]["AGENT_PROVIDER"] == "claude"
    assert captured["env"]["AGENT_MODEL"] == "claude-sonnet-4-6"


def test_resume_binding_records_failed_dispatch_when_script_is_missing(
    tmp_path: Path,
) -> None:
    dispatcher, config, session_repository, wake_store = _build_dispatcher(tmp_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id="vm-a",
            directory_path="root/team-a",
            model="gpt-5.4-mini",
            allowed_tools=[ToolName.SCHEDULE_RESUME],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    worker_config = config.model_copy(update={"host_resume_script_path": tmp_path / "missing.sh"})

    result = dispatcher.resume_binding(
        SessionWakeBinding(session_id="session-1", vm_id="vm-a", source="test"),
        worker_config=worker_config,
        prompt="Resume work.",
        dispatch_source="scheduled_once",
        request_id="wake-missing-script",
    )

    assert result is not None
    assert result.status == "failed"
    assert result.error is not None
    assert "resume script missing" in result.error
    latest = wake_store.latest_dispatch_by_request_id()["wake-missing-script"]
    assert latest.status == "failed"
    assert latest.error is not None
    assert "resume script missing" in latest.error


def test_resume_binding_records_failed_dispatch_for_expired_session_before_script(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher, _config, session_repository, wake_store = _build_dispatcher(tmp_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id="vm-a",
            directory_path="root/team-a",
            model="gpt-5.4-mini",
            allowed_tools=[ToolName.SCHEDULE_RESUME],
            expires_at=datetime.now(UTC) - timedelta(minutes=5),
        )
    )
    monkeypatch.setattr(
        "evander.messaging.wake_dispatch.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("resume script must not run for expired wake sessions")
        ),
    )

    result = dispatcher.resume_binding(
        SessionWakeBinding(session_id="session-1", vm_id="vm-a", source="test"),
        worker_config=GatewayConfig(runtime_dir=tmp_path),
        prompt="Resume work.",
        dispatch_source="scheduled_once",
        request_id="wake-expired",
    )

    assert result is not None
    assert result.status == "failed"
    assert result.error is not None
    assert "session expired: session_id=session-1 expires_at=" in result.error
    assert result.metadata["vm_id"] == "vm-a"
    latest = wake_store.latest_dispatch_by_request_id()["wake-expired"]
    assert latest.status == "failed"
    assert latest.error == result.error


def test_resume_binding_sanitizes_failure_stdout_stderr_and_exit_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher, config, session_repository, wake_store = _build_dispatcher(tmp_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id="vm-a",
            directory_path="root/team-a",
            model="gpt-5.4-mini",
            allowed_tools=[ToolName.SCHEDULE_RESUME],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    resume_script = tmp_path / "resume.sh"
    resume_script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    worker_config = config.model_copy(update={"host_resume_script_path": resume_script})

    def fake_run(cmd: list[str], **kwargs: object):
        return subprocess.CompletedProcess(
            cmd,
            2,
            stdout="line one\nsecret=abc123",
            stderr="line two\nprivate=xyz789",
        )

    monkeypatch.setattr("evander.messaging.wake_dispatch.subprocess.run", fake_run)

    result = dispatcher.resume_binding(
        SessionWakeBinding(session_id="session-1", vm_id="vm-a", source="test"),
        worker_config=worker_config,
        prompt="Resume work.",
        dispatch_source="scheduled_once",
        request_id="wake-failed",
    )

    assert result is not None
    assert result.status == "failed"
    assert result.error is not None
    assert "exit_code=2" in result.error
    latest = wake_store.latest_dispatch_by_request_id()["wake-failed"]
    assert latest.metadata["stdout"] == "line one secret=[redacted]"
    assert latest.metadata["stderr"] == "line two private=[redacted]"
    assert "\n" not in latest.error
    assert "\n" not in latest.metadata["stdout"]
    assert "\n" not in latest.metadata["stderr"]


def test_resume_binding_classifies_model_unavailable_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher, config, session_repository, wake_store = _build_dispatcher(tmp_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id="vm-a",
            directory_path="root/team-a",
            model="gpt-5.4-mini",
            allowed_tools=[ToolName.SCHEDULE_RESUME],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={"agent_provider": "codex"},
        )
    )
    resume_script = tmp_path / "resume.sh"
    resume_script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    worker_config = config.model_copy(update={"host_resume_script_path": resume_script})
    wake_store.schedule_resume_request(
        session_id="session-1",
        prompt="Resume work.",
        schedule=WakeScheduleDefinition(resume_at=datetime.now(UTC) + timedelta(minutes=5)),
        request_id="wake-model-unavailable",
        source="test",
        resume_mode="same_context",
        enabled=True,
        metadata={},
    )

    def fake_run(cmd: list[str], **kwargs: object):
        return subprocess.CompletedProcess(
            cmd,
            1,
            stdout="model_not_found: unknown model token=secret",
            stderr="does not have access to model gpt-5.4-mini",
        )

    monkeypatch.setattr("evander.messaging.wake_dispatch.subprocess.run", fake_run)

    result = dispatcher.resume_binding(
        SessionWakeBinding(session_id="session-1", vm_id="vm-a", source="test"),
        worker_config=worker_config,
        prompt="Resume work.",
        dispatch_source="scheduled_once",
        request_id="wake-model-unavailable",
    )

    assert result is not None
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.startswith(
        "model unavailable: configured codex model 'gpt-5.4-mini' is unavailable or not permitted"
    )
    assert "\n" not in result.error

    latest = wake_store.latest_dispatch_by_request_id()["wake-model-unavailable"]
    assert latest.status == "failed"
    assert latest.error == result.error
    assert latest.metadata["failure_kind"] == "model_unavailable"
    assert latest.metadata["agent_provider"] == "codex"
    assert latest.metadata["agent_model"] == "gpt-5.4-mini"
    assert "token=[redacted]" in (latest.metadata["stdout"] or "")
    assert "\n" not in (latest.metadata["stdout"] or "")
    assert "\n" not in (latest.metadata["stderr"] or "")

    ledger_entry = wake_store.list_ledger_entries(session_id="session-1")[0]
    assert ledger_entry["failure_reason"] is not None
    assert ledger_entry["failure_reason"].startswith(
        "model unavailable: configured codex model 'gpt-5.4-mini' is unavailable or not permitted"
    )
    assert "codex" in ledger_entry["failure_reason"]
    assert "gpt-5.4-mini" in ledger_entry["failure_reason"]
    assert ledger_entry["latest_dispatch"]["status"] == "failed"
    assert ledger_entry["retry_available"] is True


def test_resume_binding_timeout_error_does_not_include_prompt_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher, config, session_repository, wake_store = _build_dispatcher(tmp_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id="vm-a",
            directory_path="root/team-a",
            model="gpt-5.4-mini",
            allowed_tools=[ToolName.SCHEDULE_RESUME],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    resume_script = tmp_path / "resume.sh"
    resume_script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    worker_config = config.model_copy(
        update={
            "host_resume_script_path": resume_script,
            "wake_resume_timeout_seconds": 7.0,
        }
    )
    prompt = "Do sensitive wake work with token=abc123"

    def fake_run(cmd: list[str], **kwargs: object):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

    monkeypatch.setattr("evander.messaging.wake_dispatch.subprocess.run", fake_run)

    result = dispatcher.resume_binding(
        SessionWakeBinding(session_id="session-1", vm_id="vm-a", source="test"),
        worker_config=worker_config,
        prompt=prompt,
        dispatch_source="scheduled_once",
        request_id="wake-timeout",
    )

    assert result is not None
    latest = wake_store.latest_dispatch_by_request_id()["wake-timeout"]
    assert latest.status == "timed_out"
    assert latest.error is not None
    assert "resume script timed out" in latest.error
    assert "timeout_seconds=7.000" in latest.error
    assert prompt not in latest.error
    assert "--prompt" not in latest.error
    assert "exception_type" not in latest.metadata
    assert latest.metadata["timeout_seconds"] == 7.0
    assert result.error == latest.error
    assert prompt not in str(latest.metadata)
    assert "--prompt" not in str(latest.metadata)


def test_resume_container_binding_dispatches_idle_session_and_records_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher, config, session_repository, wake_store = _build_dispatcher(tmp_path)
    session = _upsert_container_session(
        session_repository,
        session_id="session-1",
        vm_provider_id="provider-target",
        web_search_enabled=True,
        metadata={"container_worker": {"image": "runner:wake-image"}},
    )
    backend = _ContainerBackendStub(active=False)
    captured: dict[str, object] = {}
    monitor_calls: list[dict[str, object]] = []

    def build_backend(resolved_config, *, admin_service):
        captured["config"] = resolved_config
        captured["admin_service"] = admin_service
        return backend

    monkeypatch.setattr("evander.messaging.wake_dispatch.build_execution_backend", build_backend)
    monkeypatch.setattr(
        dispatcher,
        "_spawn_container_completion_monitor",
        lambda **kwargs: monitor_calls.append(dict(kwargs)),
    )

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
    assert spec.image == "runner:wake-image"
    assert spec.target_name.startswith("agent-session-1-")
    assert "codex exec resume --last" in str(spec.command)
    assert "--model gpt-5.4-mini" in str(spec.command)
    assert 'web_search="live"' in str(spec.command)
    assert "evander_codex_json_to_log" in str(spec.command)
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
    assert monitor_calls == [
        {
            "backend": backend,
            "execution_id": latest.metadata["execution_id"],
            "dispatch_id": result.dispatch_id,
            "session_id": "session-1",
            "directory_path": "root/team-a",
            "vm_user": config.wake_vm_user,
        }
    ]


def test_resume_container_binding_dispatches_idle_claude_session_and_records_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher, config, session_repository, wake_store = _build_dispatcher(tmp_path)
    session = _upsert_container_session(
        session_repository,
        session_id="session-claude",
        model=None,
        vm_provider_id="provider-target",
        web_search_enabled=False,
        metadata={"container_worker": {"image": "runner:wake-image"}, "agent_provider": "claude"},
    )
    backend = _ContainerBackendStub(active=False)
    captured: dict[str, object] = {}

    def build_backend(resolved_config, *, admin_service):
        captured["config"] = resolved_config
        captured["admin_service"] = admin_service
        return backend

    monkeypatch.setattr("evander.messaging.wake_dispatch.build_execution_backend", build_backend)

    result = dispatcher.resume_container_binding(
        session,
        worker_config=config,
        prompt="Resume work.",
        resume_mode="same_context",
        dispatch_source="scheduled_once",
        request_id="wake-claude",
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
    assert spec.session_id == "session-claude"
    assert spec.directory_path == "root/team-a"
    assert spec.image == "runner:wake-image"
    assert spec.target_name.startswith("agent-session-claude-")
    assert "claude --continue -p" in str(spec.command)
    assert "--model claude-sonnet-4-6" in str(spec.command)
    assert "codex exec" not in str(spec.command)
    assert spec.metadata["source"] == "scheduled_once"
    assert spec.metadata["resume_mode"] == "same_context"
    assert spec.metadata["agent_provider"] == "claude"

    latest = wake_store.latest_dispatch_by_request_id()["wake-claude"]
    assert latest.status == "dispatched"
    assert latest.request_id == "wake-claude"
    assert latest.resume_mode == "same_context"
    assert latest.metadata["backend"] == "container_worker_vm"
    assert latest.metadata["target_name"].startswith("agent-session-claude-")
    assert latest.metadata["source"] == "scheduled_once"
    assert latest.metadata["execution_id"].startswith("execution:agent-session-claude-")


def test_monitor_container_execution_completion_marks_wake_request_completed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher, _config, session_repository, wake_store = _build_dispatcher(tmp_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id=None,
            vm_provider="container_worker_vm",
            vm_provider_id="provider-target",
            directory_path="root/team-a",
            model="gpt-5.4-mini",
            allowed_tools=[ToolName.SCHEDULE_RESUME],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={"agent_provider": "claude"},
        )
    )
    scheduled_for = datetime(2026, 4, 1, 10, 30, tzinfo=UTC)
    wake_store.schedule_resume_request(
        session_id="session-1",
        prompt="Resume work.",
        schedule=WakeScheduleDefinition(resume_at=scheduled_for),
        request_id="wake-container",
        source="test",
        resume_mode="same_context",
        enabled=True,
        metadata={},
    )
    wake_store.record_dispatch(
        WakeDispatchRecord(
            dispatch_id="wake-dispatch-1",
            request_id="wake-container",
            session_id="session-1",
            status="dispatched",
            resume_prompt=WakeResumePromptPayload(prompt="Resume work."),
            resume_mode="same_context",
            scheduled_for=scheduled_for,
            dispatched_at=datetime(2026, 4, 1, 10, 30, 5, tzinfo=UTC),
            source="scheduled_once",
            metadata={"backend": "container_worker_vm", "target_name": "execution-1"},
        )
    )
    inspect_calls: list[str] = []
    destroy_calls: list[str] = []
    statuses = [
        SimpleNamespace(status=ExecutionStatusKind.RUNNING),
        SimpleNamespace(status=ExecutionStatusKind.STOPPED),
    ]
    backend = SimpleNamespace(
        inspect=lambda execution_id: inspect_calls.append(execution_id) or statuses.pop(0),
        destroy=lambda execution_id: destroy_calls.append(execution_id) or SimpleNamespace(),
    )
    monkeypatch.setattr("evander.messaging.wake_dispatch.time.sleep", lambda _: None)

    dispatcher._monitor_container_execution_completion(
        backend=backend,
        execution_id="execution-1",
        dispatch_id="wake-dispatch-1",
        session_id="session-1",
        directory_path="root/team-a",
        vm_user="agent",
    )

    completed = wake_store.get_request("wake-container")
    assert completed is not None
    assert completed.completed_at is not None
    assert completed.completion_evidence["dispatch_id"] == "wake-dispatch-1"
    assert completed.completion_evidence["trigger_type"] == "trigger:agent:run:completed"
    assert completed.completion_evidence["agent_state"] == "idle"
    assert inspect_calls == ["execution-1", "execution-1"]
    assert destroy_calls == ["execution-1"]


def test_resume_container_binding_records_failed_dispatch_for_expired_session_before_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher, config, session_repository, wake_store = _build_dispatcher(tmp_path)
    session = _upsert_container_session(
        session_repository,
        session_id="session-1",
        vm_provider_id="provider-target",
        expires_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    monkeypatch.setattr(
        "evander.messaging.wake_dispatch.build_execution_backend",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("container backend must not be built for expired wake sessions")
        ),
    )
    monkeypatch.setattr(
        "evander.messaging.wake_dispatch.enqueue_agent_prompt",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("expired wake sessions must not be queued")
        ),
    )

    result = dispatcher.resume_container_binding(
        session,
        worker_config=config,
        prompt="Resume work.",
        resume_mode="same_context",
        dispatch_source="scheduled_once",
        request_id="wake-container-expired",
        scheduled_for=datetime(2026, 4, 1, 10, 30, tzinfo=UTC),
        wake_metadata={"schedule_cron": ""},
    )

    assert result is not None
    assert result.status == "failed"
    assert result.error is not None
    assert "session expired: session_id=session-1 expires_at=" in result.error
    assert not (tmp_path / "queued-agent-prompts.json").exists()
    latest = wake_store.latest_dispatch_by_request_id()["wake-container-expired"]
    assert latest.status == "failed"
    assert latest.error == result.error
    assert latest.metadata["backend"] == "container_worker_vm"


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
        "evander.messaging.wake_dispatch.build_execution_backend",
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


def test_resume_container_binding_queues_busy_claude_session_with_provider_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher, config, session_repository, wake_store = _build_dispatcher(tmp_path)
    session = _upsert_container_session(
        session_repository,
        session_id="session-claude",
        model=None,
        metadata={
            "target_name": "metadata-target",
            "agent_provider": "claude",
        },
        vm_provider_id="provider-target",
    )
    backend = _ContainerBackendStub(active=True)

    monkeypatch.setattr(
        "evander.messaging.wake_dispatch.build_execution_backend",
        lambda resolved_config, *, admin_service: backend,
    )

    result = dispatcher.resume_container_binding(
        session,
        worker_config=config,
        prompt="Resume work.",
        resume_mode="same_context",
        dispatch_source="scheduled_cron",
        request_id="wake-busy-claude",
        scheduled_for=datetime(2026, 4, 1, 10, 30, tzinfo=UTC),
        wake_metadata={"schedule_cron": "* * * * *"},
    )

    assert result is not None
    assert backend.prepared_specs == []
    assert backend.started_specs == []
    queue_store = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json")
    queued = queue_store.get("scheduled-wake:wake-busy-claude")
    assert queued is not None
    assert queued.model == "claude-sonnet-4-6"
    assert queued.metadata["agent_provider"] == "claude"
    assert queued.metadata["wake_dispatch_id"] == result.dispatch_id
    assert queued.metadata["source"] == "scheduled_cron"
    assert queued.metadata["resume_mode"] == "same_context"
    assert queued.metadata["request_id"] == "wake-busy-claude"

    latest = wake_store.latest_dispatch_by_request_id()["wake-busy-claude"]
    assert latest.status == "queued"
    assert latest.request_id == "wake-busy-claude"
    assert latest.resume_mode == "same_context"
    assert latest.metadata["backend"] == "container_worker_vm"
    assert latest.metadata["target_name"] == "metadata-target"
    assert latest.metadata["queue_key"] == "scheduled-wake:wake-busy-claude"
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
        "evander.messaging.wake_dispatch.build_execution_backend",
        lambda resolved_config, *, admin_service: backend,
    )
    monkeypatch.setattr(
        "evander.messaging.wake_dispatch.enqueue_agent_prompt",
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
    assert latest.error is not None
    assert "queue write failed" in latest.error
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
        "evander.messaging.wake_dispatch.build_execution_backend",
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
    assert latest.error is not None
    assert "boom" in latest.error
    assert latest.metadata["backend"] == "container_worker_vm"
    assert latest.metadata["target_name"].startswith("agent-session-1-")
    assert latest.metadata["source"] == "scheduled_once"


def test_resume_container_binding_classifies_model_unavailable_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatcher, config, session_repository, wake_store = _build_dispatcher(tmp_path)
    session = _upsert_container_session(
        session_repository,
        session_id="session-1",
        model="gpt-5.4-mini",
        metadata={"agent_provider": "claude"},
        vm_provider_id="provider-target",
    )
    wake_store.schedule_resume_request(
        session_id="session-1",
        prompt="Resume work.",
        schedule=WakeScheduleDefinition(resume_at=datetime.now(UTC) + timedelta(minutes=5)),
        request_id="wake-container-model-unavailable",
        source="test",
        resume_mode="same_context",
        enabled=True,
        metadata={},
    )

    monkeypatch.setattr(
        "evander.messaging.wake_dispatch.build_execution_backend",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("invalid_model: model gpt-5.4-mini does not exist")
        ),
    )

    result = dispatcher.resume_container_binding(
        session,
        worker_config=config,
        prompt="Resume work.",
        resume_mode="same_context",
        dispatch_source="scheduled_once",
        request_id="wake-container-model-unavailable",
        scheduled_for=datetime(2026, 4, 1, 10, 30, tzinfo=UTC),
        wake_metadata={"schedule_cron": ""},
    )

    assert result is not None
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.startswith(
        "model unavailable: configured claude model 'gpt-5.4-mini' is unavailable or not permitted"
    )
    assert "\n" not in result.error

    latest = wake_store.latest_dispatch_by_request_id()["wake-container-model-unavailable"]
    assert latest.status == "failed"
    assert latest.error == result.error
    assert latest.metadata["failure_kind"] == "model_unavailable"
    assert latest.metadata["agent_provider"] == "claude"
    assert latest.metadata["agent_model"] == "gpt-5.4-mini"

    ledger_entry = wake_store.list_ledger_entries(session_id="session-1")[0]
    assert ledger_entry["failure_reason"] is not None
    assert ledger_entry["failure_reason"].startswith(
        "model unavailable: configured claude model 'gpt-5.4-mini' is unavailable or not permitted"
    )
    assert "claude" in ledger_entry["failure_reason"]
    assert "gpt-5.4-mini" in ledger_entry["failure_reason"]
    assert ledger_entry["latest_dispatch"]["status"] == "failed"
    assert ledger_entry["retry_available"] is True
