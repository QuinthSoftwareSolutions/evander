from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from config_helpers import canonical_worker_config
from evander.config import GatewayConfig
from evander.daemons.effect_orchestrator import EffectOrchestratorWorker, EffectExecutionRecord
from evander.effects.providers.agent_prompt import AgentPromptEffectProvider
from evander.triggers.queue import TriggerQueueRecord


@pytest.mark.parametrize("effect_type", ["effect:agent:prompt", "effect:agent:enqueue-prompt"])
@pytest.mark.parametrize("outcome", ["success", "failure", "vm-failure", "timeout"])
def test_terminal_agent_outcome_continues_on_connected_branch(tmp_path, effect_type, outcome):
    config = canonical_worker_config(
        {
            "runtime_dir": tmp_path,
            "triggers": [
                {
                    "id": "start",
                    "display_name": "Start",
                    "type": "trigger:event",
                    "event_type": "test.start",
                }
            ],
            "effects": [
                {
                    "id": "prompt",
                    "display_name": "Prepare",
                    "type": effect_type,
                    "target": "review-17",
                    "prompt": "Prepare test data",
                },
                *[
                    {
                        "id": branch,
                        "display_name": branch,
                        "type": "effect:session:update-metadata",
                        "target": "review-17",
                        "metadata": {"branch": branch},
                    }
                    for branch in ("error", "completed")
                ],
            ],
            "edges": [
                {"source_id": "start", "target_id": "prompt"},
                *[
                    {"source_id": "prompt", "source_port": branch, "target_id": branch}
                    for branch in ("error", "completed")
                ],
            ],
        }
    )
    worker = EffectOrchestratorWorker(
        config=GatewayConfig(runtime_dir=tmp_path), worker_config=config, runtime=None
    )
    calls = []

    def build(node, **kwargs):
        def execute(**kwargs):
            calls.append(node.id)
            return {"success": True}

        return SimpleNamespace(execute=execute)

    worker.provider_factory = SimpleNamespace(build=build)
    now = datetime.now(UTC)
    record = EffectExecutionRecord(
        execution_id="run-prompt",
        idempotency_key="event:prompt",
        event_id="event",
        trace_id="trace",
        source_node_id="start",
        source_trigger_type="trigger:event",
        effect_id="prompt",
        effect_type=effect_type,
        status="waiting",
        started_at=now,
        finished_at=now,
        resolved_config_hash="hash",
        resolved_config={"target": "review-17"},
        input_payload={},
        outputs={"dispatch_id": "dispatch", "success": True, "session_id": "review-17"},
    )
    worker.execution_store.put(record)
    if outcome == "timeout":
        AgentPromptEffectProvider._fail_waiting_record(
            worker=worker,
            record=record,
            outputs={"success": False, "worker_status": "timeout"},
            error="Agent timed out",
        )
    else:
        completion = TriggerQueueRecord(
            event_id="completion",
            trigger_id="__await-agent-completion__",
            trigger_type="trigger:agent:run:completed",
            trace_id="trace",
            occurred_at=now,
            delivery_state="delivered",
            delivered_at=now,
            payload=(
                {"dispatch_id": "dispatch", "agent_state": "failed"}
                if outcome == "vm-failure"
                else {
                    "dispatch_id": "dispatch",
                    "success": outcome == "success",
                    "message": outcome,
                }
            ),
        )
        AgentPromptEffectProvider._finalize_waiting_record(
            worker=worker, record=record, queue_record=completion
        )
    for _ in range(5):
        worker.run_once()
    branch = "completed" if outcome == "success" else "error"
    assert calls == [branch]
    stored = worker.execution_store.get_execution("run-prompt")
    assert stored.status == ("succeeded" if outcome == "success" else "error_routed")
    assert stored.completion_event_branch_port == branch
    assert stored.outputs["success"] is (outcome == "success")
    if outcome != "success":
        assert stored.outputs["failed_phase"] == "execution"
