from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from config_helpers import canonical_worker_config
from evander.config import GatewayConfig
from evander.daemons.agent_completion_watcher import build_agent_run_completed_event
from evander.daemons.effect_orchestrator import EffectExecutionRecord, EffectOrchestratorWorker
from evander.effects.providers.agent_prompt import AgentPromptEffectProvider
from evander.execution import ExecutionStatusKind
from evander.models import VmSummary


@pytest.fixture
def make_worker(tmp_path):
    workers = []
    calls = []

    def create(*, selectors=None, directory="root/team-a", trigger_ids=("agent-completed",)):
        nodes = []
        edges = []
        graphs = []
        for trigger_id in trigger_ids:
            effect_id = f"{trigger_id}-follow-up"
            nodes.extend(
                [
                    {
                        "id": trigger_id,
                        "display_name": "Agent completed",
                        "type": "trigger:agent:run:completed",
                        **(selectors or {}),
                    },
                    {
                        "id": effect_id,
                        "display_name": "Follow up",
                        "type": "effect:agent:prompt",
                        "session_id": f"${{{{{trigger_id}.scope.session_id}}}}",
                        "prompt": f"Completed ${{{{{trigger_id}.dispatch_id}}}}",
                    },
                ]
            )
            edges.append({"source_id": trigger_id, "target_id": effect_id})
            graphs.append(
                {
                    "id": f"graph-{trigger_id}",
                    "name": "Completion graph",
                    "directory_path": directory,
                    "node_ids": [trigger_id, effect_id],
                }
            )
        runtime_dir = tmp_path / "runtime"
        config = canonical_worker_config(
            {
                "runtime_dir": runtime_dir,
                "poll_interval_seconds": 1,
                "jitter_seconds": 0,
                "triggers": [node for node in nodes if node["type"].startswith("trigger:")],
                "effects": [node for node in nodes if node["type"].startswith("effect:")],
                "edges": edges,
                "workflow_graphs": graphs,
            }
        )
        worker = EffectOrchestratorWorker(
            config=GatewayConfig(runtime_dir=runtime_dir),
            worker_config=config,
            runtime=None,
        )

        def build(effect, **kwargs):
            def execute(*, resolved_config=None):
                calls.append((effect.id, resolved_config))
                return {"success": True}

            return SimpleNamespace(execute=execute)

        worker.provider_factory = SimpleNamespace(build=build)
        workers.append(worker)
        return worker, calls

    yield create
    for worker in workers:
        worker._executor.shutdown(wait=True)


def _summary():
    return VmSummary(
        vm_name="vm-1",
        session_ids=["session-1"],
        directory_paths=["root/team-a"],
        libvirt_state="running",
        agent_state="idle",
        dispatch_id="dispatch-1",
        last_state_update_at=datetime.now(UTC),
    )


def _completion():
    return build_agent_run_completed_event(
        trigger_id="__await-agent-completion__",
        dispatch_id="dispatch-1",
        vm_user="agent",
        summary=_summary(),
        session_id="session-1",
        directory_path="root/team-a",
    )


def _drain(worker):
    for _ in range(6):
        worker.run_once()
        worker._reap_completed_futures()


@pytest.mark.parametrize(
    ("selectors", "directory", "expected"),
    [
        ({}, "root/team-a", True),
        ({}, "root", True),
        ({}, "root/team-b", False),
        ({}, "root/team", False),
        ({"directory_path": "root/team-b"}, "root/team-a", False),
        ({"directory_path": "root/team-a"}, "root/team-b", True),
        ({"enabled": False}, "root/team-a", False),
        ({"vm_name": "vm-1", "session_id": "session-1", "vm_user": "agent"}, "root/team-a", True),
        (
            {"vm_name": " vm-1 ", "session_id": " session-1 ", "vm_user": " agent "},
            "root/team-a",
            True,
        ),
        ({"vm_name": "other-vm"}, "root/team-a", False),
        ({"session_id": "other-session"}, "root/team-a", False),
        ({"vm_user": "other-user"}, "root/team-a", False),
    ],
)
def test_monitor_completion_routes_only_to_matching_enabled_trigger(
    make_worker, selectors, directory, expected
):
    worker, calls = make_worker(selectors=selectors, directory=directory)
    event = _completion()
    worker.event_queue.publish(event)
    _drain(worker)
    assert len(calls) == int(expected)
    if expected:
        assert calls[0][1]["session_id"] == "session-1"
        assert calls[0][1]["prompt"] == "Completed dispatch-1"
        forwarded = [
            record
            for record in worker.event_queue.read_all().values()
            if record.trigger_id == "agent-completed"
        ]
        assert len(forwarded) == 1
        assert forwarded[0].trace_id == event.trace_id
        assert forwarded[0].payload == event.payload


def test_completion_with_missing_directory_does_not_reach_scoped_trigger(make_worker):
    worker, calls = make_worker()
    event = _completion()
    event.payload.pop("directory_paths")
    event.payload["scope"].pop("directory_path")
    worker.event_queue.publish(event)
    _drain(worker)
    assert calls == []


def test_completion_matches_configured_non_default_vm_user(make_worker):
    worker, calls = make_worker(selectors={"vm_user": "operator"})
    event = _completion()
    event.payload["scope"]["vm_user"] = "operator"
    worker.event_queue.publish(event)
    _drain(worker)
    assert len(calls) == 1


def test_completion_publication_recovers_after_partial_failure_and_restart(
    make_worker, monkeypatch
):
    trigger_ids = ("first-completed", "second-completed")
    worker, calls = make_worker(trigger_ids=trigger_ids)
    event = _completion()
    source = worker.event_queue.publish(event)
    publish = worker.event_queue.publish

    def fail_second(event, **kwargs):
        if event.trigger_id == "second-completed":
            raise OSError("simulated queue write failure")
        return publish(event, **kwargs)

    monkeypatch.setattr(worker.event_queue, "publish", fail_second)
    with pytest.raises(OSError, match="simulated queue write failure"):
        worker._process_event_record(
            source, graph=worker._build_execution_graph(), config_revision="test"
        )
    restarted, _ = make_worker(trigger_ids=trigger_ids)
    _drain(restarted)
    # Another observer can publish a later event for the same completed dispatch.
    restarted.event_queue.publish(
        event.model_copy(
            update={
                "event_id": "another-completion-observer",
                "occurred_at": event.occurred_at + timedelta(seconds=1),
            }
        )
    )
    _drain(restarted)
    assert sorted(effect_id for effect_id, _ in calls) == [
        f"{item}-follow-up" for item in trigger_ids
    ]
    forwarded = [
        record
        for record in restarted.event_queue.read_all().values()
        if record.trigger_id in trigger_ids
    ]
    assert len(forwarded) == 2
    assert all(record.delivery_state == "delivered" for record in forwarded)


@pytest.mark.parametrize("backend", ["libvirt_vm", "container_worker_vm"])
def test_reconciled_completion_is_durable_before_prompt_finalization(
    make_worker, monkeypatch, backend
):
    worker, calls = make_worker()
    now = datetime.now(UTC)
    record = EffectExecutionRecord(
        execution_id="prompt-execution",
        idempotency_key="prompt-execution",
        event_id="prompt-start",
        trace_id="prompt-trace",
        source_node_id="prompt-request",
        source_trigger_type="trigger:cron",
        effect_id="owned-prompt",
        effect_type="effect:agent:prompt",
        status="waiting",
        started_at=now,
        finished_at=now,
        resolved_config_hash="test",
        resolved_config={
            "session_id": "session-1",
            "vm_name": "vm-1",
            "directory_path": "root/team-a",
        },
        outputs={
            "dispatch_id": "dispatch-1",
            "session_id": "session-1",
            "execution_id": "job-1",
            "execution_backend": backend,
            "success": True,
        },
    )
    worker.execution_store.put(record)
    if backend == "libvirt_vm":
        monkeypatch.setattr(
            "evander.effects.providers.agent_prompt.list_vm_summaries_for_agent_reconciliation",
            lambda worker: [_summary()],
        )
    else:
        container = SimpleNamespace(
            inspect=lambda execution_id: SimpleNamespace(
                status=ExecutionStatusKind.STOPPED, message="done", metadata={}
            ),
            get_log_snapshot=lambda execution_id, lines: "Completed the task.",
            destroy=lambda execution_id: None,
        )
        monkeypatch.setattr(
            AgentPromptEffectProvider,
            "_container_backend_for_worker",
            classmethod(lambda cls, **kwargs: container),
        )

    # Simulate a crash after the completion was queued but before the waiting
    # execution's final state was stored. A restart must still deliver the trigger.
    def fail_finalization(record):
        raise OSError("crash")

    with monkeypatch.context() as patch:
        patch.setattr(worker.execution_store, "put", fail_finalization)
        with pytest.raises(OSError, match="crash"):
            AgentPromptEffectProvider.reconcile_waiting(worker=worker)
    assert len(worker.event_queue.list_pending(trigger_id="__await-agent-completion__")) == 1
    assert worker.execution_store.read_all()["prompt-execution"].status == "waiting"
    assert AgentPromptEffectProvider.reconcile_waiting(worker=worker) == 1
    # Reconciliation and the monitor must share one durable dispatch identity.
    assert len(worker.event_queue.list_pending(trigger_id="__await-agent-completion__")) == 1
    restarted, _ = make_worker()
    _drain(restarted)
    assert len(calls) == 1
    assert calls[0][1]["session_id"] == "session-1"
    assert worker.execution_store.read_all()["prompt-execution"].status == "succeeded"


def test_forwarded_completion_does_not_inherit_source_workflow_routing(make_worker):
    worker, calls = make_worker()
    event = _completion()
    event.payload.update(
        {
            "__branch_port": "error",
            "__resume_target_node_id": "other-effect",
            "__node_outputs": {"private-upstream": {"value": "private"}},
        }
    )
    worker.event_queue.publish(event)
    _drain(worker)
    assert len(calls) == 1
    forwarded = next(
        record
        for record in worker.event_queue.read_all().values()
        if record.trigger_id == "agent-completed"
    )
    assert not any(key.startswith("__") for key in forwarded.payload)
