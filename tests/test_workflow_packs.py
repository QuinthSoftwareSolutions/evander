from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from evander.admin.payloads import WorkflowEditorGraphMetadata
from evander.config import OrchestrationCoreConfig
from evander.workflow.contracts import WorkflowNodeEnvelope
from evander.workflow import packs as workflow_packs
from evander.workflow.packs import (
    BUILTIN_HIDDEN_WORKFLOW_PACKS,
    WorkflowPack,
    WorkflowPackGraph,
    compose_workflow_ir as _compose_workflow_ir,
    load_workflow_pack,
)
from config_helpers import canonical_workflow_payload


def test_workflow_node_envelope_rejects_top_level_config_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        WorkflowNodeEnvelope.model_validate(
            {
                "id": "trigger-flat",
                "display_name": "Flat trigger",
                "type": "trigger:cron",
                "expression": "* * * * *",
            }
        )

    node = WorkflowNodeEnvelope.model_validate(
        {
            "id": "trigger-nested",
            "display_name": "Nested trigger",
            "type": "trigger:cron",
            "config": {"expression": "* * * * *"},
        }
    )
    assert node.config == {"expression": "* * * * *"}


def test_workflow_pack_graph_reads_legacy_directory_paths_but_editor_rejects_it() -> None:
    legacy_graph = {
        "id": "graph-a",
        "name": "Graph A",
        "directory_paths": ["root/team-a"],
        "node_ids": ["trigger-a"],
    }
    pack_graph = WorkflowPackGraph.model_validate(legacy_graph)

    assert pack_graph.directory_path == "root/team-a"
    assert "directory_paths" not in pack_graph.model_dump(mode="json")
    with pytest.raises(ValidationError, match="directory_paths is no longer supported"):
        WorkflowEditorGraphMetadata.model_validate(legacy_graph)


def test_workflow_pack_graph_rejects_multiple_legacy_directories() -> None:
    with pytest.raises(ValidationError, match="must contain at most one directory"):
        WorkflowPackGraph.model_validate(
            {
                "id": "graph-a",
                "name": "Graph A",
                "directory_paths": ["root/team-a", "root/team-b"],
            }
        )


def compose_workflow_ir(**kwargs):
    inline_ir = kwargs.get("inline_ir")
    if isinstance(inline_ir, dict):
        kwargs["inline_ir"] = canonical_workflow_payload(inline_ir)
    return _compose_workflow_ir(**kwargs)


def _write_workflow_pack(tmp_path: Path, name: str, payload: dict) -> None:
    packs_dir = tmp_path / "config" / "workflow-packs"
    packs_dir.mkdir(parents=True, exist_ok=True)
    (packs_dir / name).write_text(
        json.dumps(canonical_workflow_payload(payload)),
        encoding="utf-8",
    )


def _canonicalize_pack_file(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(canonical_workflow_payload(payload)), encoding="utf-8")


def test_compose_workflow_ir_merges_selected_packs(tmp_path: Path) -> None:
    packs_dir = tmp_path / "config" / "workflow-packs"
    packs_dir.mkdir(parents=True, exist_ok=True)

    (packs_dir / "pack-a.v1.json").write_text(
        json.dumps(
            {
                "pack_id": "pack-a",
                "pack_version": "v1",
                "graphs": [
                    {
                        "id": "graph-a",
                        "name": "Graph A",
                        "directory_path": "root",
                        "user_ids": "alice",
                        "node_ids": "trigger-a",
                    }
                ],
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
                "graphs": [
                    {
                        "id": "graph-b",
                        "name": "Graph B",
                        "directory_path": "root/team-a",
                        "user_ids": ["bob"],
                        "node_ids": ["effect-b"],
                    }
                ],
                "ir": {
                    "version": "v1",
                    "nodes": [
                        {
                            "id": "effect-b",
                            "display_name": "echo",
                            "type": "effect:event:emit",
                            "event_type": "workflow.synthetic",
                            "payload": {"message": "echo"},
                        }
                    ],
                    "edges": [],
                },
            }
        ),
        encoding="utf-8",
    )
    _canonicalize_pack_file(packs_dir / "pack-a.v1.json")
    _canonicalize_pack_file(packs_dir / "pack-b.v1.json")

    composed = compose_workflow_ir(project_dir=tmp_path, workflow_packs=["pack-a.v1", "pack-b.v1"])

    assert [trigger["id"] for trigger in composed.triggers] == ["trigger-a"]
    assert [effect["id"] for effect in composed.effects] == ["effect-b"]
    assert [graph.id for graph in composed.graphs] == ["graph-a", "graph-b"]
    assert [graph.directory_path for graph in composed.graphs] == ["root", "root/team-a"]
    assert [graph.user_ids for graph in composed.graphs] == [["alice"], ["bob"]]
    assert [graph.node_ids for graph in composed.graphs] == [["trigger-a"], ["effect-b"]]
    assert composed.edges == []


def test_compose_workflow_ir_rejects_cross_pack_edges(tmp_path: Path) -> None:
    _write_workflow_pack(
        tmp_path,
        "pack-a.v1.json",
        {
            "pack_id": "pack-a",
            "pack_version": "v1",
            "graphs": [],
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
        },
    )
    _write_workflow_pack(
        tmp_path,
        "pack-b.v1.json",
        {
            "pack_id": "pack-b",
            "pack_version": "v1",
            "graphs": [],
            "ir": {
                "version": "v1",
                "nodes": [
                    {
                        "id": "effect-b",
                        "display_name": "echo",
                        "type": "effect:event:emit",
                        "event_type": "workflow.synthetic",
                        "payload": {"message": "echo"},
                    }
                ],
                "edges": [{"source_id": "trigger-a", "target_id": "effect-b"}],
            },
        },
    )

    with pytest.raises(ValueError, match="must reference nodes from the same pack"):
        compose_workflow_ir(project_dir=tmp_path, workflow_packs=["pack-a.v1", "pack-b.v1"])


def test_compose_workflow_ir_rejects_duplicate_pack_node_ids(tmp_path: Path) -> None:
    for name, node_type in (
        ("pack-a.v1.json", "trigger:cron"),
        ("pack-b.v1.json", "effect:event:emit"),
    ):
        node = {
            "id": "shared-node",
            "display_name": "Shared",
            "type": node_type,
        }
        if node_type == "trigger:cron":
            node["expression"] = "* * * * *"
        else:
            node["event_type"] = "workflow.synthetic"
            node["payload"] = {"message": "echo"}
        _write_workflow_pack(
            tmp_path,
            name,
            {
                "pack_id": name,
                "pack_version": "v1",
                "graphs": [],
                "ir": {"version": "v1", "nodes": [node], "edges": []},
            },
        )

    with pytest.raises(ValueError, match="Duplicate workflow node id 'shared-node'"):
        compose_workflow_ir(project_dir=tmp_path, workflow_packs=["pack-a.v1", "pack-b.v1"])


def test_compose_workflow_ir_rejects_duplicate_pack_graph_ids(tmp_path: Path) -> None:
    for name, node_id in (("pack-a.v1.json", "trigger-a"), ("pack-b.v1.json", "trigger-b")):
        _write_workflow_pack(
            tmp_path,
            name,
            {
                "pack_id": name,
                "pack_version": "v1",
                "graphs": [
                    {
                        "id": "shared-graph",
                        "name": "Shared",
                        "directory_path": "root",
                        "node_ids": [node_id],
                    }
                ],
                "ir": {
                    "version": "v1",
                    "nodes": [
                        {
                            "id": node_id,
                            "display_name": "tick",
                            "type": "trigger:cron",
                            "expression": "* * * * *",
                        }
                    ],
                    "edges": [],
                },
            },
        )

    with pytest.raises(ValueError, match="Duplicate workflow graph id 'shared-graph'"):
        compose_workflow_ir(project_dir=tmp_path, workflow_packs=["pack-a.v1", "pack-b.v1"])


def test_compose_workflow_ir_rejects_duplicate_pack_webhook_slugs(tmp_path: Path) -> None:
    for name, node_id in (("pack-a.v1.json", "webhook-a"), ("pack-b.v1.json", "webhook-b")):
        _write_workflow_pack(
            tmp_path,
            name,
            {
                "pack_id": name,
                "pack_version": "v1",
                "graphs": [],
                "ir": {
                    "version": "v1",
                    "nodes": [
                        {
                            "id": node_id,
                            "display_name": "Webhook",
                            "type": "trigger:http:webhook",
                            "slug": "build-notify",
                            "secret_env": "WEBHOOK_SECRET",
                        }
                    ],
                    "edges": [],
                },
            },
        )

    with pytest.raises(ValueError, match="Duplicate webhook slug id 'build-notify'"):
        compose_workflow_ir(project_dir=tmp_path, workflow_packs=["pack-a.v1", "pack-b.v1"])


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


def test_compose_workflow_ir_preserves_disabled_trigger_flag(tmp_path: Path) -> None:
    composed = compose_workflow_ir(
        project_dir=tmp_path,
        workflow_packs=[],
        inline_ir={
            "version": "v1",
            "nodes": [
                {
                    "id": "trigger-disabled",
                    "display_name": "disabled tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "enabled": False,
                }
            ],
            "edges": [],
        },
    )

    assert composed.triggers == [
        {
            "id": "trigger-disabled",
            "display_name": "disabled tick",
            "type": "trigger:cron",
            "config": {
                "expression": "* * * * *",
                "enabled": False,
            },
        }
    ]


def test_hidden_system_workflow_pack_contains_control_plane_graphs() -> None:
    project_dir = Path(__file__).resolve().parents[1]

    composed = compose_workflow_ir(
        project_dir=project_dir,
        workflow_packs=[],
        built_in_workflow_packs=BUILTIN_HIDDEN_WORKFLOW_PACKS,
    )

    assert [trigger["id"] for trigger in composed.triggers] == [
        "system.chat_assistant",
        "system.code_consultation.requested",
        "system.code_consultation.terminate_requested",
    ]
    assert [effect["id"] for effect in composed.effects] == [
        "system.chat_assistant.ensure_session",
        "system.chat_assistant.enqueue_prompt",
        "system.code_consultation.prepare",
        "system.code_consultation.prompt",
        "system.code_consultation.finalize_completed",
        "system.code_consultation.finalize_error",
        "system.code_consultation.terminate",
    ]
    assert composed.edges == [
        {
            "source_id": "system.chat_assistant",
            "target_id": "system.chat_assistant.ensure_session",
            "source_port": None,
            "allow_cycle": False,
        },
        {
            "source_id": "system.chat_assistant.ensure_session",
            "target_id": "system.chat_assistant.enqueue_prompt",
            "source_port": None,
            "allow_cycle": False,
        },
        {
            "source_id": "system.code_consultation.requested",
            "target_id": "system.code_consultation.prepare",
            "source_port": None,
            "allow_cycle": False,
        },
        {
            "source_id": "system.code_consultation.prepare",
            "target_id": "system.code_consultation.prompt",
            "source_port": None,
            "allow_cycle": False,
        },
        {
            "source_id": "system.code_consultation.prompt",
            "target_id": "system.code_consultation.finalize_completed",
            "source_port": "completed",
            "allow_cycle": False,
        },
        {
            "source_id": "system.code_consultation.prompt",
            "target_id": "system.code_consultation.finalize_error",
            "source_port": "error",
            "allow_cycle": False,
        },
        {
            "source_id": "system.code_consultation.terminate_requested",
            "target_id": "system.code_consultation.terminate",
            "source_port": None,
            "allow_cycle": False,
        },
    ]


def test_hidden_system_workflow_pack_loads_from_package_data_when_project_config_missing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    missing_repo_root = tmp_path / "missing-repo-root"
    monkeypatch.setattr(workflow_packs, "_module_project_root", lambda: missing_repo_root)

    composed = workflow_packs.compose_workflow_ir(
        project_dir=tmp_path,
        workflow_packs=[],
        built_in_workflow_packs=workflow_packs.BUILTIN_HIDDEN_WORKFLOW_PACKS,
    )

    assert [trigger["id"] for trigger in composed.triggers] == [
        "system.chat_assistant",
        "system.code_consultation.requested",
        "system.code_consultation.terminate_requested",
    ]
    assert [effect["id"] for effect in composed.effects] == [
        "system.chat_assistant.ensure_session",
        "system.chat_assistant.enqueue_prompt",
        "system.code_consultation.prepare",
        "system.code_consultation.prompt",
        "system.code_consultation.finalize_completed",
        "system.code_consultation.finalize_error",
        "system.code_consultation.terminate",
    ]


def test_hidden_system_workflow_pack_is_declared_as_package_data() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))

    package_data = payload["tool"]["setuptools"]["package-data"]["evander"]
    assert "workflow/builtin_packs/*.json" in package_data


def test_packaged_hidden_system_workflow_pack_matches_repo_config() -> None:
    project_dir = Path(__file__).resolve().parents[1]
    repo_pack = project_dir / "config" / "workflow-packs" / "system.v1.json"
    packaged_pack = (
        project_dir / "src" / "evander" / "workflow" / "builtin_packs" / "system.v1.json"
    )

    assert json.loads(packaged_pack.read_text(encoding="utf-8")) == json.loads(
        repo_pack.read_text(encoding="utf-8")
    )


def test_team_runtime_pack_uses_dispatch_finalizer_completion_path() -> None:
    from evander_team_addon.workflow_packs import team_runtime_v1

    pack = WorkflowPack.model_validate(team_runtime_v1())
    forbidden_fragments = (
        "team-action.json",
        "query:team:agent-action-artifact",
        "effect:team:apply-agent-action",
        "recover_missing_action",
    )
    required_edges = {
        ("team-agent-run-completed", "team-route-completed-session"),
        ("team-route-completed-session", "team-completed-persistent"),
        ("team-completed-persistent", "team-finalize-persistent-dispatch"),
        ("team-route-completed-session", "team-completed-ephemeral"),
        ("team-completed-ephemeral", "team-destroy-ephemeral-agent"),
        ("team-destroy-ephemeral-agent", "team-mark-ephemeral-cleaned-up"),
    }

    serialized = json.dumps(pack.model_dump(mode="json"), sort_keys=True)
    for forbidden in forbidden_fragments:
        assert forbidden not in serialized

    nodes = {ir_node.node.id: ir_node.node.model_dump(mode="json") for ir_node in pack.ir.nodes}
    edges = {(edge.source_id, edge.target_id) for edge in pack.ir.edges}

    assert "team-read-persistent-action" not in nodes
    assert "team-read-ephemeral-action" not in nodes
    assert "team-apply-persistent-action" not in nodes
    assert "team-apply-ephemeral-action" not in nodes
    finalizer = nodes["team-finalize-persistent-dispatch"]
    assert finalizer["type"] == "effect:team:finalize-agent-dispatch"
    assert finalizer["config"] == {
        "agent_id": "${{team-route-completed-session.agent_id ?? null}}",
        "dispatch_id": "${{team-route-completed-session.dispatch_id}}",
        "session_id": "${{team-route-completed-session.session_id}}",
    }
    assert required_edges.issubset(edges)
    assert ("team-completed-persistent", "team-read-persistent-action") not in edges
    assert ("team-read-persistent-action", "team-apply-persistent-action") not in edges
    assert ("team-completed-ephemeral", "team-read-ephemeral-action") not in edges
    assert ("team-read-ephemeral-action", "team-apply-ephemeral-action") not in edges
    outbox_publisher = nodes["team-publish-outbox-events"]
    assert outbox_publisher["type"] == "effect:team:publish-outbox-events"
    assert outbox_publisher["config"]["limit"] == 25
    ephemeral_create = nodes["team-create-ephemeral-agent"]
    assert ephemeral_create["type"] == "effect:agent:create"
    assert ephemeral_create["config"]["allowed_tools"] == ["team_report_ephemeral_result"]
    assert ("team-cron", "team-publish-outbox-events") in edges
    correction_gate = nodes["team-git-promotion-correction-aggregate"]
    assert correction_gate["config"]["when"]["all"][0]["equals"] == ("correction_aggregate")
    assert (
        "team-git-promotion-requested",
        "team-git-promotion-correction-aggregate",
    ) in edges
    assert (
        "team-git-promotion-correction-aggregate",
        "team-consolidate-correction-branches",
    ) in edges
    assert (
        "team-consolidate-correction-branches",
        "team-record-correction-aggregate-promotion",
    ) in edges
    team_graph = next(graph for graph in pack.graphs if graph.id == "team-runtime")
    assert "team-git-promotion-correction-aggregate" in team_graph.node_ids
    assert "team-consolidate-correction-branches" in team_graph.node_ids
    assert "team-record-correction-aggregate-promotion" in team_graph.node_ids
    assert nodes["team-consolidate-correction-branches"]["config"][
        "required_source_ancestor_commit_sha"
    ] == ("${{team-git-promotion-requested.payload.required_source_ancestor_commit_sha ?? null}}")
    recovery_config = nodes["team-promote-release-recovery-branch"]["config"]
    assert recovery_config["base_commit_sha"] == (
        "${{team-git-release-recovery-requested.payload.base_commit_sha ?? null}}"
    )
    assert recovery_config["required_source_ancestor_commit_sha"] == (
        "${{team-git-release-recovery-requested.payload."
        "required_source_ancestor_commit_sha ?? null}}"
    )


def test_checked_in_team_runtime_pack_preserves_git_promotion_evidence() -> None:
    project_dir = Path(__file__).resolve().parents[1]
    pack = json.loads(
        (project_dir / "config" / "workflow-packs" / "team-runtime.v1.json").read_text(
            encoding="utf-8"
        )
    )
    nodes = {node["id"]: node for item in pack["ir"]["nodes"] for node in [item.get("node", item)]}
    required_result_outputs = {
        "base_commit_sha",
        "source_manifests",
        "tree_hash",
        "unexpected_commit_shas",
        "unexpected_paths",
    }

    for node_id in (
        "team-record-feature-promotion",
        "team-record-correction-aggregate-promotion",
        "team-record-release-promotion",
        "team-record-release-recovery-promotion",
    ):
        assert required_result_outputs.issubset(nodes[node_id]["config"]["result_outputs"])


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
                        "directory_path": "root",
                        "user_ids": ["alice"],
                        "node_ids": ["trigger-a", "effect-a"],
                    },
                    {
                        "id": "graph-second",
                        "name": "Second graph",
                        "directory_path": "root/team-a",
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
                            "type": "effect:event:emit",
                            "event_type": "workflow.synthetic",
                            "payload": {"message": "effect-a"},
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
                            "type": "effect:event:emit",
                            "event_type": "workflow.synthetic",
                            "payload": {"message": "effect-b"},
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
    _canonicalize_pack_file(packs_dir / "multi.v1.json")

    pack = load_workflow_pack(tmp_path, "multi.v1")

    assert [graph.name for graph in pack.graphs] == ["First graph", "Second graph"]
    assert [graph.directory_path for graph in pack.graphs] == ["root", "root/team-a"]
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

    container_triggers = {trigger["id"]: trigger for trigger in container.triggers}
    default_effects = {effect["id"]: effect for effect in default.effects}
    container_effects = {effect["id"]: effect for effect in container.effects}
    default_gates = {gate["id"]: gate for gate in default.gates}
    container_gates = {gate["id"]: gate for gate in container.gates}
    container_edges = {(edge["source_id"], edge["target_id"]): edge for edge in container.edges}
    default_edges = {(edge["source_id"], edge["target_id"]): edge for edge in default.edges}
    default_queries = {query["id"]: query for query in default.queries}
    container_queries = {query["id"]: query for query in container.queries}
    assert default_effects["effect-vm-create-1"]["type"] == "effect:vm:create"
    assert container_effects["effect-agent-create-1"]["type"] == "effect:agent:create"
    expected_issue_session_guard_query = {
        "active_only": True,
        "directory_path": "${{gate-merge-1.directory_path}}",
        "limit": 1,
        "metadata_equals": {
            "issue_number": "${{gate-merge-1.ticket.issue_number}}",
            "labels.vm-type": "issue",
        },
    }
    expected_issue_start_dedupe = {
        "key": "issue-start:${{gate-merge-1.directory_path}}:${{gate-merge-1.ticket.issue_number}}",
        "ttl": "PT15M",
    }
    expected_issue_session_guard_filter = {
        "all": [
            {
                "equals": 0,
                "left": "${{query-session-list-active-issue-1.count}}",
            }
        ]
    }
    expected_issue_create_metadata = {
        "ado_project": "${{gate-merge-1.ado_project}}",
        "directory_path": "${{gate-merge-1.directory_path}}",
        "issue_number": "${{gate-merge-1.ticket.issue_number}}",
        "issue_title": "${{gate-merge-1.ticket.title}}",
    }
    expected_container_issue_create_metadata = {
        **expected_issue_create_metadata,
        "issue_owner": "${{gate-merge-1.issue_owner ?? null}}",
    }
    for queries in (default_queries, container_queries):
        assert queries["query-session-list-active-issue-1"]["type"] == "query:session:list"
        assert (
            queries["query-session-list-active-issue-1"]["config"]
            == expected_issue_session_guard_query
        )
    for gates in (default_gates, container_gates):
        assert gates["gate-dedupe-issue-start-1"]["type"] == "gate:dedupe"
        assert gates["gate-dedupe-issue-start-1"]["config"] == expected_issue_start_dedupe
        assert (
            gates["gate-filter-no-active-issue-session-1"]["config"]["when"]
            == expected_issue_session_guard_filter
        )
    for edges in (default_edges, container_edges):
        assert ("gate-merge-1", "effect-vm-create-1") not in edges
        assert ("gate-merge-1", "effect-agent-create-1") not in edges
        assert ("gate-merge-1", "query-session-list-active-issue-1") not in edges
        assert (
            edges[("gate-merge-1", "gate-dedupe-issue-start-1")]["target_id"]
            == "gate-dedupe-issue-start-1"
        )
        assert (
            edges[("gate-dedupe-issue-start-1", "query-session-list-active-issue-1")]["target_id"]
            == "query-session-list-active-issue-1"
        )
        assert (
            edges[
                (
                    "query-session-list-active-issue-1",
                    "gate-filter-no-active-issue-session-1",
                )
            ]["target_id"]
            == "gate-filter-no-active-issue-session-1"
        )
    assert (
        default_edges[("gate-filter-no-active-issue-session-1", "effect-vm-create-1")]["target_id"]
        == "effect-vm-create-1"
    )
    assert (
        container_edges[("gate-filter-no-active-issue-session-1", "effect-agent-create-1")][
            "target_id"
        ]
        == "effect-agent-create-1"
    )
    assert (
        default_effects["effect-vm-create-1"]["config"]["metadata"]
        == expected_issue_create_metadata
    )
    assert (
        container_effects["effect-agent-create-1"]["config"]["metadata"]
        == expected_container_issue_create_metadata
    )
    assert container_effects["effect-agent-clone-repo-1"]["type"] == "effect:agent:clone-repo"
    assert (
        container_effects["effect-agent-clone-repo-1"]["config"]["git_author_name"]
        == "${{gate-merge-1.issue_owner.display_name ?? null}}"
    )
    assert (
        container_effects["effect-agent-clone-repo-1"]["config"]["git_author_email"]
        == "${{gate-merge-1.issue_owner.email ?? null}}"
    )
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
        container_effects["effect-agent-enqueue-prompt-1"]["type"] == "effect:agent:enqueue-prompt"
    )
    for effects in (default_effects, container_effects):
        teams_prompt = effects["effect-agent-enqueue-prompt-1"]["config"]["prompt"]
        assert "${{trigger-teams-new-message-2.message.content}}" in teams_prompt
        assert "${{trigger-teams-new-message-2.thread_id}}" in teams_prompt
        assert "Treat that content as the current user request" in teams_prompt
        assert effects["effect-agent-enqueue-prompt-1"]["config"]["queue_key"] == (
            "teams:${{trigger-teams-new-message-2.channel_target}}:"
            "${{trigger-teams-new-message-2.message_id ?? trigger-teams-new-message-2.cursor}}"
        )
        assert (
            effects["effect-agent-enqueue-prompt-1"]["config"]["resume_thread_metadata_key"]
            == "teams:${{trigger-teams-new-message-2.channel_target}}:agent_thread_id"
        )
        teams_dm_prompt = effects["effect-agent-enqueue-prompt-teams-dm-1"]["config"]["prompt"]
        assert "${{trigger-teams-direct-message-1.message.content}}" in teams_dm_prompt
        assert effects["effect-agent-enqueue-prompt-teams-dm-1"]["config"]["queue_key"] == (
            "teams:${{trigger-teams-direct-message-1.channel_target}}:"
            "${{trigger-teams-direct-message-1.message_id ?? "
            "trigger-teams-direct-message-1.cursor}}"
        )
        assert (
            effects["effect-agent-enqueue-prompt-teams-dm-1"]["config"][
                "resume_thread_metadata_key"
            ]
            == "teams:${{trigger-teams-direct-message-1.channel_target}}:agent_thread_id"
        )
    assert ("trigger-teams-direct-message-1", "query-session-context-teams-dm-1") in default_edges
    assert ("trigger-teams-direct-message-1", "query-session-context-teams-dm-1") in container_edges
    assert default_effects["effect-agent-enqueue-prompt-2"]["type"] == "effect:agent:enqueue-prompt"
    assert (
        container_effects["effect-agent-enqueue-prompt-2"]["type"] == "effect:agent:enqueue-prompt"
    )
    for effects in (default_effects, container_effects):
        feedback_prompt = effects["effect-agent-enqueue-prompt-2"]["config"]["prompt"]
        assert "${{trigger-pull-request-new-feedback-1.feedback.feedback_id}}" in feedback_prompt
        assert "${{trigger-pull-request-new-feedback-1.feedback.body}}" in feedback_prompt
        assert "do not skip the feedback item above" in feedback_prompt
        assert effects["effect-agent-enqueue-prompt-2"]["config"]["queue_key"] == (
            "pr-feedback:${{trigger-pull-request-new-feedback-1.session_id}}:"
            "${{trigger-pull-request-new-feedback-1.feedback.feedback_id}}"
        )
    assert "output_mode" not in container_effects["effect-agent-enqueue-prompt-2"]["config"]
    assert (
        container_edges[("effect-agent-prompt-4", "query-issue-get-1")]["source_port"]
        == "completed"
    )
    assert (
        "effect-session-update-metadata-3" not in default_effects
        and "effect-session-update-metadata-3" not in container_effects
    )
    assert (
        "effect-agent-enqueue-prompt-1",
        "effect-session-update-metadata-3",
    ) not in default_edges
    assert (
        "effect-agent-enqueue-prompt-1",
        "effect-session-update-metadata-3",
    ) not in container_edges
    assert (
        default_queries["query-git-pr-note-1"]["config"]["notes_ref"]
        == "${{trigger-git-push-1.notes_ref}}"
    )
    assert (
        container_queries["query-git-pr-note-1"]["config"]["notes_ref"]
        == "${{trigger-git-push-1.notes_ref}}"
    )
    expected_git_push_filter = {
        "all": [
            {
                "left": '${{trigger-git-push-1.branch_name ?? ""}}',
                "starts_with": "agents/",
            },
            {
                "left": '${{trigger-git-push-1.ref_name ?? ""}}',
                "starts_with": "refs/heads/agents/",
            },
            {
                "is_not_null": True,
                "left": "${{trigger-git-push-1.session_id ?? null}}",
            },
            {
                "is_not_null": True,
                "left": "${{trigger-git-push-1.default_branch ?? null}}",
            },
            {
                "is_not_null": True,
                "left": "${{trigger-git-push-1.review_branch ?? null}}",
            },
        ]
    }
    for gates in (default_gates, container_gates):
        assert gates["gate-filter-2"]["config"]["when"] == expected_git_push_filter
        assert gates["gate-filter-2"]["outputs"]["notes_ref"] == {"type": "string"}
    assert default_gates["gate-filter-8"]["config"]["when"] == {
        "all": [{"equals": False, "left": "${{effect-pr-create-1.success}}"}]
    }
    assert container_gates["gate-filter-8"]["config"]["when"] == {
        "all": [{"equals": False, "left": "${{effect-pr-create-1.success}}"}]
    }
    assert default_edges[("effect-pr-create-1", "gate-filter-8")]["target_id"] == "gate-filter-8"
    assert (
        container_edges[("gate-filter-8", "effect-agent-enqueue-prompt-3")]["target_id"]
        == "effect-agent-enqueue-prompt-3"
    )
    assert (
        container_effects["effect-agent-enqueue-prompt-3"]["config"]["target"]
        == "${{trigger-git-push-1.session_id}}"
    )
    expected_issue_pr_filter = {
        "all": [
            {
                "left": "${{query-session-context-1.metadata.pr_workflow ?? null}}",
                "not_equals": "standalone",
            },
            {
                "is_not_null": True,
                "left": "${{query-session-context-1.metadata.issue_number ?? null}}",
            },
            {
                "is_not_null": True,
                "left": "${{query-session-context-1.metadata.issue_title ?? null}}",
            },
        ]
    }
    expected_standalone_pr_filter = {
        "all": [
            {
                "equals": "standalone",
                "left": "${{query-session-context-1.metadata.pr_workflow ?? null}}",
            }
        ]
    }
    expected_standalone_preflight_filter = {
        "all": [{"equals": False, "left": "${{effect-pr-create-standalone.success}}"}]
    }
    for gates in (default_gates, container_gates):
        assert gates["gate-filter-issue-pr"]["config"]["when"] == expected_issue_pr_filter
        assert gates["gate-filter-standalone-pr"]["config"]["when"] == expected_standalone_pr_filter
        assert (
            gates["gate-filter-standalone-pr-preflight"]["config"]["when"]
            == expected_standalone_preflight_filter
        )
        assert "destination_branch" in gates["gate-filter-8"]["outputs"]
        assert "origin_branch" not in gates["gate-filter-8"]["outputs"]
        assert "destination_branch" in gates["gate-filter-standalone-pr-preflight"]["outputs"]
        assert "origin_branch" not in gates["gate-filter-standalone-pr-preflight"]["outputs"]
    for edges in (default_edges, container_edges):
        assert ("query-session-context-1", "query-git-pr-note-1") not in edges
        assert (
            edges[("query-session-context-1", "gate-filter-issue-pr")]["target_id"]
            == "gate-filter-issue-pr"
        )
        assert (
            edges[("gate-filter-issue-pr", "query-git-pr-note-1")]["target_id"]
            == "query-git-pr-note-1"
        )
        assert (
            edges[("query-session-context-1", "gate-filter-standalone-pr")]["target_id"]
            == "gate-filter-standalone-pr"
        )
        assert (
            edges[("gate-filter-standalone-pr", "query-git-pr-note-standalone")]["target_id"]
            == "query-git-pr-note-standalone"
        )
        assert (
            edges[("query-git-pr-note-standalone", "effect-pr-create-standalone")]["target_id"]
            == "effect-pr-create-standalone"
        )
        assert (
            edges[("effect-pr-create-standalone", "gate-filter-standalone-pr-preflight")][
                "target_id"
            ]
            == "gate-filter-standalone-pr-preflight"
        )
        assert (
            edges[
                (
                    "gate-filter-standalone-pr-preflight",
                    "effect-agent-enqueue-prompt-standalone-pr",
                )
            ]["target_id"]
            == "effect-agent-enqueue-prompt-standalone-pr"
        )
    for queries in (default_queries, container_queries):
        assert (
            queries["query-git-pr-note-standalone"]["config"]["notes_ref"]
            == "${{trigger-git-push-1.notes_ref}}"
        )
    for effects in (default_effects, container_effects):
        standalone_pr = effects["effect-pr-create-standalone"]
        expected_standalone_fallback_title = (
            "${{query-session-context-1.metadata.pr_title_prefix "
            "?? trigger-git-push-1.session_id}}: ${{trigger-git-push-1.branch_name}}"
        )
        assert standalone_pr["type"] == "effect:pr:create"
        assert (
            standalone_pr["config"]["title"]
            == "${{query-session-context-1.metadata.pr_title ?? null}}"
        )
        assert standalone_pr["config"]["fallback_title"] == expected_standalone_fallback_title
        assert standalone_pr["config"]["managed_repo_id"] == (
            "${{trigger-git-push-1.managed_repo_id}}"
        )
        assert standalone_pr["config"]["source_ref"] == ("${{trigger-git-push-1.review_branch}}")
        assert standalone_pr["config"]["expected_commit_sha"] == (
            "${{trigger-git-push-1.commit_hash}}"
        )
        assert standalone_pr["config"]["destination_branch"] == (
            "${{query-session-context-1.metadata.pr_origin_branch ?? null}}"
        )
        assert standalone_pr["config"]["fallback_destination_branch"] == (
            "feature/${{trigger-git-push-1.session_id}}"
        )
        assert "issue_number" not in standalone_pr["config"]["title"]
        assert "issue_title" not in standalone_pr["config"]["title"]
        assert "${{query-git-pr-note-standalone.body}}" in standalone_pr["config"]["body"]
        standalone_prompt = effects["effect-agent-enqueue-prompt-standalone-pr"]
        issue_prompt = effects["effect-agent-enqueue-prompt-3"]
        assert "effect-pr-create-1.destination_branch" in issue_prompt["config"]["prompt"]
        assert "effect-pr-create-1.origin_branch" not in issue_prompt["config"]["prompt"]
        expected_standalone_queue_key = (
            "pr-create-preflight:${{trigger-git-push-1.session_id}}:"
            "${{effect-pr-create-standalone.destination_branch}}"
        )
        assert standalone_prompt["type"] == "effect:agent:enqueue-prompt"
        assert standalone_prompt["config"]["queue_key"] == expected_standalone_queue_key
        assert standalone_prompt["config"]["target"] == "${{trigger-git-push-1.session_id}}"
    assert not any(effect["type"].startswith("effect:vm:") for effect in container.effects)

    assert container_effects["effect-agent-create-2"]["type"] == "effect:agent:create"
    assert (
        container_effects["effect-agent-create-2"]["config"]["execution_backend"]
        == "container_worker_vm"
    )
    assert (
        container_effects["effect-agent-create-2"]["config"]["npm_registry_url"]
        == "http://10.0.0.4:11881/"
    )
    assert "npm_registry_url" not in container_effects["effect-agent-create-1"]["config"]
    assert (
        "npm_registry_url"
        not in container_effects["effect-agent-create-auto-dev-approval-caller-1"]["config"]
    )
    assert container_effects["effect-agent-clone-repo-2"]["type"] == "effect:agent:clone-repo"
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

    assert container_triggers["trigger-cron-auto-dev-1"]["config"]["expression"] == "*/5 * * * *"
    auto_dev_query = container_queries["query-ado-auto-dev-1"]
    assert auto_dev_query["type"] == "query:ado:work-items"
    assert auto_dev_query["config"]["directory_path"] == "root/example/truman"
    assert auto_dev_query["config"]["project"] == "Example Industry APP"
    assert auto_dev_query["config"]["states"] == ["New", "To Do"]
    assert auto_dev_query["config"]["tags_any"] == ["auto-dev"]
    assert auto_dev_query["config"]["current_iteration"] is False
    auto_dev_owner_predicate = {
        "all": [
            {
                "is_not_null": True,
                "left": "${{gate-for-auto-dev-1.current_element.assigned_to_identity.display_name ?? null}}",
            },
            {
                "is_not_null": True,
                "left": "${{gate-for-auto-dev-1.current_element.assigned_to_identity.email ?? null}}",
            },
            {
                "left": '${{gate-for-auto-dev-1.current_element.assigned_to_identity.email ?? ""}}',
                "matches_regex": "^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$",
            },
            {
                "left": '${{gate-for-auto-dev-1.current_element.assigned_to_identity.email ?? ""}}',
                "not_equals": "truman@quinth.se",
            },
        ]
    }
    assert (
        container_gates["gate-filter-auto-dev-has-owner-1"]["config"]["when"]
        == auto_dev_owner_predicate
    )
    assert container_gates["gate-filter-auto-dev-missing-owner-1"]["config"]["when"] == {
        "not": auto_dev_owner_predicate
    }
    assert container_gates["gate-dedupe-auto-dev-missing-owner-comment-1"]["config"] == {
        "key": "auto-dev-missing-owner:${{gate-filter-auto-dev-missing-owner-1.current_element.id}}:${{gate-filter-auto-dev-missing-owner-1.current_element.rev ?? gate-filter-auto-dev-missing-owner-1.current_element.changed_at ?? gate-filter-auto-dev-missing-owner-1.current_index}}",
        "ttl": "PT168H",
    }
    auto_dev_owner_comment = container_effects["effect-ado-post-work-item-comment-auto-dev-owner-1"]
    assert auto_dev_owner_comment["type"] == "effect:ado:post-work-item-comment"
    assert (
        auto_dev_owner_comment["config"]["work_item_id"]
        == "${{gate-dedupe-auto-dev-missing-owner-comment-1.current_element.id}}"
    )
    assert "System.AssignedTo" in auto_dev_owner_comment["config"]["comment"]
    auto_dev_claim = container_effects["effect-ado-update-work-item-auto-dev-1"]
    assert auto_dev_claim["type"] == "effect:ado:update-work-item"
    assert auto_dev_claim["config"]["work_item_id"] == (
        "${{gate-filter-auto-dev-has-owner-1.current_element.id}}"
    )
    assert auto_dev_claim["config"]["expected_states"] == ["New", "To Do"]
    assert auto_dev_claim["config"]["state"] == "In Progress"
    assert auto_dev_claim["config"]["sync_proxy_cache"] is True
    assert (
        container_edges[("query-ado-auto-dev-1", "gate-for-auto-dev-1")]["target_id"]
        == "gate-for-auto-dev-1"
    )
    assert (
        container_edges[("gate-for-auto-dev-1", "gate-filter-auto-dev-has-owner-1")]["source_port"]
        == "body"
    )
    assert (
        container_edges[
            ("gate-filter-auto-dev-has-owner-1", "effect-ado-update-work-item-auto-dev-1")
        ]["target_id"]
        == "effect-ado-update-work-item-auto-dev-1"
    )
    assert (
        container_edges[("gate-for-auto-dev-1", "gate-filter-auto-dev-missing-owner-1")][
            "source_port"
        ]
        == "body"
    )
    assert (
        container_edges[
            ("gate-filter-auto-dev-missing-owner-1", "gate-dedupe-auto-dev-missing-owner-comment-1")
        ]["target_id"]
        == "gate-dedupe-auto-dev-missing-owner-comment-1"
    )
    assert (
        container_edges[
            (
                "gate-dedupe-auto-dev-missing-owner-comment-1",
                "effect-ado-post-work-item-comment-auto-dev-owner-1",
            )
        ]["target_id"]
        == "effect-ado-post-work-item-comment-auto-dev-owner-1"
    )
    auto_dev_missing_owner_dropped_loopback = container_edges[
        ("gate-dedupe-auto-dev-missing-owner-comment-1", "gate-for-auto-dev-1")
    ]
    assert auto_dev_missing_owner_dropped_loopback["source_port"] == "dropped"
    assert auto_dev_missing_owner_dropped_loopback["allow_cycle"] is True
    assert (
        container_edges[
            ("effect-ado-post-work-item-comment-auto-dev-owner-1", "gate-for-auto-dev-1")
        ]["allow_cycle"]
        is True
    )
    assert (
        container_edges[("effect-ado-update-work-item-auto-dev-1", "gate-for-auto-dev-1")][
            "allow_cycle"
        ]
        is True
    )
    assert (
        container_edges[
            ("effect-ado-update-work-item-auto-dev-1", "gate-filter-auto-dev-claimed-1")
        ]["target_id"]
        == "gate-filter-auto-dev-claimed-1"
    )
    assert container_gates["gate-filter-auto-dev-claimed-1"]["config"]["when"] == {
        "all": [
            {
                "equals": True,
                "left": "${{effect-ado-update-work-item-auto-dev-1.updated}}",
            }
        ]
    }
    assert (
        container_gates["gate-map-auto-dev-ticket-1"]["config"]["template"]["ticket"]
        == "${{effect-ado-update-work-item-auto-dev-1.ticket}}"
    )
    assert container_gates["gate-map-auto-dev-ticket-1"]["config"]["template"]["issue_owner"] == {
        "display_name": "${{effect-ado-update-work-item-auto-dev-1.remote_assigned_to_identity.display_name}}",
        "email": "${{effect-ado-update-work-item-auto-dev-1.remote_assigned_to_identity.email}}",
        "provider": "azure_devops",
        "resolved_at": "${{system.now.utc_timestamp}}",
        "source": "System.AssignedTo",
        "work_item_id": "${{effect-ado-update-work-item-auto-dev-1.work_item_id}}",
        "work_item_rev": "${{effect-ado-update-work-item-auto-dev-1.remote_work_item_rev ?? null}}",
    }
    assert container_gates["gate-map-1"]["config"]["template"]["issue_owner"] is None
    assert container_gates["gate-map-2"]["config"]["template"]["issue_owner"] is None
    assert container_gates["gate-merge-1"]["outputs"]["issue_owner"] == {"type": "object"}
    assert (
        container_edges[("gate-map-auto-dev-ticket-1", "gate-merge-1")]["target_id"]
        == "gate-merge-1"
    )
    assert (
        container_edges[("gate-for-auto-dev-1", "effect-event-emit-auto-dev-scan-complete-1")][
            "source_port"
        ]
        == "done"
    )
    assert (
        container_triggers["trigger-cron-auto-dev-approval-call-1"]["config"]["expression"]
        == "*/15 * * * *"
    )
    approval_query = container_queries["query-ado-auto-dev-awaiting-approval-1"]
    assert approval_query["config"]["directory_path"] == "root/example/truman/auto-dev"
    assert approval_query["config"]["top"] == 50
    assert "[System.Tags] CONTAINS 'auto-dev'" in approval_query["config"]["wiql"]
    assert "[System.Tags] NOT CONTAINS 'auto-dev-approved'" in approval_query["config"]["wiql"]
    assert "[System.State] IN ('New', 'To Do')" in approval_query["config"]["wiql"]
    approval_threshold = container_gates["gate-filter-auto-dev-approval-backlog-threshold-1"]
    assert approval_threshold["config"]["when"]["all"][1]["greater_than"] == 8
    approval_caller = container_effects["effect-agent-create-auto-dev-approval-caller-1"]
    assert approval_caller["config"]["session_id"] == (
        "auto-dev-ado-approval-caller-root-example-truman-auto-dev"
    )
    assert "make_phone_call" in approval_caller["config"]["allowed_tools"]
    assert "get_phone_call_result" in approval_caller["config"]["allowed_tools"]
    assert "update_ado_backlog_tags" in approval_caller["config"]["allowed_tools"]
    approval_prompt = container_effects["effect-agent-enqueue-prompt-auto-dev-approval-caller-1"][
        "config"
    ]["prompt"]
    assert "jonathan_quinth" in approval_prompt
    assert "auto-dev-approved" in approval_prompt
    assert (
        container_edges[
            (
                "effect-agent-enqueue-prompt-auto-dev-approval-caller-1",
                "effect-session-update-metadata-auto-dev-approval-caller-thread-1",
            )
        ]["target_id"]
        == "effect-session-update-metadata-auto-dev-approval-caller-thread-1"
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

    layout_path = (
        project_dir / "config" / "workflow-packs" / "agent-container-worker.v1.layout-example.json"
    )
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
