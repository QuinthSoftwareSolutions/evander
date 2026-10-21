import pytest

from evander.workflow.conversation_steering import prepare_conversation_steering_pack
from evander.workflow.packs import compose_workflow_ir
from evander.events.models import materialize_node_definition, WorkflowNodeEnvelope


def test_prepare_preserves_cold_start_and_adds_busy_path_before_preparation():
    pack = {
        "graphs": [{"node_ids": ["context", "prepare", "enqueue"]}],
        "ir": {
            "nodes": [
                {"id": "context", "display_name": "Context", "type": "gate:merge", "config": {}},
                {"id": "prepare", "display_name": "Prepare", "type": "gate:merge", "config": {}},
                {
                    "id": "enqueue",
                    "display_name": "Enqueue",
                    "type": "effect:agent:enqueue-prompt",
                    "config": {
                        "session_id": "assistant",
                        "prompt": "old",
                        "reasoning_effort": "high",
                    },
                },
            ],
            "edges": [
                {"source_id": "context", "target_id": "prepare"},
                {"source_id": "prepare", "target_id": "enqueue"},
            ],
        },
    }
    options = dict(
        enqueue_node_ids=["enqueue"],
        before_node_id="prepare",
        session_id_binding="assistant",
        conversation_key_binding="teams:chat",
        dispatch_id_binding="message-1",
        prompt="Reply to this message",
    )
    candidate = prepare_conversation_steering_pack(pack, **options)
    ir = compose_workflow_ir(project_dir=None, workflow_packs=[], inline_ir=candidate["ir"])
    for node in ir.effects + ir.queries + ir.gates:
        materialize_node_definition(WorkflowNodeEnvelope.model_validate(node))
    assert pack["ir"]["edges"][0]["target_id"] == "prepare"
    edges = candidate["ir"]["edges"]
    assert {"source_id": "prepare-busy", "source_port": "idle", "target_id": "prepare"} in edges
    assert {
        "source_id": "prepare-busy",
        "source_port": "busy",
        "target_id": "prepare-followup",
    } in edges
    assert {"source_id": "prepare", "target_id": "enqueue"} in edges
    fast = next(node for node in ir.effects if node["id"] == "prepare-followup")
    assert fast["config"]["delivery_mode"] == "steer_if_running"
    assert fast["config"]["dispatch_id"] == "message-1"
    assert "reasoning_effort" not in fast["config"]
    assert next(node for node in ir.queries)["config"]["include_activity"] is True
    with pytest.raises(ValueError, match="already configured"):
        prepare_conversation_steering_pack(candidate, **options)
