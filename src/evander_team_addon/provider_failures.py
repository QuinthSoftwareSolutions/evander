"""Provider failures."""

from __future__ import annotations

from collections.abc import (
    Mapping,
    Sequence,
)
from datetime import (
    UTC,
    datetime,
)
from typing import (
    Any,
)

from .models import (
    TeamState,
)
from .provider_projection import (
    _string_items,
)
from .provider_values import (
    _optional_text,
    _required_text,
)
from .repository import (
    TeamStateRepository,
)
from .services.agent_failure_service import (
    record_agent_wake_failure,
)
from .services.commands.outbox import (
    _team_event_outbox_records,
)
from .services.domain.dependency_composition_recovery import (
    record_dependency_composition_conflict,
)
from .services.domain.preparation_failures import (
    dispatch_project_work_ids,
    record_inbox_preparation_failure,
    record_review_preparation_failure,
    record_task_preparation_failure,
)
from .services.runtime_service import (
    TeamRuntimeService,
)


def _record_wake_failure(
    repo: TeamStateRepository,
    *,
    lease: Mapping[str, Any],
    error: str,
    delivery_not_started: bool = False,
) -> Any:
    agent_id = _required_text(lease["agent"].get("id"), "agent_id")
    dispatch_id = _required_text(lease.get("dispatch_id"), "dispatch_id")
    current_dispatch = repo.read().dispatches.get(dispatch_id)
    if current_dispatch is not None and current_dispatch.cause_kind == "context_handoff":
        return TeamRuntimeService(repo).fail_agent_dispatch(
            agent_id=agent_id,
            dispatch_id=dispatch_id,
            error=error,
            delivery_not_started=delivery_not_started,
        )
    if current_dispatch is not None and current_dispatch.status.value == "preparing":
        return TeamRuntimeService(repo).fail_agent_dispatch(
            agent_id=agent_id,
            dispatch_id=dispatch_id,
            error=error,
            delivery_not_started=delivery_not_started,
        )
    state = record_agent_wake_failure(
        repo,
        session_id=_required_text(lease["agent"].get("session_id"), "session_id"),
        dispatch_id=dispatch_id,
        error=error,
    )
    if current_dispatch is not None:
        state = TeamRuntimeService(repo).fail_agent_dispatch(
            agent_id=agent_id,
            dispatch_id=dispatch_id,
            error=error,
            delivery_not_started=delivery_not_started,
        )
    return state


def _record_dependency_git_preparation_blocker(
    repo: TeamStateRepository,
    *,
    lease: Mapping[str, Any],
    task_id: str,
    error: str,
    conflict: Mapping[str, object] | None = None,
    cause: str = "unknown",
    evidence: Mapping[str, object] | None = None,
) -> Any:
    agent_id = _required_text(lease["agent"].get("id"), "agent_id")
    result: dict[str, Any] = {"events": []}

    def _mutate(state: TeamState) -> TeamState:
        task = state.tasks.get(task_id)
        agent = state.agents.get(agent_id)
        if task is None or agent is None or agent.team_id is None:
            raise RuntimeError(
                f"cannot route dependency Git preparation blocker for task {task_id}"
            )
        project = state.projects.get(task.project_id)
        if project is None:
            raise RuntimeError(
                f"cannot route dependency Git preparation blocker without project {task.project_id}"
            )
        effective_now = datetime.now(UTC)
        blocker = record_task_preparation_failure(
            state,
            task_id=task.id,
            reporter_agent_id=agent.id,
            error=error,
            diagnostic_cause="merge_conflict" if conflict is not None else cause,
            reason_code=(
                "dependency_git_composition_conflict"
                if conflict is not None
                else "dependency_git_preparation_failed"
            ),
            result=result,
            now=effective_now,
        )
        if blocker is not None:
            from .services.domain.git_recovery import record_incident
            diagnostic_evidence = dict(evidence or {})
            attempts = [row for row in state.git_preparation_attempts.values()
                        if row.task_id == task.id and row.latest_dispatch_id == (lease.get("dispatch") or {}).get("id")]
            if len(attempts) == 1:
                diagnostic_evidence["attempt_id"] = attempts[0].id
            record_incident(state, blocker=blocker,
                            cause="merge_conflict" if conflict is not None else cause,
                            evidence=diagnostic_evidence)
        if conflict is not None and blocker is not None:
            record_dependency_composition_conflict(
                state,
                blocker=blocker,
                conflict=conflict,
                now=effective_now,
            )
        return state

    return repo.update_with_outbox(
        _mutate,
        outbox_records=lambda: _team_event_outbox_records(result.get("events") or []),
    )


def _record_project_git_preparation_blockers(
    repo: TeamStateRepository,
    *,
    lease: Mapping[str, Any],
    project_id: str,
    error: str,
    cause: str = "unknown",
    evidence: Mapping[str, object] | None = None,
) -> tuple[TeamState, list[str]]:
    agent_id = _required_text(lease["agent"].get("id"), "agent_id")
    dispatch = lease.get("dispatch") if isinstance(lease.get("dispatch"), Mapping) else {}
    result: dict[str, Any] = {"events": []}

    def _mutate(state: TeamState) -> TeamState:
        task_ids, review_ids, inbox_ids = dispatch_project_work_ids(
            state,
            dispatch=dispatch,
            project_id=project_id,
        )
        for review_id in review_ids:
            record_review_preparation_failure(
                state,
                review_request_id=review_id,
                error=(f"Repository preparation failed before review inference: {error}"),
                result=result,
            )
        for task_id in task_ids:
            blocker = record_task_preparation_failure(
                state,
                task_id=task_id,
                reporter_agent_id=agent_id,
                error=error,
                diagnostic_cause=cause,
                result=result,
            )
            if blocker is not None:
                from .services.domain.git_recovery import record_incident
                record_incident(state, blocker=blocker, cause=cause, evidence=evidence)
        for inbox_id in inbox_ids:
            record_inbox_preparation_failure(
                state,
                inbox_item_id=inbox_id,
                reporter_agent_id=agent_id,
                project_id=project_id,
                error=error,
                result=result,
            )
        if not task_ids and not review_ids and not inbox_ids:
            raise RuntimeError(
                f"project preparation failure for {project_id} had no durable "
                "dispatch work item to suppress"
            )
        return state

    state = repo.update_with_outbox(
        _mutate,
        outbox_records=lambda: _team_event_outbox_records(result.get("events") or []),
    )
    return state, list(result.get("created_blocker_ids") or [])


def _review_artifact_preparation_errors(
    errors: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    grouped: dict[str, list[str]] = {}
    for record in errors:
        review_id = _optional_text(record.get("review_request_id"))
        if review_id is None:
            continue
        message = (
            _optional_text(record.get("error")) or "declared review artifact could not be prepared"
        )
        grouped.setdefault(review_id, []).append(message)
    if not grouped:
        raise RuntimeError("review artifact preparation failed without a review request identity")
    return {
        review_id: "; ".join(sorted(set(messages)))
        for review_id, messages in sorted(grouped.items())
    }


def _record_review_artifact_preparation_blockers(
    repo: TeamStateRepository,
    *,
    grouped_errors: Mapping[str, str],
) -> tuple[TeamState, list[str]]:
    result: dict[str, Any] = {"events": []}

    def _mutate(state: TeamState) -> TeamState:
        for review_id, error in grouped_errors.items():
            record_review_preparation_failure(
                state,
                review_request_id=review_id,
                error=f"Review artifact preparation failed: {error}",
                result=result,
            )
        return state

    state = repo.update_with_outbox(
        _mutate,
        outbox_records=lambda: _team_event_outbox_records(result.get("events") or []),
    )
    return state, list(result.get("created_blocker_ids") or [])


def _record_review_repository_preparation_blockers(
    repo: TeamStateRepository,
    *,
    review_request_ids: Sequence[str],
    error: str,
) -> tuple[TeamState, list[str]]:
    """Block only the review attempts whose immutable checkout could not prepare."""

    result: dict[str, Any] = {"events": []}

    def _mutate(state: TeamState) -> TeamState:
        for review_id in sorted(set(_string_items(review_request_ids))):
            record_review_preparation_failure(
                state,
                review_request_id=review_id,
                error=f"Review repository snapshot preparation failed: {error}",
                result=result,
            )
        return state

    state = repo.update_with_outbox(
        _mutate,
        outbox_records=lambda: _team_event_outbox_records(result.get("events") or []),
    )
    return state, list(result.get("created_blocker_ids") or [])


def _new_preparation_blocker_ids(
    state: TeamState,
    *,
    task_ids: Sequence[str],
) -> list[str]:
    selected = set(task_ids)
    return sorted(
        blocker.id
        for blocker in state.blockers.values()
        if blocker.state.value == "open"
        and blocker.task_id in selected
        and blocker.reason_code
        in {
            "dependency_git_preparation_failed",
            "task_execution_preparation_failed",
        }
    )
