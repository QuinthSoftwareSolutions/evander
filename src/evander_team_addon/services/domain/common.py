from __future__ import annotations

import re
from evander.models import utc_now
from collections.abc import Mapping
from datetime import (
    UTC,
    datetime,
)
from decimal import (
    Decimal,
    InvalidOperation,
)
from typing import Any
from ...models import (
    DEFAULT_TEAM_COST_WARNING_RATIO,
    AgentTask,
    ProjectGitConfig,
    ProjectGoal,
    ReleasePackage,
    TeamInboxPriority,
    TeamRecordState,
    TeamState,
    TeamTaskState,
)
from ...templates import TeamTemplate


ADDON_ID = "team-addon"

EPHEMERAL_WORKER_REPORT_TOOL = "team_report_ephemeral_result"

DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS = 1.0

DEFAULT_MAX_NO_PROGRESS_COUNT = 3

_NON_TERMINAL_TASK_STATES = frozenset(
    {
        TeamTaskState.PENDING,
        TeamTaskState.IN_PROGRESS,
        TeamTaskState.BLOCKED,
        TeamTaskState.WAITING_FOR_INPUT,
        TeamTaskState.WAITING_FOR_REVIEW,
    }
)

_AUTOMATIC_WAKE_TASK_STATES = frozenset(
    {
        TeamTaskState.PENDING,
        TeamTaskState.IN_PROGRESS,
        TeamTaskState.BLOCKED,
        TeamTaskState.WAITING_FOR_REVIEW,
    }
)


def task_state_can_wake_owner(state: TeamTaskState) -> bool:
    """Return whether entering ``state`` creates ordinary owner work."""

    return state in _AUTOMATIC_WAKE_TASK_STATES

_MANAGER_FALLBACK_PENDING_REVIEW_STATES = frozenset(
    {
        TeamRecordState.PENDING,
        TeamRecordState.IN_PROGRESS,
        TeamRecordState.BLOCKED,
    }
)

_ACTIVE_DELIVERABLE_WORK_STATES = _MANAGER_FALLBACK_PENDING_REVIEW_STATES

_UNSET: Any = object()

_MCP_TASK_UPDATE_STATE_VALUES = (
    TeamTaskState.PENDING.value,
    TeamTaskState.IN_PROGRESS.value,
    TeamTaskState.WAITING_FOR_REVIEW.value,
    TeamTaskState.FINISHED.value,
)


def _release_candidate_branch(release: ReleasePackage) -> str | None:
    """Return the branch containing a release's immutable candidate."""

    manifest = release.manifest
    return release.release_branch or (manifest.release_branch if manifest is not None else None)


def _set_task_state(task: AgentTask, state: TeamTaskState, *, now: datetime | None = None) -> bool:
    if task.state == state:
        return False
    effective_now = now or utc_now()
    task.state = state
    task.state_entered_at = effective_now
    return True


def _require_present(collection: dict[str, Any], key: str, label: str) -> Any:
    if key not in collection:
        raise ValueError(f"{label} {key} does not exist")
    return collection[key]


def _require_absent(collection: dict[str, Any], key: str, label: str) -> None:
    if key in collection:
        raise ValueError(f"{label} {key} already exists")


def _append_unique(values: list[str], value: str) -> list[str]:
    if value in values:
        return values
    return [*values, value]


_GENERATED_ID_PART_RE = re.compile(r"[^a-z0-9]+")


def _slug_id_part(value: object, *, fallback: str) -> str:
    text = str(value or "").strip().lower()
    slug = _GENERATED_ID_PART_RE.sub("-", text).strip("-")
    return slug or fallback


def _generated_id(
    *,
    prefix: str,
    labels: list[object],
    existing_ids: Mapping[str, Any] | set[str],
) -> str:
    prefix_part = _slug_id_part(prefix, fallback="record")
    label_parts = [
        _slug_id_part(label, fallback="") for label in labels if str(label or "").strip()
    ]
    base = "-".join([prefix_part, *[part for part in label_parts if part]])
    used = set(existing_ids.keys() if isinstance(existing_ids, Mapping) else existing_ids)
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _unique_id_from_base(
    base: str,
    *,
    existing_ids: Mapping[str, Any] | set[str],
) -> str:
    candidate = _required_text(base, "id")
    used = set(existing_ids.keys() if isinstance(existing_ids, Mapping) else existing_ids)
    suffix = 2
    while candidate in used:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _project_goals_with_generated_ids(
    goals: list[ProjectGoal | Mapping[str, Any]],
    *,
    project_id: str,
) -> list[ProjectGoal]:
    normalized: list[ProjectGoal] = []
    used_ids: set[str] = set()
    for index, raw_goal in enumerate(goals):
        if isinstance(raw_goal, ProjectGoal):
            payload = raw_goal.model_dump(mode="json")
        elif isinstance(raw_goal, Mapping):
            payload = dict(raw_goal)
        else:
            raise ValueError(f"goals[{index}] must be a JSON object")
        goal_id = _optional_text(payload.get("id"))
        if goal_id is None:
            goal_id = _generated_id(
                prefix="goal",
                labels=[project_id, payload.get("description")],
                existing_ids=used_ids,
            )
        if goal_id in used_ids:
            raise ValueError(f"duplicate project goal id: {goal_id}")
        used_ids.add(goal_id)
        payload["id"] = goal_id
        normalized.append(ProjectGoal.model_validate(payload))
    return normalized


def _remove_value(values: list[str], value: str) -> list[str]:
    return [item for item in values if item != value]


def _mapping_or_empty(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _decimal_or_none(value: object) -> Decimal | None:
    text = _optional_text(value)
    if text is None:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _decimal_or_zero(value: object) -> Decimal:
    return _decimal_or_none(value) or Decimal("0")


def _priority_sort_value(priority: TeamInboxPriority) -> int:
    order = {
        TeamInboxPriority.HIGH: 0,
        TeamInboxPriority.NORMAL: 1,
        TeamInboxPriority.LOW: 2,
    }
    return order.get(priority, 1)


def _mapping(value: object, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a JSON object")
    return dict(value)


def _required_mapping(payload: Mapping[str, Any], field_name: str) -> dict[str, Any]:
    if field_name not in payload:
        raise ValueError(f"{field_name} is required")
    return _mapping(payload[field_name], field_name)


def _materialize_role_inputs(
    template: TeamTemplate,
    inputs: Mapping[str, Any],
    team_input: Mapping[str, Any],
    project_input: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    nested_agents: Mapping[str, Any] | None = None
    if "roles" in inputs:
        nested_agents = _mapping(inputs["roles"], "roles")
    elif "agents" in inputs:
        nested_agents = _mapping(inputs["agents"], "agents")

    role_inputs: dict[str, dict[str, Any]] = {}
    for role in template.roles:
        raw_role_input: object | None = None
        if role.key in inputs and role.key not in {"team", "project", "roles", "agents"}:
            raw_role_input = inputs[role.key]
        elif nested_agents is not None and role.key in nested_agents:
            raw_role_input = nested_agents[role.key]
        elif role.key == "manager" and (
            "manager_agent_id" in team_input or "manager_session_id" in team_input
        ):
            raw_role_input = {
                "agent_id": team_input.get("manager_agent_id"),
                "session_id": team_input.get("manager_session_id"),
            }
        elif role.key == "project_lead" and (
            "lead_agent_id" in project_input or "lead_session_id" in project_input
        ):
            raw_role_input = {
                "agent_id": project_input.get("lead_agent_id"),
                "session_id": project_input.get("lead_session_id"),
            }
        if raw_role_input is None:
            raise ValueError(f"{role.key} is required")
        role_inputs[role.key] = _mapping(raw_role_input, role.key)
        role_input = role_inputs[role.key]
        role_input["agent_id"] = _optional_text(
            role_input.get("agent_id", role_input.get("id")),
        )
        role_input["session_id"] = _required_text(
            role_input.get("session_id"),
            f"{role.key}.session_id",
        )
    return role_inputs


def _project_git_config(value: object) -> ProjectGitConfig | None:
    if value is None:
        return None
    git_payload = _mapping(value, "project.git")
    managed_repo_id = _optional_text(git_payload.get("managed_repo_id"))
    if managed_repo_id is None:
        return None
    return ProjectGitConfig.model_validate({**git_payload, "managed_repo_id": managed_repo_id})


def _materialized_template_record_id(project_id: str, template_record_id: str) -> str:
    return f"{project_id}-{template_record_id}"


def _ordered_template_roles(template: TeamTemplate) -> list[Any]:
    ordered: list[Any] = []
    pending = list(template.roles)
    while pending:
        progressed = False
        for role in list(pending):
            if role.reports_to_role_key is None or role.reports_to_role_key in {
                item.key for item in ordered
            }:
                ordered.append(role)
                pending.remove(role)
                progressed = True
        if not progressed:
            unresolved = ", ".join(sorted(role.key for role in pending))
            raise ValueError(f"template {template.id} has unresolved role ordering: {unresolved}")
    if ordered[0].key != template.root_role_key:
        raise ValueError(
            f"template {template.id} must start role ordering with {template.root_role_key}"
        )
    return ordered


def _required_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must be a non-empty string")
    return text


def _normalize_operator_user_id(
    value: object | None,
    *,
    created_by: object | None = None,
) -> str:
    text = _optional_text(value)
    if text is None:
        created_by_text = _optional_text(created_by)
        if created_by_text and created_by_text.startswith("operator:"):
            text = created_by_text.removeprefix("operator:")
        elif created_by_text:
            text = created_by_text
    if text is None:
        text = "admin"
    return _required_text(text, "operator_user_id").casefold()


def _operator_display_name_for_user(
    state: TeamState,
    operator_user_id: str,
) -> str | None:
    normalized_operator_user_id = _normalize_operator_user_id(operator_user_id)
    for conversation in sorted(
        state.conversations.values(),
        key=lambda item: (item.last_interaction_at, item.id),
        reverse=True,
    ):
        if conversation.operator_user_id == normalized_operator_user_id:
            return conversation.operator_display_name or conversation.operator_user_id
    return None


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_cost_limit(value: object) -> Decimal | None:
    text = _optional_text(value)
    if text is None:
        return None
    try:
        amount = Decimal(text)
    except (InvalidOperation, ValueError):
        raise ValueError("max_cost_usd must be a non-negative decimal") from None
    if amount < 0:
        raise ValueError("max_cost_usd must be a non-negative decimal")
    return amount


def _cost_warning_ratio(value: object) -> Decimal:
    text = _optional_text(value)
    if text is None:
        return DEFAULT_TEAM_COST_WARNING_RATIO
    try:
        ratio = Decimal(text)
    except (InvalidOperation, ValueError):
        raise ValueError("cost_warning_ratio must be greater than 0 and no more than 1") from None
    if ratio <= 0 or ratio > 1:
        raise ValueError("cost_warning_ratio must be greater than 0 and no more than 1")
    return ratio


def _optional_bool(value: object, field_name: str) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{field_name} must be a boolean")


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _list_of_mappings(value: object, field_name: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    records: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(f"{field_name} must contain JSON objects")
        records.append(dict(item))
    return records


def _string_list(value: object, field_name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list of strings")
    return [str(item).strip() for item in value if str(item).strip()]


def _optional_mapping(value: object, field_name: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    return dict(value)


def _bool_from_value(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError("boolean value is required")


def _json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, sort_keys=True, indent=2)


__all__ = [
    "ADDON_ID",
    "EPHEMERAL_WORKER_REPORT_TOOL",
    "DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS",
    "DEFAULT_MAX_NO_PROGRESS_COUNT",
    "utc_now",
    "_NON_TERMINAL_TASK_STATES",
    "task_state_can_wake_owner",
    "_MANAGER_FALLBACK_PENDING_REVIEW_STATES",
    "_ACTIVE_DELIVERABLE_WORK_STATES",
    "_UNSET",
    "_MCP_TASK_UPDATE_STATE_VALUES",
    "_set_task_state",
    "_require_present",
    "_require_absent",
    "_append_unique",
    "_GENERATED_ID_PART_RE",
    "_slug_id_part",
    "_generated_id",
    "_unique_id_from_base",
    "_project_goals_with_generated_ids",
    "_remove_value",
    "_mapping_or_empty",
    "_decimal_or_none",
    "_decimal_or_zero",
    "_priority_sort_value",
    "_mapping",
    "_required_mapping",
    "_materialize_role_inputs",
    "_project_git_config",
    "_materialized_template_record_id",
    "_ordered_template_roles",
    "_required_text",
    "_normalize_operator_user_id",
    "_operator_display_name_for_user",
    "_optional_text",
    "_optional_cost_limit",
    "_cost_warning_ratio",
    "_optional_bool",
    "_ensure_utc",
    "_list_of_mappings",
    "_string_list",
    "_optional_mapping",
    "_bool_from_value",
    "_json_dumps",
]
