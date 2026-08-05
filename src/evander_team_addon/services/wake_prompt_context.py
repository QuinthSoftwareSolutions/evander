from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from ..models import ACTIVE_TEAM_CASE_STATUS_VALUES


TEXT_LIMIT = 240
TITLE_LIMIT = 160
ROLE_DESCRIPTION_LIMIT = 200
ID_LIST_LIMIT = 12

MAX_SUBORDINATE_IDS = 20
MAX_SUBORDINATES = 12
MAX_TASKS = 10
MAX_PROJECTS = 8
MAX_OUTCOME_KINDS = 20
MAX_INBOX = 10
MAX_BLOCKERS = 8
MAX_REVIEWS = 8
MAX_CASES = 8
MAX_RELEASE_PACKAGES = 8
MAX_RUNTIME_ATTENTION = 10
MAX_ACTION_FEEDBACK = 4
MAX_SESSION_HEALTH = 12
MAX_MANAGER_FALLBACK_AGENTS = 12
MAX_MANAGER_FALLBACK_ROWS = 8


def wake_uses_fresh_context(
    lease: Mapping[str, Any],
    *,
    effective_resume_mode: str | None = None,
) -> bool:
    """Return whether this wake will run without retained conversation context.

    Runtime lifecycle state is authoritative. The effect/provider resume mode is
    only needed when that explicit state does not already require a fresh
    context. Agent display names and model/provider names are intentionally not
    part of this decision.
    """

    runtime = lease.get("runtime")
    runtime_behavior_available = False
    if isinstance(runtime, Mapping):
        runtime_behavior_available = (
            "context_rotation_pending" in runtime
            or "context_clearing_policy" in runtime
        )
        if bool(runtime.get("context_rotation_pending")):
            return True
        policy = _optional_text(runtime.get("context_clearing_policy"))
        if policy == "every_wake":
            return True
    if lease.get("has_prior_successful_wake") is False:
        return True
    normalized_resume_mode = _optional_text(effective_resume_mode)
    if normalized_resume_mode is not None:
        return normalized_resume_mode == "new_context"
    return not runtime_behavior_available


def build_compact_wake_context(
    lease: Mapping[str, Any],
    *,
    fresh_context: bool = True,
) -> dict[str, Any]:
    """Build the small prompt context for a Team wake lease.

    The lease payload can contain large historical slices. This keeps only the
    current/actionable rows and records counts for rows dropped by caps so an
    agent can fetch detail with read tools.
    """

    omitted_counts: dict[str, int] = {}
    agent = _mapping_or_empty(lease.get("agent"))
    subordinate_ids = _limited_values(
        _string_items(lease.get("subordinate_agent_ids")),
        MAX_SUBORDINATE_IDS,
        omitted_counts,
        "subordinate_ids",
    )
    compact_agent = (
        _compact_agent(agent)
        if fresh_context
        else _compact_fields(agent, ("id",))
    )
    if not fresh_context and lease.get("role_description_changed") is True:
        role_description = _optional_text(agent.get("role_description"))
        if role_description is not None:
            compact_agent["role_description"] = role_description
    compact_agent.pop("display_name", None)
    dispatch = _compact_current_dispatch(lease)
    context: dict[str, Any] = {
        "agent": compact_agent,
        "context_mode": "fresh_full" if fresh_context else "retained_delta",
        "reason": _json_value(lease.get("reason")),
        "current_dispatch": dispatch,
    }
    if fresh_context:
        context["superior_id"] = _optional_text(lease.get("superior_agent_id"))
        context["subordinate_ids"] = subordinate_ids

    if fresh_context:
        subordinates = sorted(
            (
                _compact_agent(row, subordinate=True)
                for row in _mapping_rows(lease.get("subordinates"))
            ),
            key=lambda row: _sort_text(row.get("id")),
        )
        if subordinates:
            context["subordinates"] = _limited_rows(
                subordinates,
                MAX_SUBORDINATES,
                omitted_counts,
                "subordinates",
            )

    tasks = sorted(
        (_compact_task(row) for row in _mapping_rows(lease.get("tasks"))),
        key=_task_sort_key,
    )
    context["tasks"] = _limited_rows(tasks, MAX_TASKS, omitted_counts, "tasks")

    if fresh_context:
        projects = sorted(
            (_compact_project(row) for row in _mapping_rows(lease.get("projects"))),
            key=lambda row: _sort_text(row.get("id")),
        )
        if projects:
            context["projects"] = _limited_rows(
                projects,
                MAX_PROJECTS,
                omitted_counts,
                "projects",
            )

    if fresh_context:
        outcome_kinds = sorted(
            (
                _compact_outcome_kind(row)
                for row in _mapping_rows(lease.get("outcome_kinds"))
            ),
            key=lambda row: (
                _sort_text(row.get("effective_project_id")),
                _sort_text(row.get("kind")),
            ),
        )
        if outcome_kinds:
            context["outcome_kinds"] = _limited_rows(
                outcome_kinds,
                MAX_OUTCOME_KINDS,
                omitted_counts,
                "outcome_kinds",
            )

    inbox_rows, non_pending_inbox_count = _pending_inbox_rows(lease.get("inbox"))
    _add_omitted(omitted_counts, "inbox_non_pending", non_pending_inbox_count)
    sorted_inbox_rows = sorted(inbox_rows, key=_inbox_sort_key)
    sole_high_priority_id = (
        _optional_text(sorted_inbox_rows[0].get("id"))
        if len(sorted_inbox_rows) == 1
        and _optional_text(sorted_inbox_rows[0].get("priority")) == "high"
        else None
    )
    context["inbox"] = _limited_rows(
        [
            _compact_inbox(
                row,
                payload_text_limit=(
                    2048
                    if _optional_text(row.get("id")) == sole_high_priority_id
                    else TEXT_LIMIT
                ),
            )
            for row in sorted_inbox_rows
        ],
        MAX_INBOX,
        omitted_counts,
        "inbox",
    )
    included_inbox_items = {
        inbox_item_id: row
        for row in context["inbox"]
        if (inbox_item_id := _optional_text(row.get("id"))) is not None
    }

    blockers = sorted(
        (_compact_blocker(row) for row in _mapping_rows(lease.get("blockers"))),
        key=lambda row: (_sort_text(row.get("created_at")), _sort_text(row.get("id"))),
    )
    if blockers:
        context["blockers"] = _limited_rows(
            blockers,
            MAX_BLOCKERS,
            omitted_counts,
            "blockers",
        )

    reviews = sorted(
        (_compact_review(row) for row in _mapping_rows(lease.get("reviews"))),
        key=lambda row: (
            _sort_text(row.get("created_at")),
            _sort_text(row.get("id")),
        ),
    )
    if reviews:
        context["reviews"] = _limited_rows(
            reviews,
            MAX_REVIEWS,
            omitted_counts,
            "reviews",
        )

    if _optional_text(lease.get("reason")) == "action_feedback":
        action_feedback = sorted(
            (
                _compact_action_feedback(row)
                for row in _mapping_rows(lease.get("recent_action_feedback"))
            ),
            key=_action_feedback_sort_key,
        )
        if action_feedback:
            context["action_feedback"] = _limited_rows(
                action_feedback,
                MAX_ACTION_FEEDBACK,
                omitted_counts,
                "action_feedback",
            )

    cases, non_current_case_count = _current_case_rows(lease.get("cases"))
    _add_omitted(omitted_counts, "cases_non_current", non_current_case_count)
    if cases:
        context["cases"] = _limited_rows(
            sorted((_compact_case(row) for row in cases), key=_case_sort_key),
            MAX_CASES,
            omitted_counts,
            "cases",
        )

    releases, non_current_release_count = _current_release_rows(lease.get("release_packages"))
    _add_omitted(omitted_counts, "release_packages_non_current", non_current_release_count)
    context["release_packages"] = _limited_release_packages(
        releases,
        omitted_counts,
    )
    visible_release_ids = set(context["release_packages"])

    runtime_attention, hidden_attention_count, out_of_scope_attention_count = (
        _runtime_attention_rows(
            lease.get("runtime_attention"),
            dispatch=dispatch,
            visible_release_ids=visible_release_ids,
        )
    )
    _add_omitted(omitted_counts, "runtime_attention_hidden_failed_actions", hidden_attention_count)
    _add_omitted(
        omitted_counts,
        "runtime_attention_outside_dispatch",
        out_of_scope_attention_count,
    )
    if not fresh_context:
        runtime_attention, duplicate_inbox_attention_count = (
            _without_inbox_runtime_attention_duplicates(
                runtime_attention,
                included_inbox_items=included_inbox_items,
            )
        )
        _add_omitted(
            omitted_counts,
            "runtime_attention_duplicate_inbox",
            duplicate_inbox_attention_count,
        )
    if runtime_attention:
        context["runtime_attention"] = _limited_rows(
            sorted(
                (_compact_runtime_attention(row) for row in runtime_attention),
                key=_runtime_attention_sort_key,
            ),
            MAX_RUNTIME_ATTENTION,
            omitted_counts,
            "runtime_attention",
        )

    session_health = sorted(
        (
            _compact_session_health(row)
            for row in _mapping_rows(lease.get("session_health"))
            if fresh_context or _session_health_requires_attention(row)
        ),
        key=_session_health_sort_key,
    )
    if session_health:
        context["session_health"] = _limited_rows(
            session_health,
            MAX_SESSION_HEALTH,
            omitted_counts,
            "session_health",
        )

    operator_chat_participants = sorted(
        (
            _compact_operator_chat_participant(row)
            for row in _mapping_rows(lease.get("operator_chat_participants"))
        ),
        key=lambda row: (
            _sort_text(row.get("operator_display_name")),
            _sort_text(row.get("operator_user_id")),
        ),
    )
    if "operator_chat_participants" in lease:
        context["operator_chat_participants_replaces_prior"] = True
        context["operator_chat_participants"] = operator_chat_participants

    manager_fallback_summary = lease.get("manager_fallback_summary")
    if isinstance(manager_fallback_summary, Mapping):
        context["manager_fallback_summary"] = _compact_manager_fallback_summary(
            manager_fallback_summary
        )

    project_repos = _deduplicated_project_repos(
        lease,
        retained_delta=not fresh_context,
    )
    if project_repos:
        context["project_repos"] = project_repos

    review_artifact_imports = sorted(
        (
            _compact_review_artifact_import(row)
            for row in _mapping_rows(lease.get("review_artifact_imports"))
        ),
        key=lambda row: (
            _sort_text(row.get("review_request_id")),
            _sort_text(row.get("deliverable_id")),
            _sort_text(row.get("artifact_key")),
        ),
    )
    if review_artifact_imports:
        context["review_artifact_imports"] = review_artifact_imports

    review_artifact_errors = sorted(
        (
            _compact_review_artifact_error(row)
            for row in _mapping_rows(lease.get("review_artifact_import_errors"))
        ),
        key=lambda row: (
            _sort_text(row.get("review_request_id")),
            _sort_text(row.get("deliverable_id")),
            _sort_text(row.get("artifact_ref")),
        ),
    )
    if review_artifact_errors:
        context["review_artifact_import_errors"] = review_artifact_errors

    if omitted_counts:
        context["omitted_counts"] = dict(sorted(omitted_counts.items()))
    return _json_value(context)


def _compact_current_dispatch(lease: Mapping[str, Any]) -> dict[str, Any]:
    source = lease.get("dispatch")
    dispatch = source if isinstance(source, Mapping) else {}
    row = _compact_fields(
        dispatch,
        (
            "id",
            "cause_kind",
            "cause_id",
            "task_ids",
            "blocker_ids",
            "review_request_ids",
            "inbox_item_ids",
            "project_ids",
            "managed_repo_ids",
            "review_routing_authorities",
            "lease_expires_at",
        ),
    )
    if "id" not in row:
        dispatch_id = _optional_text(lease.get("dispatch_id"))
        if dispatch_id is not None:
            row["id"] = dispatch_id
    if "cause_kind" not in row:
        reason = _optional_text(lease.get("reason"))
        if reason is not None:
            row["cause_kind"] = reason
    if "cause_id" not in row:
        cause_id = _optional_text(lease.get("cause_id"))
        if cause_id is not None:
            row["cause_id"] = cause_id
    return row


def _deduplicated_project_repos(
    lease: Mapping[str, Any],
    *,
    retained_delta: bool = False,
) -> list[dict[str, Any]]:
    candidates = list(_mapping_rows(lease.get("project_repos")))
    project_repo = lease.get("project_repo")
    if isinstance(project_repo, Mapping):
        candidates.append(project_repo)
    result: list[dict[str, Any]] = []
    for candidate in candidates:
        compact = _compact_project_repo(
            candidate,
            retained_delta=retained_delta,
        )
        if compact not in result:
            result.append(compact)
    return result


def _compact_agent(agent: Mapping[str, Any], *, subordinate: bool = False) -> dict[str, Any]:
    fields = (
        "id",
        "session_id",
        "display_name",
        "team_id",
        "role_id",
        "role_name",
        "status",
    )
    row = {key: _json_value(agent.get(key)) for key in fields if key in agent}
    role_description = _optional_text(agent.get("role_description"))
    if role_description is not None:
        row["role_description"] = (
            _trim_text(role_description, ROLE_DESCRIPTION_LIMIT)
            if subordinate
            else role_description
        )
    for key in (
        "project_ids",
        "lead_project_ids",
        "role_permissions",
        "effective_role_permissions",
    ):
        values = _string_items(agent.get(key))
        if values:
            row[key] = values[:ID_LIST_LIMIT]
            if len(values) > ID_LIST_LIMIT:
                row[f"{key}_omitted_count"] = len(values) - ID_LIST_LIMIT
    assignments = [
        _compact_project_assignment(item)
        for item in _mapping_rows(agent.get("project_assignments"))
    ]
    if assignments and not subordinate:
        row["project_assignments"] = sorted(
            assignments,
            key=lambda item: (
                _sort_text(item.get("project_id")),
                _sort_text(item.get("kind")),
                _sort_text(item.get("id")),
            ),
        )[:ID_LIST_LIMIT]
        if len(assignments) > ID_LIST_LIMIT:
            row["project_assignments_omitted_count"] = len(assignments) - ID_LIST_LIMIT
    return row


def _compact_project_assignment(item: Mapping[str, Any]) -> dict[str, Any]:
    return _compact_fields(
        item,
        (
            "id",
            "team_id",
            "project_id",
            "role_id",
            "kind",
        ),
    )


def _compact_task(task: Mapping[str, Any]) -> dict[str, Any]:
    row = _compact_fields(
        task,
        (
            "id",
            "state",
            "priority",
            "owner_agent_id",
            "project_id",
            "case_id",
            "parent_task_id",
            "dependency_status",
            "created_at",
            "updated_at",
            "state_entered_at",
            "task_kind",
            "integration_boundary",
        ),
    )
    row["title"] = _trim_text(task.get("title"), TITLE_LIMIT)
    row["blocked_by_task_ids"] = _limited_row_values(task.get("blocked_by_task_ids"), ID_LIST_LIMIT)
    row["child_task_ids"] = _limited_row_values(task.get("child_task_ids"), ID_LIST_LIMIT)
    row["deliverable_ids"] = _limited_row_values(task.get("deliverable_ids"), ID_LIST_LIMIT)
    _copy_row_omitted_count(row, "blocked_by_task_ids", task.get("blocked_by_task_ids"))
    _copy_row_omitted_count(row, "child_task_ids", task.get("child_task_ids"))
    _copy_row_omitted_count(row, "deliverable_ids", task.get("deliverable_ids"))
    subtask_counts = task.get("subtask_counts")
    row["subtask_counts"] = _json_value(
        subtask_counts if isinstance(subtask_counts, Mapping) else {}
    )
    if isinstance(task.get("outcome_contract"), Mapping):
        row["outcome_contract"] = _json_value(task["outcome_contract"])
    if isinstance(task.get("deliverable_contract"), Mapping):
        contract = task["deliverable_contract"]
        row["deliverable_contract"] = _compact_fields(
            contract,
            (
                "require_git_provenance",
                "required_contribution_task_ids",
            ),
        )
    if isinstance(task.get("completion"), Mapping):
        row["completion"] = _json_value(task["completion"])
    return row


def _compact_project(project: Mapping[str, Any]) -> dict[str, Any]:
    row = _compact_fields(
        project,
        (
            "id",
            "team_id",
            "name",
            "lead_agent_id",
            "status",
            "updated_at",
        ),
    )
    goals = [
        {
            **_compact_fields(goal, ("id", "state")),
            "description": _trim_text(goal.get("description"), 1200),
        }
        for goal in _mapping_rows(project.get("goals"))
    ]
    if goals:
        row["goals"] = goals[:ID_LIST_LIMIT]
        if len(goals) > ID_LIST_LIMIT:
            row["goals_omitted_count"] = len(goals) - ID_LIST_LIMIT
    blocking_sources = _mapping_rows(project.get("blocking_sources"))
    if blocking_sources:
        row["blocking_sources"] = [
            {
                **_compact_fields(source, ("kind", "id", "state")),
                "summary": _trim_text(source.get("summary"), 640),
            }
            for source in blocking_sources[:ID_LIST_LIMIT]
        ]
    return row


def _compact_outcome_kind(definition: Mapping[str, Any]) -> dict[str, Any]:
    row = _compact_fields(
        definition,
        (
            "id",
            "kind",
            "name",
            "team_id",
            "project_id",
            "effective_project_id",
            "inherited",
        ),
    )
    for field_name in (
        "description",
        "reference_requirements",
        "evidence_requirements",
    ):
        value = _trim_text(definition.get(field_name), 640)
        if value:
            row[field_name] = value
    return row


def _compact_inbox(
    item: Mapping[str, Any],
    *,
    payload_text_limit: int = TEXT_LIMIT,
) -> dict[str, Any]:
    row = _compact_fields(
        item,
        (
            "id",
            "item_type",
            "sender_agent_id",
            "recipient_agent_id",
            "task_id",
            "response_requested",
            "priority",
            "state",
            "created_at",
            "updated_at",
        ),
    )
    payload = item.get("payload")
    if isinstance(payload, Mapping):
        compact_payload = _compact_payload(
            payload,
            text_limit=payload_text_limit,
        )
        if compact_payload:
            row["payload"] = compact_payload
    return row


def _compact_case(case: Mapping[str, Any]) -> dict[str, Any]:
    row = _compact_fields(
        case,
        (
            "id",
            "team_id",
            "project_id",
            "status",
            "priority",
            "owner_agent_id",
            "source",
            "source_ref",
            "created_at",
            "updated_at",
        ),
    )
    row["title"] = _trim_text(case.get("title"), TITLE_LIMIT)
    task_ids = _string_items(case.get("task_ids"))
    if task_ids:
        row["task_ids"] = task_ids[:ID_LIST_LIMIT]
        if len(task_ids) > ID_LIST_LIMIT:
            row["task_ids_omitted_count"] = len(task_ids) - ID_LIST_LIMIT
    tags = _string_items(case.get("tags"))
    if tags:
        row["tags"] = tags[:ID_LIST_LIMIT]
        if len(tags) > ID_LIST_LIMIT:
            row["tags_omitted_count"] = len(tags) - ID_LIST_LIMIT
    return row


def _compact_release_package(release: Mapping[str, Any]) -> dict[str, Any]:
    row = _compact_fields(
        release,
        (
            "id",
            "project_id",
            "state",
            "release_branch",
            "canonical_branch",
            "published_commit_sha",
            "candidate_commit_sha",
            "approved_commit_sha",
            "next_action",
            "blocked_reason_code",
            "created_at",
            "updated_at",
            "published_at",
        ),
    )
    for key in (
        "deliverable_ids",
        "artifact_refs",
        "feature_branches",
        "git_promotion_record_ids",
    ):
        values = _string_items(release.get(key))
        if values:
            row[key] = values[:ID_LIST_LIMIT]
            if len(values) > ID_LIST_LIMIT:
                row[f"{key}_omitted_count"] = len(values) - ID_LIST_LIMIT
    error = _optional_text(release.get("error"))
    if error is not None:
        row["error"] = _trim_text(error)
    next_action_hint = _optional_text(release.get("next_action_hint"))
    if next_action_hint is not None:
        row["next_action_hint"] = _trim_text(next_action_hint, 640)
    gate_failure = release.get("gate_failure")
    if isinstance(gate_failure, Mapping):
        row["gate_failure"] = {
            key: gate_failure.get(key)
            for key in ("stage", "kind", "source_id", "message", "occurred_at")
            if gate_failure.get(key) is not None
        }
    return row


def _compact_runtime_attention(row: Mapping[str, Any]) -> dict[str, Any]:
    compact = _compact_fields(
        row,
        (
            "id",
            "kind",
            "severity",
            "state",
            "team_id",
            "project_id",
            "task_id",
            "deliverable_id",
            "inbox_item_id",
            "assignment_id",
            "promotion_id",
            "release_package_id",
            "review_request_id",
            "blocker_id",
            "source_kind",
            "source_id",
            "owner_agent_id",
            "delegate_agent_id",
            "route",
            "escalation_depth",
            "active_dispatch_id",
            "follow_up_at",
            "session_id",
            "session_expires_at",
            "item_type",
            "response_requested",
            "priority",
            "promotion_kind",
            "source_branch",
            "target_branch",
            "next_action",
        ),
    )
    agent_id = _optional_text(row.get("agent_id"))
    if agent_id is not None:
        compact["agent"] = agent_id
    sender_id = _optional_text(row.get("sender_agent_id"))
    if sender_id is not None:
        compact["sender_agent_id"] = sender_id
    for key in ("title", "detail", "error", "next_action_hint"):
        text = _optional_text(row.get(key))
        if text is not None:
            compact[key] = _trim_text(text, 640 if key == "next_action_hint" else TEXT_LIMIT)
    for key in ("project_ids", "blocked_by_task_ids", "deliverable_ids", "artifact_refs"):
        values = _string_items(row.get(key))
        if values:
            compact[key] = values[:ID_LIST_LIMIT]
            if len(values) > ID_LIST_LIMIT:
                compact[f"{key}_omitted_count"] = len(values) - ID_LIST_LIMIT
    return compact


def _compact_blocker(blocker: Mapping[str, Any]) -> dict[str, Any]:
    row = _compact_fields(
        blocker,
        (
            "id",
            "team_id",
            "project_id",
            "task_id",
            "source_kind",
            "source_id",
            "reporter_agent_id",
            "owner_agent_id",
            "delegate_agent_id",
            "state",
            "route",
            "priority",
            "escalation_depth",
            "active_dispatch_id",
            "follow_up_at",
            "last_progress_at",
            "created_at",
            "updated_at",
        ),
    )
    for key in ("summary", "details"):
        value = _optional_text(blocker.get(key))
        if value is not None:
            row[key] = _trim_text(value)
    return row


def _compact_action_feedback(row: Mapping[str, Any]) -> dict[str, Any]:
    compact = _compact_fields(
        row,
        (
            "action_record_id",
            "dispatch_id",
            "created_at",
        ),
    )
    error = _optional_text(row.get("error"))
    if error is not None:
        compact["error"] = _trim_text(error, 640)
    action_fields = _string_items(row.get("action_fields"))
    if action_fields:
        compact["action_fields"] = action_fields[:ID_LIST_LIMIT]
        if len(action_fields) > ID_LIST_LIMIT:
            compact["action_fields_omitted_count"] = len(action_fields) - ID_LIST_LIMIT
    correction_hints = [
        _trim_text(hint, TEXT_LIMIT) for hint in _string_items(row.get("correction_hints"))
    ]
    if correction_hints:
        compact["correction_hints"] = correction_hints[:ID_LIST_LIMIT]
        if len(correction_hints) > ID_LIST_LIMIT:
            compact["correction_hints_omitted_count"] = len(correction_hints) - ID_LIST_LIMIT
    return compact


def _compact_session_health(row: Mapping[str, Any]) -> dict[str, Any]:
    compact = _compact_fields(
        row,
        (
            "team_id",
            "role_id",
            "session_id",
            "session_present",
            "expires_at",
            "is_expired",
            "status",
            "vm_provider",
            "execution_backend",
            "container_lifecycle_state",
            "agent_execution_backend",
            "agent_execution_status",
            "agent_active_execution_count",
            "agent_latest_execution_id",
        ),
    )
    agent_id = _optional_text(row.get("agent_id"))
    if agent_id is not None:
        compact["agent"] = agent_id
    for key in ("project_ids", "lead_project_ids"):
        values = _string_items(row.get(key))
        compact[key] = values[:ID_LIST_LIMIT]
        if len(values) > ID_LIST_LIMIT:
            compact[f"{key}_omitted_count"] = len(values) - ID_LIST_LIMIT
    return compact


def _session_health_requires_attention(row: Mapping[str, Any]) -> bool:
    status = (_optional_text(row.get("status")) or "").casefold()
    execution_status = (
        _optional_text(row.get("agent_execution_status")) or ""
    ).casefold()
    lifecycle = (
        _optional_text(row.get("container_lifecycle_state")) or ""
    ).casefold()
    return (
        row.get("is_expired") is True
        or row.get("session_present") is False
        or status in {"missing", "expired", "failed", "error", "unhealthy"}
        or execution_status in {"failed", "error", "stalled"}
        or lifecycle in {"failed", "error", "stopped", "exited"}
    )


def _compact_operator_chat_participant(row: Mapping[str, Any]) -> dict[str, Any]:
    compact = _compact_fields(row, ("operator_user_id", "operator_display_name"))
    conversation_ids = _string_items(row.get("conversation_ids"))
    if conversation_ids:
        compact["conversation_ids"] = conversation_ids
    return compact


def _compact_manager_fallback_summary(summary: Mapping[str, Any]) -> dict[str, Any]:
    omitted_counts = {
        str(key): _json_value(value)
        for key, value in _mapping_or_empty(summary.get("omitted_counts")).items()
        if _positive_int(value) > 0
    }
    row: dict[str, Any] = _compact_fields(summary, ("reason", "generated_at"))
    team = summary.get("team")
    if isinstance(team, Mapping):
        row["team"] = _compact_fields(team, ("id", "name", "manager_agent_id"))
    projects = [
        _compact_fields(project, ("id", "name", "lead_agent_id", "status"))
        for project in _mapping_rows(summary.get("projects"))
    ]
    if projects:
        row["projects"] = sorted(projects, key=lambda item: _sort_text(item.get("id")))
    counts = summary.get("summary_counts")
    if isinstance(counts, Mapping):
        row["summary_counts"] = _json_value(counts)
    reviewer_ids = _string_items(summary.get("reviewer_candidate_agent_ids"))
    row["reviewer_candidate_ids"] = reviewer_ids[:ID_LIST_LIMIT]
    if len(reviewer_ids) > ID_LIST_LIMIT:
        omitted_counts["reviewer_candidate_ids"] = len(reviewer_ids) - ID_LIST_LIMIT

    row["agents"] = _manager_fallback_limited_rows(
        [_compact_manager_agent(item) for item in _mapping_rows(summary.get("agents"))],
        MAX_MANAGER_FALLBACK_AGENTS,
        "agents",
        omitted_counts,
        key=lambda item: _sort_text(item.get("id")),
    )
    row["open_work"] = _manager_fallback_limited_rows(
        [_compact_manager_task(item) for item in _mapping_rows(summary.get("open_work"))],
        MAX_MANAGER_FALLBACK_ROWS,
        "open_work",
        omitted_counts,
        key=_task_sort_key,
    )
    row["deliverables"] = _manager_fallback_limited_rows(
        [_compact_manager_deliverable(item) for item in _mapping_rows(summary.get("deliverables"))],
        MAX_MANAGER_FALLBACK_ROWS,
        "deliverables",
        omitted_counts,
        key=lambda item: (_state_rank(item.get("state")), _sort_text(item.get("id"))),
    )
    row["pending_initial_release_decisions"] = _manager_fallback_limited_rows(
        [
            _compact_fields(
                item,
                (
                    "project_id",
                    "task_id",
                    "task_title",
                    "deliverable_id",
                    "summary",
                    "producer_agent_id",
                    "disposition",
                    "release_policy_configured",
                    "artifact_ref_count",
                    "reviewed_commit_sha",
                    "git_base_branch",
                ),
            )
            for item in _mapping_rows(
                summary.get("pending_initial_release_decisions")
            )
        ],
        MAX_MANAGER_FALLBACK_ROWS,
        "pending_initial_release_decisions",
        omitted_counts,
        key=lambda item: (
            _sort_text(item.get("project_id")),
            _sort_text(item.get("deliverable_id")),
        ),
    )
    row["review_requests"] = _manager_fallback_limited_rows(
        [
            _compact_manager_review_request(item)
            for item in _mapping_rows(summary.get("review_requests"))
        ],
        MAX_MANAGER_FALLBACK_ROWS,
        "review_requests",
        omitted_counts,
        key=lambda item: (_state_rank(item.get("state")), _sort_text(item.get("id"))),
    )
    row["pending_inbox"] = _manager_fallback_limited_rows(
        [_compact_manager_inbox(item) for item in _mapping_rows(summary.get("pending_inbox"))],
        MAX_MANAGER_FALLBACK_ROWS,
        "pending_inbox",
        omitted_counts,
        key=_inbox_sort_key,
    )
    row["routing_gaps"] = _manager_fallback_limited_rows(
        [_compact_routing_gap(item) for item in _mapping_rows(summary.get("routing_gaps"))],
        MAX_MANAGER_FALLBACK_ROWS,
        "routing_gaps",
        omitted_counts,
        key=lambda item: (
            _severity_rank(item.get("severity")),
            _sort_text(item.get("kind")),
            _sort_text(item.get("agent_id")),
        ),
    )
    if omitted_counts:
        row["omitted_counts"] = dict(sorted(omitted_counts.items()))
    return row


def _compact_manager_agent(item: Mapping[str, Any]) -> dict[str, Any]:
    row = _compact_fields(
        item,
        (
            "id",
            "display_name",
            "status",
            "role_id",
            "role_name",
            "active_task_count",
            "pending_review_request_count",
            "pending_inbox_count",
            "response_requested_inbox_count",
            "failed_action_count",
            "latest_failed_action_id",
            "wake_reason",
            "wake_leased",
            "wake_leased_until",
            "next_wake_at",
            "active_stand_down_count",
        ),
    )
    active_wake = _optional_text(item.get("active_dispatch_id"))
    if active_wake is not None:
        row["active_wake"] = active_wake
    for key in ("project_ids", "active_task_ids", "pending_review_request_ids"):
        values = _string_items(item.get(key))
        if values:
            row[key] = values[:ID_LIST_LIMIT]
            if len(values) > ID_LIST_LIMIT:
                row[f"{key}_omitted_count"] = len(values) - ID_LIST_LIMIT
    return row


def _compact_manager_task(item: Mapping[str, Any]) -> dict[str, Any]:
    row = _compact_fields(
        item,
        (
            "id",
            "state",
            "owner_agent_id",
            "project_id",
            "priority",
            "dependency_status",
            "waiting_reason",
        ),
    )
    row["title"] = _trim_text(item.get("title"), TITLE_LIMIT)
    for key in (
        "blocked_by_task_ids",
        "deliverable_ids",
        "review_request_ids",
        "reviewer_agent_ids",
    ):
        values = _string_items(item.get(key))
        if values:
            row[key] = values[:ID_LIST_LIMIT]
            if len(values) > ID_LIST_LIMIT:
                row[f"{key}_omitted_count"] = len(values) - ID_LIST_LIMIT
    for key in ("review_state_counts", "subtask_counts"):
        value = item.get(key)
        if isinstance(value, Mapping):
            row[key] = _json_value(value)
    return row


def _compact_manager_deliverable(item: Mapping[str, Any]) -> dict[str, Any]:
    row = _compact_fields(
        item,
        (
            "id",
            "task_id",
            "producer_agent_id",
            "state",
        ),
    )
    summary = _optional_text(item.get("summary"))
    if summary is not None:
        row["summary"] = _trim_text(summary)
    for key in ("review_request_ids", "reviewer_agent_ids", "artifact_refs"):
        values = _string_items(item.get(key))
        if values:
            row[key] = values[:ID_LIST_LIMIT]
            if len(values) > ID_LIST_LIMIT:
                row[f"{key}_omitted_count"] = len(values) - ID_LIST_LIMIT
    for key in ("review_state_counts", "git_refs"):
        value = item.get(key)
        if isinstance(value, Mapping):
            row[key] = _json_value(value)
    return row


def _compact_manager_review_request(item: Mapping[str, Any]) -> dict[str, Any]:
    row = _compact_fields(
        item,
        (
            "id",
            "deliverable_id",
            "reviewer_agent_id",
            "state",
            "review_profile_id",
            "finding_count",
        ),
    )
    reviewer_agent_id = _optional_text(item.get("reviewer_agent_id"))
    if reviewer_agent_id is not None:
        row["reviewer_id"] = reviewer_agent_id
    for key in ("instructions", "latest_finding"):
        text = _optional_text(item.get(key))
        if text is not None:
            row[key] = _trim_text(text)
    counts = item.get("criteria_state_counts")
    if isinstance(counts, Mapping):
        row["criteria_state_counts"] = _json_value(counts)
    return row


def _compact_manager_inbox(item: Mapping[str, Any]) -> dict[str, Any]:
    row = _compact_fields(
        item,
        (
            "id",
            "recipient_agent_id",
            "sender_agent_id",
            "task_id",
            "item_type",
            "response_requested",
            "priority",
            "created_at",
        ),
    )
    summary = _optional_text(item.get("summary"))
    if summary is not None:
        row["summary"] = _trim_text(summary)
    return row


def _compact_routing_gap(item: Mapping[str, Any]) -> dict[str, Any]:
    row = _compact_fields(
        item,
        (
            "kind",
            "severity",
            "agent_id",
            "agent_display_name",
        ),
    )
    agent_id = _optional_text(item.get("agent_id"))
    if agent_id is not None:
        row["agent"] = agent_id
    for key in ("summary", "suggested_action"):
        text = _optional_text(item.get(key))
        if text is not None:
            row[key] = _trim_text(text)
    for key in ("project_ids", "task_ids", "deliverable_ids", "existing_reviewer_agent_ids"):
        values = _string_items(item.get(key))
        if values:
            output_key = "existing_reviewer_ids" if key == "existing_reviewer_agent_ids" else key
            row[output_key] = values[:ID_LIST_LIMIT]
            if len(values) > ID_LIST_LIMIT:
                row[f"{output_key}_omitted_count"] = len(values) - ID_LIST_LIMIT
    return row


def _compact_project_repo(
    repo: Mapping[str, Any],
    *,
    retained_delta: bool = False,
) -> dict[str, Any]:
    row = _compact_fields(
        repo,
        (
            "prepared",
            "project_id",
            "managed_repo_id",
            "target_dir",
            "guest_repo_name",
            "remote_url",
            "pr_notes_ref",
            "execution_backend",
            "vm_name",
            "base_branch",
            "branch_context_id",
            "agent_branch",
        ),
    )
    git_contexts = [
        _compact_git_work_context(row) for row in _mapping_rows(repo.get("git_work_contexts"))
    ]
    if git_contexts:
        row["git_work_contexts"] = git_contexts
    if retained_delta and len(git_contexts) == 1 and git_contexts[0].get("state") == "active":
        active_context = git_contexts[0]
        for field_name in ("base_branch", "branch_context_id", "agent_branch"):
            if row.get(field_name) == active_context.get(field_name):
                row.pop(field_name, None)
    message = _optional_text(repo.get("message"))
    if message is not None:
        row["message"] = _trim_text(message)
    return row


def _compact_review(review: Mapping[str, Any]) -> dict[str, Any]:
    row = _compact_fields(
        review,
        (
            "id",
            "deliverable_id",
            "release_package_id",
            "reviewer_agent_id",
            "state",
            "review_profile_id",
            "candidate_commit_sha",
            "created_at",
        ),
    )
    instructions = _optional_text(review.get("instructions"))
    if instructions is not None:
        row["instructions"] = _trim_text(instructions)
    return row


def _compact_git_work_context(context: Mapping[str, Any]) -> dict[str, Any]:
    row = _compact_fields(
        context,
        (
            "id",
            "branch_context_id",
            "agent_id",
            "task_id",
            "agent_branch",
            "base_branch",
            "commit_sha",
            "dispatch_id",
            "worktree_path",
            "state",
        ),
    )
    dependency_sources = _mapping_rows(context.get("dependency_git_sources"))
    if dependency_sources:
        row["dependency_git_sources"] = dependency_sources
    return row


def _compact_review_artifact_import(item: Mapping[str, Any]) -> dict[str, Any]:
    row = _compact_fields(
        item,
        (
            "review_request_id",
            "deliverable_id",
            "artifact_ref",
            "artifact_space_path",
            "artifact_id",
            "artifact_key",
            "artifact_space_version_id",
            "artifact_space_version",
            "destination_path",
            "artifact_path",
            "imported_file_count",
        ),
    )
    imported_paths = _string_items(item.get("imported_paths"))
    if imported_paths:
        row["imported_paths"] = imported_paths
    return row


def _compact_review_artifact_error(item: Mapping[str, Any]) -> dict[str, Any]:
    row = _compact_fields(
        item,
        (
            "review_request_id",
            "deliverable_id",
            "artifact_ref",
            "inbox_item_id",
            "artifact_space_path",
            "artifact_id",
            "artifact_key",
            "artifact_space_version_id",
            "artifact_space_version",
        ),
    )
    error = _optional_text(item.get("error"))
    if error is not None:
        row["error"] = _trim_text(error)
    return row


def _compact_payload(
    payload: Mapping[str, Any],
    *,
    text_limit: int = TEXT_LIMIT,
) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    omitted_keys: list[str] = []
    for raw_key, value in sorted(payload.items(), key=lambda entry: str(entry[0])):
        key = str(raw_key)
        if value in (None, "", [], {}):
            continue
        if key in _PAYLOAD_TEXT_KEYS:
            compact[key] = _trim_text(value, text_limit)
        elif key in _PAYLOAD_LIST_KEYS or key.endswith("_ids"):
            values = _string_items(value)
            compact[key] = values[:ID_LIST_LIMIT]
            if len(values) > ID_LIST_LIMIT:
                compact[f"{key}_omitted_count"] = len(values) - ID_LIST_LIMIT
        elif key in _PAYLOAD_MAPPING_KEYS:
            compact[key] = _compact_nested_payload_mapping(value)
        elif key.endswith("_id") or key in _PAYLOAD_SCALAR_KEYS:
            compact[key] = _json_value(value)
        else:
            omitted_keys.append(key)
    if omitted_keys:
        compact["omitted_payload_keys"] = omitted_keys[:ID_LIST_LIMIT]
        if len(omitted_keys) > ID_LIST_LIMIT:
            compact["omitted_payload_keys_omitted_count"] = len(omitted_keys) - ID_LIST_LIMIT
    return compact


_PAYLOAD_TEXT_KEYS = {
    "body",
    "directive",
    "instructions",
    "message",
    "note",
    "question",
    "reason",
    "summary",
}
_PAYLOAD_LIST_KEYS = {
    "artifact_paths",
    "artifact_refs",
    "available_alternatives",
    "available_decisions",
    "criteria",
    "deliverable_ids",
    "findings",
    "handled_inbox_item_ids",
    "source_paths",
    "task_ids",
}
_PAYLOAD_MAPPING_KEYS = {
    "git_refs",
    "review_profile",
    "verification",
}
_PAYLOAD_SCALAR_KEYS = {
    "kind",
    "priority",
    "project_id",
    "response_requested",
    "source",
    "source_ref",
    "state",
    "status",
    "type",
}


def _compact_nested_payload_mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    compact: dict[str, Any] = {}
    for key, item in value.items():
        key_text = str(key)
        if item in (None, "", [], {}):
            continue
        if isinstance(item, str):
            compact[key_text] = _trim_text(item)
        elif isinstance(item, (bool, int, float, Decimal)):
            compact[key_text] = _json_value(item)
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            values = _string_items(item)
            compact[key_text] = values[:ID_LIST_LIMIT]
            if len(values) > ID_LIST_LIMIT:
                compact[f"{key_text}_omitted_count"] = len(values) - ID_LIST_LIMIT
    return compact


def _pending_inbox_rows(value: object) -> tuple[list[Mapping[str, Any]], int]:
    rows = _mapping_rows(value)
    pending: list[Mapping[str, Any]] = []
    skipped = 0
    for row in rows:
        state = _optional_text(row.get("state"))
        if state is not None and state != "pending":
            skipped += 1
            continue
        pending.append(row)
    return pending, skipped


def _current_case_rows(value: object) -> tuple[list[Mapping[str, Any]], int]:
    rows = _mapping_rows(value)
    current: list[Mapping[str, Any]] = []
    skipped = 0
    for row in rows:
        if _optional_text(row.get("status")) not in ACTIVE_TEAM_CASE_STATUS_VALUES:
            skipped += 1
            continue
        current.append(row)
    return current, skipped


def _current_release_rows(value: object) -> tuple[list[Mapping[str, Any]], int]:
    rows = _mapping_rows(value)
    current: list[Mapping[str, Any]] = []
    skipped = 0
    for row in rows:
        state = _optional_text(row.get("state"))
        if state in {"accepted", "rejected"}:
            skipped += 1
            continue
        current.append(row)
    return current, skipped


def _runtime_attention_rows(
    value: object,
    *,
    dispatch: Mapping[str, Any],
    visible_release_ids: set[str],
) -> tuple[list[Mapping[str, Any]], int, int]:
    rows = _mapping_rows(value)
    visible: list[Mapping[str, Any]] = []
    hidden = 0
    out_of_scope = 0
    for row in rows:
        if row.get("kind") == "failed_action_record":
            hidden += 1
            continue
        if row.get("kind") == "blocked_release_package":
            if _optional_text(row.get("state")) in {"accepted", "rejected"}:
                continue
            release_id = _optional_text(row.get("release_package_id"))
            if release_id in visible_release_ids:
                continue
        if not _runtime_attention_matches_dispatch(row, dispatch):
            out_of_scope += 1
            continue
        visible.append(row)
    return visible, hidden, out_of_scope


def _without_inbox_runtime_attention_duplicates(
    rows: Sequence[Mapping[str, Any]],
    *,
    included_inbox_items: Mapping[str, dict[str, Any]],
) -> tuple[list[Mapping[str, Any]], int]:
    """Fold enriched context into included inbox items before dropping mirrors."""

    if not included_inbox_items:
        return list(rows), 0
    retained: list[Mapping[str, Any]] = []
    duplicates = 0
    for row in rows:
        inbox_item_id = _optional_text(row.get("inbox_item_id"))
        inbox_item = included_inbox_items.get(inbox_item_id or "")
        if inbox_item is not None:
            _merge_runtime_attention_context_into_inbox(inbox_item, row)
            duplicates += 1
            continue
        retained.append(row)
    return retained, duplicates


def _merge_runtime_attention_context_into_inbox(
    inbox_item: dict[str, Any],
    attention: Mapping[str, Any],
) -> None:
    """Preserve scoped relationship metadata omitted from legacy payload shapes."""

    for key in ("team_id", "project_id"):
        value = _optional_text(attention.get(key))
        if value is not None and _optional_text(inbox_item.get(key)) is None:
            inbox_item[key] = value
    project_ids = _string_items(attention.get("project_ids"))
    if project_ids and not _string_items(inbox_item.get("project_ids")):
        inbox_item["project_ids"] = project_ids[:ID_LIST_LIMIT]
        if len(project_ids) > ID_LIST_LIMIT:
            inbox_item["project_ids_omitted_count"] = len(project_ids) - ID_LIST_LIMIT


def _runtime_attention_matches_dispatch(
    row: Mapping[str, Any],
    dispatch: Mapping[str, Any],
) -> bool:
    dispatch_id = _optional_text(dispatch.get("id"))
    cause_id = _optional_text(dispatch.get("cause_id"))
    scopes = {
        "task": set(_string_items(dispatch.get("task_ids"))),
        "blocker": set(_string_items(dispatch.get("blocker_ids"))),
        "review_request": set(_string_items(dispatch.get("review_request_ids"))),
        "inbox_item": set(_string_items(dispatch.get("inbox_item_ids"))),
        "project": set(_string_items(dispatch.get("project_ids"))),
        "managed_repo": set(_string_items(dispatch.get("managed_repo_ids"))),
    }
    if dispatch_id is None and cause_id is None and not any(scopes.values()):
        return True
    if dispatch_id is not None and dispatch_id in {
        _optional_text(row.get("active_dispatch_id")),
        _optional_text(row.get("dispatch_id")),
        _optional_text(row.get("source_dispatch_id")),
    }:
        return True
    if cause_id is not None and cause_id in {
        _optional_text(row.get("id")),
        _optional_text(row.get("source_id")),
        _optional_text(row.get("inbox_item_id")),
        _optional_text(row.get("blocker_id")),
        _optional_text(row.get("review_request_id")),
        _optional_text(row.get("task_id")),
    }:
        return True
    for prefix, ids in scopes.items():
        if not ids:
            continue
        singular = _optional_text(row.get(f"{prefix}_id"))
        if singular in ids:
            return True
        if ids.intersection(_string_items(row.get(f"{prefix}_ids"))):
            return True
    links = row.get("links")
    if isinstance(links, Mapping):
        link_keys = {
            "task": "task_ids",
            "blocker": "blocker_ids",
            "review_request": "review_request_ids",
            "inbox_item": "inbox_item_ids",
            "project": "project_ids",
            "managed_repo": "managed_repo_ids",
        }
        if any(
            ids.intersection(_string_items(links.get(link_keys[prefix])))
            for prefix, ids in scopes.items()
            if ids
        ):
            return True
        if dispatch_id is not None and dispatch_id in _string_items(
            links.get("dispatch_ids")
        ):
            return True
    return False


def _limited_release_packages(
    releases: list[Mapping[str, Any]],
    omitted_counts: dict[str, int],
) -> dict[str, dict[str, Any]]:
    rows = sorted((_compact_release_package(row) for row in releases), key=_release_sort_key)
    limited = rows[:MAX_RELEASE_PACKAGES]
    _add_omitted(omitted_counts, "release_packages", len(rows) - len(limited))
    result: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(limited):
        release_id = _optional_text(row.get("id")) or f"release-{index}"
        result[release_id] = row
    return result


def _manager_fallback_limited_rows(
    rows: list[dict[str, Any]],
    limit: int,
    count_key: str,
    omitted_counts: dict[str, Any],
    *,
    key: Any,
) -> list[dict[str, Any]]:
    sorted_rows = sorted(rows, key=key)
    limited = sorted_rows[:limit]
    if len(sorted_rows) > limit:
        omitted_counts[count_key] = _positive_int(omitted_counts.get(count_key)) + (
            len(sorted_rows) - limit
        )
    return limited


def _limited_rows(
    rows: list[dict[str, Any]],
    limit: int,
    omitted_counts: dict[str, int],
    count_key: str,
) -> list[dict[str, Any]]:
    limited = rows[:limit]
    _add_omitted(omitted_counts, count_key, len(rows) - len(limited))
    return limited


def _limited_values(
    values: list[str],
    limit: int,
    omitted_counts: dict[str, int],
    count_key: str,
) -> list[str]:
    limited = values[:limit]
    _add_omitted(omitted_counts, count_key, len(values) - len(limited))
    return limited


def _limited_row_values(value: object, limit: int) -> list[str]:
    return _string_items(value)[:limit]


def _copy_row_omitted_count(row: dict[str, Any], key: str, value: object) -> None:
    values = _string_items(value)
    if len(values) > ID_LIST_LIMIT:
        row[f"{key}_omitted_count"] = len(values) - ID_LIST_LIMIT


def _compact_fields(source: Mapping[str, Any], fields: Sequence[str]) -> dict[str, Any]:
    return {field: _json_value(source.get(field)) for field in fields if field in source}


def _mapping_rows(value: object) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        rows: list[Mapping[str, Any]] = []
        for key, item in sorted(value.items(), key=lambda entry: str(entry[0])):
            if isinstance(item, Mapping):
                row = dict(item)
                row.setdefault("id", str(key))
                rows.append(row)
        return rows
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [item for item in value if isinstance(item, Mapping)]
    return []


def _mapping_or_empty(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _string_items(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        result = []
        for item in value:
            text = _optional_text(item)
            if text is not None:
                result.append(text)
        return result
    text = _optional_text(value)
    return [text] if text is not None else []


def _json_value(value: object) -> Any:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Enum):
        return _json_value(value.value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, set):
        return sorted((_json_value(item) for item in value), key=str)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_value(item) for item in value]
    return str(value)


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(_json_value(value)).strip()
    return text or None


def _trim_text(value: object, limit: int = TEXT_LIMIT) -> str:
    text = _optional_text(value) or ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _drop_empty_lists(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value != []}


def _add_omitted(counts: dict[str, int], key: str, count: int) -> None:
    if count > 0:
        counts[key] = counts.get(key, 0) + count


def _positive_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    try:
        return max(0, int(str(value)))
    except (TypeError, ValueError):
        return 0


def _priority_rank(value: object) -> int:
    if isinstance(value, bool):
        return 99
    if isinstance(value, int):
        return value
    text = _optional_text(value)
    order = {"high": 0, "normal": 1, "low": 2}
    return order.get(text or "", 99)


def _state_rank(value: object) -> int:
    order = {
        "blocked": 0,
        "in_progress": 1,
        "pending": 2,
        "waiting": 3,
        "waiting_for_review": 4,
        "open": 5,
        "triage": 6,
        "accepted": 7,
        "finished": 8,
        "resolved": 9,
        "rejected": 10,
        "closed": 11,
    }
    return order.get(_optional_text(value) or "", 99)


def _severity_rank(value: object) -> int:
    order = {"critical": 0, "warning": 1, "info": 2}
    return order.get(_optional_text(value) or "", 99)


def _sort_text(value: object) -> str:
    return _optional_text(value) or ""


def _task_sort_key(row: Mapping[str, Any]) -> tuple[int, int, int, str, str]:
    dependency_status = _optional_text(row.get("dependency_status"))
    state = _optional_text(row.get("state"))
    attention_rank = (
        0
        if state == "blocked" or dependency_status == "blocked"
        else 1
        if dependency_status == "satisfied" or _open_subtask_count(row) > 0
        else 2
    )
    return (
        attention_rank,
        _priority_rank(row.get("priority")),
        _state_rank(state),
        _sort_text(row.get("updated_at") or row.get("created_at")),
        _sort_text(row.get("id")),
    )


def _inbox_sort_key(row: Mapping[str, Any]) -> tuple[int, int, str, str]:
    return (
        _priority_rank(row.get("priority")),
        0 if row.get("response_requested") is True else 1,
        _sort_text(row.get("created_at")),
        _sort_text(row.get("id")),
    )


def _case_sort_key(row: Mapping[str, Any]) -> tuple[int, int, str, str]:
    return (
        _priority_rank(row.get("priority")),
        _state_rank(row.get("status")),
        _sort_text(row.get("updated_at") or row.get("created_at")),
        _sort_text(row.get("id")),
    )


def _release_sort_key(row: Mapping[str, Any]) -> tuple[int, int, str, str]:
    needs_attention = 0 if row.get("error") or row.get("next_action_hint") else 1
    return (
        needs_attention,
        _state_rank(row.get("state")),
        _sort_text(row.get("updated_at") or row.get("created_at")),
        _sort_text(row.get("id")),
    )


def _runtime_attention_sort_key(row: Mapping[str, Any]) -> tuple[int, str, str]:
    return (
        _severity_rank(row.get("severity")),
        _sort_text(row.get("kind")),
        _sort_text(row.get("id")),
    )


def _action_feedback_sort_key(row: Mapping[str, Any]) -> tuple[str, str]:
    return (
        _sort_text(row.get("created_at")),
        _sort_text(row.get("action_record_id") or row.get("dispatch_id")),
    )


def _session_health_sort_key(row: Mapping[str, Any]) -> tuple[int, str]:
    status = _optional_text(row.get("status"))
    rank = 0 if row.get("is_expired") is True else 1 if status == "missing" else 2
    return (rank, _sort_text(row.get("agent") or row.get("session_id")))


def _open_subtask_count(row: Mapping[str, Any]) -> int:
    counts = row.get("subtask_counts")
    if not isinstance(counts, Mapping):
        return 0
    return _positive_int(counts.get("open"))


__all__ = ["build_compact_wake_context", "wake_uses_fresh_context"]
