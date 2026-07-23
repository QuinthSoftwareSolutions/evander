from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import uuid4
from .communication_helpers import _team_record_note_normalized_payload
from .types import (
    TeamCommandApplyResult,
    TeamCommandContext,
    TeamCommandValidationError,
    _ResolvedTeamCommandContext,
)
from ...models import (
    AgentActionRecord,
    TeamInboxItemState,
    TeamState,
    _unique_text_list,
)
from ..domain.common import (
    _optional_text,
    _required_text,
)

from .executor import TeamCommandExecutor


class TeamNoteCommandService(TeamCommandExecutor):
    def record_team_note_command(
        self,
        *,
        session_id: str,
        body: str,
        request_id: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_body = _optional_text(body) or ""
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_record_note",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="context",
        )
        normalized_payload = _team_record_note_normalized_payload(
            body=normalized_body,
            handled_inbox_item_ids=normalized_handled_ids,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = resolved.agent
            if agent is None or resolved.dispatch_id is None:
                raise TeamCommandValidationError(
                    code="invalid_command_scope",
                    message="team_record_note requires an active agent dispatch",
                )
            body_text = _optional_text(normalized_body)
            if body_text is None:
                raise TeamCommandValidationError(
                    code="empty_body",
                    message="body must be a non-empty string",
                    correction_context={"field": "body"},
                )
            action_record_id = f"action-{uuid4().hex}"
            action_payload: dict[str, Any] = {
                "tool_name": context.tool_name,
                "body": body_text,
                "handled_inbox_item_ids": list(normalized_handled_ids),
            }
            if context.correlation_id is not None:
                action_payload["correlation_id"] = context.correlation_id
            if context.causation_id is not None:
                action_payload["causation_id"] = context.causation_id
            state.action_records[action_record_id] = AgentActionRecord(
                id=action_record_id,
                agent_id=agent.id,
                dispatch_id=resolved.dispatch_id,
                action=action_payload,
                success=True,
            )
            return TeamCommandApplyResult(
                response_payload={
                    "note_id": action_record_id,
                    "action_record_id": action_record_id,
                    "progress_made": False,
                },
                created_domain_ids=[action_record_id],
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def mark_inbox_handled_command(
        self,
        *,
        session_id: str,
        inbox_item_ids: Sequence[str],
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_inbox_ids = _unique_text_list(inbox_item_ids, "inbox_item_ids")
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_mark_inbox_handled",
            request_id=None,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="inbox_handled",
        )
        normalized_payload = {
            "inbox_item_ids": sorted(normalized_inbox_ids),
        }

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            if resolved.agent is None or resolved.dispatch_id is None:
                raise TeamCommandValidationError(
                    code="invalid_command_scope",
                    message="team_mark_inbox_handled requires an active agent dispatch",
                )
            if not normalized_inbox_ids:
                raise TeamCommandValidationError(
                    code="empty_inbox_item_ids",
                    message="inbox_item_ids must contain at least one inbox item id",
                    correction_context={"field": "inbox_item_ids"},
                )
            pending_ids = [
                inbox_item_id
                for inbox_item_id in normalized_inbox_ids
                if state.inbox[inbox_item_id].state == TeamInboxItemState.PENDING
            ]
            return TeamCommandApplyResult(
                response_payload={
                    "handled_inbox_item_ids": pending_ids,
                    "progress_made": bool(pending_ids),
                },
                created_domain_ids=pending_ids,
                applied=bool(pending_ids),
                state_changed=bool(pending_ids),
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_inbox_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )


__all__ = ["TeamNoteCommandService"]
