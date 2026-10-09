"""Backend-aware local inference lease reconciliation shared by prompt effects."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from evander.execution import ExecutionBackendKind
from evander.local_inference_capacity import (
    LocalInferenceCapacityLease,
    LocalInferenceCapacityStore,
    local_inference_execution_is_active,
    local_inference_vm_lease_is_active,
)

from .agent_session import safe_effect_execution_id
from .base import EffectRuntimeContext


def reconcile_local_inference_capacity(
    *,
    context: EffectRuntimeContext,
    capacity_store: LocalInferenceCapacityStore,
    config: Any,
    backend_factory: Callable[[EffectRuntimeContext, Any], Any],
    config_for_session: Callable[[EffectRuntimeContext, str], Any],
) -> None:
    running_leases = [
        lease for lease in capacity_store.read_all().values() if lease.status == "running"
    ]
    if not running_leases:
        return

    summaries: list[Any] | None = None
    if any(
        lease.execution_backend != ExecutionBackendKind.CONTAINER_WORKER_VM.value
        for lease in running_leases
    ):
        list_vms = getattr(context.vm_inventory, "list_vms", None)
        if callable(list_vms):
            try:
                summaries = list(list_vms(include_inactive=True, vm_user="agent"))
            except Exception:
                summaries = None

    def is_active(lease: LocalInferenceCapacityLease) -> bool | None:
        if lease.execution_backend != ExecutionBackendKind.CONTAINER_WORKER_VM.value:
            if summaries is None:
                return None
            return local_inference_vm_lease_is_active(lease, summaries)

        session_id = str(lease.session_id or "").strip()
        execution_id = str(lease.execution_id or "").strip()
        if not execution_id and session_id:
            execution_id = safe_effect_execution_id("agent", session_id)
        if not execution_id:
            return None
        effective_config = config_for_session(context, session_id) if session_id else config
        backend = backend_factory(context, effective_config)
        return local_inference_execution_is_active(backend.inspect(execution_id))

    capacity_store.reconcile_running(is_active)
