from __future__ import annotations

from evander.config import TriggerOrchestratorWorkerGlobalConfig
from evander.events.models import effective_node_output_schema
from evander.workflow.validation import WorkflowValidationDiagnostic


def augment_await_agent_completion(
    worker_config: TriggerOrchestratorWorkerGlobalConfig,
) -> TriggerOrchestratorWorkerGlobalConfig:
    """Accept the retired transform name without rewriting the workflow."""
    return worker_config


def validate_await_agent_gate(
    *,
    node: object,
    nodes_by_id: dict[str, object],
    inbound_edges: dict[str, list[object]],
) -> list[WorkflowValidationDiagnostic]:
    node_id = str(getattr(node, "id", "")).strip()
    node_type = str(getattr(node, "type", "")).strip() or None
    diagnostics: list[WorkflowValidationDiagnostic] = []
    inbound = list(inbound_edges.get(node_id, ()))
    if len(inbound) != 1:
        diagnostics.append(
            WorkflowValidationDiagnostic(
                code="await_agent_inbound_arity",
                message=f"gate:await-agent {node_id} should have exactly one inbound edge (found {len(inbound)})",
                node_id=node_id or None,
                node_type=node_type,
            )
        )
        return diagnostics
    upstream = nodes_by_id.get(str(getattr(inbound[0], "source_id", "")).strip())
    upstream_type = str(getattr(upstream, "type", "")).strip()
    if upstream_type != "effect:agent:prompt":
        diagnostics.append(
            WorkflowValidationDiagnostic(
                code="await_agent_upstream_type",
                message=f"gate:await-agent {node_id} inbound source should be effect:agent:prompt (found {upstream_type or 'unknown'})",
                node_id=node_id or None,
                node_type=node_type,
            )
        )
    expected_outputs = effective_node_output_schema({"type": "trigger:agent:run:completed"})
    actual_outputs = dict(getattr(node, "outputs", None) or {})
    if actual_outputs and actual_outputs != expected_outputs:
        diagnostics.append(
            WorkflowValidationDiagnostic(
                code="await_agent_outputs_mismatch",
                message=f"gate:await-agent {node_id} outputs should match agent completion outputs",
                node_id=node_id or None,
                node_type=node_type,
            )
        )
    return diagnostics
