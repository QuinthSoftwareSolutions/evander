from __future__ import annotations

from typing import Any

from ..models import ReleaseVerificationCommandResult, TeamState
from ..repository import TeamStateRepository
from .domain.release_gates import record_release_verification_result


class TeamReleaseGateService:
    def __init__(self, repository: TeamStateRepository) -> None:
        self._repository = repository

    def record_verification_result(
        self,
        *,
        release_package_id: str,
        verification_run_id: str,
        candidate_commit_sha: str,
        verification_state: str,
        command_results: list[dict[str, Any]],
        error: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_results = [
            ReleaseVerificationCommandResult.model_validate(item)
            for item in command_results
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

        def _mutate(state: TeamState) -> TeamState:
            record_release_verification_result(
                state,
                release_package_id=release_package_id,
                verification_run_id=verification_run_id,
                candidate_commit_sha=candidate_commit_sha,
                verification_state=verification_state,
                command_results=normalized_results,
                error=error,
                result=result,
            )
            result["success"] = True
            return state

        state = self._repository.update(_mutate)
        result["revision"] = state.revision
        return state, result


__all__ = ["TeamReleaseGateService"]
