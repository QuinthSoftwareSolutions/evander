from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_sandbox.config import GatewayConfig
from agent_sandbox.messaging.targets import (
    SqliteCommunicationTargetsRepository,
    communication_targets_db_path,
)
from agent_sandbox.workflow.runtime_config import (
    TriggerOrchestratorWorkerGlobalConfigReloader,
    load_trigger_orchestrator_worker_global_config,
)


def _config_path(project_dir: Path) -> Path:
    return project_dir / "config" / "trigger-orchestrator" / "config.json"


def _node_ids(nodes: object) -> list[str]:
    return [str(getattr(node, "id")) for node in nodes]


def _write_communication_targets(runtime_dir: Path, targets: list[dict[str, object]]) -> None:
    repository = SqliteCommunicationTargetsRepository(communication_targets_db_path(runtime_dir))
    repository.write_overlay("root", {"targets": targets})


def _write_system_pack(packs_dir: Path, *, enqueue_display_name: str) -> None:
    (packs_dir / "system.v1.json").write_text(
        json.dumps(
            {
                "pack_id": "system",
                "pack_version": "v1",
                "ir": {
                    "version": "v1",
                    "nodes": [
                        {
                            "id": "system.chat_assistant",
                            "display_name": "System Chat Assistant",
                            "type": "trigger:system:chat-assistant",
                        },
                        {
                            "id": "system.chat_assistant.ensure_session",
                            "display_name": "Resolve chat assistant session",
                            "type": "effect:chat-assistant:ensure-session",
                            "user_id": "${{system.chat_assistant.user_id}}",
                            "thread_id": "${{system.chat_assistant.thread_id}}",
                            "user_message_id": "${{system.chat_assistant.user_message_id}}",
                            "content": "${{system.chat_assistant.content}}",
                            "backend": "${{system.chat_assistant.backend}}",
                            "assistant_capability_mode": (
                                "${{system.chat_assistant.assistant_capability_mode}}"
                            ),
                            "assistant_scope": "admin_chat",
                        },
                        {
                            "id": "system.chat_assistant.enqueue_prompt",
                            "display_name": enqueue_display_name,
                            "type": "effect:agent:enqueue-prompt",
                            "target": "${{system.chat_assistant.ensure_session.session_id}}",
                            "session_id": "${{system.chat_assistant.ensure_session.session_id}}",
                            "vm_name": "${{system.chat_assistant.ensure_session.vm_name}}",
                            "prompt": "${{system.chat_assistant.ensure_session.prompt}}",
                            "model": "${{system.chat_assistant.ensure_session.model}}",
                            "vm_user": "agent",
                            "resume_thread_id": (
                                "${{system.chat_assistant.ensure_session.agent_thread_id}}"
                            ),
                            "queue_key": (
                                "chat:${{system.chat_assistant.ensure_session.thread_id}}:"
                                "${{system.chat_assistant.ensure_session.user_message_id}}"
                            ),
                            "execution_backend": (
                                "${{system.chat_assistant.ensure_session.execution_backend}}"
                            ),
                        },
                    ],
                    "edges": [
                        {
                            "source_id": "system.chat_assistant",
                            "target_id": "system.chat_assistant.ensure_session",
                        },
                        {
                            "source_id": "system.chat_assistant.ensure_session",
                            "target_id": "system.chat_assistant.enqueue_prompt",
                        },
                    ],
                },
            }
        ),
        encoding="utf-8",
    )


def test_loaded_worker_config_defaults_to_sqlite_orchestration_store(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    project_dir = tmp_path
    config = load_trigger_orchestrator_worker_global_config(
        project_dir, base_config=GatewayConfig(runtime_dir=runtime_dir)
    )

    assert config.orchestration_store_backend == "sqlite"
    assert config.orchestration_store_path == runtime_dir / "orchestration.db"
    assert "orchestration_store_backend" in config.model_fields_set


def test_reload_uses_last_known_good_config_when_new_payload_is_invalid(
    tmp_path: Path, caplog
) -> None:
    runtime_dir = tmp_path / "runtime"
    project_dir = tmp_path
    path = _config_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"poll_interval_seconds": 5}), encoding="utf-8")
    base_config = GatewayConfig(runtime_dir=runtime_dir)
    initial_config = load_trigger_orchestrator_worker_global_config(
        project_dir, base_config=base_config
    )
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
    initial_config = load_trigger_orchestrator_worker_global_config(
        project_dir, base_config=base_config
    )
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
    initial_config = load_trigger_orchestrator_worker_global_config(
        project_dir, base_config=base_config
    )
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


def test_get_latest_detects_referenced_workflow_pack_change(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    project_dir = tmp_path
    path = _config_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    packs_dir = project_dir / "config" / "workflow-packs"
    packs_dir.mkdir(parents=True, exist_ok=True)
    pack_path = packs_dir / "synthetic.v1.json"
    pack_path.write_text(
        json.dumps(
            {
                "pack_id": "synthetic",
                "pack_version": "v1",
                "ir": {
                    "version": "v1",
                    "nodes": [
                        {
                            "id": "trigger-old",
                            "display_name": "Trigger old",
                            "type": "trigger:cron",
                            "expression": "* * * * *",
                        },
                        {
                            "id": "effect-old",
                            "display_name": "Effect old",
                            "type": "effect:agent:prompt",
                            "prompt": "old",
                        },
                    ],
                    "edges": [{"source_id": "trigger-old", "target_id": "effect-old"}],
                },
            }
        ),
        encoding="utf-8",
    )
    path.write_text(json.dumps({"workflow_packs": ["synthetic.v1.json"]}), encoding="utf-8")
    base_config = GatewayConfig(runtime_dir=runtime_dir)
    initial_config = load_trigger_orchestrator_worker_global_config(
        project_dir, base_config=base_config
    )
    reloader = TriggerOrchestratorWorkerGlobalConfigReloader(
        project_dir=project_dir,
        initial_config=initial_config,
        base_config=base_config,
    )

    pack_path.write_text(
        json.dumps(
            {
                "pack_id": "synthetic",
                "pack_version": "v1",
                "ir": {
                    "version": "v1",
                    "nodes": [
                        {
                            "id": "trigger-new",
                            "display_name": "Trigger new",
                            "type": "trigger:cron",
                            "expression": "*/5 * * * *",
                        },
                        {
                            "id": "effect-new",
                            "display_name": "Effect new",
                            "type": "effect:agent:prompt",
                            "prompt": "new",
                        },
                    ],
                    "edges": [{"source_id": "trigger-new", "target_id": "effect-new"}],
                },
            }
        ),
        encoding="utf-8",
    )

    latest, changed = reloader.get_latest()

    assert changed is True
    assert _node_ids(latest.triggers) == ["trigger-new", "system.chat_assistant"]
    assert _node_ids(latest.effects) == [
        "effect-new",
        "system.chat_assistant.ensure_session",
        "system.chat_assistant.enqueue_prompt",
    ]


def test_get_latest_detects_hidden_system_workflow_pack_change(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    project_dir = tmp_path
    path = _config_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    packs_dir = project_dir / "config" / "workflow-packs"
    packs_dir.mkdir(parents=True, exist_ok=True)
    _write_system_pack(packs_dir, enqueue_display_name="Enqueue chat assistant prompt")
    path.write_text(json.dumps({"runtime_dir": str(runtime_dir)}), encoding="utf-8")
    base_config = GatewayConfig(runtime_dir=runtime_dir)
    initial_config = load_trigger_orchestrator_worker_global_config(
        project_dir, base_config=base_config
    )
    reloader = TriggerOrchestratorWorkerGlobalConfigReloader(
        project_dir=project_dir,
        initial_config=initial_config,
        base_config=base_config,
    )

    _write_system_pack(packs_dir, enqueue_display_name="Enqueue updated chat assistant prompt")

    latest, changed = reloader.get_latest()

    enqueue_effect = next(
        effect for effect in latest.effects if effect.id == "system.chat_assistant.enqueue_prompt"
    )
    assert changed is True
    assert enqueue_effect.display_name == "Enqueue updated chat assistant prompt"


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
                "workflow_transforms": ["await_agent_completion"],
            }
        ),
        encoding="utf-8",
    )

    config = load_trigger_orchestrator_worker_global_config(
        project_dir, base_config=GatewayConfig(runtime_dir=runtime_dir)
    )

    assert any(trigger.type == "trigger:agent:run:completed" for trigger in config.triggers)
    await_gate = next(gate for gate in config.gates if gate.type == "gate:await-agent")
    assert await_gate.completion_trigger_id == "__await-agent-completion__"


def test_load_config_composes_workflow_pack_ir(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    project_dir = tmp_path
    path = _config_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    packs_dir = project_dir / "config" / "workflow-packs"
    packs_dir.mkdir(parents=True, exist_ok=True)
    (packs_dir / "synthetic-trivial.v1.json").write_text(
        json.dumps(
            {
                "pack_id": "synthetic-trivial",
                "pack_version": "v1",
                "ir": {
                    "version": "v1",
                    "nodes": [
                        {
                            "id": "trigger-cron-synthetic",
                            "display_name": "Synthetic tick",
                            "type": "trigger:cron",
                            "expression": "* * * * *",
                        },
                        {
                            "id": "effect-log-synthetic",
                            "display_name": "Synthetic script",
                            "type": "effect:bash:script",
                            "script_path": "/bin/echo",
                            "args": ["synthetic-workflow"],
                        },
                    ],
                    "edges": [
                        {"source_id": "trigger-cron-synthetic", "target_id": "effect-log-synthetic"}
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    path.write_text(json.dumps({"workflow_packs": ["synthetic-trivial.v1.json"]}), encoding="utf-8")

    config = load_trigger_orchestrator_worker_global_config(
        project_dir, base_config=GatewayConfig(runtime_dir=runtime_dir)
    )

    assert _node_ids(config.triggers) == [
        "trigger-cron-synthetic",
        "system.chat_assistant",
    ]
    assert _node_ids(config.effects) == [
        "effect-log-synthetic",
        "system.chat_assistant.ensure_session",
        "system.chat_assistant.enqueue_prompt",
    ]
    assert [(edge.source_id, edge.target_id) for edge in config.edges] == [
        ("trigger-cron-synthetic", "effect-log-synthetic"),
        ("system.chat_assistant", "system.chat_assistant.ensure_session"),
        (
            "system.chat_assistant.ensure_session",
            "system.chat_assistant.enqueue_prompt",
        ),
    ]


def test_load_config_composes_hidden_system_pack_without_operator_packs(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    project_dir = tmp_path
    path = _config_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"runtime_dir": str(runtime_dir)}), encoding="utf-8")

    config = load_trigger_orchestrator_worker_global_config(
        project_dir, base_config=GatewayConfig(runtime_dir=runtime_dir)
    )

    assert _node_ids(config.triggers) == ["system.chat_assistant"]
    assert _node_ids(config.effects) == [
        "system.chat_assistant.ensure_session",
        "system.chat_assistant.enqueue_prompt",
    ]
    assert [(edge.source_id, edge.target_id) for edge in config.edges] == [
        ("system.chat_assistant", "system.chat_assistant.ensure_session"),
        (
            "system.chat_assistant.ensure_session",
            "system.chat_assistant.enqueue_prompt",
        ),
    ]


def test_load_config_ignores_editor_workflow_graph_metadata(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    project_dir = tmp_path
    path = _config_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "workflow_graphs": [
                    {
                        "id": "graph-cron",
                        "name": "Cron graph",
                        "description": "Editor-only graph metadata.",
                        "directory_paths": ["root"],
                        "user_ids": ["alice"],
                        "node_ids": ["trigger-cron"],
                    }
                ],
                "triggers": [
                    {
                        "id": "trigger-cron",
                        "display_name": "Cron",
                        "type": "trigger:cron",
                        "expression": "* * * * *",
                    }
                ],
                "effects": [],
                "edges": [],
            }
        ),
        encoding="utf-8",
    )

    config = load_trigger_orchestrator_worker_global_config(
        project_dir, base_config=GatewayConfig(runtime_dir=runtime_dir)
    )

    assert _node_ids(config.triggers) == ["trigger-cron", "system.chat_assistant"]


def test_load_config_composes_configured_pack_with_hidden_system_pack(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    project_dir = tmp_path
    path = _config_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    packs_dir = project_dir / "config" / "workflow-packs"
    packs_dir.mkdir(parents=True, exist_ok=True)
    (packs_dir / "agent-container-worker.v1.json").write_text(
        json.dumps(
            {
                "pack_id": "agent-container-worker",
                "pack_version": "v1",
                "ir": {
                    "version": "v1",
                    "nodes": [
                        {
                            "id": "trigger-cron-synthetic",
                            "display_name": "Synthetic tick",
                            "type": "trigger:cron",
                            "expression": "* * * * *",
                        },
                        {
                            "id": "effect-log-synthetic",
                            "display_name": "Synthetic script",
                            "type": "effect:bash:script",
                            "script_path": "/bin/echo",
                            "args": ["synthetic-workflow"],
                        },
                    ],
                    "edges": [
                        {"source_id": "trigger-cron-synthetic", "target_id": "effect-log-synthetic"}
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    path.write_text(
        json.dumps({"workflow_packs": ["agent-container-worker.v1.json"]}),
        encoding="utf-8",
    )

    config = load_trigger_orchestrator_worker_global_config(
        project_dir, base_config=GatewayConfig(runtime_dir=runtime_dir)
    )

    assert _node_ids(config.triggers) == [
        "trigger-cron-synthetic",
        "system.chat_assistant",
    ]
    assert _node_ids(config.effects) == [
        "effect-log-synthetic",
        "system.chat_assistant.ensure_session",
        "system.chat_assistant.enqueue_prompt",
    ]
    assert [(edge.source_id, edge.target_id) for edge in config.edges] == [
        ("trigger-cron-synthetic", "effect-log-synthetic"),
        ("system.chat_assistant", "system.chat_assistant.ensure_session"),
        (
            "system.chat_assistant.ensure_session",
            "system.chat_assistant.enqueue_prompt",
        ),
    ]


def test_load_config_uses_hidden_system_pack_instead_of_legacy_top_level_system_nodes(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    project_dir = tmp_path
    path = _config_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "runtime_dir": str(runtime_dir),
                "triggers": [
                    {
                        "id": "system.chat_assistant",
                        "display_name": "Stale Chat Assistant",
                        "type": "trigger:system:chat-assistant",
                    }
                ],
                "effects": [
                    {
                        "id": "system.chat_assistant.enqueue_prompt",
                        "display_name": "Stale enqueue",
                        "type": "effect:agent:enqueue-prompt",
                        "target": "stale",
                        "prompt": "stale",
                    }
                ],
                "edges": [
                    {
                        "source_id": "system.chat_assistant",
                        "target_id": "system.chat_assistant.enqueue_prompt",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    config = load_trigger_orchestrator_worker_global_config(
        project_dir, base_config=GatewayConfig(runtime_dir=runtime_dir)
    )

    trigger = next(trigger for trigger in config.triggers if trigger.id == "system.chat_assistant")
    enqueue = next(
        effect for effect in config.effects if effect.id == "system.chat_assistant.enqueue_prompt"
    )
    assert trigger.display_name == "System Chat Assistant"
    assert enqueue.display_name == "Enqueue chat assistant prompt"
    assert enqueue.config["queue_key"] == (
        "chat:${{system.chat_assistant.ensure_session.thread_id}}:"
        "${{system.chat_assistant.ensure_session.user_message_id}}"
    )


def test_load_config_rejects_unknown_communication_target_ref(tmp_path: Path) -> None:
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
                        "communication_target_id": "missing-target",
                    }
                ],
                "edges": [{"source_id": "cron-1", "target_id": "effect-1"}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError, match=r"unknown communication target reference\(s\): missing-target"
    ):
        load_trigger_orchestrator_worker_global_config(
            project_dir, base_config=GatewayConfig(runtime_dir=runtime_dir)
        )


def test_load_config_accepts_notification_effect_communication_target_ref(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    project_dir = tmp_path
    _write_communication_targets(
        runtime_dir,
        [
            {
                "id": "teams-primary",
                "provider": "teams",
                "kind": "teams",
                "address": "team/channel",
                "policy": {"capabilities": ["notify"]},
            }
        ],
    )
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
                        "id": "notify-1",
                        "display_name": "Notify",
                        "type": "effect:notification:send",
                        "communication_target_id": "teams-primary",
                        "message": "Done",
                    }
                ],
                "edges": [{"source_id": "cron-1", "target_id": "notify-1"}],
            }
        ),
        encoding="utf-8",
    )

    config = load_trigger_orchestrator_worker_global_config(
        project_dir, base_config=GatewayConfig(runtime_dir=runtime_dir)
    )

    assert config.effects[0].type == "effect:notification:send"


def test_load_config_accepts_communication_send_message_target_ref(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    project_dir = tmp_path
    _write_communication_targets(
        runtime_dir,
        [
            {
                "id": "teams-primary",
                "provider": "teams",
                "kind": "teams",
                "address": "chat:chat-1",
                "policy": {"capabilities": ["write"]},
            }
        ],
    )
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
                        "id": "send-message-1",
                        "display_name": "Send message",
                        "type": "effect:communication:send-message",
                        "communication_target_id": "teams-primary",
                        "message": "Done",
                    }
                ],
                "edges": [{"source_id": "cron-1", "target_id": "send-message-1"}],
            }
        ),
        encoding="utf-8",
    )

    config = load_trigger_orchestrator_worker_global_config(
        project_dir, base_config=GatewayConfig(runtime_dir=runtime_dir)
    )

    assert config.effects[0].type == "effect:communication:send-message"
