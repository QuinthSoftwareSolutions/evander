from __future__ import annotations

import json
from typing import Any

AGENT_PROMPT_COMPLETED_PORT = "completed"
AGENT_PROMPT_EFFECT_TYPE = "effect:agent:prompt"
AGENT_PROMPT_STARTED_PORT = "started"

_LEGACY_AGENT_PROMPT_OUTPUT_MODE = "output_mode"


def migrate_agent_prompt_lifecycle_ports(payload: dict[str, Any]) -> dict[str, Any]:
    """Upgrade legacy prompt output_mode edges to explicit lifecycle ports."""

    migrated = json.loads(json.dumps(payload))
    if not isinstance(migrated, dict):
        return payload

    _migrate_workflow_payload(migrated)
    return migrated


def _migrate_workflow_payload(payload: dict[str, Any]) -> None:
    _migrate_flat_workflow(payload)

    workflow_ir = payload.get("workflow_ir")
    if isinstance(workflow_ir, dict):
        _migrate_ir(workflow_ir)

    ir = payload.get("ir")
    if isinstance(ir, dict):
        _migrate_ir(ir)

    if isinstance(payload.get("nodes"), list) and isinstance(payload.get("edges"), list):
        _migrate_ir(payload)


def _migrate_flat_workflow(payload: dict[str, Any]) -> None:
    effects = payload.get("effects")
    edges = payload.get("edges")
    if not isinstance(effects, list) or not isinstance(edges, list):
        return

    prompt_ports: dict[str, str] = {}
    for effect in effects:
        if not isinstance(effect, dict):
            continue
        prompt_id, port = _migrate_prompt_node(effect)
        if prompt_id:
            prompt_ports[prompt_id] = port
    _migrate_edges(edges, prompt_ports=prompt_ports)


def _migrate_ir(ir_payload: dict[str, Any]) -> None:
    nodes = ir_payload.get("nodes")
    edges = ir_payload.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return

    prompt_ports: dict[str, str] = {}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_payload = node.get("node") if isinstance(node.get("node"), dict) else node
        if not isinstance(node_payload, dict):
            continue
        prompt_id, port = _migrate_prompt_node(node_payload)
        if prompt_id:
            prompt_ports[prompt_id] = port
    _migrate_edges(edges, prompt_ports=prompt_ports)


def _migrate_prompt_node(node: dict[str, Any]) -> tuple[str | None, str]:
    node_type = str(node.get("type") or "").strip()
    if node_type != AGENT_PROMPT_EFFECT_TYPE:
        return None, AGENT_PROMPT_STARTED_PORT

    output_mode: Any = None
    if _LEGACY_AGENT_PROMPT_OUTPUT_MODE in node:
        output_mode = node.pop(_LEGACY_AGENT_PROMPT_OUTPUT_MODE, None)

    config = node.get("config")
    if isinstance(config, dict) and _LEGACY_AGENT_PROMPT_OUTPUT_MODE in config:
        output_mode = config.pop(_LEGACY_AGENT_PROMPT_OUTPUT_MODE, None)

    node_id = str(node.get("id") or "").strip()
    return node_id or None, _legacy_output_mode_port(output_mode)


def _legacy_output_mode_port(output_mode: Any) -> str:
    if str(output_mode or "").strip() == "on_completion":
        return AGENT_PROMPT_COMPLETED_PORT
    return AGENT_PROMPT_STARTED_PORT


def _migrate_edges(edges: list[Any], *, prompt_ports: dict[str, str]) -> None:
    if not prompt_ports:
        return
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source_id = str(edge.get("source_id") or "").strip()
        if source_id not in prompt_ports:
            continue
        if edge.get("source_port") is None:
            edge["source_port"] = prompt_ports[source_id]
