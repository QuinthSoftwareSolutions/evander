from __future__ import annotations


from collections.abc import (
    Mapping,
    Sequence,
)
from typing import Any
from ...git_naming import validate_branch_name
from .types import TeamCommandValidationError
from .validators import _team_command_validation_error_from_exception
from ...models import (
    PersistentAgent,
    TeamCaseStatus,
    TeamInboxPriority,
    TeamRecordState,
    TeamState,
    TeamTaskState,
    _unique_text_list,
)
from ..domain.case_task import (
    _TaskCreationPlan,
    _plan_task_creation,
)
from ..domain.common import _UNSET, _optional_text


def _team_create_case_normalized_payload(
    *,
    case_id: str | None,
    project_id: str | None,
    title: str,
    description: str,
    status: str,
    priority: str,
    owner_agent_id: str | None,
    source: str | None,
    source_ref: str | None,
    tags: Sequence[str],
    task_ids: Sequence[str],
    handled_inbox_item_ids: Sequence[str],
    correlation_id: str | None,
    causation_id: str | None,
) -> dict[str, Any]:
    return {
        "id": _optional_text(case_id),
        "project_id": _optional_text(project_id),
        "title": _optional_text(title) or "",
        "description": _optional_text(description) or "",
        "status": _optional_text(status) or TeamCaseStatus.OPEN.value,
        "priority": _optional_text(priority) or TeamInboxPriority.NORMAL.value,
        "owner_agent_id": _optional_text(owner_agent_id),
        "source": _optional_text(source),
        "source_ref": _optional_text(source_ref),
        "tags": sorted(_unique_text_list(tags, "tags")),
        "task_ids": sorted(_unique_text_list(task_ids, "task_ids")),
        "handled_inbox_item_ids": sorted(
            _unique_text_list(handled_inbox_item_ids, "handled_inbox_item_ids")
        ),
        "correlation_id": _optional_text(correlation_id),
        "causation_id": _optional_text(causation_id),
    }


def _normalize_case_update_command_fields(**fields: object) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for name, value in fields.items():
        if value is _UNSET:
            continue
        if name in {"project_id", "owner_agent_id", "source", "source_ref"}:
            normalized[name] = _optional_text(value)
        elif name in {"title", "description", "status", "priority"}:
            normalized[name] = _optional_text(value)
        elif name in {"tags", "task_ids"}:
            normalized[name] = _unique_text_list(value, name)
    return normalized


def _case_command_fingerprint(state: TeamState, case_id: str) -> dict[str, Any]:
    case = state.cases.get(case_id)
    if case is None:
        return {}
    case_payload = case.model_dump(mode="json")
    case_payload.pop("updated_at", None)
    task_ids = set(case.task_ids)
    task_links = [
        {"task_id": task.id, "case_id": task.case_id}
        for task in state.tasks.values()
        if task.id in task_ids or task.case_id == case.id
    ]
    return {
        "case": case_payload,
        "task_links": sorted(task_links, key=lambda item: item["task_id"]),
    }


def _team_case_status_value(
    value: TeamCaseStatus | str | None,
    *,
    default: TeamCaseStatus,
) -> str:
    raw = value.value if isinstance(value, TeamCaseStatus) else value
    return _optional_text(raw) or default.value


def _team_case_priority_value(
    value: TeamInboxPriority | str | None,
    *,
    default: TeamInboxPriority,
) -> str:
    raw = value.value if isinstance(value, TeamInboxPriority) else value
    return _optional_text(raw) or default.value


def _normalize_task_creation_command_payload(
    *,
    task_id: str | None,
    owner_agent_id: str,
    project_id: str,
    case_id: str | None,
    priority: int,
    title: str,
    description: str,
    state: TeamTaskState | str,
    parent_task_id: str | None,
    originating_task_id: str | None,
    git_base_branch: str | None,
    dependency_task_ids: Sequence[str] | None,
    task_kind: str = "support",
    outcome_contract: Mapping[str, Any] | None = None,
    integration_boundary: bool = False,
    deliverable_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return _normalize_task_creation_mapping(
        {
            "task_id": task_id,
            "owner_agent_id": owner_agent_id,
            "project_id": project_id,
            "case_id": case_id,
            "priority": priority,
            "title": title,
            "description": description,
            "state": state.value if isinstance(state, TeamTaskState) else state,
            "parent_task_id": parent_task_id,
            "originating_task_id": originating_task_id,
            "git_base_branch": git_base_branch,
            "dependency_task_ids": list(dependency_task_ids or []),
            "task_kind": task_kind,
            "outcome_contract": (
                dict(outcome_contract) if outcome_contract is not None else None
            ),
            "integration_boundary": integration_boundary,
            "deliverable_contract": (
                dict(deliverable_contract)
                if deliverable_contract is not None
                else None
            ),
        },
        field_name="task",
    )


def _normalize_task_creation_mapping(
    item: Mapping[str, Any],
    *,
    field_name: str,
) -> dict[str, Any]:
    if not isinstance(item, Mapping):
        raise ValueError(f"{field_name} must be a JSON object")
    return {
        "task_id": _optional_text(item.get("task_id")),
        "owner_agent_id": _optional_text(item.get("owner_agent_id")),
        "project_id": _optional_text(item.get("project_id")),
        "case_id": _optional_text(item.get("case_id")),
        "priority": item.get("priority", 0),
        "title": _optional_text(item.get("title")),
        "description": _optional_text(item.get("description")),
        "state": _optional_text(item.get("state")) or TeamTaskState.PENDING.value,
        "parent_task_id": _optional_text(item.get("parent_task_id")),
        "originating_task_id": _optional_text(item.get("originating_task_id")),
        "git_base_branch": _optional_text(item.get("git_base_branch")),
        "dependency_task_ids": _unique_text_list(
            item.get("dependency_task_ids") or [],
            "dependency_task_ids",
        ),
        "task_kind": _optional_text(item.get("task_kind")) or "support",
        "outcome_contract": (
            dict(item["outcome_contract"])
            if isinstance(item.get("outcome_contract"), Mapping)
            else item.get("outcome_contract")
        ),
        "integration_boundary": item.get("integration_boundary", False),
        "deliverable_contract": (
            dict(item["deliverable_contract"])
            if isinstance(item.get("deliverable_contract"), Mapping)
            else item.get("deliverable_contract")
        ),
    }


def _team_create_task_normalized_payload(
    *,
    creation: Mapping[str, Any],
    handled_inbox_item_ids: Sequence[str],
    correlation_id: str | None,
    causation_id: str | None,
) -> dict[str, Any]:
    return {
        "task": dict(creation),
        "handled_inbox_item_ids": sorted(
            _unique_text_list(handled_inbox_item_ids, "handled_inbox_item_ids")
        ),
        "correlation_id": _optional_text(correlation_id),
        "causation_id": _optional_text(causation_id),
    }


def _command_plan_task_creation(
    state: TeamState,
    *,
    actor: PersistentAgent,
    creation: dict[str, Any],
    reserved_task_ids: set[str],
) -> _TaskCreationPlan:
    result: dict[str, Any] = {"errors": []}
    try:
        plan = _plan_task_creation(
            state,
            actor=actor,
            creation=creation,
            result=result,
            reserved_task_ids=reserved_task_ids,
        )
    except ValueError as exc:
        raise _team_command_validation_error_from_exception(
            exc,
            code="task_creation_failed",
            correction_context={"task": dict(creation)},
        ) from exc
    if result["errors"] or plan is None:
        raise TeamCommandValidationError(
            code="task_creation_failed",
            message="; ".join(result["errors"]) or "task creation failed",
            correction_context={"task": dict(creation)},
        )
    return plan


def _normalize_task_update_command_fields(**fields: object) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for name, value in fields.items():
        if value is _UNSET:
            continue
        if name == "state":
            normalized[name] = _optional_text(
                value.value if isinstance(value, TeamTaskState) else value
            )
        elif name == "priority":
            normalized[name] = value
        elif name in {"title", "description", "owner_agent_id"}:
            normalized[name] = _optional_text(value)
        elif name == "git_base_branch":
            text = _optional_text(value)
            normalized[name] = validate_branch_name(text) if text is not None else None
        elif name == "completion":
            normalized[name] = dict(value) if isinstance(value, Mapping) else value
        elif name == "integration_boundary":
            if not isinstance(value, bool):
                raise ValueError("integration_boundary must be a boolean")
            normalized[name] = value
        elif name == "deliverable_contract":
            normalized[name] = dict(value) if isinstance(value, Mapping) else value
        elif name == "dependency_task_ids":
            normalized[name] = _unique_text_list(value, "dependency_task_ids")
    return normalized


def _team_record_state_value(value: TeamRecordState | str) -> str:
    raw = value.value if isinstance(value, TeamRecordState) else value
    return _optional_text(raw) or ""


def _task_graph_correction_context(
    state: TeamState,
    *,
    project_id: str | None,
    task_id: str | None,
    parent_task_id: str | None,
    dependency_task_ids: Sequence[str],
) -> dict[str, Any]:
    referenced_task_ids = {
        item for item in [task_id, parent_task_id, *list(dependency_task_ids)] if item is not None
    }
    return {
        "task_id": task_id,
        "project_id": project_id,
        "parent_task_id": parent_task_id,
        "dependency_task_ids": list(dependency_task_ids),
        "current_tasks": _task_graph_current_tasks(
            state,
            project_ids={project_id} if project_id is not None else set(),
            referenced_task_ids=referenced_task_ids,
        ),
    }


def _task_graph_correction_context_for_plans(
    state: TeamState,
    plans: Sequence[_TaskCreationPlan],
    *,
    graph_errors: Mapping[str, str],
) -> dict[str, Any]:
    project_ids = {plan.project_id for plan in plans}
    referenced_task_ids: set[str] = set()
    planned_tasks: list[dict[str, Any]] = []
    for plan in plans:
        referenced_task_ids.add(plan.task_id)
        if plan.parent_task_id is not None:
            referenced_task_ids.add(plan.parent_task_id)
        referenced_task_ids.update(plan.dependency_task_ids)
        planned_tasks.append(
            {
                "task_id": plan.task_id,
                "project_id": plan.project_id,
                "owner_agent_id": plan.owner_id,
                "parent_task_id": plan.parent_task_id,
                "originating_task_id": plan.originating_task_id,
                "dependency_task_ids": list(plan.dependency_task_ids),
                "state": plan.task_state.value,
            }
        )
    return {
        "graph_errors": dict(graph_errors),
        "planned_tasks": sorted(planned_tasks, key=lambda item: item["task_id"]),
        "current_tasks": _task_graph_current_tasks(
            state,
            project_ids=project_ids,
            referenced_task_ids=referenced_task_ids,
        ),
    }


def _task_graph_current_tasks(
    state: TeamState,
    *,
    project_ids: set[str],
    referenced_task_ids: set[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for task in state.tasks.values():
        if task.project_id not in project_ids and task.id not in referenced_task_ids:
            continue
        rows.append(
            {
                "task_id": task.id,
                "project_id": task.project_id,
                "owner_agent_id": task.owner_agent_id,
                "parent_task_id": task.parent_task_id,
                "dependency_task_ids": list(task.dependency_task_ids),
                "state": task.state.value,
            }
        )
    return sorted(rows, key=lambda item: item["task_id"])


__all__ = [
    "_team_create_case_normalized_payload",
    "_normalize_case_update_command_fields",
    "_case_command_fingerprint",
    "_team_case_status_value",
    "_team_case_priority_value",
    "_normalize_task_creation_command_payload",
    "_normalize_task_creation_mapping",
    "_team_create_task_normalized_payload",
    "_command_plan_task_creation",
    "_normalize_task_update_command_fields",
    "_team_record_state_value",
    "_task_graph_correction_context",
    "_task_graph_correction_context_for_plans",
    "_task_graph_current_tasks",
]
