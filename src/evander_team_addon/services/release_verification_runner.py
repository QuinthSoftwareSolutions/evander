from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
from pathlib import PurePosixPath
import time
from typing import Any, Protocol

from evander.worker.models import WorkerResourceLimits

from ..models import (
    ReleasePackage,
    ReleaseVerificationCommand,
    ReleaseVerificationCommandResult,
    ReleaseVerificationRun,
    TeamState,
    utc_now,
)
from ..repository import TeamStateRepository
from .domain.common import _optional_text
from .release_gate_service import TeamReleaseGateService
from .verification_evidence import read_verification_evidence
from .verification_logs import (
    bound_log_text,
    redact_verifier_text,
    retain_verification_log,
    prune_verification_logs,
)
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

    def read_artifact(self, *, session_id: str, relative_path: str, max_bytes: int) -> str: ...


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
        _validate_verification_request_identity(
            self._repo.read(),
            release_package_id=release_package_id,
            verification_run_id=verification_run_id,
            candidate_commit_sha=candidate_commit_sha,
        )
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
        release, run = _validate_verification_request_identity(
            state,
            release_package_id=release_package_id,
            verification_run_id=verification_run_id,
            candidate_commit_sha=candidate_commit_sha,
        )
        if run.state in {"passed", "failed", "timed_out", "cancelled"}:
            return {
                "success": True,
                "release_package_id": release_package_id,
                "verification_run_id": verification_run_id,
                "verification_state": run.state,
                "skipped": True,
            }

        project = state.projects.get(release.project_id)
        git = project.git if project is not None else None
        manifest = release.manifest
        if project is None or git is None or manifest is None:
            raise ValueError(
                "release project, Git configuration, or frozen candidate manifest is missing"
            )
        verification_commands = manifest.verification_commands
        if not verification_commands:
            raise ValueError("release verification run has no frozen commands")
        if not run.verifier_settings_snapshotted:
            state = self._repo.update(
                lambda current: _snapshot_legacy_verifier_settings(
                    current,
                    release_package_id=release_package_id,
                    verification_run_id=verification_run_id,
                    candidate_commit_sha=candidate_commit_sha,
                )
            )
            release, run = _validate_verification_request_identity(
                state,
                release_package_id=release_package_id,
                verification_run_id=verification_run_id,
                candidate_commit_sha=candidate_commit_sha,
            )
            project = state.projects.get(release.project_id)
            git = project.git if project is not None else None
            manifest = release.manifest
            if project is None or git is None or manifest is None:
                raise ValueError(
                    "release project, Git configuration, or frozen candidate manifest is missing"
                )
            verification_commands = manifest.verification_commands

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
            # The process may have stopped between final command persistence and
            # session cleanup. Reuse evidence, but still reconcile the session.
            try:
                self._executor.destroy_verifier(
                    run_id=f"{run.id}-g{run.execution_generation}", session_id=run.session_id
                )
            except Exception as exc:
                if terminal_state == "passed":
                    terminal_state, failure_kind = "failed", "infrastructure"
                    terminal_error = redact_verifier_text(
                        f"failed to destroy release verifier: {exc}"
                    )
            _state, result = self._release_gate_service.record_verification_result(
                release_package_id=release_package_id,
                verification_run_id=verification_run_id,
                candidate_commit_sha=candidate_commit_sha,
                verification_state=terminal_state,
                command_results=command_results,
                failure_kind=failure_kind,
                error=terminal_error,
                execution_generation=run.execution_generation,
            )
            return result
        terminal_state = "passed"
        failure_kind: str | None = None
        terminal_error: str | None = None
        created = False
        try:
            prune_verification_logs(self._repo.runtime_dir)
            # A passed command is only reusable with its original workspace. The
            # executor always repins/cleans, so an interrupted run restarts fully.
            interrupted = run.execution_generation > 0 or any(
                item.get("state") != "pending" for item in command_results
            )
            if interrupted:
                self._executor.destroy_verifier(
                    run_id=f"{run.id}-g{run.execution_generation}", session_id=run.session_id
                )
            if run.execution_generation >= manifest.verification_max_generations:
                raise RuntimeError("verifier workspace recovery limit exhausted")
            state = self._repo.update(
                lambda current: _begin_execution_generation(
                    current,
                    verification_run_id=run.id,
                    expected_generation=run.execution_generation,
                    interrupted=interrupted,
                )
            )
            run = state.release_verification_runs[run.id]
            execution_id = f"{run.id}-g{run.execution_generation}"
            command_results = [
                item.model_dump(mode="json", exclude_none=True) for item in run.command_results
            ]
            created = True  # Also reconcile a partially successful create.
            self._executor.create_verifier(
                run_id=execution_id,
                session_id=run.session_id,
                release_package_id=release.id,
                candidate_commit_sha=candidate_commit_sha,
                directory_path=git.directory_path,
                container_image=run.container_image,
                resource_limits=run.resource_limits,
            )
            target_dir = self._executor.clone_candidate(
                run_id=execution_id,
                session_id=run.session_id,
                managed_repo_id=git.managed_repo_id,
                release_branch=release.release_branch,
            )
            self._executor.pin_candidate(
                run_id=execution_id,
                session_id=run.session_id,
                target_dir=target_dir,
                candidate_commit_sha=candidate_commit_sha,
            )

            for index, command in enumerate(verification_commands):
                if self._repo.read().release_verification_runs[run.id].state == "cancelled":
                    break
                existing_result = command_results[index]
                if existing_result.get("state") == "passed":
                    continue
                started_result = {
                    "name": command.name,
                    "execution_generation": run.execution_generation,
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
                        "execution_generation": run.execution_generation,
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
                        run_id=execution_id,
                        command_index=index,
                        session_id=run.session_id,
                        target_dir=target_dir,
                        command=_command_with_context(command, release),
                        on_started=_record_started_job,
                    )
                except ReleaseVerificationTimeoutError as exc:
                    duration = time.monotonic() - started
                    error = redact_verifier_text(
                        str(exc) or f"release verification command {command.name!r} timed out"
                    )
                    job_id = command_results[index].get("job_id") or f"{execution_id}-{index}"
                    result = {
                        "name": command.name,
                        "execution_generation": run.execution_generation,
                        "state": "timed_out",
                        "failure_kind": "timeout",
                        "execution_outcome": "timeout",
                        "policy_outcome": "infrastructure",
                        "job_id": job_id,
                        "log_artifact": retain_verification_log(
                            self._repo.runtime_dir,
                            job_id=job_id,
                            text=error,
                            retention_days=manifest.verification_log_retention_days,
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
                execution_outcome = (
                    "success"
                    if success
                    else "timeout"
                    if command_timed_out
                    else "command_failure"
                    if command_output.get("failure_kind") == "command"
                    else "infrastructure"
                )
                evidence = None
                policy_outcome = (
                    "passed"
                    if success
                    else "infrastructure"
                    if execution_outcome in {"infrastructure", "timeout"}
                    else "failed"
                )
                # Worker infrastructure/timeout evidence always takes precedence.
                if command.result_protocol == "json-v1" and execution_outcome in {
                    "success",
                    "command_failure",
                }:
                    try:
                        report = read_verification_evidence(
                            str(command_output.get("stdout") or ""),
                            command=command,
                            candidate_commit_sha=candidate_commit_sha,
                            base_commit_sha=manifest.base_commit_sha,
                        )
                        artifact_log = self._read_evidence_artifacts(
                            report.artifact_refs, session_id=run.session_id
                        )
                        if artifact_log:
                            # Reserve room for complete evidence attachments; a
                            # long build log must not truncate the comparison.
                            command_log = (
                                bound_log_text(command_log, limit=4 * 1024 * 1024) + artifact_log
                            )
                        evidence = report.model_dump(mode="json")
                        evidence["explanation"] = redact_verifier_text(evidence["explanation"])
                        evidence["artifact_refs"] = [
                            redact_verifier_text(ref) for ref in evidence["artifact_refs"]
                        ]
                        policy_outcome = report.outcome
                        success = execution_outcome == "success" and report.outcome in {
                            "passed",
                            "baseline_exception",
                        }
                        if not success and report.outcome in {"passed", "baseline_exception"}:
                            policy_outcome = "failed"
                        command_failure_kind = (
                            None
                            if success
                            else (
                                "infrastructure"
                                if report.outcome == "infrastructure"
                                else "candidate"
                            )
                        )
                    except (ValueError, TypeError) as exc:
                        success = False
                        policy_outcome = "infrastructure"
                        command_failure_kind = "infrastructure"
                        command_log += f"\nInvalid verifier evidence: {exc}"
                if (
                    not success
                    and command_failure_kind == "candidate"
                    and command.command_failure_action == "block"
                ):
                    command_failure_kind = "command"
                command_log = redact_verifier_text(command_log)
                job_id = (
                    _optional_text(command_output.get("execution_id"))
                    or command_results[index].get("job_id")
                    or f"{execution_id}-{index}"
                )
                log_artifact = retain_verification_log(
                    self._repo.runtime_dir,
                    job_id=job_id,
                    text=command_log,
                    retention_days=manifest.verification_log_retention_days,
                )
                excerpt = _bounded_verification_log_excerpt(command_log)
                result = {
                    "name": command.name,
                    "execution_generation": run.execution_generation,
                    "execution_outcome": execution_outcome,
                    "policy_outcome": policy_outcome,
                    "evidence": evidence,
                    "log_artifact": log_artifact,
                    "toolchain_mounts": command_output.get("toolchain_mounts", []),
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
                    "job_id": job_id,
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
                        if command_failure_kind in {"candidate", "command"}:
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
                        run_id=f"{run.id}-g{run.execution_generation}",
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
            error=redact_verifier_text(terminal_error) if terminal_error else None,
            execution_generation=run.execution_generation,
        )
        return result

    def _read_evidence_artifacts(self, references: list[str], *, session_id: str) -> str:
        """Retain bounded session-local text evidence before destroying its owner.

        Other references are opaque identifiers for evaluator-owned durable
        storage. The control plane never fetches arbitrary evaluator URLs.
        """
        sections = []
        size = 0
        for ref in dict.fromkeys(references):
            if not ref.startswith("/artifacts/"):
                continue
            path = PurePosixPath(ref.removeprefix("/artifacts/"))
            if path.is_absolute() or ".." in path.parts or str(path) == ".":
                raise ValueError("invalid verifier artifact path")
            remaining_bytes = 5 * 1024 * 1024 - size
            if remaining_bytes <= 0:
                raise ValueError("verifier evidence artifacts exceed the 5 MiB retention limit")
            try:
                content = self._executor.read_artifact(
                    session_id=session_id, relative_path=str(path), max_bytes=remaining_bytes
                )
            except Exception as exc:
                raise ValueError(f"could not retain verifier artifact {ref}: {exc}") from exc
            size += len(content.encode("utf-8"))
            if size > 5 * 1024 * 1024:
                raise ValueError("verifier evidence artifacts exceed the 5 MiB retention limit")
            sections.append(f"\n\nRetained evidence artifact: {ref}\n{content}")
        return "".join(sections)


def _begin_execution_generation(
    state: TeamState,
    *,
    verification_run_id: str,
    expected_generation: int,
    interrupted: bool,
) -> TeamState:
    run = state.release_verification_runs[verification_run_id]
    release = state.release_packages[run.release_package_id]
    if (
        run.execution_generation != expected_generation
        or run.state != "running"
        or release.state == "cancelled"
    ):
        raise ValueError("verification changed before workspace recovery")
    if interrupted:
        run.generation_history.append(
            {
                "generation": run.execution_generation,
                "session_id": run.session_id,
                "interrupted_at": utc_now().isoformat(),
                "command_results": [item.model_dump(mode="json") for item in run.command_results],
            }
        )
        digest = hashlib.sha256(f"{run.id}:{expected_generation + 1}".encode()).hexdigest()[:30]
        run.session_id = f"team-release-verification-{digest}"
    run.execution_generation += 1
    run.command_results = [
        ReleaseVerificationCommandResult(
            name=item.name,
            execution_generation=run.execution_generation,
        )
        for item in run.command_results
    ]
    run.current_command_index = 0
    run.updated_at = utc_now()
    return state


def _command_with_context(
    command: ReleaseVerificationCommand, release: ReleasePackage
) -> ReleaseVerificationCommand:
    if command.result_protocol != "json-v1":
        return command
    return command.model_copy(
        update={
            "argv": [
                "env",
                f"EVANDER_VERIFICATION_CANDIDATE_SHA={release.candidate_commit_sha}",
                f"EVANDER_VERIFICATION_BASE_SHA={release.manifest.base_commit_sha or ''}",
                *command.argv,
            ]
        }
    )


def _validate_verification_request_identity(
    state: TeamState,
    *,
    release_package_id: str,
    verification_run_id: str,
    candidate_commit_sha: str,
) -> tuple[ReleasePackage, ReleaseVerificationRun]:
    """Validate the immutable run identity before an invocation can coalesce."""

    release = state.release_packages.get(release_package_id)
    run = state.release_verification_runs.get(verification_run_id)
    if release is None or run is None:
        raise ValueError("release package or verification run does not exist")
    if run.release_package_id != release_package_id:
        raise ValueError(
            f"verification run {run.id} belongs to release {run.release_package_id}, "
            f"not {release_package_id}"
        )
    if (
        release.candidate_commit_sha != candidate_commit_sha
        or run.candidate_commit_sha != candidate_commit_sha
    ):
        raise ValueError("release verification request does not match the pinned candidate")
    return release, run


def _snapshot_legacy_verifier_settings(
    state: TeamState,
    *,
    release_package_id: str,
    verification_run_id: str,
    candidate_commit_sha: str,
) -> TeamState:
    release, run = _validate_verification_request_identity(
        state,
        release_package_id=release_package_id,
        verification_run_id=verification_run_id,
        candidate_commit_sha=candidate_commit_sha,
    )
    if run.verifier_settings_snapshotted:
        return state
    project = state.projects.get(release.project_id)
    if project is None:
        raise ValueError("release project is missing")
    policy = project.release_policy
    run.container_image = policy.container_image if policy is not None else None
    run.resource_limits = policy.resource_limits if policy is not None else None
    run.verifier_settings_snapshotted = True
    run.updated_at = utc_now()
    return state


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
                if persisted_failure_kind in {"candidate", "command", "infrastructure"}
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
