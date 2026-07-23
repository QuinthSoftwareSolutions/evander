from __future__ import annotations

from collections.abc import (
    Mapping,
    Sequence,
)
from typing import Any

from evander.models import utc_now

from .executor import TeamCommandExecutor
from .types import (
    TeamCommandApplyResult,
    TeamCommandContext,
    TeamCommandValidationError,
    _ResolvedTeamCommandContext,
)
from .validators import _require_command_agent
from .work_item_helpers import (
    _command_plan_task_creation,
    _normalize_task_creation_command_payload,
    _normalize_task_creation_mapping,
    _task_graph_correction_context,
    _task_graph_correction_context_for_plans,
    _team_create_task_normalized_payload,
)
from ...models import (
    TeamState,
    TeamTaskState,
    _unique_text_list,
)
from ..domain.case_task import (
    _TaskCreationPlan,
    _apply_task_creation_plan,
    _task_creation_batch_graph_errors,
)
from ..domain.common import (
    DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS,
    _list_of_mappings,
    _optional_text,
    _required_text,
)
from ..domain.project_status import _recalculate_project_status
from ..domain.wake import _append_agent_wake_requested_event


class TeamTaskCreationCommandService(TeamCommandExecutor):
    def create_task_command(
        self,
        *,
        session_id: str,
        owner_agent_id: str,
        project_id: str,
        title: str,
        description: str,
        request_id: str,
        task_id: str | None = None,
        case_id: str | None = None,
        priority: int = 0,
        state: TeamTaskState | str = TeamTaskState.PENDING,
        parent_task_id: str | None = None,
        dependency_task_ids: Sequence[str] | None = None,
        task_kind: str = "support",
        outcome_contract: Mapping[str, Any] | None = None,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        creation = _normalize_task_creation_command_payload(
            task_id=task_id,
            owner_agent_id=owner_agent_id,
            project_id=project_id,
            case_id=case_id,
            priority=priority,
            title=title,
            description=description,
            state=state,
            parent_task_id=parent_task_id,
            dependency_task_ids=dependency_task_ids,
            task_kind=task_kind,
            outcome_contract=outcome_contract,
        )
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_create_task",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="progress",
        )
        normalized_payload = _team_create_task_normalized_payload(
            creation=creation,
            handled_inbox_item_ids=normalized_handled_ids,
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_create_task")
            plan = _command_plan_task_creation(
                state,
                actor=agent,
                creation=creation,
                reserved_task_ids=set(),
            )
            graph_errors = _task_creation_batch_graph_errors(
                state,
                [plan],
                actor=agent,
            )
            if graph_errors:
                error = graph_errors.get(plan.task_id) or next(iter(graph_errors.values()))
                raise TeamCommandValidationError(
                    code="task_graph_invalid",
                    message=error,
                    correction_context=_task_graph_correction_context(
                        state,
                        project_id=plan.project_id,
                        task_id=plan.task_id,
                        parent_task_id=plan.parent_task_id,
                        dependency_task_ids=plan.dependency_task_ids,
                    ),
                )
            created_id = _apply_task_creation_plan(state, plan=plan, actor=agent)
            _recalculate_project_status(state, plan.project_id)
            response_payload = {
                "task_id": created_id,
                "created_task_ids": [created_id],
                "progress_made": True,
            }
            _append_agent_wake_requested_event(
                state,
                agent_id=plan.owner_id,
                result=response_payload,
                reason="task_ready",
                now=utc_now(),
                delay_seconds=DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS,
            )
            return TeamCommandApplyResult(
                response_payload=response_payload,
                created_domain_ids=[created_id],
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )

    def create_tasks_command(
        self,
        *,
        session_id: str,
        tasks: Sequence[Mapping[str, Any]],
        request_id: str,
        handled_inbox_item_ids: Sequence[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_request_id = _required_text(request_id, "request_id")
        normalized_tasks = [
            _normalize_task_creation_mapping(item, field_name=f"tasks[{index}]")
            for index, item in enumerate(_list_of_mappings(list(tasks), "tasks"))
        ]
        if not normalized_tasks:
            raise ValueError("tasks must contain at least one task")
        normalized_handled_ids = _unique_text_list(
            handled_inbox_item_ids or [],
            "handled_inbox_item_ids",
        )
        context = TeamCommandContext(
            ambient_session_id=normalized_session_id,
            tool_name="team_create_tasks",
            request_id=normalized_request_id,
            correlation_id=_optional_text(correlation_id),
            causation_id=_optional_text(causation_id),
            progress_classification="progress",
        )
        normalized_payload = {
            "tasks": normalized_tasks,
            "handled_inbox_item_ids": sorted(normalized_handled_ids),
            "correlation_id": context.correlation_id,
            "causation_id": context.causation_id,
        }

        def _apply(
            state: TeamState,
            resolved: _ResolvedTeamCommandContext,
        ) -> TeamCommandApplyResult:
            agent = _require_command_agent(resolved, "team_create_tasks")
            plans: list[_TaskCreationPlan] = []
            reserved_task_ids: set[str] = set()
            for creation in normalized_tasks:
                plan = _command_plan_task_creation(
                    state,
                    actor=agent,
                    creation=creation,
                    reserved_task_ids=reserved_task_ids,
                )
                reserved_task_ids.add(plan.task_id)
                plans.append(plan)
            graph_errors = _task_creation_batch_graph_errors(
                state,
                plans,
                actor=agent,
            )
            if graph_errors:
                raise TeamCommandValidationError(
                    code="task_graph_invalid",
                    message="; ".join(graph_errors[task_id] for task_id in sorted(graph_errors)),
                    correction_context=_task_graph_correction_context_for_plans(
                        state,
                        plans,
                        graph_errors=graph_errors,
                    ),
                )
            created_ids: list[str] = []
            recalc_project_ids: set[str] = set()
            for plan in plans:
                created_id = _apply_task_creation_plan(state, plan=plan, actor=agent)
                created_ids.append(created_id)
                recalc_project_ids.add(plan.project_id)
            for recalc_id in sorted(recalc_project_ids):
                _recalculate_project_status(state, recalc_id)
            response_payload = {
                "created_task_ids": created_ids,
                "progress_made": True,
            }
            now = utc_now()
            for plan in plans:
                _append_agent_wake_requested_event(
                    state,
                    agent_id=plan.owner_id,
                    result=response_payload,
                    reason="task_ready",
                    now=now,
                    delay_seconds=DEFAULT_AGENT_WAKE_REQUEST_DEBOUNCE_SECONDS,
                )
            return TeamCommandApplyResult(
                response_payload=response_payload,
                created_domain_ids=list(created_ids),
            )

        return self._execute_team_command(
            context=context,
            normalized_payload=normalized_payload,
            handled_inbox_item_ids=normalized_handled_ids,
            apply_command=_apply,
            require_active_dispatch=True,
        )


__all__ = ["TeamTaskCreationCommandService"]
