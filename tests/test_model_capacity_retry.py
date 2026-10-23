from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from evander.codex_auth import CodexAuthMode, legacy_worker_model_capacity_deferral
from evander.config import GatewayConfig
from evander.daemons.effect_orchestrator import EffectExecutionRecord, SqliteExecutionStore
from evander.effects.providers.agent_prompt import AgentPromptEffectProvider
from evander.effects.providers.agent_enqueue_prompt import AgentEnqueuePromptEffectProvider
from evander.effects.providers.lifecycle_hooks import AgentEnqueuePromptQueueDispatchHook
from evander.execution import ExecutionStatusKind
from evander.messaging.agent_prompt_queue import AgentPromptQueueStore
from evander.models import SessionManifest
from evander.repositories.session_manifests import JsonSessionManifestRepository
from evander.triggers.queue import SqliteTriggerEventQueue


class Backend:
    def __init__(self, *, legacy=False):
        self.legacy = legacy
        self.repository = SimpleNamespace(read_all=lambda: {})
        self.started = []
        self.destroyed = []
        self.status = SimpleNamespace(
            status=ExecutionStatusKind.FAILED,
            message="Selected model is at capacity. Please try a different model.",
            metadata={
                "codex_subscription_thread_id": "native-current-thread",
                "codex_subscription_deferral": {"profile_id": "team-a", "reason": "model_capacity"},
            },
        )
        if legacy:
            self.status.metadata.pop("codex_subscription_deferral")
            self.status.metadata.update(
                {
                    "codex_auth_mode": "chatgpt_subscription",
                    "codex_subscription_exit_code": 1,
                    "subscription_profile_id": "team-a",
                }
            )

    def prepare(self, spec):
        return spec

    def start(self, spec):
        self.started.append(spec)
        return SimpleNamespace(execution_id=spec.target_name)

    def inspect(self, execution_id):
        return self.status

    def destroy(self, execution_id):
        self.destroyed.append(execution_id)

    def get_log_snapshot(self, execution_id, *, lines=50):
        logs = "session id: native-current-thread\nFix committed; ready to publish.\n"
        if self.legacy and self.status.status == ExecutionStatusKind.FAILED:
            logs += "codex error: Selected model is at capacity. Please try a different model.\n"
        return logs


def worker_at(tmp_path, published):
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
    )
    sessions = JsonSessionManifestRepository(config.session_store_path)
    if sessions.get_session("session-1") is None:
        sessions.upsert_session(
            SessionManifest(
                session_id="session-1",
                vm_id=None,
                allowed_tools=[],
                expires_at=datetime.now(UTC) + timedelta(hours=2),
                model="test-model",
                codex_auth_mode=CodexAuthMode.CHATGPT_SUBSCRIPTION,
                subscription_profile_id="team-a",
                metadata={
                    "execution_backend": "container_worker_vm",
                    "container_worker": {"image": "runner:test"},
                },
            )
        )
    return SimpleNamespace(
        config=config,
        execution_store=SqliteExecutionStore(tmp_path / "orchestration.db"),
        event_queue=SqliteTriggerEventQueue(tmp_path / "orchestration.db"),
        queued_agent_prompt_store=AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json"),
        agent_prompt_dispatch_port=object(),
        provider_config_resolver=SimpleNamespace(resolve_for_session_id=lambda sid: config),
        provider_factory=SimpleNamespace(
            admin_service=SimpleNamespace(runtime=SimpleNamespace(session_repository=sessions))
        ),
        _publish_completion_event_from_record=lambda record, outputs, branch_port=None: (
            published.append((branch_port, dict(outputs))) or "published"
        ),
        _update_event_delivery_state=lambda event_id: None,
    )


def waiting_record():
    now = datetime.now(UTC)
    return EffectExecutionRecord(
        execution_id="effect-original",
        idempotency_key="original::prompt",
        event_id="original",
        trace_id="workflow-1",
        source_node_id="trigger",
        source_trigger_type="trigger:event",
        effect_id="prompt",
        effect_type="effect:agent:enqueue-prompt",
        status="waiting",
        started_at=now,
        finished_at=now,
        resolved_config_hash="config",
        resolved_config={
            "target": "session-1",
            "session_id": "session-1",
            "vm_name": "session-1",
            "prompt": "Finish publishing the approved fix.",
            "model": "test-model",
            "resume_mode": "new_context",
            "queue_key": "prompt:dispatch-1",
            "workflow_run_id": "workflow-1",
            "directory_path": "root",
            "execution_backend": "container_worker_vm",
        },
        input_payload={},
        outputs={
            "dispatch_id": "dispatch-1",
            "session_id": "session-1",
            "execution_id": "worker-original",
            "execution_backend": "container_worker_vm",
        },
    )


@pytest.mark.parametrize("legacy", [False, True])
def test_model_capacity_recovery_resumes_current_thread_once_after_restart(
    tmp_path, monkeypatch, legacy
):
    published = []
    backend = Backend(legacy=legacy)
    for module in ("agent_prompt", "lifecycle_hooks"):
        monkeypatch.setattr(
            f"evander.effects.providers.{module}.build_execution_backend",
            lambda config, admin_service: backend,
        )
    worker = worker_at(tmp_path, published)
    worker.execution_store.put(waiting_record())
    assert AgentEnqueuePromptEffectProvider.reconcile_waiting(worker=worker) == 1
    queued = next(iter(worker.queued_agent_prompt_store.read_all().values()))
    assert queued.queue_reason == "model_capacity"
    assert queued.resume_thread_id == "native-current-thread"
    assert queued.resume_mode == "same_context"
    assert queued.metadata["model_capacity_retry_count"] == "1"
    assert queued.metadata["workflow_run_id"] == "workflow-1"
    assert queued.dispatch_id == "dispatch-1"
    assert published == []  # No terminal completion while a retry is pending.

    worker = worker_at(tmp_path, published)
    hook = AgentEnqueuePromptQueueDispatchHook()
    assert hook.on_post_completion_dispatch(worker=worker) == 0
    assert backend.started == []
    # Make only the persisted delay eligible; a restarted dispatcher must use
    # the saved conversation and create one fresh execution for this dispatch.
    monkeypatch.setattr(hook, "_retry_not_before_ready", lambda record: True)
    assert hook.on_post_completion_dispatch(worker=worker) == 1
    assert hook.on_post_completion_dispatch(worker=worker) == 0
    assert len(backend.started) == 1
    turn = backend.started[0].codex_turn
    assert turn.resume_thread_id == "native-current-thread"
    assert turn.resume_mode == "same_context"
    assert backend.started[0].session_id == "session-1"
    assert backend.started[0].metadata["dispatch_id"] == "dispatch-1"
    assert worker.queued_agent_prompt_store.read_all() == {}

    # Another capacity failure must keep the original budget after the queue
    # entry was removed for dispatch and the orchestrator was restarted.
    worker = worker_at(tmp_path, published)
    assert AgentEnqueuePromptEffectProvider.reconcile_waiting(worker=worker) == 1
    again = next(iter(worker.queued_agent_prompt_store.read_all().values()))
    assert again.metadata["model_capacity_retry_count"] == "2"
    assert (
        again.metadata["model_capacity_retry_started_at"]
        == queued.metadata["model_capacity_retry_started_at"]
    )
    assert published[0][0] == "started"
    assert all(port != "completed" for port, _ in published)
    backend.status = SimpleNamespace(status=ExecutionStatusKind.STOPPED, message=None, metadata={})
    worker = worker_at(tmp_path, published)
    hook = AgentEnqueuePromptQueueDispatchHook()
    monkeypatch.setattr(hook, "_retry_not_before_ready", lambda record: True)
    assert hook.on_post_completion_dispatch(worker=worker) == 1
    assert AgentEnqueuePromptEffectProvider.reconcile_waiting(worker=worker) == 1
    worker = worker_at(tmp_path, published)
    assert AgentEnqueuePromptEffectProvider.reconcile_waiting(worker=worker) == 0
    assert AgentEnqueuePromptQueueDispatchHook().on_post_completion_dispatch(worker=worker) == 0
    assert len(backend.started) == 2
    completions = [outputs for port, outputs in published if port == "completed"]
    assert len(completions) == 1
    assert completions[0]["success"] is True
    assert completions[0]["dispatch_id"] == "dispatch-1"


@pytest.mark.parametrize("exhaustion", ["attempts", "deadline"])
@pytest.mark.parametrize("error_route", [False, True])
def test_model_capacity_exhaustion_marks_waiting_execution_failed(
    tmp_path, exhaustion, error_route, monkeypatch
):
    published = []
    worker = worker_at(tmp_path, published)
    monkeypatch.setattr(
        AgentPromptEffectProvider, "_has_error_route", staticmethod(lambda **kw: error_route)
    )
    record = waiting_record()
    record.input_payload.update(
        {
            "model_capacity_retry_count": "8" if exhaustion == "attempts" else "1",
            "model_capacity_retry_started_at": (
                datetime.now(UTC) - timedelta(hours=2 if exhaustion == "deadline" else 0)
            ).isoformat(),
        }
    )
    worker.execution_store.put(record)
    backend = Backend()
    assert AgentPromptEffectProvider._defer_subscription_waiting_record(
        worker=worker,
        record=record,
        backend=backend,
        status=backend.status,
        session_id="session-1",
        execution_id="worker-original",
    )
    stored = worker.execution_store.get_execution("effect-original")
    assert stored.status == ("error_routed" if error_route else "failed")
    assert stored.outputs["retry_exhausted"] is True
    assert stored.outputs["queued"] is False
    assert "Model-capacity retries exhausted" in stored.error
    assert backend.destroyed == ["worker-original"]
    assert worker.queued_agent_prompt_store.read_all() == {}
    assert len(published) == 1
    assert published[0][0] == ("error" if error_route else "completed")
    assert published[0][1]["success"] is False


def test_queued_capacity_deadline_expires_without_launching_after_restart(tmp_path, monkeypatch):
    worker = worker_at(tmp_path, [])
    backend = Backend()
    record = waiting_record()
    assert AgentPromptEffectProvider._defer_subscription_waiting_record(
        worker=worker,
        record=record,
        backend=backend,
        status=backend.status,
        session_id="session-1",
        execution_id="worker-original",
    )
    queued = next(iter(worker.queued_agent_prompt_store.read_all().values()))
    queued.metadata["model_capacity_retry_started_at"] = (
        datetime.now(UTC) - timedelta(hours=2)
    ).isoformat()
    worker.queued_agent_prompt_store.put(queued, count_retry=False)
    worker = worker_at(tmp_path, [])
    hook = AgentEnqueuePromptQueueDispatchHook()
    assert hook.on_post_completion_dispatch(worker=worker) == 1
    assert hook.on_post_completion_dispatch(worker=worker) == 0
    assert backend.started == []
    failed = [r for r in worker.execution_store.read_all().values() if r.status == "failed"]
    assert len(failed) == 1
    assert failed[0].outputs["retry_exhausted"] is True
    assert worker.queued_agent_prompt_store.read_all() == {}


@pytest.mark.parametrize(
    "metadata,logs",
    [
        ({"codex_subscription_exit_code": 0}, "codex error: Selected model is at capacity."),
        ({"codex_auth_mode": "metered_api"}, "codex error: Selected model is at capacity."),
        ({"subscription_profile_id": None}, "codex error: Selected model is at capacity."),
        ({}, "Tool output: Selected model is at capacity."),
        ({}, "codex error: Selected model is at capacity.\ncodex error: Invalid configuration."),
        (
            {"codex_subscription_deferral": {"reason": "rate_limited"}},
            "codex error: Selected model is at capacity.",
        ),
    ],
)
def test_legacy_compatibility_requires_failed_subscription_and_last_native_error(metadata, logs):
    worker_metadata = {
        "codex_auth_mode": "chatgpt_subscription",
        "codex_subscription_exit_code": 1,
        "subscription_profile_id": "team-a",
        **metadata,
    }
    assert legacy_worker_model_capacity_deferral(worker_metadata, logs) is None
