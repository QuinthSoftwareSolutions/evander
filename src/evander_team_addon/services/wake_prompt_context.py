from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
import hashlib
import json
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
MAX_CASE_INDEX = 12
MAX_EXTERNAL_SOURCE_EVIDENCE_SNAPSHOTS = 8
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
            "context_rotation_pending" in runtime or "context_clearing_policy" in runtime
        )
        if bool(runtime.get("context_rotation_pending")):
            return True
        policy = _optional_text(runtime.get("context_clearing_policy"))
        if policy == "every_wake":
            return True
        if policy == "work_lineage":
            return work_lineage_resume_thread_id(lease) is None
    if lease.get("has_prior_successful_wake") is False:
        return True
    normalized_resume_mode = _optional_text(effective_resume_mode)
    if normalized_resume_mode is not None:
        return normalized_resume_mode == "new_context"
    return not runtime_behavior_available


def work_lineage_resume_thread_id(lease: Mapping[str, Any]) -> str | None:
    runtime = lease.get("runtime")
    dispatch = lease.get("dispatch")
    if not isinstance(runtime, Mapping) or not isinstance(dispatch, Mapping):
        return None
    if _optional_text(runtime.get("context_clearing_policy")) != "work_lineage":
        return None
    if bool(runtime.get("context_rotation_pending")):
        return None
    lineage_id = _optional_text(dispatch.get("work_lineage_id"))
    bindings = runtime.get("work_lineage_thread_ids")
    if lineage_id is None or not isinstance(bindings, Mapping):
        return None
    return _optional_text(bindings.get(lineage_id))


def build_compact_wake_context(
    lease: Mapping[str, Any],
    *,
    fresh_context: bool = True,
) -> dict[str, Any]:
    """Project one self-contained current working set, independent of history.

    Caps bound previews, never obligation discovery. Overflow records retain
    actionable identities and a collection-level read reference. Deduplication
    is confined to this payload; no previous-wake fingerprints are consulted.
    """

    omitted: dict[str, int] = {}
    agent = _compact_agent(_mapping_or_empty(lease.get("agent")))
    agent.pop("display_name", None)
    dispatch = _compact_current_dispatch(lease)
    context: dict[str, Any] = {
        "agent": agent,
        "context_mode": "fresh_full" if fresh_context else "retained_current",
        "reason": _json_value(lease.get("reason")),
        "current_dispatch": dispatch,
        "superior_id": _optional_text(lease.get("superior_agent_id")),
        "subordinate_ids": sorted(set(_string_items(lease.get("subordinate_agent_ids")))),
        "retrieval": {},
    }

    def include(
        key: str,
        rows: Sequence[Mapping[str, Any]],
        compact: Any,
        limit: int,
        sort_key: Any,
        *,
        reverse: bool = False,
    ) -> None:
        unique = _unique_records(rows)
        ordered = sorted((compact(row) for row in unique), key=sort_key, reverse=reverse)
        context[key] = ordered[:limit]
        if key in RECORD_READS and ordered:
            context["retrieval"][key] = {**RECORD_READS[key], "count": len(ordered)}
        if len(ordered) > limit:
            omitted[key] = len(ordered) - limit
            context.setdefault("overflow", {})[key] = [
                _overflow_record(row) for row in ordered[limit:]
            ]

    include(
        "subordinates",
        _mapping_rows(lease.get("subordinates")),
        lambda row: _compact_agent(row, subordinate=True),
        MAX_SUBORDINATES,
        lambda row: _sort_text(row.get("id")),
    )
    include("tasks", _mapping_rows(lease.get("tasks")), _compact_task, MAX_TASKS, _task_sort_key)
    include(
        "projects",
        _mapping_rows(lease.get("projects")),
        _compact_project,
        MAX_PROJECTS,
        lambda row: _sort_text(row.get("id")),
    )
    include(
        "outcome_kinds",
        _mapping_rows(lease.get("outcome_kinds")),
        _compact_outcome_kind,
        MAX_OUTCOME_KINDS,
        lambda row: (_sort_text(row.get("effective_project_id")), _sort_text(row.get("kind"))),
    )

    inbox, skipped = _pending_inbox_rows(lease.get("inbox"))
    _add_omitted(omitted, "inbox_non_pending", skipped)
    # Deliver all urgent messages and operator directives verbatim, even when
    # more than MAX_INBOX arrive together. Acknowledgements remain explicit.
    urgent_count = sum(_urgent_inbox(row) for row in _unique_records(inbox))
    include(
        "inbox",
        inbox,
        _compact_inbox,
        max(MAX_INBOX, urgent_count),
        lambda row: (not _urgent_inbox(row), _inbox_sort_key(row)),
    )
    include(
        "blockers",
        _mapping_rows(lease.get("blockers")),
        _compact_blocker,
        MAX_BLOCKERS,
        lambda row: (_sort_text(row.get("created_at")), _sort_text(row.get("id"))),
    )
    include(
        "reviews",
        _mapping_rows(lease.get("reviews")),
        _compact_review,
        MAX_REVIEWS,
        lambda row: (_sort_text(row.get("created_at")), _sort_text(row.get("id"))),
    )
    if lease.get("reason") == "action_feedback":
        include(
            "action_feedback",
            _mapping_rows(lease.get("recent_action_feedback")),
            _compact_action_feedback,
            MAX_ACTION_FEEDBACK,
            _action_feedback_sort_key,
        )

    cases, skipped = _current_case_rows(lease.get("cases"))
    _add_omitted(omitted, "cases_non_current", skipped)
    include("cases", cases, _compact_case, MAX_CASES, _case_sort_key)
    _add_omitted(
        omitted, "cases_outside_dispatch", _positive_int(lease.get("unrelated_case_count"))
    )
    include(
        "case_index",
        _mapping_rows(lease.get("case_index")),
        _compact_case_index,
        MAX_CASE_INDEX,
        _case_sort_key,
    )
    include(
        "external_source_evidence_snapshots",
        _mapping_rows(lease.get("external_source_evidence_snapshots")),
        _compact_external_source_evidence_snapshot,
        MAX_EXTERNAL_SOURCE_EVIDENCE_SNAPSHOTS,
        _external_source_evidence_newest_sort_key,
        reverse=True,
    )
    releases, skipped = _current_release_rows(lease.get("release_packages"))
    _add_omitted(omitted, "release_packages_non_current", skipped)
    include(
        "release_packages",
        releases,
        _compact_release_package,
        MAX_RELEASE_PACKAGES,
        _release_sort_key,
    )
    context["release_packages"] = {row["id"]: row for row in context["release_packages"]}

    attention, hidden, outside = _runtime_attention_rows(
        lease.get("runtime_attention"),
        dispatch=dispatch,
        visible_release_ids={str(row.get("id")) for row in releases},
    )
    _add_omitted(omitted, "runtime_attention_hidden_failed_actions", hidden)
    _add_omitted(omitted, "runtime_attention_outside_dispatch", outside)
    attention, duplicates = _without_inbox_runtime_attention_duplicates(
        attention,
        included_inbox_items={row["id"]: row for row in context["inbox"]},
    )
    _add_omitted(omitted, "runtime_attention_duplicate_inbox", duplicates)
    include(
        "runtime_attention",
        attention,
        _compact_runtime_attention,
        MAX_RUNTIME_ATTENTION,
        _runtime_attention_sort_key,
    )

    health = _mapping_rows(lease.get("session_health"))
    actionable_health = [row for row in health if _session_health_requires_attention(row)]
    if "session_health" in lease:
        context["session_health_summary"] = {
            "checked_count": len(health),
            "attention_required_count": len(actionable_health),
            "non_actionable_omitted_count": len(health) - len(actionable_health),
        }
    include(
        "session_health",
        actionable_health,
        _compact_session_health,
        MAX_SESSION_HEALTH,
        _session_health_sort_key,
    )
    if "operator_chat_participants" in lease:
        context["operator_chat_participants_replaces_prior"] = True
        context["operator_chat_participants"] = sorted(
            (
                _compact_operator_chat_participant(row)
                for row in _unique_records(_mapping_rows(lease["operator_chat_participants"]))
            ),
            key=lambda row: _sort_text(row.get("operator_user_id")),
        )
    summary = lease.get("manager_fallback_summary")
    if isinstance(summary, Mapping):
        context["manager_fallback_summary"] = _compact_manager_fallback_summary(summary)
    overview = lease.get("supervisory_overview")
    if isinstance(overview, Mapping) and overview:
        context["supervisory_overview"] = _json_value(overview)
    repos = _deduplicated_project_repos(lease)
    if repos:
        context["project_repos"] = repos
    for key, compact in (
        ("review_artifact_imports", _compact_review_artifact_import),
        ("review_artifact_import_errors", _compact_review_artifact_error),
    ):
        rows = _unique_records([compact(row) for row in _mapping_rows(lease.get(key))])
        if rows:
            context[key] = rows
    _deduplicate_management_records(context)
    _intern_git_records(context)
    if omitted:
        context["omitted_counts"] = dict(sorted(omitted.items()))
    # Empty scope/participant lists are meaningful replacement values.
    required = {
        "tasks",
        "inbox",
        "release_packages",
        "operator_chat_participants",
        "subordinate_ids",
        "superior_id",
        "current_dispatch",
    }
    return _json_value(
        {
            key: value
            for key, value in context.items()
            if key in required or value not in (None, [], {})
        }
    )


# Shared by wake and dispatch-summary projections. IDs address durable records;
# list tools are explicitly paginated when no individual read exists.
RECORD_READS = {
    "tasks": {"tool": "team_get_task", "id_parameter": "task_id"},
    "inbox": {"tool": "team_get_inbox_item", "id_parameter": "inbox_item_id"},
    "blockers": {"tool": "team_get_blocker", "id_parameter": "blocker_id"},
    "reviews": {"tool": "team_get_review_request", "id_parameter": "review_request_id"},
    "release_packages": {"tool": "team_get_release_package", "id_parameter": "release_package_id"},
    "projects": {"tool": "team_get_project", "id_parameter": "project_id"},
    "cases": {"tool": "team_get_case", "id_parameter": "case_id"},
    "case_index": {"tool": "team_get_case", "id_parameter": "case_id"},
    "subordinates": {"tool": "team_get_agent", "id_parameter": "agent_id"},
    "session_health": {"tool": "team_get_agent", "id_parameter": "agent_id", "id_field": "agent"},
    "outcome_kinds": {"tool": "team_get_outcome_kind", "id_parameter": "definition_id"},
    "external_source_evidence_snapshots": {
        "tool": "team_get_source_evidence_snapshot",
        "id_parameter": "snapshot_id",
    },
    "runtime_attention": {"tool": "team_list_runtime_attention", "paginate": True},
    "action_feedback": {"tool": "team_list_runtime_attention", "paginate": True},
}


def compact_context_json(value: object) -> str:
    return json.dumps(_json_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _unique_records(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    # Compare complete records, not just external version, repo ID or a truncated
    # preview: two captures or bindings can share those fields and still differ.
    return [json.loads(key) for key in sorted({compact_context_json(row) for row in rows})]


def _overflow_record(row: Mapping[str, Any]) -> dict[str, Any]:
    fields = {key for key in row if key.endswith(("_id", "_ids", "_sha"))}
    fields.update(
        (
            "id",
            "agent",
            "state",
            "status",
            "priority",
            "title",
            "kind",
            "item_type",
            "response_requested",
            "next_action",
            "next_action_hint",
            "blocked_reason_code",
            "route",
            "follow_up_at",
            "summary",
            "external_version",
            "content_digest",
            "repository_snapshot",
            "reason_code",
            "resolution_scope",
        )
    )
    result = _compact_fields(row, sorted(fields.intersection(row)))
    if "payload" in row:
        result["payload"] = _compact_payload(_mapping_or_empty(row["payload"]))
    result["read_required"] = True
    return result


def _urgent_inbox(row: Mapping[str, Any]) -> bool:
    return row.get("priority") == "high" or row.get("item_type") == "operator_directive"


def _intern_git_records(context: dict[str, Any]) -> None:
    """Represent exact Git bindings and review snapshots once within a payload."""
    bindings: dict[str, Any] = {}
    snapshots: dict[str, Any] = {}

    def intern(table: dict[str, Any], row: Mapping[str, Any], prefix: str) -> str:
        key = prefix + hashlib.sha256(compact_context_json(row).encode()).hexdigest()[:20]
        table[key] = _json_value(row)
        return key

    for repo in context.get("project_repos", []):
        rows = repo.pop("git_work_contexts", [])
        if rows:
            repo["git_work_context_refs"] = sorted({intern(bindings, row, "git-") for row in rows})
        rows = repo.pop("review_repository_snapshots", [])
        if rows:
            repo["review_repository_snapshot_refs"] = []
            for row in rows:
                snapshot = {k: v for k, v in row.items() if k != "review_request_id"}
                repo["review_repository_snapshot_refs"].append(
                    {
                        "review_request_id": row.get("review_request_id"),
                        "snapshot_ref": intern(snapshots, snapshot, "review-"),
                    }
                )
    for review in [*context.get("reviews", []), *context.get("overflow", {}).get("reviews", [])]:
        snapshot = review.pop("repository_snapshot", None)
        if snapshot:
            review["repository_snapshot_ref"] = intern(snapshots, snapshot, "review-")
    if bindings:
        context["git_work_contexts"] = dict(sorted(bindings.items()))
    if snapshots:
        context["review_repository_snapshots"] = dict(sorted(snapshots.items()))


def _deduplicate_management_records(context: dict[str, Any]) -> None:
    summary = context.get("manager_fallback_summary")
    if not isinstance(summary, dict):
        return
    for key, collection in (
        ("open_work", "tasks"),
        ("review_requests", "reviews"),
        ("pending_inbox", "inbox"),
        ("projects", "projects"),
        ("agents", "subordinates"),
    ):
        records = {row.get("id"): row for row in context.get(collection, [])}
        for row in summary.get(key, []):
            shared = records.get(row.get("id"))
            if shared is None:
                continue
            for field in list(row):
                if field != "id" and field in shared and row[field] == shared[field]:
                    del row[field]
            row["record_collection"] = collection


def build_dispatch_summary(full: Mapping[str, Any]) -> dict[str, Any]:
    """Summarize an already authorized, dispatch-bound read without changing scope."""
    result: dict[str, Any] = {
        "detail": "summary",
        "dispatch": _compact_current_dispatch({"dispatch": full["dispatch"]}),
        "retrieval": {},
    }
    result["dispatch"].update(_compact_fields(full["dispatch"], ("agent_id", "session_id")))
    for key, compact in (
        ("tasks", _compact_task),
        ("blockers", _compact_blocker),
        ("reviews", _compact_review),
        ("inbox", _compact_inbox),
        ("projects", _compact_project),
        ("external_source_evidence_snapshots", _compact_external_source_evidence_snapshot),
    ):
        rows = []
        for source in _unique_records(_mapping_rows(full.get(key))):
            row = compact(source)
            if key == "tasks" and "description" in row:
                row["description_preview"] = _trim_text(row.pop("description"), 480)
            rows.append(row)
        result[key] = rows
        result["retrieval"][key] = {**RECORD_READS[key], "count": len(rows)}
    # These rows have already been restricted to the active dispatch. Never
    # resolve a task's newer/current Git context while summarizing this payload.
    result["git_work_contexts"] = _unique_records(_mapping_rows(full.get("git_work_contexts")))
    for task in result["tasks"]:
        task["git_work_context_ids"] = [
            row["id"] for row in result["git_work_contexts"] if row.get("task_id") == task.get("id")
        ]
    return result


def _compact_current_dispatch(lease: Mapping[str, Any]) -> dict[str, Any]:
    source = lease.get("dispatch")
    dispatch = source if isinstance(source, Mapping) else {}
    row = _compact_fields(
        dispatch,
        (
            "id",
            "work_lineage_id",
            "status",
            "cause_kind",
            "cause_id",
            "task_ids",
            "blocker_ids",
            "review_request_ids",
            "inbox_item_ids",
            "project_ids",
            "managed_repo_ids",
            "source_evidence_snapshot_ids",
            "review_routing_authorities",
            "chat_message_ids",
            "claimed_role_task_ids",
            "initial_release_decision_deliverable_ids",
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
) -> list[dict[str, Any]]:
    candidates = list(_mapping_rows(lease.get("project_repos")))
    project_repo = lease.get("project_repo")
    if isinstance(project_repo, Mapping):
        candidates.append(project_repo)
    result: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        compact = _compact_project_repo(candidate)
        nested_keys = ("git_work_contexts", "review_repository_snapshots")
        identity = compact_context_json(
            {key: value for key, value in compact.items() if key not in nested_keys}
        )
        shared = result.setdefault(
            identity, {key: value for key, value in compact.items() if key not in nested_keys}
        )
        for key in nested_keys:
            rows = [*shared.get(key, []), *compact.get(key, [])]
            if rows:
                shared[key] = _unique_records(rows)
    return [result[key] for key in sorted(result)]


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
            row[key] = values
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
        )
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
            "source_evidence_snapshot_ids",
            "description",
            "work_lineage_id",
            "git_base_branch",
            "git_base_commit_sha",
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
        if len(blocking_sources) > ID_LIST_LIMIT:
            row["blocking_sources_omitted_count"] = len(blocking_sources) - ID_LIST_LIMIT
    return row


def _compact_external_source_evidence_snapshot(
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    row = _compact_fields(
        snapshot,
        (
            "id",
            "team_id",
            "project_id",
            "provider_type",
            "source_type",
            "external_identifier",
            "external_version",
            "fetched_at",
            "expires_at",
            "freshness_status",
            "effective_freshness_status",
            "access_classification",
            "content_digest",
            "created_at",
        ),
    )
    title = _optional_text(snapshot.get("title"))
    if title is not None:
        row["title"] = _trim_text(title, TITLE_LIMIT)
    body = snapshot.get("body")
    if isinstance(body, Mapping):
        row["body_excerpt"] = _trim_text(body.get("content"), 320)
        row["body_character_count"] = len(str(body.get("content") or ""))
    for key in ("acceptance_criteria", "discussion", "links"):
        row[f"{key}_count"] = len(_mapping_rows(snapshot.get(key)))
    row["read_required"] = True
    return row


def _external_source_evidence_newest_sort_key(
    snapshot: Mapping[str, Any],
) -> tuple[tuple[int, float, str], tuple[int, float, str], str]:
    return (
        _absolute_timestamp_sort_key(snapshot.get("fetched_at")),
        _absolute_timestamp_sort_key(snapshot.get("created_at")),
        _sort_text(snapshot.get("id")),
    )


def _absolute_timestamp_sort_key(value: object) -> tuple[int, float, str]:
    """Sort offset-aware timestamps by instant, with deterministic invalid fallback."""

    parsed: datetime | None = None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            parsed = None
    if parsed is not None and parsed.tzinfo is not None:
        return (1, parsed.astimezone(timezone.utc).timestamp(), "")
    return (0, 0.0, _sort_text(value))


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
        compact_payload = (
            _json_value(payload)
            if _urgent_inbox(item)
            else _compact_payload(payload, text_limit=payload_text_limit)
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
            "source_evidence_snapshot_ids",
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


def _compact_case_index(case: Mapping[str, Any]) -> dict[str, Any]:
    row = _compact_fields(
        case,
        (
            "id",
            "project_id",
            "status",
            "priority",
        ),
    )
    row["title"] = _trim_text(case.get("title"), TITLE_LIMIT)
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
            "work_lineage_id",
            "supersedes_release_package_id",
            "review_request_ids",
            "verification_status",
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
            "reason_code",
            "resolution_scope",
            "affected_task_ids",
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
    execution_status = (_optional_text(row.get("agent_execution_status")) or "").casefold()
    lifecycle = (_optional_text(row.get("container_lifecycle_state")) or "").casefold()
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
                    "authored_by",
                    "work_item_attributions",
                    "git_base_branch",
                ),
            )
            for item in _mapping_rows(summary.get("pending_initial_release_decisions"))
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
    row["retrieval"] = {
        "agents": {"tool": "team_list_agents", "paginate": True},
        "open_work": {"tool": "team_list_tasks", "paginate": True},
        "deliverables": {"tool": "team_list_deliverables", "paginate": True},
        "pending_initial_release_decisions": {"tool": "team_list_deliverables", "paginate": True},
        "review_requests": {"tool": "team_list_review_requests", "paginate": True},
        "pending_inbox": {"tool": "team_list_inbox", "paginate": True},
        "routing_gaps": {"tool": "team_list_runtime_attention", "paginate": True},
    }
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
            "commit_sha",
            "pinned_ref",
            "pinned_commit_sha",
        ),
    )
    git_contexts = [
        _compact_git_work_context(row) for row in _mapping_rows(repo.get("git_work_contexts"))
    ]
    if git_contexts:
        row["git_work_contexts"] = git_contexts
    review_snapshots = [
        _compact_fields(
            snapshot,
            (
                "review_request_id",
                "managed_repo_id",
                "directory_path",
                "commit_sha",
                "pinned_ref",
                "source_kind",
                "source_ref",
            ),
        )
        for snapshot in _mapping_rows(repo.get("review_repository_snapshots"))
    ]
    if review_snapshots:
        row["review_repository_snapshots"] = review_snapshots
    if len(git_contexts) == 1 and git_contexts[0].get("state") == "active":
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
            "reviewed_commit_sha",
            "created_at",
        ),
    )
    instructions = _optional_text(review.get("instructions"))
    if instructions is not None:
        row["instructions"] = instructions
    repository_snapshot = review.get("repository_snapshot")
    if isinstance(repository_snapshot, Mapping):
        row["repository_snapshot"] = _compact_fields(
            repository_snapshot,
            (
                "managed_repo_id",
                "directory_path",
                "commit_sha",
                "pinned_ref",
                "source_kind",
                "source_ref",
            ),
        )
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
            "base_commit",
            "preparation_generation",
            "dependency_composition_recovery_id",
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
        elif key.endswith(("_id", "_sha", "_ref", "_branch")) or key in _PAYLOAD_SCALAR_KEYS:
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
    for key in (
        "next_action",
        "next_action_hint",
        "blocker_id",
        "release_package_id",
        "review_request_id",
        "deliverable_id",
        "follow_up_at",
        "error",
    ):
        if attention.get(key) is not None and key not in inbox_item:
            inbox_item[key] = _json_value(attention[key])


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
        if dispatch_id is not None and dispatch_id in _string_items(links.get("dispatch_ids")):
            return True
    return False


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
