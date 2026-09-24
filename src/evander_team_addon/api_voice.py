"""Api voice."""

from __future__ import annotations

import hashlib
import json
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

from starlette.requests import (
    Request,
)

from evander.voice_sessions import (
    sanitize_voice_metadata,
)

from .api_requests import (
    _agent_display_name,
    _datetime_text,
    _enum_value,
    _limit_text,
    _operator_identity_from_request,
    _optional,
)
from .models import (
    TeamRecordState,
    TeamState,
    TeamTaskState,
    agent_project_assignment_payloads,
    agent_project_ids,
    effective_role_permissions,
)
from .provider_runtime import _session_records_by_id
from .provider_wake_chat import build_voice_reconciliation_context
from .services.chat_service import (
    TeamChatService,
)


def _record_chat_voice_call_status(
    chat_service: TeamChatService,
    record: Any,
) -> None:
    metadata = getattr(record, "caller_metadata", None)
    if not isinstance(metadata, Mapping) or not _optional(metadata, "conversation_id"):
        return
    try:
        chat_service.update_conversation_voice_call_status(
            voice_session_id=record.voice_session_id,
            status=str(getattr(record, "status", "") or "ended"),
            summary=str(getattr(record, "summary", "") or "").strip() or None,
            error=str(getattr(record, "error", "") or "").strip() or None,
        )
    except ValueError:
        return


def _session_is_expired(session: Any) -> bool:
    expires_at = getattr(session, "expires_at", None)
    if not isinstance(expires_at, datetime):
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= datetime.now(UTC)


def _build_agent_voice_projection(
    *,
    state: TeamState,
    agent: Any,
    session: Any,
    tool_summary: Any | None,
) -> dict[str, Any]:
    team = state.teams.get(agent.team_id)
    assigned_project_ids = agent_project_ids(state, agent.id)
    project = state.projects.get(assigned_project_ids[0]) if assigned_project_ids else None
    role = state.roles.get(agent.role_id) if agent.role_id is not None else None
    runtime = state.agent_runtimes.get(agent.id)
    subordinates = [
        state.agents[subordinate_id]
        for subordinate_id in agent.subordinate_agent_ids
        if subordinate_id in state.agents
    ]
    open_tasks = [
        task
        for task in state.tasks.values()
        if task.owner_agent_id == agent.id
        and _enum_value(task.state)
        not in {
            TeamTaskState.FINISHED.value,
            TeamTaskState.ACCEPTED.value,
            TeamTaskState.REJECTED.value,
            TeamTaskState.CANCELLED.value,
        }
    ]
    open_tasks.sort(
        key=lambda task: (
            int(task.priority),
            task.created_at.isoformat() if task.created_at else "",
            task.id,
        )
    )
    pending_inbox = [
        item
        for item in state.inbox.values()
        if item.recipient_agent_id == agent.id and _enum_value(item.state) != "handled"
    ]
    pending_inbox.sort(key=lambda item: item.created_at.isoformat())
    pending_reviews = [
        review
        for review in state.review_requests.values()
        if review.reviewer_agent_id == agent.id
        and _enum_value(review.state)
        not in {TeamRecordState.ACCEPTED.value, TeamRecordState.REJECTED.value}
    ]
    pending_reviews.sort(key=lambda review: (review.created_at or datetime.min).isoformat())
    recent_deliverables = [
        deliverable
        for deliverable in state.deliverables.values()
        if deliverable.producer_agent_id == agent.id
    ]
    recent_deliverables.sort(
        key=lambda deliverable: deliverable.created_at.isoformat(),
        reverse=True,
    )
    active_stand_downs = [
        stand_down
        for stand_down in state.stand_downs.values()
        if stand_down.agent_id == agent.id and not stand_down.cleared_by
    ]
    context_payload = {
        "team_state_revision": state.revision,
        "agent": {
            "id": agent.id,
            "display_name": _agent_display_name(agent),
            "status": _enum_value(agent.status),
            "session_id": agent.session_id,
            "reports_to_agent_id": agent.reports_to_agent_id,
            "subordinates": [
                {
                    "id": subordinate.id,
                    "display_name": _agent_display_name(subordinate),
                    "status": _enum_value(subordinate.status),
                }
                for subordinate in subordinates
            ],
        },
        "team": {
            "id": team.id if team else agent.team_id,
            "name": team.name if team else agent.team_id,
            "archived": bool(team.archived) if team else False,
        },
        "project": {
            "id": project.id if project else None,
            "name": project.name if project else None,
            "status": _enum_value(project.status) if project else None,
            "assignments": agent_project_assignment_payloads(state, agent.id),
            "goals": [
                {
                    "id": goal.id,
                    "description": goal.description,
                    "state": _enum_value(goal.state),
                }
                for goal in (project.goals if project else [])
            ],
        },
        "role": {
            "id": role.id if role else agent.role_id,
            "name": role.name if role else agent.role_id,
            "description": role.description if role else "",
            "permissions": [
                _enum_value(permission) for permission in effective_role_permissions(state, agent)
            ],
        },
        "runtime": {
            "wake_reason": getattr(runtime, "wake_reason", None),
            "active_dispatch_id": getattr(runtime, "active_dispatch_id", None),
            "wake_leased_until": _datetime_text(getattr(runtime, "wake_leased_until", None)),
            "last_wake_at": _datetime_text(getattr(runtime, "last_wake_at", None)),
            "next_wake_at": _datetime_text(getattr(runtime, "next_wake_at", None)),
            "no_progress_count": getattr(runtime, "no_progress_count", None),
            "last_progress_at": _datetime_text(getattr(runtime, "last_progress_at", None)),
            "context_clearing_policy": _enum_value(
                getattr(runtime, "context_clearing_policy", "off")
            ),
            "context_clearing_idle_grace_seconds": getattr(
                runtime,
                "context_clearing_idle_grace_seconds",
                0,
            ),
            "context_idle_rotation_due_at": _datetime_text(
                getattr(runtime, "context_idle_rotation_due_at", None)
            ),
            "auto_context_clearing_enabled": getattr(
                runtime,
                "auto_context_clearing_enabled",
                False,
            ),
            "context_rotation_pending": getattr(runtime, "context_rotation_pending", False),
            "last_handoff_summary": getattr(runtime, "last_handoff_summary", None),
            "context_handoff_preparation_pending": getattr(
                runtime, "context_handoff_preparation_pending", False
            ),
            "context_handoff_preparation_status": getattr(
                runtime, "context_handoff_preparation_status", None
            ),
            "last_agent_handoff_summary": getattr(runtime, "last_agent_handoff_summary", None),
            "last_agent_handoff_at": _datetime_text(
                getattr(runtime, "last_agent_handoff_at", None)
            ),
            "last_thread_id": getattr(runtime, "last_thread_id", None),
        },
        "open_tasks": [
            {
                "id": task.id,
                "priority": task.priority,
                "state": _enum_value(task.state),
                "title": task.title,
                "description": _limit_text(task.description, 700),
                "parent_task_id": task.parent_task_id,
                "dependency_task_ids": list(task.dependency_task_ids),
                "deliverable_ids": list(task.deliverable_ids),
            }
            for task in open_tasks[:10]
        ],
        "pending_inbox": [
            {
                "id": item.id,
                "item_type": _enum_value(item.item_type),
                "sender_agent_id": item.sender_agent_id,
                "task_id": item.task_id,
                "priority": _enum_value(item.priority),
                "response_requested": item.response_requested,
                "payload": sanitize_voice_metadata(item.payload),
            }
            for item in pending_inbox[:10]
        ],
        "pending_reviews": [
            {
                "id": review.id,
                "deliverable_id": review.deliverable_id,
                "state": _enum_value(review.state),
                "instructions": _limit_text(review.instructions, 500),
                "findings": list(review.findings),
            }
            for review in pending_reviews[:6]
        ],
        "recent_deliverables": [
            {
                "id": deliverable.id,
                "task_id": deliverable.task_id,
                "state": _enum_value(deliverable.state),
                "summary": _limit_text(deliverable.summary, 500),
                "artifact_refs": list(deliverable.artifact_refs),
            }
            for deliverable in recent_deliverables[:6]
        ],
        "active_stand_downs": [
            {
                "id": stand_down.id,
                "scope": stand_down.scope,
                "source": _enum_value(stand_down.source),
                "reason": stand_down.reason,
                "set_by": stand_down.set_by,
                "set_at": _datetime_text(stand_down.set_at),
            }
            for stand_down in active_stand_downs[:5]
        ],
        "origin_session": {
            "session_id": session.session_id,
            "directory_path": session.directory_path,
            "model": session.model,
            "expires_at": _datetime_text(session.expires_at),
        },
        "tool_summary": sanitize_voice_metadata(tool_summary or {}),
    }
    instructions = (
        "You are the browser voice projection of the selected persistent Team Add-on agent. "
        "The operator clicked Call in the Agent Addon People tab, so speak as a live helper "
        "for this agent and team status, not as a phone bridge.\n\n"
        "Use the projected team context below to answer questions about the agent, team, "
        "tasks, inbox, reviews, deliverables, runtime state, and visible tool capabilities. "
        "Keep answers concise and conversational. Do not claim to have changed files, tasks, "
        "or runtime state unless the origin agent confirms it. If a question needs files, "
        "tools, repository state, or live session details not present in the projection, call "
        "query_origin_agent with a concise question and continue once the answer arrives.\n\n"
        f"Selected agent: {_agent_display_name(agent)} ({agent.id})\n"
        f"Team: {context_payload['team']['name']} ({context_payload['team']['id']})\n"
        f"Primary project: {context_payload['project']['name'] or 'None'} "
        f"({context_payload['project']['id'] or 'none'})\n"
        f"Role: {context_payload['role']['name']} ({context_payload['role']['id']})\n\n"
        "Projected context JSON:\n"
        f"{json.dumps(context_payload, indent=2, sort_keys=True, default=str)}"
    )
    return {
        "prompt": instructions,
        "metadata": {
            "call_surface": "team_people_tab",
            "agent_id": agent.id,
            "team_id": agent.team_id,
            "project_ids": agent_project_ids(state, agent.id),
            "team_state_revision": state.revision,
        },
        "context": context_payload,
    }


def _agent_voice_prompt_metadata(
    *,
    state: TeamState,
    agent: Any,
    session: Any,
    prompt: str,
    effective_prompt: str | None = None,
    voice_profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = str(prompt or "").strip()
    effective = str(effective_prompt or normalized).strip()
    voice_profile_payload = sanitize_voice_metadata(voice_profile or {})
    return {
        "sha256": hashlib.sha256(effective.encode("utf-8")).hexdigest(),
        "length": len(effective),
        "base_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        "base_length": len(normalized),
        "team_state_revision": state.revision,
        "agent_id": agent.id,
        "team_id": agent.team_id,
        "project_ids": agent_project_ids(state, agent.id),
        "role_id": agent.role_id,
        "session_id": session.session_id,
        "call_surface": "team_people_tab",
        "voice_profile": voice_profile_payload,
    }


def _agent_voice_profile_payload(agent: Any) -> dict[str, Any]:
    voice_profile = getattr(agent, "voice_profile", None)
    if voice_profile is None:
        return {}
    model_dump = getattr(voice_profile, "model_dump", None)
    if callable(model_dump):
        payload = model_dump(mode="json", exclude_none=True)
    elif isinstance(voice_profile, Mapping):
        payload = {key: value for key, value in voice_profile.items() if value is not None}
    else:
        payload = {}
    return sanitize_voice_metadata(payload) if isinstance(payload, dict) else {}


def _agent_voice_effective_prompt(
    prompt: str,
    *,
    voice_profile: Mapping[str, Any],
) -> str:
    normalized = str(prompt or "").strip()
    speaking_style = str(voice_profile.get("speaking_style") or "").strip()
    if not speaking_style:
        return normalized
    return (
        f"{normalized}\n\n"
        "Voice delivery style for this agent:\n"
        f"{speaking_style}\n\n"
        "Apply this as spoken delivery guidance only. Do not treat it as task, "
        "team, tool, repository, or runtime state."
    )


def _require_browser_voice_session_operator(
    auth: Any | None,
    request: Request,
    record: Any,
) -> dict[str, str]:
    """Bind browser-side call events and completion to the caller that opened it."""

    identity = _operator_identity_from_request(auth, request)
    caller_metadata = getattr(record, "caller_metadata", {})
    if not isinstance(caller_metadata, Mapping):
        return identity
    operator_user_id = _optional(caller_metadata, "operator_user_id")
    if operator_user_id is not None and operator_user_id != identity["user_id"]:
        raise PermissionError("voice session belongs to a different operator")
    return identity


def _browser_voice_transcript_speaker_label(record: Any, speaker_role: str | None) -> str | None:
    """Replace browser-supplied roles with durable, human-readable participants."""

    normalized_role = str(speaker_role or "").strip().casefold()
    caller_metadata = getattr(record, "caller_metadata", {})
    if not isinstance(caller_metadata, Mapping):
        caller_metadata = {}
    if normalized_role in {"operator", "caller", "human", "user"}:
        return _voice_participant_label(
            display_name=_optional(caller_metadata, "operator_display_name"),
            user_id=_optional(caller_metadata, "operator_user_id"),
            fallback="operator",
        )
    if normalized_role in {"agent", "assistant", "model"}:
        return _voice_participant_label(
            display_name=_voice_participant_text(getattr(record, "contact_display_name", None)),
            user_id=_voice_participant_text(getattr(record, "contact_id", None)),
            fallback="agent",
        )
    return _voice_participant_text(speaker_role)


def _browser_voice_reconciliation_transcript(record: Any, transcript: str | None) -> str | None:
    """Canonicalize the role prefixes in the browser's final transcript excerpt."""

    normalized = _voice_participant_text(transcript)
    if normalized is None:
        return None
    lines: list[str] = []
    for line in normalized.splitlines():
        role, separator, body = line.partition(":")
        normalized_role = role.strip().casefold()
        if separator and normalized_role in {
            "operator",
            "caller",
            "human",
            "user",
            "agent",
            "assistant",
            "model",
        }:
            label = _browser_voice_transcript_speaker_label(record, role) or role
            lines.append(f"{label}: {body.lstrip()}")
        else:
            lines.append(line)
    return "\n".join(lines)


def _voice_participant_label(
    *,
    display_name: str | None,
    user_id: str | None,
    fallback: str,
) -> str:
    display = _voice_participant_text(display_name)
    user = _voice_participant_text(user_id)
    if display is not None and user is not None and display != user:
        return f"{display} ({user})"
    return display or user or fallback


def _voice_participant_text(value: object | None) -> str | None:
    text = str(value or "").strip()
    return text or None


def _browser_voice_reconciliation_context(
    *,
    state: TeamState,
    runtime: Any,
    record: Any,
) -> dict[str, Any]:
    """Snapshot the same compact Team working set used by ordinary fresh wakes."""

    agent_id = _voice_participant_text(getattr(record, "contact_id", None))
    if agent_id is None:
        return {}
    return build_voice_reconciliation_context(
        state,
        agent_id=agent_id,
        session_records=_session_records_by_id(getattr(runtime, "session_repository", None)),
    )


def _team_voice_session_payload(record: Any) -> dict[str, Any]:
    return {
        "voice_session_id": record.voice_session_id,
        "request_id": record.request_id,
        "origin_session_id": record.origin_session_id,
        "directory_path": record.directory_path,
        "channel": record.channel,
        "status": record.status,
        "contact_id": record.contact_id,
        "contact_display_name": record.contact_display_name,
        "transcript": record.transcript,
        "transcript_events": [event.model_dump(mode="json") for event in record.transcript_events],
        "reconciliation_status": record.reconciliation_status,
        "reconciliation_request_id": record.reconciliation_request_id,
        "error": record.error,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        "ended_at": record.ended_at.isoformat() if record.ended_at else None,
    }


def _realtime_calls_url(config: Any) -> str:
    base_url = (
        str(
            getattr(config, "openai_realtime_api_base_url", "https://api.openai.com/v1")
            or "https://api.openai.com/v1"
        )
        .strip()
        .rstrip("/")
    )
    return f"{base_url}/realtime/calls"
