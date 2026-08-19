from __future__ import annotations

from collections.abc import Callable, Mapping
import time
from typing import Any, Protocol

from evander.worker.models import WorkerResourceLimits

from ..models import ReleaseVerificationCommand
from ..repository import TeamStateRepository
from .domain.common import _optional_text
from .release_gate_service import TeamReleaseGateService
from .release_verification_singleflight import (
    TEAM_RELEASE_VERIFICATION_SINGLE_FLIGHT,
    TeamReleaseVerificationSingleFlight,
)


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
        on_started: Callable[[str], None] | None = None,
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
        single_flight: TeamReleaseVerificationSingleFlight | None = None,
    ) -> None:
        self._repo = repo
        self._executor = executor
        self._release_gate_service = release_gate_service or TeamReleaseGateService(repo)
        self._single_flight = single_flight or TEAM_RELEASE_VERIFICATION_SINGLE_FLIGHT

    def run(
        self,
        *,
        release_package_id: str,
        verification_run_id: str,
        candidate_commit_sha: str,
    ) -> dict[str, Any]:
        return self._single_flight.run(
            state_db_path=self._repo.store.path,
            verification_run_id=verification_run_id,
            execute=lambda: self._run_once(
                release_package_id=release_package_id,
                verification_run_id=verification_run_id,
                candidate_commit_sha=candidate_commit_sha,
            ),
        )

    def _run_once(
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

        state = self._release_gate_service.mark_verification_started(
            release_package_id=release_package_id,
            verification_run_id=verification_run_id,
            candidate_commit_sha=candidate_commit_sha,
        )
        run = state.release_verification_runs[verification_run_id]
        command_results = [
            item.model_dump(mode="json", exclude_none=True) for item in run.command_results
        ]
        if len(command_results) != len(verification_commands):
            raise ValueError("release verification run does not match its frozen command manifest")
        persisted_outcome = _persisted_terminal_outcome(command_results)
        if persisted_outcome is not None:
            terminal_state, failure_kind, terminal_error = persisted_outcome
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
                existing_result = command_results[index]
                if existing_result.get("state") == "passed":
                    continue
                started_result = {
                    "name": command.name,
                    "state": "running",
                    **(
                        {"job_id": existing_result["job_id"]}
                        if existing_result.get("job_id")
                        else {}
                    ),
                }
                self._release_gate_service.record_verification_command_progress(
                    release_package_id=release_package_id,
                    verification_run_id=verification_run_id,
                    candidate_commit_sha=candidate_commit_sha,
                    command_index=index,
                    command_result=started_result,
                )
                command_results[index] = started_result

                def _record_started_job(
                    job_id: str,
                    *,
                    _index: int = index,
                    _command_name: str = command.name,
                ) -> None:
                    running_result = {
                        "name": _command_name,
                        "state": "running",
                        "job_id": job_id,
                    }
                    self._release_gate_service.record_verification_command_progress(
                        release_package_id=release_package_id,
                        verification_run_id=verification_run_id,
                        candidate_commit_sha=candidate_commit_sha,
                        command_index=_index,
                        command_result=running_result,
                    )
                    command_results[_index] = running_result

                started = time.monotonic()
                try:
                    command_output = self._executor.execute_command(
                        run_id=run.id,
                        command_index=index,
                        session_id=run.session_id,
                        target_dir=target_dir,
                        command=command,
                        on_started=_record_started_job,
                    )
                except ReleaseVerificationTimeoutError as exc:
                    duration = time.monotonic() - started
                    error = str(exc) or f"release verification command {command.name!r} timed out"
                    result = {
                        "name": command.name,
                        "state": "timed_out",
                        "failure_kind": "timeout",
                        **(
                            {"job_id": command_results[index]["job_id"]}
                            if command_results[index].get("job_id")
                            else {}
                        ),
                        "duration_seconds": duration,
                        "log_excerpt": _bounded_verification_log_excerpt(error),
                    }
                    self._release_gate_service.record_verification_command_progress(
                        release_package_id=release_package_id,
                        verification_run_id=verification_run_id,
                        candidate_commit_sha=candidate_commit_sha,
                        command_index=index,
                        command_result=result,
                    )
                    command_results[index] = result
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
                command_failure_kind = (
                    None
                    if success
                    else "timeout"
                    if command_timed_out
                    else "candidate"
                    if command_output.get("failure_kind") == "command"
                    else "infrastructure"
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
                result = {
                    "name": command.name,
                    "state": "passed"
                    if success
                    else "timed_out"
                    if command_timed_out
                    else "failed",
                    **(
                        {"failure_kind": command_failure_kind}
                        if command_failure_kind is not None
                        else {}
                    ),
                    "job_id": (
                        _optional_text(command_output.get("execution_id"))
                        or command_results[index].get("job_id")
                    ),
                    "exit_code": command_output.get("exit_code"),
                    "failure_cause": _optional_text(command_output.get("failure_cause")),
                    "duration_seconds": duration,
                    "log_excerpt": excerpt or None,
                }
                self._release_gate_service.record_verification_command_progress(
                    release_package_id=release_package_id,
                    verification_run_id=verification_run_id,
                    candidate_commit_sha=candidate_commit_sha,
                    command_index=index,
                    command_result=result,
                )
                command_results[index] = result
                if not success:
                    if command_timed_out:
                        terminal_state = "timed_out"
                        failure_kind = command_failure_kind
                        terminal_error = f"release verification command {command.name!r} timed out"
                    else:
                        terminal_state = "failed"
                        failure_kind = command_failure_kind
                        if command_failure_kind == "candidate":
                            terminal_error = f"release verification command {command.name!r} failed"
                        else:
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


def _persisted_terminal_outcome(
    command_results: list[dict[str, Any]],
) -> tuple[str, str | None, str | None] | None:
    """Return a terminal run outcome already durably recorded by a prior effect."""

    for command_result in command_results:
        command_state = str(command_result.get("state") or "")
        command_name = str(command_result.get("name") or "verification command")
        if command_state == "timed_out":
            return (
                "timed_out",
                "timeout",
                f"release verification command {command_name!r} timed out",
            )
        if command_state == "failed":
            persisted_failure_kind = _optional_text(command_result.get("failure_kind"))
            failure_kind = (
                persisted_failure_kind
                if persisted_failure_kind in {"candidate", "infrastructure"}
                else "infrastructure"
                if command_result.get("failure_cause")
                else "candidate"
            )
            return (
                "failed",
                failure_kind,
                (
                    f"release verification command {command_name!r} failed"
                    + (" in verifier infrastructure" if failure_kind == "infrastructure" else "")
                ),
            )
    if command_results and all(
        str(command_result.get("state") or "") == "passed" for command_result in command_results
    ):
        return "passed", None, None
    return None


def _bounded_verification_log_excerpt(log_text: str) -> str:
    """Retain both the first diagnostic and final summary of a bounded command log."""

    if len(log_text) <= _VERIFICATION_LOG_EXCERPT_LIMIT:
        return log_text
    retained = _VERIFICATION_LOG_EXCERPT_LIMIT - len(_VERIFICATION_LOG_OMISSION_MARKER)
    head_length = retained // 2
    tail_length = retained - head_length
    return log_text[:head_length] + _VERIFICATION_LOG_OMISSION_MARKER + log_text[-tail_length:]
