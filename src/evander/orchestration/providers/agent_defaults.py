from __future__ import annotations

from typing import Any, Literal

from evander.effects.providers.lifecycle_hooks import build_effect_lifecycle_hooks
from evander.orchestration.maintenance import MaintenanceResult
from evander.orchestration.isolated_maintenance import IsolatedMaintenanceHook
from .agent_completion import publish_configured_agent_completions
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

    @property
    def maintenance_name(self) -> str:
        return (
            getattr(self._hook, "maintenance_name", type(self._hook).__name__)
            + ":"
            + getattr(getattr(self._hook, "_provider_class", None), "__name__", "")
        )

    @property
    def maintenance_priority(self) -> int:
        return getattr(self._hook, "maintenance_priority", 0)

    def run_maintenance_slice(self, *, worker: Any, graph: Any, budget: Any) -> MaintenanceResult:
        sliced = getattr(self._hook, "on_waiting_reconciliation_slice", None)
        if callable(sliced):
            return sliced(worker=worker, budget=budget)
        return MaintenanceResult(changes=self.reconcile(worker=worker, graph=graph))

    def reconcile(self, *, worker: Any, graph: Any) -> int:
        del graph
        return int(self._hook.on_waiting_reconciliation(worker=worker) or 0)


class _ProviderCompletionDispatchHook:
    def __init__(self, hook: Any) -> None:
        self._hook = hook

    @property
    def maintenance_name(self) -> str:
        return getattr(self._hook, "maintenance_name", type(self._hook).__name__)

    def initialize(self, *, worker: Any) -> None:
        initialize = getattr(self._hook, "initialize", None)
        if callable(initialize):
            initialize(worker=worker)

    def close(self) -> None:
        close = getattr(self._hook, "close", None)
        if callable(close):
            close()

    def poll(self, *, worker: Any) -> int:
        poll = getattr(self._hook, "poll", None)
        return int(poll(worker=worker) or 0) if callable(poll) else 0

    @property
    def maintenance_priority(self) -> int:
        return getattr(self._hook, "maintenance_priority", 0)

    def run_maintenance_slice(self, *, worker: Any, graph: Any, budget: Any) -> MaintenanceResult:
        sliced = getattr(self._hook, "on_post_completion_dispatch_slice", None)
        if callable(sliced):
            return sliced(worker=worker, budget=budget)
        return MaintenanceResult(changes=self.dispatch(worker=worker))

    def on_prompt_execution_changed(self, *, worker: Any, record: Any) -> None:
        notify = getattr(self._hook, "on_prompt_execution_changed", None)
        if callable(notify):
            notify(worker=worker, record=record)

    def validate_queued_prompt(self, *, worker: Any, record: Any) -> bool:
        validate = getattr(self._hook, "validate_queued_prompt", None)
        return bool(validate(worker=worker, record=record)) if callable(validate) else True

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
        if (
            str(getattr(queue_record, "trigger_type", "") or "")
            != _AGENT_RUN_COMPLETED_TRIGGER_TYPE
        ):
            return None
        should_run = (phase == "pre" and not source_node_exists) or (
            phase == "post" and source_node_exists
        )
        if not should_run:
            return SpecialEventResult(consume_event=not source_node_exists)
        publish_configured_agent_completions(
            queue=worker.event_queue, record=queue_record, graph=graph
        )
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
    from evander.model_usage.worker_delivery import WorkerUsageDeliveryHook

    lifecycle_hooks = tuple(
        IsolatedMaintenanceHook(hook) if getattr(hook, "isolated_maintenance", False) else hook
        for hook in (
            *build_effect_lifecycle_hooks(provider_factory=provider_factory),
            WorkerUsageDeliveryHook(),
        )
    )
    return (
        (AgentCompletionSpecialEventHandler(lifecycle_hooks),),
        tuple(_ProviderLifecycleReconciliationHook(hook) for hook in lifecycle_hooks),
        tuple(_ProviderCompletionDispatchHook(hook) for hook in lifecycle_hooks),
    )
