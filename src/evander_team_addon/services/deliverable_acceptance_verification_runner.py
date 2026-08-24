from __future__ import annotations

from collections.abc import Callable, Mapping
import time
from typing import Any, Protocol

from evander.models import utc_now
from evander.worker.models import WorkerResourceLimits

from ..models import (
    Deliverable,
    DeliverableAcceptanceVerificationCommandResult,
    ReleaseVerificationCommand,
    ReviewRepositorySnapshot,
    TeamState,
)
from ..repository import TeamStateRepository
from .domain.acceptance_verification import (
    deliverable_requires_server_acceptance_verification,
)
from .commands.outbox import _team_event_outbox_records
from .domain.common import (
    DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS,
    _optional_text,
)
from .domain.wake import _append_agent_wake_requested_event
from .domain.work_due import _mark_agent_work_due
from .release_verification_runner import (
    ReleaseVerificationTimeoutError,
    _bounded_verification_log_excerpt,
)
from .release_verification_singleflight import (
    TEAM_RELEASE_VERIFICATION_SINGLE_FLIGHT,
    TeamReleaseVerificationSingleFlight,
)


class DeliverableAcceptanceVerificationExecutor(Protocol):
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
    ) -> None: ...

    def clone_candidate(
        self,
        *,
        run_id: str,
        session_id: str,
        review_id: str,
        snapshot: ReviewRepositorySnapshot,
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


class TeamDeliverableAcceptanceVerificationRunner:
    """Execute a frozen pre-acceptance policy against one reviewed commit."""

    def __init__(
        self,
        repo: TeamStateRepository,
        executor: DeliverableAcceptanceVerificationExecutor,
        *,
        single_flight: TeamReleaseVerificationSingleFlight | None = None,
    ) -> None:
        self._repo = repo
        self._executor = executor
        self._single_flight = single_flight or TEAM_RELEASE_VERIFICATION_SINGLE_FLIGHT

    def run(
        self,
        *,
        deliverable_id: str,
        verification_run_id: str,
        candidate_commit_sha: str,
    ) -> dict[str, Any]:
        _validate_request(
            self._repo.read(),
            deliverable_id=deliverable_id,
            verification_run_id=verification_run_id,
            candidate_commit_sha=candidate_commit_sha,
        )
        return self._single_flight.run(
            state_db_path=self._repo.store.path,
            verification_run_id=verification_run_id,
            execute=lambda: self._run_once(
                deliverable_id=deliverable_id,
                verification_run_id=verification_run_id,
                candidate_commit_sha=candidate_commit_sha,
            ),
        )

    def _run_once(
        self,
        *,
        deliverable_id: str,
        verification_run_id: str,
        candidate_commit_sha: str,
    ) -> dict[str, Any]:
        state = self._repo.read()
        deliverable, review_id, snapshot = _validate_request(
            state,
            deliverable_id=deliverable_id,
            verification_run_id=verification_run_id,
            candidate_commit_sha=candidate_commit_sha,
        )
        verification = deliverable.verification
        assert verification is not None
        if verification.status in {"passed", "failed", "partial", "not_run"}:
            return {
                "success": True,
                "deliverable_id": deliverable.id,
                "verification_run_id": verification.run_id,
                "verification_state": verification.status,
                "skipped": True,
            }

        session_id = f"team-acceptance-verification-{verification_run_id}"

        def _mark_started(current: TeamState) -> TeamState:
            current_deliverable, _review_id, _snapshot = _validate_request(
                current,
                deliverable_id=deliverable_id,
                verification_run_id=verification_run_id,
                candidate_commit_sha=candidate_commit_sha,
            )
            current_verification = current_deliverable.verification
            assert current_verification is not None
            current_verification.status = "running"
            current_verification.started_at = current_verification.started_at or utc_now()
            return current

        state = self._repo.update(_mark_started)
        deliverable, review_id, snapshot = _validate_request(
            state,
            deliverable_id=deliverable_id,
            verification_run_id=verification_run_id,
            candidate_commit_sha=candidate_commit_sha,
        )
        verification = deliverable.verification
        assert verification is not None
        command_results = [
            result.model_dump(mode="json", exclude_none=True)
            for result in verification.command_results
        ]
        terminal_state = "passed"
        terminal_error: str | None = None
        created = False
        try:
            self._executor.create_verifier(
                run_id=verification_run_id,
                session_id=session_id,
                deliverable_id=deliverable.id,
                candidate_commit_sha=candidate_commit_sha,
                directory_path=snapshot.directory_path,
                container_image=verification.container_image,
                resource_limits=verification.resource_limits,
            )
            created = True
            target_dir = self._executor.clone_candidate(
                run_id=verification_run_id,
                session_id=session_id,
                review_id=review_id,
                snapshot=snapshot,
            )
            self._executor.pin_candidate(
                run_id=verification_run_id,
                session_id=session_id,
                target_dir=target_dir,
                candidate_commit_sha=candidate_commit_sha,
            )
            for index, command in enumerate(verification.verification_commands):
                running = {"name": command.name, "state": "running"}
                self._record_command_result(
                    deliverable_id=deliverable_id,
                    verification_run_id=verification_run_id,
                    candidate_commit_sha=candidate_commit_sha,
                    command_index=index,
                    command_result=running,
                )
                command_results[index] = running

                def _record_started_job(
                    job_id: str,
                    *,
                    _index: int = index,
                    _name: str = command.name,
                ) -> None:
                    started_result = {
                        "name": _name,
                        "state": "running",
                        "job_id": job_id,
                    }
                    self._record_command_result(
                        deliverable_id=deliverable_id,
                        verification_run_id=verification_run_id,
                        candidate_commit_sha=candidate_commit_sha,
                        command_index=_index,
                        command_result=started_result,
                    )
                    command_results[_index] = started_result

                started = time.monotonic()
                try:
                    output = self._executor.execute_command(
                        run_id=verification_run_id,
                        command_index=index,
                        session_id=session_id,
                        target_dir=target_dir,
                        command=command,
                        on_started=_record_started_job,
                    )
                except ReleaseVerificationTimeoutError as exc:
                    duration = time.monotonic() - started
                    terminal_state = "partial"
                    terminal_error = str(exc) or f"verification command {command.name!r} timed out"
                    result = {
                        "name": command.name,
                        "state": "timed_out",
                        "failure_kind": "timeout",
                        "duration_seconds": duration,
                        "log_excerpt": _bounded_verification_log_excerpt(terminal_error),
                    }
                    self._record_command_result(
                        deliverable_id=deliverable_id,
                        verification_run_id=verification_run_id,
                        candidate_commit_sha=candidate_commit_sha,
                        command_index=index,
                        command_result=result,
                    )
                    command_results[index] = result
                    break
                duration = time.monotonic() - started
                success = bool(output.get("success"))
                status = str(output.get("status") or "").strip().casefold()
                timed_out = not success and (
                    output.get("failure_kind") == "timeout"
                    or status in {"unknown", "preparing", "starting", "running"}
                )
                failure_kind = (
                    None
                    if success
                    else "timeout"
                    if timed_out
                    else "candidate"
                    if output.get("failure_kind") == "command"
                    else "infrastructure"
                )
                log = "\n".join(
                    part
                    for part in (
                        _optional_text(output.get("stdout")),
                        _optional_text(output.get("stderr")),
                    )
                    if part
                )
                result = {
                    "name": command.name,
                    "state": "passed" if success else "timed_out" if timed_out else "failed",
                    **({"failure_kind": failure_kind} if failure_kind is not None else {}),
                    "job_id": (
                        _optional_text(output.get("execution_id"))
                        or command_results[index].get("job_id")
                    ),
                    "exit_code": output.get("exit_code"),
                    "failure_cause": _optional_text(output.get("failure_cause")),
                    "duration_seconds": duration,
                    "log_excerpt": _bounded_verification_log_excerpt(log) or None,
                }
                self._record_command_result(
                    deliverable_id=deliverable_id,
                    verification_run_id=verification_run_id,
                    candidate_commit_sha=candidate_commit_sha,
                    command_index=index,
                    command_result=result,
                )
                command_results[index] = result
                if not success:
                    terminal_state = "failed" if failure_kind == "candidate" else "partial"
                    terminal_error = (
                        f"verification command {command.name!r} timed out"
                        if timed_out
                        else f"verification command {command.name!r} failed"
                        if failure_kind == "candidate"
                        else f"verification command {command.name!r} failed in verifier infrastructure"
                    )
                    break
        except ReleaseVerificationTimeoutError as exc:
            terminal_state = "partial"
            terminal_error = str(exc) or "acceptance verification timed out"
        except Exception as exc:
            terminal_state = "partial"
            terminal_error = str(exc) or type(exc).__name__
        finally:
            if created:
                try:
                    self._executor.destroy_verifier(
                        run_id=verification_run_id,
                        session_id=session_id,
                    )
                except Exception as exc:
                    if terminal_state == "passed":
                        terminal_state = "partial"
                        terminal_error = f"failed to destroy acceptance verifier: {exc}"

        result: dict[str, Any] = {"events": []}

        def _record_terminal(current: TeamState) -> TeamState:
            current_deliverable, _review_id, _snapshot = _validate_request(
                current,
                deliverable_id=deliverable_id,
                verification_run_id=verification_run_id,
                candidate_commit_sha=candidate_commit_sha,
            )
            current_verification = current_deliverable.verification
            assert current_verification is not None
            current_verification.status = terminal_state
            current_verification.summary = (
                "Server-owned release-equivalent verification passed."
                if terminal_state == "passed"
                else terminal_error
                or (
                    "Server-owned release-equivalent verification was partial."
                    if terminal_state == "partial"
                    else "Server-owned release-equivalent verification failed."
                )
            )
            current_verification.evidence_refs = (
                [
                    "team://deliverables/"
                    f"{deliverable_id}/acceptance-verifications/{verification_run_id}"
                ]
                if terminal_state == "passed"
                else []
            )
            finished_at = utc_now()
            current_verification.finished_at = finished_at
            reviewer_ids = {
                review.reviewer_agent_id
                for review_id in current_deliverable.review_request_ids
                if (review := current.review_requests.get(review_id)) is not None
                and review.state.value in {"pending", "in_progress"}
                and review.superseded_by_review_request_id is None
                and review.retired_at is None
            }
            for reviewer_agent_id in sorted(reviewer_ids):
                _mark_agent_work_due(
                    current,
                    agent_id=reviewer_agent_id,
                    now=finished_at,
                )
                _append_agent_wake_requested_event(
                    current,
                    agent_id=reviewer_agent_id,
                    result=result,
                    reason="review_verification_completed",
                    now=finished_at,
                    delay_seconds=DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS,
                    idempotency_suffix=verification_run_id,
                    payload_extra={
                        "deliverable_id": deliverable_id,
                        "verification_run_id": verification_run_id,
                        "verification_state": terminal_state,
                    },
                )
            return current

        final_state = self._repo.update_with_outbox(
            _record_terminal,
            outbox_records=lambda: _team_event_outbox_records(result["events"]),
        )
        final = final_state.deliverables[deliverable_id].verification
        assert final is not None
        return {
            "success": True,
            "revision": final_state.revision,
            "deliverable_id": deliverable_id,
            "verification_run_id": verification_run_id,
            "verification_state": final.status,
            "events": result["events"],
            **({"error": terminal_error} if terminal_error is not None else {}),
        }

    def _record_command_result(
        self,
        *,
        deliverable_id: str,
        verification_run_id: str,
        candidate_commit_sha: str,
        command_index: int,
        command_result: Mapping[str, Any],
    ) -> None:
        parsed = DeliverableAcceptanceVerificationCommandResult.model_validate(command_result)

        def _mutate(state: TeamState) -> TeamState:
            deliverable, _review_id, _snapshot = _validate_request(
                state,
                deliverable_id=deliverable_id,
                verification_run_id=verification_run_id,
                candidate_commit_sha=candidate_commit_sha,
            )
            verification = deliverable.verification
            assert verification is not None
            if command_index < 0 or command_index >= len(verification.command_results):
                raise ValueError("acceptance verification command index is out of range")
            if verification.command_results[command_index].name != parsed.name:
                raise ValueError("acceptance verification command identity changed")
            verification.command_results[command_index] = parsed
            verification.status = "running"
            return state

        self._repo.update(_mutate)


def _validate_request(
    state: TeamState,
    *,
    deliverable_id: str,
    verification_run_id: str,
    candidate_commit_sha: str,
) -> tuple[Deliverable, str, ReviewRepositorySnapshot]:
    deliverable = state.deliverables.get(deliverable_id)
    if deliverable is None:
        raise ValueError(f"deliverable {deliverable_id} does not exist")
    task = state.tasks.get(deliverable.task_id)
    if task is None or not deliverable_requires_server_acceptance_verification(
        state,
        task=task,
        deliverable=deliverable,
    ):
        raise ValueError(f"deliverable {deliverable_id} does not require server verification")
    verification = deliverable.verification
    if (
        verification is None
        or verification.provenance != "server_release_equivalent"
        or verification.run_id != verification_run_id
        or verification.candidate_commit_sha != candidate_commit_sha
    ):
        raise ValueError("acceptance verification request does not match the frozen run")
    snapshots = [
        (review_id, review.repository_snapshot)
        for review_id in deliverable.review_request_ids
        if (review := state.review_requests.get(review_id)) is not None
        and review.repository_snapshot is not None
        and review.repository_snapshot.commit_sha.casefold() == candidate_commit_sha.casefold()
    ]
    if not snapshots:
        raise ValueError("acceptance verification has no immutable review repository snapshot")
    review_id, snapshot = snapshots[0]
    if any(
        item.managed_repo_id != snapshot.managed_repo_id
        or item.directory_path != snapshot.directory_path
        or item.commit_sha.casefold() != snapshot.commit_sha.casefold()
        for _item_review_id, item in snapshots[1:]
    ):
        raise ValueError("acceptance verification review snapshots disagree")
    return deliverable, review_id, snapshot


__all__ = [
    "DeliverableAcceptanceVerificationExecutor",
    "TeamDeliverableAcceptanceVerificationRunner",
]
