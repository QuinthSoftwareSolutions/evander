from __future__ import annotations

from typing import Any, Literal

from ...effects.providers.lifecycle_hooks import build_effect_lifecycle_hooks
from ..effect_extensions import (
    CompletionDispatchHook,
    LifecycleReconciliationHook,
    SpecialEventHandler,
    SpecialEventResult,
)

_AGENT_RUN_COMPLETED_TRIGGER_TYPE = "trigger:agent:run:completed"


class _ProviderLifecycleReconciliationHook:
    def __init__(self, hook: Any) -> None:
        self._hook = hook

    def reconcile(self, *, worker: Any, graph: Any) -> int:
        del graph
        return int(self._hook.on_waiting_reconciliation(worker=worker) or 0)


class _ProviderCompletionDispatchHook:
    def __init__(self, hook: Any) -> None:
        self._hook = hook

    def dispatch(self, *, worker: Any) -> int:
        return int(self._hook.on_post_completion_dispatch(worker=worker) or 0)


class AgentCompletionSpecialEventHandler:
    def __init__(self, lifecycle_hooks: tuple[Any, ...]) -> None:
        self._lifecycle_hooks = lifecycle_hooks

    def handle_event(
        self,
        *,
        worker: Any,
        queue_record: Any,
        graph: Any,
        phase: Literal["pre", "post"],
        source_node_exists: bool,
    ) -> SpecialEventResult | None:
        if str(getattr(queue_record, "trigger_type", "") or "") != _AGENT_RUN_COMPLETED_TRIGGER_TYPE:
            return None
        should_run = (phase == "pre" and not source_node_exists) or (phase == "post" and source_node_exists)
        if not should_run:
            return SpecialEventResult(consume_event=not source_node_exists)
        for hook in self._lifecycle_hooks:
            hook.on_completion_fan_out(worker=worker, queue_record=queue_record, graph=graph)
        worker._handle_gate_completion_event(queue_record=queue_record, graph=graph)
        return SpecialEventResult(consume_event=not source_node_exists)

    def force_complete(self, *, worker: Any, dispatch_id: str) -> bool:
        graph = worker._build_execution_graph()
        provider = worker._get_gate_provider("gate:await-agent")
        if provider is None:
            return False
        return bool(provider.force_complete(worker=worker, graph=graph, dispatch_id=dispatch_id))


def build_agent_default_effect_handlers(
    *,
    provider_factory: Any,
) -> tuple[
    tuple[SpecialEventHandler, ...],
    tuple[LifecycleReconciliationHook, ...],
    tuple[CompletionDispatchHook, ...],
]:
    lifecycle_hooks = build_effect_lifecycle_hooks(provider_factory=provider_factory)
    return (
        (AgentCompletionSpecialEventHandler(lifecycle_hooks),),
        tuple(_ProviderLifecycleReconciliationHook(hook) for hook in lifecycle_hooks),
        tuple(_ProviderCompletionDispatchHook(hook) for hook in lifecycle_hooks),
    )
