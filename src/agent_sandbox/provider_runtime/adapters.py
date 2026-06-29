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


class AdminRuntimeProviderAdapter:
    """Narrow provider-runtime adapter over a host runtime object."""

    def __init__(self, admin_runtime: object) -> None:
        self._admin_runtime = admin_runtime
        self.runtime = getattr(admin_runtime, "runtime", None)
        self.config = getattr(admin_runtime, "config", None)
        self.project_dir = getattr(admin_runtime, "project_dir", None)
        self.vm_agent_admin = getattr(admin_runtime, "vm_agent_admin", None)
        self.issue_git_admin = getattr(admin_runtime, "issue_git_admin", None)

    def _vm_agent_admin(self) -> object:
        return self.vm_agent_admin or self._admin_runtime

    def _issue_git_admin(self) -> object:
        return self.issue_git_admin or self._admin_runtime

    def _operations_admin(self) -> object:
        return getattr(self._admin_runtime, "operations_admin", self._admin_runtime)

    def _skill_admin(self) -> object:
        return getattr(self._admin_runtime, "skill_admin", self._admin_runtime)

    def create_vm(self, **kwargs: Any) -> Any:
        return self._vm_agent_admin().create_vm(**kwargs)

    def resolve_vm_defaults(self, directory_path: str) -> Any:
        return self._vm_agent_admin().resolve_vm_defaults(directory_path)

    def clone_repo_into_vm(self, **kwargs: Any) -> Any:
        return self._vm_agent_admin().clone_repo_into_vm(**kwargs)

    def bootstrap_python_in_vm(self, **kwargs: Any) -> Any:
        return self._vm_agent_admin().bootstrap_python_in_vm(**kwargs)

    def bootstrap_frontend_in_vm(self, **kwargs: Any) -> Any:
        return self._vm_agent_admin().bootstrap_frontend_in_vm(**kwargs)

    def resume_agent(self, **kwargs: Any) -> Any:
        return self._vm_agent_admin().resume_agent(**kwargs)

    def start_vm(self, target: str) -> Any:
        return self._vm_agent_admin().start_vm(target)

    def shutdown_vm(self, target: str, *, timeout_seconds: int = 60) -> Any:
        return self._vm_agent_admin().shutdown_vm(target, timeout_seconds=timeout_seconds)

    def list_vms(self, **kwargs: Any) -> list[Any]:
        return self._vm_agent_admin().list_vms(**kwargs)

    def vm_exec(self, **kwargs: Any) -> Mapping[str, Any]:
        return self._vm_agent_admin().vm_exec(**kwargs)

    def vm_copy_files(self, **kwargs: Any) -> Mapping[str, Any]:
        return self._vm_agent_admin().vm_copy_files(**kwargs)

    def vm_delete_files(self, **kwargs: Any) -> Mapping[str, Any]:
        return self._vm_agent_admin().vm_delete_files(**kwargs)

    def read_all_sessions(self) -> Mapping[str, Any]:
        runtime = self.runtime
        session_repository = getattr(runtime, "session_repository", None)
        if session_repository is not None:
            return {session.session_id: session for session in session_repository.list_sessions()}
        return {}

    def update_issue(self, issue_id: str, **kwargs: Any) -> Any:
        return self._issue_git_admin().update_issue(issue_id, **kwargs)

    def update_issue_metadata(self, **kwargs: Any) -> Any:
        return self._issue_git_admin().update_issue_metadata(**kwargs)

    def session_update_metadata(self, *, target: str, metadata: dict[str, Any]) -> Any:
        return self._issue_git_admin().session_update_metadata(target=target, metadata=metadata)

    def promote_agent_branch(self, **kwargs: Any) -> Any:
        return self._issue_git_admin().promote_agent_branch(**kwargs)

    def push_branch(self, **kwargs: Any) -> Any:
        return self._issue_git_admin().push_branch(**kwargs)

    def pr_create(self, **kwargs: Any) -> Any:
        return self._issue_git_admin().pr_create(**kwargs)

    def git_pr_note_query(self, **kwargs: Any) -> Any:
        return self._issue_git_admin().git_pr_note_query(**kwargs)

    def get_issue(self, **kwargs: Any) -> Any:
        return self._issue_git_admin().get_issue(**kwargs)

    def list_issues(self, **kwargs: Any) -> Any:
        return self._issue_git_admin().list_issues(**kwargs)

    def session_context_query(self, **kwargs: Any) -> Any:
        return self._issue_git_admin().session_context_query(**kwargs)

    def read_session_artifact_text(self, **kwargs: Any) -> Any:
        return self._vm_agent_admin().read_session_artifact_text(**kwargs)

    def sync_added_discord_session_cursors(self, session_ids: list[str] | set[str]) -> None:
        return self._vm_agent_admin().sync_added_discord_session_cursors(session_ids)

    def validate_managed_codex_skills(self, skill_names: list[str]) -> list[str]:
        return self._skill_admin().validate_managed_codex_skills(skill_names)

    def destroy_vm(self, target: str) -> Any:
        return self._vm_agent_admin().destroy_vm(target)

    def delete_admin_agent(self, agent_id: str, *, backend: str | None = None) -> Any:
        return self._vm_agent_admin().delete_admin_agent(agent_id, backend=backend)

    def read_assistant_settings(self) -> dict[str, object]:
        return self._operations_admin().read_assistant_settings()

    def _resolve_issue_setup(self, directory_path: str, *, root_dir: Any) -> Any:
        return self._vm_agent_admin()._resolve_issue_setup(directory_path, root_dir=root_dir)


class GateProviderAdapter:
    """Narrow gate-runtime adapter over a host runtime object."""

    def __init__(self, admin_runtime: object) -> None:
        self._admin_runtime = admin_runtime

    def list_vms(self, **kwargs: Any) -> list[Any]:
        vm_agent_admin = getattr(self._admin_runtime, "vm_agent_admin", self._admin_runtime)
        return vm_agent_admin.list_vms(**kwargs)


def build_provider_runtime_ports(
    *,
    provider_runtime: object | None = None,
    admin_service: object | None = None,
    admin_adapter: object | None = None,
    gate_adapter: object | None = None,
) -> ProviderRuntimePorts:
    if admin_adapter is None and admin_service is not None:
        admin_adapter = AdminRuntimeProviderAdapter(admin_service)
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
        session_artifact_reader=_pick(provider_runtime, "session_artifact_reader", admin_adapter),
        admin_service=_pick(provider_runtime, "admin_service", admin_adapter, admin_service),
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
