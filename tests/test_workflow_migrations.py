from __future__ import annotations

from evander.workflow.migrations import migrate_agent_prompt_lifecycle_ports


def test_migrate_agent_prompt_output_mode_to_lifecycle_ports() -> None:
    payload = {
        "effects": [
            {
                "id": "prompt-start",
                "type": "effect:agent:prompt",
                "prompt": "start",
                "output_mode": "immediate",
            },
            {
                "id": "prompt-completed",
                "type": "effect:agent:prompt",
                "prompt": "complete",
                "output_mode": "on_completion",
            },
        ],
        "edges": [
            {"source_id": "prompt-start", "target_id": "started-target"},
            {"source_id": "prompt-completed", "target_id": "completed-target"},
            {
                "source_id": "prompt-completed",
                "source_port": "started",
                "target_id": "already-ported",
            },
        ],
    }

    migrated = migrate_agent_prompt_lifecycle_ports(payload)

    effects = {effect["id"]: effect for effect in migrated["effects"]}
    edges = {(edge["source_id"], edge["target_id"]): edge for edge in migrated["edges"]}
    assert "output_mode" not in effects["prompt-start"]
    assert "output_mode" not in effects["prompt-completed"]
    assert edges[("prompt-start", "started-target")]["source_port"] == "started"
    assert edges[("prompt-completed", "completed-target")]["source_port"] == "completed"
    assert edges[("prompt-completed", "already-ported")]["source_port"] == "started"


def test_migrate_agent_prompt_lifecycle_ports_handles_workflow_ir_nodes() -> None:
    payload = {
        "ir": {
            "nodes": [
                {
                    "id": "prompt",
                    "type": "effect:agent:prompt",
                    "config": {"prompt": "complete", "output_mode": "on_completion"},
                }
            ],
            "edges": [{"source_id": "prompt", "target_id": "target"}],
        }
    }

    migrated = migrate_agent_prompt_lifecycle_ports(payload)

    prompt_node = migrated["ir"]["nodes"][0]
    assert "output_mode" not in prompt_node["config"]
    assert migrated["ir"]["edges"][0]["source_port"] == "completed"


def test_migrate_agent_enqueue_prompt_edges_to_started_port() -> None:
    payload = {
        "effects": [
            {
                "id": "enqueue",
                "type": "effect:agent:enqueue-prompt",
                "prompt": "continue",
            }
        ],
        "edges": [{"source_id": "enqueue", "target_id": "target"}],
    }

    migrated = migrate_agent_prompt_lifecycle_ports(payload)

    assert migrated["edges"][0]["source_port"] == "started"
