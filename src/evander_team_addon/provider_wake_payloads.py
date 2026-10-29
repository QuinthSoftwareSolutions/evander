"""Provider wake payloads."""

from __future__ import annotations

import hashlib
import json
from collections.abc import (
    Mapping,
    Sequence,
)
from datetime import (
    datetime,
)
from typing import (
    Any,
)

from .models import (
    TeamDispatchStatus,
    TeamState,
    agent_project_ids,
)
from .provider_constants import (
    _TEAM_WAKE_DEFINITION_PAYLOAD_FIELDS,
    TEAM_WAKE_OUTPUT_MAX_ERROR_CHARS,
    TEAM_WAKE_OUTPUT_MAX_ID_CHARS,
    TEAM_WAKE_OUTPUT_MAX_IDS_PER_FIELD,
)
from .provider_values import (
    _definition_payload,
    _team_datetime_utc,
)


def _team_wake_invocation_key(
    definition: Any,
    *,
    resolved_config: Mapping[str, Any] | None,
    state_revision: int,
    field_names: Sequence[str] = _TEAM_WAKE_DEFINITION_PAYLOAD_FIELDS,
    namespace: str = "agent",
) -> str:
    payload = _definition_payload(definition, *field_names)
    if resolved_config:
        payload.update(dict(resolved_config))
    payload["state_revision"] = int(state_revision)
    payload["wake_namespace"] = namespace
    serialized = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _team_wake_has_chat_claim_candidate(
    state: TeamState,
    *,
    team_id: str | None,
    project_id: str | None,
    agent_id: str | None,
    blocked_team_ids: set[str],
    now: datetime,
) -> bool:
    """Cheaply avoid a full write transaction when a wake has no chat work."""

    def _agent_is_in_scope(candidate_agent_id: str) -> bool:
        agent = state.agents.get(candidate_agent_id)
        if agent is None:
            return False
        if team_id is not None and agent.team_id != team_id:
            return False
        if agent.team_id is not None and agent.team_id in blocked_team_ids:
            return False
        if agent_id is not None and agent.id != agent_id:
            return False
        if project_id is not None and project_id not in agent_project_ids(state, agent.id):
            return False
        return True

    for message in state.conversation_messages.values():
        if (
            message.sender != "operator"
            or message.message_type != "text"
            or message.status != "queued"
        ):
            continue
        conversation = state.conversations.get(message.conversation_id)
        if conversation is not None and _agent_is_in_scope(conversation.agent_id):
            return True

    effective_now = _team_datetime_utc(now)
    return any(
        dispatch.cause_kind == "team_chat"
        and dispatch.status == TeamDispatchStatus.PREPARING
        and dispatch.preparation_expires_at is not None
        and _team_datetime_utc(dispatch.preparation_expires_at) <= effective_now
        for dispatch in state.dispatches.values()
    )


def _team_wake_lease_summary(
    lease: Mapping[str, Any],
    *,
    state: TeamState,
) -> dict[str, Any]:
    agent = lease.get("agent") if isinstance(lease.get("agent"), Mapping) else {}
    governance_group = (
        lease.get("governance_group") if isinstance(lease.get("governance_group"), Mapping) else {}
    )
    dispatch_id = _team_wake_output_text(lease.get("dispatch_id"))
    dispatch = _team_wake_final_dispatch_payload(
        state,
        dispatch_id=dispatch_id,
        agent=agent,
        governance_group=governance_group,
        fallback=lease.get("dispatch"),
    )
    release_package_ids = _team_wake_record_ids(lease.get("release_packages"))
    agenda_ids = _team_wake_record_ids(lease.get("agenda"))
    context_counts = {
        field_name: _team_wake_collection_count(lease.get(field_name))
        for field_name in (
            "tasks",
            "projects",
            "outcome_kinds",
            "cases",
            "external_source_evidence_snapshots",
            "inbox",
            "blockers",
            "reviews",
            "release_packages",
            "runtime_attention",
            "recent_action_feedback",
            "subordinates",
            "agenda",
            "member_teams",
            "cost_signals",
            "blocked_team_signals",
        )
    }
    summary: dict[str, Any] = {
        "agent_id": _team_wake_output_text(agent.get("id")),
        "session_id": _team_wake_output_text(agent.get("session_id")),
        "team_id": _team_wake_output_text(agent.get("team_id")),
        "dispatch_id": dispatch_id,
        "reason": _team_wake_output_text(lease.get("reason")),
        "status": _team_wake_dispatch_status(dispatch),
        "agent": {
            "id": _team_wake_output_text(agent.get("id")),
            "session_id": _team_wake_output_text(agent.get("session_id")),
            "team_id": _team_wake_output_text(agent.get("team_id")),
            "role_id": _team_wake_output_text(agent.get("role_id")),
            "status": _team_wake_output_text(agent.get("status")),
        },
        "dispatch": _team_wake_dispatch_summary(dispatch, lease=lease),
        "context_counts": context_counts,
        "release_package_ids": release_package_ids,
        "release_package_ids_truncated": (
            context_counts["release_packages"] > len(release_package_ids)
        ),
        "error": _team_wake_output_text(
            dispatch.get("error"),
            max_chars=TEAM_WAKE_OUTPUT_MAX_ERROR_CHARS,
        ),
    }
    if governance_group:
        summary["governance_group_id"] = _team_wake_output_text(governance_group.get("id"))
        summary["governance_group"] = {"id": _team_wake_output_text(governance_group.get("id"))}
        summary["agenda"] = [{"id": item_id} for item_id in agenda_ids]
        summary["agenda_ids_truncated"] = context_counts["agenda"] > len(agenda_ids)
    return summary


def _team_wake_final_dispatch_payload(
    state: TeamState,
    *,
    dispatch_id: str | None,
    agent: Mapping[str, Any],
    governance_group: Mapping[str, Any],
    fallback: Any,
) -> Mapping[str, Any]:
    """Return the persisted dispatch state corresponding to one wake lease."""

    if dispatch_id is not None:
        dispatch = state.dispatches.get(dispatch_id)
        if dispatch is not None:
            return dispatch.model_dump(mode="json")
    governance_group_id = _team_wake_output_text(governance_group.get("id"))
    if governance_group_id is not None:
        runtime = state.governance_runtimes.get(governance_group_id)
        return _team_wake_governance_dispatch_payload(
            runtime=(runtime.model_dump(mode="json") if runtime is not None else {}),
            dispatch_id=dispatch_id,
            agent=agent,
        )
    return fallback if isinstance(fallback, Mapping) else {}


def _team_wake_governance_dispatch_payload(
    *,
    runtime: Mapping[str, Any],
    dispatch_id: str | None,
    agent: Mapping[str, Any],
) -> dict[str, Any]:
    active_dispatch_id = _team_wake_output_text(runtime.get("active_dispatch_id"))
    if active_dispatch_id == dispatch_id:
        if runtime.get("queued_at") is not None:
            status = "queued"
        elif runtime.get("wake_leased_until") is not None:
            status = "leased"
        else:
            status = "preparing"
    elif active_dispatch_id is None:
        status = "completed"
    else:
        status = "superseded"
    return {
        "id": dispatch_id,
        "agent_id": _team_wake_output_text(agent.get("id")),
        "session_id": _team_wake_output_text(agent.get("session_id")),
        "status": status,
    }


def _team_wake_dispatch_status(dispatch: Mapping[str, Any]) -> str:
    status = _team_wake_output_text(dispatch.get("status")) or "preparing"
    if status == "preparing" and dispatch.get("queued_at") is not None:
        return "queued"
    return status


def _team_wake_dispatch_summary(
    dispatch: Mapping[str, Any],
    *,
    lease: Mapping[str, Any],
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "id": _team_wake_output_text(dispatch.get("id") or lease.get("dispatch_id")),
        "agent_id": _team_wake_output_text(dispatch.get("agent_id")),
        "session_id": _team_wake_output_text(dispatch.get("session_id")),
        "cause_kind": _team_wake_output_text(dispatch.get("cause_kind")),
        "cause_id": _team_wake_output_text(dispatch.get("cause_id")),
        "status": _team_wake_dispatch_status(dispatch),
        "error": _team_wake_output_text(
            dispatch.get("error"),
            max_chars=TEAM_WAKE_OUTPUT_MAX_ERROR_CHARS,
        ),
    }
    for field_name in (
        "task_ids",
        "blocker_ids",
        "review_request_ids",
        "inbox_item_ids",
        "project_ids",
        "managed_repo_ids",
        "source_evidence_snapshot_ids",
        "chat_message_ids",
        "action_feedback_dispatch_ids",
        "initial_release_decision_deliverable_ids",
    ):
        values = _team_wake_id_items(dispatch.get(field_name))
        summary[field_name] = values
        summary[f"{field_name[:-4]}_count"] = _team_wake_collection_count(dispatch.get(field_name))
        summary[f"{field_name}_truncated"] = summary[f"{field_name[:-4]}_count"] > len(values)
    return summary


def _team_wake_record_ids(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        return _team_wake_id_items(value.keys())
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return _team_wake_id_items(
        item.get("id") if isinstance(item, Mapping) else item for item in value
    )


def _team_wake_id_items(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        items: Sequence[Any] = list(value.keys())
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        items = value
    else:
        try:
            items = list(value or [])
        except TypeError:
            return []
    result: list[str] = []
    for item in items:
        normalized = _team_wake_output_text(item)
        if normalized is None or normalized in result:
            continue
        result.append(normalized)
        if len(result) >= TEAM_WAKE_OUTPUT_MAX_IDS_PER_FIELD:
            break
    return result


def _team_wake_collection_count(value: Any) -> int:
    if isinstance(value, (Mapping, Sequence)) and not isinstance(value, (str, bytes, bytearray)):
        return len(value)
    return 0


def _team_wake_output_text(
    value: Any,
    *,
    max_chars: int = TEAM_WAKE_OUTPUT_MAX_ID_CHARS,
) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max(0, max_chars - 3)] + "..."
