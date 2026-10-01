"""Opt-in task/case field projection for workflow reads of Team state."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .models import AgentTask, TeamCase, TeamState
from .provider_projection import _ScopedTeamStateProjection, _blocker_payload_is_visible
from .services.domain.task_assessment import assess_tasks, task_payload_with_assessment


_DERIVED_TASK_FIELDS = frozenset({
    "child_task_ids", "dependency_status", "blocked_by_task_ids", "subtask_counts",
    "waiting_reason", "assignment_wait_reason", "assignment_role_name", "assessment",
    "resolution_status", "readiness", "integration_ready", "invalid_reason_codes",
    "active_blocker_ids", "inherited_blockers",
})
_RECORD_FIELDS = {
    "tasks": frozenset(AgentTask.model_fields) | _DERIVED_TASK_FIELDS,
    "cases": frozenset(TeamCase.model_fields),
}
_SCOPE_COLLECTIONS = frozenset({"teams", "projects", "agents", "project_assignments"})
_ASSESSMENT_COLLECTIONS = frozenset({
    "tasks", "deliverables", "review_requests", "blockers", "git_work_contexts", "roles",
    "release_packages", "human_review_requests",
})


def validate_record_fields(value: Any) -> dict[str, frozenset[str]] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or not value:
        raise ValueError("query:team:state record_fields must be a nonempty JSON object")
    selected: dict[str, frozenset[str]] = {}
    for collection, fields in value.items():
        if collection not in _RECORD_FIELDS:
            raise ValueError(f"Unknown Team query projection collection: {collection!r}")
        if (
            not isinstance(fields, list) or not fields
            or any(not isinstance(field, str) for field in fields)
        ):
            raise ValueError(f"record_fields.{collection} must be a nonempty array of field names")
        unknown = set(fields) - _RECORD_FIELDS[collection]
        if unknown:
            raise ValueError(f"Unknown Team {collection} projection fields: {sorted(unknown)!r}")
        selected[collection] = frozenset(fields)
    return selected


def query_projection_collections(
    record_fields: Mapping[str, frozenset[str]], *, agent_id: str | None,
) -> frozenset[str]:
    """Load scope and derivation dependencies, retaining global task truth."""
    collections = set(_SCOPE_COLLECTIONS) | set(record_fields)
    task_fields = record_fields.get("tasks", frozenset())
    if "cases" in record_fields and (agent_id is not None or "task_ids" in record_fields["cases"]):
        collections.add("tasks")
    if "case_id" in task_fields:
        collections.add("cases")
    if "deliverable_ids" in task_fields:
        collections.add("deliverables")
    if "work_lineage_id" in task_fields:
        collections.add("work_lineages")
    if task_fields & _DERIVED_TASK_FIELDS:
        collections |= _ASSESSMENT_COLLECTIONS
    return frozenset(collections)


def compact_team_state_payload(
    state: TeamState,
    *,
    record_fields: Mapping[str, frozenset[str]],
    team_id: str | None,
    project_id: str | None,
    agent_id: str | None,
) -> dict[str, Any]:
    # Reuse the ordinary query's intersection rules without serializing project
    # descriptions, agent configuration, conversations or runtime projections.
    scope = _ScopedTeamStateProjection(
        state, team_id=team_id, project_id=project_id, agent_id=agent_id, session_records=None,
    )
    scope.teams = {key: {} for key in state.teams}
    scope.projects = {
        key: {"team_id": row.team_id} for key, row in state.projects.items()
    }
    scope.agents = {key: {"team_id": row.team_id} for key, row in state.agents.items()}
    scope.project_assignments = {
        key: {"agent_id": row.agent_id, "project_id": row.project_id}
        for key, row in state.project_assignments.items()
    }
    scope._select_scope()
    collections = query_projection_collections(record_fields, agent_id=agent_id)
    tasks = state.tasks if "tasks" in collections else {}
    visible_task_ids = {
        key for key, task in tasks.items()
        if task.project_id in scope.visible_project_ids
        and task.owner_agent_id in scope.visible_agent_ids
    }
    cases = state.cases if "cases" in collections else {}
    visible_case_ids = {
        key for key, case in cases.items()
        if case.team_id in scope.visible_team_ids
        and (case.project_id is None or case.project_id in scope.visible_project_ids)
        and (
            agent_id is None or case.owner_agent_id in scope.visible_agent_ids
            or bool(set(case.task_ids) & visible_task_ids)
        )
    }
    task_fields = record_fields.get("tasks", frozenset())
    visible_deliverable_ids = {
        key for key, row in state.deliverables.items()
        if row.task_id in visible_task_ids and row.producer_agent_id in scope.visible_agent_ids
    } if "deliverables" in collections else set()
    derived = bool(task_fields & _DERIVED_TASK_FIELDS)
    if derived:
        # Assess the complete dependency graph in the same committed read
        # projection; scope only the resulting references, never the assessment.
        assessments = assess_tasks(state)
        visible_review_ids = {
            key for key, row in state.review_requests.items()
            if row.deliverable_id in visible_deliverable_ids
            and row.reviewer_agent_id in scope.visible_agent_ids
        }
        visible_release_ids = {
            key for key, row in state.release_packages.items()
            if row.project_id in scope.visible_project_ids
            and all(value in visible_deliverable_ids for value in row.deliverable_ids)
        }
        visible_human_review_ids = {
            row.id for row in state.human_review_requests.values()
            if row.release_package_id in visible_release_ids
        }
        blocker_scope = {
            "visible_team_ids": scope.visible_team_ids,
            "visible_project_ids": scope.visible_project_ids,
            "visible_agent_ids": scope.visible_agent_ids,
            "visible_task_ids": visible_task_ids,
            "visible_deliverable_ids": visible_deliverable_ids,
            "visible_review_ids": visible_review_ids,
            "visible_release_ids": visible_release_ids,
            "visible_human_review_ids": visible_human_review_ids,
        }
        visible_blocker_ids = {
            key for key, row in state.blockers.items()
            if _blocker_payload_is_visible(row.model_dump(mode="json", include={
                "team_id", "project_id", "source_id", "source_kind", "task_id",
                "affected_task_ids", "reporter_agent_id", "owner_agent_id", "delegate_agent_id",
            }), **blocker_scope)
        }
    result: dict[str, Any] = {}
    if "tasks" in record_fields:
        result["tasks"] = {}
        for key in sorted(visible_task_ids):
            task = tasks[key]
            if derived:
                row = task_payload_with_assessment(
                    state, task, assessment=assessments[key], assessments=assessments,
                    visible_task_ids=visible_task_ids,
                    visible_deliverable_ids=visible_deliverable_ids,
                    visible_review_request_ids=visible_review_ids,
                    visible_blocker_ids=visible_blocker_ids,
                    visible_agent_ids=scope.visible_agent_ids,
                    include_fields=task_fields,
                )
            else:
                row = task.model_dump(mode="json", include=set(task_fields))
                for field in ("parent_task_id", "originating_task_id"):
                    if field in row and row[field] not in visible_task_ids:
                        row[field] = None
                if "dependency_task_ids" in row:
                    row["dependency_task_ids"] = [
                        value for value in row["dependency_task_ids"] if value in visible_task_ids
                    ]
                if "deliverable_ids" in row:
                    row["deliverable_ids"] = [
                        value for value in row["deliverable_ids"] if value in visible_deliverable_ids
                    ]
                if "created_by_agent_id" in row and row["created_by_agent_id"] not in scope.visible_agent_ids:
                    row["created_by_agent_id"] = None
                if isinstance(row.get("cancellation"), dict):
                    if row["cancellation"]["successor_task_id"] not in visible_task_ids:
                        row["cancellation"]["successor_task_id"] = None
            if "case_id" in row and row["case_id"] not in visible_case_ids:
                row["case_id"] = None
            if "work_lineage_id" in row and row["work_lineage_id"] not in state.work_lineages:
                row["work_lineage_id"] = None
            result["tasks"][key] = row
    if "cases" in record_fields:
        result["cases"] = {}
        for key in sorted(visible_case_ids):
            row = cases[key].model_dump(mode="json", include=set(record_fields["cases"]))
            if "task_ids" in row:
                row["task_ids"] = [value for value in row["task_ids"] if value in visible_task_ids]
            result["cases"][key] = row
    return result
