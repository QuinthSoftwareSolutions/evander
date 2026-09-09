from __future__ import annotations

from datetime import UTC, datetime, timedelta
import sqlite3
from types import SimpleNamespace

import pytest

from evander.agent_threads import agent_resume_thread_missing, usable_agent_thread_id
from evander.daemons.effect_orchestrator import SqliteExecutionStore
from evander.effects.providers.agent_enqueue_prompt import AgentEnqueuePromptEffectProvider
from evander.execution import ExecutionStatusKind
from evander.models import SessionManifest
from evander.orchestration.agent_prompt_activity import AgentPromptActivityRuntime
from evander.triggers.queue import SqliteTriggerEventQueue
from evander_team_addon.models import AgentContextClearingPolicy
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.providers import (
    TeamAgentPromptCompletionFinalizeHook,
    _activate_delivered_team_prompt,
)
from evander_team_addon.services.admin_service import TeamAdminService
from evander_team_addon.services.runtime_service import TeamRuntimeService
from evander_team_addon.sqlite_store import TeamStateSqliteStore
from evander_team_addon.services.wake_prompt_context import (
    build_compact_wake_context,
    wake_uses_fresh_context,
    work_lineage_resume_thread_id,
)

from test_admin_service import build_admin_runtime, write_config
from test_team_addon_runtime import _seed_team


NATIVE_A = "01a085f1-767c-7592-a03e-b276a20b2aa1"
NATIVE_B = "01a085f8-6d66-77f2-a8eb-4ab769b8a5ea"


class NativeBackend:
    """The real prompt provider and reconciliation run against a bounded worker port."""

    def __init__(self):
        self.repository = SimpleNamespace(read_all=lambda: {})
        self.jobs = {}
        self.native_queries = []

    def prepare(self, spec):
        return spec

    def start(self, spec):
        self.jobs[spec.target_name] = SimpleNamespace(
            spec=spec, logs="", metadata={}, status=ExecutionStatusKind.RUNNING
        )
        return SimpleNamespace(execution_id=spec.target_name)

    def inspect(self, execution_id):
        job = self.jobs[execution_id]
        return SimpleNamespace(status=job.status, metadata=job.metadata, message=None)

    def get_log_snapshot(self, execution_id, *, lines=400):
        return "\n".join(self.jobs[execution_id].logs.splitlines()[-lines:])

    def get_log_page(self, execution_id, *, query, limit, mode):
        self.native_queries.append((execution_id, query, limit, mode))
        assert (query, limit, mode) == ("session id:", 20, "raw")
        return {
            "text": "\n".join(
                line for line in self.jobs[execution_id].logs.splitlines() if query in line
            )
        }

    def destroy(self, execution_id):
        self.jobs[execution_id].status = ExecutionStatusKind.DESTROYED


@pytest.fixture
def native_env(tmp_path, monkeypatch):
    admin = build_admin_runtime(
        write_config(tmp_path, container_worker_base_url="http://worker.test")
    )
    runtime_dir = admin.config.runtime_dir
    repo = _seed_team(runtime_dir)
    TeamAdminService(repo).update_agent(
        agent_id="backend",
        context_clearing_policy=AgentContextClearingPolicy.WORK_LINEAGE,
        set_context_clearing_policy=True,
        expected_revision=repo.read().revision,
    )
    admin.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-backend",
            vm_provider="container_worker_vm",
            vm_provider_id="container-target",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "container-target",
            },
        )
    )
    backend = NativeBackend()
    monkeypatch.setattr(
        "evander.effects.providers.agent_enqueue_prompt.container_backend", lambda *a, **kw: backend
    )
    monkeypatch.setattr(
        "evander.effects.providers.agent_prompt.build_execution_backend", lambda *a, **kw: backend
    )
    execution_store = SqliteExecutionStore(runtime_dir / "orchestration.db")
    worker = SimpleNamespace(
        config=admin.config,
        execution_store=execution_store,
        _agent_prompt_activity=AgentPromptActivityRuntime(
            runtime_dir=runtime_dir, execution_store=execution_store
        ),
        provider_config_resolver=admin.runtime.provider_config,
        provider_factory=SimpleNamespace(admin_service=admin),
        event_queue=SqliteTriggerEventQueue(runtime_dir / "orchestration.db"),
        _publish_completion_event_from_record=lambda **kwargs: "effect-completed",
        _update_event_delivery_state=lambda *args: None,
    )
    return SimpleNamespace(
        admin=admin,
        repo=repo,
        backend=backend,
        worker=worker,
        runtime=TeamRuntimeService(repo),
        hook=TeamAgentPromptCompletionFinalizeHook(),
    )


def _lease(env):
    _, leases = env.runtime.lease_due_agents(
        agent_id="backend",
        respect_wake_interval=False,
        defer_activation=True,
    )
    assert len(leases) == 1
    return leases[0]


def _submit(env, lease, *, queued_activation=False):
    fresh = wake_uses_fresh_context(lease, effective_resume_mode="same_context")
    prompt = str(build_compact_wake_context(lease, fresh_context=fresh))
    result = env.admin.vm_agent_admin.enqueue_agent_prompt(
        "session-backend",
        prompt,
        dispatch_id=lease["dispatch_id"],
        resume_thread_id=work_lineage_resume_thread_id(lease),
        resume_mode="new_context" if fresh else "same_context",
    )
    assert result.success and not result.queued
    assert result.thread_id is None
    if queued_activation:
        env.runtime.mark_agent_dispatch_queued(agent_id="backend", dispatch_id=lease["dispatch_id"])
        env.hook.on_post_completion_dispatch(worker=env.worker)
    else:
        _activate_delivered_team_prompt(
            None,
            repo=env.repo,
            dispatch_id=lease["dispatch_id"],
            result_payload=result.model_dump(),
            mirror_session_metadata=False,
        )
    records = env.worker.execution_store.list_waiting_records("effect:agent:enqueue-prompt")
    record = next(row for row in records if row.outputs["dispatch_id"] == lease["dispatch_id"])
    assert record.outputs["thread_id"] is None
    assert record.outputs["native_thread_id"] is None
    return record, env.backend.jobs[record.outputs["execution_id"]]


def _complete(env, job, *, failed=False):
    job.status = ExecutionStatusKind.FAILED if failed else ExecutionStatusKind.STOPPED
    assert AgentEnqueuePromptEffectProvider.reconcile_waiting(worker=env.worker) == 1
    env.hook.on_post_completion_dispatch(worker=env.worker)
    assert env.repo.read().agent_runtimes["backend"].active_dispatch_id is None


@pytest.mark.parametrize("queued_activation", [False, True])
def test_async_native_capture_and_same_thread_resume_across_lineages(native_env, queued_activation):
    env = native_env
    first = _lease(env)
    lineage_a = first["dispatch"]["work_lineage_id"]
    record, job = _submit(env, first, queued_activation=queued_activation)
    assert env.repo.read().agent_runtimes["backend"].work_lineage_thread_ids == {}
    assert job.spec.codex_turn.resume_thread_id is None
    assert job.spec.codex_turn.resume_mode == "new_context"
    # More than both reconciliation tail limits; capture must search the raw job.
    job.logs = f"session id: {NATIVE_A}\n" + "working\n" * 900
    assert AgentEnqueuePromptEffectProvider.reconcile_waiting(worker=env.worker) == 0
    env.hook.on_post_completion_dispatch(worker=env.worker)
    state = TeamStateRepository(env.admin.config.runtime_dir).read()
    assert state.dispatches[first["dispatch_id"]].native_thread_id == NATIVE_A
    assert state.agent_runtimes["backend"].work_lineage_thread_ids == {lineage_a: NATIVE_A}
    assert env.backend.native_queries
    _complete(env, job)

    TeamAdminService(env.repo).create_or_reprioritize_task(
        task_id="task-b",
        owner_agent_id="backend",
        project_id="project",
        priority=0,
        title="Unrelated work",
        description="Different lineage",
        created_by_agent_id="lead",
        expected_revision=env.repo.read().revision,
    )
    second = _lease(env)
    lineage_b = second["dispatch"]["work_lineage_id"]
    assert lineage_b != lineage_a
    _, second_job = _submit(env, second)
    assert second_job.spec.codex_turn.resume_thread_id is None
    second_job.logs = f"session id: {NATIVE_B}\ncompleted work\n"
    _complete(env, second_job)

    def prioritize_a(state):
        state.tasks["task-b"].priority = 5
        state.agent_runtimes["backend"].next_wake_at = None
        return state

    env.repo.update(prioritize_a)
    third = _lease(env)
    assert third["dispatch"]["work_lineage_id"] == lineage_a
    assert work_lineage_resume_thread_id(third) == NATIVE_A
    _, third_job = _submit(env, third)
    assert third_job.spec.codex_turn.resume_thread_id == NATIVE_A
    assert "codex exec resume" in third_job.spec.command
    assert NATIVE_A in third_job.spec.command
    third_job.logs = f"session id: {NATIVE_A}\ncontinued original work\n"
    _complete(env, third_job)
    assert env.repo.read().agent_runtimes["backend"].work_lineage_thread_ids == {
        lineage_a: NATIVE_A,
        lineage_b: NATIVE_B,
    }


@pytest.mark.parametrize(
    "poison", ["team-dispatch-poison", "dispatch-old", "exec-old", "chat-thread-old"]
)
def test_poisoned_binding_falls_back_to_full_fresh_context_and_rebinds(native_env, poison):
    env = native_env
    lineage = env.repo.read().tasks["task"].work_lineage_id

    def poison_binding(state):
        state.agent_runtimes["backend"].work_lineage_thread_ids[lineage] = poison
        return state

    env.repo.update(poison_binding)
    lease = _lease(env)
    assert work_lineage_resume_thread_id(lease) is None
    context = build_compact_wake_context(lease, fresh_context=wake_uses_fresh_context(lease))
    assert context["context_mode"] == "fresh_full"
    assert context["current_dispatch"]["task_ids"] == ["task"]
    _, job = _submit(env, lease)
    assert job.spec.codex_turn.resume_mode == "new_context"
    assert poison not in job.spec.command
    assert env.repo.read().agent_runtimes["backend"].work_lineage_thread_ids == {}
    job.logs = f"session id: {NATIVE_A}\ncontinued from durable records\n"
    _complete(env, job)
    assert env.repo.read().agent_runtimes["backend"].work_lineage_thread_ids == {lineage: NATIVE_A}


def test_provider_observation_overrides_requested_native_id_and_uses_worker_metadata(native_env):
    env = native_env
    first = _lease(env)
    _, job = _submit(env, first)
    job.metadata = {"codex_subscription_thread_id": NATIVE_A}
    assert AgentEnqueuePromptEffectProvider.reconcile_waiting(worker=env.worker) == 0
    env.hook.on_post_completion_dispatch(worker=env.worker)
    assert env.backend.native_queries == []
    job.logs = "done\n"
    _complete(env, job)
    second = _lease(env)
    record, job = _submit(env, second)
    assert job.spec.codex_turn.resume_thread_id == NATIVE_A
    # A provider can reject/fall back internally; its observation wins.
    job.metadata = {"codex_subscription_thread_id": NATIVE_B}
    AgentEnqueuePromptEffectProvider.reconcile_waiting(worker=env.worker)
    env.hook.on_post_completion_dispatch(worker=env.worker)
    observed = env.worker.execution_store.get_execution(record.execution_id)
    assert observed.outputs["thread_id"] == NATIVE_B
    assert observed.outputs["resume_thread_id"] == NATIVE_A
    state = env.repo.read()
    assert state.agent_runtimes["backend"].work_lineage_thread_ids == {
        second["dispatch"]["work_lineage_id"]: NATIVE_B,
    }
    revision = state.revision
    env.hook.on_post_completion_dispatch(worker=env.worker)
    assert env.repo.read().revision == revision


@pytest.mark.parametrize("same_lineage_correction", [False, True])
def test_completed_receipt_uses_original_lineage_and_cannot_replace_newer_owner(
    native_env, same_lineage_correction
):
    env = native_env
    first = _lease(env)
    _, job = _submit(env, first)
    first_id = first["dispatch_id"]
    lineage_a = first["dispatch"]["work_lineage_id"]
    # Simulate completion routing winning the race before the post-completion hook.
    env.runtime.finalize_agent_dispatch_from_activity(
        session_id="session-backend", dispatch_id=first_id
    )
    TeamAdminService(env.repo).create_or_reprioritize_task(
        task_id="task-correction",
        owner_agent_id="backend",
        project_id="project",
        priority=0,
        title="Correction",
        description="Correct the prior task",
        parent_task_id="task" if same_lineage_correction else None,
        created_by_agent_id="lead",
        expected_revision=env.repo.read().revision,
    )
    correction = _lease(env)
    lineage_b = correction["dispatch"]["work_lineage_id"]
    assert (lineage_b == lineage_a) == same_lineage_correction
    env.runtime.activate_delivered_agent_dispatch(
        dispatch_id=correction["dispatch_id"], thread_id="opaque/correction"
    )
    observed_at = datetime.now(UTC)
    state, recorded = env.runtime.record_agent_dispatch_native_thread(
        session_id="session-backend",
        dispatch_id=first_id,
        thread_id=NATIVE_A,
        observed_at=observed_at,
    )
    assert recorded
    assert state.dispatches[first_id].native_thread_id == NATIVE_A
    expected = {lineage_a: NATIVE_A, lineage_b: "opaque/correction"}
    assert state.agent_runtimes["backend"].work_lineage_thread_ids == expected
    assert state.agent_runtimes["backend"].last_thread_id == "opaque/correction"
    assert state.agent_runtimes["backend"].active_dispatch_id == correction["dispatch_id"]
    # An older receipt for the same dispatch also cannot reverse a newer observation.
    state, recorded = env.runtime.record_agent_dispatch_native_thread(
        session_id="session-backend",
        dispatch_id=first_id,
        thread_id=NATIVE_B,
        observed_at=observed_at - timedelta(seconds=1),
    )
    assert not recorded
    assert state.dispatches[first_id].native_thread_id == NATIVE_A
    env.runtime.record_agent_dispatch_native_thread(
        session_id="session-backend",
        dispatch_id=first_id,
        thread_id=NATIVE_A,
        observed_at=observed_at + timedelta(seconds=2),
    )
    state, recorded = env.runtime.record_agent_dispatch_native_thread(
        session_id="session-backend",
        dispatch_id=first_id,
        thread_id=NATIVE_B,
        observed_at=observed_at + timedelta(seconds=1),
    )
    assert not recorded
    assert state.dispatches[first_id].native_thread_id == NATIVE_A


def test_delayed_binding_ignores_session_mismatch_policy_reset_and_separate_chat(native_env):
    env = native_env
    lease = _lease(env)
    _, job = _submit(env, lease)
    dispatch_id = lease["dispatch_id"]
    state, recorded = env.runtime.record_agent_dispatch_native_thread(
        session_id="session-other",
        dispatch_id=dispatch_id,
        thread_id=NATIVE_A,
    )
    assert not recorded and state.dispatches[dispatch_id].native_thread_id is None
    for policy in (AgentContextClearingPolicy.OFF, AgentContextClearingPolicy.WORK_LINEAGE):
        TeamAdminService(env.repo).update_agent(
            agent_id="backend",
            context_clearing_policy=policy,
            set_context_clearing_policy=True,
            expected_revision=env.repo.read().revision,
        )
    state, _ = env.runtime.record_agent_dispatch_native_thread(
        session_id="session-backend",
        dispatch_id=dispatch_id,
        thread_id=NATIVE_A,
    )
    assert state.agent_runtimes["backend"].work_lineage_thread_ids == {}

    # An unscoped chat receipt may update its own dispatch, never a task binding.
    def separate_chat(state):
        state.dispatches[dispatch_id].work_lineage_id = None
        state.dispatches[dispatch_id].cause_kind = "team_chat"
        return state

    env.repo.update(separate_chat)
    state, _ = env.runtime.record_agent_dispatch_native_thread(
        session_id="session-backend",
        dispatch_id=dispatch_id,
        thread_id=NATIVE_B,
    )
    assert state.agent_runtimes["backend"].work_lineage_thread_ids == {}


def test_native_ids_are_opaque_but_correlation_ids_are_not_threads():
    assert usable_agent_thread_id("provider/native:opaque") == "provider/native:opaque"
    assert usable_agent_thread_id("arbitrary-job-id", "arbitrary-job-id") is None
    assert usable_agent_thread_id({"thread": "invalid"}) is None
    assert usable_agent_thread_id(" ") is None


def test_async_missing_native_thread_invalidates_only_its_owned_binding(native_env):
    env = native_env
    first = _lease(env)
    _, job = _submit(env, first)
    job.logs = f"session id: {NATIVE_A}\nwork recorded\n"
    _complete(env, job)
    second = _lease(env)
    record, job = _submit(env, second)
    assert job.spec.codex_turn.resume_thread_id == NATIVE_A
    job.logs = f"No rollout found for session ID {NATIVE_A}\n"
    _complete(env, job, failed=True)
    failed_record = env.worker.execution_store.get_execution(record.execution_id)
    assert failed_record.outputs["resume_thread_missing"] is True
    assert env.repo.read().agent_runtimes["backend"].work_lineage_thread_ids == {}
    third = _lease(env)
    assert work_lineage_resume_thread_id(third) is None
    _, job = _submit(env, third)
    assert job.spec.codex_turn.resume_mode == "new_context"
    # Even if the same native ID becomes available again, replaying the older
    # failure cannot remove a binding now owned by this newer dispatch.
    job.logs = f"session id: {NATIVE_A}\nrecovered from durable work\n"
    _complete(env, job)
    env.hook.on_post_completion_dispatch(worker=env.worker)
    assert env.repo.read().agent_runtimes["backend"].work_lineage_thread_ids == {
        first["dispatch"]["work_lineage_id"]: NATIVE_A,
    }


@pytest.mark.parametrize("binding_state", ["cleared", "different_thread", "new_owner"])
def test_handled_missing_thread_receipts_do_not_open_state_transactions(
    native_env, monkeypatch, binding_state
):
    env = native_env
    first = _lease(env)
    _, job = _submit(env, first)
    job.logs = f"session id: {NATIVE_A}\nwork recorded\n"
    _complete(env, job)
    failed_lease = _lease(env)
    _, job = _submit(env, failed_lease)
    job.logs = f"No rollout found for session ID {NATIVE_A}\n"
    _complete(env, job, failed=True)
    lineage = first["dispatch"]["work_lineage_id"]

    if binding_state == "different_thread":
        def replace_thread(state):
            runtime = state.agent_runtimes["backend"]
            runtime.work_lineage_thread_ids[lineage] = NATIVE_B
            runtime.work_lineage_thread_dispatch_ids[lineage] = failed_lease["dispatch_id"]
            return state

        env.repo.update(replace_thread)
    elif binding_state == "new_owner":
        _, job = _submit(env, _lease(env))
        job.logs = f"session id: {NATIVE_A}\ncontinued work\n"
        _complete(env, job)

    before = env.repo.read()
    transactions = []
    update_state = TeamStateSqliteStore.update_state

    def track_update(store, *args, **kwargs):
        transactions.append(store.path)
        return update_state(store, *args, **kwargs)

    monkeypatch.setattr(TeamStateSqliteStore, "update_state", track_update)
    # The failed execution remains available for all of these polling cycles.
    # An unchanged revision alone would miss the expensive no-op transactions.
    for _ in range(3):
        env.hook.on_post_completion_dispatch(worker=env.worker)
    after = env.repo.read()
    assert transactions == []
    assert after.revision == before.revision
    assert after.agent_runtimes["backend"] == before.agent_runtimes["backend"]


@pytest.mark.parametrize(
    "diagnostic",
    [
        "error: missing dependency foo",
        "file not found: config.json",
        "model missing: selected-model",
    ],
)
def test_failed_resumed_work_does_not_invalidate_observed_thread(native_env, diagnostic):
    env = native_env
    first = _lease(env)
    _, job = _submit(env, first)
    job.logs = f"session id: {NATIVE_A}\nwork recorded\n"
    _complete(env, job)
    second = _lease(env)
    record, job = _submit(env, second)
    job.logs = f"session id: {NATIVE_A}\n{diagnostic}\n"
    assert not agent_resume_thread_missing(job.logs, resume_thread_id=NATIVE_A)
    _complete(env, job, failed=True)
    failed_record = env.worker.execution_store.get_execution(record.execution_id)
    assert not failed_record.outputs.get("resume_thread_missing")
    assert env.repo.read().agent_runtimes["backend"].work_lineage_thread_ids == {
        first["dispatch"]["work_lineage_id"]: NATIVE_A,
    }


@pytest.mark.parametrize("already_completed", [False, True])
def test_legacy_schema_and_poisoned_binding_recover_through_execution_receipt(
    native_env, already_completed
):
    env = native_env
    lease = _lease(env)
    record, job = _submit(env, lease)
    dispatch_id = lease["dispatch_id"]
    lineage = lease["dispatch"]["work_lineage_id"]
    # Recreate the old output contract and old schema only in this test database.
    record.outputs.pop("native_thread_id", None)
    record.outputs["thread_id"] = dispatch_id
    env.worker.execution_store.put(record)

    def legacy_binding(state):
        state.agent_runtimes["backend"].work_lineage_thread_ids[lineage] = dispatch_id
        return state

    env.repo.update(legacy_binding)
    with sqlite3.connect(env.repo.store.path) as connection:
        connection.execute("ALTER TABLE team_dispatches DROP COLUMN native_thread_id")
        connection.execute("ALTER TABLE team_dispatches DROP COLUMN native_thread_observed_at")
        connection.execute(
            "ALTER TABLE team_agent_runtimes DROP COLUMN work_lineage_thread_dispatch_ids_json"
        )
    env.repo = TeamStateRepository(env.admin.config.runtime_dir)
    env.runtime = TeamRuntimeService(env.repo)
    migrated = env.repo.read()
    assert migrated.agent_runtimes["backend"].work_lineage_thread_dispatch_ids == {}
    assert migrated.dispatches[dispatch_id].native_thread_id is None
    if already_completed:
        env.runtime.finalize_agent_dispatch_from_activity(
            session_id="session-backend",
            dispatch_id=dispatch_id,
        )
    job.logs = f"session id: {NATIVE_A}\nactual provider result\n"
    _complete(env, job)
    recovered = TeamStateRepository(env.admin.config.runtime_dir).read()
    assert recovered.dispatches[dispatch_id].native_thread_id == NATIVE_A
    assert recovered.dispatches[dispatch_id].native_thread_observed_at is not None
    assert recovered.agent_runtimes["backend"].work_lineage_thread_ids == {lineage: NATIVE_A}
    assert recovered.agent_runtimes["backend"].work_lineage_thread_dispatch_ids == {
        lineage: dispatch_id
    }
