from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agent_sandbox.effects.providers.agent_enqueue_prompt import AgentEnqueuePromptEffectProvider
from agent_sandbox.effects.providers.base import EffectRuntimeContext
from agent_sandbox.events.provider_models import (
    AgentEnqueuePromptEffectDefinition,
    IssueGetQueryDefinition,
    VmRunStateQueryDefinition,
)
from agent_sandbox.models import SessionManifest, ToolName, VmSummary
from agent_sandbox.queries.providers.issue_get import IssueGetQueryProvider
from agent_sandbox.queries.providers.vm_run_state import VmRunStateQueryProvider
from agent_sandbox.queries.providers.base import QueryRuntimeContext


@dataclass
class _FakeEffectServices:
    resumed: list[dict[str, Any]] = field(default_factory=list)

    def resume_agent(self, **kwargs: Any) -> Any:
        self.resumed.append(kwargs)
        return type("ResumeResult", (), {"success": True, "thread_id": "thread-1"})()

    def list_vms(self, **kwargs: Any) -> list[VmSummary]:
        del kwargs
        return [
            VmSummary(
                vm_name="vm-1",
                session_ids=["session-1"],
                libvirt_state="shut off",
                agent_state="stopped",
            )
        ]

    def read_all_sessions(self):
        return {
            "session-1": SessionManifest(
                session_id="session-1",
                vm_id="vm-1",
                allowed_tools=[ToolName.GET_AGENT_STATUS],
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        }


def test_effect_provider_can_run_with_only_contract_interfaces(tmp_path: Path) -> None:
    services = _FakeEffectServices()
    provider = AgentEnqueuePromptEffectProvider()
    provider.initialize(
        AgentEnqueuePromptEffectDefinition(
            id="effect-1",
            display_name="enqueue",
            type="effect:agent:enqueue-prompt",
            session_id="session-1",
            prompt="hello",
        ),
        EffectRuntimeContext(
            provider_config={"runtime_dir": tmp_path},
            agent_resumer=services,
            vm_inventory=services,
            session_lookup=services,
        ),
    )

    output = provider.execute()

    assert output["success"] is True
    assert services.resumed and services.resumed[0]["target"] == "session-1"


@dataclass
class _FakeQueryServices:
    def list_vms(self, **kwargs: Any) -> list[VmSummary]:
        del kwargs
        return [
            VmSummary(
                vm_name="vm-2",
                session_ids=["session-2"],
                libvirt_state="running",
                agent_state="omitted",
            )
        ]

    def get_issue(self, **kwargs: Any) -> dict[str, Any]:
        return {"issue_id": kwargs["issue_id"], "title": "Synthetic issue"}


def test_query_providers_can_run_with_fake_contract_implementations() -> None:
    services = _FakeQueryServices()

    vm_provider = VmRunStateQueryProvider()
    vm_provider.initialize(
        VmRunStateQueryDefinition(
            id="query-vm",
            display_name="vm",
            type="query:vm:run-state",
            session_id="session-2",
        ),
        QueryRuntimeContext(provider_config={}, vm_inventory=services),
    )
    vm_output = vm_provider.execute(resolved_config={"session_id": "session-2"})
    assert vm_output["state_code"] == "running"

    issue_provider = IssueGetQueryProvider()
    issue_provider.initialize(
        IssueGetQueryDefinition(
            id="query-issue",
            display_name="issue",
            type="query:issue:get",
            issue_id="root:issue-7",
        ),
        QueryRuntimeContext(provider_config={}, issue_reader=services),
    )
    issue_output = issue_provider.execute()
    assert issue_output["issue_id"] == "root:issue-7"
