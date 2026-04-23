from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_sandbox.provider_config import (
    EffectProviderConfig,
    GateProviderConfig,
    QueryProviderConfig,
    TriggerProviderConfig,
)
from agent_sandbox.provider_runtime.contracts import ProviderRuntimePorts


class AdminServiceProviderAdapter:
    """Narrow provider-runtime adapter over a host runtime object."""

    def __init__(self, admin_service: object) -> None:
        self._admin_service = admin_service

    def create_vm(self, **kwargs: Any) -> Any:
        return self._admin_service.create_vm(**kwargs)

    def clone_repo_into_vm(self, **kwargs: Any) -> Any:
        return self._admin_service.clone_repo_into_vm(**kwargs)

    def bootstrap_python_in_vm(self, **kwargs: Any) -> Any:
        return self._admin_service.bootstrap_python_in_vm(**kwargs)

    def bootstrap_frontend_in_vm(self, **kwargs: Any) -> Any:
        return self._admin_service.bootstrap_frontend_in_vm(**kwargs)

    def resume_agent(self, **kwargs: Any) -> Any:
        return self._admin_service.resume_agent(**kwargs)

    def start_vm(self, target: str) -> Any:
        return self._admin_service.start_vm(target)

    def shutdown_vm(self, target: str, *, timeout_seconds: int = 60) -> Any:
        return self._admin_service.shutdown_vm(target, timeout_seconds=timeout_seconds)

    def list_vms(self, **kwargs: Any) -> list[Any]:
        return self._admin_service.list_vms(**kwargs)

    def vm_exec(self, **kwargs: Any) -> Mapping[str, Any]:
        return self._admin_service.vm_exec(**kwargs)

    def vm_copy_files(self, **kwargs: Any) -> Mapping[str, Any]:
        return self._admin_service.vm_copy_files(**kwargs)

    def vm_delete_files(self, **kwargs: Any) -> Mapping[str, Any]:
        return self._admin_service.vm_delete_files(**kwargs)

    def read_all_sessions(self) -> Mapping[str, Any]:
        runtime = getattr(self._admin_service, "runtime", None)
        session_repository = getattr(runtime, "session_repository", None)
        if session_repository is not None:
            return {
                session.session_id: session
                for session in session_repository.list_sessions()
            }
        sessions = getattr(runtime, "sessions", None)
        read_all = getattr(sessions, "read_all", None)
        if callable(read_all):
            return read_all()
        return {}

    def update_issue(self, issue_id: str, **kwargs: Any) -> Any:
        return self._admin_service.update_issue(issue_id, **kwargs)

    def session_update_metadata(self, *, target: str, metadata: dict[str, Any]) -> Any:
        return self._admin_service.session_update_metadata(target=target, metadata=metadata)

    def promote_agent_branch(self, **kwargs: Any) -> Any:
        return self._admin_service.promote_agent_branch(**kwargs)

    def pr_create(self, **kwargs: Any) -> Any:
        return self._admin_service.pr_create(**kwargs)

    def git_pr_note_query(self, **kwargs: Any) -> Any:
        return self._admin_service.git_pr_note_query(**kwargs)

    def get_issue(self, **kwargs: Any) -> Any:
        return self._admin_service.get_issue(**kwargs)

    def session_context_query(self, **kwargs: Any) -> Any:
        return self._admin_service.session_context_query(**kwargs)


class GateProviderAdapter:
    """Narrow gate-runtime adapter over a host runtime object."""

    def __init__(self, admin_service: object) -> None:
        self._admin_service = admin_service

    def list_vms(self, **kwargs: Any) -> list[Any]:
        return self._admin_service.list_vms(**kwargs)


def build_provider_runtime_ports(
    *,
    provider_runtime: object | None = None,
    admin_service: object | None = None,
    admin_adapter: object | None = None,
    gate_adapter: object | None = None,
) -> ProviderRuntimePorts:
    if admin_adapter is None and admin_service is not None:
        admin_adapter = AdminServiceProviderAdapter(admin_service)
    if gate_adapter is None and admin_service is not None:
        gate_adapter = GateProviderAdapter(admin_service)
    provider_metadata = _extract_mapping_attr(provider_runtime, "metadata")
    return ProviderRuntimePorts(
        vm_creator=_pick(provider_runtime, "vm_creator", admin_adapter),
        vm_repo_cloner=_pick(provider_runtime, "vm_repo_cloner", admin_adapter),
        vm_python_bootstrapper=_pick(provider_runtime, "vm_python_bootstrapper", admin_adapter),
        vm_frontend_bootstrapper=_pick(provider_runtime, "vm_frontend_bootstrapper", admin_adapter),
        agent_resumer=_pick(provider_runtime, "agent_resumer", admin_adapter),
        vm_power_controller=_pick(provider_runtime, "vm_power_controller", admin_adapter),
        vm_inventory=_pick(provider_runtime, "vm_inventory", admin_adapter),
        gate_vm_inventory=_pick(provider_runtime, "gate_vm_inventory", gate_adapter, admin_adapter),
        vm_executor=_pick(provider_runtime, "vm_executor", admin_adapter),
        vm_file_copier=_pick(provider_runtime, "vm_file_copier", admin_adapter),
        vm_file_deleter=_pick(provider_runtime, "vm_file_deleter", admin_adapter),
        session_lookup=_pick(provider_runtime, "session_lookup", admin_adapter),
        issue_updater=_pick(provider_runtime, "issue_updater", admin_adapter),
        session_metadata_updater=_pick(provider_runtime, "session_metadata_updater", admin_adapter),
        git_branch_promoter=_pick(provider_runtime, "git_branch_promoter", admin_adapter),
        pull_request_creator=_pick(provider_runtime, "pull_request_creator", admin_adapter),
        git_pr_note_reader=_pick(provider_runtime, "git_pr_note_reader", admin_adapter),
        issue_reader=_pick(provider_runtime, "issue_reader", admin_adapter),
        session_context_reader=_pick(provider_runtime, "session_context_reader", admin_adapter),
        admin_service=_pick(provider_runtime, "admin_service", admin_service, admin_adapter),
        metadata=provider_metadata,
    )


def trigger_provider_config_blob(config: TriggerProviderConfig) -> dict[str, Any]:
    return config.model_dump(mode="json")


def effect_provider_config_blob(config: EffectProviderConfig) -> dict[str, Any]:
    return config.model_dump(mode="json")


def query_provider_config_blob(config: QueryProviderConfig) -> dict[str, Any]:
    return config.model_dump(mode="json")


def gate_provider_config_blob(config: GateProviderConfig) -> dict[str, Any]:
    return config.model_dump(mode="json")


def _pick(source: object | None, attr_name: str, *fallbacks: object | None) -> object | None:
    value = getattr(source, attr_name, None) if source is not None else None
    if value is not None:
        return value
    for fallback in fallbacks:
        if fallback is None:
            continue
        return fallback
    return None


def _extract_mapping_attr(source: object | None, attr_name: str) -> dict[str, Any]:
    value = getattr(source, attr_name, None) if source is not None else None
    if isinstance(value, Mapping):
        return dict(value)
    return {}
