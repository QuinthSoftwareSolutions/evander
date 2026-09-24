from __future__ import annotations

from config_helpers import canonical_worker_config

from pathlib import Path

import pytest

from evander.workflow.compiler import (
    collect_domain_validation_diagnostics,
    compile_trigger_orchestrator_config,
    compile_trigger_orchestrator_config_with_diagnostics,
)
def test_compile_trigger_orchestrator_config_is_valid_without_domain_transforms(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    worker_config = canonical_worker_config(
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
    assert "completion_trigger_id" not in await_gate.config


@pytest.mark.parametrize(
    ("effect_type", "display_name"),
    [
        pytest.param("effect:agent:prompt", "Prompt", id="prompt"),
        pytest.param("effect:agent:enqueue-prompt", "Enqueue", id="enqueue-prompt"),
    ],
)
@pytest.mark.parametrize("source_port", [None, "error"], ids=["unported", "error"])
def test_compile_agent_prompt_output_ports(
    tmp_path: Path, effect_type: str, display_name: str, source_port: str | None
) -> None:
    runtime_dir = tmp_path / "runtime"
    worker_config = canonical_worker_config(
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
                    "display_name": display_name,
                    "type": effect_type,
                    "session_id": "session-1",
                    "prompt": "Continue",
                },
                {
                    "id": "effect-2",
                    "display_name": "Update metadata",
                    "type": "effect:session:update-metadata",
                    ("target" if source_port is None else "session_id"): "session-1",
                    "metadata": {"done": True},
                },
            ],
            "edges": [
                {"source_id": "cron-1", "target_id": "effect-1"},
                {
                    "source_id": "effect-1",
                    "target_id": "effect-2",
                    **({} if source_port is None else {"source_port": source_port}),
                },
            ],
        }
    )

    _, diagnostics = compile_trigger_orchestrator_config_with_diagnostics(worker_config)

    if source_port is None:
        assert any(
            diagnostic.code == "node_capability"
            and f"{effect_type} effect-1 uses invalid source_port value(s): None"
            in diagnostic.message
            for diagnostic in diagnostics
        )
    else:
        assert diagnostics == []


def test_compile_trigger_orchestrator_config_with_diagnostics_reports_await_agent_domain_issue(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    worker_config = canonical_worker_config(
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
                    "display_name": "Exec",
                    "type": "effect:vm:exec",
                    "target": "vm-1",
                    "command": "echo hi",
                }
            ],
            "gates": [
                {
                    "id": "await-agent-1",
                    "display_name": "Await",
                    "type": "gate:await-agent",
                    "session_id": "s-1",
                }
            ],
            "edges": [
                {"source_id": "cron-1", "target_id": "effect-1"},
                {"source_id": "effect-1", "target_id": "await-agent-1"},
            ],
        }
    )

    _, diagnostics = compile_trigger_orchestrator_config_with_diagnostics(worker_config)

    assert any(item.code == "await_agent_upstream_type" for item in diagnostics)
    assert collect_domain_validation_diagnostics(worker_config)


def test_collect_domain_validation_diagnostics_reports_invalid_for_gate_port(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    worker_config = canonical_worker_config(
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
            "gates": [
                {"id": "for-1", "display_name": "Iterate", "type": "gate:for", "items": ["a", "b"]}
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Exec",
                    "type": "effect:vm:exec",
                    "target": "vm-1",
                    "command": "echo hi",
                }
            ],
            "edges": [
                {"source_id": "cron-1", "target_id": "for-1"},
                {"source_id": "for-1", "source_port": "other", "target_id": "effect-1"},
            ],
        }
    )

    diagnostics = collect_domain_validation_diagnostics(worker_config)

    assert any(item.code == "node_capability" for item in diagnostics)
    assert any("uses invalid source_port value(s): other" in item.message for item in diagnostics)


def test_collect_domain_validation_diagnostics_reports_body_for_gate_without_loopback(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    worker_config = canonical_worker_config(
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
            "gates": [
                {"id": "for-1", "display_name": "Iterate", "type": "gate:for", "items": ["a", "b"]}
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Exec",
                    "type": "effect:vm:exec",
                    "target": "vm-1",
                    "command": "echo hi",
                }
            ],
            "edges": [
                {"source_id": "cron-1", "target_id": "for-1"},
                {"source_id": "for-1", "source_port": "body", "target_id": "effect-1"},
            ],
        }
    )

    diagnostics = collect_domain_validation_diagnostics(worker_config)

    assert any(
        item.code == "node_capability"
        and item.node_id == "for-1"
        and "body branch must have an allow_cycle path back to for-1" in item.message
        for item in diagnostics
    )


def test_collect_domain_validation_diagnostics_reports_switch_port_issues(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    worker_config = canonical_worker_config(
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
            "gates": [
                {
                    "id": "gate-switch-1",
                    "display_name": "Route work",
                    "type": "gate:switch",
                    "default_port": "fallback",
                    "cases": [
                        {"port": "ready", "when": {"left": "ready", "equals": "ready"}},
                        {"port": "ready", "match": {"state": "ready"}},
                        {"port": "blocked", "match": {"state": "blocked"}},
                    ],
                }
            ],
            "effects": [
                {
                    "id": "effect-ready",
                    "display_name": "Ready",
                    "type": "effect:agent:prompt",
                    "session_id": "session-1",
                    "prompt": "ready",
                },
                {
                    "id": "effect-missing",
                    "display_name": "Missing port",
                    "type": "effect:agent:prompt",
                    "session_id": "session-1",
                    "prompt": "missing",
                },
                {
                    "id": "effect-invalid",
                    "display_name": "Invalid port",
                    "type": "effect:agent:prompt",
                    "session_id": "session-1",
                    "prompt": "invalid",
                },
                {
                    "id": "effect-error",
                    "display_name": "Error recovery",
                    "type": "effect:agent:prompt",
                    "session_id": "session-1",
                    "prompt": "recover",
                },
            ],
            "edges": [
                {"source_id": "cron-1", "target_id": "gate-switch-1"},
                {
                    "source_id": "gate-switch-1",
                    "target_id": "effect-ready",
                    "source_port": "ready",
                },
                {"source_id": "gate-switch-1", "target_id": "effect-missing"},
                {
                    "source_id": "gate-switch-1",
                    "target_id": "effect-invalid",
                    "source_port": "other",
                },
                {
                    "source_id": "gate-switch-1",
                    "target_id": "effect-error",
                    "source_port": "error",
                },
            ],
        }
    )

    diagnostics = collect_domain_validation_diagnostics(worker_config)

    assert any(
        item.code == "node_capability"
        and item.node_id == "gate-switch-1"
        and item.node_type == "gate:switch"
        and "outbound edges require source_port" in item.message
        for item in diagnostics
    )
    invalid_port_diagnostic = next(
        item
        for item in diagnostics
        if item.code == "node_capability"
        and item.node_id == "gate-switch-1"
        and item.node_type == "gate:switch"
        and "uses invalid source_port value(s): other" in item.message
    )
    assert "error" not in invalid_port_diagnostic.message


def test_collect_domain_validation_diagnostics_reports_switch_bad_inbound_count(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    worker_config = canonical_worker_config(
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
            "gates": [
                {
                    "id": "gate-switch-1",
                    "display_name": "Route work",
                    "type": "gate:switch",
                    "cases": [{"port": "ready", "when": {"left": "ready", "equals": "ready"}}],
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Effect one",
                    "type": "effect:agent:prompt",
                    "session_id": "session-1",
                    "prompt": "one",
                }
            ],
            "edges": [
                {"source_id": "cron-1", "target_id": "gate-switch-1"},
                {"source_id": "effect-1", "target_id": "gate-switch-1"},
            ],
        }
    )

    _, diagnostics = compile_trigger_orchestrator_config_with_diagnostics(worker_config)

    assert any(
        item.code == "node_capability"
        and "must have at most 1 inbound edge(s), found 2" in item.message
        for item in diagnostics
    )


def test_collect_domain_validation_diagnostics_rejects_validate_unknown_port(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    worker_config = canonical_worker_config(
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
            "gates": [
                {
                    "id": "validate-1",
                    "display_name": "Validate",
                    "type": "gate:validate",
                    "checks": [
                        {
                            "code": "candidate.present",
                            "message": "Candidate is required.",
                            "when": {"left": "candidate", "equals": "candidate"},
                        }
                    ],
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "session_id": "session-1",
                    "prompt": "continue",
                }
            ],
            "edges": [
                {"source_id": "cron-1", "target_id": "validate-1"},
                {
                    "source_id": "validate-1",
                    "source_port": "maybe",
                    "target_id": "effect-1",
                },
            ],
        }
    )

    diagnostics = collect_domain_validation_diagnostics(worker_config)

    assert any(
        item.code == "node_capability"
        and "gate:validate validate-1 uses invalid source_port value(s): maybe" in item.message
        for item in diagnostics
    )


def test_collect_domain_validation_diagnostics_allows_body_for_gate_with_loopback(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    worker_config = canonical_worker_config(
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
            "gates": [
                {"id": "for-1", "display_name": "Iterate", "type": "gate:for", "items": ["a", "b"]}
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Exec",
                    "type": "effect:vm:exec",
                    "target": "vm-1",
                    "command": "echo hi",
                }
            ],
            "edges": [
                {"source_id": "cron-1", "target_id": "for-1"},
                {"source_id": "for-1", "source_port": "body", "target_id": "effect-1"},
                {"source_id": "effect-1", "target_id": "for-1", "allow_cycle": True},
            ],
        }
    )

    diagnostics = collect_domain_validation_diagnostics(worker_config)

    assert not any(item.code == "node_capability" for item in diagnostics)
