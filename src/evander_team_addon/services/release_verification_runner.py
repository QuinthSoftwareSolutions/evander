from __future__ import annotations

from collections.abc import Mapping
import time
from typing import Any, Protocol

from evander.worker.models import WorkerResourceLimits

from ..models import ReleaseVerificationCommand
from ..repository import TeamStateRepository
from .domain.common import _optional_text
from .release_gate_service import TeamReleaseGateService


_VERIFICATION_LOG_EXCERPT_LIMIT = 8000
_VERIFICATION_LOG_OMISSION_MARKER = "\n... [middle of verifier log omitted] ...\n"


class ReleaseVerificationTimeoutError(RuntimeError):
    """A release-verification infrastructure operation exceeded its deadline."""


class ReleaseVerificationExecutor(Protocol):
    """Infrastructure port used by the release-verification application runner."""

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
    ) -> None: ...

    def clone_candidate(
        self,
        *,
        run_id: str,
        session_id: str,
        managed_repo_id: str,
        release_branch: str,
    ) -> str: ...

    def pin_candidate(
        self,
        *,
        run_id: str,
        session_id: str,
        target_dir: str,
        candidate_commit_sha: str,
    ) -> None: ...

    def execute_command(
        self,
        *,
        run_id: str,
        command_index: int,
        session_id: str,
        target_dir: str,
        command: ReleaseVerificationCommand,
    ) -> Mapping[str, Any]: ...

    def destroy_verifier(self, *, run_id: str, session_id: str) -> None: ...


class TeamReleaseVerificationRunner:
    """Run a frozen release candidate verification and persist its gate result."""

    def __init__(
        self,
        repo: TeamStateRepository,
        executor: ReleaseVerificationExecutor,
        *,
        release_gate_service: TeamReleaseGateService | None = None,
    ) -> None:
        self._repo = repo
        self._executor = executor
        self._release_gate_service = release_gate_service or TeamReleaseGateService(repo)

    def run(
        self,
        *,
        release_package_id: str,
        verification_run_id: str,
        candidate_commit_sha: str,
    ) -> dict[str, Any]:
        state = self._repo.read()
        release = state.release_packages.get(release_package_id)
        run = state.release_verification_runs.get(verification_run_id)
        if release is None or run is None:
            raise ValueError("release package or verification run does not exist")
        if (
            release.candidate_commit_sha != candidate_commit_sha
            or run.candidate_commit_sha != candidate_commit_sha
        ):
            raise ValueError("release verification request does not match the pinned candidate")
        if run.state in {"passed", "failed", "timed_out"}:
            return {
                "success": True,
                "release_package_id": release_package_id,
                "verification_run_id": verification_run_id,
                "verification_state": run.state,
                "skipped": True,
            }

        project = state.projects.get(release.project_id)
        policy = project.release_policy if project is not None else None
        git = project.git if project is not None else None
        manifest = release.manifest
        if project is None or git is None or manifest is None:
            raise ValueError(
                "release project, Git configuration, or frozen candidate manifest is missing"
            )
        verification_commands = manifest.verification_commands
        if not verification_commands:
            raise ValueError("release verification run has no frozen commands")

        command_results: list[dict[str, Any]] = []
        terminal_state = "passed"
        failure_kind: str | None = None
        terminal_error: str | None = None
        created = False
        try:
            self._executor.create_verifier(
                run_id=run.id,
                session_id=run.session_id,
                release_package_id=release.id,
                candidate_commit_sha=candidate_commit_sha,
                directory_path=git.directory_path,
                container_image=policy.container_image if policy is not None else None,
                resource_limits=policy.resource_limits if policy is not None else None,
            )
            created = True
            target_dir = self._executor.clone_candidate(
                run_id=run.id,
                session_id=run.session_id,
                managed_repo_id=git.managed_repo_id,
                release_branch=release.release_branch,
            )
            self._executor.pin_candidate(
                run_id=run.id,
                session_id=run.session_id,
                target_dir=target_dir,
                candidate_commit_sha=candidate_commit_sha,
            )

            for index, command in enumerate(verification_commands):
                started = time.monotonic()
                try:
                    command_output = self._executor.execute_command(
                        run_id=run.id,
                        command_index=index,
                        session_id=run.session_id,
                        target_dir=target_dir,
                        command=command,
                    )
                except ReleaseVerificationTimeoutError as exc:
                    duration = time.monotonic() - started
                    error = str(exc) or f"release verification command {command.name!r} timed out"
                    command_results.append(
                        {
                            "name": command.name,
                            "state": "timed_out",
                            "duration_seconds": duration,
                            "log_excerpt": _bounded_verification_log_excerpt(error),
                        }
                    )
                    terminal_state = "timed_out"
                    failure_kind = "timeout"
                    terminal_error = error
                    break
                duration = time.monotonic() - started
                success = bool(command_output.get("success"))
                command_status = str(command_output.get("status") or "").strip().casefold()
                command_timed_out = not success and (
                    command_output.get("failure_kind") == "timeout"
                    or command_status in {"unknown", "preparing", "starting", "running"}
                )
                command_log = "\n".join(
                    part
                    for part in (
                        _optional_text(command_output.get("stdout")),
                        _optional_text(command_output.get("stderr")),
                    )
                    if part
                )
                excerpt = _bounded_verification_log_excerpt(command_log)
                command_results.append(
                    {
                        "name": command.name,
                        "state": (
                            "passed" if success else "timed_out" if command_timed_out else "failed"
                        ),
                        "job_id": _optional_text(command_output.get("execution_id")),
                        "exit_code": command_output.get("exit_code"),
                        "failure_cause": _optional_text(
                            command_output.get("failure_cause")
                        ),
                        "duration_seconds": duration,
                        "log_excerpt": excerpt or None,
                    }
                )
                if not success:
                    if command_timed_out:
                        terminal_state = "timed_out"
                        failure_kind = "timeout"
                        terminal_error = f"release verification command {command.name!r} timed out"
                    else:
                        terminal_state = "failed"
                        if command_output.get("failure_kind") == "command":
                            failure_kind = "candidate"
                            terminal_error = (
                                f"release verification command {command.name!r} failed"
                            )
                        else:
                            failure_kind = "infrastructure"
                            terminal_error = (
                                f"release verification command {command.name!r} "
                                "failed in verifier infrastructure"
                            )
                    break
        except ReleaseVerificationTimeoutError as exc:
            terminal_state = "timed_out"
            failure_kind = "timeout"
            terminal_error = str(exc) or "release verification timed out"
        except Exception as exc:
            terminal_state = "failed"
            failure_kind = "infrastructure"
            terminal_error = str(exc) or type(exc).__name__
        finally:
            if created:
                try:
                    self._executor.destroy_verifier(
                        run_id=run.id,
                        session_id=run.session_id,
                    )
                except Exception as exc:
                    if terminal_state == "passed":
                        terminal_state = "failed"
                        failure_kind = "infrastructure"
                        terminal_error = f"failed to destroy release verifier: {exc}"

        _state, result = self._release_gate_service.record_verification_result(
            release_package_id=release_package_id,
            verification_run_id=verification_run_id,
            candidate_commit_sha=candidate_commit_sha,
            verification_state=terminal_state,
            command_results=command_results,
            failure_kind=failure_kind,
            error=terminal_error,
        )
        return result


def _bounded_verification_log_excerpt(log_text: str) -> str:
    """Retain both the first diagnostic and final summary of a bounded command log."""

    if len(log_text) <= _VERIFICATION_LOG_EXCERPT_LIMIT:
        return log_text
    retained = _VERIFICATION_LOG_EXCERPT_LIMIT - len(_VERIFICATION_LOG_OMISSION_MARKER)
    head_length = retained // 2
    tail_length = retained - head_length
    return (
        log_text[:head_length]
        + _VERIFICATION_LOG_OMISSION_MARKER
        + log_text[-tail_length:]
    )
