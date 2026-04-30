from __future__ import annotations

import json
from pathlib import Path

from agent_sandbox.config import OrchestrationCoreConfig
from agent_sandbox.workflow.packs import compose_workflow_ir


def test_compose_workflow_ir_merges_selected_packs(tmp_path: Path) -> None:
    packs_dir = tmp_path / "config" / "workflow-packs"
    packs_dir.mkdir(parents=True, exist_ok=True)

    (packs_dir / "pack-a.v1.json").write_text(
        json.dumps(
            {
                "pack_id": "pack-a",
                "pack_version": "v1",
                "ir": {
                    "version": "v1",
                    "nodes": [
                        {
                            "id": "trigger-a",
                            "display_name": "tick",
                            "type": "trigger:cron",
                            "expression": "* * * * *",
                        }
                    ],
                    "edges": [],
                },
            }
        ),
        encoding="utf-8",
    )
    (packs_dir / "pack-b.v1.json").write_text(
        json.dumps(
            {
                "pack_id": "pack-b",
                "pack_version": "v1",
                "ir": {
                    "version": "v1",
                    "nodes": [
                        {
                            "id": "effect-b",
                            "display_name": "echo",
                            "type": "effect:bash:script",
                            "script_path": "/bin/echo",
                        }
                    ],
                    "edges": [{"source_id": "trigger-a", "target_id": "effect-b"}],
                },
            }
        ),
        encoding="utf-8",
    )

    composed = compose_workflow_ir(project_dir=tmp_path, workflow_packs=["pack-a.v1", "pack-b.v1"])

    assert [trigger["id"] for trigger in composed.triggers] == ["trigger-a"]
    assert [effect["id"] for effect in composed.effects] == ["effect-b"]
    assert composed.edges == [
        {
            "source_id": "trigger-a",
            "target_id": "effect-b",
            "source_port": None,
            "allow_cycle": False,
        }
    ]


def test_compose_workflow_ir_accepts_inline_overlay_with_ir_metadata(tmp_path: Path) -> None:
    composed = compose_workflow_ir(
        project_dir=tmp_path,
        workflow_packs=[],
        inline_ir={
            "version": "v1",
            "nodes": [
                {
                    "node": {
                        "id": "trigger-inline",
                        "display_name": "tick",
                        "type": "trigger:cron",
                        "expression": "* * * * *",
                    },
                    "bindings": {"example": "${{trigger-inline.expression}}"},
                    "retry": {"max_attempts": 3, "backoff_seconds": 1.0},
                    "idempotency": {"key_template": "${{system.event_id}}::trigger-inline"},
                }
            ],
            "edges": [],
        },
    )

    assert [trigger["id"] for trigger in composed.triggers] == ["trigger-inline"]


def test_container_worker_pack_validates_ticket_created_agent_branch() -> None:
    project_dir = Path(__file__).resolve().parents[1]

    default = compose_workflow_ir(
        project_dir=project_dir,
        workflow_packs=["agent-default.v1-example"],
    )
    container = compose_workflow_ir(
        project_dir=project_dir,
        workflow_packs=["agent-container-worker.v1-example"],
    )

    default_effects = {effect["id"]: effect for effect in default.effects}
    container_effects = {effect["id"]: effect for effect in container.effects}
    assert default_effects["effect-vm-create-1"]["type"] == "effect:vm:create"
    assert container_effects["effect-agent-create-1"]["type"] == "effect:agent:create"
    assert container_effects["effect-agent-clone-repo-1"]["type"] == "effect:agent:clone-repo"
    assert (
        container_effects["effect-agent-bootstrap-frontend-1"]["type"]
        == "effect:agent:bootstrap-frontend"
    )
    assert container_effects["effect-agent-copy-files-1"]["type"] == "effect:agent:copy-files"
    assert (
        container_effects["effect-agent-prompt-4"]["config"]["target"]
        == "${{effect-agent-clone-repo-1.session_id}}"
    )
    assert (
        container_effects["effect-agent-enqueue-prompt-1"]["type"]
        == "effect:agent:enqueue-prompt"
    )
    assert container_effects["effect-agent-enqueue-prompt-2"]["type"] == "effect:agent:prompt"
    assert (
        container_effects["effect-agent-enqueue-prompt-2"]["config"]["output_mode"]
        == "on_completion"
    )
    assert not any(effect["type"].startswith("effect:vm:") for effect in container.effects)

    assert container_effects["effect-agent-create-2"]["type"] == "effect:agent:create"
    assert (
        container_effects["effect-agent-create-2"]["config"]["execution_backend"]
        == "container_worker_vm"
    )
    assert (
        container_effects["effect-agent-clone-repo-2"]["type"] == "effect:agent:clone-repo"
    )
    assert (
        container_effects["effect-agent-clone-repo-2"]["config"]["target"]
        == "${{effect-agent-create-2.session_id}}"
    )
    assert container_effects["effect-agent-copy-files-5"]["type"] == "effect:agent:copy-files"
    assert (
        container_effects["effect-agent-copy-files-5"]["config"]["target"]
        == "${{effect-agent-clone-repo-2.session_id}}"
    )
    assert container_effects["effect-agent-exec-1"]["type"] == "effect:agent:exec"
    assert (
        container_effects["effect-agent-exec-1"]["config"]["target"]
        == "${{effect-agent-clone-repo-2.session_id}}"
    )
    assert (
        container_effects["effect-agent-exec-1"]["config"]["working_directory"]
        == "${{effect-agent-clone-repo-2.target_dir}}"
    )
    assert (
        container_effects["effect-agent-bootstrap-frontend-2"]["type"]
        == "effect:agent:bootstrap-frontend"
    )
    assert (
        container_effects["effect-agent-bootstrap-frontend-2"]["config"]["target"]
        == "${{effect-agent-clone-repo-2.session_id}}"
    )
    assert container_effects["effect-agent-copy-files-4"]["type"] == "effect:agent:copy-files"
    assert (
        container_effects["effect-agent-copy-files-4"]["config"]["target"]
        == "${{effect-agent-clone-repo-2.session_id}}"
    )
    assert (
        container_effects["effect-agent-prompt-5"]["config"]["target"]
        == "${{effect-agent-clone-repo-2.session_id}}"
    )
    assert (
        "${{effect-agent-clone-repo-2.target_dir}}/REVIEW_TASK.yaml"
        in container_effects["effect-agent-prompt-5"]["config"]["prompt"]
    )

    all_node_ids = {
        *(trigger["id"] for trigger in container.triggers),
        *(gate["id"] for gate in container.gates),
        *(effect["id"] for effect in container.effects),
        *(query["id"] for query in container.queries),
    }
    for edge in container.edges:
        assert edge["source_id"] in all_node_ids
        assert edge["target_id"] in all_node_ids

    layout_path = project_dir / "config" / "workflow-packs" / "agent-container-worker.v1.layout-example.json"
    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    assert set(layout["nodes"]).issubset(all_node_ids)
    for stale_node_id in {
        "effect-vm-create-2",
        "effect-vm-clone-repo-2",
        "effect-vm-copy-files-5",
        "effect-vm-exec-1",
        "effect-vm-bootstrap-frontend-2",
        "effect-vm-copy-files-4",
    }:
        assert stale_node_id not in layout["nodes"]

    OrchestrationCoreConfig.model_validate(
        {
            "runtime_dir": project_dir / "runtime",
            "triggers": container.triggers,
            "gates": container.gates,
            "effects": container.effects,
            "queries": container.queries,
            "edges": container.edges,
        }
    )
