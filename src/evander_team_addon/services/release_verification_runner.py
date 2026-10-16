from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
from pathlib import PurePosixPath
import time
from typing import Any, Protocol

from evander.worker.models import WorkerResourceLimits

from ..models import (
    ProjectGitConfig,
    ReleaseCandidateManifest,
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
        candidate_commit_sha: str,
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
        return _ReleaseVerificationExecution(
            runner=self,
            release_package_id=release_package_id,
            verification_run_id=verification_run_id,
            candidate_commit_sha=candidate_commit_sha,
        ).execute()

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


class _ReleaseVerificationExecution:
    """Own one run's frozen inputs, workspace generation, and durable progress."""

    release: ReleasePackage
    run: ReleaseVerificationRun
    git: ProjectGitConfig
    manifest: ReleaseCandidateManifest
    verification_commands: list[ReleaseVerificationCommand]
    command_results: list[dict[str, Any]]
    execution_id: str
    target_dir: str

    def __init__(
        self,
        *,
        runner: TeamReleaseVerificationRunner,
        release_package_id: str,
        verification_run_id: str,
        candidate_commit_sha: str,
    ) -> None:
        self.runner = runner
        self._repo = runner._repo
        self._executor = runner._executor
        self._release_gate_service = runner._release_gate_service
        self.release_package_id = release_package_id
        self.verification_run_id = verification_run_id
        self.candidate_commit_sha = candidate_commit_sha
        self.terminal_state = "passed"
        self.failure_kind: str | None = None
        self.terminal_error: str | None = None
        self.workspace_creation_attempted = False

    def execute(self) -> dict[str, Any]:
        skipped = self._load_candidate()
        if skipped is not None:
            return skipped
        self._mark_started()
        persisted = self._finish_persisted_commands()
        if persisted is not None:
            return persisted
        try:
            self._prepare_workspace()
            self._execute_commands()
        except ReleaseVerificationTimeoutError as exc:
            self.terminal_state = "timed_out"
            self.failure_kind = "timeout"
            self.terminal_error = str(exc) or "release verification timed out"
        except Exception as exc:
            self.terminal_state = "failed"
            self.failure_kind = "infrastructure"
            self.terminal_error = str(exc) or type(exc).__name__
        finally:
            self._cleanup_workspace()
        return self._record_result(
            error=redact_verifier_text(self.terminal_error) if self.terminal_error else None
        )

    def _load_candidate(self) -> dict[str, Any] | None:
        state = self._repo.read()
        self.release, self.run = _validate_verification_request_identity(
            state,
            release_package_id=self.release_package_id,
            verification_run_id=self.verification_run_id,
            candidate_commit_sha=self.candidate_commit_sha,
        )
        if self.run.state in {"passed", "failed", "timed_out", "cancelled"}:
            return {
                "success": True,
                "release_package_id": self.release_package_id,
                "verification_run_id": self.verification_run_id,
                "verification_state": self.run.state,
                "skipped": True,
            }

        project = state.projects.get(self.release.project_id)
        self.git = project.git if project is not None else None
        self.manifest = self.release.manifest
        if project is None or self.git is None or self.manifest is None:
            raise ValueError(
                "release project, Git configuration, or frozen candidate manifest is missing"
            )
        self.verification_commands = self.manifest.verification_commands
        if not self.verification_commands:
            raise ValueError("release verification run has no frozen commands")
        if not self.run.verifier_settings_snapshotted:
            state = self._repo.update(
                lambda current: _snapshot_legacy_verifier_settings(
                    current,
                    release_package_id=self.release_package_id,
                    verification_run_id=self.verification_run_id,
                    candidate_commit_sha=self.candidate_commit_sha,
                )
            )
            self.release, self.run = _validate_verification_request_identity(
                state,
                release_package_id=self.release_package_id,
                verification_run_id=self.verification_run_id,
                candidate_commit_sha=self.candidate_commit_sha,
            )
            project = state.projects.get(self.release.project_id)
            self.git = project.git if project is not None else None
            self.manifest = self.release.manifest
            if project is None or self.git is None or self.manifest is None:
                raise ValueError(
                    "release project, Git configuration, or frozen candidate manifest is missing"
                )
            self.verification_commands = self.manifest.verification_commands

        return None

    def _mark_started(self) -> None:
        state = self._release_gate_service.mark_verification_started(
            release_package_id=self.release_package_id,
            verification_run_id=self.verification_run_id,
            candidate_commit_sha=self.candidate_commit_sha,
        )
        self.run = state.release_verification_runs[self.verification_run_id]
        self.command_results = [
            item.model_dump(mode="json", exclude_none=True) for item in self.run.command_results
        ]
        if len(self.command_results) != len(self.verification_commands):
            raise ValueError("release verification run does not match its frozen command manifest")

    def _finish_persisted_commands(self) -> dict[str, Any] | None:
        persisted_outcome = _persisted_terminal_outcome(self.command_results)
        if persisted_outcome is not None:
            self.terminal_state, self.failure_kind, self.terminal_error = persisted_outcome
            # The process may have stopped between final command persistence and
            # session cleanup. Reuse evidence, but still reconcile the session.
            try:
                self._executor.destroy_verifier(
                    run_id=f"{self.run.id}-g{self.run.execution_generation}",
                    session_id=self.run.session_id,
                )
            except Exception as exc:
                if self.terminal_state == "passed":
                    self.terminal_state, self.failure_kind = "failed", "infrastructure"
                    self.terminal_error = redact_verifier_text(
                        f"failed to destroy release verifier: {exc}"
                    )
            return self._record_result(error=self.terminal_error)
        return None

    def _prepare_workspace(self) -> None:
        prune_verification_logs(self._repo.runtime_dir)
        # A passed command is only reusable with its original workspace. The
        # executor always repins/cleans, so an interrupted run restarts fully.
        interrupted = self.run.execution_generation > 0 or any(
            item.get("state") != "pending" for item in self.command_results
        )
        if interrupted:
            self._executor.destroy_verifier(
                run_id=f"{self.run.id}-g{self.run.execution_generation}",
                session_id=self.run.session_id,
            )
        if self.run.execution_generation >= self.manifest.verification_max_generations:
            raise RuntimeError("verifier workspace recovery limit exhausted")
        state = self._repo.update(
            lambda current: _begin_execution_generation(
                current,
                verification_run_id=self.run.id,
                expected_generation=self.run.execution_generation,
                interrupted=interrupted,
            )
        )
        self.run = state.release_verification_runs[self.run.id]
        self.execution_id = f"{self.run.id}-g{self.run.execution_generation}"
        self.command_results = [
            item.model_dump(mode="json", exclude_none=True) for item in self.run.command_results
        ]
        self.workspace_creation_attempted = True  # Reconcile partially successful setup too.
        self._executor.create_verifier(
            run_id=self.execution_id,
            session_id=self.run.session_id,
            release_package_id=self.release.id,
            candidate_commit_sha=self.candidate_commit_sha,
            directory_path=self.git.directory_path,
            container_image=self.run.container_image,
            resource_limits=self.run.resource_limits,
        )
        self.target_dir = self._executor.clone_candidate(
            run_id=self.execution_id,
            session_id=self.run.session_id,
            managed_repo_id=self.git.managed_repo_id,
            release_branch=self.release.release_branch,
            candidate_commit_sha=self.candidate_commit_sha,
        )
        self._executor.pin_candidate(
            run_id=self.execution_id,
            session_id=self.run.session_id,
            target_dir=self.target_dir,
            candidate_commit_sha=self.candidate_commit_sha,
        )

    def _execute_commands(self) -> None:
        for index, command in enumerate(self.verification_commands):
            if self._repo.read().release_verification_runs[self.run.id].state == "cancelled":
                break
            existing_result = self.command_results[index]
            if existing_result.get("state") == "passed":
                continue
            if not _ReleaseVerificationCommandExecution(
                execution=self,
                index=index,
                command=command,
            ).execute():
                break

    def _cleanup_workspace(self) -> None:
        if self.workspace_creation_attempted:
            try:
                self._executor.destroy_verifier(
                    run_id=f"{self.run.id}-g{self.run.execution_generation}",
                    session_id=self.run.session_id,
                )
            except Exception as exc:
                if self.terminal_state == "passed":
                    self.terminal_state = "failed"
                    self.failure_kind = "infrastructure"
                    self.terminal_error = f"failed to destroy release verifier: {exc}"

    def _record_command_progress(self, index: int, result: dict[str, Any]) -> None:
        self._release_gate_service.record_verification_command_progress(
            release_package_id=self.release_package_id,
            verification_run_id=self.verification_run_id,
            candidate_commit_sha=self.candidate_commit_sha,
            command_index=index,
            command_result=result,
        )
        self.command_results[index] = result

    def _record_result(self, *, error: str | None) -> dict[str, Any]:
        _state, result = self._release_gate_service.record_verification_result(
            release_package_id=self.release_package_id,
            verification_run_id=self.verification_run_id,
            candidate_commit_sha=self.candidate_commit_sha,
            verification_state=self.terminal_state,
            command_results=self.command_results,
            failure_kind=self.failure_kind,
            error=error,
            execution_generation=self.run.execution_generation,
        )
        return result


class _ReleaseVerificationCommandExecution:
    """Persist one command's worker execution and evidence policy separately."""

    duration: float
    success: bool
    command_timed_out: bool
    command_failure_kind: str | None
    command_log: str
    execution_outcome: str
    evidence: dict[str, Any] | None
    policy_outcome: str

    def __init__(
        self,
        *,
        execution: _ReleaseVerificationExecution,
        index: int,
        command: ReleaseVerificationCommand,
    ) -> None:
        self.execution = execution
        self.index = index
        self.command = command

    def execute(self) -> bool:
        self._mark_started()
        started = time.monotonic()
        try:
            command_output = self.execution._executor.execute_command(
                run_id=self.execution.execution_id,
                command_index=self.index,
                session_id=self.execution.run.session_id,
                target_dir=self.execution.target_dir,
                command=_command_with_context(self.command, self.execution.release),
                on_started=self._record_started_job,
            )
        except ReleaseVerificationTimeoutError as exc:
            self._record_timeout(exc, duration=time.monotonic() - started)
            return False
        self.duration = time.monotonic() - started
        self._classify_output(command_output)
        self._apply_evidence(command_output)
        self._record_output(command_output)
        if not self.success:
            self._fail_run()
        return self.success

    def _mark_started(self) -> None:
        existing_result = self.execution.command_results[self.index]
        started_result = {
            "name": self.command.name,
            "execution_generation": self.execution.run.execution_generation,
            "state": "running",
            **({"job_id": existing_result["job_id"]} if existing_result.get("job_id") else {}),
        }
        self.execution._record_command_progress(self.index, started_result)

    def _record_started_job(self, job_id: str) -> None:
        running_result = {
            "name": self.command.name,
            "execution_generation": self.execution.run.execution_generation,
            "state": "running",
            "job_id": job_id,
        }
        self.execution._record_command_progress(self.index, running_result)

    def _record_timeout(self, exc: ReleaseVerificationTimeoutError, *, duration: float) -> None:
        error = redact_verifier_text(
            str(exc) or f"release verification command {self.command.name!r} timed out"
        )
        job_id = (
            self.execution.command_results[self.index].get("job_id")
            or f"{self.execution.execution_id}-{self.index}"
        )
        result = {
            "name": self.command.name,
            "execution_generation": self.execution.run.execution_generation,
            "state": "timed_out",
            "failure_kind": "timeout",
            "execution_outcome": "timeout",
            "policy_outcome": "infrastructure",
            "job_id": job_id,
            "log_artifact": retain_verification_log(
                self.execution._repo.runtime_dir,
                job_id=job_id,
                text=error,
                retention_days=self.execution.manifest.verification_log_retention_days,
            ),
            "duration_seconds": duration,
            "log_excerpt": _bounded_verification_log_excerpt(error),
        }
        self.execution._record_command_progress(self.index, result)
        self.execution.terminal_state = "timed_out"
        self.execution.failure_kind = "timeout"
        self.execution.terminal_error = error

    def _classify_output(self, command_output: Mapping[str, Any]) -> None:
        self.success = bool(command_output.get("success"))
        command_status = str(command_output.get("status") or "").strip().casefold()
        self.command_timed_out = not self.success and (
            command_output.get("failure_kind") == "timeout"
            or command_status in {"unknown", "preparing", "starting", "running"}
        )
        self.command_failure_kind = (
            None
            if self.success
            else "timeout"
            if self.command_timed_out
            else "candidate"
            if command_output.get("failure_kind") == "command"
            else "infrastructure"
        )
        self.command_log = "\n".join(
            part
            for part in (
                _optional_text(command_output.get("stdout")),
                _optional_text(command_output.get("stderr")),
            )
            if part
        )
        self.execution_outcome = (
            "success"
            if self.success
            else "timeout"
            if self.command_timed_out
            else "command_failure"
            if command_output.get("failure_kind") == "command"
            else "infrastructure"
        )
        self.evidence = None
        self.policy_outcome = (
            "passed"
            if self.success
            else "infrastructure"
            if self.execution_outcome in {"infrastructure", "timeout"}
            else "failed"
        )

    def _apply_evidence(self, command_output: Mapping[str, Any]) -> None:
        # Worker infrastructure/timeout evidence always takes precedence.
        if self.command.result_protocol == "json-v1" and self.execution_outcome in {
            "success",
            "command_failure",
        }:
            try:
                report = read_verification_evidence(
                    str(command_output.get("stdout") or ""),
                    command=self.command,
                    candidate_commit_sha=self.execution.candidate_commit_sha,
                    base_commit_sha=self.execution.manifest.base_commit_sha,
                )
                artifact_log = self.execution.runner._read_evidence_artifacts(
                    report.artifact_refs, session_id=self.execution.run.session_id
                )
                if artifact_log:
                    # Reserve room for complete evidence attachments; a
                    # long build log must not truncate the comparison.
                    self.command_log = (
                        bound_log_text(self.command_log, limit=4 * 1024 * 1024) + artifact_log
                    )
                self.evidence = report.model_dump(mode="json")
                self.evidence["explanation"] = redact_verifier_text(self.evidence["explanation"])
                self.evidence["artifact_refs"] = [
                    redact_verifier_text(ref) for ref in self.evidence["artifact_refs"]
                ]
                self.policy_outcome = report.outcome
                self.success = self.execution_outcome == "success" and report.outcome in {
                    "passed",
                    "baseline_exception",
                }
                if not self.success and report.outcome in {"passed", "baseline_exception"}:
                    self.policy_outcome = "failed"
                self.command_failure_kind = (
                    None
                    if self.success
                    else ("infrastructure" if report.outcome == "infrastructure" else "candidate")
                )
            except (ValueError, TypeError) as exc:
                self.success = False
                self.policy_outcome = "infrastructure"
                self.command_failure_kind = "infrastructure"
                self.command_log += f"\nInvalid verifier evidence: {exc}"

    def _record_output(self, command_output: Mapping[str, Any]) -> None:
        if (
            not self.success
            and self.command_failure_kind == "candidate"
            and self.command.command_failure_action == "block"
        ):
            self.command_failure_kind = "command"
        self.command_log = redact_verifier_text(self.command_log)
        job_id = (
            _optional_text(command_output.get("execution_id"))
            or self.execution.command_results[self.index].get("job_id")
            or f"{self.execution.execution_id}-{self.index}"
        )
        log_artifact = retain_verification_log(
            self.execution._repo.runtime_dir,
            job_id=job_id,
            text=self.command_log,
            retention_days=self.execution.manifest.verification_log_retention_days,
        )
        excerpt = _bounded_verification_log_excerpt(self.command_log)
        result = {
            "name": self.command.name,
            "execution_generation": self.execution.run.execution_generation,
            "execution_outcome": self.execution_outcome,
            "policy_outcome": self.policy_outcome,
            "evidence": self.evidence,
            "log_artifact": log_artifact,
            "toolchain_mounts": command_output.get("toolchain_mounts", []),
            "state": "passed"
            if self.success
            else "timed_out"
            if self.command_timed_out
            else "failed",
            **(
                {"failure_kind": self.command_failure_kind}
                if self.command_failure_kind is not None
                else {}
            ),
            "job_id": job_id,
            "exit_code": command_output.get("exit_code"),
            "failure_cause": _optional_text(command_output.get("failure_cause")),
            "duration_seconds": self.duration,
            "log_excerpt": excerpt or None,
        }
        self.execution._record_command_progress(self.index, result)

    def _fail_run(self) -> None:
        if self.command_timed_out:
            self.execution.terminal_state = "timed_out"
            self.execution.failure_kind = self.command_failure_kind
            self.execution.terminal_error = (
                f"release verification command {self.command.name!r} timed out"
            )
        else:
            self.execution.terminal_state = "failed"
            self.execution.failure_kind = self.command_failure_kind
            if self.command_failure_kind in {"candidate", "command"}:
                self.execution.terminal_error = (
                    f"release verification command {self.command.name!r} failed"
                )
            else:
                self.execution.terminal_error = (
                    f"release verification command {self.command.name!r} "
                    "failed in verifier infrastructure"
                )


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
