"""Filter Team read payloads to the authorized agent and project scope."""

from __future__ import annotations

from collections.abc import (
    Mapping,
)
from typing import (
    Any,
)

from .human_review_projection import (
    apply_compact_human_review_projection,
)
from .models import (
    filter_work_lineage_payloads,
    filter_work_lineage_references,
    team_state_payload,
)
from .provider_values import (
    _optional_text,
    _required_text,
)
from .services.domain.task_assessment import (
    assess_tasks,
    task_payload_with_assessment,
)


def _filtered_team_state_payload(
    state: Any,
    *,
    team_id: str | None,
    project_id: str | None,
    agent_id: str | None,
    session_records: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    filtered = team_state_payload(
        state,
        include_agent_avatars=False,
        session_records=session_records,
    )
    apply_compact_human_review_projection(filtered, state)
    filtered["memories"] = {}
    # Large/private context records are available only through scoped detail APIs.
    filtered["notes"] = {}
    filtered["case_context_views"] = {}
    filtered["case_context_snapshots"] = {}
    teams = filtered["teams"]
    projects = filtered["projects"]
    agents = filtered["agents"]
    project_assignments = filtered.get("project_assignments", {})

    visible_team_ids = set(teams)
    visible_project_ids = set(projects)
    visible_agent_ids = set(agents)

    if team_id is not None:
        visible_team_ids &= {team_id}
        visible_project_ids = {
            key for key, value in projects.items() if value.get("team_id") in visible_team_ids
        }
        visible_agent_ids = {
            key for key, value in agents.items() if value.get("team_id") in visible_team_ids
        }

    if project_id is not None:
        if project_id in visible_project_ids and project_id in projects:
            visible_project_ids = {project_id}
            visible_team_ids &= {_required_text(projects[project_id].get("team_id"), "team_id")}
            visible_agent_ids = {
                key
                for key in visible_agent_ids
                if any(
                    assignment.get("agent_id") == key and assignment.get("project_id") == project_id
                    for assignment in project_assignments.values()
                )
            }
        else:
            visible_team_ids = set()
            visible_project_ids = set()
            visible_agent_ids = set()

    if agent_id is not None:
        if agent_id in visible_agent_ids and agent_id in agents:
            agent = agents[agent_id]
            visible_agent_ids = {agent_id}
            agent_team_id = _optional_text(agent.get("team_id"))
            visible_team_ids = (
                visible_team_ids & {agent_team_id} if agent_team_id is not None else set()
            )
            agent_project_ids = {
                _required_text(assignment.get("project_id"), "project_id")
                for assignment in project_assignments.values()
                if assignment.get("agent_id") == agent_id and assignment.get("project_id")
            }
            visible_project_ids = visible_project_ids & agent_project_ids
        else:
            visible_team_ids = set()
            visible_project_ids = set()
            visible_agent_ids = set()

    filtered["teams"] = {
        key: {**value, "project_ids": _filter_ids(value.get("project_ids"), visible_project_ids)}
        for key, value in teams.items()
        if key in visible_team_ids
    }
    filtered["projects"] = {
        key: value for key, value in projects.items() if key in visible_project_ids
    }
    filtered["agents"] = {
        key: {
            **value,
            "reports_to_agent_id": value.get("reports_to_agent_id")
            if value.get("reports_to_agent_id") in visible_agent_ids
            else None,
            "subordinate_agent_ids": _filter_ids(
                value.get("subordinate_agent_ids"),
                visible_agent_ids,
            ),
        }
        for key, value in agents.items()
        if key in visible_agent_ids
    }
    filtered["project_assignments"] = {
        key: value
        for key, value in project_assignments.items()
        if value.get("project_id") in visible_project_ids
        and value.get("agent_id") in visible_agent_ids
    }
    visible_role_ids = {
        role_id
        for value in filtered["agents"].values()
        if (role_id := _optional_text(value.get("role_id"))) is not None
    }
    visible_role_ids.update(
        role_id
        for value in filtered["project_assignments"].values()
        if (role_id := _optional_text(value.get("role_id"))) is not None
    )
    filtered["agent_runtimes"] = {
        key: value
        for key, value in filtered.get("agent_runtimes", {}).items()
        if key in visible_agent_ids
    }
    filtered["tasks"] = {
        key: value
        for key, value in filtered["tasks"].items()
        if value.get("project_id") in visible_project_ids
        and value.get("owner_agent_id") in visible_agent_ids
    }
    visible_task_ids = set(filtered["tasks"])
    filtered["git_work_contexts"] = {
        key: value
        for key, value in filtered.get("git_work_contexts", {}).items()
        if value.get("agent_id") in visible_agent_ids and value.get("task_id") in visible_task_ids
    }
    filtered["cases"] = {
        key: {
            **value,
            "task_ids": _filter_ids(value.get("task_ids"), visible_task_ids),
        }
        for key, value in filtered.get("cases", {}).items()
        if value.get("team_id") in visible_team_ids
        and (value.get("project_id") is None or value.get("project_id") in visible_project_ids)
        and (
            agent_id is None
            or value.get("owner_agent_id") in visible_agent_ids
            or any(task_id in visible_task_ids for task_id in _string_items(value.get("task_ids")))
        )
    }
    visible_case_ids = set(filtered["cases"])
    visible_source_evidence_snapshot_ids: set[str] | None = None
    if agent_id is not None:
        visible_source_evidence_snapshot_ids = {
            snapshot_id
            for value in (*filtered["tasks"].values(), *filtered["cases"].values())
            for snapshot_id in _string_items(value.get("source_evidence_snapshot_ids"))
        }
        visible_source_evidence_snapshot_ids.update(
            snapshot_id
            for dispatch in state.dispatches.values()
            if dispatch.agent_id in visible_agent_ids
            for snapshot_id in dispatch.source_evidence_snapshot_ids
        )
    filtered["external_source_evidence_snapshots"] = {
        key: value
        for key, value in filtered.get(
            "external_source_evidence_snapshots",
            {},
        ).items()
        if value.get("team_id") in visible_team_ids
        and (value.get("project_id") is None or value.get("project_id") in visible_project_ids)
        and (
            visible_source_evidence_snapshot_ids is None
            or key in visible_source_evidence_snapshot_ids
        )
    }
    filtered["deliverables"] = {
        key: value
        for key, value in filtered.get("deliverables", {}).items()
        if value.get("task_id") in visible_task_ids
        and value.get("producer_agent_id") in visible_agent_ids
    }
    visible_deliverable_ids = set(filtered["deliverables"])
    filtered["review_requests"] = {
        key: value
        for key, value in filtered.get("review_requests", {}).items()
        if value.get("deliverable_id") in visible_deliverable_ids
        and value.get("reviewer_agent_id") in visible_agent_ids
    }
    filtered["outcome_kind_definitions"] = {
        key: value
        for key, value in filtered.get(
            "outcome_kind_definitions",
            {},
        ).items()
        if value.get("team_id") in visible_team_ids
        and (value.get("project_id") is None or value.get("project_id") in visible_project_ids)
    }
    filtered["review_profiles"] = {
        key: {
            **value,
            "applicable_role_ids": _filter_ids(
                value.get("applicable_role_ids"),
                visible_role_ids,
            ),
        }
        for key, value in filtered.get("review_profiles", {}).items()
        if value.get("team_id") in visible_team_ids
        and (value.get("project_id") is None or value.get("project_id") in visible_project_ids)
    }
    for profile in filtered["review_profiles"].values():
        visible_role_ids.update(_string_items(profile.get("applicable_role_ids")))
    filtered["roles"] = {
        key: value
        for key, value in filtered.get("roles", {}).items()
        if key in visible_role_ids
        or (
            value.get("team_id") in visible_team_ids
            and (value.get("project_id") is None or value.get("project_id") in visible_project_ids)
        )
    }
    visible_review_ids = set(filtered["review_requests"])
    filtered["deliverables"] = {
        key: {
            **value,
            "review_request_ids": _filter_ids(value.get("review_request_ids"), visible_review_ids),
        }
        for key, value in filtered["deliverables"].items()
    }
    filtered["tasks"] = {
        key: {
            **value,
            "deliverable_ids": _filter_ids(value.get("deliverable_ids"), visible_deliverable_ids),
            "case_id": value.get("case_id") if value.get("case_id") in visible_case_ids else None,
            # Keep the graph self-consistent within the scoped view: drop edges that
            # point at tasks the caller cannot see. dependency_status and
            # subtask_counts are intentionally left as computed from the full state
            # so they reflect global truth even when some edges are hidden here.
            "parent_task_id": value.get("parent_task_id")
            if value.get("parent_task_id") in visible_task_ids
            else None,
            "dependency_task_ids": _filter_ids(value.get("dependency_task_ids"), visible_task_ids),
            "child_task_ids": _filter_ids(value.get("child_task_ids"), visible_task_ids),
            "blocked_by_task_ids": _filter_ids(value.get("blocked_by_task_ids"), visible_task_ids),
        }
        for key, value in filtered["tasks"].items()
    }
    filtered["inbox"] = {
        key: value
        for key, value in filtered.get("inbox", {}).items()
        if (
            value.get("recipient_agent_id") in visible_agent_ids
            or value.get("sender_agent_id") in visible_agent_ids
        )
        and (value.get("task_id") is None or value.get("task_id") in visible_task_ids)
    }
    filtered["action_records"] = {
        key: value
        for key, value in filtered.get("action_records", {}).items()
        if value.get("agent_id") in visible_agent_ids
    }
    filtered["conversations"] = {
        key: value
        for key, value in filtered.get("conversations", {}).items()
        if value.get("agent_id") in visible_agent_ids
    }
    visible_conversation_ids = set(filtered["conversations"])
    filtered["conversation_messages"] = {
        key: value
        for key, value in filtered.get("conversation_messages", {}).items()
        if value.get("conversation_id") in visible_conversation_ids
    }
    visible_conversation_message_ids = set(filtered["conversation_messages"])
    filtered["release_packages"] = {
        key: {
            **value,
            "deliverable_ids": _filter_ids(value.get("deliverable_ids"), visible_deliverable_ids),
        }
        for key, value in filtered.get("release_packages", {}).items()
        if value.get("project_id") in visible_project_ids
        and all(
            deliverable_id in visible_deliverable_ids
            for deliverable_id in _string_items(value.get("deliverable_ids"))
        )
    }
    visible_release_ids = set(filtered["release_packages"])
    visible_managed_repo_ids = {
        managed_repo_id
        for value in filtered["projects"].values()
        if isinstance(git := value.get("git"), Mapping)
        if (managed_repo_id := _optional_text(git.get("managed_repo_id"))) is not None
    }
    scoped_dispatch_payloads = {
        key: value
        for key, value in filtered.get("dispatches", {}).items()
        if value.get("agent_id") in visible_agent_ids
        and (
            project_id is None
            or bool(set(_string_items(value.get("project_ids"))).intersection(visible_project_ids))
        )
        and (
            team_id is None
            or not value.get("project_ids")
            or bool(set(_string_items(value.get("project_ids"))).intersection(visible_project_ids))
        )
        and (
            agent_id is None
            or not value.get("project_ids")
            or bool(set(_string_items(value.get("project_ids"))).intersection(visible_project_ids))
        )
    }
    filtered["dispatches"] = {
        key: {
            **value,
            "task_ids": _filter_ids(value.get("task_ids"), visible_task_ids),
            "review_request_ids": _filter_ids(
                value.get("review_request_ids"),
                visible_review_ids,
            ),
            "inbox_item_ids": _filter_ids(value.get("inbox_item_ids"), set(filtered["inbox"])),
            "project_ids": _filter_ids(value.get("project_ids"), visible_project_ids),
            "managed_repo_ids": _filter_ids(
                value.get("managed_repo_ids"),
                visible_managed_repo_ids,
            ),
            "source_evidence_snapshot_ids": _filter_ids(
                value.get("source_evidence_snapshot_ids"),
                set(filtered["external_source_evidence_snapshots"]),
            ),
            "chat_message_ids": _filter_ids(
                value.get("chat_message_ids"),
                visible_conversation_message_ids,
            ),
            "initial_release_decision_deliverable_ids": _filter_ids(
                value.get("initial_release_decision_deliverable_ids"),
                visible_deliverable_ids,
            ),
            "review_routing_authorities": [
                {
                    **authority,
                    "consumed_at": (
                        authority.get("consumed_at")
                        if authority.get("consumed_by_review_request_id") in visible_review_ids
                        else None
                    ),
                    "consumed_by_review_request_id": (
                        authority.get("consumed_by_review_request_id")
                        if authority.get("consumed_by_review_request_id") in visible_review_ids
                        else None
                    ),
                }
                for authority in _mapping_items(value.get("review_routing_authorities"))
                if authority.get("reviewer_agent_id") in visible_agent_ids
                and authority.get("project_id") in visible_project_ids
                and authority.get("task_id") in visible_task_ids
                and authority.get("deliverable_id") in visible_deliverable_ids
            ],
        }
        for key, value in scoped_dispatch_payloads.items()
    }
    visible_dispatch_ids = set(filtered["dispatches"])
    visible_human_review_ids = {
        request.id
        for request in state.human_review_requests.values()
        if request.release_package_id in visible_release_ids
    }
    filtered["blockers"] = {
        key: _scoped_blocker_payload(
            value,
            visible_task_ids=visible_task_ids,
            visible_agent_ids=visible_agent_ids,
            visible_dispatch_ids=visible_dispatch_ids,
        )
        for key, value in filtered.get("blockers", {}).items()
        if _blocker_payload_is_visible(
            value,
            visible_team_ids=visible_team_ids,
            visible_project_ids=visible_project_ids,
            visible_agent_ids=visible_agent_ids,
            visible_task_ids=visible_task_ids,
            visible_deliverable_ids=visible_deliverable_ids,
            visible_review_ids=visible_review_ids,
            visible_release_ids=visible_release_ids,
            visible_human_review_ids=visible_human_review_ids,
        )
    }
    visible_blocker_ids = set(filtered["blockers"])
    visible_dispatch_cause_ids = {
        "task": visible_task_ids,
        "blocker": visible_blocker_ids,
        "review": visible_review_ids,
        "inbox": set(filtered["inbox"]),
        "low_priority_inbox": set(filtered["inbox"]),
        "team_chat": visible_conversation_message_ids,
        "action_feedback": set(filtered["action_records"]),
    }
    visible_wake_work_record_ids = {
        "inbox": set(filtered["inbox"]),
        "review": visible_review_ids,
        "open_work": visible_task_ids,
        "deliverables": visible_deliverable_ids,
        "review_requests": visible_review_ids,
        "pending_inbox": set(filtered["inbox"]),
        "latest_failed_actions": set(filtered["action_records"]),
        "pending_initial_release_decisions": visible_deliverable_ids,
    }
    filtered["dispatches"] = {
        key: {
            **value,
            "blocker_ids": _filter_ids(value.get("blocker_ids"), visible_blocker_ids),
            "action_feedback_dispatch_ids": _filter_ids(
                value.get("action_feedback_dispatch_ids"),
                visible_dispatch_ids,
            ),
            "wake_work_record_fingerprints": _filter_wake_work_record_fingerprints(
                value.get("wake_work_record_fingerprints"),
                visible_ids_by_kind=visible_wake_work_record_ids,
            ),
            "cause_id": (
                value.get("cause_id")
                if value.get("cause_id") is None
                or (
                    value.get("cause_kind") in visible_dispatch_cause_ids
                    and value.get("cause_id")
                    in visible_dispatch_cause_ids[str(value.get("cause_kind"))]
                )
                else None
            ),
        }
        for key, value in filtered["dispatches"].items()
    }
    visible_lineage_ids = {
        lineage_id
        for collection_name in ("tasks", "release_packages")
        for value in filtered[collection_name].values()
        if (lineage_id := _optional_text(value.get("work_lineage_id"))) is not None
    }
    if project_id is None:
        visible_lineage_ids.update(
            lineage_id
            for dispatch_id, raw_dispatch in scoped_dispatch_payloads.items()
            if (scoped_dispatch := filtered["dispatches"].get(dispatch_id)) is not None
            and _dispatch_work_scope_survives(raw_dispatch, scoped_dispatch)
            if (lineage_id := _optional_text(raw_dispatch.get("work_lineage_id"))) is not None
        )
    filtered["work_lineages"] = filter_work_lineage_payloads(
        filtered.get("work_lineages"),
        visible_conversation_ids=visible_conversation_ids,
        visible_lineage_ids=visible_lineage_ids,
    )
    filtered["blocker_events"] = {
        key: _scoped_blocker_event_payload(
            value,
            visible_agent_ids=visible_agent_ids,
            visible_dispatch_ids=visible_dispatch_ids,
        )
        for key, value in filtered.get("blocker_events", {}).items()
        if value.get("blocker_id") in visible_blocker_ids
    }
    assessments = assess_tasks(state)
    filtered["tasks"] = {
        task_id: {
            **task_payload_with_assessment(
                state,
                state.tasks[task_id],
                assessment=assessments[task_id],
                assessments=assessments,
                visible_task_ids=visible_task_ids,
                visible_deliverable_ids=visible_deliverable_ids,
                visible_review_request_ids=visible_review_ids,
                visible_blocker_ids=visible_blocker_ids,
                visible_agent_ids=visible_agent_ids,
            ),
            "case_id": (
                state.tasks[task_id].case_id
                if state.tasks[task_id].case_id in visible_case_ids
                else None
            ),
        }
        for task_id in sorted(visible_task_ids)
    }
    filtered["git_promotions"] = {
        key: value
        for key, value in filtered.get("git_promotions", {}).items()
        if value.get("project_id") in visible_project_ids
        and (value.get("task_id") is None or value.get("task_id") in visible_task_ids)
        and (
            value.get("deliverable_id") is None
            or value.get("deliverable_id") in visible_deliverable_ids
        )
        and (
            value.get("release_package_id") is None
            or value.get("release_package_id") in visible_release_ids
        )
    }
    visible_git_promotion_ids = set(filtered["git_promotions"])
    filtered["deliverables"] = {
        key: _filter_deliverable_git_refs(value, visible_git_promotion_ids)
        for key, value in filtered["deliverables"].items()
    }
    filtered["release_packages"] = {
        key: {
            **value,
            "git_promotion_record_ids": _filter_ids(
                value.get("git_promotion_record_ids"),
                visible_git_promotion_ids,
            ),
        }
        for key, value in filtered["release_packages"].items()
    }
    filtered["ephemeral_assignments"] = {
        key: value
        for key, value in filtered.get("ephemeral_assignments", {}).items()
        if value.get("parent_agent_id") in visible_agent_ids
        and value.get("project_id") in visible_project_ids
        and value.get("task_id") in visible_task_ids
    }
    filtered["runtime_operations"] = _filter_runtime_operations(
        filtered.get("runtime_operations"),
        visible_agent_ids=visible_agent_ids,
        visible_project_ids=visible_project_ids,
        visible_task_ids=visible_task_ids,
        visible_deliverable_ids=visible_deliverable_ids,
        visible_inbox_ids=set(filtered["inbox"]),
        visible_action_ids=set(filtered["action_records"]),
        visible_release_ids=visible_release_ids,
        visible_git_promotion_ids=visible_git_promotion_ids,
        visible_assignment_ids=set(filtered["ephemeral_assignments"]),
        visible_dispatch_ids=visible_dispatch_ids,
    )
    visible_session_ids = {
        session_id
        for value in filtered["agents"].values()
        if (session_id := _optional_text(value.get("session_id"))) is not None
    }
    visible_dispatch_ids = set(filtered["dispatches"])
    visible_stand_down_ids = {
        stand_down_id
        for value in filtered["agents"].values()
        for item in _mapping_items(value.get("effective_stand_downs"))
        if (stand_down_id := _optional_text(item.get("id"))) is not None
    }
    filtered_activity_summaries = _filter_agent_activity_summaries(
        filtered.get("agent_activity_summaries"),
        visible_agent_ids=visible_agent_ids,
        visible_session_ids=visible_session_ids,
        visible_stand_down_ids=visible_stand_down_ids,
        visible_task_ids=visible_task_ids,
        visible_deliverable_ids=visible_deliverable_ids,
        visible_inbox_ids=set(filtered["inbox"]),
        visible_action_ids=set(filtered["action_records"]),
        visible_release_ids=visible_release_ids,
        visible_git_promotion_ids=visible_git_promotion_ids,
        visible_assignment_ids=set(filtered["ephemeral_assignments"]),
        visible_dispatch_ids=visible_dispatch_ids,
    )
    filtered["agent_activity_summaries"] = filtered_activity_summaries
    filtered["agents"] = {
        key: _agent_payload_with_filtered_activity_summary(
            value,
            filtered_activity_summaries.get(key),
        )
        for key, value in filtered["agents"].items()
    }
    filtered["agent_runtimes"] = {
        key: {
            **value,
            "active_dispatch_id": (
                value.get("active_dispatch_id")
                if value.get("active_dispatch_id") in visible_dispatch_ids
                else None
            ),
        }
        for key, value in filtered["agent_runtimes"].items()
    }
    # Several collections above are rebuilt from the full state after their
    # initial scope pass. Enforce lineage referential closure only after those
    # projections and the final dispatch update are complete.
    filter_work_lineage_references(
        filtered,
        visible_lineage_ids=set(filtered["work_lineages"]),
    )
    return filtered


def _filter_agent_activity_summaries(
    value: object,
    *,
    visible_agent_ids: set[str],
    visible_session_ids: set[str],
    visible_stand_down_ids: set[str],
    visible_task_ids: set[str],
    visible_deliverable_ids: set[str],
    visible_inbox_ids: set[str],
    visible_action_ids: set[str],
    visible_release_ids: set[str],
    visible_git_promotion_ids: set[str],
    visible_assignment_ids: set[str],
    visible_dispatch_ids: set[str],
) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    allowed_by_type = {
        "agent": visible_agent_ids,
        "session": visible_session_ids,
        "stand_down": visible_stand_down_ids,
        "task": visible_task_ids,
        "deliverable": visible_deliverable_ids,
        "inbox": visible_inbox_ids,
        "action": visible_action_ids,
        "release": visible_release_ids,
        "promotion": visible_git_promotion_ids,
        "assignment": visible_assignment_ids,
        "dispatch": visible_dispatch_ids,
    }
    return {
        agent_id: dict(summary)
        for agent_id, summary in value.items()
        if agent_id in visible_agent_ids
        and isinstance(summary, dict)
        and _activity_summary_refs_are_visible(summary.get("reason_refs"), allowed_by_type)
    }


def _activity_summary_refs_are_visible(
    refs: object,
    allowed_by_type: Mapping[str, set[str]],
) -> bool:
    if not isinstance(refs, list) or not refs:
        return False
    for ref in refs:
        if not isinstance(ref, dict):
            return False
        ref_type = _optional_text(ref.get("type"))
        ref_id = _optional_text(ref.get("id"))
        if ref_type is None or ref_id is None:
            return False
        allowed_ids = allowed_by_type.get(ref_type)
        if allowed_ids is None or ref_id not in allowed_ids:
            return False
    return True


def _agent_payload_with_filtered_activity_summary(
    value: dict[str, Any],
    summary: dict[str, Any] | None,
) -> dict[str, Any]:
    payload = dict(value)
    if summary is None:
        payload.pop("activity_summary", None)
    else:
        payload["activity_summary"] = summary
    return payload


def _filter_runtime_operations(
    value: object,
    *,
    visible_agent_ids: set[str],
    visible_project_ids: set[str],
    visible_task_ids: set[str],
    visible_deliverable_ids: set[str],
    visible_inbox_ids: set[str],
    visible_action_ids: set[str],
    visible_release_ids: set[str],
    visible_git_promotion_ids: set[str],
    visible_assignment_ids: set[str],
    visible_dispatch_ids: set[str],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"generated_at": None, "agents": [], "attention": []}
    agents = [
        {
            **row,
            "active_dispatch_id": (
                row.get("active_dispatch_id")
                if row.get("active_dispatch_id") in visible_dispatch_ids
                else None
            ),
        }
        for row in _mapping_items(value.get("agents"))
        if _optional_text(row.get("id")) in visible_agent_ids
    ]
    attention = [
        _scoped_runtime_attention_row(
            row,
            visible_task_ids=visible_task_ids,
            visible_dispatch_ids=visible_dispatch_ids,
        )
        for row in _mapping_items(value.get("attention"))
        if _runtime_row_is_visible(
            row,
            visible_agent_ids=visible_agent_ids,
            visible_project_ids=visible_project_ids,
            visible_task_ids=visible_task_ids,
            visible_deliverable_ids=visible_deliverable_ids,
            visible_inbox_ids=visible_inbox_ids,
            visible_action_ids=visible_action_ids,
            visible_release_ids=visible_release_ids,
            visible_git_promotion_ids=visible_git_promotion_ids,
            visible_assignment_ids=visible_assignment_ids,
        )
    ]
    session_health = [
        row
        for row in _mapping_items(value.get("session_health"))
        if _optional_text(row.get("agent_id")) in visible_agent_ids
    ]
    return {
        "generated_at": value.get("generated_at"),
        "agents": agents,
        "attention": attention,
        "session_health": session_health,
    }


def _scoped_runtime_attention_row(
    row: dict[str, Any],
    *,
    visible_task_ids: set[str],
    visible_dispatch_ids: set[str],
) -> dict[str, Any]:
    payload = dict(row)
    if (
        payload.get("dispatch_id") is not None
        and payload.get("dispatch_id") not in visible_dispatch_ids
    ):
        payload["dispatch_id"] = None
    if payload.get("kind") not in {
        "blocked_task",
        "waiting_dependency_task",
    }:
        return payload
    payload["blocked_by_task_ids"] = _filter_ids(
        payload.get("blocked_by_task_ids"),
        visible_task_ids,
    )
    detail = _optional_text(payload.get("detail"))
    if detail is not None:
        payload["detail"] = "; ".join(
            part for part in detail.split("; ") if not part.startswith("depends on ")
        )
    return payload


def _runtime_row_is_visible(
    row: dict[str, Any],
    *,
    visible_agent_ids: set[str],
    visible_project_ids: set[str],
    visible_task_ids: set[str],
    visible_deliverable_ids: set[str],
    visible_inbox_ids: set[str],
    visible_action_ids: set[str],
    visible_release_ids: set[str],
    visible_git_promotion_ids: set[str],
    visible_assignment_ids: set[str],
) -> bool:
    checks = (
        (_optional_text(row.get("agent_id")), visible_agent_ids),
        (_optional_text(row.get("project_id")), visible_project_ids),
        (_optional_text(row.get("task_id")), visible_task_ids),
        (_optional_text(row.get("deliverable_id")), visible_deliverable_ids),
        (_optional_text(row.get("inbox_item_id")), visible_inbox_ids),
        (_optional_text(row.get("action_record_id")), visible_action_ids),
        (_optional_text(row.get("release_package_id")), visible_release_ids),
        (_optional_text(row.get("promotion_id")), visible_git_promotion_ids),
        (_optional_text(row.get("assignment_id")), visible_assignment_ids),
    )
    if not any(identifier is not None for identifier, _allowed in checks):
        return False
    for identifier, allowed in checks:
        if identifier is not None and identifier not in allowed:
            return False
    deliverable_ids = _string_items(row.get("deliverable_ids"))
    if deliverable_ids and any(
        deliverable_id not in visible_deliverable_ids for deliverable_id in deliverable_ids
    ):
        return False
    return True


def _mapping_items(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _filter_ids(value: object, allowed: set[str]) -> list[str]:
    return [item for item in _string_items(value) if item in allowed]


def _filter_wake_work_record_fingerprints(
    value: object,
    *,
    visible_ids_by_kind: Mapping[str, set[str]],
) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    filtered: dict[str, str] = {}
    for raw_key, raw_fingerprint in value.items():
        key = _optional_text(raw_key)
        fingerprint = _optional_text(raw_fingerprint)
        if key is None or fingerprint is None:
            continue
        kind, separator, record_id = key.partition(":")
        if not separator:
            continue
        allowed_ids = visible_ids_by_kind.get(kind)
        if kind == "routing_gaps":
            # Routing-gap identities are opaque digests, not durable record IDs.
            filtered[key] = fingerprint
            continue
        if allowed_ids is None or record_id not in allowed_ids:
            continue
        filtered[key] = fingerprint
    return filtered


def _dispatch_work_scope_survives(
    raw_dispatch: Mapping[str, Any],
    scoped_dispatch: Mapping[str, Any],
) -> bool:
    durable_id_fields = (
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
    )
    if any(
        _string_items(raw_dispatch.get(field_name))
        != _string_items(scoped_dispatch.get(field_name))
        for field_name in durable_id_fields
    ):
        return False
    if _mapping_items(raw_dispatch.get("review_routing_authorities")) != _mapping_items(
        scoped_dispatch.get("review_routing_authorities")
    ):
        return False
    raw_fingerprints = raw_dispatch.get("wake_work_record_fingerprints")
    scoped_fingerprints = scoped_dispatch.get("wake_work_record_fingerprints")
    if (dict(raw_fingerprints) if isinstance(raw_fingerprints, Mapping) else {}) != (
        dict(scoped_fingerprints) if isinstance(scoped_fingerprints, Mapping) else {}
    ):
        return False
    raw_cause_id = _optional_text(raw_dispatch.get("cause_id"))
    return raw_cause_id is None or raw_cause_id == _optional_text(scoped_dispatch.get("cause_id"))


def _blocker_payload_is_visible(
    value: object,
    *,
    visible_team_ids: set[str],
    visible_project_ids: set[str],
    visible_agent_ids: set[str],
    visible_task_ids: set[str],
    visible_deliverable_ids: set[str],
    visible_review_ids: set[str],
    visible_release_ids: set[str],
    visible_human_review_ids: set[str],
) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("team_id") not in visible_team_ids:
        return False
    project_id = _optional_text(value.get("project_id"))
    if project_id is not None and project_id not in visible_project_ids:
        return False
    source_id = _optional_text(value.get("source_id"))
    source_kind = _optional_text(value.get("source_kind"))
    source_visible = {
        "task": source_id in visible_task_ids,
        "deliverable": source_id in visible_deliverable_ids,
        "review_request": source_id in visible_review_ids,
        "release_package": source_id in visible_release_ids,
        "human_review_request": source_id in visible_human_review_ids,
        "agent_execution_stall": source_id in visible_agent_ids,
    }.get(source_kind)
    if source_visible is not None:
        return source_visible
    if source_kind != "explicit":
        return False
    linked_task_ids = {
        task_id
        for task_id in [
            _optional_text(value.get("task_id")),
            *_string_items(value.get("affected_task_ids")),
        ]
        if task_id is not None
    }
    if linked_task_ids.intersection(visible_task_ids):
        return True
    return any(
        agent_id in visible_agent_ids
        for agent_id in (
            _optional_text(value.get("reporter_agent_id")),
            _optional_text(value.get("owner_agent_id")),
            _optional_text(value.get("delegate_agent_id")),
        )
        if agent_id is not None
    )


def _scoped_blocker_payload(
    value: dict[str, Any],
    *,
    visible_task_ids: set[str],
    visible_agent_ids: set[str],
    visible_dispatch_ids: set[str],
) -> dict[str, Any]:
    return {
        **value,
        "task_id": (value.get("task_id") if value.get("task_id") in visible_task_ids else None),
        "affected_task_ids": _filter_ids(
            value.get("affected_task_ids"),
            visible_task_ids,
        ),
        "reporter_agent_id": (
            value.get("reporter_agent_id")
            if value.get("reporter_agent_id") in visible_agent_ids
            else None
        ),
        "owner_agent_id": (
            value.get("owner_agent_id")
            if value.get("owner_agent_id") in visible_agent_ids
            else None
        ),
        "delegate_agent_id": (
            value.get("delegate_agent_id")
            if value.get("delegate_agent_id") in visible_agent_ids
            else None
        ),
        "active_dispatch_id": (
            value.get("active_dispatch_id")
            if value.get("active_dispatch_id") in visible_dispatch_ids
            else None
        ),
    }


def _scoped_blocker_event_payload(
    value: dict[str, Any],
    *,
    visible_agent_ids: set[str],
    visible_dispatch_ids: set[str],
) -> dict[str, Any]:
    return {
        **value,
        "actor_agent_id": (
            value.get("actor_agent_id")
            if value.get("actor_agent_id") in visible_agent_ids
            else None
        ),
        "from_agent_id": (
            value.get("from_agent_id") if value.get("from_agent_id") in visible_agent_ids else None
        ),
        "to_agent_id": (
            value.get("to_agent_id") if value.get("to_agent_id") in visible_agent_ids else None
        ),
        "dispatch_id": (
            value.get("dispatch_id") if value.get("dispatch_id") in visible_dispatch_ids else None
        ),
    }


def _filter_deliverable_git_refs(value: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    git_refs = value.get("git_refs")
    if not isinstance(git_refs, dict):
        return value
    return {
        **value,
        "git_refs": {
            **git_refs,
            "promotion_record_ids": _filter_ids(
                git_refs.get("promotion_record_ids"),
                allowed,
            ),
        },
    }


def _string_items(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str)]
