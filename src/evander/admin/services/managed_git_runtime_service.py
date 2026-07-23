from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import logging
import pwd
from typing import Any, Protocol

from evander.config import GatewayConfig
from evander.directories import DirectoryService
from evander.directory_config import (
    DirectoryConfigResolver,
    get_domain_base_config,
    validate_effective_domain_payload,
)


LOGGER = logging.getLogger(__name__)


class ManagedGitMaintenanceOperationsPort(Protocol):
    def preflight(self) -> dict[str, Any]: ...

    def reconcile_stale_worktrees(self) -> dict[str, Any]: ...

    def cleanup_active_worktrees(self) -> dict[str, int]: ...


class ManagedGitRepositoryOperationsPort(Protocol):
    def refresh_repository(self, managed_repo_id: str) -> dict[str, Any]: ...


class ManagedGitMaintenancePort(Protocol):
    def preflight(self) -> Mapping[str, Any]: ...

    def reconcile_stale_worktrees(self) -> Mapping[str, Any]: ...

    def cleanup_active_worktrees(self) -> Mapping[str, int]: ...


class ManagedGitRepositoryRefreshPort(Protocol):
    def refresh_repository(self, managed_repo_id: str) -> Mapping[str, Any]: ...


class ManagedGitRefreshAuthorizationPort(Protocol):
    def configured_git_uids(self) -> set[int]: ...


@dataclass(frozen=True, slots=True)
class ManagedGitMaintenanceService:
    """Narrow lifecycle view over the managed-Git domain service."""

    managed_git: ManagedGitMaintenanceOperationsPort

    def preflight(self) -> Mapping[str, Any]:
        return self.managed_git.preflight()

    def reconcile_stale_worktrees(self) -> Mapping[str, Any]:
        return self.managed_git.reconcile_stale_worktrees()

    def cleanup_active_worktrees(self) -> Mapping[str, int]:
        return self.managed_git.cleanup_active_worktrees()


@dataclass(frozen=True, slots=True)
class ManagedGitRepositoryRefreshService:
    """Narrow upstream-refresh view over the managed-Git domain service."""

    managed_git: ManagedGitRepositoryOperationsPort

    def refresh_repository(self, managed_repo_id: str) -> Mapping[str, Any]:
        return self.managed_git.refresh_repository(managed_repo_id)


def _resolve_user_uid(user: str) -> int:
    return pwd.getpwnam(user).pw_uid


@dataclass(frozen=True, slots=True)
class ManagedGitRefreshAuthorizationDependencies:
    config: GatewayConfig
    directory_service: DirectoryService
    directory_config_resolver: DirectoryConfigResolver
    resolve_user_uid: Callable[[str], int] = _resolve_user_uid


class ManagedGitRefreshAuthorizationService:
    """Resolves the host accounts allowed to use the local refresh broker."""

    def __init__(self, dependencies: ManagedGitRefreshAuthorizationDependencies) -> None:
        self._deps = dependencies

    def configured_git_uids(self) -> set[int]:
        directories = [
            self._deps.directory_service.show("root").directory,
            *self._deps.directory_service.list_descendants("root"),
        ]
        base_config = get_domain_base_config(self._deps.config, "issue_setup")
        users: set[str] = set()
        for directory in directories:
            try:
                resolution = self._deps.directory_config_resolver.resolve_domain(
                    directory.path,
                    domain="issue_setup",
                    base_config=base_config,
                    validator=lambda candidate: validate_effective_domain_payload(
                        "issue_setup", candidate
                    ),
                )
                git_user = str(resolution.effective_config.get("git_user") or "").strip()
            except Exception as exc:
                LOGGER.warning(
                    "Managed Git refresh authorization config failed directory=%s category=%s",
                    directory.path,
                    type(exc).__name__,
                )
                continue
            if git_user:
                users.add(git_user)

        allowed_uids: set[int] = set()
        for git_user in users:
            try:
                allowed_uids.add(self._deps.resolve_user_uid(git_user))
            except KeyError:
                LOGGER.warning(
                    "Managed Git refresh authorization user does not exist user=%s",
                    git_user,
                )
        return allowed_uids
