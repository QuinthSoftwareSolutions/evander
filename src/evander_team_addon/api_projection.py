"""Api projection."""

from __future__ import annotations

import json
from collections.abc import (
    Mapping,
    Sequence,
)
from datetime import (
    UTC,
    datetime,
)
from pathlib import (
    Path,
)
from typing import (
    Any,
)

from starlette.requests import (
    Request,
)

from evander.addons import (
    AddonContext,
)
from evander.messaging.targets import (
    load_communication_targets_config,
)
from evander.model_usage.runtime import (
    MODEL_USAGE_STORE_FILENAME,
    model_usage_store,
)
from evander.models import (
    utc_now,
)

from .api_authorization import (
    _add_team_chat_mutation_modes,
    _authorized_external_source_evidence_snapshot_ids,
    _can_view_all_team_chats,
    _chat_list_scope,
    _chat_scope_for_operator,
    _filter_external_source_evidence_payload,
    _operator_options,
)
from .api_chat import (
    _chat_agent_payload,
    _conversation_collection_payload,
)
from .api_constants import (
    _COST_COLLECTIONS,
    DEFAULT_INBOX_HISTORY_LIMIT,
    DEFAULT_TIMELINE_LIMIT,
    MAX_INBOX_HISTORY_LIMIT,
    MAX_TIMELINE_LIMIT,
    TEAM_STATE_COLLECTION_KEYS,
    TEAM_STATE_HISTORY_COLLECTIONS,
    TEAM_VIEW_COLLECTIONS,
    TEAM_VIEW_IDS,
)
from .api_requests import (
    _operator_identity_from_request,
    _query_bool,
    _query_limited_int,
    _query_non_negative_int,
    _query_state_history_limit,
    _query_text,
    _query_values,
    _session_repository_from_context,
)
from .api_revision import (
    _add_team_resource_tokens,
    _add_team_resource_tokens_for_collections,
    _team_resource_tokens_for_collections,
    _trim_resource_tokens,
)
from .costs import (
    TeamCostService,
)
from .human_review_projection import (
    HUMAN_REVIEW_COLLECTION_KEYS,
    apply_compact_human_review_projection,
    release_package_api_payload,
)
from .models import (
    RolePermission,
    TeamInboxItemState,
    TeamInboxItemType,
    TeamOutboxStatus,
    TeamRecordState,
    TeamState,
    _agent_payload_with_role,
    agent_activity_summaries_payload,
    agent_project_ids,
    current_failed_action_records,
    external_source_evidence_snapshot_payload,
    filter_work_lineage_payloads,
    filter_work_lineage_references,
    runtime_operations_payload,
    team_state_payload,
)
from .provider_runtime import (
    _session_records_by_id,
)
from .repository import (
    TeamStateRepository,
)
from .services.admin_service import (
    TeamAdminService,
)
from .services.domain.git_common import (
    GIT_PROMOTION_EVENT_TYPE,
)
from .services.domain.task_assessment import (
    task_payload_with_assessment,
)
from .services.memory_service import (
    memory_record_payload,
)
from .timeline import (
    derive_team_timeline,
)


def _team_costs_payload(
    context: AddonContext,
    repository: TeamStateRepository,
    *,
    backfill_transcripts: bool,
) -> dict[str, Any]:
    state = repository.read_projection(collections=sorted(_COST_COLLECTIONS))
    state = TeamAdminService(repository).advance_team_cost_budget_windows(
        current_state=state,
    )
    service = TeamCostService(
        runtime_dir=context.runtime_dir,
        state=state,
        session_repository=_session_repository_from_context(context),
    )
    return service.costs_payload(backfill_transcripts=backfill_transcripts)


def _team_state_payload_for_request(
    auth: Any | None,
    request: Request,
    state: TeamState,
    *,
    runtime_dir: Path | None = None,
    session_records: Mapping[str, Any] | None = None,
    agent_execution_records: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    from .services.domain.project_status import project_blocking_sources
    from .services.domain.task_assessment import TaskAssessmentService

    assessment_service = TaskAssessmentService(state)
    payload = team_state_payload(
        state,
        session_records=session_records,
        agent_execution_records=agent_execution_records,
        task_assessments=assessment_service.assess_all(),
    )
    for project_id, project_payload in payload.get("projects", {}).items():
        if isinstance(project_payload, dict):
            project_payload["blocking_sources"] = project_blocking_sources(
                state,
                project_id,
                assessment_service=assessment_service,
            )
    apply_compact_human_review_projection(payload, state)
    _add_team_resource_tokens(payload, state)
    _filter_external_source_evidence_payload(
        payload,
        allowed_snapshot_ids=_authorized_external_source_evidence_snapshot_ids(
            auth,
            request,
            state,
        ),
    )
    if runtime_dir is not None:
        _add_context_reset_usage(payload, state, runtime_dir=runtime_dir)
    if not _can_view_all_team_chats(auth, request):
        identity = _operator_identity_from_request(auth, request)
        allowed_conversation_ids = {
            conversation.id
            for conversation in state.conversations.values()
            if conversation.operator_user_id == identity["user_id"]
        }
        payload["conversations"] = {
            conversation_id: conversation_payload
            for conversation_id, conversation_payload in payload.get("conversations", {}).items()
            if conversation_id in allowed_conversation_ids
        }
        payload["conversation_messages"] = {
            message_id: message_payload
            for message_id, message_payload in payload.get("conversation_messages", {}).items()
            if isinstance(message_payload, Mapping)
            and message_payload.get("conversation_id") in allowed_conversation_ids
        }
        payload["work_lineages"] = filter_work_lineage_payloads(
            payload.get("work_lineages"),
            visible_conversation_ids=allowed_conversation_ids,
        )
        filter_work_lineage_references(
            payload,
            visible_lineage_ids=set(payload["work_lineages"]),
        )
    _window_state_history_payload(
        payload,
        state,
        history_limit=_query_state_history_limit(request),
    )
    return payload


def _add_release_outbox_metadata(
    payload: dict[str, Any],
    repository: TeamStateRepository,
) -> None:
    release_packages = payload.get("release_packages")
    if not isinstance(release_packages, dict) or not release_packages:
        return
    promotion_to_release: dict[str, dict[str, Any]] = {}
    for release_payload in release_packages.values():
        if not isinstance(release_payload, dict):
            continue
        for promotion_id in release_payload.get("git_promotion_record_ids") or []:
            if isinstance(promotion_id, str) and promotion_id:
                promotion_to_release[promotion_id] = release_payload
    if not promotion_to_release:
        return
    for record in repository.list_outbox_records(intent_type=GIT_PROMOTION_EVENT_TYPE):
        if record.aggregate_id not in promotion_to_release:
            continue
        release_payload = promotion_to_release[record.aggregate_id]
        release_payload["promotion_attempt_count"] = max(
            int(release_payload.get("promotion_attempt_count") or 0),
            record.attempt_count,
        )
        row = {
            "id": record.id,
            "status": record.status.value,
            "aggregate_id": record.aggregate_id,
            "available_at": record.available_at.isoformat(),
            "attempt_count": record.attempt_count,
            "claimed_by": record.claimed_by,
            "completed_at": (
                record.completed_at.isoformat() if record.completed_at is not None else None
            ),
            "last_error_code": record.last_error_code,
            "last_error_message": record.last_error_message,
        }
        release_payload.setdefault("outbox_records", []).append(
            {key: value for key, value in row.items() if value is not None}
        )
        if record.status in {TeamOutboxStatus.PENDING, TeamOutboxStatus.CLAIMED}:
            release_payload.setdefault("active_outbox_record_ids", []).append(record.id)


def _bootstrap_payload_for_request(
    auth: Any | None,
    request: Request,
    state: TeamState,
    *,
    communication_targets: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    identity = _operator_identity_from_request(auth, request)
    scope = _chat_scope_for_operator(auth, request, state, identity["user_id"], identity=identity)
    selected_operator_user_id = str(scope["selected_operator"]["user_id"])
    return {
        "revision": state.revision,
        "views": list(TEAM_VIEW_IDS),
        "default_view": "people",
        "generated_at": utc_now().isoformat(),
        "current_operator": scope["current_operator"],
        "selected_operator": scope["selected_operator"],
        "can_view_all_operators": bool(scope.get("can_view_all_operators")),
        "operator_options": _operator_options(state, scope=scope),
        "communication_targets": list(communication_targets or ()),
        "counts": {
            "teams": len(state.teams),
            "governance_groups": len(state.governance_groups),
            "projects": len(state.projects),
            "agents": len(state.agents),
            "cases": len(state.cases),
            "tasks": len(state.tasks),
            "deliverables": len(state.deliverables),
            "review_requests": len(state.review_requests),
            "conversations": sum(
                1
                for conversation in state.conversations.values()
                if conversation.operator_user_id == selected_operator_user_id
            ),
        },
    }


def _registered_communication_target_options(
    context: AddonContext,
) -> list[dict[str, Any]]:
    """Return every root-directory communication target for Team configuration."""

    service = context.service
    runtime = getattr(service, "runtime", service)
    directory_store = getattr(runtime, "directories", None)
    if directory_store is None:
        return []
    try:
        targets = load_communication_targets_config(
            runtime_dir=context.runtime_dir,
            directory_store=directory_store,
            directory_path="root",
        ).targets
    except (OSError, ValueError):
        return []
    options: list[dict[str, Any]] = []
    for target in targets:
        capabilities = sorted(target.policy.capabilities)
        capability_suffix = f", {', '.join(capabilities)}" if capabilities else ""
        options.append(
            {
                "value": target.id,
                "label": f"{target.id} ({target.provider}/{target.kind}{capability_suffix})",
            }
        )
    return options


def _view_payload_for_request(
    auth: Any | None,
    request: Request,
    state: TeamState,
    *,
    view: str,
    runtime_dir: Path | None = None,
    session_records: Mapping[str, Any] | None = None,
    agent_execution_records: Mapping[str, Any] | None = None,
    session_repository: Any | None = None,
) -> dict[str, Any]:
    generated_at = utc_now().isoformat()
    if view == "chat":
        scope = _chat_list_scope(auth, request, state)
        scope = _add_team_chat_mutation_modes(
            scope,
            auth=auth,
            service=None,
            session_repository=session_repository,
            request=request,
            state=state,
        )
        selected_conversation_id = _query_text(request, "selected_conversation_id")
        chat_payload = _conversation_collection_payload(
            state,
            scope=scope,
            selected_conversation_id=selected_conversation_id,
            include_selected_messages=True,
        )
        agent_ids = {
            str(conversation.get("agent_id"))
            for conversation in chat_payload.get("conversations", [])
            if isinstance(conversation, Mapping) and conversation.get("agent_id")
        }
        agent_ids.update(
            str(agent.get("id"))
            for agent in chat_payload.get("eligible_agents", [])
            if isinstance(agent, Mapping) and agent.get("id")
        )
        agent_state = {
            agent_id: _chat_agent_payload(state.agents.get(agent_id))
            for agent_id in agent_ids
            if state.agents.get(agent_id) is not None
        }
        return {
            "revision": state.revision,
            "view": view,
            "generated_at": generated_at,
            "data": {
                "chat": chat_payload,
                "state": {
                    "revision": state.revision,
                    "agents": agent_state,
                    "resource_tokens": _team_resource_tokens_for_collections(
                        state,
                        {"agents"},
                        ids_by_collection={"agents": agent_ids},
                    ),
                },
            },
            "resource_tokens": _team_resource_tokens_for_collections(
                state,
                {"agents"},
                ids_by_collection={"agents": agent_ids},
            ),
        }

    collections = TEAM_VIEW_COLLECTIONS.get(view, TEAM_VIEW_COLLECTIONS["workspace"])
    ids_by_collection = _view_ids_by_collection(state, view, request=request)
    authorized_evidence_ids = _authorized_external_source_evidence_snapshot_ids(
        auth,
        request,
        state,
    )
    requested_evidence_ids = ids_by_collection.get("external_source_evidence_snapshots")
    ids_by_collection["external_source_evidence_snapshots"] = (
        authorized_evidence_ids
        if requested_evidence_ids is None
        else requested_evidence_ids & authorized_evidence_ids
    )
    data: dict[str, Any] = {
        "state": _state_slice_payload_for_request(
            auth,
            request,
            state,
            collections=collections,
            ids_by_collection=ids_by_collection,
            runtime_dir=runtime_dir,
            session_records=session_records,
            agent_execution_records=agent_execution_records,
            session_repository=session_repository,
        )
    }
    if view == "timeline":
        data["timeline"] = _timeline_payload_for_request(request, state)
    return {
        "revision": state.revision,
        "view": view,
        "generated_at": generated_at,
        "data": data,
        "resource_tokens": _team_resource_tokens_for_collections(
            state,
            collections,
            ids_by_collection=ids_by_collection,
        ),
    }


def _timeline_payload_for_request(request: Request, state: TeamState) -> dict[str, Any]:
    team_id = _query_text(request, "team_id")
    project_id = _query_text(request, "project_id")
    agent_id = _query_text(request, "agent_id")
    types = _query_values(request, "type")
    statuses = _query_values(request, "status")
    attention_needed = _query_bool(request, "attention_needed")
    limit = _query_limited_int(
        request,
        "limit",
        default=DEFAULT_TIMELINE_LIMIT,
        max_value=MAX_TIMELINE_LIMIT,
    )
    offset = _query_non_negative_int(request, "offset", default=0)
    filters: dict[str, object] = {}
    if team_id is not None:
        filters["team_id"] = team_id
    if project_id is not None:
        filters["project_id"] = project_id
    if agent_id is not None:
        filters["agent_id"] = agent_id
    if types is not None:
        filters["type"] = types
    if statuses is not None:
        filters["status"] = statuses
    if attention_needed is not None:
        filters["attention_needed"] = attention_needed
    return {
        **derive_team_timeline(
            state,
            team_id=team_id,
            project_id=project_id,
            agent_id=agent_id,
            types=types,
            statuses=statuses,
            attention_needed=attention_needed,
            limit=limit,
            offset=offset,
        ),
        "source": "derived_from_current_team_state",
        "kind": "activity_timeline",
        "completeness": "retained_current_state_only",
        "retention_boundary": (
            "Only records and timestamps retained in the current TeamState snapshot are "
            "represented."
        ),
        "description": (
            "Activity reconstructed from records retained in the current TeamState snapshot; "
            "earlier transitions, deleted or compacted records, and previous field values may "
            "be unavailable. This is not an audit log."
        ),
        "filters": filters,
    }


def _inbox_history_payload_for_request(request: Request, state: TeamState) -> dict[str, Any]:
    team_id = _query_text(request, "team_id")
    agent_id = _query_text(request, "agent_id")
    direction = (_query_text(request, "direction") or "all").casefold()
    item_type = _query_text(request, "item_type")
    item_state = _query_text(request, "state")
    search_text = _query_text(request, "q")
    limit = _query_limited_int(
        request,
        "limit",
        default=DEFAULT_INBOX_HISTORY_LIMIT,
        max_value=MAX_INBOX_HISTORY_LIMIT,
    )
    offset = _query_non_negative_int(request, "offset", default=0)

    if direction not in {"all", "received", "sent"}:
        raise ValueError("direction must be all, received, or sent")
    if direction != "all" and not agent_id:
        raise ValueError("agent_id is required when filtering inbox direction")
    if item_type and item_type not in {value.value for value in TeamInboxItemType}:
        raise ValueError(f"unknown inbox item_type: {item_type}")
    if item_state and item_state not in {value.value for value in TeamInboxItemState}:
        raise ValueError(f"unknown inbox state: {item_state}")

    rows = list(state.inbox.values())
    if team_id:
        team_agent_ids = {agent.id for agent in state.agents.values() if agent.team_id == team_id}
        rows = [
            item
            for item in rows
            if item.recipient_agent_id in team_agent_ids or item.sender_agent_id in team_agent_ids
        ]
    if agent_id:
        if direction == "received":
            rows = [item for item in rows if item.recipient_agent_id == agent_id]
        elif direction == "sent":
            rows = [item for item in rows if item.sender_agent_id == agent_id]
        else:
            rows = [
                item
                for item in rows
                if item.recipient_agent_id == agent_id or item.sender_agent_id == agent_id
            ]
    if item_type:
        rows = [item for item in rows if item.item_type.value == item_type]
    if search_text:
        needle = search_text.casefold()
        rows = [item for item in rows if needle in _inbox_history_search_text(state, item)]

    state_counts = {
        value.value: sum(1 for item in rows if item.state == value) for value in TeamInboxItemState
    }
    if item_state:
        rows = [item for item in rows if item.state.value == item_state]

    rows.sort(key=_inbox_history_sort_key, reverse=True)
    total_count = len(rows)
    page = rows[offset : offset + limit]
    filters = {
        key: value
        for key, value in {
            "team_id": team_id,
            "agent_id": agent_id,
            "direction": direction if direction != "all" else None,
            "item_type": item_type,
            "state": item_state,
            "q": search_text,
        }.items()
        if value is not None
    }
    return {
        "revision": state.revision,
        "generated_at": state.updated_at.isoformat(),
        "filters": filters,
        "state_counts": state_counts,
        "total_count": total_count,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(page) < total_count,
        "items": [
            _inbox_history_item_payload(state, item, focus_agent_id=agent_id) for item in page
        ],
    }


def _inbox_history_item_payload(
    state: TeamState,
    item: Any,
    *,
    focus_agent_id: str | None,
) -> dict[str, Any]:
    payload = item.model_dump(mode="json")
    payload["sender"] = _inbox_history_agent_payload(state, item.sender_agent_id)
    payload["recipient"] = _inbox_history_agent_payload(state, item.recipient_agent_id)
    task = state.tasks.get(item.task_id) if item.task_id else None
    payload["task"] = (
        {
            "id": task.id,
            "title": task.title,
            "project_id": task.project_id,
            "state": task.state.value,
        }
        if task is not None
        else None
    )
    if focus_agent_id and item.sender_agent_id == focus_agent_id:
        payload["direction"] = "sent"
    elif focus_agent_id and item.recipient_agent_id == focus_agent_id:
        payload["direction"] = "received"
    else:
        payload["direction"] = "internal" if item.sender_agent_id else "system"
    return payload


def _inbox_history_agent_payload(state: TeamState, agent_id: str | None) -> dict[str, Any] | None:
    if not agent_id:
        return None
    agent = state.agents.get(agent_id)
    if agent is None:
        return {"id": agent_id, "display_name": agent_id}
    role = state.roles.get(agent.role_id) if agent.role_id else None
    team = state.teams.get(agent.team_id) if agent.team_id else None
    return {
        "id": agent.id,
        "display_name": agent.display_name or agent.id,
        "team_id": agent.team_id,
        "team_name": team.name if team is not None else None,
        "role_name": role.name if role is not None else None,
        "avatar": agent.avatar.model_dump(mode="json") if agent.avatar is not None else None,
    }


def _inbox_history_search_text(state: TeamState, item: Any) -> str:
    sender = state.agents.get(item.sender_agent_id) if item.sender_agent_id else None
    recipient = state.agents.get(item.recipient_agent_id)
    values = [
        item.id,
        item.item_type.value,
        item.state.value,
        item.priority.value,
        item.sender_agent_id or "",
        sender.display_name if sender is not None else "",
        item.recipient_agent_id,
        recipient.display_name if recipient is not None else "",
        item.task_id or "",
        json.dumps(item.payload, sort_keys=True, ensure_ascii=False),
    ]
    return "\n".join(str(value or "") for value in values).casefold()


def _inbox_history_sort_key(item: Any) -> tuple[float, str]:
    timestamp = item.created_at
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return (timestamp.timestamp(), item.id)


def _latest_task_preparation_invalidation_ids(state: TeamState) -> set[str]:
    latest_by_task_id: dict[str, Any] = {}
    for item in state.inbox.values():
        task_id = str(item.task_id or "").strip()
        if (
            not task_id
            or task_id not in state.tasks
            or item.payload.get("kind") != "task_preparation_invalidated"
        ):
            continue
        current = latest_by_task_id.get(task_id)
        if current is None:
            latest_by_task_id[task_id] = item
            continue
        item_timestamp = _inbox_history_sort_key(item)[0]
        current_timestamp = _inbox_history_sort_key(current)[0]
        if item_timestamp > current_timestamp or (
            item_timestamp == current_timestamp and item.id < current.id
        ):
            latest_by_task_id[task_id] = item
    return {item.id for item in latest_by_task_id.values()}


def _task_board_case_task_ids(
    state: TeamState,
    *,
    case_id: str,
    project_id: str,
) -> set[str]:
    project_tasks = {
        task.id: task for task in state.tasks.values() if task.project_id == project_id
    }
    member_ids = {task.id for task in project_tasks.values() if task.case_id == case_id}
    children_by_source_id: dict[str, set[str]] = {}
    for task in project_tasks.values():
        for source_id in {task.parent_task_id, task.originating_task_id} - {None}:
            children_by_source_id.setdefault(source_id, set()).add(task.id)
    pending = list(member_ids)
    for task_id in pending:
        for child_id in children_by_source_id.get(task_id, set()):
            if child_id in member_ids:
                continue
            member_ids.add(child_id)
            pending.append(child_id)
    return member_ids


def _task_board_reference_task_ids(state: TeamState, task_ids: set[str]) -> set[str]:
    included_ids = set(task_ids)
    pending = list(task_ids)
    for task_id in pending:
        task = state.tasks.get(task_id)
        if task is None:
            continue
        for reference_id in {task.parent_task_id, task.originating_task_id} - {None}:
            if reference_id in included_ids or reference_id not in state.tasks:
                continue
            included_ids.add(reference_id)
            pending.append(reference_id)
    for task_id in task_ids:
        task = state.tasks.get(task_id)
        if task is not None:
            included_ids.update(
                reference_id
                for reference_id in task.dependency_task_ids
                if reference_id in state.tasks
            )
    return included_ids


def _task_board_selected_ids_by_collection(
    state: TeamState,
    request: Request,
) -> dict[str, set[str]]:
    case_id = _query_text(request, "case_id")
    requested_project_id = _query_text(request, "project_id")
    selected_case = state.cases.get(case_id) if case_id else None
    project_id = (
        str(selected_case.project_id or "").strip()
        if selected_case is not None
        else requested_project_id
    )
    project = state.projects.get(project_id) if project_id else None

    if selected_case is not None and project is not None:
        board_task_ids = _task_board_case_task_ids(
            state,
            case_id=selected_case.id,
            project_id=project.id,
        )
    elif case_id:
        board_task_ids = set()
    elif project is not None:
        board_task_ids = {task.id for task in state.tasks.values() if task.project_id == project.id}
    else:
        board_task_ids = set()
    task_ids = _task_board_reference_task_ids(state, board_task_ids)

    team_id = project.team_id if project is not None else ""
    case_ids = (
        {
            item.id
            for item in state.cases.values()
            if item.project_id == project.id
            or (item.project_id is None and item.team_id == team_id)
        }
        if project is not None
        else ({selected_case.id} if selected_case is not None else set())
    )
    if selected_case is not None:
        case_ids.add(selected_case.id)

    blocker_ids = {
        blocker.id
        for blocker in state.blockers.values()
        if blocker.task_id in board_task_ids
        or bool(set(blocker.affected_task_ids) & board_task_ids)
        or (
            getattr(blocker.source_kind, "value", blocker.source_kind) == "task"
            and blocker.source_id in board_task_ids
        )
    }
    blocker_event_ids = {
        event.id for event in state.blocker_events.values() if event.blocker_id in blocker_ids
    }
    recovery_ids = {
        recovery.id
        for recovery in state.dependency_composition_recoveries.values()
        if recovery.blocker_id in blocker_ids
        or recovery.target_task_id in board_task_ids
        or recovery.recovery_task_id in board_task_ids
    }
    dispatch_ids = {
        dispatch.id
        for dispatch in state.dispatches.values()
        if bool(set(dispatch.task_ids) & board_task_ids)
        or bool(set(dispatch.blocker_ids) & blocker_ids)
    }
    inbox_ids = {
        item_id
        for item_id in _latest_task_preparation_invalidation_ids(state)
        if state.inbox[item_id].task_id in board_task_ids
    }
    git_preparation_attempt_ids = {
        attempt.id
        for attempt in state.git_preparation_attempts.values()
        if attempt.task_id in board_task_ids
    }
    cleanup_candidate_ids = {
        candidate.id
        for candidate in state.worktree_cleanup_candidates.values()
        if candidate.task_id in board_task_ids
    }
    git_work_context_ids = {
        context.id
        for context in state.git_work_contexts.values()
        if context.task_id in board_task_ids
    }

    agent_ids = set()
    if project is not None:
        agent_ids.update(
            agent.id
            for agent in state.agents.values()
            if project.id in agent_project_ids(state, agent.id)
        )
        agent_ids.add(project.lead_agent_id)
    for task_id in task_ids:
        task = state.tasks.get(task_id)
        if task is not None:
            agent_ids.update({task.owner_agent_id, task.created_by_agent_id} - {None})
    for case_record_id in case_ids:
        case_record = state.cases.get(case_record_id)
        if case_record is not None and case_record.owner_agent_id:
            agent_ids.add(case_record.owner_agent_id)
    for blocker_id in blocker_ids:
        blocker = state.blockers[blocker_id]
        agent_ids.update(
            {
                blocker.reporter_agent_id,
                blocker.owner_agent_id,
                blocker.delegate_agent_id,
            }
            - {None}
        )
    for dispatch_id in dispatch_ids:
        agent_ids.add(state.dispatches[dispatch_id].agent_id)
    for item_id in inbox_ids:
        item = state.inbox[item_id]
        agent_ids.update({item.recipient_agent_id, item.sender_agent_id} - {None})
    for attempt_id in git_preparation_attempt_ids:
        agent_ids.add(state.git_preparation_attempts[attempt_id].agent_id)
    for candidate_id in cleanup_candidate_ids:
        agent_ids.add(state.worktree_cleanup_candidates[candidate_id].agent_id)
    for context_id in git_work_context_ids:
        agent_ids.add(state.git_work_contexts[context_id].agent_id)
    agent_ids.intersection_update(state.agents)

    evidence_ids = set()
    if selected_case is not None:
        evidence_ids.update(selected_case.source_evidence_snapshot_ids)
    for task_id in board_task_ids:
        task = state.tasks.get(task_id)
        if task is not None:
            evidence_ids.update(task.source_evidence_snapshot_ids)

    return {
        "agents": agent_ids,
        "agent_runtimes": agent_ids,
        "tasks": task_ids,
        "cases": case_ids,
        "external_source_evidence_snapshots": evidence_ids,
        "inbox": inbox_ids,
        "blockers": blocker_ids,
        "blocker_events": blocker_event_ids,
        "dependency_composition_recoveries": recovery_ids,
        "dispatches": dispatch_ids,
        "git_preparation_attempts": git_preparation_attempt_ids,
        "worktree_cleanup_candidates": cleanup_candidate_ids,
        "git_work_contexts": git_work_context_ids,
    }


def _view_ids_by_collection(
    state: TeamState,
    view: str,
    *,
    request: Request,
) -> dict[str, set[str]]:
    ids_by_collection: dict[str, set[str]] = {}
    if view in {"people", "org", "operations", "workspace"}:
        ids_by_collection["inbox"] = {
            item_id
            for item_id, item in state.inbox.items()
            if item.state != TeamInboxItemState.HANDLED
        }
    if view in {"planning", "task-board"}:
        ids_by_collection["inbox"] = _latest_task_preparation_invalidation_ids(state)
    if view == "task-board" and _query_text(request, "scope") == "selection":
        ids_by_collection.update(_task_board_selected_ids_by_collection(state, request))
    if view in {"operations", "workspace"}:
        ids_by_collection["action_records"] = {
            record.id for record in current_failed_action_records(state)
        }
        open_blocker_ids = {
            blocker.id for blocker in state.blockers.values() if blocker.state.value == "open"
        }
        ids_by_collection["blockers"] = open_blocker_ids
        ids_by_collection["blocker_events"] = {
            event.id
            for event in state.blocker_events.values()
            if event.blocker_id in open_blocker_ids
        }
        ids_by_collection["dependency_composition_recoveries"] = {
            recovery.id
            for recovery in state.dependency_composition_recoveries.values()
            if recovery.blocker_id in open_blocker_ids
        }
    return ids_by_collection


def _state_slice_payload_for_request(
    auth: Any | None,
    request: Request,
    state: TeamState,
    *,
    collections: set[str],
    ids_by_collection: Mapping[str, set[str]] | None = None,
    runtime_dir: Path | None = None,
    session_records: Mapping[str, Any] | None = None,
    agent_execution_records: Mapping[str, Any] | None = None,
    session_repository: Any | None = None,
) -> dict[str, Any]:
    scoped_ids = dict(ids_by_collection or {})
    requested_evidence_ids = scoped_ids.get("external_source_evidence_snapshots")
    authorized_evidence_ids = _authorized_external_source_evidence_snapshot_ids(
        auth,
        request,
        state,
    )
    scoped_ids["external_source_evidence_snapshots"] = (
        authorized_evidence_ids
        if requested_evidence_ids is None
        else requested_evidence_ids & authorized_evidence_ids
    )
    payload = state.model_dump(
        mode="json",
        include={"schema_version", "revision", "updated_at"},
    )
    payload["available_role_permissions"] = [permission.value for permission in RolePermission]
    for collection in TEAM_STATE_COLLECTION_KEYS:
        if collection not in collections:
            continue
        payload[collection] = _state_collection_slice_payload(
            state,
            collection,
            allowed_ids=scoped_ids.get(collection),
        )
    agent_ids = _state_slice_agent_ids(state, collections, scoped_ids)
    scoped_session_records = session_records
    if scoped_session_records is None:
        scoped_session_records = _session_records_for_agent_ids(
            session_repository,
            state,
            agent_ids,
        )
    runtime_agent_ids = None if agent_ids == set(state.agents) else agent_ids
    runtime_operations = runtime_operations_payload(
        state,
        session_records=scoped_session_records,
        agent_execution_records=agent_execution_records,
        agent_ids=runtime_agent_ids,
    )
    payload["runtime_operations"] = runtime_operations
    payload["agent_activity_summaries"] = agent_activity_summaries_payload(
        state,
        runtime_operations=runtime_operations,
        agent_ids=runtime_agent_ids,
    )
    for agent_id, summary in payload["agent_activity_summaries"].items():
        agent_payload = payload.get("agents", {}).get(agent_id)
        if isinstance(agent_payload, dict):
            agent_payload["activity_summary"] = summary
    if runtime_dir is not None:
        _add_context_reset_usage(payload, state, runtime_dir=runtime_dir)
    _add_team_resource_tokens_for_collections(
        payload,
        state,
        collections=collections,
        ids_by_collection=scoped_ids,
    )
    _filter_external_source_evidence_payload(
        payload,
        allowed_snapshot_ids=scoped_ids["external_source_evidence_snapshots"],
    )
    _window_state_history_payload(
        payload,
        state,
        history_limit=_query_state_history_limit(request),
    )
    return payload


def _add_context_reset_usage(
    payload: dict[str, Any],
    state: TeamState,
    *,
    runtime_dir: Path,
) -> None:
    """Attach reset-bounded input-token totals to eligible runtime payloads."""
    runtime_payloads = payload.get("agent_runtimes")
    if not isinstance(runtime_payloads, dict):
        return

    usage_store = None
    usage_store_unavailable = False
    if (Path(runtime_dir) / MODEL_USAGE_STORE_FILENAME).is_file():
        try:
            usage_store = model_usage_store(runtime_dir)
        except Exception:
            usage_store_unavailable = True

    for agent_id, runtime_payload in runtime_payloads.items():
        if not isinstance(runtime_payload, dict):
            continue
        runtime = state.agent_runtimes.get(agent_id)
        agent = state.agents.get(agent_id)
        if runtime is None or agent is None:
            continue
        policy = str(
            getattr(runtime.context_clearing_policy, "value", runtime.context_clearing_policy)
            or "off"
        )
        if policy in {"every_wake", "work_lineage"}:
            continue

        input_tokens_since_reset: int | None = None
        if runtime.last_context_rotated_at is not None:
            if usage_store_unavailable:
                input_tokens_since_reset = None
            elif usage_store is None:
                input_tokens_since_reset = 0
            else:
                try:
                    input_tokens_since_reset = usage_store.sum_input_tokens(
                        session_id=agent.session_id,
                        occurred_after=runtime.last_context_rotated_at,
                    )
                except Exception:
                    input_tokens_since_reset = None
        runtime_payload["context_reset_usage"] = {
            "input_tokens_since_reset": input_tokens_since_reset,
        }


def _state_collection_slice_payload(
    state: TeamState,
    collection: str,
    *,
    allowed_ids: set[str] | None = None,
) -> dict[str, Any]:
    if collection in HUMAN_REVIEW_COLLECTION_KEYS:
        return {}
    value = getattr(state, collection, {})
    if not isinstance(value, Mapping):
        return {}
    rows: dict[str, Any] = {}
    for item_id, item in value.items():
        if allowed_ids is not None and item_id not in allowed_ids:
            continue
        if collection == "agents":
            rows[item_id] = _agent_payload_with_role(state, item)
        elif collection == "tasks":
            rows[item_id] = task_payload_with_assessment(state, item)
        elif collection == "release_packages":
            rows[item_id] = release_package_api_payload(
                state,
                item,
                include_human_details=False,
            )
        elif collection == "memories":
            rows[item_id] = memory_record_payload(item, include_body=False)
        elif collection == "external_source_evidence_snapshots":
            rows[item_id] = external_source_evidence_snapshot_payload(item)
        elif hasattr(item, "model_dump"):
            rows[item_id] = item.model_dump(mode="json")
        else:
            rows[item_id] = item
    return rows


def _state_slice_agent_ids(
    state: TeamState,
    collections: set[str],
    ids_by_collection: Mapping[str, set[str]],
) -> set[str]:
    if "agents" in collections:
        allowed_ids = ids_by_collection.get("agents")
        if allowed_ids is not None:
            return {agent_id for agent_id in allowed_ids if agent_id in state.agents}
        return set(state.agents)
    return set()


def _session_records_for_agent_ids(
    repository: Any | None,
    state: TeamState,
    agent_ids: set[str],
) -> dict[str, Any] | None:
    records = _session_records_by_id(repository)
    if records is None or not agent_ids:
        return None
    session_ids: set[str] = set()
    for agent_id in sorted(agent_ids):
        agent = state.agents.get(agent_id)
        session_id = str(getattr(agent, "session_id", "") or "").strip() if agent else ""
        if session_id:
            session_ids.add(session_id)
    return {session_id: records[session_id] for session_id in session_ids if session_id in records}


def _filter_state_payload(
    payload: dict[str, Any],
    *,
    collections: set[str],
    ids_by_collection: Mapping[str, set[str]],
) -> dict[str, Any]:
    filtered: dict[str, Any] = {}
    for key, value in payload.items():
        if key in TEAM_STATE_COLLECTION_KEYS:
            if key not in collections:
                continue
            allowed_ids = ids_by_collection.get(key)
            if allowed_ids is not None and isinstance(value, Mapping):
                filtered[key] = {
                    item_id: item for item_id, item in value.items() if item_id in allowed_ids
                }
                continue
        filtered[key] = value
    tokens = filtered.get("resource_tokens")
    if isinstance(tokens, Mapping):
        scoped_tokens: dict[str, Any] = {}
        for collection, collection_tokens in tokens.items():
            if collection not in collections:
                continue
            allowed_ids = ids_by_collection.get(str(collection))
            if allowed_ids is not None and isinstance(collection_tokens, Mapping):
                scoped_tokens[str(collection)] = {
                    item_id: token
                    for item_id, token in collection_tokens.items()
                    if item_id in allowed_ids
                }
            else:
                scoped_tokens[str(collection)] = collection_tokens
        filtered["resource_tokens"] = scoped_tokens
    return filtered


def _window_state_history_payload(
    payload: dict[str, Any],
    state: TeamState,
    *,
    history_limit: int,
) -> None:
    windows: dict[str, dict[str, Any]] = {}
    for collection in TEAM_STATE_HISTORY_COLLECTIONS:
        rows = payload.get(collection)
        if not isinstance(rows, Mapping):
            continue
        allowed_ids = {str(item_id) for item_id in rows}
        keep_ids = _state_history_keep_ids(
            state,
            collection,
            allowed_ids=allowed_ids,
            history_limit=history_limit,
        )
        payload[collection] = {
            item_id: item for item_id, item in rows.items() if str(item_id) in keep_ids
        }
        _trim_resource_tokens(payload, collection, keep_ids)
        returned_count = len(payload[collection])
        total_count = len(rows)
        windowed = returned_count < total_count
        windows[collection] = {
            "total_count": total_count,
            "returned_count": returned_count,
            "limit": history_limit,
            "windowed": windowed,
        }
        payload[f"{collection}_total_count"] = total_count
        payload[f"{collection}_returned_count"] = returned_count
        payload[f"{collection}_windowed"] = windowed
    if windows:
        payload["history_windows"] = windows


def _state_history_keep_ids(
    state: TeamState,
    collection: str,
    *,
    allowed_ids: set[str],
    history_limit: int,
) -> set[str]:
    source = getattr(state, collection, {})
    if not isinstance(source, Mapping) or not allowed_ids:
        return set()
    if collection == "action_records":
        current_ids = {
            record.id for record in current_failed_action_records(state) if record.id in allowed_ids
        }
    else:
        current_ids = {
            str(item_id)
            for item_id, item in source.items()
            if str(item_id) in allowed_ids and _state_history_item_is_current(collection, item)
        }
    newest_ids = [
        str(item_id)
        for item_id, _item in sorted(
            (
                (str(item_id), item)
                for item_id, item in source.items()
                if str(item_id) in allowed_ids and str(item_id) not in current_ids
            ),
            key=lambda row: _state_history_sort_key(row[0], row[1]),
            reverse=True,
        )
    ]
    keep_ids = set(current_ids)
    # Release history has its own allowance so active work cannot hide publications.
    remaining_slots = (
        history_limit if collection == "release_packages" else max(0, history_limit - len(keep_ids))
    )
    keep_ids.update(newest_ids[:remaining_slots])
    return keep_ids


def _state_history_item_is_current(collection: str, item: Any) -> bool:
    if collection == "inbox":
        return _record_state_value(item) != TeamInboxItemState.HANDLED.value
    if collection == "release_packages":
        return _record_state_value(item) not in {
            TeamRecordState.ACCEPTED.value,
            TeamRecordState.REJECTED.value,
            TeamRecordState.CANCELLED.value,
        }
    if collection == "blockers":
        return _record_state_value(item) == "open"
    if collection == "dependency_composition_recoveries":
        return _record_state_value(item) in {"awaiting_assignment", "in_progress"}
    if collection == "ephemeral_assignments":
        return _record_state_value(item) not in {
            "accepted",
            "cleaned_up",
            "completed",
        }
    if collection == "git_promotions":
        return _record_state_value(item) != "succeeded"
    return False


def _record_state_value(item: Any) -> str:
    value = getattr(item, "state", "") or ""
    enum_value = getattr(value, "value", None)
    return str(enum_value if enum_value is not None else value)


def _state_history_sort_key(item_id: str, item: Any) -> tuple[float, str]:
    for field_name in (
        "updated_at",
        "last_interaction_at",
        "published_at",
        "decision_at",
        "created_at",
    ):
        timestamp = getattr(item, field_name, None)
        if isinstance(timestamp, datetime):
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
            return (timestamp.timestamp(), item_id)
    return (float("-inf"), item_id)
