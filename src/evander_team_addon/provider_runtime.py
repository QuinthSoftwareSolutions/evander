"""Provider runtime."""

from __future__ import annotations

from collections.abc import (
    Mapping,
)
from datetime import (
    UTC,
    datetime,
)
from typing import (
    Any,
)

from evander.model_usage.runtime import (
    register_model_usage_correlation_context,
)
from evander.models import (
    tool_name_value,
)
from evander.repositories.mcp_transport_bindings import (
    JsonMcpTransportBindingRepository,
)

from .costs import (
    TeamCostService,
)
from .mcp import (
    GOVERNANCE_TEAM_MCP_TOOL_NAMES,
    MCP_TOOL_NAMES,
    ORDINARY_TEAM_MCP_TOOL_NAMES,
    READ_TEAM_MCP_TOOL_NAMES,
    TEAM_ACKNOWLEDGE_OPERATOR_CHAT_MESSAGES_TOOL,
    TEAM_LIST_CONVERSATION_MESSAGES_TOOL,
    TEAM_REPLY_TO_CHAT_TOOL,
    TEAM_SEND_OPERATOR_CHAT_MESSAGE_TOOL,
    TEAM_STAGE_CHAT_ATTACHMENT_TOOL,
)
from .models import (
    TeamConversationMessage,
    TeamDispatch,
    TeamState,
)
from .provider_constants import (
    _TEAM_CODE_INDEX_TOOL_NAMES,
)
from .provider_projection import (
    _string_items,
)
from .provider_values import (
    _number_or_none,
    _number_or_zero,
    _optional_text,
    _required_text,
    _runtime_dir,
    _team_datetime_utc,
)
from .repository import (
    TeamStateRepository,
)
from .services.chat_service import (
    team_chat_mutation_allowed,
    team_chat_mutation_scope_lost,
)


def _cost_guard_blocked_teams(
    context: Any,
    *,
    state: Any,
    team_id: str | None,
    project_id: str | None,
    agent_id: str | None,
) -> list[dict[str, Any]]:
    candidate_team_ids = _cost_guard_candidate_team_ids(
        state,
        team_id=team_id,
        project_id=project_id,
        agent_id=agent_id,
    )
    limited_team_ids = {
        candidate_team_id
        for candidate_team_id in candidate_team_ids
        if _number_or_none(getattr(state.teams.get(candidate_team_id), "max_cost_usd", None))
        is not None
    }
    if not limited_team_ids:
        return []

    service = TeamCostService(
        runtime_dir=_runtime_dir(context),
        state=state,
        session_repository=_session_repository_from_context(context),
    )
    cost_payload = service.current_budget_costs_payload(team_ids=limited_team_ids)
    team_costs = cost_payload.get("team_costs")
    team_costs = team_costs if isinstance(team_costs, Mapping) else {}

    blocked: list[dict[str, Any]] = []
    for blocked_team_id in sorted(limited_team_ids):
        team = state.teams.get(blocked_team_id)
        if team is None:
            continue
        cost = team_costs.get(blocked_team_id)
        cost = cost if isinstance(cost, Mapping) else {}
        max_cost = _number_or_none(cost.get("max_cost_usd"))
        if max_cost is None:
            max_cost = _number_or_none(getattr(team, "max_cost_usd", None))
        if max_cost is None:
            continue
        lifetime_estimated_cost = _number_or_zero(cost.get("estimated_cost"))
        budget_window_estimated_cost = _number_or_none(cost.get("budget_window_estimated_cost"))
        estimated_cost = (
            budget_window_estimated_cost
            if budget_window_estimated_cost is not None
            else lifetime_estimated_cost
        )
        limit_reached = bool(cost.get("cost_limit_reached"))
        if not limit_reached:
            limit_reached = estimated_cost >= max_cost
        if not limit_reached:
            continue
        blocked.append(
            {
                "team_id": blocked_team_id,
                "team_name": getattr(team, "name", blocked_team_id),
                "estimated_cost": estimated_cost,
                "lifetime_estimated_cost": lifetime_estimated_cost,
                "max_cost_usd": max_cost,
                "remaining_cost_usd": _number_or_none(cost.get("remaining_cost_usd")),
                "cost_limit_basis": cost.get("cost_limit_basis") or "lifetime",
                "coverage_status": cost.get("coverage_status"),
                "coverage_reasons": list(cost.get("coverage_reasons") or []),
            }
        )
    return blocked


def _cost_guard_candidate_team_ids(
    state: Any,
    *,
    team_id: str | None,
    project_id: str | None,
    agent_id: str | None,
) -> set[str]:
    candidate_team_ids = set(state.teams)
    if team_id is not None:
        candidate_team_ids &= {team_id}
    if project_id is not None:
        project = state.projects.get(project_id)
        candidate_team_ids &= {project.team_id} if project is not None else set()
    if agent_id is not None:
        agent = state.agents.get(agent_id)
        candidate_team_ids &= {agent.team_id} if agent is not None else set()
    return candidate_team_ids


def _session_repository_from_context(context: Any) -> Any | None:
    repository = getattr(context, "session_lookup", None)
    if repository is not None:
        return repository
    repository = getattr(context, "session_repository", None)
    if repository is not None:
        return repository
    runtime = getattr(context, "runtime", None)
    repository = getattr(runtime, "session_repository", None)
    if repository is not None:
        return repository
    service = getattr(context, "admin_service", None)
    service_runtime = getattr(service, "runtime", None)
    return getattr(service_runtime, "session_repository", None)


def _session_records_by_id(repository: Any | None) -> dict[str, Any] | None:
    if repository is None:
        return None
    readers = (
        getattr(repository, "read_all_sessions", None),
        getattr(repository, "read_all_safe", None),
        getattr(repository, "read_all", None),
    )
    for reader in readers:
        if not callable(reader):
            continue
        try:
            records = reader()
        except Exception:
            continue
        if isinstance(records, Mapping):
            return {str(key): value for key, value in records.items()}
    list_sessions = getattr(repository, "list_sessions", None)
    if callable(list_sessions):
        try:
            sessions = list_sessions()
        except Exception:
            return None
        return {
            str(getattr(session, "session_id")): session
            for session in sessions
            if getattr(session, "session_id", None)
        }
    return None


def _prompt_session_health_agent_ids(
    state: TeamState,
    agent: Mapping[str, Any],
) -> set[str]:
    agent_id = _optional_text(agent.get("id"))
    if agent_id is None:
        return set()
    visible_ids = {agent_id}
    state_agent = state.agents.get(agent_id)

    subordinate_stack = list(_string_items(agent.get("subordinate_agent_ids")))
    if state_agent is not None:
        subordinate_stack.extend(state_agent.subordinate_agent_ids)
    while subordinate_stack:
        subordinate_id = subordinate_stack.pop()
        if subordinate_id in visible_ids:
            continue
        subordinate = state.agents.get(subordinate_id)
        if subordinate is None:
            continue
        visible_ids.add(subordinate_id)
        subordinate_stack.extend(subordinate.subordinate_agent_ids)

    visible_project_ids = set(_string_items(agent.get("lead_project_ids")))
    managed_team_ids: set[str] = set()
    for team in state.teams.values():
        if team.manager_agent_id == agent_id:
            managed_team_ids.add(team.id)
    for project in state.projects.values():
        if project.lead_agent_id == agent_id or project.team_id in managed_team_ids:
            visible_project_ids.add(project.id)
    if managed_team_ids:
        for candidate in state.agents.values():
            if candidate.team_id in managed_team_ids:
                visible_ids.add(candidate.id)
    for assignment in state.project_assignments.values():
        if assignment.project_id in visible_project_ids:
            visible_ids.add(assignment.agent_id)
    return visible_ids


def _session_repository_for_manifest_updates(context: Any) -> Any | None:
    candidates = [
        getattr(context, "session_repository", None),
        getattr(context, "session_lookup", None),
        getattr(getattr(context, "runtime", None), "session_repository", None),
        getattr(
            getattr(getattr(context, "admin_service", None), "runtime", None),
            "session_repository",
            None,
        ),
    ]
    for repository in candidates:
        if repository is None:
            continue
        if callable(getattr(repository, "get_session", None)) and callable(
            getattr(repository, "upsert_session", None)
        ):
            return repository
    return None


def _ordinary_team_tools_for_agent(agent: Mapping[str, Any]) -> list[str]:
    if _optional_text(agent.get("team_id")) is None:
        return []
    return [
        *ORDINARY_TEAM_MCP_TOOL_NAMES,
        *READ_TEAM_MCP_TOOL_NAMES,
        *_TEAM_CODE_INDEX_TOOL_NAMES,
    ]


def _team_chat_mutation_allowed(message: object, dispatch: object) -> bool:
    return (
        isinstance(message, TeamConversationMessage)
        and isinstance(dispatch, TeamDispatch)
        and team_chat_mutation_allowed(message, dispatch)
    )


def _team_chat_mutation_scope_lost(message: object, dispatch: object) -> bool:
    return (
        isinstance(message, TeamConversationMessage)
        and isinstance(dispatch, TeamDispatch)
        and team_chat_mutation_scope_lost(message, dispatch)
    )


def _team_session_has_active_prompt(context: Any, *, session_id: str) -> bool:
    activity = getattr(context, "agent_prompt_activity", None)
    has_active_prompt = getattr(activity, "has_active_enqueue_prompt", None)
    if not callable(has_active_prompt):
        return False
    try:
        return bool(
            has_active_prompt(
                target=session_id,
                vm_name=session_id,
                session_id=session_id,
            )
        )
    except Exception:
        return False


def _set_session_team_mcp_tools(
    context: Any,
    *,
    session_id: object,
    tool_names: list[str] | tuple[str, ...],
    dispatch_id: object | None = None,
) -> dict[str, Any]:
    normalized_session_id = _required_text(session_id, "session_id")
    requested_tools = _unique_requested_tools(tool_names)
    normalized_dispatch_id = _optional_text(dispatch_id)
    result: dict[str, Any] = {
        "session_id": normalized_session_id,
        "requested_tools": requested_tools,
        "already_allowed_tools": [],
        "granted_tools": [],
        "removed_tools": [],
        "error": None,
    }
    repository = _session_repository_for_manifest_updates(context)
    if repository is None:
        result["error"] = "session repository is not available"
        return result
    session = repository.get_session(normalized_session_id)
    if session is None:
        result["error"] = f"session {normalized_session_id} was not found"
        return result
    allowed_values = [tool_name_value(tool) for tool in getattr(session, "allowed_tools", [])]
    managed_tools = set(MCP_TOOL_NAMES) | set(_TEAM_CODE_INDEX_TOOL_NAMES)
    existing_team_tools = [tool for tool in allowed_values if tool in managed_tools]
    active_scope_tools = _active_team_mcp_tools_for_other_dispatches(
        context,
        session_id=normalized_session_id,
        dispatch_id=normalized_dispatch_id,
    )
    effective_team_tools = _unique_requested_tools([*requested_tools, *active_scope_tools])
    effective_team_tool_set = set(effective_team_tools)
    preserved_tools: list[str] = []
    seen_preserved: set[str] = set()
    for tool in allowed_values:
        if tool in managed_tools or tool in seen_preserved:
            continue
        preserved_tools.append(tool)
        seen_preserved.add(tool)
    next_allowed_values = preserved_tools + effective_team_tools
    already_allowed = [tool for tool in requested_tools if tool in existing_team_tools]
    granted = [tool for tool in requested_tools if tool not in existing_team_tools]
    removed = [tool for tool in existing_team_tools if tool not in effective_team_tool_set]
    if next_allowed_values != allowed_values:
        if callable(getattr(session, "model_copy", None)):
            updated_session = session.model_copy(update={"allowed_tools": next_allowed_values})
        else:
            setattr(session, "allowed_tools", next_allowed_values)
            updated_session = session
        repository.upsert_session(updated_session)
    confirmed_session = repository.get_session(normalized_session_id)
    confirmed_values = {
        tool_name_value(tool) for tool in getattr(confirmed_session, "allowed_tools", [])
    }
    missing_requested_tools = [tool for tool in requested_tools if tool not in confirmed_values]
    if missing_requested_tools:
        result["error"] = "Team MCP policy did not retain requested tool(s): " + ", ".join(
            missing_requested_tools
        )
    result["already_allowed_tools"] = already_allowed
    result["granted_tools"] = granted
    result["removed_tools"] = removed
    return result


def _review_mcp_capability_observation(
    context: Any,
    *,
    dispatch: TeamDispatch | None,
) -> dict[str, Any] | None:
    """Summarize post-lease tools/list evidence without retaining its catalogue."""

    receipt = dispatch.mcp_capability_receipt if dispatch is not None else None
    if receipt is None or not receipt.required_tool_names or receipt.grant_confirmed_at is None:
        return None
    cutoff = receipt.grant_confirmed_at
    bindings = JsonMcpTransportBindingRepository.from_runtime_dir(_runtime_dir(context))
    try:
        candidates = [
            binding
            for binding in bindings.read_all().values()
            if binding.bound_session_id == dispatch.session_id
            and binding.last_tools_list_at is not None
            and _team_datetime_utc(binding.last_tools_list_at) > _team_datetime_utc(cutoff)
        ]
    except Exception:
        candidates = []
    if not candidates:
        return None
    binding = max(
        candidates,
        key=lambda item: _team_datetime_utc(item.last_tools_list_at),
    )
    visible_tool_names = set(binding.last_visible_tool_names)
    missing_tool_names = sorted(set(receipt.required_tool_names) - visible_tool_names)
    return {
        "verification_status": "missing" if missing_tool_names else "verified",
        "observed_transport_session_id": binding.transport_session_id,
        "observed_at": binding.last_tools_list_at.isoformat(),
        "observed_tool_fingerprint": binding.last_visible_tool_fingerprint,
        "missing_tool_names": missing_tool_names,
        "diagnostic": (
            "The authenticated MCP tools/list response did not include the required review tool(s)."
            if missing_tool_names
            else None
        ),
    }


def _active_team_mcp_tools_for_other_dispatches(
    context: Any,
    *,
    session_id: str,
    dispatch_id: str | None,
) -> list[str]:
    try:
        state = TeamStateRepository(_runtime_dir(context)).read()
    except Exception:
        return []

    now = datetime.now(UTC)
    tool_names: list[str] = []
    for agent_id, runtime in sorted(state.agent_runtimes.items()):
        active_dispatch_id = _optional_text(getattr(runtime, "active_dispatch_id", None))
        if active_dispatch_id is None or active_dispatch_id == dispatch_id:
            continue
        if not _team_runtime_lease_active(runtime, now=now):
            continue
        agent = state.agents.get(agent_id)
        if agent is None or getattr(agent, "session_id", None) != session_id:
            continue
        if _optional_text(getattr(runtime, "wake_reason", None)) == "team_chat":
            tool_names.append(TEAM_REPLY_TO_CHAT_TOOL)
            tool_names.append(TEAM_LIST_CONVERSATION_MESSAGES_TOOL)
            tool_names.extend(
                [
                    TEAM_SEND_OPERATOR_CHAT_MESSAGE_TOOL,
                    TEAM_ACKNOWLEDGE_OPERATOR_CHAT_MESSAGES_TOOL,
                ]
            )
            active_dispatch = state.dispatches.get(active_dispatch_id)
            if active_dispatch is not None and active_dispatch.project_ids:
                tool_names.append(TEAM_STAGE_CHAT_ATTACHMENT_TOOL)
        else:
            tool_names.extend(
                _ordinary_team_tools_for_agent({"team_id": getattr(agent, "team_id", None)})
            )

    for group_id, runtime in sorted(state.governance_runtimes.items()):
        active_dispatch_id = _optional_text(getattr(runtime, "active_dispatch_id", None))
        if active_dispatch_id is None or active_dispatch_id == dispatch_id:
            continue
        if not _team_runtime_lease_active(runtime, now=now):
            continue
        group = state.governance_groups.get(group_id)
        if group is None:
            continue
        agent = state.agents.get(group.manager_agent_id)
        if agent is None or getattr(agent, "session_id", None) != session_id:
            continue
        tool_names.extend(GOVERNANCE_TEAM_MCP_TOOL_NAMES)

    return _unique_requested_tools(tool_names)


def _team_runtime_lease_active(runtime: Any, *, now: datetime) -> bool:
    wake_leased_until = getattr(runtime, "wake_leased_until", None)
    if wake_leased_until is None:
        return False
    if isinstance(wake_leased_until, str):
        lease_text = wake_leased_until.strip()
        if not lease_text:
            return False
        try:
            wake_leased_until = datetime.fromisoformat(lease_text.replace("Z", "+00:00"))
        except ValueError:
            return False
    if not isinstance(wake_leased_until, datetime):
        return False
    if wake_leased_until.tzinfo is None:
        wake_leased_until = wake_leased_until.replace(tzinfo=UTC)
    return wake_leased_until > now


def _unique_requested_tools(tool_names: list[str] | tuple[str, ...]) -> list[str]:
    requested: list[str] = []
    seen: set[str] = set()
    for tool in (str(tool_name or "").strip() for tool_name in tool_names):
        if not tool or tool in seen:
            continue
        seen.add(tool)
        requested.append(tool)
    return requested


def _register_team_dispatch_model_usage_context(
    context: Any,
    *,
    agent: Mapping[str, Any],
    dispatch_id: object,
    effect_id: str | None,
) -> None:
    register_model_usage_correlation_context(
        runtime_dir=_runtime_dir(context),
        session_id=_optional_text(agent.get("session_id")),
        dispatch_id=_optional_text(dispatch_id),
        workflow_run_id=_context_metadata_text(
            context,
            "workflow_run_id",
            "workflow_trace_id",
            "trace_id",
        ),
        effect_id=effect_id,
        team_id=_optional_text(agent.get("team_id")),
        project_id=(
            _string_items(agent.get("project_ids"))[0]
            if _string_items(agent.get("project_ids"))
            else None
        ),
        agent_id=_optional_text(agent.get("id")),
        trace_id=_context_metadata_text(context, "trace_id", "workflow_trace_id"),
    )


def _context_metadata_text(context: Any, *keys: str) -> str | None:
    metadata = getattr(context, "metadata", None)
    if not isinstance(metadata, Mapping):
        return None
    for key in keys:
        text = _optional_text(metadata.get(key))
        if text:
            return text
    return None
