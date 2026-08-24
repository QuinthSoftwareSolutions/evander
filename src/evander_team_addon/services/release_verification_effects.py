from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
import subprocess
from typing import Any, Protocol

from evander.effects.providers.agent_clone_repo import AgentCloneRepoEffectProvider
from evander.effects.providers.agent_create import AgentCreateEffectProvider
from evander.effects.providers.agent_destroy import AgentDestroyEffectProvider
from evander.effects.providers.agent_exec import AgentExecEffectProvider
from evander.effects.providers.base import EffectRuntimeContext
from evander.events.provider_models import (
    AgentCloneRepoEffectDefinition,
    AgentCreateEffectDefinition,
    AgentDestroyEffectDefinition,
    AgentExecEffectDefinition,
)
from evander.worker.models import WorkerResourceLimits

from ..models import ReleaseVerificationCommand, ReviewRepositorySnapshot
from .domain.common import _optional_text, _required_text
from .release_verification_runner import ReleaseVerificationTimeoutError


class _ExecutableEffectProvider(Protocol):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]: ...


class EffectReleaseVerificationExecutor:
    """Adapt the generic agent effects to the Team release-verification port."""

    def __init__(self, runtime_context: EffectRuntimeContext) -> None:
        self._runtime_context = runtime_context

    def create_verifier(
        self,
        *,
        run_id: str,
        session_id: str,
        release_package_id: str,
        candidate_commit_sha: str,
        directory_path: str,
        container_image: str | None,
        resource_limits: WorkerResourceLimits | None,
    ) -> None:
        provider = AgentCreateEffectProvider()
        provider.initialize(
            AgentCreateEffectDefinition(
                id=f"team-create-release-verifier-{run_id}",
                display_name="Create disposable release verifier",
                type="effect:agent:create",
                execution_backend="container_worker_vm",
                session_id=session_id,
                existing_agent_behavior="upsert",
                container_image=container_image,
                directory_path=directory_path,
                dotnet=True,
                nuget=True,
                ttl_hours=4,
                metadata={
                    "team_release_verification": True,
                    "release_package_id": release_package_id,
                    "candidate_commit_sha": candidate_commit_sha,
                    **(
                        {
                            "container_worker_resource_limits": resource_limits.model_dump(
                                mode="json",
                                exclude_none=True,
                            )
                        }
                        if resource_limits is not None
                        else {}
                    ),
                },
            ),
            self._runtime_context,
        )
        result = _execute_provider(provider)
        if not bool(result.get("success")):
            raise RuntimeError(
                _optional_text(result.get("message"))
                or "failed to create disposable release verifier"
            )

    def clone_candidate(
        self,
        *,
        run_id: str,
        session_id: str,
        managed_repo_id: str,
        release_branch: str,
    ) -> str:
        provider = AgentCloneRepoEffectProvider()
        provider.initialize(
            AgentCloneRepoEffectDefinition(
                id=f"team-clone-release-candidate-{run_id}",
                display_name="Clone release candidate",
                type="effect:agent:clone-repo",
                session_id=session_id,
                managed_repo_id=managed_repo_id,
                clone_branch=release_branch,
                git_access_mode="read-only",
            ),
            self._runtime_context,
        )
        result = _execute_provider(provider)
        if not bool(result.get("success")):
            raise RuntimeError(
                _optional_text(result.get("message")) or "failed to clone release candidate"
            )
        return _required_text(result.get("target_dir"), "target_dir")

    def pin_candidate(
        self,
        *,
        run_id: str,
        session_id: str,
        target_dir: str,
        candidate_commit_sha: str,
    ) -> None:
        checkout_commands = (
            (
                "pin",
                "Pin release candidate checkout",
                ["git", "checkout", "--detach", "--force", candidate_commit_sha],
            ),
            (
                "reset",
                "Reset release candidate checkout",
                ["git", "reset", "--hard", candidate_commit_sha],
            ),
            ("clean", "Clean release candidate checkout", ["git", "clean", "-ffdx"]),
        )
        for operation, display_name, command in checkout_commands:
            provider = AgentExecEffectProvider()
            provider.initialize(
                AgentExecEffectDefinition(
                    id=f"team-{operation}-release-candidate-{run_id}",
                    display_name=display_name,
                    type="effect:agent:exec",
                    session_id=session_id,
                    command=command,
                    working_directory=target_dir,
                    timeout_seconds=120,
                    check=True,
                ),
                self._runtime_context,
            )
            _execute_provider(provider)

    def execute_command(
        self,
        *,
        run_id: str,
        command_index: int,
        session_id: str,
        target_dir: str,
        command: ReleaseVerificationCommand,
        on_started: Callable[[str], None] | None = None,
    ) -> Mapping[str, Any]:
        provider = AgentExecEffectProvider()
        provider.initialize(
            AgentExecEffectDefinition(
                id=f"team-release-check-{run_id}-{command_index}",
                display_name=command.name,
                type="effect:agent:exec",
                session_id=session_id,
                command=list(command.argv),
                working_directory=_working_directory(target_dir, command.cwd),
                timeout_seconds=command.timeout_seconds,
                check=False,
            ),
            self._runtime_context,
        )
        provider.set_container_execution_started_callback(on_started)
        return _execute_provider(provider)

    def destroy_verifier(self, *, run_id: str, session_id: str) -> None:
        provider = AgentDestroyEffectProvider()
        provider.initialize(
            AgentDestroyEffectDefinition(
                id=f"team-destroy-release-verifier-{run_id}",
                display_name="Destroy disposable release verifier",
                type="effect:agent:destroy",
                session_id=session_id,
                execution_backend="container_worker_vm",
            ),
            self._runtime_context,
        )
        result = _execute_provider(provider)
        if not bool(result.get("success")):
            raise RuntimeError(
                _optional_text(result.get("message"))
                or "release verifier deletion reported failure"
            )


class EffectDeliverableAcceptanceVerificationExecutor:
    """Run pre-acceptance checks from an immutable Team review snapshot."""

    def __init__(self, runtime_context: EffectRuntimeContext) -> None:
        self._runtime_context = runtime_context

    def create_verifier(
        self,
        *,
        run_id: str,
        session_id: str,
        deliverable_id: str,
        candidate_commit_sha: str,
        directory_path: str,
        container_image: str | None,
        resource_limits: WorkerResourceLimits | None,
    ) -> None:
        provider = AgentCreateEffectProvider()
        provider.initialize(
            AgentCreateEffectDefinition(
                id=f"team-create-acceptance-verifier-{run_id}",
                display_name="Create disposable deliverable acceptance verifier",
                type="effect:agent:create",
                execution_backend="container_worker_vm",
                session_id=session_id,
                existing_agent_behavior="upsert",
                container_image=container_image,
                directory_path=directory_path,
                dotnet=True,
                nuget=True,
                ttl_hours=4,
                metadata={
                    "team_deliverable_acceptance_verification": True,
                    "deliverable_id": deliverable_id,
                    "candidate_commit_sha": candidate_commit_sha,
                    **(
                        {
                            "container_worker_resource_limits": resource_limits.model_dump(
                                mode="json",
                                exclude_none=True,
                            )
                        }
                        if resource_limits is not None
                        else {}
                    ),
                },
            ),
            self._runtime_context,
        )
        result = _execute_provider(provider)
        if not bool(result.get("success")):
            raise RuntimeError(
                _optional_text(result.get("message"))
                or "failed to create disposable acceptance verifier"
            )

    def clone_candidate(
        self,
        *,
        run_id: str,
        session_id: str,
        review_id: str,
        snapshot: ReviewRepositorySnapshot,
    ) -> str:
        managed_git = getattr(
            getattr(self._runtime_context, "admin_service", None),
            "managed_git_admin",
            None,
        )
        pin_snapshot = getattr(managed_git, "pin_team_review_snapshot", None)
        if not callable(pin_snapshot):
            raise RuntimeError("managed Git Team review snapshot pinning is unavailable")
        pinned = pin_snapshot(
            snapshot.managed_repo_id,
            snapshot.directory_path,
            review_id=review_id,
            commit_sha=snapshot.commit_sha,
            source_ref=snapshot.source_ref,
            pinned_ref=snapshot.pinned_ref,
        )
        if not isinstance(pinned, Mapping) or (
            _required_text(pinned.get("commit_sha"), "pinned commit_sha").casefold()
            != snapshot.commit_sha.casefold()
        ):
            raise RuntimeError("managed Git pinned the wrong acceptance candidate")
        provider = AgentCloneRepoEffectProvider()
        provider.initialize(
            AgentCloneRepoEffectDefinition(
                id=f"team-clone-acceptance-candidate-{run_id}",
                display_name="Clone immutable deliverable acceptance candidate",
                type="effect:agent:clone-repo",
                session_id=session_id,
                managed_repo_id=snapshot.managed_repo_id,
                git_access_mode="read-only",
                pinned_ref=snapshot.pinned_ref,
                pinned_commit_sha=snapshot.commit_sha,
            ),
            self._runtime_context,
        )
        result = _execute_provider(provider)
        if not bool(result.get("success")):
            raise RuntimeError(
                _optional_text(result.get("message"))
                or "failed to clone immutable acceptance candidate"
            )
        return _required_text(result.get("target_dir"), "target_dir")

    def pin_candidate(
        self,
        *,
        run_id: str,
        session_id: str,
        target_dir: str,
        candidate_commit_sha: str,
    ) -> None:
        for operation, display_name, command in (
            (
                "pin",
                "Pin deliverable acceptance candidate",
                ["git", "checkout", "--detach", "--force", candidate_commit_sha],
            ),
            (
                "reset",
                "Reset deliverable acceptance candidate",
                ["git", "reset", "--hard", candidate_commit_sha],
            ),
            ("clean", "Clean deliverable acceptance candidate", ["git", "clean", "-ffdx"]),
        ):
            provider = AgentExecEffectProvider()
            provider.initialize(
                AgentExecEffectDefinition(
                    id=f"team-{operation}-acceptance-candidate-{run_id}",
                    display_name=display_name,
                    type="effect:agent:exec",
                    session_id=session_id,
                    command=command,
                    working_directory=target_dir,
                    timeout_seconds=120,
                    check=True,
                ),
                self._runtime_context,
            )
            _execute_provider(provider)

    def execute_command(
        self,
        *,
        run_id: str,
        command_index: int,
        session_id: str,
        target_dir: str,
        command: ReleaseVerificationCommand,
        on_started: Callable[[str], None] | None = None,
    ) -> Mapping[str, Any]:
        provider = AgentExecEffectProvider()
        provider.initialize(
            AgentExecEffectDefinition(
                id=f"team-acceptance-check-{run_id}-{command_index}",
                display_name=command.name,
                type="effect:agent:exec",
                session_id=session_id,
                command=list(command.argv),
                working_directory=_working_directory(target_dir, command.cwd),
                timeout_seconds=command.timeout_seconds,
                check=False,
            ),
            self._runtime_context,
        )
        provider.set_container_execution_started_callback(on_started)
        return _execute_provider(provider)

    def destroy_verifier(self, *, run_id: str, session_id: str) -> None:
        provider = AgentDestroyEffectProvider()
        provider.initialize(
            AgentDestroyEffectDefinition(
                id=f"team-destroy-acceptance-verifier-{run_id}",
                display_name="Destroy disposable deliverable acceptance verifier",
                type="effect:agent:destroy",
                session_id=session_id,
                execution_backend="container_worker_vm",
            ),
            self._runtime_context,
        )
        result = _execute_provider(provider)
        if not bool(result.get("success")):
            raise RuntimeError(
                _optional_text(result.get("message"))
                or "acceptance verifier deletion reported failure"
            )


def _working_directory(repo_root: str, cwd: str) -> str:
    normalized = cwd.strip()
    if normalized in {"", "."}:
        return repo_root
    path = Path(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("release verification cwd must stay within the candidate checkout")
    return f"{repo_root.rstrip('/')}/{normalized.strip('/')}"


def _execute_provider(provider: _ExecutableEffectProvider) -> dict[str, Any]:
    try:
        return provider.execute()
    except (TimeoutError, subprocess.TimeoutExpired) as exc:
        raise ReleaseVerificationTimeoutError(str(exc) or "release verification timed out") from exc
