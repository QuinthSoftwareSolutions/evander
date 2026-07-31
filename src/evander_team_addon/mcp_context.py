from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from mcp.server.fastmcp.exceptions import ToolError

from evander.addons import AddonContext

from .models import (
    AgentRuntime,
    AgentStatus,
    EphemeralAssignment,
    GovernanceGroup,
    GovernanceRuntime,
    PersistentAgent,
    TeamState,
)
from .repository import TeamStateRepository


@dataclass(frozen=True)
class TeamMcpCallerContext:
    session_id: str
    state: TeamState
    agent: PersistentAgent | None
    agent_runtime: AgentRuntime | None
    governance_groups: tuple[GovernanceGroup, ...]
    governance_runtimes: tuple[GovernanceRuntime, ...]
    ephemeral_assignments: tuple[EphemeralAssignment, ...]

    @property
    def agent_id(self) -> str | None:
        return self.agent.id if self.agent is not None else None

    @property
    def active_dispatch_id(self) -> str | None:
        return self.agent_runtime.active_dispatch_id if self.agent_runtime is not None else None

    @property
    def wake_reason(self) -> str | None:
        return self.agent_runtime.wake_reason if self.agent_runtime is not None else None

    @property
    def governance_group_ids(self) -> tuple[str, ...]:
        return tuple(group.id for group in self.governance_groups)

    @property
    def active_governance_group_ids(self) -> tuple[str, ...]:
        return tuple(
            runtime.governance_group_id
            for runtime in self.governance_runtimes
            if runtime.active_dispatch_id is not None
        )

    @property
    def assignment_ids(self) -> tuple[str, ...]:
        return tuple(assignment.id for assignment in self.ephemeral_assignments)

    def correction_context(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "team_id": self.agent.team_id if self.agent is not None else None,
            "agent_status": self.agent.status.value if self.agent is not None else None,
            "active_dispatch_id": self.active_dispatch_id,
            "wake_reason": self.wake_reason,
            "governance_group_ids": list(self.governance_group_ids),
            "active_governance_group_ids": list(self.active_governance_group_ids),
            "assignment_ids": list(self.assignment_ids),
        }


class TeamMcpContextResolver:
    def __init__(self, *, repo: TeamStateRepository, context: AddonContext) -> None:
        self._repo = repo
        self._context = context

    def resolve(self, *, session_id: str | None, tool_name: str) -> TeamMcpCallerContext:
        resolved_session_id = self._require_tool(session_id, tool_name)
        state = self._repo.read()
        return resolve_team_mcp_caller(state, resolved_session_id)

    def require_persistent_action_context(
        self,
        *,
        session_id: str | None,
        tool_name: str,
    ) -> TeamMcpCallerContext:
        caller = self.resolve(session_id=session_id, tool_name=tool_name)
        if caller.agent is None:
            raise ToolError(f"{tool_name} requires a persistent Team agent session")
        if caller.agent.status != AgentStatus.ACTIVE:
            raise ToolError(f"{tool_name} requires an active Team agent")
        if caller.agent.team_id is None:
            raise ToolError(f"{tool_name} requires a Team-assigned persistent agent")
        if caller.active_dispatch_id is None:
            raise ToolError(f"{tool_name} requires an active Team dispatch lease")
        return caller

    def require_context_handoff_context(
        self,
        *,
        session_id: str | None,
        tool_name: str,
    ) -> TeamMcpCallerContext:
        caller = self.resolve(session_id=session_id, tool_name=tool_name)
        if caller.agent is None:
            raise ToolError(f"{tool_name} requires a persistent Team agent session")
        if caller.agent.status != AgentStatus.ACTIVE:
            raise ToolError(f"{tool_name} requires an active Team agent")
        if caller.active_dispatch_id is None:
            raise ToolError(f"{tool_name} requires an active Team dispatch lease")
        dispatch = caller.state.dispatches.get(caller.active_dispatch_id or "")
        if dispatch is None or dispatch.cause_kind != "context_handoff":
            raise ToolError(f"{tool_name} requires an active context handoff dispatch")
        return caller

    def require_persistent_read_context(
        self,
        *,
        session_id: str | None,
        tool_name: str,
    ) -> TeamMcpCallerContext:
        caller = self.resolve(session_id=session_id, tool_name=tool_name)
        if caller.agent is None:
            raise ToolError(f"{tool_name} requires a persistent Team agent session")
        if caller.agent.status != AgentStatus.ACTIVE:
            raise ToolError(f"{tool_name} requires an active Team agent")
        if caller.agent.team_id is None:
            raise ToolError(f"{tool_name} requires a Team-assigned persistent agent")
        return caller

    def require_chat_reply_context(
        self,
        *,
        session_id: str | None,
        tool_name: str,
    ) -> TeamMcpCallerContext:
        caller = self.resolve(session_id=session_id, tool_name=tool_name)
        if caller.agent is None:
            raise ToolError(f"{tool_name} requires a persistent Team agent session")
        if caller.agent.status != AgentStatus.ACTIVE:
            raise ToolError(f"{tool_name} requires an active Team agent")
        if caller.active_dispatch_id is None:
            raise ToolError(f"{tool_name} requires an active Team dispatch lease")
        if caller.wake_reason != "team_chat":
            raise ToolError(f"{tool_name} requires an active Team Chat dispatch")
        return caller

    def require_chat_attachment_context(
        self,
        *,
        session_id: str | None,
        tool_name: str,
    ) -> TeamMcpCallerContext:
        caller = self.resolve(session_id=session_id, tool_name=tool_name)
        if caller.agent is None:
            raise ToolError(f"{tool_name} requires a persistent Team agent session")
        if caller.agent.status != AgentStatus.ACTIVE:
            raise ToolError(f"{tool_name} requires an active Team agent")
        if caller.active_dispatch_id is None:
            raise ToolError(f"{tool_name} requires an active Team dispatch lease")
        if caller.agent.team_id is None and caller.wake_reason != "team_chat":
            raise ToolError(
                f"{tool_name} requires a Team-assigned agent or an active Team Chat dispatch"
            )
        return caller

    def require_governance_context(
        self,
        *,
        session_id: str | None,
        tool_name: str,
    ) -> TeamMcpCallerContext:
        caller = self.resolve(session_id=session_id, tool_name=tool_name)
        if caller.agent is None:
            raise ToolError(f"{tool_name} requires a governance manager Team agent session")
        if caller.agent.status != AgentStatus.ACTIVE or not caller.governance_groups:
            raise ToolError(f"{tool_name} requires an active governance manager session")
        return caller

    def require_ephemeral_assignment_context(
        self,
        *,
        session_id: str | None,
        tool_name: str,
        assignment_id: str | None = None,
    ) -> TeamMcpCallerContext:
        caller = self.resolve(session_id=session_id, tool_name=tool_name)
        if not caller.ephemeral_assignments:
            raise ToolError(f"{tool_name} requires an ephemeral worker assignment session")
        normalized_assignment_id = str(assignment_id or "").strip()
        if normalized_assignment_id and normalized_assignment_id not in caller.assignment_ids:
            raise ToolError(
                "assignment_id is bound by the MCP connection; omit it or use the "
                "connected assignment"
            )
        return caller

    def _require_tool(self, session_id: str | None, tool_name: str) -> str:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            raise ToolError("session_id is required")
        runtime = self._context.service
        if runtime is None or not hasattr(runtime, "policy"):
            raise ToolError("Team MCP tools require a runtime policy context")
        runtime.policy.require_tool(normalized_session_id, tool_name)
        return normalized_session_id


def resolve_team_mcp_caller(state: TeamState, session_id: str) -> TeamMcpCallerContext:
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        raise ToolError("session_id is required")
    agent = _first(
        agent for agent in state.agents.values() if agent.session_id == normalized_session_id
    )
    agent_runtime = state.agent_runtimes.get(agent.id) if agent is not None else None
    governance_groups = tuple(
        group
        for group in state.governance_groups.values()
        if agent is not None and group.manager_agent_id == agent.id and not group.archived
    )
    governance_group_ids = {group.id for group in governance_groups}
    governance_runtimes = tuple(
        runtime
        for runtime in state.governance_runtimes.values()
        if runtime.governance_group_id in governance_group_ids
    )
    ephemeral_assignments = tuple(
        assignment
        for assignment in state.ephemeral_assignments.values()
        if assignment.session_id == normalized_session_id
    )
    return TeamMcpCallerContext(
        session_id=normalized_session_id,
        state=state,
        agent=agent,
        agent_runtime=agent_runtime,
        governance_groups=governance_groups,
        governance_runtimes=governance_runtimes,
        ephemeral_assignments=ephemeral_assignments,
    )


def team_mcp_success(
    *,
    revision: int,
    progress_made: bool,
    agent_id: str | None = None,
    governance_group_id: str | None = None,
    assignment_id: str | None = None,
    action_record_id: str | None = None,
    activity_id: str | None = None,
    **payload: Any,
) -> dict[str, Any]:
    result = _base_result(
        success=True,
        applied=True,
        progress_made=progress_made,
        revision=revision,
        agent_id=agent_id,
        governance_group_id=governance_group_id,
        assignment_id=assignment_id,
        action_record_id=action_record_id,
        activity_id=activity_id,
        errors=[],
    )
    result.update(payload)
    result.setdefault("error", None)
    return result


def team_mcp_failure(
    *,
    revision: int | None,
    errors: str | Iterable[str],
    agent_id: str | None = None,
    governance_group_id: str | None = None,
    assignment_id: str | None = None,
    action_record_id: str | None = None,
    activity_id: str | None = None,
    correction_context: dict[str, Any] | None = None,
    **payload: Any,
) -> dict[str, Any]:
    normalized_errors = _normalize_errors(errors)
    result = _base_result(
        success=False,
        applied=False,
        progress_made=False,
        revision=revision,
        agent_id=agent_id,
        governance_group_id=governance_group_id,
        assignment_id=assignment_id,
        action_record_id=action_record_id,
        activity_id=activity_id,
        errors=normalized_errors,
    )
    result.update(payload)
    result["error"] = normalized_errors[0] if normalized_errors else None
    result["correction_context"] = correction_context or {}
    return result


def _base_result(
    *,
    success: bool,
    applied: bool,
    progress_made: bool,
    revision: int | None,
    agent_id: str | None,
    governance_group_id: str | None,
    assignment_id: str | None,
    action_record_id: str | None,
    activity_id: str | None,
    errors: list[str],
) -> dict[str, Any]:
    return {
        "success": success,
        "applied": applied,
        "progress_made": progress_made,
        "revision": revision,
        "agent_id": agent_id,
        "governance_group_id": governance_group_id,
        "assignment_id": assignment_id,
        "action_record_id": action_record_id,
        "activity_id": activity_id,
        "errors": errors,
    }


def _normalize_errors(errors: str | Iterable[str]) -> list[str]:
    if isinstance(errors, str):
        return [errors]
    return [str(error) for error in errors if str(error).strip()]


def _first(items: Iterable[Any]) -> Any | None:
    for item in items:
        return item
    return None
