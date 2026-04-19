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


class AgentResumer(Protocol):
    def resume_agent(self, **kwargs: Any) -> Any: ...


class VmPowerController(Protocol):
    def start_vm(self, target: str) -> Any: ...

    def shutdown_vm(self, target: str, *, timeout_seconds: int = 60) -> Any: ...


class VmInventory(Protocol):
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
class ProviderRuntimeBase:
    provider_config: Mapping[str, Any]
    env: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    secret_resolver: Callable[[str], str | None] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

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

    @property
    def resolved_config(self) -> Any:
        return SimpleNamespace(**dict(self.provider_config))
