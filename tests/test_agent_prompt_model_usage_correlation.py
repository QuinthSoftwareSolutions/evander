from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from evander.config import GatewayConfig
from evander.daemons.effect_orchestrator import EffectExecutionRecord, SqliteExecutionStore
from evander.effects.providers.agent_enqueue_prompt import AgentEnqueuePromptEffectProvider
from evander.effects.providers.agent_prompt import AgentPromptEffectProvider
from evander.effects.providers.lifecycle_hooks import AgentEnqueuePromptQueueDispatchHook
from evander.events.provider_models import (
    AgentEnqueuePromptEffectDefinition,
    AgentPromptEffectDefinition,
)
from evander.model_usage.store import ModelUsageStore
from evander.models import SessionManifest, ToolName
from evander.messaging.agent_prompt_queue import (
    AgentPromptQueueStore,
    QueuedAgentPromptRecord,
)
from evander.triggers.queue import SqliteTriggerEventQueue, TriggerQueueRecord


def _session_repository(config: GatewayConfig, *, vm_provider: str = "libvirt_vm") -> object:
    from evander.repositories.session_manifests import JsonSessionManifestRepository

    repository = JsonSessionManifestRepository(config.session_store_path)
    repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id="vm-1",
            vm_provider=vm_provider,
            vm_provider_id="truman",
            model="gpt-5.2-codex",
            directory_path="root/team-a",
            allowed_tools=[ToolName.GET_AGENT_STATUS],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    return repository


def _runtime_context(
    config: GatewayConfig,
    *,
    session_repository: object,
    resumer: object | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        config=config,
        provider_config={},
        metadata={"directory_path": "root/team-a"},
        admin_service=SimpleNamespace(
            config=config,
            runtime=SimpleNamespace(session_repository=session_repository),
        ),
        agent_resumer=resumer,
        vm_inventory=SimpleNamespace(list_vms=lambda **kwargs: []),
        session_lookup=SimpleNamespace(read_all_sessions=session_repository.read_all),
        session_metadata_updater=SimpleNamespace(session_update_metadata=lambda **kwargs: None),
    )


class _ResumerStub:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def resume_agent(self, **kwargs):
        self.calls.append(dict(kwargs))
        return self.result


class _BackendStub:
    def __init__(self) -> None:
        self.prepared_specs: list[object] = []
        self.started_specs: list[object] = []
        self.destroyed: list[str] = []

    def prepare(self, spec):
        self.prepared_specs.append(spec)
        return spec

    def start(self, spec):
        self.started_specs.append(spec)
        return SimpleNamespace(execution_id=f"exec-{len(self.started_specs)}")

    def destroy(self, execution_id: str) -> None:
        self.destroyed.append(execution_id)


def _usage_store(runtime_dir: Path) -> ModelUsageStore:
    return ModelUsageStore(runtime_dir / "model-usage.db")


def _waiting_record(
    *,
    effect_type: str,
    dispatch_id: str,
    session_id: str,
    execution_backend: str,
    thread_id: str | None = None,
    execution_id: str = "exec-1",
) -> EffectExecutionRecord:
    outputs = {
        "dispatch_id": dispatch_id,
        "success": True,
        "session_id": session_id,
        "execution_backend": execution_backend,
    }
    if thread_id is not None:
        outputs["thread_id"] = thread_id
    now = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)
    return EffectExecutionRecord(
        execution_id=execution_id,
        idempotency_key=f"{dispatch_id}::effect-1",
        event_id=f"event-{dispatch_id}",
        trace_id=f"trace-{dispatch_id}",
        source_node_id="effect-1",
        source_trigger_type="trigger:cron",
        effect_id="effect-1",
        effect_type=effect_type,
        status="waiting",
        started_at=now,
        finished_at=now,
        resolved_config_hash="hash",
        resolved_config={"session_id": session_id, "directory_path": "root/team-a"},
        input_payload={},
        outputs=outputs,
    )


def _finalize_waiting_record(
    *,
    runtime_dir: Path,
    record: EffectExecutionRecord,
    session_id: str,
    execution_backend: str,
) -> None:
    worker = SimpleNamespace(
        config=SimpleNamespace(runtime_dir=runtime_dir),
        event_queue=SqliteTriggerEventQueue(runtime_dir / "orchestration.db"),
        execution_store=SimpleNamespace(put=lambda record: None),
        _publish_completion_event_from_record=lambda **kwargs: "published-event",
        _update_event_delivery_state=lambda event_id: None,
    )
    queue_record = TriggerQueueRecord(
        event_id=f"completion-{record.execution_id}",
        trigger_id="__await-agent-completion__",
        trigger_type="trigger:agent:run:completed",
        occurred_at=datetime(2026, 5, 24, 12, 5, tzinfo=UTC),
        payload={
            "dispatch_id": record.outputs["dispatch_id"],
            "session_id": session_id,
            "execution_backend": execution_backend,
            "thread_id": record.outputs.get("thread_id"),
        },
        delivery_state="delivered",
        delivered_at=datetime(2026, 5, 24, 12, 5, tzinfo=UTC),
    )
    AgentPromptEffectProvider._finalize_waiting_record(
        worker=worker,
        record=record,
        queue_record=queue_record,
    )


@pytest.mark.parametrize(
    ("provider_class", "definition_class", "effect_type"),
    [
        (
            AgentPromptEffectProvider,
            AgentPromptEffectDefinition,
            "effect:agent:prompt",
        ),
        (
            AgentEnqueuePromptEffectProvider,
            AgentEnqueuePromptEffectDefinition,
            "effect:agent:enqueue-prompt",
        ),
    ],
)
def test_direct_prompt_dispatch_registers_and_clears_correlation_context(
    tmp_path: Path,
    provider_class,
    definition_class,
    effect_type: str,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    session_repository = _session_repository(config)
    resumer = _ResumerStub(SimpleNamespace(success=True, thread_id="thread-1"))
    provider = provider_class()
    provider.initialize(
        definition_class(
            id="effect-1",
            display_name="Prompt" if effect_type == "effect:agent:prompt" else "Enqueue",
            type=effect_type,
            target="session-1",
            session_id="session-1",
            prompt="Continue the task",
            model="gpt-5.2-codex",
        ),
        _runtime_context(config, session_repository=session_repository, resumer=resumer),
    )

    result = provider.execute(
        execution_context={"workflow_run_id": "workflow-1", "trace_id": "trace-1"}
    )

    assert result["dispatch_id"].startswith("dispatch-")
    assert resumer.calls
    assert resumer.calls[0]["dispatch_id"] == result["dispatch_id"]
    context = _usage_store(runtime_dir).get_active_correlation_context(
        session_id="session-1",
        agent_harness="codex",
        execution_backend="libvirt_vm",
        dispatch_id=result["dispatch_id"],
    )
    assert context is not None
    assert context.workflow_run_id == "workflow-1"
    assert context.effect_id == "effect-1"
    assert context.node_id == "effect-1"
    assert context.directory_path == "root/team-a"
    assert context.model == "gpt-5.2-codex"
    assert context.trace_id == "trace-1"

    _finalize_waiting_record(
        runtime_dir=runtime_dir,
        record=_waiting_record(
            effect_type=effect_type,
            dispatch_id=result["dispatch_id"],
            session_id="session-1",
            execution_backend="libvirt_vm",
            thread_id=result["thread_id"],
        ),
        session_id="session-1",
        execution_backend="libvirt_vm",
    )
    assert (
        _usage_store(runtime_dir).get_active_correlation_context(
            session_id="session-1",
            agent_harness="codex",
            execution_backend="libvirt_vm",
            dispatch_id=result["dispatch_id"],
        )
        is None
    )


def test_direct_container_prompt_registers_and_clears_correlation_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(
        runtime_dir=runtime_dir,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
    )
    session_repository = _session_repository(config, vm_provider="container_worker_vm")
    backend = _BackendStub()
    monkeypatch.setattr(
        "evander.effects.providers.agent_session.build_execution_backend",
        lambda config, admin_service: backend,
    )
    provider = AgentPromptEffectProvider()
    provider.initialize(
        AgentPromptEffectDefinition(
            id="effect-1",
            display_name="Prompt",
            type="effect:agent:prompt",
            target="session-1",
            session_id="session-1",
            prompt="Continue the task",
            model="gpt-5.2-codex",
        ),
        _runtime_context(
            config,
            session_repository=session_repository,
            resumer=_ResumerStub(SimpleNamespace(success=True, thread_id="thread-1")),
        ),
    )

    result = provider.execute(
        resolved_config={"execution_backend": "container_worker_vm"},
        execution_context={"workflow_run_id": "workflow-2", "trace_id": "trace-2"},
    )

    assert result["execution_backend"] == "container_worker_vm"
    assert backend.started_specs
    context = _usage_store(runtime_dir).get_active_correlation_context(
        session_id="session-1",
        agent_harness="codex",
        execution_backend="container_worker_vm",
        dispatch_id=result["dispatch_id"],
    )
    assert context is not None
    assert context.workflow_run_id == "workflow-2"
    assert context.directory_path == "root/team-a"
    assert context.model == "gpt-5.2-codex"
    assert context.trace_id == "trace-2"

    _finalize_waiting_record(
        runtime_dir=runtime_dir,
        record=_waiting_record(
            effect_type="effect:agent:prompt",
            dispatch_id=result["dispatch_id"],
            session_id="session-1",
            execution_backend="container_worker_vm",
            thread_id=result["thread_id"],
            execution_id=result["execution_id"],
        ),
        session_id="session-1",
        execution_backend="container_worker_vm",
    )
    assert (
        _usage_store(runtime_dir).get_active_correlation_context(
            session_id="session-1",
            agent_harness="codex",
            execution_backend="container_worker_vm",
            dispatch_id=result["dispatch_id"],
        )
        is None
    )


def test_failed_prompt_dispatch_does_not_leave_correlation_context(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    session_repository = _session_repository(config)
    provider = AgentPromptEffectProvider()
    provider.initialize(
        AgentPromptEffectDefinition(
            id="effect-1",
            display_name="Prompt",
            type="effect:agent:prompt",
            target="session-1",
            session_id="session-1",
            prompt="Continue the task",
            model="gpt-5.2-codex",
        ),
        _runtime_context(
            config,
            session_repository=session_repository,
            resumer=_ResumerStub(SimpleNamespace(success=False, message="boom")),
        ),
    )

    with pytest.raises(Exception):
        provider.execute(execution_context={"workflow_run_id": "workflow-1", "trace_id": "trace-1"})

    assert (
        _usage_store(runtime_dir).get_active_correlation_context(
            session_id="session-1",
            agent_harness="codex",
            execution_backend="libvirt_vm",
        )
        is None
    )


def test_queued_libvirt_prompt_dispatch_registers_only_when_dispatched(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    session_repository = _session_repository(config)
    queue_path = runtime_dir / "queued-agent-prompts.json"
    queue_store = AgentPromptQueueStore(queue_path)
    queue_store.put(
        QueuedAgentPromptRecord(
            queue_key="session-1",
            target="session-1",
            vm_name="vm-1",
            session_id="session-1",
            prompt="Continue the task",
            model="gpt-5.2-codex",
            vm_user="agent",
            resume_thread_id=None,
            effect_id="effect-1",
            metadata={"workflow_run_id": "workflow-1"},
        )
    )
    execution_store = SqliteExecutionStore(runtime_dir / "orchestration.db")
    result = SimpleNamespace(success=True, thread_id="thread-queued")
    resume_calls: list[dict[str, object]] = []
    worker = SimpleNamespace(
        config=config,
        queued_agent_prompt_store=queue_store,
        agent_run_status_port=SimpleNamespace(
            list_vm_summaries=lambda: [
                SimpleNamespace(
                    vm_name="vm-1",
                    libvirt_state="running",
                    agent_state="running",
                    session_ids=["session-1"],
                )
            ]
        ),
        agent_prompt_dispatch_port=SimpleNamespace(
            dispatch_agent_prompt=lambda **kwargs: resume_calls.append(dict(kwargs)) or result
        ),
        execution_store=execution_store,
        provider_factory=SimpleNamespace(
            admin_service=SimpleNamespace(
                runtime=SimpleNamespace(session_repository=session_repository)
            )
        ),
        _build_execution_graph=lambda: SimpleNamespace(
            effects_by_id={"effect-1": SimpleNamespace(type="effect:agent:enqueue-prompt")}
        ),
        _publish_completion_event_from_record=lambda **kwargs: "published-event",
        _update_event_delivery_state=lambda event_id: None,
    )

    dispatched = AgentEnqueuePromptQueueDispatchHook().on_post_completion_dispatch(worker=worker)

    assert dispatched == 0
    assert resume_calls == []
    assert (
        _usage_store(runtime_dir).get_active_correlation_context(
            session_id="session-1",
            agent_harness="codex",
            execution_backend="libvirt_vm",
        )
        is None
    )

    worker.agent_run_status_port = SimpleNamespace(
        list_vm_summaries=lambda: [
            SimpleNamespace(
                vm_name="vm-1",
                libvirt_state="running",
                agent_state="idle",
                session_ids=["session-1"],
            )
        ]
    )

    dispatched = AgentEnqueuePromptQueueDispatchHook().on_post_completion_dispatch(worker=worker)

    assert dispatched == 1
    assert resume_calls
    waiting_record = next(iter(execution_store.read_all().values()))
    context = _usage_store(runtime_dir).get_active_correlation_context(
        session_id="session-1",
        agent_harness="codex",
        execution_backend="libvirt_vm",
        dispatch_id=waiting_record.outputs["dispatch_id"],
    )
    assert context is not None
    assert context.workflow_run_id == "workflow-1"
    assert context.directory_path == "root/team-a"
    assert context.model == "gpt-5.2-codex"

    _finalize_waiting_record(
        runtime_dir=runtime_dir,
        record=waiting_record,
        session_id="session-1",
        execution_backend="libvirt_vm",
    )
    assert (
        _usage_store(runtime_dir).get_active_correlation_context(
            session_id="session-1",
            agent_harness="codex",
            execution_backend="libvirt_vm",
            dispatch_id=waiting_record.outputs["dispatch_id"],
        )
        is None
    )


def test_queued_container_prompt_dispatch_registers_and_clears_correlation_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(
        runtime_dir=runtime_dir,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
    )
    session_repository = _session_repository(config, vm_provider="container_worker_vm")
    queue_store = AgentPromptQueueStore(runtime_dir / "queued-agent-prompts.json")
    queue_store.put(
        QueuedAgentPromptRecord(
            queue_key="session-1",
            target="session-1",
            vm_name="truman",
            session_id="session-1",
            prompt="Continue the task",
            model="gpt-5.2-codex",
            vm_user="agent",
            resume_thread_id=None,
            effect_id="effect-1",
            metadata={"workflow_run_id": "workflow-2"},
        )
    )
    backend = _BackendStub()
    monkeypatch.setattr(
        "evander.effects.providers.lifecycle_hooks.build_execution_backend",
        lambda config, admin_service: backend,
    )
    execution_store = SqliteExecutionStore(runtime_dir / "orchestration.db")
    worker = SimpleNamespace(
        config=config,
        queued_agent_prompt_store=queue_store,
        provider_config_resolver=SimpleNamespace(
            resolve_for_session_id=lambda session_id: config,
            resolve_for_directory=lambda directory_path: config,
        ),
        provider_factory=SimpleNamespace(
            admin_service=SimpleNamespace(
                runtime=SimpleNamespace(session_repository=session_repository)
            )
        ),
        execution_store=execution_store,
        _build_execution_graph=lambda: SimpleNamespace(
            effects_by_id={"effect-1": SimpleNamespace(type="effect:agent:enqueue-prompt")}
        ),
        _publish_completion_event_from_record=lambda **kwargs: "published-event",
        _update_event_delivery_state=lambda event_id: None,
    )

    dispatched = AgentEnqueuePromptQueueDispatchHook().on_post_completion_dispatch(worker=worker)

    assert dispatched == 1
    assert backend.started_specs
    waiting_record = next(iter(execution_store.read_all().values()))
    context = _usage_store(runtime_dir).get_active_correlation_context(
        session_id="session-1",
        agent_harness="codex",
        execution_backend="container_worker_vm",
        dispatch_id=waiting_record.outputs["dispatch_id"],
    )
    assert context is not None
    assert context.workflow_run_id == "workflow-2"
    assert context.directory_path == "root/team-a"
    assert context.model == "gpt-5.2-codex"

    _finalize_waiting_record(
        runtime_dir=runtime_dir,
        record=waiting_record,
        session_id="session-1",
        execution_backend="container_worker_vm",
    )
    assert (
        _usage_store(runtime_dir).get_active_correlation_context(
            session_id="session-1",
            agent_harness="codex",
            execution_backend="container_worker_vm",
            dispatch_id=waiting_record.outputs["dispatch_id"],
        )
        is None
    )
