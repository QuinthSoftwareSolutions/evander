from __future__ import annotations

from pathlib import Path

from agent_sandbox.config import TriggerOrchestratorWorkerGlobalConfig
from agent_sandbox.workflow_compiler import (
    collect_domain_validation_diagnostics,
    compile_trigger_orchestrator_config,
    compile_trigger_orchestrator_config_with_diagnostics,
)
from agent_sandbox.workflow_transforms.await_agent import augment_await_agent_completion


def test_compile_trigger_orchestrator_config_can_apply_await_agent_completion_transform(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": runtime_dir,
            "triggers": [
                {
                    "id": "cron-1",
                    "display_name": "Every minute",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "session_id": "session-1",
                    "prompt": "Continue",
                }
            ],
            "gates": [
                {
                    "id": "await-agent-1",
                    "display_name": "Await agent",
                    "type": "gate:await-agent",
                    "session_id": "session-1",
                    "outputs": {},
                }
            ],
            "edges": [
                {"source_id": "cron-1", "target_id": "effect-1"},
                {"source_id": "effect-1", "target_id": "await-agent-1"},
            ],
        }
    )

    compiled = compile_trigger_orchestrator_config(worker_config, transforms=[augment_await_agent_completion])

    assert len(compiled.triggers) == 2
    assert any(trigger.type == "trigger:agent:run:completed" for trigger in compiled.triggers)
    await_gate = next(gate for gate in compiled.gates if gate.type == "gate:await-agent")
    assert await_gate.completion_trigger_id == "__await-agent-completion__"


def test_compile_trigger_orchestrator_config_is_valid_without_domain_transforms(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": runtime_dir,
            "triggers": [],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "session_id": "session-1",
                    "prompt": "Continue",
                }
            ],
            "gates": [
                {
                    "id": "await-agent-1",
                    "display_name": "Await agent",
                    "type": "gate:await-agent",
                    "session_id": "session-1",
                    "outputs": {},
                }
            ],
            "edges": [{"source_id": "effect-1", "target_id": "await-agent-1"}],
        }
    )

    compiled = compile_trigger_orchestrator_config(worker_config)

    assert compiled == worker_config
    assert len(compiled.triggers) == 0
    await_gate = next(gate for gate in compiled.gates if gate.type == "gate:await-agent")
    assert await_gate.completion_trigger_id is None


def test_compile_trigger_orchestrator_config_with_diagnostics_reports_await_agent_domain_issue(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": runtime_dir,
            "triggers": [{"id": "cron-1", "display_name": "Every minute", "type": "trigger:cron", "expression": "* * * * *"}],
            "effects": [{"id": "effect-1", "display_name": "Exec", "type": "effect:vm:exec", "target": "vm-1", "command": "echo hi"}],
            "gates": [{"id": "await-agent-1", "display_name": "Await", "type": "gate:await-agent", "session_id": "s-1"}],
            "edges": [
                {"source_id": "cron-1", "target_id": "effect-1"},
                {"source_id": "effect-1", "target_id": "await-agent-1"},
            ],
        }
    )

    _, diagnostics = compile_trigger_orchestrator_config_with_diagnostics(worker_config)

    assert any(item.code == "await_agent_upstream_type" for item in diagnostics)
    assert collect_domain_validation_diagnostics(worker_config)
