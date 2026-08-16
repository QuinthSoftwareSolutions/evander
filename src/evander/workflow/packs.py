from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

from evander.models import DirectoryPath
from evander.events.models import EdgeDefinition, WorkflowNodeEnvelope

SYSTEM_WORKFLOW_PACK_REFERENCE = "system.v1.json"
BUILTIN_HIDDEN_WORKFLOW_PACKS: tuple[str, ...] = (SYSTEM_WORKFLOW_PACK_REFERENCE,)


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


class WorkflowPackGraph(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str | None = None
    directory_path: str = "root"
    user_ids: list[str] = Field(default_factory=list)
    node_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_directory_paths(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        if "directory_path" in payload:
            payload.pop("directory_paths", None)
            return payload

        legacy_directory_paths = payload.pop("directory_paths", None)
        if legacy_directory_paths is None:
            payload["directory_path"] = "root"
            return payload

        if isinstance(legacy_directory_paths, str):
            raw_directory_paths = [legacy_directory_paths]
        elif isinstance(legacy_directory_paths, list):
            raw_directory_paths = legacy_directory_paths
        else:
            raw_directory_paths = [legacy_directory_paths]

        normalized_directory_paths: list[str] = []
        seen_directory_paths: set[str] = set()
        for raw_directory_path in raw_directory_paths:
            directory_path = str(raw_directory_path or "").strip()
            if not directory_path:
                continue
            normalized_directory_path = DirectoryPath(segments=directory_path).as_posix()
            if normalized_directory_path in seen_directory_paths:
                continue
            seen_directory_paths.add(normalized_directory_path)
            normalized_directory_paths.append(normalized_directory_path)

        if len(normalized_directory_paths) > 1:
            raise ValueError("Workflow graph directory_paths must contain at most one directory.")
        payload["directory_path"] = (
            normalized_directory_paths[0] if normalized_directory_paths else "root"
        )
        return payload

    @field_validator("user_ids", "node_ids", mode="before")
    @classmethod
    def _normalize_string_lists(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return value

    @field_validator("directory_path", mode="before")
    @classmethod
    def _normalize_directory_path(cls, value: object) -> str:
        directory_path = str(value or "").strip() or "root"
        return DirectoryPath(segments=directory_path).as_posix()

    @computed_field(return_type=list[str])
    @property
    def directory_paths(self) -> list[str]:
        return [self.directory_path]


class WorkflowPack(BaseModel):
    pack_id: str = Field(min_length=1)
    pack_version: str = Field(min_length=1)
    description: str | None = None
    graphs: list[WorkflowPackGraph] = Field(default_factory=list)
    ir: WorkflowIR


class WorkflowIRCompositionResult(BaseModel):
    graphs: list[WorkflowPackGraph] = Field(default_factory=list)
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


def _module_project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _packaged_builtin_pack_path(pack_reference: str) -> Path:
    return Path(__file__).resolve().parent / "builtin_packs" / pack_reference


def _pack_path(project_dir: Path, pack_reference: str) -> Path:
    normalized = _normalize_pack_reference(pack_reference)
    path = project_dir / "config" / "workflow-packs" / normalized
    if normalized in BUILTIN_HIDDEN_WORKFLOW_PACKS and not path.exists():
        fallback_path = _module_project_root() / "config" / "workflow-packs" / normalized
        if fallback_path.exists():
            return fallback_path
        packaged_path = _packaged_builtin_pack_path(normalized)
        if packaged_path.exists():
            return packaged_path
    return path


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
    built_in_workflow_packs: list[str] | tuple[str, ...] | None = None,
    inline_ir: dict[str, Any] | None = None,
) -> WorkflowIRCompositionResult:
    graphs: list[WorkflowPackGraph] = []
    nodes: list[WorkflowIRNode] = []
    edges: list[EdgeDefinition] = []
    loaded_packs: list[tuple[str, WorkflowPack]] = []

    for pack_reference in workflow_packs or []:
        pack = load_workflow_pack(project_dir, pack_reference)
        _validate_workflow_pack_internal_refs(pack, pack_reference=pack_reference)
        loaded_packs.append((pack_reference, pack))
        graphs.extend(pack.graphs)
        nodes.extend(pack.ir.nodes)
        edges.extend(pack.ir.edges)

    for pack_reference in built_in_workflow_packs or []:
        pack = load_workflow_pack(project_dir, pack_reference)
        _validate_workflow_pack_internal_refs(pack, pack_reference=pack_reference)
        loaded_packs.append((pack_reference, pack))
        graphs.extend(pack.graphs)
        nodes.extend(pack.ir.nodes)
        edges.extend(pack.ir.edges)

    _validate_global_workflow_pack_ids(loaded_packs)

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
        node_payload = ir_node.node.model_dump(mode="json", exclude_none=True)
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
        graphs=graphs,
        triggers=triggers,
        gates=gates,
        effects=effects,
        queries=queries,
        edges=[edge.model_dump(mode="json") for edge in edges],
    )


def _validate_workflow_pack_internal_refs(
    pack: WorkflowPack,
    *,
    pack_reference: object,
) -> None:
    node_ids = {ir_node.node.id for ir_node in pack.ir.nodes}
    for edge in pack.ir.edges:
        if edge.source_id in node_ids and edge.target_id in node_ids:
            continue
        raise ValueError(
            f"workflow pack {pack_reference!r} edge {edge.source_id!r} -> "
            f"{edge.target_id!r} must reference nodes from the same pack"
        )

    for graph in pack.graphs:
        unknown_node_ids = sorted(node_id for node_id in graph.node_ids if node_id not in node_ids)
        if unknown_node_ids:
            unknown_list = ", ".join(unknown_node_ids)
            raise ValueError(
                f"workflow pack {pack_reference!r} graph {graph.id!r} references "
                f"nodes outside the pack: {unknown_list}"
            )


def _validate_global_workflow_pack_ids(
    packs: list[tuple[str, WorkflowPack]],
) -> None:
    node_owners: dict[str, str] = {}
    graph_owners: dict[str, str] = {}
    webhook_slug_owners: dict[str, str] = {}

    for pack_reference, pack in packs:
        owner = str(pack_reference)
        for graph in pack.graphs:
            _record_unique_workflow_pack_id(
                graph_owners,
                graph.id,
                owner=owner,
                label="workflow graph",
            )
        for ir_node in pack.ir.nodes:
            node = ir_node.node
            _record_unique_workflow_pack_id(
                node_owners,
                node.id,
                owner=owner,
                label="workflow node",
            )
            if node.type != "trigger:http:webhook":
                continue
            slug = str(node.config.get("slug") or "").strip()
            if not slug:
                continue
            _record_unique_workflow_pack_id(
                webhook_slug_owners,
                slug,
                owner=owner,
                label="webhook slug",
            )


def _record_unique_workflow_pack_id(
    owners: dict[str, str],
    value: str,
    *,
    owner: str,
    label: str,
) -> None:
    if value in owners:
        raise ValueError(
            f"Duplicate {label} id {value!r} in workflow packs {owners[value]!r} and {owner!r}"
        )
    owners[value] = owner
