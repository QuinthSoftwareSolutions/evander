from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from evander.workflow.binding import BindingResolutionError, _parse_binding_expression
from evander.workflow.contracts import GATE_FAILURE_OUTPUT_SCHEMA, effective_node_output_schema
from evander.workflow.graph import (
    build_inbound_edge_index,
    merge_output_schema_intersection,
    would_form_cycle,
)


@dataclass(frozen=True)
class NodeCapability:
    min_inbound: int | None = None
    max_inbound: int | None = None
    non_cyclic_inbound_exact: int | None = None
    allowed_source_ports: frozenset[str | None] | None = None
    required_source_ports: frozenset[str] = field(default_factory=frozenset)
    completion_semantics: str | None = None


class WorkflowValidationDiagnostic(BaseModel):
    code: str
    message: str
    severity: str = "error"
    node_id: str | None = None
    node_type: str | None = None
    field_path: str | None = None
    reference: str | None = None


def validate_workflow_graph(
    *,
    nodes: Iterable[Any],
    edges: Iterable[Any],
    trigger_node_ids: Iterable[str],
    node_capabilities: Mapping[str, NodeCapability] | None = None,
) -> list[str]:
    """Validate workflow graph structure and optional node capability rules."""
    nodes_by_id = {str(node.id): node for node in nodes}
    edges_list = list(edges)
    trigger_ids = {str(node_id) for node_id in trigger_node_ids}
    inbound_edges = build_inbound_edge_index(edges_list)
    downstream_non_cyclic: dict[str, list[str]] = {}
    outbound_edges_by_source: dict[str, list[Any]] = {}

    errors: list[str] = []
    for edge in edges_list:
        outbound_edges_by_source.setdefault(edge.source_id, []).append(edge)
        if bool(getattr(edge, "allow_cycle", False)):
            continue
        if would_form_cycle(edge.source_id, edge.target_id, downstream_non_cyclic):
            errors.append(
                f"Non-cyclic edges cannot form a cycle: {edge.source_id} -> {edge.target_id}"
            )
            continue
        downstream_non_cyclic.setdefault(edge.source_id, []).append(edge.target_id)

    missing_endpoints: set[str] = set()
    self_edges: set[str] = set()
    for edge in edges_list:
        source_known = edge.source_id in nodes_by_id
        target_known = edge.target_id in nodes_by_id
        if edge.source_id == edge.target_id:
            self_edges.add(edge.source_id)
        if not source_known:
            missing_endpoints.add(edge.source_id)
        if not target_known:
            missing_endpoints.add(edge.target_id)

    if missing_endpoints:
        missing_list = ", ".join(sorted(missing_endpoints))
        errors.append(f"Edge endpoints must reference existing node IDs: {missing_list}")

    if self_edges:
        self_edge_list = ", ".join(sorted(self_edges))
        errors.append(f"Edges cannot connect a node to itself: {self_edge_list}")

    trigger_inbound_errors: list[str] = []
    for trigger_node_id in sorted(trigger_ids):
        inbound_count = len(inbound_edges.get(trigger_node_id, ()))
        if inbound_count != 0:
            trigger_inbound_errors.append(
                f"trigger {trigger_node_id} must have 0 inbound edges, found {inbound_count}"
            )
    errors.extend(trigger_inbound_errors)

    if not node_capabilities:
        return errors

    reachable_node_ids = set(trigger_ids)
    stack = list(trigger_ids)
    while stack:
        current = stack.pop()
        for edge in outbound_edges_by_source.get(current, ()):
            if edge.target_id in reachable_node_ids:
                continue
            reachable_node_ids.add(edge.target_id)
            stack.append(edge.target_id)

    capability_errors: list[str] = []
    for node in nodes_by_id.values():
        if node.id in trigger_ids or node.id not in reachable_node_ids:
            continue
        capability = node_capabilities.get(node.type)
        if capability is None:
            continue
        inbound = list(inbound_edges.get(node.id, ()))
        inbound_count = len(inbound)
        if capability.min_inbound is not None and inbound_count < capability.min_inbound:
            capability_errors.append(
                f"{node.type} {node.id} must have at least {capability.min_inbound} inbound edge(s), found {inbound_count}"
            )
        if capability.max_inbound is not None and inbound_count > capability.max_inbound:
            capability_errors.append(
                f"{node.type} {node.id} must have at most {capability.max_inbound} inbound edge(s), found {inbound_count}"
            )
        if capability.non_cyclic_inbound_exact is not None:
            non_cyclic_count = sum(
                1 for edge in inbound if not bool(getattr(edge, "allow_cycle", False))
            )
            if non_cyclic_count != capability.non_cyclic_inbound_exact:
                capability_errors.append(
                    f"{node.type} {node.id} must have exactly {capability.non_cyclic_inbound_exact} non-cyclic inbound edge(s), found {non_cyclic_count}"
                )
        if capability.allowed_source_ports is not None:
            outbound_ports = [
                edge.source_port for edge in outbound_edges_by_source.get(node.id, ())
            ]
            invalid_ports = [
                port for port in outbound_ports if port not in capability.allowed_source_ports
            ]
            if invalid_ports:
                capability_errors.append(
                    f"{node.type} {node.id} uses invalid source_port value(s): {', '.join(sorted({str(port) for port in invalid_ports}))}"
                )
        for required_port in sorted(capability.required_source_ports):
            if not any(
                edge.source_port == required_port
                for edge in outbound_edges_by_source.get(node.id, ())
            ):
                capability_errors.append(
                    f"{node.type} {node.id} must have an outbound edge with source_port={required_port}"
                )
    errors.extend(capability_errors)
    return errors


def validate_workflow_binding_diagnostics(
    config: Mapping[str, Any],
) -> list[WorkflowValidationDiagnostic]:
    """Return advisory diagnostics for `${{node.output}}` config bindings."""

    nodes = list(_iter_workflow_nodes(config))
    nodes_by_id = {node_id: node for node_id, node in nodes}
    all_node_ids = set(nodes_by_id)
    edges = config.get("edges", [])
    edge_list = edges if isinstance(edges, list) else []
    output_schemas = derive_workflow_output_schemas(config)
    dominators_by_node = _compute_dominators(edge_list, set(nodes_by_id))

    diagnostics: list[WorkflowValidationDiagnostic] = []
    for node_id, node in nodes:
        node_type = str(node.get("type") or "").strip()
        ancestor_ids = _collect_ancestor_node_ids(edge_list, node_id)
        declared_outputs = {
            ancestor_id: output_schemas.get(ancestor_id, {})
            for ancestor_id in ancestor_ids
            if ancestor_id in nodes_by_id
        }
        for field_path, value in _walk_node_config_values(node):
            diagnostics.extend(
                _validate_binding_value(
                    value=value,
                    field_path=field_path,
                    node_id=node_id,
                    node_type=node_type,
                    declared_outputs=declared_outputs,
                    guaranteed_node_ids=dominators_by_node.get(node_id, {node_id}) - {node_id},
                    all_node_ids=all_node_ids,
                )
            )
    return diagnostics


def derive_workflow_output_schemas(
    config: Mapping[str, Any],
) -> dict[str, dict[str, dict[str, Any]]]:
    """Return graph-aware output schemas for workflow nodes.

    Some gates do not own a static output contract. A filter gate forwards its
    single inbound payload, and a merge gate emits the compatible intersection
    of its inbound payloads. Derive those contracts from the configured graph so
    binding validation does not treat dynamic gate outputs as missing.
    """

    nodes = list(_iter_workflow_nodes(config))
    nodes_by_id = {node_id: node for node_id, node in nodes}
    edges = config.get("edges", [])
    edge_list = edges if isinstance(edges, list) else []
    cache: dict[str, dict[str, dict[str, Any]]] = {}
    for node_id in nodes_by_id:
        _derive_workflow_node_output_schema(
            node_id=node_id,
            nodes_by_id=nodes_by_id,
            edges=edge_list,
            cache=cache,
            visiting=set(),
        )
    return cache


def _derive_workflow_node_output_schema(
    *,
    node_id: str,
    nodes_by_id: Mapping[str, Mapping[str, Any]],
    edges: list[Any],
    cache: dict[str, dict[str, dict[str, Any]]],
    visiting: set[str],
) -> dict[str, dict[str, Any]]:
    cached = cache.get(node_id)
    if cached is not None:
        return cached
    node = nodes_by_id.get(node_id)
    if node is None:
        return {}
    output_node = _flatten_node_config(node)
    if node_id in visiting:
        return _copy_output_schema(effective_node_output_schema(output_node))

    visiting.add(node_id)
    node_type = str(output_node.get("type") or "").strip()
    if node_type in {"gate:filter", "gate:debounce", "gate:throttle", "gate:switch", "gate:classifier:switch"}:
        upstream_ids = _collect_direct_upstream_node_ids(edges, node_id)
        schema = (
            _derive_workflow_node_output_schema(
                node_id=upstream_ids[0],
                nodes_by_id=nodes_by_id,
                edges=edges,
                cache=cache,
                visiting=visiting,
            )
            if upstream_ids
            else {}
        )
    elif node_type in {"gate:validate", "gate:event:validate"}:
        upstream_ids = _collect_direct_upstream_node_ids(edges, node_id)
        schema = (
            _derive_workflow_node_output_schema(
                node_id=upstream_ids[0],
                nodes_by_id=nodes_by_id,
                edges=edges,
                cache=cache,
                visiting=visiting,
            )
            if upstream_ids
            else {}
        )
        schema = _copy_output_schema(schema)
        schema.update(_copy_output_schema(effective_node_output_schema(output_node)))
    elif node_type == "gate:counter":
        upstream_ids = _collect_direct_upstream_node_ids(edges, node_id)
        schema = (
            _derive_workflow_node_output_schema(
                node_id=upstream_ids[0],
                nodes_by_id=nodes_by_id,
                edges=edges,
                cache=cache,
                visiting=visiting,
            )
            if upstream_ids
            else {}
        )
        schema = _copy_output_schema(schema)
        schema["count"] = {"type": "integer"}
    elif node_type == "gate:dedupe":
        upstream_ids = _collect_direct_upstream_node_ids(edges, node_id)
        schema = (
            _derive_workflow_node_output_schema(
                node_id=upstream_ids[0],
                nodes_by_id=nodes_by_id,
                edges=edges,
                cache=cache,
                visiting=visiting,
            )
            if upstream_ids
            else {}
        )
    elif node_type == "gate:merge":
        upstream_schemas = [
            _derive_workflow_node_output_schema(
                node_id=upstream_id,
                nodes_by_id=nodes_by_id,
                edges=edges,
                cache=cache,
                visiting=visiting,
            )
            for upstream_id in _collect_direct_upstream_node_ids(edges, node_id)
            if upstream_id in nodes_by_id
        ]
        schema = merge_output_schema_intersection(*upstream_schemas) if upstream_schemas else {}
    elif node_type == "gate:await-agent":
        schema = effective_node_output_schema({"type": "trigger:agent:run:completed"})
    else:
        schema = effective_node_output_schema(output_node)
    visiting.remove(node_id)

    normalized = _copy_output_schema(schema)
    if node_type.startswith("gate:"):
        normalized.update(_copy_output_schema(GATE_FAILURE_OUTPUT_SCHEMA))
    cache[node_id] = normalized
    return normalized


def _flatten_node_config(node: Mapping[str, Any]) -> dict[str, Any]:
    flattened = dict(node)
    config = flattened.pop("config", None)
    if isinstance(config, Mapping):
        flattened = {
            **{str(key): deepcopy(value) for key, value in config.items()},
            **flattened,
        }
    return flattened


def _collect_direct_upstream_node_ids(edges: list[Any], node_id: str) -> tuple[str, ...]:
    upstream_ids: list[str] = []
    for edge in edges:
        if not isinstance(edge, Mapping):
            continue
        source_id = str(edge.get("source_id") or "").strip()
        target_id = str(edge.get("target_id") or "").strip()
        if source_id and target_id == node_id:
            upstream_ids.append(source_id)
    return tuple(upstream_ids)


def _copy_output_schema(
    schema: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    return {str(key): dict(value) for key, value in schema.items() if isinstance(value, Mapping)}


def _iter_workflow_nodes(config: Mapping[str, Any]) -> Iterable[tuple[str, Mapping[str, Any]]]:
    for collection_name in ("triggers", "gates", "effects", "queries"):
        entries = config.get(collection_name, [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            node_id = str(entry.get("id") or "").strip()
            if not node_id:
                continue
            yield node_id, entry


def _collect_ancestor_node_ids(edges: list[Any], node_id: str) -> tuple[str, ...]:
    inbound_by_target: dict[str, list[str]] = {}
    for edge in edges:
        if not isinstance(edge, Mapping):
            continue
        source_id = str(edge.get("source_id") or "").strip()
        target_id = str(edge.get("target_id") or "").strip()
        if not source_id or not target_id:
            continue
        inbound_by_target.setdefault(target_id, []).append(source_id)

    ordered: list[str] = []
    queue = list(inbound_by_target.get(node_id, ()))
    seen: set[str] = set()
    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        ordered.append(current)
        queue.extend(inbound_by_target.get(current, ()))
    return tuple(ordered)


def _walk_node_config_values(node: Mapping[str, Any]) -> Iterable[tuple[str, Any]]:
    for key, value in node.items():
        if key in {"id", "display_name", "type"}:
            continue
        if key == "config" and isinstance(value, Mapping):
            for config_key, config_value in value.items():
                yield from _walk_config_value(config_value, path=str(config_key))
            continue
        yield from _walk_config_value(value, path=str(key))


def _walk_config_value(value: Any, *, path: str) -> Iterable[tuple[str, Any]]:
    if isinstance(value, Mapping):
        if not value:
            yield path, value
            return
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            yield from _walk_config_value(child, path=child_path)
        return
    if isinstance(value, list):
        if not value:
            yield path, value
            return
        for index, child in enumerate(value):
            yield from _walk_config_value(child, path=f"{path}[{index}]")
        return
    yield path, value


def _validate_binding_value(
    *,
    value: Any,
    field_path: str,
    node_id: str,
    node_type: str,
    declared_outputs: Mapping[str, Mapping[str, Any]],
    guaranteed_node_ids: set[str],
    all_node_ids: set[str],
) -> list[WorkflowValidationDiagnostic]:
    if not isinstance(value, str) or "${{" not in value:
        return []

    diagnostics: list[WorkflowValidationDiagnostic] = []
    index = 0
    while True:
        start = value.find("${{", index)
        if start == -1:
            return diagnostics
        end = value.find("}}", start + 3)
        if end == -1:
            reference = value[start:]
            diagnostics.append(
                WorkflowValidationDiagnostic(
                    code="malformed_binding",
                    message=f"{field_path} contains malformed output reference",
                    node_id=node_id,
                    node_type=node_type,
                    field_path=field_path,
                    reference=reference,
                )
            )
            return diagnostics
        source_text = value[start : end + 2]
        expression_text = value[start + 3 : end]
        try:
            expression = _parse_binding_expression(expression_text, source_text=source_text)
        except BindingResolutionError:
            fallback_hint = "??" in expression_text
            diagnostics.append(
                WorkflowValidationDiagnostic(
                    code="malformed_binding_fallback" if fallback_hint else "malformed_binding",
                    message=(
                        f"{field_path} has malformed fallback syntax"
                        if fallback_hint
                        else f"{field_path} contains malformed output reference"
                    ),
                    node_id=node_id,
                    node_type=node_type,
                    field_path=field_path,
                    reference=source_text,
                )
            )
            index = end + 2
            continue
        diagnostics.extend(
            _validate_binding_expression(
                expression=expression,
                field_path=field_path,
                node_id=node_id,
                node_type=node_type,
                declared_outputs=declared_outputs,
                guaranteed_node_ids=guaranteed_node_ids,
                all_node_ids=all_node_ids,
            )
        )
        index = end + 2


def _validate_binding_expression(
    *,
    expression: Any,
    field_path: str,
    node_id: str,
    node_type: str,
    declared_outputs: Mapping[str, Mapping[str, Any]],
    guaranteed_node_ids: set[str],
    all_node_ids: set[str],
) -> list[WorkflowValidationDiagnostic]:
    diagnostics = _validate_binding_reference(
        reference=expression.primary,
        field_path=field_path,
        node_id=node_id,
        node_type=node_type,
        declared_outputs=declared_outputs,
        guaranteed_node_ids=guaranteed_node_ids,
        all_node_ids=all_node_ids,
    )
    if not bool(getattr(expression, "has_fallback", False)):
        return diagnostics
    fallback = expression.fallback
    if _is_binding_expression(fallback):
        diagnostics.extend(
            _validate_binding_expression(
                expression=fallback,
                field_path=field_path,
                node_id=node_id,
                node_type=node_type,
                declared_outputs=declared_outputs,
                guaranteed_node_ids=guaranteed_node_ids,
                all_node_ids=all_node_ids,
            )
        )
    elif _is_binding_reference(fallback):
        diagnostics.extend(
            _validate_binding_reference(
                reference=fallback,
                field_path=field_path,
                node_id=node_id,
                node_type=node_type,
                declared_outputs=declared_outputs,
                guaranteed_node_ids=guaranteed_node_ids,
                all_node_ids=all_node_ids,
            )
        )
    return diagnostics


def _validate_binding_reference(
    *,
    reference: Any,
    field_path: str,
    node_id: str,
    node_type: str,
    declared_outputs: Mapping[str, Mapping[str, Any]],
    guaranteed_node_ids: set[str],
    all_node_ids: set[str],
) -> list[WorkflowValidationDiagnostic]:
    resolved_node_id, output_path = _resolve_available_reference(
        reference_node_id=str(reference.node_id),
        output_path=str(reference.output_path),
        declared_outputs=declared_outputs,
        all_node_ids=all_node_ids,
    )
    output_ref = f"{resolved_node_id}.{output_path}"
    if resolved_node_id == "system":
        return []

    if resolved_node_id not in declared_outputs:
        relation = "non-upstream" if resolved_node_id in all_node_ids else "deleted"
        return [
            WorkflowValidationDiagnostic(
                code="invalid_binding_node",
                message=f"{field_path} references {relation} node {resolved_node_id}",
                node_id=node_id,
                node_type=node_type,
                field_path=field_path,
                reference=output_ref,
            )
        ]

    output_name = output_path.split(".", 1)[0]
    if output_name not in declared_outputs.get(resolved_node_id, {}):
        return [
            WorkflowValidationDiagnostic(
                code="missing_binding_output",
                message=f"{field_path} references missing output {output_ref}",
                node_id=node_id,
                node_type=node_type,
                field_path=field_path,
                reference=output_ref,
            )
        ]
    if resolved_node_id not in guaranteed_node_ids:
        return [
            WorkflowValidationDiagnostic(
                code="conditional_binding_node",
                message=(
                    f"{field_path} references conditionally available upstream node "
                    f"{resolved_node_id}; bind through a merge-projected output when this "
                    "value must be available after branching"
                ),
                severity="warning",
                node_id=node_id,
                node_type=node_type,
                field_path=field_path,
                reference=output_ref,
            )
        ]
    return []


def _resolve_available_reference(
    *,
    reference_node_id: str,
    output_path: str,
    declared_outputs: Mapping[str, Mapping[str, Any]],
    all_node_ids: set[str],
) -> tuple[str, str]:
    full_reference = f"{reference_node_id}.{output_path}"
    segments = tuple(segment for segment in full_reference.split(".") if segment)
    for index in range(len(segments) - 1, 0, -1):
        candidate_node_id = ".".join(segments[:index])
        candidate_output_path = ".".join(segments[index:])
        if (
            candidate_node_id == "system"
            or candidate_node_id in declared_outputs
            or candidate_node_id in all_node_ids
        ):
            return candidate_node_id, candidate_output_path
    return reference_node_id, output_path


def _compute_dominators(edges: list[Any], node_ids: set[str]) -> dict[str, set[str]]:
    """Return node -> nodes that are guaranteed to execute before it on acyclic paths."""

    predecessors: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    for edge in edges:
        if not isinstance(edge, Mapping):
            continue
        if bool(edge.get("allow_cycle", False)):
            continue
        source_id = str(edge.get("source_id") or "").strip()
        target_id = str(edge.get("target_id") or "").strip()
        if source_id in node_ids and target_id in node_ids:
            predecessors[target_id].add(source_id)

    dominators: dict[str, set[str]] = {}
    for node_id in node_ids:
        dominators[node_id] = {node_id} if not predecessors[node_id] else set(node_ids)

    changed = True
    while changed:
        changed = False
        for node_id in sorted(node_ids):
            preds = predecessors[node_id]
            if not preds:
                next_dominators = {node_id}
            else:
                shared = set(node_ids)
                for predecessor_id in preds:
                    shared &= dominators.get(predecessor_id, {predecessor_id})
                next_dominators = shared | {node_id}
            if next_dominators != dominators[node_id]:
                dominators[node_id] = next_dominators
                changed = True
    return dominators


def _is_binding_expression(value: Any) -> bool:
    return hasattr(value, "primary") and hasattr(value, "has_fallback")


def _is_binding_reference(value: Any) -> bool:
    return hasattr(value, "node_id") and hasattr(value, "output_path")
