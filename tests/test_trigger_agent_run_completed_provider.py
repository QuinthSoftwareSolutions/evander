from __future__ import annotations

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
    third = provider.poll()

    assert first == []
    assert len(second) == 1
    assert third == []
    event = second[0]
    assert event.trigger_type == "trigger:agent:run:completed"
    assert event.payload["vm_name"] == "lab-vm-1"
    assert event.payload["agent_state"] == "idle"
    assert event.payload["session_ids"] == ["session-1"]
    assert event.payload["labels"] == {"workflow": "issue-worker", "agent_role": "issue"}
    assert event.payload["last_state_update_at"] == "2026-04-05T12:00:00Z"
    assert admin_service.calls[0]["directory_path"] == "root/team-a"
    assert admin_service.calls[0]["vm_user"] == "agent"
