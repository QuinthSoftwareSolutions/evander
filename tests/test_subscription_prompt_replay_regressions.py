from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from evander.config import GatewayConfig
from evander.effects.providers.agent_prompt import AgentPromptEffectProvider
from evander.execution import ExecutionStatusKind
from evander.messaging.agent_prompt_queue import AgentPromptQueueStore


@pytest.mark.parametrize("speed", [False, True, "ultrafast"])
def test_mid_turn_subscription_deferral_preserves_replay_metadata(tmp_path, speed) -> None:
    retry_at = datetime.now(UTC) + timedelta(minutes=10)
    record = SimpleNamespace(
        effect_id="effect-prompt",
        effect_type="effect:agent:enqueue-prompt",
        event_id="event-prompt",
        trace_id="synthetic-queue-trace",
        status="waiting",
        error=None,
        finished_at=None,
        resolved_config={
            "target": "session-1",
            "vm_name": "worker-target",
            "session_id": "session-1",
            "prompt": "Continue the approved workflow.",
            "model": "gpt-5.4",
            "reasoning_effort": None,
            "model_provider_mode": "openai_public",
            "queue_key": "effect-prompt:dispatch-1",
            "workflow_run_id": "workflow-run-1",
            "mcp_tool_approval_lease_id": "approval-lease-1",
        },
        input_payload={
            "context_retry_prompt": "Retry with less context.",
            "agent_context_retry_enabled": True,
            "agent_context_retry_max_attempts": 2,
            "agent_context_retry_target_chars": 32000,
            "operator_steering": "interrupt_and_queue",
            "operator_steering_note_id": "note-1",
            "wake_dispatch_id": "wake-dispatch-1",
            "request_id": "wake-request-1",
            "source": "scheduled_once",
            "subscription_replay_attempt": "3",
            "__node_outputs": {"trigger-1": {"request_id": "request-1"}},
            "__for_execution_ids": {"gate-1": "gate-execution-1"},
        },
        outputs={
            "dispatch_id": "dispatch-1",
            "session_id": "session-1",
            "execution_id": "worker-execution-1",
            "reasoning_effort": "low",
            "reasoning_effort_source": "session",
            "fast_mode": speed,
            "fast_mode_source": "prompt",
        },
    )
    stored_records: list[object] = []
    delivery_updates: list[str] = []
    destroyed: list[str] = []
    worker = SimpleNamespace(
        config=GatewayConfig(runtime_dir=tmp_path),
        execution_store=SimpleNamespace(put=stored_records.append),
        _update_event_delivery_state=delivery_updates.append,
    )
    backend = SimpleNamespace(destroy=destroyed.append)
    status = SimpleNamespace(
        status=ExecutionStatusKind.FAILED,
        metadata={
            "codex_subscription_deferral": {
                "profile_id": "team-a",
                "reason": "rate_limited",
                "retry_at": retry_at.isoformat(),
            }
        },
    )

    deferred = AgentPromptEffectProvider._defer_subscription_waiting_record(
        worker=worker,
        record=record,
        backend=backend,
        status=status,
        session_id="session-1",
        execution_id="worker-execution-1",
    )

    queued = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json").get(
        "effect-prompt:dispatch-1"
    )
    assert deferred is True
    assert queued is not None
    assert queued.reasoning_effort == "low"
    assert queued.reasoning_effort_source == "session"
    assert queued.fast_mode == speed
    assert queued.fast_mode_source == "prompt"
    assert queued.dispatch_id == "dispatch-1"
    assert queued.subscription_profile_id == "team-a"
    assert queued.retry_not_before == retry_at
    assert queued.metadata == {
        "workflow_run_id": "workflow-run-1",
        "mcp_tool_approval_lease_id": "approval-lease-1",
        "context_retry_prompt": "Retry with less context.",
        "agent_context_retry_enabled": "True",
        "agent_context_retry_max_attempts": "2",
        "agent_context_retry_target_chars": "32000",
        "operator_steering": "interrupt_and_queue",
        "operator_steering_note_id": "note-1",
        "wake_dispatch_id": "wake-dispatch-1",
        "request_id": "wake-request-1",
        "source": "scheduled_once",
        "subscription_replay_attempt": "4",
    }
    assert queued.workflow_context == {"trigger-1": {"request_id": "request-1"}}
    assert queued.workflow_for_execution_ids == {"gate-1": "gate-execution-1"}
    assert record.status == "succeeded"
    assert record.outputs["queued"] is True
    assert stored_records == [record]
    assert delivery_updates == ["event-prompt"]
    assert destroyed == ["worker-execution-1"]
