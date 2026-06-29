from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from .types import (
    TeamCommandApplyResult,
    TeamCommandContext,
    TeamCommandValidationError,
    _ResolvedTeamCommandContext,
)
from .validators import (
    _require_command_agent,
    _require_command_agent_by_id,
    _team_command_validation_error_from_exception,
)
from ..domain_helpers import (
    AgentStatus,
    TeamState,
    _apply_superior_stand_down,
    _apply_superior_stand_down_revocation,
    _optional_text,
    _required_text,
    _unique_text_list,
)

from .executor import TeamCommandExecutor


class TeamAgentControlCommandService(TeamCommandExecutor):
    def stand_down_agent_command(
        self,
        *,
        session_id: str,
        agent_id: str,
        reason: str,
        request_id: str,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_agent_id = _required_text(agent_id, "agent_id")
        normalized_reason = _optional_text(reason) or ""
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_stand_down_agent",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="progress",
        )
        normalized_payload = {
            "agent_id": normalized_agent_id,
            "reason": normalized_reason,
            "handled_inbox_item_ids": sorted(normalized_handled_ids),
            "correlation_id": context.correlation_id,
            "causation_id": context.causation_id,
        }

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_stand_down_agent")
            target = _require_command_agent_by_id(
                state,
                normalized_agent_id,
                field_name="agent_id",
            )
            if target.status != AgentStatus.ACTIVE:
                raise TeamCommandValidationError(
                    code="stand_down_target_not_active",
                    message=f"agent {target.id} is {target.status.value}",
                    correction_context={
                        "agent_id": target.id,
                        "status": target.status.value,
                    },
                )
            try:
                stand_down_id = _apply_superior_stand_down(
                    state,
                    actor=agent,
                    request={"agent_id": normalized_agent_id, "reason": normalized_reason},
                )
            except ValueError as exc:
                raise _team_command_validation_error_from_exception(
                    exc,
                    code="stand_down_failed",
                    correction_context={
                        "caller_agent_id": agent.id,
                        "agent_id": normalized_agent_id,
                    },
                ) from exc
            return TeamCommandApplyResult(
                response_payload={
                    "stand_down_id": stand_down_id,
                    "created_stand_down_ids": [stand_down_id],
                    "progress_made": True,
                },
                created_domain_ids=[stand_down_id],
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def revoke_stand_down_command(
        self,
        *,
        session_id: str,
        agent_id: str,
        request_id: str | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _optional_text(request_id)
        normalized_agent_id = _required_text(agent_id, "agent_id")
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_revoke_stand_down",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="progress",
        )
        normalized_payload = {
            "agent_id": normalized_agent_id,
            "handled_inbox_item_ids": sorted(normalized_handled_ids),
            "correlation_id": context.correlation_id,
            "causation_id": context.causation_id,
        }

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_revoke_stand_down")
            try:
                revoked_ids = _apply_superior_stand_down_revocation(
                    state,
                    actor=agent,
                    request={"agent_id": normalized_agent_id},
                )
            except ValueError as exc:
                raise _team_command_validation_error_from_exception(
                    exc,
                    code="stand_down_revocation_failed",
                    correction_context={
                        "caller_agent_id": agent.id,
                        "agent_id": normalized_agent_id,
                    },
                ) from exc
            return TeamCommandApplyResult(
                response_payload={
                    "revoked_stand_down_ids": revoked_ids,
                    "progress_made": bool(revoked_ids),
                },
                created_domain_ids=list(revoked_ids),
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )


__all__ = ["TeamAgentControlCommandService"]
