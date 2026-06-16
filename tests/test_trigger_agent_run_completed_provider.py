from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from agent_sandbox.config import GatewayConfig
from agent_sandbox.models import VmSummary
from agent_sandbox.triggers.providers.agent_run_completed import AgentRunCompletedTriggerProvider
from agent_sandbox.triggers.providers.base import TriggerRuntimeContext


@dataclass
class _AdminServiceStub:
    responses: list[list[VmSummary]]
    calls: list[dict[str, object]]

    def list_vms(self, **kwargs):
        self.calls.append(kwargs)
        if self.responses:
            return self.responses.pop(0)
        return []


def _runtime_context(tmp_path: Path, admin_service: _AdminServiceStub) -> TriggerRuntimeContext:
    return TriggerRuntimeContext(
        resolved_config=GatewayConfig(runtime_dir=tmp_path / "runtime"),
        admin_service=admin_service,
        env={},
        metadata={"directory_path": "root/team-a"},
    )


def _trigger_definition() -> SimpleNamespace:
    return SimpleNamespace(
        id="agent-run-complete",
        display_name="Agent completed",
        type="trigger:agent:run:completed",
        vm_user="agent",
    )


def test_agent_run_completed_emits_once_on_running_to_idle_transition(tmp_path: Path) -> None:
    updated_at = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)
    admin_service = _AdminServiceStub(
        responses=[
            [
                VmSummary(
                    vm_name="lab-vm-1",
                    session_ids=["session-1"],
                    labels={"workflow": "issue-worker", "agent_role": "issue"},
                    directory_paths=["root/team-a"],
                    libvirt_state="running",
                    ip_address="192.0.2.15",
                    agent_state="running",
                    last_state_update_at=datetime(2026, 4, 5, 11, 59, tzinfo=UTC),
                )
            ],
            [
                VmSummary(
                    vm_name="lab-vm-1",
                    session_ids=["session-1"],
                    labels={"workflow": "issue-worker", "agent_role": "issue"},
                    directory_paths=["root/team-a"],
                    libvirt_state="running",
                    ip_address="192.0.2.15",
                    agent_state="idle",
                    last_state_update_at=updated_at,
                    dispatch_id="dispatch-123",
                )
            ],
            [
                VmSummary(
                    vm_name="lab-vm-1",
                    session_ids=["session-1"],
                    labels={"workflow": "issue-worker", "agent_role": "issue"},
                    directory_paths=["root/team-a"],
                    libvirt_state="running",
                    ip_address="192.0.2.15",
                    agent_state="idle",
                    last_state_update_at=updated_at,
                )
            ],
        ],
        calls=[],
    )
    provider = AgentRunCompletedTriggerProvider()
    provider.initialize(_trigger_definition(), _runtime_context(tmp_path, admin_service))

    first = provider.poll()
    second = provider.poll()
    cursor_payload = json.loads(
        (tmp_path / "runtime" / "trigger-cursors-agent-run-completed.json").read_text(
            encoding="utf-8"
        )
    )
    assert cursor_payload["agent-run-complete"]["seen_by_vm"]["lab-vm-1"] == {
        "last_agent_state": "running",
        "last_state_update_at": "2026-04-05T11:59:00Z",
    }

    assert first == []
    assert len(second) == 1
    provider.acknowledge_published_event(second[0])
    third = provider.poll()
    assert third == []
    event = second[0]
    assert event.trigger_type == "trigger:agent:run:completed"
    assert event.payload["vm_name"] == "lab-vm-1"
    assert event.payload["agent_state"] == "idle"
    assert event.payload["session_ids"] == ["session-1"]
    assert event.payload["labels"] == {"workflow": "issue-worker", "agent_role": "issue"}
    assert event.payload["last_state_update_at"] == "2026-04-05T12:00:00Z"
    assert event.payload["dispatch_id"] == "dispatch-123"
    assert admin_service.calls[0]["directory_path"] == "root/team-a"
    assert admin_service.calls[0]["vm_user"] == "agent"
    cursor_payload = json.loads(
        (tmp_path / "runtime" / "trigger-cursors-agent-run-completed.json").read_text(
            encoding="utf-8"
        )
    )
    assert cursor_payload["agent-run-complete"]["seen_by_vm"]["lab-vm-1"] == {
        "last_agent_state": "idle",
        "last_state_update_at": "2026-04-05T12:00:00Z",
        "last_dispatch_id": "dispatch-123",
        "last_emitted_state_update_at": "2026-04-05T12:00:00Z",
        "last_emitted_dispatch_id": "dispatch-123",
    }


def test_agent_run_completed_emits_distinct_idle_dispatches_for_same_vm(
    tmp_path: Path,
) -> None:
    first_completed_at = datetime(2026, 4, 5, 12, 0, tzinfo=UTC)
    second_completed_at = datetime(2026, 4, 5, 12, 0, 20, tzinfo=UTC)
    admin_service = _AdminServiceStub(
        responses=[
            [
                VmSummary(
                    vm_name="team-manager",
                    session_ids=["session-manager"],
                    labels={"team": "starship"},
                    directory_paths=["root/team-a"],
                    libvirt_state="running",
                    ip_address="192.0.2.20",
                    agent_state="running",
                    last_state_update_at=datetime(2026, 4, 5, 11, 59, 50, tzinfo=UTC),
                    dispatch_id="wake-1-team-manager",
                )
            ],
            [
                VmSummary(
                    vm_name="team-manager",
                    session_ids=["session-manager"],
                    labels={"team": "starship"},
                    directory_paths=["root/team-a"],
                    libvirt_state="running",
                    ip_address="192.0.2.20",
                    agent_state="idle",
                    last_state_update_at=first_completed_at,
                    dispatch_id="wake-1-team-manager",
                )
            ],
            [
                VmSummary(
                    vm_name="team-manager",
                    session_ids=["session-manager"],
                    labels={"team": "starship"},
                    directory_paths=["root/team-a"],
                    libvirt_state="running",
                    ip_address="192.0.2.20",
                    agent_state="idle",
                    last_state_update_at=second_completed_at,
                    dispatch_id="team-dispatch-1",
                )
            ],
            [
                VmSummary(
                    vm_name="team-manager",
                    session_ids=["session-manager"],
                    labels={"team": "starship"},
                    directory_paths=["root/team-a"],
                    libvirt_state="running",
                    ip_address="192.0.2.20",
                    agent_state="idle",
                    last_state_update_at=second_completed_at,
                    dispatch_id="team-dispatch-1",
                )
            ],
        ],
        calls=[],
    )
    provider = AgentRunCompletedTriggerProvider()
    provider.initialize(_trigger_definition(), _runtime_context(tmp_path, admin_service))

    assert provider.poll() == []
    first = provider.poll()
    assert len(first) == 1
    assert first[0].payload["dispatch_id"] == "wake-1-team-manager"
    assert "wake-1-team-manager" in first[0].event_id
    provider.acknowledge_published_event(first[0])

    second = provider.poll()
    assert len(second) == 1
    assert second[0].payload["dispatch_id"] == "team-dispatch-1"
    assert "team-dispatch-1" in second[0].event_id
    provider.acknowledge_published_event(second[0])

    assert provider.poll() == []
    cursor_payload = json.loads(
        (tmp_path / "runtime" / "trigger-cursors-agent-run-completed.json").read_text(
            encoding="utf-8"
        )
    )
    assert cursor_payload["agent-run-complete"]["seen_by_vm"]["team-manager"] == {
        "last_agent_state": "idle",
        "last_state_update_at": "2026-04-05T12:00:20Z",
        "last_dispatch_id": "team-dispatch-1",
        "last_emitted_state_update_at": "2026-04-05T12:00:20Z",
        "last_emitted_dispatch_id": "team-dispatch-1",
    }
