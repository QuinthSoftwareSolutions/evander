from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import uuid4

from ..models import AgentRuntime, TeamState
from ..repository import TeamStateRepository
from .agent_action_batch import AgentActionBatch
from .commands.outbox import _team_event_outbox_records
from .domain.agent_access import _resolve_action_agent
from .domain.chat import _publish_admin_chat_notifications
from .domain.common import _optional_text
from .domain.ephemeral import _ephemeral_assignment_event_payload
from .domain.human_reviews import HUMAN_REVIEW_ELIGIBILITY_RESOLVER_RESULT_KEY


class AgentActionApplication:
    def __init__(
        self,
        repository: TeamStateRepository,
        *,
        human_review_eligibility_resolver: Callable[[Any], list[str]] | None = None,
        git_default_branch_resolver: Callable[..., object] | None = None,
        git_base_commit_resolver: Callable[..., object] | None = None,
        session_lookup: Any | None = None,
    ) -> None:
        self._repository = repository
        self._human_review_eligibility_resolver = human_review_eligibility_resolver
        self._git_default_branch_resolver = git_default_branch_resolver
        self._git_base_commit_resolver = git_base_commit_resolver
        self._session_lookup = session_lookup

    @property
    def repository(self) -> TeamStateRepository:
        return self._repository

    def apply_agent_action(
        self,
        *,
        action: dict[str, Any] | None,
        session_id: str | None = None,
        dispatch_id: str | None = None,
        valid: bool = True,
        error: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_action = dict(action or {}) if isinstance(action, dict) else {}
        normalized_session_id = _optional_text(session_id)
        normalized_dispatch_id = _optional_text(dispatch_id) or _optional_text(
            normalized_action.get("dispatch_id")
        )
        result: dict[str, Any] = {
            "success": False,
            "applied": False,
            "agent_id": None,
            "errors": [],
            "created_inbox_item_ids": [],
            "created_deliverable_ids": [],
            "created_case_ids": [],
            "updated_case_ids": [],
            "created_task_ids": [],
            "created_status_request_ids": [],
            "created_review_request_ids": [],
            "existing_review_request_ids": [],
            "decided_review_request_ids": [],
            "updated_goal_ids": [],
            "operator_escalation_event_ids": [],
            "git_promotion_event_ids": [],
            "release_recovery_event_ids": [],
            "release_package_ids": [],
            "updated_task_ids": [],
            "handled_inbox_item_ids": [],
            "created_ephemeral_assignment_ids": [],
            "ephemeral_events": [],
            "events": [],
            "case_closed_events": [],
            "accepted_ephemeral_assignment_ids": [],
            "rejected_ephemeral_assignment_ids": [],
            "created_stand_down_ids": [],
            "revoked_stand_down_ids": [],
            "created_chat_reply_message_ids": [],
            "replied_chat_message_ids": [],
            "created_operator_chat_message_ids": [],
        }
        if self._human_review_eligibility_resolver is not None:
            result[HUMAN_REVIEW_ELIGIBILITY_RESOLVER_RESULT_KEY] = (
                self._human_review_eligibility_resolver
            )

        def _mutate(state_model: TeamState) -> TeamState:
            agent = _resolve_action_agent(
                state_model,
                action=normalized_action,
                session_id=normalized_session_id,
            )
            result["agent_id"] = agent.id
            runtime = state_model.agent_runtimes.setdefault(
                agent.id,
                AgentRuntime(agent_id=agent.id),
            )
            batch = AgentActionBatch(
                state=state_model,
                agent=agent,
                runtime=runtime,
                action=normalized_action,
                dispatch_id=normalized_dispatch_id,
                action_id=f"action-{uuid4().hex}",
                result=result,
                git_default_branch_resolver=self._git_default_branch_resolver,
                git_base_commit_resolver=self._git_base_commit_resolver,
                session_lookup=self._session_lookup,
            )
            return batch.apply(valid=valid, error=error)

        state = self._repository.update_with_outbox(
            _mutate,
            outbox_records=lambda: _team_event_outbox_records(result["case_closed_events"]),
            expected_revision=expected_revision,
        )
        result.pop(HUMAN_REVIEW_ELIGIBILITY_RESOLVER_RESULT_KEY, None)
        result.pop("case_closed_events", None)
        result["revision"] = state.revision
        result["ephemeral_events"] = [
            _ephemeral_assignment_event_payload(state, assignment_id)
            for assignment_id in result["created_ephemeral_assignment_ids"]
        ]
        _publish_admin_chat_notifications(
            runtime_dir=self._repository.runtime_dir,
            state=state,
            message_ids=[
                str(message_id)
                for message_id in (
                    list(result["created_chat_reply_message_ids"])
                    + list(result["created_operator_chat_message_ids"])
                )
            ],
        )
        return state, result
