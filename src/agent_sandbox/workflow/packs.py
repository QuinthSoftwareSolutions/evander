from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from agent_sandbox.events.models import EdgeDefinition, WorkflowNodeEnvelope


class WorkflowNodeRetryPolicy(BaseModel):
    max_attempts: int = Field(default=1, ge=1)
    backoff_seconds: float = Field(default=0.0, ge=0.0)


class WorkflowNodeIdempotencyPolicy(BaseModel):
    key_template: str | None = None


class WorkflowIRNode(BaseModel):
    node: WorkflowNodeEnvelope
    bindings: dict[str, Any] = Field(default_factory=dict)
    retry: WorkflowNodeRetryPolicy | None = None
    idempotency: WorkflowNodeIdempotencyPolicy | None = None

    @field_validator("node", mode="before")
    @classmethod
    def _materialize_node(cls, value: object) -> object:
        if isinstance(value, dict):
            return value
        return value


class WorkflowIR(BaseModel):
    version: str = "v1"
    nodes: list[WorkflowIRNode] = Field(default_factory=list)
    edges: list[EdgeDefinition] = Field(default_factory=list)


class WorkflowPack(BaseModel):
    pack_id: str = Field(min_length=1)
    pack_version: str = Field(min_length=1)
    description: str | None = None
    ir: WorkflowIR


class WorkflowIRCompositionResult(BaseModel):
    triggers: list[dict[str, Any]] = Field(default_factory=list)
    gates: list[dict[str, Any]] = Field(default_factory=list)
    effects: list[dict[str, Any]] = Field(default_factory=list)
    queries: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)


def _normalize_pack_reference(pack: str) -> str:
    text = str(pack).strip()
    if not text:
        raise ValueError("workflow pack reference must be a non-empty string")
    if text.endswith(".json"):
        return text
    return f"{text}.json"


def _pack_path(project_dir: Path, pack_reference: str) -> Path:
    normalized = _normalize_pack_reference(pack_reference)
    return project_dir / "config" / "workflow-packs" / normalized


def load_workflow_pack(project_dir: Path, pack_reference: str) -> WorkflowPack:
    path = _pack_path(project_dir, pack_reference)
    if not path.exists():
        raise ValueError(f"workflow pack {pack_reference!r} does not exist at {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"workflow pack file {path} must contain a JSON object")

    ir_payload = payload.get("ir")
    if isinstance(ir_payload, dict):
        nodes_payload = ir_payload.get("nodes")
        if isinstance(nodes_payload, list):
            converted_nodes: list[dict[str, Any]] = []
            for node in nodes_payload:
                if isinstance(node, dict) and "node" not in node:
                    converted_nodes.append({"node": node})
                else:
                    converted_nodes.append(node)
            ir_payload["nodes"] = converted_nodes

    return WorkflowPack.model_validate(payload)


def compose_workflow_ir(
    *,
    project_dir: Path,
    workflow_packs: list[str] | None,
    inline_ir: dict[str, Any] | None = None,
) -> WorkflowIRCompositionResult:
    nodes: list[WorkflowIRNode] = []
    edges: list[EdgeDefinition] = []

    for pack_reference in workflow_packs or []:
        pack = load_workflow_pack(project_dir, pack_reference)
        nodes.extend(pack.ir.nodes)
        edges.extend(pack.ir.edges)

    if inline_ir:
        inline_payload = dict(inline_ir)
        inline_nodes = inline_payload.get("nodes")
        if isinstance(inline_nodes, list):
            normalized_inline_nodes: list[dict[str, Any]] = []
            for node in inline_nodes:
                if isinstance(node, dict) and "node" not in node:
                    normalized_inline_nodes.append({"node": node})
                else:
                    normalized_inline_nodes.append(node)
            inline_payload["nodes"] = normalized_inline_nodes
        inline_model = WorkflowIR.model_validate(inline_payload)
        nodes.extend(inline_model.nodes)
        edges.extend(inline_model.edges)

    triggers: list[dict[str, Any]] = []
    gates: list[dict[str, Any]] = []
    effects: list[dict[str, Any]] = []
    queries: list[dict[str, Any]] = []

    for ir_node in nodes:
        node_payload = ir_node.node.model_dump(mode="json")
        node_type = str(node_payload.get("type") or "")
        if node_type.startswith("trigger:"):
            triggers.append(node_payload)
        elif node_type.startswith("gate:"):
            gates.append(node_payload)
        elif node_type.startswith("effect:"):
            effects.append(node_payload)
        elif node_type.startswith("query:"):
            queries.append(node_payload)
        else:
            raise ValueError(f"Unsupported workflow IR node type: {node_type!r}")

    return WorkflowIRCompositionResult(
        triggers=triggers,
        gates=gates,
        effects=effects,
        queries=queries,
        edges=[edge.model_dump(mode="json") for edge in edges],
    )
