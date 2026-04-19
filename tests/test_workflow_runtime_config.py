from __future__ import annotations

import json
from pathlib import Path

from agent_sandbox.config import GatewayConfig
from agent_sandbox.workflow_runtime_config import (
    TriggerOrchestratorWorkerGlobalConfigReloader,
    load_trigger_orchestrator_worker_global_config,
)


def _config_path(project_dir: Path) -> Path:
    return project_dir / "config" / "trigger-orchestrator" / "config.json"


def test_reload_uses_last_known_good_config_when_new_payload_is_invalid(tmp_path: Path, caplog) -> None:
    runtime_dir = tmp_path / "runtime"
    project_dir = tmp_path
    path = _config_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"poll_interval_seconds": 5}), encoding="utf-8")
    base_config = GatewayConfig(runtime_dir=runtime_dir)
    initial_config = load_trigger_orchestrator_worker_global_config(project_dir, base_config=base_config)
    reloader = TriggerOrchestratorWorkerGlobalConfigReloader(
        project_dir=project_dir,
        initial_config=initial_config,
        base_config=base_config,
    )

    path.write_text("{not-json", encoding="utf-8")

    latest, changed = reloader.get_latest()

    assert changed is False
    assert latest.poll_interval_seconds == 5
    assert "keeping last-known-good" in caplog.text


def test_reload_now_applies_new_valid_payload(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    project_dir = tmp_path
    path = _config_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"poll_interval_seconds": 2}), encoding="utf-8")
    base_config = GatewayConfig(runtime_dir=runtime_dir)
    initial_config = load_trigger_orchestrator_worker_global_config(project_dir, base_config=base_config)
    reloader = TriggerOrchestratorWorkerGlobalConfigReloader(
        project_dir=project_dir,
        initial_config=initial_config,
        base_config=base_config,
    )

    path.write_text(json.dumps({"poll_interval_seconds": 11}), encoding="utf-8")

    latest, changed = reloader.reload_now()

    assert changed is True
    assert latest.poll_interval_seconds == 11


def test_get_latest_detects_config_file_replacement(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    project_dir = tmp_path
    path = _config_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"poll_interval_seconds": 2}), encoding="utf-8")
    base_config = GatewayConfig(runtime_dir=runtime_dir)
    initial_config = load_trigger_orchestrator_worker_global_config(project_dir, base_config=base_config)
    reloader = TriggerOrchestratorWorkerGlobalConfigReloader(
        project_dir=project_dir,
        initial_config=initial_config,
        base_config=base_config,
    )

    replacement = path.parent / ".config.json.tmp"
    replacement.write_text(json.dumps({"poll_interval_seconds": 9}), encoding="utf-8")
    replacement.replace(path)

    latest, changed = reloader.get_latest()

    assert changed is True
    assert latest.poll_interval_seconds == 9


def test_load_config_compiles_await_agent_templates_to_explicit_triggers(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    project_dir = tmp_path
    path = _config_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
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
        ),
        encoding="utf-8",
    )

    config = load_trigger_orchestrator_worker_global_config(project_dir, base_config=GatewayConfig(runtime_dir=runtime_dir))

    assert any(trigger.type == "trigger:agent:run:completed" for trigger in config.triggers)
    await_gate = next(gate for gate in config.gates if gate.type == "gate:await-agent")
    assert await_gate.completion_trigger_id == "__await-agent-completion__"
