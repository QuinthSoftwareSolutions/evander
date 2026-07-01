from __future__ import annotations

from evander.config import TriggerOrchestratorWorkerGlobalConfig
from evander.events.models import effective_node_output_schema
from evander.workflow.validation import WorkflowValidationDiagnostic

DEFAULT_AWAIT_AGENT_COMPLETION_TRIGGER_ID = "__await-agent-completion__"


def augment_await_agent_completion(
    worker_config: TriggerOrchestratorWorkerGlobalConfig,
) -> TriggerOrchestratorWorkerGlobalConfig:
    payload = worker_config.model_dump(mode="json")
    trigger_payloads = payload.get("triggers", [])
    gate_payloads = payload.get("gates", [])
    if not isinstance(trigger_payloads, list) or not isinstance(gate_payloads, list):
        return worker_config

    await_gate_payloads = [
        gate
        for gate in gate_payloads
        if isinstance(gate, dict) and gate.get("type") == "gate:await-agent"
    ]
    if not await_gate_payloads:
        return worker_config

    trigger_by_id = {
        trigger.get("id"): trigger for trigger in trigger_payloads if isinstance(trigger, dict)
    }
    existing_completion_ids = [
        trigger.get("id")
        for trigger in trigger_payloads
        if isinstance(trigger, dict) and trigger.get("type") == "trigger:agent:run:completed"
    ]
    fallback_completion_id = next(
        (
            trigger_id
            for trigger_id in existing_completion_ids
            if isinstance(trigger_id, str) and trigger_id.strip()
        ),
        DEFAULT_AWAIT_AGENT_COMPLETION_TRIGGER_ID,
    )

    required_completion_ids: set[str] = set()
    changed = False
    for gate in await_gate_payloads:
        configured_id = gate.get("completion_trigger_id")
        completion_id = (
            configured_id
            if isinstance(configured_id, str) and configured_id.strip()
            else fallback_completion_id
        )
        required_completion_ids.add(completion_id)
        if configured_id != completion_id:
            gate["completion_trigger_id"] = completion_id
            changed = True

    for completion_id in sorted(required_completion_ids):
        existing_trigger = trigger_by_id.get(completion_id)
        if existing_trigger is None:
            trigger_payloads.append(
                {
                    "id": completion_id,
                    "display_name": "Await agent completion",
                    "type": "trigger:agent:run:completed",
                    "vm_user": "agent",
                }
            )
            changed = True
            continue
        existing_type = existing_trigger.get("type")
        if existing_type != "trigger:agent:run:completed":
            raise ValueError(
                f"await-agent completion trigger {completion_id!r} must use trigger:agent:run:completed, found {existing_type!r}"
            )

    if not changed:
        return worker_config
    payload["triggers"] = trigger_payloads
    payload["gates"] = gate_payloads
    return TriggerOrchestratorWorkerGlobalConfig.model_validate(payload)


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
