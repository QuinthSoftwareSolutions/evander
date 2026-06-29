from __future__ import annotations

from collections.abc import (
    Mapping,
    Sequence,
)
from typing import Any

from agent_sandbox.models import utc_now

from .deliverable_helpers import (
    _apply_command_deliverable_submission,
    _command_deliverable_id,
    _command_deliverable_task_for_submission,
    _normalize_deliverable_submission_mapping,
    _normalize_review_request_mapping,
    _plan_command_review_requests_for_deliverable,
    _publish_command_deliverable_artifacts,
    _team_submit_deliverable_for_review_normalized_payload,
    _team_submit_deliverable_normalized_payload,
    _validate_command_deliverable_submission_pre_publication,
)
from .executor import TeamCommandExecutor
from .types import (
    TeamCommandApplyResult,
    TeamCommandContext,
    TeamCommandValidationError,
    TeamDeliverableArtifactPublisher,
    _ResolvedTeamCommandContext,
)
from .validators import _require_command_agent
from ...models import (
    TeamState,
    _unique_text_list,
)
from ..domain.common import (
    _optional_text,
    _required_text,
)
from ..domain.deliverables import (
    _deliverable_submission_next_action_hints,
)
from ..domain.review import (
    _apply_review_request,
    _review_request_was_created,
)
from ..domain.work_due import (
    _mark_agent_work_due,
)


class TeamDeliverableSubmissionCommandService(TeamCommandExecutor):
    def submit_deliverable_command(
        self,
        *,
        session_id: str,
        task_id: str,
        summary: str,
        request_id: str,
        id: str | None = None,
        supersedes_deliverable_id: str | None = None,
        artifact_paths: Sequence[str] | None = None,
        artifact_refs: Sequence[str] | None = None,
        artifact_space_ref: str | None = None,
        git_refs: Mapping[str, Any] | None = None,
        verification: Mapping[str, Any] | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        artifact_publisher: TeamDeliverableArtifactPublisher | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_deliverable = _normalize_deliverable_submission_mapping(
            {
                "id": id,
                "task_id": task_id,
                "summary": summary,
                "supersedes_deliverable_id": supersedes_deliverable_id,
                "artifact_paths": list(artifact_paths or []),
                "artifact_refs": list(artifact_refs or []),
                "artifact_space_ref": artifact_space_ref,
                "git_refs": dict(git_refs or {}),
                "verification": dict(verification) if verification is not None else None,
            },
            field_name="deliverable",
        )
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_submit_deliverable",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="deliverable",
        )
        normalized_payload = _team_submit_deliverable_normalized_payload(
            deliverable=normalized_deliverable,
            handled_inbox_item_ids=normalized_handled_ids,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_submit_deliverable")
            task = _command_deliverable_task_for_submission(
                state,
                agent=agent,
                task_id=normalized_deliverable["task_id"],
            )
            deliverable_id = _command_deliverable_id(
                state,
                deliverable=normalized_deliverable,
                request_id=normalized_request_id,
            )
            _validate_command_deliverable_submission_pre_publication(
                state,
                agent=agent,
                task=task,
                deliverable=normalized_deliverable,
                deliverable_id=deliverable_id,
            )
            published_records = _publish_command_deliverable_artifacts(
                state,
                agent=agent,
                task=task,
                deliverable_id=deliverable_id,
                dispatch_id=resolved.dispatch_id or "manual",
                artifact_paths=normalized_deliverable["artifact_paths"],
                artifact_publisher=artifact_publisher,
            )
            created_inbox_ids = _apply_command_deliverable_submission(
                state,
                agent=agent,
                task=task,
                deliverable=normalized_deliverable,
                deliverable_id=deliverable_id,
                published_records=published_records,
                suppress_superior_notice=False,
            )
            published_refs = [
                _required_text(record.get("artifact_ref"), "artifact_ref")
                for record in published_records
            ]
            return TeamCommandApplyResult(
                response_payload={
                    "deliverable_id": deliverable_id,
                    "created_deliverable_ids": [deliverable_id],
                    "created_inbox_item_ids": created_inbox_ids,
                    "published_artifact_refs": published_refs,
                    "published_deliverable_artifacts": [
                        dict(record) for record in published_records
                    ],
                    "task_id": task.id,
                    "task_state": state.tasks[task.id].state.value,
                    "intent_recorded": True,
                    "async_effect_pending": bool(created_inbox_ids),
                    "verification": normalized_deliverable.get("verification"),
                    "next_action_hints": _deliverable_submission_next_action_hints(
                        deliverable_id=deliverable_id,
                        deliverable=normalized_deliverable,
                        created_inbox_ids=created_inbox_ids,
                        created_review_request_ids=[],
                    ),
                    "progress_made": True,
                },
                created_domain_ids=[deliverable_id, *created_inbox_ids],
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def submit_deliverable_for_review_command(
        self,
        *,
        session_id: str,
        deliverable: Mapping[str, Any],
        reviews: Sequence[Mapping[str, Any]],
        request_id: str,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        artifact_publisher: TeamDeliverableArtifactPublisher | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_deliverable = _normalize_deliverable_submission_mapping(
            deliverable,
            field_name="deliverable",
        )
        normalized_reviews = [
            _normalize_review_request_mapping(item, field_name=f"reviews[{index}]")
            for index, item in enumerate(reviews)
        ]
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_submit_deliverable_for_review",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="deliverable_review_request",
        )
        normalized_payload = _team_submit_deliverable_for_review_normalized_payload(
            deliverable=normalized_deliverable,
            reviews=normalized_reviews,
            handled_inbox_item_ids=normalized_handled_ids,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(
                resolved,
                "team_submit_deliverable_for_review",
            )
            task = _command_deliverable_task_for_submission(
                state,
                agent=agent,
                task_id=normalized_deliverable["task_id"],
            )
            deliverable_id = _command_deliverable_id(
                state,
                deliverable=normalized_deliverable,
                request_id=normalized_request_id,
            )
            _validate_command_deliverable_submission_pre_publication(
                state,
                agent=agent,
                task=task,
                deliverable=normalized_deliverable,
                deliverable_id=deliverable_id,
            )
            if not normalized_reviews:
                raise TeamCommandValidationError(
                    code="empty_reviews",
                    message="reviews must contain at least one review request",
                    correction_context={"field": "reviews"},
                )
            planned_reviews = _plan_command_review_requests_for_deliverable(
                state,
                actor=agent,
                deliverable_id=deliverable_id,
                task=task,
                reviews=normalized_reviews,
                request_id=normalized_request_id,
            )
            published_records = _publish_command_deliverable_artifacts(
                state,
                agent=agent,
                task=task,
                deliverable_id=deliverable_id,
                dispatch_id=resolved.dispatch_id or "manual",
                artifact_paths=normalized_deliverable["artifact_paths"],
                artifact_publisher=artifact_publisher,
            )
            _apply_command_deliverable_submission(
                state,
                agent=agent,
                task=task,
                deliverable=normalized_deliverable,
                deliverable_id=deliverable_id,
                published_records=published_records,
                suppress_superior_notice=True,
            )
            review_result = {"errors": [], "created_inbox_item_ids": []}
            created_review_ids: list[str] = []
            for index, request_payload in enumerate(planned_reviews):
                created_id = _apply_review_request(
                    state,
                    actor=agent,
                    request=request_payload,
                    result=review_result,
                )
                if created_id is None:
                    raise TeamCommandValidationError(
                        code="review_request_failed",
                        message="; ".join(review_result["errors"])
                        or f"reviews[{index}] could not be created",
                        correction_context={
                            "deliverable_id": deliverable_id,
                            "review": dict(request_payload),
                            "errors": list(review_result["errors"]),
                        },
                    )
                if _review_request_was_created(review_result, created_id):
                    created_review_ids.append(created_id)
            for review_id in created_review_ids:
                review = state.review_requests[review_id]
                _mark_agent_work_due(
                    state,
                    agent_id=review.reviewer_agent_id,
                    now=utc_now(),
                )
            published_refs = [
                _required_text(record.get("artifact_ref"), "artifact_ref")
                for record in published_records
            ]
            created_inbox_ids = list(review_result["created_inbox_item_ids"])
            return TeamCommandApplyResult(
                response_payload={
                    "deliverable_id": deliverable_id,
                    "created_deliverable_ids": [deliverable_id],
                    "created_review_request_ids": created_review_ids,
                    "existing_review_request_ids": list(
                        review_result.get("existing_review_request_ids", [])
                    ),
                    "created_inbox_item_ids": created_inbox_ids,
                    "published_artifact_refs": published_refs,
                    "published_deliverable_artifacts": [
                        dict(record) for record in published_records
                    ],
                    "task_id": task.id,
                    "task_state": state.tasks[task.id].state.value,
                    "intent_recorded": True,
                    "async_effect_pending": bool(created_inbox_ids),
                    "verification": normalized_deliverable.get("verification"),
                    "next_action_hints": _deliverable_submission_next_action_hints(
                        deliverable_id=deliverable_id,
                        deliverable=normalized_deliverable,
                        created_inbox_ids=created_inbox_ids,
                        created_review_request_ids=created_review_ids,
                    ),
                    "progress_made": True,
                },
                created_domain_ids=[
                    deliverable_id,
                    *created_review_ids,
                    *created_inbox_ids,
                ],
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )


__all__ = ["TeamDeliverableSubmissionCommandService"]
