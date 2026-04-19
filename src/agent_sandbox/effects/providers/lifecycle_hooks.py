from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from agent_sandbox.effects.providers.base import EffectLifecycleHookBase
class ProviderCompletionLifecycleHook(EffectLifecycleHookBase):
    def __init__(self, provider_class: type[Any]) -> None:
        self._provider_class = provider_class

    def on_waiting_reconciliation(self, *, worker: Any) -> int:
        return int(self._provider_class.reconcile_waiting(worker=worker) or 0)

    def on_completion_fan_out(self, *, worker: Any, queue_record: Any, graph: Any) -> int:
        del graph
        return int(self._provider_class.on_completion_event(worker=worker, queue_record=queue_record) or 0)


class AgentEnqueuePromptQueueDispatchHook(EffectLifecycleHookBase):
    def on_post_completion_dispatch(self, *, worker: Any) -> int:
        queue_store = getattr(worker, "queued_agent_prompt_store", None)
        provider_factory = getattr(worker, "provider_factory", None)
        admin_service = getattr(provider_factory, "admin_service", None)
        if queue_store is None or admin_service is None:
            return 0

        queued_records = queue_store.read_all()
        if not queued_records:
            return 0

        summaries = {
            str(getattr(item, "vm_name", "") or "").strip(): item
            for item in admin_service.list_vms(include_inactive=True, vm_user="agent")
        }

        dispatched = 0
        for queue_key, record in sorted(queued_records.items()):
            vm_name = str(record.vm_name or "").strip()
            summary = summaries.get(vm_name)
            if summary is not None and str(getattr(summary, "agent_state", "") or "").strip() == "running":
                continue

            dispatch_id = f"dispatch-{record.effect_id}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}"
            result = admin_service.resume_agent(
                target=record.target,
                prompt=record.prompt,
                model=record.model,
                vm_user=record.vm_user,
                dispatch_id=dispatch_id,
                resume_thread_id=record.resume_thread_id,
            )
            if bool(getattr(result, "success", False)):
                queue_store.delete(queue_key)
                dispatched += 1

        return dispatched


def build_effect_lifecycle_hooks(*, provider_factory: Any) -> tuple[EffectLifecycleHookBase, ...]:
    hooks: list[EffectLifecycleHookBase] = []

    for effect_type in provider_factory.registry.registered_types():
        provider_class = provider_factory.registry.resolve(effect_type)
        hooks.append(ProviderCompletionLifecycleHook(provider_class))
        for hook_class in provider_class.lifecycle_hooks():
            hooks.append(hook_class())
    return tuple(hooks)
