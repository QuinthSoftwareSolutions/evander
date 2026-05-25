from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_sandbox.config import GatewayConfig
from agent_sandbox.directories import DirectoryService, DirectoryStore
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


def _write_communication_targets(
    runtime_dir: Path,
    targets: list[dict[str, object]],
    *,
    directory_id: str = "root",
) -> None:
    repository = SqliteCommunicationTargetsRepository(communication_targets_db_path(runtime_dir))
    repository.write_overlay(directory_id, {"targets": targets})


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


def test_reload_preserves_custom_directory_store_for_communication_triggers(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    project_dir = tmp_path
    directories_store_path = tmp_path / "state" / "directories.json"
    ops = DirectoryService(DirectoryStore(directories_store_path)).create(
        parent="root", name="ops"
    )
    _write_communication_targets(
        runtime_dir,
        [
            {
                "id": "ops-teams",
                "provider": "teams",
                "kind": "teams",
                "address": "team/channel",
                "policy": {"capabilities": ["read"]},
            }
        ],
        directory_id=ops.id,
    )
    path = _config_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "poll_interval_seconds": 2,
                "triggers": [
                    {
                        "id": "comm-1",
                        "display_name": "Communication",
                        "type": "trigger:communication:new-message",
                        "directory_path": "root/ops",
                        "communication_target_ids": ["ops-teams"],
                    }
                ],
                "edges": [],
            }
        ),
        encoding="utf-8",
    )
    base_config = GatewayConfig(
        runtime_dir=runtime_dir,
        directories_store_path=directories_store_path,
    )
    initial_config = load_trigger_orchestrator_worker_global_config(
        project_dir, base_config=base_config
    )
    reloader = TriggerOrchestratorWorkerGlobalConfigReloader(
        project_dir=project_dir,
        initial_config=initial_config,
        base_config=base_config,
    )

    path.write_text(
        json.dumps(
            {
                "poll_interval_seconds": 13,
                "triggers": [
                    {
                        "id": "comm-1",
                        "display_name": "Communication",
                        "type": "trigger:communication:new-message",
                        "directory_path": "root/ops",
                        "communication_target_ids": ["ops-teams"],
                    }
                ],
                "edges": [],
            }
        ),
        encoding="utf-8",
    )

    latest, changed = reloader.reload_now()

    assert changed is True
    assert latest.poll_interval_seconds == 13


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
                "graphs": [
                    {
                        "id": "graph-synthetic-trivial",
                        "name": "Synthetic graph",
                        "directory_paths": ["root/team-a"],
                        "user_ids": ["alice"],
                        "node_ids": [
                            "trigger-cron-synthetic",
                            "effect-log-synthetic",
                        ],
                    }
                ],
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
    assert [graph.id for graph in config.workflow_graphs] == ["graph-synthetic-trivial"]
    assert config.workflow_graphs[0].directory_paths == ["root/team-a"]


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


def test_load_config_preserves_editor_workflow_graph_metadata(tmp_path: Path) -> None:
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
    assert [graph.id for graph in config.workflow_graphs] == ["graph-cron"]
    assert config.workflow_graphs[0].directory_paths == ["root"]
    assert config.workflow_graphs[0].user_ids == ["alice"]
    assert config.workflow_graphs[0].node_ids == ["trigger-cron"]


def test_load_config_preserves_inline_workflow_graph_metadata_without_inline_ir(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    project_dir = tmp_path
    path = _config_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "workflow_graphs": [
                    {
                        "id": "graph-inline-only",
                        "name": "Inline-only graph",
                        "description": "Runtime graph metadata without inline IR.",
                        "directory_paths": "root/team-a",
                        "user_ids": "alice",
                        "node_ids": "trigger-cron",
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

    assert [graph.id for graph in config.workflow_graphs] == ["graph-inline-only"]
    assert config.workflow_graphs[0].directory_paths == ["root/team-a"]
    assert config.workflow_graphs[0].user_ids == ["alice"]
    assert config.workflow_graphs[0].node_ids == ["trigger-cron"]


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
    assert config.workflow_graphs == []
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


def test_load_config_exposes_workflow_pack_graph_metadata(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    project_dir = tmp_path
    path = _config_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    packs_dir = project_dir / "config" / "workflow-packs"
    packs_dir.mkdir(parents=True, exist_ok=True)
    (packs_dir / "graph-pack.v1.json").write_text(
        json.dumps(
            {
                "pack_id": "graph-pack",
                "pack_version": "v1",
                "graphs": [
                    {
                        "id": "graph-synthetic",
                        "name": "Synthetic graph",
                        "description": "Pack-level workflow graph metadata.",
                        "directory_paths": "root/team-a",
                        "user_ids": "alice",
                        "node_ids": "trigger-cron-synthetic",
                    }
                ],
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
        json.dumps({"workflow_packs": ["graph-pack.v1.json"]}),
        encoding="utf-8",
    )

    config = load_trigger_orchestrator_worker_global_config(
        project_dir, base_config=GatewayConfig(runtime_dir=runtime_dir)
    )

    assert [graph.id for graph in config.workflow_graphs] == ["graph-synthetic"]
    assert config.workflow_graphs[0].directory_paths == ["root/team-a"]
    assert config.workflow_graphs[0].user_ids == ["alice"]
    assert config.workflow_graphs[0].node_ids == ["trigger-cron-synthetic"]


def test_load_config_preserves_state_workflow_graph_metadata(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    project_dir = tmp_path
    path = _config_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "workflow_graphs": [
                    {
                        "id": "graph-state",
                        "name": "State graph",
                        "description": "Persisted state graph metadata.",
                        "directory_paths": ["root/team-a"],
                        "node_ids": [
                            "trigger-state",
                            "effect-state-update",
                            "query-state-get",
                        ],
                    }
                ],
                "triggers": [
                    {
                        "id": "trigger-state",
                        "display_name": "State trigger",
                        "type": "trigger:cron",
                        "expression": "* * * * *",
                    }
                ],
                "effects": [
                    {
                        "id": "effect-state-update",
                        "display_name": "Update state",
                        "type": "effect:state:update",
                        "namespace": "workflow",
                        "key": "cursor",
                        "operation": "set",
                        "value": {"counter": 1},
                    }
                ],
                "queries": [
                    {
                        "id": "query-state-get",
                        "display_name": "Read state",
                        "type": "query:state:get",
                        "namespace": "workflow",
                        "key": "cursor",
                    }
                ],
                "edges": [
                    {"source_id": "trigger-state", "target_id": "effect-state-update"},
                    {"source_id": "effect-state-update", "target_id": "query-state-get"},
                ],
            }
        ),
        encoding="utf-8",
    )

    config = load_trigger_orchestrator_worker_global_config(
        project_dir, base_config=GatewayConfig(runtime_dir=runtime_dir)
    )

    assert [graph.id for graph in config.workflow_graphs] == ["graph-state"]
    assert config.workflow_graphs[0].directory_paths == ["root/team-a"]
    assert config.workflow_graphs[0].node_ids == [
        "trigger-state",
        "effect-state-update",
        "query-state-get",
    ]


def test_load_config_rejects_duplicate_state_workflow_graph_ids(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    project_dir = tmp_path
    path = _config_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "workflow_graphs": [
                    {
                        "id": "graph-state",
                        "name": "Alpha state graph",
                        "directory_paths": ["root/team-a"],
                        "node_ids": ["trigger-alpha", "effect-state-alpha"],
                    },
                    {
                        "id": "graph-state",
                        "name": "Beta state graph",
                        "directory_paths": ["root/team-a"],
                        "node_ids": ["trigger-beta", "effect-state-beta"],
                    },
                ],
                "triggers": [
                    {
                        "id": "trigger-alpha",
                        "display_name": "Alpha state trigger",
                        "type": "trigger:cron",
                        "expression": "* * * * *",
                    },
                    {
                        "id": "trigger-beta",
                        "display_name": "Beta state trigger",
                        "type": "trigger:cron",
                        "expression": "*/5 * * * *",
                    },
                ],
                "effects": [
                    {
                        "id": "effect-state-alpha",
                        "display_name": "Update alpha state",
                        "type": "effect:state:update",
                        "namespace": "workflow",
                        "key": "cursor",
                        "operation": "set",
                        "value": {"graph": "alpha"},
                    },
                    {
                        "id": "effect-state-beta",
                        "display_name": "Update beta state",
                        "type": "effect:state:update",
                        "namespace": "workflow",
                        "key": "cursor",
                        "operation": "set",
                        "value": {"graph": "beta"},
                    },
                ],
                "edges": [
                    {"source_id": "trigger-alpha", "target_id": "effect-state-alpha"},
                    {"source_id": "trigger-beta", "target_id": "effect-state-beta"},
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Duplicate workflow graph id: graph-state"):
        load_trigger_orchestrator_worker_global_config(
            project_dir, base_config=GatewayConfig(runtime_dir=runtime_dir)
        )


@pytest.mark.parametrize(
    ("workflow_graphs", "message"),
    [
        (
            [],
            "Workflow node effect-state-update requires persisted workflow graph metadata",
        ),
        (
            [
                {
                    "id": "graph-state",
                    "name": "State graph",
                    "node_ids": ["trigger-state", "effect-state-update"],
                }
            ],
            "Workflow graph graph-state node_ids must exactly match the connected component",
        ),
        (
            [
                {
                    "id": "graph-state",
                    "name": "State graph",
                    "node_ids": [
                        "trigger-state",
                        "effect-state-update",
                        "missing-node",
                    ],
                }
            ],
            "references unknown node IDs: missing-node",
        ),
    ],
)
def test_load_config_requires_persisted_state_workflow_graph_metadata(
    tmp_path: Path, workflow_graphs: list[dict[str, object]], message: str
) -> None:
    runtime_dir = tmp_path / "runtime"
    project_dir = tmp_path
    path = _config_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "workflow_graphs": workflow_graphs,
                "triggers": [
                    {
                        "id": "trigger-state",
                        "display_name": "State trigger",
                        "type": "trigger:cron",
                        "expression": "* * * * *",
                    }
                ],
                "effects": [
                    {
                        "id": "effect-state-update",
                        "display_name": "Update state",
                        "type": "effect:state:update",
                        "namespace": "workflow",
                        "key": "cursor",
                        "operation": "set",
                        "value": {"counter": 1},
                    }
                ],
                "queries": [
                    {
                        "id": "query-state-get",
                        "display_name": "Read state",
                        "type": "query:state:get",
                        "namespace": "workflow",
                        "key": "cursor",
                    }
                ],
                "edges": [
                    {"source_id": "trigger-state", "target_id": "effect-state-update"},
                    {"source_id": "effect-state-update", "target_id": "query-state-get"},
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=message):
        load_trigger_orchestrator_worker_global_config(
            project_dir, base_config=GatewayConfig(runtime_dir=runtime_dir)
        )


def test_load_config_strips_legacy_system_nodes_from_configured_pack(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    project_dir = tmp_path
    path = _config_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    packs_dir = project_dir / "config" / "workflow-packs"
    packs_dir.mkdir(parents=True, exist_ok=True)
    (packs_dir / "legacy-system-nodes.v1.json").write_text(
        json.dumps(
            {
                "pack_id": "legacy-system-nodes",
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
                        {
                            "id": "system.chat_assistant",
                            "display_name": "Stale pack chat assistant",
                            "type": "trigger:system:chat-assistant",
                        },
                        {
                            "id": "system.chat_assistant.enqueue_prompt",
                            "display_name": "Stale pack enqueue",
                            "type": "effect:agent:enqueue-prompt",
                            "target": "stale",
                            "prompt": "stale",
                        },
                    ],
                    "edges": [
                        {
                            "source_id": "trigger-cron-synthetic",
                            "target_id": "effect-log-synthetic",
                        },
                        {
                            "source_id": "system.chat_assistant",
                            "target_id": "system.chat_assistant.enqueue_prompt",
                        },
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    path.write_text(
        json.dumps({"workflow_packs": ["legacy-system-nodes.v1.json"]}),
        encoding="utf-8",
    )

    config = load_trigger_orchestrator_worker_global_config(
        project_dir, base_config=GatewayConfig(runtime_dir=runtime_dir)
    )

    trigger = next(trigger for trigger in config.triggers if trigger.id == "system.chat_assistant")
    enqueue = next(
        effect for effect in config.effects if effect.id == "system.chat_assistant.enqueue_prompt"
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
    assert trigger.display_name == "System Chat Assistant"
    assert enqueue.display_name == "Enqueue chat assistant prompt"
    assert [(edge.source_id, edge.target_id) for edge in config.edges] == [
        ("trigger-cron-synthetic", "effect-log-synthetic"),
        ("system.chat_assistant", "system.chat_assistant.ensure_session"),
        (
            "system.chat_assistant.ensure_session",
            "system.chat_assistant.enqueue_prompt",
        ),
    ]


def test_load_config_strips_legacy_system_nodes_from_inline_workflow_ir(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    project_dir = tmp_path
    path = _config_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "workflow_ir": {
                    "version": "v1",
                    "nodes": [
                        {
                            "id": "trigger-cron-inline",
                            "display_name": "Inline tick",
                            "type": "trigger:cron",
                            "expression": "* * * * *",
                        },
                        {
                            "id": "effect-log-inline",
                            "display_name": "Inline script",
                            "type": "effect:bash:script",
                            "script_path": "/bin/echo",
                            "args": ["inline-workflow"],
                        },
                        {
                            "id": "system.chat_assistant",
                            "display_name": "Stale inline chat assistant",
                            "type": "trigger:system:chat-assistant",
                        },
                    ],
                    "edges": [
                        {"source_id": "trigger-cron-inline", "target_id": "effect-log-inline"},
                        {"source_id": "system.chat_assistant", "target_id": "effect-log-inline"},
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    config = load_trigger_orchestrator_worker_global_config(
        project_dir, base_config=GatewayConfig(runtime_dir=runtime_dir)
    )

    trigger = next(trigger for trigger in config.triggers if trigger.id == "system.chat_assistant")
    assert _node_ids(config.triggers) == [
        "trigger-cron-inline",
        "system.chat_assistant",
    ]
    assert trigger.display_name == "System Chat Assistant"
    assert [(edge.source_id, edge.target_id) for edge in config.edges] == [
        ("trigger-cron-inline", "effect-log-inline"),
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


def test_load_config_accepts_read_capable_communication_targets(
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
                "policy": {"capabilities": ["read"]},
            },
            {
                "id": "discord-ops",
                "provider": "discord",
                "kind": "discord",
                "address": "123456789012345678",
                "policy": {"capabilities": ["read"]},
            },
        ],
    )
    path = _config_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "triggers": [
                    {
                        "id": "comm-1",
                        "display_name": "Communication",
                        "type": "trigger:communication:new-message",
                        "communication_target_ids": ["teams-primary", "discord-ops"],
                    }
                ],
                "edges": [],
            }
        ),
        encoding="utf-8",
    )

    config = load_trigger_orchestrator_worker_global_config(
        project_dir, base_config=GatewayConfig(runtime_dir=runtime_dir)
    )

    trigger = next(trigger for trigger in config.triggers if trigger.id == "comm-1")
    assert trigger.communication_target_ids == ["teams-primary", "discord-ops"]


@pytest.mark.parametrize(
    ("targets", "error_match"),
    [
        (
            [
                {
                    "id": "teams-notify",
                    "provider": "teams",
                    "kind": "teams",
                    "address": "team/channel",
                    "policy": {"capabilities": ["notify"]},
                }
            ],
            "read capability",
        ),
        (
            [
                {
                    "id": "email-primary",
                    "provider": "email",
                    "kind": "email",
                    "address": "ops@example.com",
                    "policy": {"capabilities": ["read"]},
                }
            ],
            "only teams and discord are supported",
        ),
        (
            [
                {
                    "id": "teams-user",
                    "provider": "teams",
                    "kind": "teams",
                    "address": "user:operator@example.com",
                    "policy": {"capabilities": ["read"]},
                }
            ],
            "Teams user target",
        ),
    ],
)
def test_load_config_rejects_invalid_communication_targets_for_triggers(
    tmp_path: Path,
    targets: list[dict[str, object]],
    error_match: str,
) -> None:
    runtime_dir = tmp_path / "runtime"
    project_dir = tmp_path
    _write_communication_targets(runtime_dir, targets)
    path = _config_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "triggers": [
                    {
                        "id": "comm-1",
                        "display_name": "Communication",
                        "type": "trigger:communication:new-message",
                        "communication_target_ids": [targets[0]["id"]],
                    }
                ],
                "edges": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=error_match):
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
