from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...models import (
    AgentActionRecord,
    AgentRuntime,
    TeamInboxPriority,
    TeamState,
)

from .chat import _append_pending_chat_message_queued_event_for_agent
from .common import utc_now
from .context_rotation import _maybe_mark_idle_context_rotation_due


def _append_action_item_error(
    result: dict[str, Any],
    field_name: str,
    index: int,
    error: ValueError,
) -> None:
    result["errors"].append(f"{field_name}[{index}]: {error}")


def _response_requested_from_item(
    item: Mapping[str, Any],
    *,
    default: bool,
) -> bool:
    if "response_requested" not in item:
        return default
    value = item.get("response_requested")
    if isinstance(value, bool):
        return value
    raise ValueError("response_requested must be a boolean")


def _priority_from_item(
    item: Mapping[str, Any],
    *,
    default: TeamInboxPriority,
) -> TeamInboxPriority:
    if "priority" not in item:
        return default
    try:
        return TeamInboxPriority(str(item.get("priority") or "").strip())
    except ValueError:
        allowed = ", ".join(priority.value for priority in TeamInboxPriority)
        raise ValueError(f"priority must be one of: {allowed}") from None


def _git_release_sequence_error(action: Mapping[str, Any]) -> str | None:
    feature_updates = action.get("git_feature_updates")
    release_requests = action.get("git_release_requests")
    if (
        isinstance(feature_updates, list)
        and feature_updates
        and isinstance(release_requests, list)
        and release_requests
    ):
        return (
            "cannot combine git_feature_updates and git_release_requests in one "
            "Team action; submit git_release_requests alone when the intent is to "
            "release accepted deliverables. The release request will enqueue "
            "prerequisite feature promotions from accepted deliverable review or "
            "agent branches before continuing."
        )
    return None


def _mark_action_failure(
    state: TeamState,
    *,
    runtime: AgentRuntime,
    agent_id: str,
    dispatch_id: str,
    action: dict[str, Any],
    error: str,
    action_id: str,
    result: dict[str, Any],
) -> None:
    runtime.active_dispatch_id = None
    runtime.wake_leased_until = None
    runtime.wake_reason = None
    runtime.no_progress_count += 1
    runtime.updated_at = utc_now()
    _maybe_mark_idle_context_rotation_due(state, agent_id=agent_id, now=runtime.updated_at)
    state.action_records[action_id] = AgentActionRecord(
        id=action_id,
        agent_id=agent_id,
        dispatch_id=dispatch_id,
        action=action,
        success=False,
        error=error,
    )
    result["errors"].append(error)
    agent = state.agents.get(agent_id)
    if agent is not None:
        _append_pending_chat_message_queued_event_for_agent(
            state,
            agent=agent,
            completed_dispatch_id=dispatch_id,
            result=result,
        )


__all__ = [
    "_append_action_item_error",
    "_response_requested_from_item",
    "_priority_from_item",
    "_git_release_sequence_error",
    "_mark_action_failure",
]
