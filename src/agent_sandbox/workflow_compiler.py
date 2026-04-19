from __future__ import annotations

from dataclasses import dataclass

from .config import TriggerOrchestratorWorkerGlobalConfig

DEFAULT_AWAIT_AGENT_COMPLETION_TRIGGER_ID = "__await-agent-completion__"


@dataclass(frozen=True)
class WorkflowCompilationPolicies:
    augment_await_agent_completion: bool = False


def compile_trigger_orchestrator_config(
    worker_config: TriggerOrchestratorWorkerGlobalConfig,
    *,
    policies: WorkflowCompilationPolicies | None = None,
) -> TriggerOrchestratorWorkerGlobalConfig:
    resolved_policies = policies or WorkflowCompilationPolicies()
    if not resolved_policies.augment_await_agent_completion:
        return worker_config

    payload = worker_config.model_dump(mode="json")
    trigger_payloads = payload.get("triggers", [])
    gate_payloads = payload.get("gates", [])
    if not isinstance(trigger_payloads, list) or not isinstance(gate_payloads, list):
        return worker_config

    await_gate_payloads = [
        gate for gate in gate_payloads if isinstance(gate, dict) and gate.get("type") == "gate:await-agent"
    ]
    if not await_gate_payloads:
        return worker_config

    trigger_by_id = {trigger.get("id"): trigger for trigger in trigger_payloads if isinstance(trigger, dict)}
    existing_completion_ids = [
        trigger.get("id")
        for trigger in trigger_payloads
        if isinstance(trigger, dict) and trigger.get("type") == "trigger:agent:run:completed"
    ]
    fallback_completion_id = next(
        (trigger_id for trigger_id in existing_completion_ids if isinstance(trigger_id, str) and trigger_id.strip()),
        DEFAULT_AWAIT_AGENT_COMPLETION_TRIGGER_ID,
    )

    required_completion_ids: set[str] = set()
    changed = False
    for gate in await_gate_payloads:
        configured_id = gate.get("completion_trigger_id")
        completion_id = configured_id if isinstance(configured_id, str) and configured_id.strip() else fallback_completion_id
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

