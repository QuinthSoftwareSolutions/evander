"""Prepare a reviewable, opt-in conversation-steering workflow pack.

This is an offline transformation. Apply the result through the revision-checked
workflow editor API only after compatible workers and control-plane code deploy.
"""

from __future__ import annotations

from copy import deepcopy


def prepare_conversation_steering_pack(
    pack: dict,
    *,
    enqueue_node_ids: list[str],
    before_node_id: str,
    session_id_binding: str,
    conversation_key_binding: str,
    dispatch_id_binding: str,
    prompt: str,
) -> dict:
    candidate = deepcopy(pack)
    raw_nodes = candidate["ir"]["nodes"]
    nodes = {value.get("node", value)["id"]: value.get("node", value) for value in raw_nodes}
    if not enqueue_node_ids or before_node_id not in nodes:
        raise ValueError("Select enqueue nodes and the preparation entry node")
    ids = [before_node_id + suffix for suffix in ("-activity", "-busy", "-followup")]
    if set(ids) & nodes.keys():
        raise ValueError("Conversation steering is already configured")
    for node_id in enqueue_node_ids:
        node = nodes[node_id]
        if node["type"] != "effect:agent:enqueue-prompt":
            raise ValueError("Selected node must enqueue an agent prompt")
        node["config"].update(
            delivery_mode="steer_if_running",
            conversation_key=conversation_key_binding,
            dispatch_id=dispatch_id_binding,
            resume_mode="same_context",
            prompt=prompt,
        )
    graphs = [
        graph
        for graph in candidate["graphs"]
        if before_node_id in graph["node_ids"] and set(enqueue_node_ids) <= set(graph["node_ids"])
    ]
    if len(graphs) != 1:
        raise ValueError("Preparation and enqueue nodes must belong to one graph")
    edges = candidate["ir"]["edges"]
    incoming = [edge for edge in edges if edge["target_id"] == before_node_id]
    if not incoming:
        raise ValueError("Preparation entry node needs an incoming route")
    for edge in incoming:
        edge["target_id"] = ids[0]
    config = deepcopy(nodes[enqueue_node_ids[0]]["config"])
    config.pop("reasoning_effort", None)
    raw_nodes.extend(
        [
            {
                "id": ids[0],
                "display_name": "Read active conversation",
                "type": "query:session-context",
                "config": {"target": session_id_binding, "include_activity": True},
            },
            {
                "id": ids[1],
                "display_name": "Route active follow-up",
                "type": "gate:switch",
                "config": {
                    "cases": [
                        {
                            "port": "busy",
                            "label": "Assistant is active",
                            "when": {"left": "${{" + ids[0] + ".activity.active}}", "equals": True},
                        }
                    ],
                    "default_port": "idle",
                },
            },
            {
                "id": ids[2],
                "display_name": "Deliver conversation follow-up",
                "type": "effect:agent:enqueue-prompt",
                "config": config,
            },
        ]
    )
    edges.extend(
        [
            {"source_id": ids[0], "target_id": ids[1]},
            {"source_id": ids[1], "source_port": "busy", "target_id": ids[2]},
            {"source_id": ids[1], "source_port": "idle", "target_id": before_node_id},
        ]
    )
    graphs[0]["node_ids"].extend(ids)
    return candidate
