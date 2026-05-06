from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Protocol


class SecretResolver(Protocol):
    def resolve_secret(self, ref: str) -> str | None: ...


class VmCreator(Protocol):
    def create_vm(self, **kwargs: Any) -> Any: ...


class VmRepoCloner(Protocol):
    def clone_repo_into_vm(self, **kwargs: Any) -> Any: ...


class VmPythonBootstrapper(Protocol):
    def bootstrap_python_in_vm(self, **kwargs: Any) -> Any: ...


class VmFrontendBootstrapper(Protocol):
    def bootstrap_frontend_in_vm(self, **kwargs: Any) -> Any: ...


class AgentResumer(Protocol):
    def resume_agent(self, **kwargs: Any) -> Any: ...


class VmPowerController(Protocol):
    def start_vm(self, target: str) -> Any: ...

    def shutdown_vm(self, target: str, *, timeout_seconds: int = 60) -> Any: ...


class VmInventory(Protocol):
    def list_vms(self, **kwargs: Any) -> list[Any]: ...


class GateVmInventory(Protocol):
    def list_vms(self, **kwargs: Any) -> list[Any]: ...


class VmExecutor(Protocol):
    def vm_exec(self, **kwargs: Any) -> Mapping[str, Any]: ...


class VmFileCopier(Protocol):
    def vm_copy_files(self, **kwargs: Any) -> Mapping[str, Any]: ...


class VmFileDeleter(Protocol):
    def vm_delete_files(self, **kwargs: Any) -> Mapping[str, Any]: ...


class SessionLookup(Protocol):
    def read_all_sessions(self) -> Mapping[str, Any]: ...


class IssueUpdater(Protocol):
    def update_issue(self, issue_id: str, **kwargs: Any) -> Any: ...


class SessionMetadataUpdater(Protocol):
    def session_update_metadata(self, *, target: str, metadata: dict[str, Any]) -> Any: ...


class GitBranchPromoter(Protocol):
    def promote_agent_branch(self, **kwargs: Any) -> Any: ...


class PullRequestCreator(Protocol):
    def pr_create(self, **kwargs: Any) -> Any: ...


class GitPrNoteReader(Protocol):
    def git_pr_note_query(self, **kwargs: Any) -> Any: ...


class IssueReader(Protocol):
    def get_issue(self, **kwargs: Any) -> Any: ...


class SessionContextReader(Protocol):
    def session_context_query(self, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class ProviderRuntimePorts:
    vm_creator: VmCreator | None = None
    vm_repo_cloner: VmRepoCloner | None = None
    vm_python_bootstrapper: VmPythonBootstrapper | None = None
    vm_frontend_bootstrapper: VmFrontendBootstrapper | None = None
    agent_resumer: AgentResumer | None = None
    vm_power_controller: VmPowerController | None = None
    vm_inventory: VmInventory | None = None
    gate_vm_inventory: GateVmInventory | None = None
    vm_executor: VmExecutor | None = None
    vm_file_copier: VmFileCopier | None = None
    vm_file_deleter: VmFileDeleter | None = None
    session_lookup: SessionLookup | None = None
    issue_updater: IssueUpdater | None = None
    session_metadata_updater: SessionMetadataUpdater | None = None
    git_branch_promoter: GitBranchPromoter | None = None
    pull_request_creator: PullRequestCreator | None = None
    git_pr_note_reader: GitPrNoteReader | None = None
    issue_reader: IssueReader | None = None
    session_context_reader: SessionContextReader | None = None
    admin_service: Any | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderRuntimeBase:
    provider_config: Mapping[str, Any] = field(default_factory=dict)
    provider_config_resolver: Any | None = None
    resolved_config: Any | None = None
    env: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    secret_resolver: Callable[[str], str | None] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        provider_config = dict(self.provider_config)
        if not provider_config and self.resolved_config is not None:
            provider_config = dict(_config_to_mapping(self.resolved_config))
        object.__setattr__(self, "provider_config", provider_config)
        object.__setattr__(self, "resolved_config", _config_to_namespace(provider_config))

    def resolve_secret(self, ref: str) -> str | None:
        normalized_ref = ref.strip()
        if not normalized_ref:
            return None
        if self.secret_resolver is not None:
            return self.secret_resolver(normalized_ref)
        return self.env.get(normalized_ref)

    def config_path(self, key: str) -> Path:
        value = self.provider_config.get(key)
        if isinstance(value, Path):
            return value
        if value is None:
            raise KeyError(f"Missing provider config path: {key}")
        return Path(str(value))


def _config_to_mapping(config: Any) -> Mapping[str, Any]:
    if isinstance(config, Mapping):
        return dict(config)
    model_dump = getattr(config, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    if hasattr(config, "__dataclass_fields__"):
        return {name: getattr(config, name) for name in config.__dataclass_fields__}
    if hasattr(config, "__dict__"):
        return dict(vars(config))
    raise TypeError(f"resolved_config must be mapping-like, got {type(config).__name__}")


def _config_to_namespace(config: Mapping[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        **{key: _normalize_config_value(key, value) for key, value in config.items()}
    )


def _normalize_config_value(key: str, value: Any) -> Any:
    if isinstance(value, str) and (key.endswith("_path") or key.endswith("_dir")):
        return Path(value)
    return value
