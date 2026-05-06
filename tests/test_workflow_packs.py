from __future__ import annotations

import json
from pathlib import Path

from agent_sandbox.config import OrchestrationCoreConfig
from agent_sandbox.workflow.packs import compose_workflow_ir, load_workflow_pack


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


def test_workflow_pack_can_store_multiple_named_graphs(tmp_path: Path) -> None:
    packs_dir = tmp_path / "config" / "workflow-packs"
    packs_dir.mkdir(parents=True, exist_ok=True)
    (packs_dir / "multi.v1.json").write_text(
        json.dumps(
            {
                "pack_id": "multi",
                "pack_version": "v1",
                "graphs": [
                    {
                        "id": "graph-first",
                        "name": "First graph",
                        "description": "First connected component.",
                        "directory_paths": ["root"],
                        "user_ids": ["alice"],
                        "node_ids": ["trigger-a", "effect-a"],
                    },
                    {
                        "id": "graph-second",
                        "name": "Second graph",
                        "directory_paths": ["root/team-a"],
                        "user_ids": ["bob"],
                        "node_ids": ["trigger-b", "effect-b"],
                    },
                ],
                "ir": {
                    "version": "v1",
                    "nodes": [
                        {
                            "id": "trigger-a",
                            "display_name": "Trigger A",
                            "type": "trigger:cron",
                            "expression": "* * * * *",
                        },
                        {
                            "id": "effect-a",
                            "display_name": "Effect A",
                            "type": "effect:bash:script",
                            "script_path": "/bin/echo",
                        },
                        {
                            "id": "trigger-b",
                            "display_name": "Trigger B",
                            "type": "trigger:cron",
                            "expression": "*/5 * * * *",
                        },
                        {
                            "id": "effect-b",
                            "display_name": "Effect B",
                            "type": "effect:bash:script",
                            "script_path": "/bin/echo",
                        },
                    ],
                    "edges": [
                        {"source_id": "trigger-a", "target_id": "effect-a"},
                        {"source_id": "trigger-b", "target_id": "effect-b"},
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    pack = load_workflow_pack(tmp_path, "multi.v1")

    assert [graph.name for graph in pack.graphs] == ["First graph", "Second graph"]
    assert [graph.directory_paths for graph in pack.graphs] == [["root"], ["root/team-a"]]
    assert [graph.user_ids for graph in pack.graphs] == [["alice"], ["bob"]]
    assert pack.graphs[0].node_ids == ["trigger-a", "effect-a"]


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
    default_gates = {gate["id"]: gate for gate in default.gates}
    container_gates = {gate["id"]: gate for gate in container.gates}
    container_edges = {
        (edge["source_id"], edge["target_id"]): edge for edge in container.edges
    }
    default_edges = {(edge["source_id"], edge["target_id"]): edge for edge in default.edges}
    default_queries = {query["id"]: query for query in default.queries}
    container_queries = {query["id"]: query for query in container.queries}
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
    assert "output_mode" not in container_effects["effect-agent-enqueue-prompt-2"]["config"]
    assert (
        container_edges[("effect-agent-prompt-4", "query-issue-get-1")]["source_port"]
        == "completed"
    )
    assert (
        default_edges[("effect-agent-enqueue-prompt-1", "effect-session-update-metadata-3")][
            "source_port"
        ]
        == "started"
    )
    assert (
        container_edges[("effect-agent-enqueue-prompt-1", "effect-session-update-metadata-3")][
            "source_port"
        ]
        == "started"
    )
    assert (
        default_queries["query-git-pr-note-1"]["config"]["notes_ref"]
        == "${{trigger-git-push-1.notes_ref}}"
    )
    assert (
        container_queries["query-git-pr-note-1"]["config"]["notes_ref"]
        == "${{trigger-git-push-1.notes_ref}}"
    )
    assert default_gates["gate-filter-8"]["config"]["when"] == {
        "all": [{"equals": False, "left": "${{effect-pr-create-1.success}}"}]
    }
    assert container_gates["gate-filter-8"]["config"]["when"] == {
        "all": [{"equals": False, "left": "${{effect-pr-create-1.success}}"}]
    }
    assert (
        default_edges[("effect-pr-create-1", "gate-filter-8")]["target_id"]
        == "gate-filter-8"
    )
    assert (
        container_edges[("gate-filter-8", "effect-agent-enqueue-prompt-3")]["target_id"]
        == "effect-agent-enqueue-prompt-3"
    )
    assert (
        container_effects["effect-agent-enqueue-prompt-3"]["config"]["target"]
        == "${{trigger-git-push-1.session_id}}"
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
    assert (
        "timeout --preserve-status <seconds> <command>"
        in container_effects["effect-agent-prompt-1"]["config"]["prompt"]
    )
    assert (
        "dotnet restore --configfile ~/.nuget/NuGet/NuGet.Config"
        in container_effects["effect-agent-prompt-1"]["config"]["prompt"]
    )
    assert "--no-restore" in container_effects["effect-agent-prompt-1"]["config"]["prompt"]
    assert (
        "timeout --preserve-status <seconds> <command>"
        in container_effects["effect-agent-prompt-5"]["config"]["prompt"]
    )
    assert (
        "dotnet restore --configfile ~/.nuget/NuGet/NuGet.Config"
        in container_effects["effect-agent-prompt-5"]["config"]["prompt"]
    )
    assert "--no-restore" in container_effects["effect-agent-prompt-5"]["config"]["prompt"]

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
