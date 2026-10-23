import hashlib
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
from evander.execution.container_worker_repository import (
    ContainerWorkerExecutionRecord,
    ContainerWorkerExecutionRepository,
)
from evander.messaging.agent_prompt_queue import (
    AgentPromptQueueStore,
    QueuedAgentPromptRecord,
    agent_prompt_replay_queue_key,
)
from evander.messaging.wake_store import WakeRequestStore
from evander.models import (
    SessionManifest,
    WakeDispatchRecord,
    WakeResumePromptPayload,
    WakeScheduleDefinition,
)
from evander.operator_steering import OperatorSteeringNoteStore, operator_steering_note_store
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

    def get_log_snapshot(self, execution_id, *, lines=50, mode=None):
        if self.legacy and mode != "raw":
            return "Later session activity without the old job's native failure."
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


@pytest.mark.parametrize("session_expired", [False, True])
def test_queued_capacity_deadline_expires_without_launching_after_restart(
    tmp_path, monkeypatch, session_expired
):
    worker = worker_at(tmp_path, [])
    backend = Backend()
    monkeypatch.setattr(
        "evander.effects.providers.lifecycle_hooks.build_execution_backend",
        lambda config, admin_service: backend,
    )
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
    if session_expired:
        sessions = worker.provider_factory.admin_service.runtime.session_repository
        session = sessions.get_session("session-1")
        session.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        sessions.upsert_session(session)
        queued.retry_not_before = datetime.now(UTC) - timedelta(seconds=1)
        worker.queued_agent_prompt_store.put(queued, count_retry=False)
        assert AgentEnqueuePromptQueueDispatchHook().on_post_completion_dispatch(worker=worker) == 0
        assert worker.queued_agent_prompt_store.get(queued.queue_key) is not None
    queued.metadata["model_capacity_retry_started_at"] = (
        datetime.now(UTC) - timedelta(hours=2)
    ).isoformat()
    queued.retry_not_before = datetime.now(UTC) - timedelta(minutes=1)
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


def test_expired_capacity_retry_completes_its_wake_in_the_session_directory(tmp_path, monkeypatch):
    published = []
    worker = worker_at(tmp_path, published)
    sessions = worker.provider_factory.admin_service.runtime.session_repository
    session = sessions.get_session("session-1")
    session.directory_path = "root/team-a"
    sessions.upsert_session(session)
    wake = WakeRequestStore(tmp_path / "wake-requests.json")
    worker.runtime = SimpleNamespace(wake_store=wake)
    wake.schedule_resume_request(
        session_id="session-1",
        prompt="Finish the approved work.",
        schedule=WakeScheduleDefinition(resume_at=datetime.now(UTC) - timedelta(hours=2)),
        request_id="wake-request-1",
        source="test",
        resume_mode="same_context",
        enabled=True,
        metadata={},
        directory_path="root/team-a",
    )
    wake.record_dispatch(
        WakeDispatchRecord(
            dispatch_id="wake-dispatch-1",
            request_id="wake-request-1",
            session_id="session-1",
            status="queued",
            resume_prompt=WakeResumePromptPayload(prompt="Finish the approved work."),
            resume_mode="same_context",
            source="scheduled_once",
            metadata={"capacity_deferral": "true", "queue_reason": "model_capacity"},
        )
    )
    queued = QueuedAgentPromptRecord(
        queue_key="scheduled-wake:wake-request-1",
        target="session-1",
        vm_name="session-1",
        session_id="session-1",
        prompt="Finish the approved work.",
        model="test-model",
        dispatch_id="wake-dispatch-1",
        effect_id="scheduled-wake:wake-request-1",
        queue_reason="model_capacity",
        subscription_profile_id="team-a",
        retry_not_before=datetime.now(UTC) - timedelta(minutes=1),
        metadata={
            "model_capacity_retry_count": "2",
            "model_capacity_retry_started_at": (datetime.now(UTC) - timedelta(hours=2)).isoformat(),
            "subscription_replay_attempt": "2",
            "wake_dispatch_id": "wake-dispatch-1",
            "request_id": "wake-request-1",
            "source": "scheduled_once",
        },
    )
    worker.queued_agent_prompt_store.put(queued, count_retry=False)
    backend = Backend()
    monkeypatch.setattr(
        "evander.effects.providers.lifecycle_hooks.build_execution_backend",
        lambda config, admin_service: backend,
    )
    hook = AgentEnqueuePromptQueueDispatchHook()
    assert hook.on_post_completion_dispatch(worker=worker) == 1
    assert hook.on_post_completion_dispatch(worker=worker) == 0
    completed = wake.get_request("wake-request-1")
    assert completed.status == "completed_or_acknowledged"
    assert completed.completed_at is not None
    assert completed.enabled is False
    assert completed.completion_evidence["directory_path"] == "root/team-a"
    assert wake.find_due_requests(now_utc=datetime.now(UTC) + timedelta(days=1)) == []
    assert worker.queued_agent_prompt_store.read_all() == {}
    assert backend.started == []
    assert len(published) == 1
    assert published[0][1]["success"] is False
    assert published[0][1]["scope"]["directory_path"] == "root/team-a"
    assert published[0][1]["wake_dispatch_id"] == "wake-dispatch-1"


def test_capacity_deadline_preserves_worker_accepted_before_waiting_record_saved(
    tmp_path, monkeypatch
):
    published = []
    worker = worker_at(tmp_path, published)
    backend = Backend()
    assert AgentPromptEffectProvider._defer_subscription_waiting_record(
        worker=worker,
        record=waiting_record(),
        backend=backend,
        status=backend.status,
        session_id="session-1",
        execution_id="worker-original",
    )
    queued = next(iter(worker.queued_agent_prompt_store.read_all().values()))
    queued.metadata["model_capacity_retry_started_at"] = (
        datetime.now(UTC) - timedelta(hours=2)
    ).isoformat()
    queued.retry_not_before = datetime.now(UTC) - timedelta(minutes=1)
    worker.queued_agent_prompt_store.put(queued, count_retry=False)
    claimed = worker.queued_agent_prompt_store.claim_for_replay(
        queued.queue_key,
        queued.dispatch_id,
        claimed_queue_key=agent_prompt_replay_queue_key(queued),
    )
    assert claimed is not None
    backend.repository = SimpleNamespace(
        read_all=lambda: {
            "worker-accepted": SimpleNamespace(
                session_id="session-1", execution_id="worker-accepted", last_status="running"
            )
        }
    )
    backend.status = SimpleNamespace(status=ExecutionStatusKind.RUNNING, message=None, metadata={})
    monkeypatch.setattr(
        "evander.effects.providers.lifecycle_hooks.build_execution_backend",
        lambda config, admin_service: backend,
    )
    worker = worker_at(tmp_path, published)
    hook = AgentEnqueuePromptQueueDispatchHook()
    assert hook.on_post_completion_dispatch(worker=worker) == 0
    assert worker.queued_agent_prompt_store.get(claimed.queue_key) is not None
    assert all(r.status != "failed" for r in worker.execution_store.read_all().values())
    assert published == []
    assert backend.started == []
    assert "worker-accepted" not in backend.destroyed


@pytest.mark.parametrize("legacy_mapping", [False, True])
@pytest.mark.parametrize("already_completed", [False, True])
@pytest.mark.parametrize("long_queue_key", [False, True])
@pytest.mark.parametrize("budget_expired", [False, True])
def test_capacity_recovery_adopts_the_real_worker_outcome_after_start_crash(
    tmp_path, monkeypatch, legacy_mapping, already_completed, long_queue_key, budget_expired
):
    published = []
    worker = worker_at(tmp_path, published)
    backend = Backend()
    backend.repository = ContainerWorkerExecutionRepository(tmp_path / "worker-executions.db")
    failed_at = datetime.now(UTC) - timedelta(minutes=120 if budget_expired else 15)
    accepted_at = failed_at + timedelta(minutes=1)
    queued = QueuedAgentPromptRecord(
        queue_key="prompt:" + ("q" * 600 if long_queue_key else "") + ":dispatch-1",
        target="session-1",
        vm_name="session-1",
        session_id="session-1",
        prompt="Finish the approved work.",
        model="test-model",
        dispatch_id="dispatch-1",
        effect_id="prompt",
        queue_reason="model_capacity",
        subscription_profile_id="team-a",
        resume_thread_id="native-current-thread",
        resume_mode="same_context",
        created_at=failed_at,
        updated_at=failed_at,
        retry_not_before=failed_at + timedelta(seconds=15),
        metadata={
            "model_capacity_retry_count": "1",
            "model_capacity_retry_started_at": failed_at.isoformat(),
            "model_capacity_failed_execution_id": "worker-original",
            "subscription_replay_attempt": "1",
        },
    )
    worker.queued_agent_prompt_store.put(queued, count_retry=False)
    backend.status = SimpleNamespace(status=ExecutionStatusKind.RUNNING, message=None, metadata={})
    steering = operator_steering_note_store(tmp_path)
    note = steering.create_note(
        session_id="session-1", dispatch_id="dispatch-1", message="Finish the approved work."
    )
    queued.metadata["operator_steering_note_id"] = note.note_id
    worker.queued_agent_prompt_store.put(queued, count_retry=False)

    def accept(spec):
        backend.started.append(spec)
        backend.repository.put(
            ContainerWorkerExecutionRecord(
                execution_id=spec.target_name,
                session_id=spec.session_id,
                worker_job_id=spec.target_name,
                worker_url="http://worker.test",
                target_name=spec.target_name,
                directory_path=spec.directory_path,
                created_at=accepted_at,
                started_at=accepted_at,
                last_status="running",
                metadata=dict(spec.metadata),
            )
        )
        return SimpleNamespace(execution_id=spec.target_name)

    backend.start = accept
    monkeypatch.setattr(
        "evander.effects.providers.lifecycle_hooks.build_execution_backend",
        lambda config, admin_service: backend,
    )
    monkeypatch.setattr(
        "evander.effects.providers.agent_prompt.build_execution_backend",
        lambda config, admin_service: backend,
    )

    class BeforeDeadline(datetime):
        @classmethod
        def now(cls, tz=None):
            return accepted_at.astimezone(tz) if tz else accepted_at.replace(tzinfo=None)

    def crash_after_accept(**kwargs):
        raise SystemExit("orchestrator stopped before saving its waiting row")

    hook = AgentEnqueuePromptQueueDispatchHook()
    with monkeypatch.context() as crash:
        crash.setattr("evander.effects.providers.lifecycle_hooks.datetime", BeforeDeadline)
        crash.setattr(hook, "_record_container_waiting_execution", crash_after_accept)
        with pytest.raises(SystemExit):
            hook.on_post_completion_dispatch(worker=worker)
    assert len(backend.started) == 1
    mapping = backend.repository.list_by_session("session-1")[0]
    if legacy_mapping:
        mapping.metadata.pop("queued_prompt_execution_key_sha256")
    if already_completed:
        mapping.last_status = "stopped"
        backend.status = SimpleNamespace(
            status=ExecutionStatusKind.STOPPED, message=None, metadata={}
        )
    backend.repository.put(mapping)

    # Restore through a new worker after the retry deadline, or while the
    # budget is live but the already accepted job's session has since expired.
    worker = worker_at(tmp_path, published)
    if not budget_expired:
        sessions = worker.provider_factory.admin_service.runtime.session_repository
        session = sessions.get_session("session-1")
        session.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        sessions.upsert_session(session)
    recovered = AgentEnqueuePromptQueueDispatchHook()

    def fail_note_transition(*args, **kwargs):
        raise OSError("temporary steering-store failure")

    with monkeypatch.context() as failure:
        failure.setattr(
            OperatorSteeringNoteStore, "mark_queued_replacement_started", fail_note_transition
        )
        assert recovered.on_post_completion_dispatch(worker=worker) == 1
    assert len(backend.started) == 1
    assert worker.queued_agent_prompt_store.read_all()
    assert steering.get_note(note.note_id).status == "pending"
    AgentEnqueuePromptQueueDispatchHook().on_post_completion_dispatch(worker=worker)
    assert worker.queued_agent_prompt_store.read_all() == {}
    delivered = steering.get_note(note.note_id)
    assert delivered.status == "delivered"
    assert delivered.worker_execution_id == mapping.execution_id
    waiting = [r for r in worker.execution_store.read_all().values() if r.status == "waiting"]
    assert len(waiting) == 1
    assert waiting[0].outputs["execution_id"] == mapping.execution_id
    assert mapping.execution_id not in backend.destroyed
    assert all(port != "completed" for port, _ in published)

    backend.status = SimpleNamespace(status=ExecutionStatusKind.STOPPED, message=None, metadata={})
    assert AgentEnqueuePromptEffectProvider.reconcile_waiting(worker=worker) == 1
    worker = worker_at(tmp_path, published)
    assert AgentEnqueuePromptEffectProvider.reconcile_waiting(worker=worker) == 0
    assert AgentEnqueuePromptQueueDispatchHook().on_post_completion_dispatch(worker=worker) == 0
    completions = [outputs for port, outputs in published if port == "completed"]
    assert len(completions) == 1
    assert completions[0]["success"] is True
    assert completions[0].get("retry_exhausted") is not True
    assert completions[0]["worker_execution_id"] == mapping.execution_id
    assert len(backend.started) == 1


@pytest.mark.parametrize(
    "mismatch", ["attempt", "profile", "directory", "old_legacy", "previous_failure"]
)
def test_capacity_recovery_does_not_adopt_a_different_or_historical_job(
    tmp_path, monkeypatch, mismatch
):
    worker = worker_at(tmp_path, [])
    backend = Backend()
    backend.repository = ContainerWorkerExecutionRepository(tmp_path / "worker-executions.db")
    failed_at = datetime.now(UTC) - timedelta(hours=2)
    queued = QueuedAgentPromptRecord(
        queue_key="prompt:dispatch-1:replay:dispatch-1",
        target="session-1",
        vm_name="session-1",
        session_id="session-1",
        prompt="Finish the approved work.",
        model="test-model",
        dispatch_id="dispatch-1",
        effect_id="prompt",
        queue_reason="model_capacity",
        subscription_profile_id="team-a",
        created_at=failed_at,
        updated_at=failed_at,
        retry_not_before=failed_at,
        metadata={
            "model_capacity_retry_count": "1",
            "model_capacity_retry_started_at": failed_at.isoformat(),
            "model_capacity_failed_execution_id": "worker-original",
            "subscription_replay_attempt": "1",
        },
    )
    worker.queued_agent_prompt_store.put(queued, count_retry=False)
    hook = AgentEnqueuePromptQueueDispatchHook()
    metadata = {
        "dispatch_id": "dispatch-1",
        "queued": True,
        "subscription_profile_id": "team-a",
        "model": "test-model",
        "queued_prompt_execution_key_sha256": hashlib.sha256(
            (
                hook._queued_prompt_execution_event_id(
                    queue_key=queued.queue_key, dispatch_id=queued.dispatch_id, record=queued
                )
                + "::prompt"
            ).encode()
        ).hexdigest(),
    }
    if mismatch == "attempt":
        metadata["queued_prompt_execution_key_sha256"] += ":different-attempt"
    elif mismatch == "profile":
        metadata["subscription_profile_id"] = "team-b"
    elif mismatch == "old_legacy":
        metadata.pop("queued_prompt_execution_key_sha256")
    execution_id = "worker-original" if mismatch == "previous_failure" else "worker-other"
    backend.repository.put(
        ContainerWorkerExecutionRecord(
            execution_id=execution_id,
            session_id="session-1",
            worker_job_id=execution_id,
            worker_url="http://worker.test",
            target_name=execution_id,
            last_status="stopped",
            directory_path="root/other" if mismatch == "directory" else "root",
            created_at=failed_at + timedelta(minutes=-1 if mismatch == "old_legacy" else 1),
            metadata=metadata,
        )
    )
    backend.status = SimpleNamespace(status=ExecutionStatusKind.STOPPED, message=None, metadata={})
    monkeypatch.setattr(
        "evander.effects.providers.lifecycle_hooks.build_execution_backend",
        lambda config, admin_service: backend,
    )
    assert hook.on_post_completion_dispatch(worker=worker) == 1
    records = list(worker.execution_store.read_all().values())
    assert len(records) == 1
    assert records[0].status == "failed"
    assert records[0].outputs["retry_exhausted"] is True
    assert worker.queued_agent_prompt_store.read_all() == {}
    assert backend.started == []


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
