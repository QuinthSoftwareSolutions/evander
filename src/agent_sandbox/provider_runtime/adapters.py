from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent_sandbox.admin_service import AdminService
from agent_sandbox.config import GatewayConfig


class AdminServiceProviderAdapter:
    """Narrow provider-runtime adapter over AdminService."""

    def __init__(self, admin_service: AdminService) -> None:
        self._admin_service = admin_service

    def create_vm(self, **kwargs: Any) -> Any:
        return self._admin_service.create_vm(**kwargs)

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
        return self._admin_service.runtime.sessions.read_all()

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
    """Narrow gate-runtime adapter over AdminService."""

    def __init__(self, admin_service: AdminService) -> None:
        self._admin_service = admin_service

    def list_vms(self, **kwargs: Any) -> list[Any]:
        return self._admin_service.list_vms(**kwargs)


def trigger_provider_config_blob(config: GatewayConfig) -> dict[str, Any]:
    return {
        "runtime_dir": config.runtime_dir,
        "session_store_path": config.session_store_path,
        "pr_map_path": config.pr_map_path,
        "github_token_env": config.github_token_env,
        "azure_devops_pr_pat_env": config.azure_devops_pr_pat_env,
        "azure_devops_organization_url": config.azure_devops_organization_url,
        "azure_devops_project": config.azure_devops_project,
        "email_provider_timeout_seconds": config.email_provider_timeout_seconds,
        "teams": config.teams,
    }


def effect_provider_config_blob(config: GatewayConfig) -> dict[str, Any]:
    return {"runtime_dir": config.runtime_dir}


def query_provider_config_blob(config: GatewayConfig) -> dict[str, Any]:
    return {"runtime_dir": config.runtime_dir}


def gate_provider_config_blob(config: GatewayConfig) -> dict[str, Any]:
    return {"runtime_dir": config.runtime_dir}
