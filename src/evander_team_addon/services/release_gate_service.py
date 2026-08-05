from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..models import ReleaseVerificationCommandResult, TeamState
from ..repository import TeamStateRepository
from .commands.outbox import _team_event_outbox_records
from .domain.release_gates import (
    mark_release_verification_started,
    record_release_verification_command_progress,
    record_release_verification_result,
)
from .domain.human_reviews import (
    HUMAN_REVIEW_ELIGIBILITY_RESOLVER_RESULT_KEY,
    HUMAN_REVIEW_PREPARATION_REQUESTED_EVENT_TYPE,
    HUMAN_REVIEW_REQUESTED_EVENT_TYPE,
)
from .domain.wake import AGENT_WAKE_REQUESTED_EVENT_TYPE


class TeamReleaseGateService:
    def __init__(
        self,
        repository: TeamStateRepository,
        *,
        human_review_eligibility_resolver: Callable[[Any], list[str]] | None = None,
    ) -> None:
        self._repository = repository
        self._human_review_eligibility_resolver = (
            human_review_eligibility_resolver
        )

    def record_verification_result(
        self,
        *,
        release_package_id: str,
        verification_run_id: str,
        candidate_commit_sha: str,
        verification_state: str,
        command_results: list[dict[str, Any]],
        failure_kind: str | None = None,
        error: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_results = [
            ReleaseVerificationCommandResult.model_validate(item) for item in command_results
        ]
        result: dict[str, Any] = {
            "success": False,
            "release_package_id": release_package_id,
            "verification_run_id": verification_run_id,
            "verification_state": verification_state,
            "created_review_request_ids": [],
            "created_inbox_item_ids": [],
            "events": [],
            "error": error,
        }
        if self._human_review_eligibility_resolver is not None:
            result[HUMAN_REVIEW_ELIGIBILITY_RESOLVER_RESULT_KEY] = (
                self._human_review_eligibility_resolver
            )

        def _mutate(state: TeamState) -> TeamState:
            record_release_verification_result(
                state,
                release_package_id=release_package_id,
                verification_run_id=verification_run_id,
                candidate_commit_sha=candidate_commit_sha,
                verification_state=verification_state,
                failure_kind=failure_kind,
                command_results=normalized_results,
                error=error,
                result=result,
            )
            result["success"] = True
            return state

        state = self._repository.update_with_outbox(
            _mutate,
            outbox_records=lambda: _team_event_outbox_records(
                [
                    event
                    for event in result["events"]
                    if (
                        event.get("event_type")
                        in {
                            AGENT_WAKE_REQUESTED_EVENT_TYPE,
                            HUMAN_REVIEW_PREPARATION_REQUESTED_EVENT_TYPE,
                            HUMAN_REVIEW_REQUESTED_EVENT_TYPE,
                        }
                    )
                ]
            ),
        )
        result.pop(HUMAN_REVIEW_ELIGIBILITY_RESOLVER_RESULT_KEY, None)
        result["revision"] = state.revision
        return state, result

    def mark_verification_started(
        self,
        *,
        release_package_id: str,
        verification_run_id: str,
        candidate_commit_sha: str,
    ) -> TeamState:
        return self._repository.update(
            lambda state: _mark_verification_started(
                state,
                release_package_id=release_package_id,
                verification_run_id=verification_run_id,
                candidate_commit_sha=candidate_commit_sha,
            )
        )

    def record_verification_command_progress(
        self,
        *,
        release_package_id: str,
        verification_run_id: str,
        candidate_commit_sha: str,
        command_index: int,
        command_result: dict[str, Any],
    ) -> TeamState:
        normalized_result = ReleaseVerificationCommandResult.model_validate(command_result)
        return self._repository.update(
            lambda state: _record_verification_command_progress(
                state,
                release_package_id=release_package_id,
                verification_run_id=verification_run_id,
                candidate_commit_sha=candidate_commit_sha,
                command_index=command_index,
                command_result=normalized_result,
            )
        )


def _mark_verification_started(
    state: TeamState,
    *,
    release_package_id: str,
    verification_run_id: str,
    candidate_commit_sha: str,
) -> TeamState:
    mark_release_verification_started(
        state,
        release_package_id=release_package_id,
        verification_run_id=verification_run_id,
        candidate_commit_sha=candidate_commit_sha,
    )
    return state


def _record_verification_command_progress(
    state: TeamState,
    *,
    release_package_id: str,
    verification_run_id: str,
    candidate_commit_sha: str,
    command_index: int,
    command_result: ReleaseVerificationCommandResult,
) -> TeamState:
    record_release_verification_command_progress(
        state,
        release_package_id=release_package_id,
        verification_run_id=verification_run_id,
        candidate_commit_sha=candidate_commit_sha,
        command_index=command_index,
        command_result=command_result,
    )
    return state


__all__ = ["TeamReleaseGateService"]
