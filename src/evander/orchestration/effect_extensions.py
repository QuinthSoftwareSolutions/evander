from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class SpecialEventResult:
    consume_event: bool = False


class SpecialEventHandler(Protocol):
    def handle_event(
        self,
        *,
        worker: Any,
        queue_record: Any,
        graph: Any,
        phase: Literal["pre", "post"],
        source_node_exists: bool,
    ) -> SpecialEventResult | None: ...

    def force_complete(self, *, worker: Any, dispatch_id: str) -> bool: ...


class LifecycleReconciliationHook(Protocol):
    def reconcile(self, *, worker: Any, graph: Any) -> int: ...


class CompletionDispatchHook(Protocol):
    def dispatch(self, *, worker: Any) -> int: ...


class AgentRunStatusPort(Protocol):
    def list_vm_summaries(self) -> list[Any]: ...


class AgentPromptDispatchPort(Protocol):
    def dispatch_agent_prompt(
        self,
        *,
        target: str,
        prompt: str,
        model: str | None = None,
        vm_user: str = "agent",
        dispatch_id: str | None = None,
        resume_thread_id: str | None = None,
        resume_mode: str = "new_context",
        persist_thread_id: bool = True,
        mcp_gateway_url: str | None = None,
    ) -> Any: ...


class NoOpSpecialEventHandler:
    def handle_event(
        self,
        *,
        worker: Any,
        queue_record: Any,
        graph: Any,
        phase: Literal["pre", "post"],
        source_node_exists: bool,
    ) -> SpecialEventResult | None:
        del worker, queue_record, graph, phase, source_node_exists
        return None

    def force_complete(self, *, worker: Any, dispatch_id: str) -> bool:
        del worker, dispatch_id
        return False


class NoOpLifecycleReconciliationHook:
    def reconcile(self, *, worker: Any, graph: Any) -> int:
        del worker, graph
        return 0


class NoOpCompletionDispatchHook:
    def dispatch(self, *, worker: Any) -> int:
        del worker
        return 0


def resolve_agent_run_status_port(worker: Any) -> AgentRunStatusPort | None:
    worker_dict = getattr(worker, "__dict__", {})
    legacy_lister = worker_dict.get("_list_vm_summaries_for_await_agent_reconciliation")
    if callable(legacy_lister):
        return _CallableAgentRunStatusPort(legacy_lister)

    legacy_lister = worker_dict.get("_list_vm_summaries_for_reconciliation")
    if callable(legacy_lister):
        return _CallableAgentRunStatusPort(legacy_lister)

    port = getattr(worker, "agent_run_status_port", None)
    if port is not None:
        return port

    provider_factory = getattr(worker, "provider_factory", None)
    admin_service = getattr(provider_factory, "admin_service", None)
    if admin_service is not None:
        return _AdminServiceAgentRunStatusPort(admin_service)
    return None


def resolve_agent_prompt_dispatch_port(worker: Any) -> AgentPromptDispatchPort | None:
    port = getattr(worker, "agent_prompt_dispatch_port", None)
    if port is not None:
        return port

    provider_factory = getattr(worker, "provider_factory", None)
    admin_service = getattr(provider_factory, "admin_service", None)
    if admin_service is not None:
        return _AdminServiceAgentPromptDispatchPort(admin_service)
    return None


def list_vm_summaries_for_agent_reconciliation(worker: Any) -> list[Any]:
    port = resolve_agent_run_status_port(worker)
    if port is None:
        return []
    return list(port.list_vm_summaries())


def dispatch_agent_prompt(
    worker: Any,
    *,
    target: str,
    prompt: str,
    model: str | None = None,
    vm_user: str = "agent",
    dispatch_id: str | None = None,
    resume_thread_id: str | None = None,
    resume_mode: str = "new_context",
    persist_thread_id: bool = True,
    mcp_gateway_url: str | None = None,
) -> Any:
    port = resolve_agent_prompt_dispatch_port(worker)
    if port is None:
        return None
    return port.dispatch_agent_prompt(
        target=target,
        prompt=prompt,
        model=model,
        vm_user=vm_user,
        dispatch_id=dispatch_id,
        resume_thread_id=resume_thread_id,
        resume_mode=resume_mode,
        persist_thread_id=persist_thread_id,
        mcp_gateway_url=mcp_gateway_url,
    )


class _CallableAgentRunStatusPort:
    def __init__(self, lister: Any) -> None:
        self._lister = lister

    def list_vm_summaries(self) -> list[Any]:
        return list(self._lister())


class _AdminServiceAgentRunStatusPort:
    def __init__(self, admin_service: Any) -> None:
        self._admin_service = admin_service

    def list_vm_summaries(self) -> list[Any]:
        try:
            summaries = self._admin_service.list_vms(
                include_inactive=True,
                omit_state=False,
                vm_user="agent",
                directory_path=None,
                direct_only=False,
            )
        except TypeError:
            summaries = self._admin_service.list_vms(include_inactive=True, vm_user="agent")
        return list(summaries)


class _AdminServiceAgentPromptDispatchPort:
    def __init__(self, admin_service: Any) -> None:
        self._admin_service = admin_service

    def dispatch_agent_prompt(
        self,
        *,
        target: str,
        prompt: str,
        model: str | None = None,
        vm_user: str = "agent",
        dispatch_id: str | None = None,
        resume_thread_id: str | None = None,
        resume_mode: str = "new_context",
        persist_thread_id: bool = True,
        mcp_gateway_url: str | None = None,
    ) -> Any:
        return self._admin_service.resume_agent(
            target=target,
            prompt=prompt,
            model=model,
            vm_user=vm_user,
            dispatch_id=dispatch_id,
            resume_thread_id=resume_thread_id,
            resume_mode=resume_mode,
            persist_thread_id=persist_thread_id,
            mcp_gateway_url=mcp_gateway_url,
        )
