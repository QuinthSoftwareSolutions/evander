from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from typing import Any

from ...models import (
    AgentTask,
    Deliverable,
    DeliverableGitRefs,
    GitWorkContextState,
    TaskAssessment,
    TaskAssessmentReadiness,
    TaskCompletionKind,
    TaskResolutionStatus,
    TeamBlockerState,
    TeamRecordState,
    TeamState,
    TeamTaskKind,
    TeamTaskState,
    task_payload_with_derived,
)


_ACTIVE_RECORD_STATES = frozenset(
    {
        TeamRecordState.PENDING,
        TeamRecordState.IN_PROGRESS,
        TeamRecordState.BLOCKED,
    }
)
_CURRENT_COMPOSITION_STATES = frozenset(
    {
        TeamRecordState.PENDING,
        TeamRecordState.IN_PROGRESS,
        TeamRecordState.ACCEPTED,
        TeamRecordState.BLOCKED,
    }
)
_CYCLE_REASON_CODES = frozenset(
    {
        "parent_cycle",
        "dependency_cycle",
        "hierarchy_dependency_cycle",
    }
)


class TaskAssessmentBoundaryError(ValueError):
    """A stable lifecycle-boundary rejection with machine-readable context."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        correction_context: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.correction_context = dict(correction_context or {})


class TaskAssessmentService:
    """Pure, recursive task lifecycle evaluation over one immutable state view."""

    def __init__(self, state: TeamState) -> None:
        self._state = state
        self._children = _children_by_parent(state)
        self._deliverables = _deliverables_by_task(state)
        self._reviews = _reviews_by_task(state)
        self._blockers = _blockers_by_task(state)
        self._static_invalid_codes = _static_task_invalid_reason_codes(
            state,
            children=self._children,
            deliverables=self._deliverables,
        )
        self._memo: dict[str, TaskAssessment] = {}
        self._visiting: set[str] = set()

    def assess(self, task_id: str) -> TaskAssessment:
        normalized_task_id = str(task_id or "").strip()
        if normalized_task_id not in self._state.tasks:
            raise TaskAssessmentBoundaryError(
                code="task_not_found",
                message=f"task {normalized_task_id or task_id} does not exist",
                correction_context={"task_id": normalized_task_id or str(task_id)},
            )
        return self._assess(normalized_task_id)

    def assess_all(self) -> dict[str, TaskAssessment]:
        return {
            task_id: self._assess(task_id)
            for task_id in sorted(self._state.tasks)
        }

    def _assess(self, task_id: str) -> TaskAssessment:
        cached = self._memo.get(task_id)
        if cached is not None:
            return cached
        task = self._state.tasks[task_id]
        direct_child_ids = list(self._children.get(task_id, ()))
        local_acceptance_ids = _active_acceptance_ids(
            self._deliverables.get(task_id, ()),
            self._reviews.get(task_id, ()),
        )
        local_review_ids = _active_review_ids(self._reviews.get(task_id, ()))
        local_blocker_ids = sorted(
            blocker.id for blocker in self._blockers.get(task_id, ())
        )
        invalid_codes = set(self._static_invalid_codes.get(task_id, ()))

        if task_id in self._visiting:
            return _invalid_assessment(
                task_id=task_id,
                direct_child_ids=direct_child_ids,
                active_acceptance_ids=local_acceptance_ids,
                active_review_request_ids=local_review_ids,
                active_blocker_ids=local_blocker_ids,
                contribution_coverage=_empty_contribution_coverage(
                    task_id=task_id,
                    has_children=bool(direct_child_ids),
                ),
                invalid_reason_codes=["hierarchy_dependency_cycle"],
            )

        # Known graph cycles cannot be traversed recursively. Returning the
        # invalid value here also lets every non-cyclic ancestor remain readable.
        if invalid_codes.intersection(_CYCLE_REASON_CODES):
            assessment = _invalid_assessment(
                task_id=task_id,
                direct_child_ids=direct_child_ids,
                unresolved_descendant_ids=_descendant_ids(
                    task_id,
                    children=self._children,
                ),
                unsatisfied_dependency_ids=sorted(task.dependency_task_ids),
                active_acceptance_ids=local_acceptance_ids,
                active_review_request_ids=local_review_ids,
                active_blocker_ids=local_blocker_ids,
                contribution_coverage=_empty_contribution_coverage(
                    task_id=task_id,
                    has_children=bool(direct_child_ids),
                ),
                invalid_reason_codes=sorted(invalid_codes),
            )
            self._memo[task_id] = assessment
            return assessment

        self._visiting.add(task_id)
        try:
            child_assessments = [
                self._assess(child_id) for child_id in direct_child_ids
            ]
            unresolved_descendant_ids = sorted(
                {
                    descendant_id
                    for child_assessment in child_assessments
                    for descendant_id in (
                        (
                            [child_assessment.task_id]
                            if child_assessment.resolution_status
                            != TaskResolutionStatus.RESOLVED
                            else []
                        )
                        + child_assessment.unresolved_descendant_ids
                    )
                }
            )

            dependency_assessments: dict[str, TaskAssessment] = {}
            for dependency_id in task.dependency_task_ids:
                if dependency_id in self._state.tasks:
                    dependency_assessments[dependency_id] = self._assess(
                        dependency_id
                    )
            unsatisfied_dependency_ids = sorted(
                dependency_id
                for dependency_id in task.dependency_task_ids
                if dependency_id not in dependency_assessments
                or dependency_assessments[dependency_id].resolution_status
                != TaskResolutionStatus.RESOLVED
            )
            active_blocker_ids = sorted(
                {
                    *local_blocker_ids,
                    *(
                        blocker_id
                        for child_assessment in child_assessments
                        for blocker_id in child_assessment.active_blocker_ids
                    ),
                    *(
                        blocker_id
                        for dependency_id in unsatisfied_dependency_ids
                        if dependency_id in dependency_assessments
                        for blocker_id in dependency_assessments[
                            dependency_id
                        ].active_blocker_ids
                    ),
                }
            )

            contribution_coverage = _task_contribution_coverage(
                self._state,
                task=task,
                direct_child_ids=direct_child_ids,
            )
            invalid_codes.update(
                _local_lifecycle_invalid_reason_codes(
                    self._state,
                    task=task,
                    unresolved_descendant_ids=unresolved_descendant_ids,
                    local_acceptance_ids=local_acceptance_ids,
                    local_review_ids=local_review_ids,
                    local_blocker_ids=local_blocker_ids,
                    contribution_coverage=contribution_coverage,
                    current_deliverables=self._deliverables.get(task_id, ()),
                )
            )

            integration_task_ids = _required_contribution_task_ids(
                task,
                direct_child_ids=direct_child_ids,
            )
            integration_ready = bool(integration_task_ids) and all(
                contribution_task_id in self._state.tasks
                and self._assess(contribution_task_id).resolution_status
                == TaskResolutionStatus.RESOLVED
                for contribution_task_id in integration_task_ids
            )

            if invalid_codes:
                assessment = _invalid_assessment(
                    task_id=task.id,
                    direct_child_ids=direct_child_ids,
                    unresolved_descendant_ids=unresolved_descendant_ids,
                    unsatisfied_dependency_ids=unsatisfied_dependency_ids,
                    active_acceptance_ids=local_acceptance_ids,
                    active_review_request_ids=local_review_ids,
                    active_blocker_ids=active_blocker_ids,
                    integration_ready=integration_ready,
                    contribution_coverage=contribution_coverage,
                    invalid_reason_codes=sorted(invalid_codes),
                )
            else:
                resolved_reason = _task_resolution_reason(
                    self._state,
                    task=task,
                    current_deliverables=self._deliverables.get(task_id, ()),
                )
                if resolved_reason is not None:
                    assessment = TaskAssessment(
                        task_id=task.id,
                        resolution_status=TaskResolutionStatus.RESOLVED,
                        resolution_reason=resolved_reason,
                        readiness=TaskAssessmentReadiness.RESOLVED,
                        direct_child_ids=direct_child_ids,
                        unresolved_descendant_ids=[],
                        unsatisfied_dependency_ids=unsatisfied_dependency_ids,
                        active_acceptance_ids=local_acceptance_ids,
                        active_review_request_ids=local_review_ids,
                        active_blocker_ids=active_blocker_ids,
                        integration_ready=integration_ready,
                        contribution_coverage=contribution_coverage,
                    )
                else:
                    pending_accepted_rework_ids = (
                        _pending_accepted_deliverable_rework_ids(
                            self._deliverables.get(task_id, ())
                        )
                    )
                    readiness, reason = _unresolved_readiness(
                        task=task,
                        unresolved_descendant_ids=unresolved_descendant_ids,
                        unsatisfied_dependency_ids=unsatisfied_dependency_ids,
                        local_acceptance_ids=local_acceptance_ids,
                        local_review_ids=local_review_ids,
                        local_blocker_ids=local_blocker_ids,
                        pending_accepted_rework_ids=pending_accepted_rework_ids,
                    )
                    assessment = TaskAssessment(
                        task_id=task.id,
                        resolution_status=TaskResolutionStatus.UNRESOLVED,
                        resolution_reason=reason,
                        readiness=readiness,
                        direct_child_ids=direct_child_ids,
                        unresolved_descendant_ids=unresolved_descendant_ids,
                        unsatisfied_dependency_ids=unsatisfied_dependency_ids,
                        active_acceptance_ids=local_acceptance_ids,
                        active_review_request_ids=local_review_ids,
                        active_blocker_ids=active_blocker_ids,
                        integration_ready=integration_ready,
                        contribution_coverage=contribution_coverage,
                    )
            self._memo[task_id] = assessment
            return assessment
        finally:
            self._visiting.discard(task_id)


def assess_task(state: TeamState, task_id: str) -> TaskAssessment:
    return TaskAssessmentService(state).assess(task_id)


def assess_tasks(state: TeamState) -> dict[str, TaskAssessment]:
    return TaskAssessmentService(state).assess_all()


def task_payload_with_assessment(
    state: TeamState,
    task: AgentTask,
    *,
    assessment: TaskAssessment | None = None,
    assessments: Mapping[str, TaskAssessment] | None = None,
    visible_task_ids: Collection[str] | None = None,
    visible_deliverable_ids: Collection[str] | None = None,
    visible_review_request_ids: Collection[str] | None = None,
    visible_blocker_ids: Collection[str] | None = None,
    visible_agent_ids: Collection[str] | None = None,
) -> dict[str, Any]:
    """Return the backwards-compatible task payload plus canonical lifecycle state."""

    payload = task_payload_with_derived(state, task)
    assessment_service: TaskAssessmentService | None = None
    if assessment is None:
        if assessments is not None and task.id in assessments:
            assessment = assessments[task.id]
        else:
            assessment_service = TaskAssessmentService(state)
            assessment = assessment_service.assess(task.id)
    assessment_payload = assessment.model_dump(mode="json")
    if visible_task_ids is not None:
        payload["parent_task_id"] = _visible_reference(
            payload.get("parent_task_id"),
            visible_task_ids,
        )
        payload["originating_task_id"] = _visible_reference(
            payload.get("originating_task_id"),
            visible_task_ids,
        )
        payload["dependency_task_ids"] = _visible_ids(
            payload.get("dependency_task_ids"),
            visible_task_ids,
        )
        payload["child_task_ids"] = _visible_ids(
            payload.get("child_task_ids"),
            visible_task_ids,
        )
        payload["blocked_by_task_ids"] = _visible_ids(
            payload.get("blocked_by_task_ids"),
            visible_task_ids,
        )
        if isinstance(payload.get("cancellation"), dict):
            payload["cancellation"] = {
                **payload["cancellation"],
                "successor_task_id": _visible_reference(
                    payload["cancellation"].get("successor_task_id"),
                    visible_task_ids,
                ),
            }
        for field_name in (
            "direct_child_ids",
            "unresolved_descendant_ids",
            "unsatisfied_dependency_ids",
        ):
            assessment_payload[field_name] = _visible_ids(
                assessment_payload.get(field_name),
                visible_task_ids,
            )
    if visible_deliverable_ids is not None:
        payload["deliverable_ids"] = _visible_ids(
            payload.get("deliverable_ids"),
            visible_deliverable_ids,
        )
        assessment_payload["active_acceptance_ids"] = _visible_ids(
            assessment_payload.get("active_acceptance_ids"),
            visible_deliverable_ids,
        )
    if (
        visible_task_ids is not None
        or visible_deliverable_ids is not None
    ):
        assessment_payload["contribution_coverage"] = (
            _visible_contribution_coverage(
                assessment_payload.get("contribution_coverage"),
                visible_task_ids=visible_task_ids,
                visible_deliverable_ids=visible_deliverable_ids,
            )
        )
    if visible_review_request_ids is not None:
        assessment_payload["active_review_request_ids"] = _visible_ids(
            assessment_payload.get("active_review_request_ids"),
            visible_review_request_ids,
        )
    if visible_blocker_ids is not None:
        assessment_payload["active_blocker_ids"] = _visible_ids(
            assessment_payload.get("active_blocker_ids"),
            visible_blocker_ids,
        )
        if "active_blocker_ids" in payload:
            payload["active_blocker_ids"] = _visible_ids(
                payload.get("active_blocker_ids"),
                visible_blocker_ids,
            )
    if visible_agent_ids is not None:
        payload["created_by_agent_id"] = _visible_reference(
            payload.get("created_by_agent_id"),
            visible_agent_ids,
        )
    payload["assessment"] = assessment_payload
    payload["resolution_status"] = assessment.resolution_status.value
    payload["readiness"] = assessment.readiness.value
    payload["integration_ready"] = assessment.integration_ready
    payload["invalid_reason_codes"] = list(assessment.invalid_reason_codes)
    payload["active_blocker_ids"] = list(
        assessment_payload["active_blocker_ids"]
    )
    payload["inherited_blockers"] = inherited_task_blocker_payloads(
        state,
        task.id,
        assessments=assessments,
        assessment_service=assessment_service,
        visible_task_ids=visible_task_ids,
        visible_blocker_ids=visible_blocker_ids,
        visible_agent_ids=visible_agent_ids,
    )
    return payload


def inherited_task_blocker_payloads(
    state: TeamState,
    task_id: str,
    *,
    assessments: Mapping[str, TaskAssessment] | None = None,
    assessment_service: TaskAssessmentService | None = None,
    visible_task_ids: Collection[str] | None = None,
    visible_blocker_ids: Collection[str] | None = None,
    visible_agent_ids: Collection[str] | None = None,
) -> list[dict[str, Any]]:
    """Project upstream blocker reasons without creating another durable blocker."""

    service = assessment_service or TaskAssessmentService(state)

    def _assessment(candidate_task_id: str) -> TaskAssessment:
        if assessments is not None and candidate_task_id in assessments:
            return assessments[candidate_task_id]
        return service.assess(candidate_task_id)

    assessment = _assessment(task_id)
    candidates = (
        ("dependency", assessment.unsatisfied_dependency_ids),
        ("descendant", assessment.unresolved_descendant_ids),
    )
    rows: list[dict[str, Any]] = []
    seen_blocker_ids: set[str] = set()
    for relationship, upstream_task_ids in candidates:
        for upstream_task_id in upstream_task_ids:
            if (
                upstream_task_id not in state.tasks
                or (
                    visible_task_ids is not None
                    and upstream_task_id not in visible_task_ids
                )
            ):
                continue
            upstream = _assessment(upstream_task_id)
            for blocker_id in upstream.active_blocker_ids:
                if blocker_id in seen_blocker_ids:
                    continue
                blocker = state.blockers.get(blocker_id)
                if (
                    blocker is None
                    or blocker.state != TeamBlockerState.OPEN
                    or (
                        visible_blocker_ids is not None
                        and blocker_id not in visible_blocker_ids
                    )
                ):
                    continue
                blocker_payload = blocker.model_dump(mode="json")
                rows.append(
                    {
                        "upstream_task_id": upstream_task_id,
                        "relationship": relationship,
                        "blocker_id": blocker.id,
                        "reason_code": blocker.reason_code,
                        "reason": blocker.summary,
                        "summary": blocker.summary,
                        "details": blocker.details,
                        "source_kind": blocker_payload["source_kind"],
                        "source_id": blocker.source_id,
                        "blocker_task_id": _visible_reference(
                            blocker.task_id,
                            visible_task_ids,
                        ),
                        "owner_agent_id": _visible_reference(
                            blocker.owner_agent_id,
                            visible_agent_ids,
                        ),
                        "delegate_agent_id": _visible_reference(
                            blocker.delegate_agent_id,
                            visible_agent_ids,
                        ),
                        "route": blocker_payload["route"],
                        "escalation_depth": blocker.escalation_depth,
                        "priority": blocker_payload["priority"],
                        "created_at": blocker_payload["created_at"],
                        "follow_up_at": blocker_payload["follow_up_at"],
                        "last_progress_at": blocker_payload["last_progress_at"],
                    }
                )
                seen_blocker_ids.add(blocker_id)
    return rows


def _visible_reference(
    value: object,
    visible_ids: Collection[str] | None,
) -> str | None:
    normalized = str(value).strip() if value is not None else ""
    if not normalized:
        return None
    if visible_ids is not None and normalized not in visible_ids:
        return None
    return normalized


def _visible_ids(
    values: object,
    visible_ids: Collection[str],
) -> list[str]:
    if not isinstance(values, Sequence) or isinstance(values, str | bytes):
        return []
    return [
        normalized
        for value in values
        if (normalized := str(value).strip()) and normalized in visible_ids
    ]


def _visible_contribution_coverage(
    value: object,
    *,
    visible_task_ids: Collection[str] | None,
    visible_deliverable_ids: Collection[str] | None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    payload = dict(value)
    task_id_fields = (
        "required_contribution_task_ids",
        "missing_contribution_task_ids",
    )
    deliverable_id_fields = (
        "required_source_deliverable_ids",
        "provided_source_deliverable_ids",
        "missing_source_deliverable_ids",
        "unexpected_source_deliverable_ids",
        "invalid_source_deliverable_ids",
        "current_deliverable_ids",
    )
    if visible_task_ids is not None:
        for field_name in task_id_fields:
            payload[field_name] = _visible_ids(
                payload.get(field_name),
                visible_task_ids,
            )
    if visible_deliverable_ids is not None:
        for field_name in deliverable_id_fields:
            payload[field_name] = _visible_ids(
                payload.get(field_name),
                visible_deliverable_ids,
            )
    by_deliverable = payload.get("by_deliverable")
    if isinstance(by_deliverable, Mapping):
        payload["by_deliverable"] = {
            str(deliverable_id): _visible_contribution_coverage(
                row,
                visible_task_ids=visible_task_ids,
                visible_deliverable_ids=visible_deliverable_ids,
            )
            for deliverable_id, row in by_deliverable.items()
            if (
                visible_deliverable_ids is None
                or str(deliverable_id) in visible_deliverable_ids
            )
        }
    else:
        payload["by_deliverable"] = {}
    return payload


def contribution_coverage_for_sources(
    state: TeamState,
    *,
    task_id: str,
    source_deliverable_ids: Sequence[str],
) -> dict[str, Any]:
    task = state.tasks.get(task_id)
    if task is None:
        raise TaskAssessmentBoundaryError(
            code="task_not_found",
            message=f"task {task_id} does not exist",
            correction_context={"task_id": task_id},
        )
    direct_child_ids = sorted(
        child.id
        for child in state.tasks.values()
        if child.parent_task_id == task.id
    )
    required_contribution_task_ids = _required_contribution_task_ids(
        task,
        direct_child_ids=direct_child_ids,
    )
    return _coverage_for_source_ids(
        state,
        task=task,
        required_contribution_task_ids=required_contribution_task_ids,
        source_deliverable_ids=source_deliverable_ids,
    )


def deliverable_source_lineage(
    state: TeamState,
    deliverable_ids: Sequence[str],
) -> dict[str, Any]:
    """Return recursive source evidence without copying any source records."""

    roots = _unique_strings(deliverable_ids)
    graph: dict[str, list[str]] = {}
    ordered_ids: list[str] = []
    referenced_source_ids: set[str] = set()
    missing_ids: set[str] = set()
    invalid_codes: set[str] = set()
    cycle_paths: list[list[str]] = []
    visiting: list[str] = []
    visited: set[str] = set()

    def _visit(deliverable_id: str) -> None:
        if deliverable_id in visiting:
            start = visiting.index(deliverable_id)
            cycle_paths.append([*visiting[start:], deliverable_id])
            invalid_codes.add("deliverable_source_cycle")
            return
        if deliverable_id in visited:
            return
        deliverable = state.deliverables.get(deliverable_id)
        if deliverable is None:
            missing_ids.add(deliverable_id)
            invalid_codes.add("deliverable_source_missing")
            return
        visiting.append(deliverable_id)
        sources = sorted(deliverable.source_deliverable_ids)
        graph[deliverable_id] = sources
        owner_task = state.tasks.get(deliverable.task_id)
        direct_child_ids = (
            sorted(
                task.id
                for task in state.tasks.values()
                if task.parent_task_id == owner_task.id
            )
            if owner_task is not None
            else []
        )
        allowed_source_task_ids = (
            set(
                _required_contribution_task_ids(
                    owner_task,
                    direct_child_ids=direct_child_ids,
                )
            )
            if owner_task is not None
            else set()
        )
        for source_id in sources:
            referenced_source_ids.add(source_id)
            source = state.deliverables.get(source_id)
            if source is None:
                missing_ids.add(source_id)
                invalid_codes.add("deliverable_source_missing")
            else:
                if source.task_id not in allowed_source_task_ids:
                    invalid_codes.add(
                        "deliverable_source_not_required_contribution"
                    )
                if source.state != TeamRecordState.ACCEPTED:
                    invalid_codes.add("deliverable_source_not_accepted")
                if source.superseded_by_deliverable_id is not None:
                    invalid_codes.add("deliverable_source_superseded")
            _visit(source_id)
        visiting.pop()
        visited.add(deliverable_id)
        ordered_ids.append(deliverable_id)

    for root_id in roots:
        _visit(root_id)
    return {
        "root_deliverable_ids": roots,
        "graph": {key: graph[key] for key in sorted(graph)},
        "ordered_deliverable_ids": ordered_ids,
        "source_deliverable_ids": sorted(referenced_source_ids),
        "missing_deliverable_ids": sorted(missing_ids),
        "cycle_paths": cycle_paths,
        "invalid_reason_codes": sorted(invalid_codes),
        "valid": not invalid_codes,
    }


def recursive_source_deliverable_ids(
    state: TeamState,
    deliverable_id: str,
) -> list[str]:
    lineage = deliverable_source_lineage(state, [deliverable_id])
    if not lineage["valid"]:
        raise TaskAssessmentBoundaryError(
            code="deliverable_source_lineage_invalid",
            message=f"deliverable {deliverable_id} has invalid source lineage",
            correction_context=lineage,
        )
    return list(lineage["source_deliverable_ids"])


def require_task_resolved(
    state: TeamState,
    task_id: str,
    *,
    boundary: str,
) -> TaskAssessment:
    assessment = assess_task(state, task_id)
    if assessment.resolution_status != TaskResolutionStatus.RESOLVED:
        raise _boundary_error(
            assessment,
            boundary=boundary,
            code="task_not_resolved",
            message=(
                f"task {task_id} must be resolved before {boundary}; "
                f"it is {assessment.resolution_status.value} "
                f"({assessment.resolution_reason})"
            ),
        )
    return assessment


def require_task_boundary_clear(
    state: TeamState,
    task_id: str,
    *,
    boundary: str,
    ignore_acceptance_ids: Iterable[str] = (),
    ignore_review_request_ids: Iterable[str] = (),
    ignore_invalid_reason_codes: Iterable[str] = (),
) -> TaskAssessment:
    """Reject blockers shared by finish, submit, accept, review and release."""

    assessment = assess_task(state, task_id)
    ignored_acceptances = set(ignore_acceptance_ids)
    ignored_reviews = set(ignore_review_request_ids)
    ignored_invalid_codes = set(ignore_invalid_reason_codes)
    remaining_acceptances = sorted(
        set(assessment.active_acceptance_ids) - ignored_acceptances
    )
    remaining_reviews = sorted(
        set(assessment.active_review_request_ids) - ignored_reviews
    )
    remaining_invalid_codes = sorted(
        set(assessment.invalid_reason_codes) - ignored_invalid_codes
    )
    reasons: list[str] = []
    if (
        assessment.resolution_status == TaskResolutionStatus.INVALID
        and remaining_invalid_codes
    ):
        reasons.append("invalid task state")
    if assessment.unresolved_descendant_ids:
        reasons.append("unresolved descendants")
    if assessment.unsatisfied_dependency_ids:
        reasons.append("unsatisfied dependencies")
    if assessment.active_blocker_ids:
        reasons.append("active blockers")
    if remaining_acceptances:
        reasons.append("active acceptances")
    if remaining_reviews:
        reasons.append("active reviews")
    if task_id in state.tasks and state.tasks[task_id].state in {
        TeamTaskState.BLOCKED,
        TeamTaskState.REJECTED,
    }:
        reasons.append(f"task state {state.tasks[task_id].state.value}")
    if reasons:
        raise _boundary_error(
            assessment,
            boundary=boundary,
            code="task_boundary_blocked",
            message=(
                f"task {task_id} cannot enter {boundary}: "
                + ", ".join(dict.fromkeys(reasons))
            ),
            extra_context={
                "remaining_active_acceptance_ids": remaining_acceptances,
                "remaining_active_review_request_ids": remaining_reviews,
                "remaining_invalid_reason_codes": remaining_invalid_codes,
            },
        )
    return assessment


def require_task_can_finish(
    state: TeamState,
    task_id: str,
    *,
    dependency_task_ids: Sequence[str] | None = None,
) -> TaskAssessment:
    task = state.tasks.get(task_id)
    if task is None:
        return require_task_boundary_clear(
            state,
            task_id,
            boundary="finish",
        )
    if task.integration_boundary or task.deliverable_contract is not None:
        boundary_kind = (
            "integration boundary"
            if task.integration_boundary
            else "contracted leaf"
        )
        raise TaskAssessmentBoundaryError(
            code=(
                "integration_boundary_direct_finish_forbidden"
                if task.integration_boundary
                else "deliverable_contract_direct_finish_forbidden"
            ),
            message=(
                f"{boundary_kind} task {task_id} must resolve through an "
                "accepted deliverable satisfying its deliverable contract"
            ),
            correction_context={
                "task_id": task_id,
                "deliverable_contract": (
                    task.deliverable_contract.model_dump(mode="json")
                    if task.deliverable_contract is not None
                    else None
                ),
                "recommended_tool": "team_submit_deliverable",
            },
        )
    if task.task_kind != TeamTaskKind.SUPPORT:
        raise TaskAssessmentBoundaryError(
            code="outcome_task_direct_finish_forbidden",
            message=(
                f"outcome task {task_id} must resolve through an accepted "
                "deliverable, not direct finish"
            ),
            correction_context={"task_id": task_id},
        )
    pending_rework_ids = _pending_accepted_deliverable_rework_ids(
        deliverable
        for deliverable in state.deliverables.values()
        if deliverable.task_id == task.id
    )
    if pending_rework_ids:
        raise TaskAssessmentBoundaryError(
            code="accepted_deliverable_rework_pending",
            message=(
                f"task {task.id} cannot finish until its accepted deliverable "
                "rework is replaced and freshly reviewed"
            ),
            correction_context={
                "task_id": task.id,
                "supersedes_deliverable_ids": pending_rework_ids,
                "recommended_tool": "team_submit_deliverable_for_review",
            },
        )
    assessment_state = state
    if dependency_task_ids is not None:
        proposed_task = task.model_copy(
            update={"dependency_task_ids": list(dependency_task_ids)}
        )
        assessment_state = state.model_copy(
            update={
                "tasks": {
                    **state.tasks,
                    task_id: proposed_task,
                }
            }
        )
    return require_task_boundary_clear(
        assessment_state,
        task_id,
        boundary="finish",
    )


def require_task_deliverable_contract_provenance(
    state: TeamState,
    *,
    task_id: str,
    git_refs: DeliverableGitRefs,
    producer_agent_id: str,
    deliverable_id: str | None = None,
    require_submitted_context: bool = True,
) -> None:
    task = state.tasks.get(task_id)
    if task is None:
        raise TaskAssessmentBoundaryError(
            code="task_not_found",
            message=f"task {task_id} does not exist",
            correction_context={"task_id": task_id},
        )
    contract = task.deliverable_contract
    if contract is None or not contract.require_git_provenance:
        return
    reference_values = {
        "branch_context_id": git_refs.branch_context_id,
        "agent_branch": git_refs.agent_branch,
        "commit_sha": git_refs.commit_sha,
        "submitted_commit_sha": git_refs.submitted_commit_sha,
    }
    missing_fields = sorted(
        field_name
        for field_name, value in reference_values.items()
        if value is None
    )
    invalid_fields: list[str] = []
    commit_sha = git_refs.commit_sha
    submitted_commit_sha = git_refs.submitted_commit_sha
    if (
        commit_sha is not None
        and re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", commit_sha)
        is None
    ):
        invalid_fields.append("commit_sha")
    if (
        submitted_commit_sha is not None
        and re.fullmatch(
            r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})",
            submitted_commit_sha,
        )
        is None
    ):
        invalid_fields.append("submitted_commit_sha")
    if (
        commit_sha is not None
        and submitted_commit_sha is not None
        and commit_sha.casefold() != submitted_commit_sha.casefold()
    ):
        invalid_fields.append("submitted_commit_sha_mismatch")
    context = state.git_work_contexts.get(git_refs.branch_context_id or "")
    if context is None:
        invalid_fields.append("branch_context_not_found")
    else:
        if (
            require_submitted_context
            and context.state != GitWorkContextState.SUBMITTED
        ):
            invalid_fields.append("branch_context_not_submitted")
        if context.task_id != task.id:
            invalid_fields.append("branch_context_task_mismatch")
        if context.agent_id != producer_agent_id:
            invalid_fields.append("branch_context_agent_mismatch")
        if context.agent_branch != git_refs.agent_branch:
            invalid_fields.append("agent_branch_mismatch")
        if context.commit_sha is None:
            invalid_fields.append("branch_context_commit_missing")
        elif (
            commit_sha is not None
            and context.commit_sha.casefold() != commit_sha.casefold()
        ):
            invalid_fields.append("branch_context_commit_mismatch")
    if missing_fields or invalid_fields:
        target = (
            f"deliverable {deliverable_id}"
            if deliverable_id is not None
            else f"deliverable for task {task.id}"
        )
        raise TaskAssessmentBoundaryError(
            code="task_git_deliverable_contract_unsatisfied",
            message=(
                f"{target} does not carry authoritative pinned Git provenance "
                "required by its deliverable contract"
            ),
            correction_context={
                "task_id": task.id,
                "deliverable_id": deliverable_id,
                "missing_git_ref_fields": missing_fields,
                "invalid_git_provenance_reasons": sorted(
                    set(invalid_fields)
                ),
                "required_action": (
                    "submit from the task's active Git work context with "
                    "git_refs.commit_sha pinned to the server-resolved branch tip"
                ),
            },
        )


def require_deliverable_submission(
    state: TeamState,
    *,
    task_id: str,
    source_deliverable_ids: Sequence[str],
) -> TaskAssessment:
    current_assessment = assess_task(state, task_id)
    if current_assessment.resolution_status == TaskResolutionStatus.RESOLVED:
        raise _boundary_error(
            current_assessment,
            boundary="deliverable submission",
            code="task_already_resolved",
            message=(
                f"task {task_id} is already resolved and cannot enter "
                "deliverable submission"
            ),
        )
    assessment = require_task_boundary_clear(
        state,
        task_id,
        boundary="deliverable submission",
        # A task may produce several independently reviewed products. Existing
        # handoffs do not prevent submitting another product; task resolution
        # still waits for every current product.
        ignore_acceptance_ids=current_assessment.active_acceptance_ids,
        ignore_review_request_ids=current_assessment.active_review_request_ids,
    )
    coverage = contribution_coverage_for_sources(
        state,
        task_id=task_id,
        source_deliverable_ids=source_deliverable_ids,
    )
    if not coverage["complete"]:
        raise TaskAssessmentBoundaryError(
            code="contribution_coverage_incomplete",
            message=(
                f"deliverable for task {task_id} does not cover its required "
                "contributions"
            ),
            correction_context={"task_id": task_id, "contribution_coverage": coverage},
        )
    return assessment


def require_deliverable_acceptance(
    state: TeamState,
    deliverable_id: str,
) -> TaskAssessment:
    deliverable = state.deliverables.get(deliverable_id)
    if deliverable is None:
        raise TaskAssessmentBoundaryError(
            code="deliverable_not_found",
            message=f"deliverable {deliverable_id} does not exist",
            correction_context={"deliverable_id": deliverable_id},
        )
    require_task_deliverable_contract_provenance(
        state,
        task_id=deliverable.task_id,
        git_refs=deliverable.git_refs,
        producer_agent_id=deliverable.producer_agent_id,
        deliverable_id=deliverable.id,
    )
    current_assessment = assess_task(state, deliverable.task_id)
    assessment = require_task_boundary_clear(
        state,
        deliverable.task_id,
        boundary="deliverable acceptance",
        # Each product or formal review must be allowed to advance independently.
        # Task resolution still waits until the remaining active products close.
        ignore_acceptance_ids=current_assessment.active_acceptance_ids,
        ignore_review_request_ids=current_assessment.active_review_request_ids,
        # Review closure can leave the persisted task state one transition behind
        # after the final request closes. Acceptance is the corrective transition.
        ignore_invalid_reason_codes=[
            "waiting_for_review_without_active_request",
            "active_deliverable_without_acceptance_path",
            "blocked_deliverable_without_active_blocker",
        ],
    )
    coverage = contribution_coverage_for_sources(
        state,
        task_id=deliverable.task_id,
        source_deliverable_ids=deliverable.source_deliverable_ids,
    )
    if not coverage["complete"]:
        raise TaskAssessmentBoundaryError(
            code="contribution_coverage_incomplete",
            message=(
                f"deliverable {deliverable.id} does not cover the accepted "
                "required contributions"
            ),
            correction_context={
                "deliverable_id": deliverable.id,
                "task_id": deliverable.task_id,
                "contribution_coverage": coverage,
            },
        )
    return assessment


def require_release_scope(
    state: TeamState,
    *,
    project_id: str,
    deliverable_ids: Sequence[str],
    allowed_parent_task_ids: Collection[str] = (),
) -> dict[str, Any]:
    normalized_ids = _unique_strings(deliverable_ids)
    allowed_parent_ids = set(_unique_strings(allowed_parent_task_ids))
    errors: dict[str, list[str]] = {}
    task_assessments: dict[str, TaskAssessment] = {}
    for deliverable_id in normalized_ids:
        deliverable = state.deliverables.get(deliverable_id)
        deliverable_errors: list[str] = []
        if deliverable is None:
            errors[deliverable_id] = ["deliverable_missing"]
            continue
        task = state.tasks.get(deliverable.task_id)
        if task is None:
            deliverable_errors.append("deliverable_task_missing")
        else:
            if task.project_id != project_id:
                deliverable_errors.append("deliverable_outside_project")
            if (
                task.parent_task_id is not None
                and task.parent_task_id not in allowed_parent_ids
            ):
                deliverable_errors.append("release_scope_not_top_level")
            assessment = assess_task(state, task.id)
            task_assessments[task.id] = assessment
            if assessment.resolution_status != TaskResolutionStatus.RESOLVED:
                deliverable_errors.append("release_task_not_resolved")
        if deliverable.state != TeamRecordState.ACCEPTED:
            deliverable_errors.append("release_deliverable_not_accepted")
        if deliverable.superseded_by_deliverable_id is not None:
            deliverable_errors.append("release_deliverable_superseded")
        coverage = (
            contribution_coverage_for_sources(
                state,
                task_id=deliverable.task_id,
                source_deliverable_ids=deliverable.source_deliverable_ids,
            )
            if task is not None
            else None
        )
        if coverage is not None and not coverage["complete"]:
            deliverable_errors.append("contribution_coverage_incomplete")
        if deliverable_errors:
            errors[deliverable_id] = deliverable_errors

    lineage = deliverable_source_lineage(state, normalized_ids)
    if lineage["invalid_reason_codes"]:
        errors.setdefault("__lineage__", []).extend(
            lineage["invalid_reason_codes"]
        )
    redundant_source_ids = sorted(
        set(normalized_ids).intersection(lineage["source_deliverable_ids"])
    )
    if redundant_source_ids:
        errors.setdefault("__scope__", []).append(
            "release_scope_contains_aggregate_and_source"
        )
    result = {
        "project_id": project_id,
        "deliverable_ids": normalized_ids,
        "task_assessments": {
            task_id: assessment.model_dump(mode="json")
            for task_id, assessment in sorted(task_assessments.items())
        },
        "source_lineage": lineage,
        "redundant_source_deliverable_ids": redundant_source_ids,
        "errors": errors,
        "valid": not errors,
    }
    if errors:
        error_summary = "; ".join(
            f"{scope_id}: "
            + ", ".join(reason.replace("_", " ") for reason in reasons)
            for scope_id, reasons in sorted(errors.items())
        )
        raise TaskAssessmentBoundaryError(
            code="release_scope_invalid",
            message=(
                f"release scope for project {project_id} is invalid: "
                f"{error_summary}"
            ),
            correction_context=result,
        )
    return result


def assessment_review_ids_for_deliverable(
    state: TeamState,
    deliverable_id: str,
) -> list[str]:
    return sorted(
        review.id
        for review in state.review_requests.values()
        if review.deliverable_id == deliverable_id
        and review.superseded_by_review_request_id is None
        and review.retired_at is None
        and review.state in _ACTIVE_RECORD_STATES
    )


def _children_by_parent(state: TeamState) -> dict[str, tuple[str, ...]]:
    rows: dict[str, list[str]] = {}
    for task in state.tasks.values():
        if task.parent_task_id is not None and task.parent_task_id in state.tasks:
            rows.setdefault(task.parent_task_id, []).append(task.id)
    return {
        parent_id: tuple(sorted(child_ids))
        for parent_id, child_ids in rows.items()
    }


def _deliverables_by_task(
    state: TeamState,
) -> dict[str, tuple[Deliverable, ...]]:
    rows: dict[str, list[Deliverable]] = {}
    for deliverable in state.deliverables.values():
        if deliverable.superseded_by_deliverable_id is None:
            rows.setdefault(deliverable.task_id, []).append(deliverable)
    return {
        task_id: tuple(sorted(deliverables, key=lambda item: item.id))
        for task_id, deliverables in rows.items()
    }


def _reviews_by_task(state: TeamState) -> dict[str, tuple[Any, ...]]:
    rows: dict[str, list[Any]] = {}
    for review in state.review_requests.values():
        if (
            review.deliverable_id is None
            or review.superseded_by_review_request_id is not None
            or review.retired_at is not None
        ):
            continue
        deliverable = state.deliverables.get(review.deliverable_id)
        if deliverable is not None:
            rows.setdefault(deliverable.task_id, []).append(review)
    return {
        task_id: tuple(sorted(reviews, key=lambda item: item.id))
        for task_id, reviews in rows.items()
    }


def _blockers_by_task(state: TeamState) -> dict[str, tuple[Any, ...]]:
    rows: dict[str, dict[str, Any]] = {}
    for blocker in state.blockers.values():
        if blocker.state != TeamBlockerState.OPEN:
            continue
        task_ids: set[str] = set()
        if blocker.task_id in state.tasks:
            task_ids.add(blocker.task_id)
        for affected_task_id in getattr(blocker, "affected_task_ids", ()):
            if affected_task_id in state.tasks:
                task_ids.add(affected_task_id)
        source_kind = getattr(blocker.source_kind, "value", str(blocker.source_kind))
        if source_kind == "task" and blocker.source_id in state.tasks:
            task_ids.add(blocker.source_id)
        elif source_kind == "deliverable":
            deliverable = state.deliverables.get(blocker.source_id)
            if deliverable is not None:
                task_ids.add(deliverable.task_id)
        elif source_kind == "review_request":
            review = state.review_requests.get(blocker.source_id)
            deliverable = (
                state.deliverables.get(review.deliverable_id or "")
                if review is not None
                else None
            )
            if deliverable is not None:
                task_ids.add(deliverable.task_id)
        for task_id in task_ids:
            rows.setdefault(task_id, {})[blocker.id] = blocker
    return {
        task_id: tuple(blockers[blocker_id] for blocker_id in sorted(blockers))
        for task_id, blockers in rows.items()
    }


def _static_task_invalid_reason_codes(
    state: TeamState,
    *,
    children: Mapping[str, Sequence[str]],
    deliverables: Mapping[str, Sequence[Deliverable]],
) -> dict[str, set[str]]:
    invalid: dict[str, set[str]] = {
        task_id: set() for task_id in state.tasks
    }
    parent_graph: dict[str, list[str]] = {}
    dependency_graph: dict[str, list[str]] = {}
    wait_graph: dict[str, list[str]] = {}
    for task in state.tasks.values():
        if task.parent_task_id is not None:
            parent_graph[task.id] = [task.parent_task_id]
            parent = state.tasks.get(task.parent_task_id)
            if parent is None:
                invalid[task.id].add("parent_task_missing")
            elif parent.project_id != task.project_id:
                invalid[task.id].add("parent_task_outside_project")
        else:
            parent_graph[task.id] = []

        dependency_graph[task.id] = list(task.dependency_task_ids)
        wait_graph[task.id] = [
            *children.get(task.id, ()),
            *task.dependency_task_ids,
        ]
        for dependency_id in task.dependency_task_ids:
            dependency = state.tasks.get(dependency_id)
            if dependency is None:
                invalid[task.id].add("dependency_task_missing")
            elif dependency.project_id != task.project_id:
                invalid[task.id].add("dependency_task_outside_project")

        if task.deliverable_contract is not None:
            allowed_contribution_task_ids = {
                *children.get(task.id, ()),
                *task.dependency_task_ids,
            }
            for contribution_task_id in (
                task.deliverable_contract.required_contribution_task_ids
            ):
                contribution_task = state.tasks.get(contribution_task_id)
                if contribution_task is None:
                    invalid[task.id].add(
                        "deliverable_contribution_task_missing"
                    )
                elif contribution_task.project_id != task.project_id:
                    invalid[task.id].add(
                        "deliverable_contribution_task_outside_project"
                    )
                elif contribution_task_id not in allowed_contribution_task_ids:
                    invalid[task.id].add(
                        "deliverable_contribution_task_not_child_or_dependency"
                    )

        if task.originating_task_id is not None:
            origin = state.tasks.get(task.originating_task_id)
            if origin is None:
                invalid[task.id].add("originating_task_missing")
            elif origin.project_id != task.project_id:
                invalid[task.id].add("originating_task_outside_project")

        cancellation = task.cancellation
        if task.state == TeamTaskState.CANCELLED and cancellation is None:
            invalid[task.id].add("cancelled_without_disposition")
        elif task.state != TeamTaskState.CANCELLED and cancellation is not None:
            invalid[task.id].add("cancellation_on_non_cancelled_task")
        if cancellation is not None and cancellation.successor_task_id is not None:
            successor = state.tasks.get(cancellation.successor_task_id)
            if successor is None:
                invalid[task.id].add("cancellation_successor_missing")
            elif successor.id == task.id:
                invalid[task.id].add("cancellation_successor_is_self")
            elif successor.project_id != task.project_id:
                invalid[task.id].add("cancellation_successor_outside_project")

        forward_deliverable_ids = set(task.deliverable_ids)
        for deliverable_id in forward_deliverable_ids:
            deliverable = state.deliverables.get(deliverable_id)
            if deliverable is None:
                invalid[task.id].add("task_deliverable_missing")
            elif deliverable.task_id != task.id:
                invalid[task.id].add("task_deliverable_backlink_mismatch")
        for deliverable in deliverables.get(task.id, ()):
            if deliverable.id not in forward_deliverable_ids:
                invalid[task.id].add("deliverable_missing_task_backlink")

    for task_id in _cycle_nodes(parent_graph):
        invalid[task_id].add("parent_cycle")
    for task_id in _cycle_nodes(dependency_graph):
        invalid[task_id].add("dependency_cycle")
    for task_id in _cycle_nodes(wait_graph):
        if (
            "parent_cycle" not in invalid[task_id]
            and "dependency_cycle" not in invalid[task_id]
        ):
            invalid[task_id].add("hierarchy_dependency_cycle")
    return invalid


def _cycle_nodes(graph: Mapping[str, Sequence[str]]) -> set[str]:
    visited: set[str] = set()
    visiting: list[str] = []
    visiting_positions: dict[str, int] = {}
    cycle_nodes: set[str] = set()

    def _visit(node: str) -> None:
        if node in visiting_positions:
            cycle_nodes.update(visiting[visiting_positions[node] :])
            return
        if node in visited or node not in graph:
            return
        visiting_positions[node] = len(visiting)
        visiting.append(node)
        for target in graph.get(node, ()):
            if target in graph:
                _visit(target)
        visiting.pop()
        visiting_positions.pop(node, None)
        visited.add(node)

    for node in graph:
        _visit(node)
    return cycle_nodes


def _local_lifecycle_invalid_reason_codes(
    state: TeamState,
    *,
    task: AgentTask,
    unresolved_descendant_ids: Sequence[str],
    local_acceptance_ids: Sequence[str],
    local_review_ids: Sequence[str],
    local_blocker_ids: Sequence[str],
    contribution_coverage: Mapping[str, Any],
    current_deliverables: Sequence[Deliverable],
) -> set[str]:
    invalid: set[str] = set()
    terminal_state = task.state in {
        TeamTaskState.FINISHED,
        TeamTaskState.ACCEPTED,
        TeamTaskState.REJECTED,
        TeamTaskState.CANCELLED,
    }
    if terminal_state and unresolved_descendant_ids:
        invalid.add("terminal_task_with_unresolved_descendants")
    if terminal_state and local_acceptance_ids:
        invalid.add("terminal_task_with_active_acceptance")
    if terminal_state and local_review_ids:
        invalid.add("terminal_task_with_active_review")
    if terminal_state and local_blocker_ids:
        invalid.add("terminal_task_with_active_blocker")

    if task.task_kind == TeamTaskKind.OUTCOME:
        if task.state == TeamTaskState.FINISHED:
            invalid.add("outcome_task_finished_without_acceptance")
        elif task.state == TeamTaskState.ACCEPTED:
            invalid.update(
                _accepted_outcome_invalid_reason_codes(
                    task,
                    current_deliverables=current_deliverables,
                )
            )
        elif task.completion is not None:
            invalid.add("outcome_completion_without_acceptance")
    elif task.state == TeamTaskState.ACCEPTED:
        invalid.add("support_task_accepted_instead_of_finished")

    if (
        task.deliverable_contract is not None
        and task.state
        in {
            TeamTaskState.FINISHED,
            TeamTaskState.ACCEPTED,
        }
    ):
        accepted_deliverables = [
            deliverable
            for deliverable in current_deliverables
            if deliverable.state == TeamRecordState.ACCEPTED
        ]
        boundary_prefix = (
            "integration_boundary"
            if task.integration_boundary
            else "deliverable_contract"
        )
        if not accepted_deliverables:
            invalid.add(f"{boundary_prefix}_missing_accepted_deliverable")
        if task.integration_boundary and not contribution_coverage.get("complete"):
            invalid.add(
                "integration_boundary_contribution_contract_unsatisfied"
            )
        for deliverable in accepted_deliverables:
            try:
                require_task_deliverable_contract_provenance(
                    state,
                    task_id=task.id,
                    git_refs=deliverable.git_refs,
                    producer_agent_id=deliverable.producer_agent_id,
                    deliverable_id=deliverable.id,
                )
            except TaskAssessmentBoundaryError:
                invalid.add(f"{boundary_prefix}_git_provenance_unsatisfied")

    if (
        task.state == TeamTaskState.WAITING_FOR_REVIEW
        and not local_acceptance_ids
        and not local_review_ids
    ):
        invalid.add("waiting_for_review_without_active_request")

    blocked_deliverables = [
        deliverable
        for deliverable in current_deliverables
        if deliverable.state == TeamRecordState.BLOCKED
    ]
    if blocked_deliverables and not local_blocker_ids:
        invalid.add("blocked_deliverable_without_active_blocker")
    orphaned_active_deliverables = [
        deliverable
        for deliverable in current_deliverables
        if deliverable.state in _ACTIVE_RECORD_STATES
        and deliverable.id not in local_acceptance_ids
        and not any(
            review_id in local_review_ids
            for review_id in deliverable.review_request_ids
        )
    ]
    if orphaned_active_deliverables:
        invalid.add("active_deliverable_without_acceptance_path")

    if contribution_coverage.get("status") == "incomplete" and any(
        deliverable.state in _CURRENT_COMPOSITION_STATES
        for deliverable in current_deliverables
    ):
        invalid.add("aggregate_deliverable_contribution_invalid")
    return invalid


def _accepted_outcome_invalid_reason_codes(
    task: AgentTask,
    *,
    current_deliverables: Sequence[Deliverable],
) -> set[str]:
    invalid: set[str] = set()
    completion = task.completion
    accepted = [
        deliverable
        for deliverable in current_deliverables
        if deliverable.state == TeamRecordState.ACCEPTED
    ]
    if completion is None:
        return {"accepted_outcome_missing_completion"}
    contract = task.outcome_contract
    if completion.kind == TaskCompletionKind.EVIDENCED_NOOP:
        if contract is None or not contract.allow_evidenced_noop:
            invalid.add("accepted_evidenced_noop_not_allowed")
        if not any(
            deliverable.completion is not None
            and deliverable.completion.kind == TaskCompletionKind.EVIDENCED_NOOP
            for deliverable in accepted
        ):
            invalid.add("accepted_evidenced_noop_missing_deliverable")
        return invalid

    accepted_outcomes = [
        outcome
        for deliverable in accepted
        for outcome in deliverable.outcomes
    ]
    accepted_reference_ids = {
        reference_id
        for outcome in accepted_outcomes
        for reference_id in outcome.reference_ids
    }
    if not set(completion.outcome_reference_ids).issubset(
        accepted_reference_ids
    ):
        invalid.add("accepted_outcome_completion_references_missing")
    if not accepted_outcomes:
        invalid.add("accepted_outcome_missing_delivered_result")
    if contract is not None:
        outcome_kinds = {outcome.kind for outcome in accepted_outcomes}
        required = set(contract.required_outcome_kinds)
        required_satisfied = (
            bool(required.intersection(outcome_kinds))
            if contract.match == "any"
            else required.issubset(outcome_kinds)
        )
        if required and not required_satisfied:
            invalid.add("accepted_outcome_contract_unsatisfied")
        if outcome_kinds.intersection(contract.forbidden_outcome_kinds):
            invalid.add("accepted_outcome_contains_forbidden_kind")
    return invalid


def _task_resolution_reason(
    state: TeamState,
    *,
    task: AgentTask,
    current_deliverables: Sequence[Deliverable],
) -> str | None:
    del state
    if _pending_accepted_deliverable_rework_ids(current_deliverables):
        return None
    if task.state == TeamTaskState.CANCELLED:
        return "cancelled_by_authorized_disposition"
    if task.integration_boundary and task.state in {
        TeamTaskState.FINISHED,
        TeamTaskState.ACCEPTED,
    }:
        return "integration_deliverable_accepted"
    if (
        task.task_kind == TeamTaskKind.SUPPORT
        and task.state == TeamTaskState.FINISHED
    ):
        return "support_task_finished"
    if (
        task.task_kind == TeamTaskKind.OUTCOME
        and task.state == TeamTaskState.ACCEPTED
        and not _accepted_outcome_invalid_reason_codes(
            task,
            current_deliverables=current_deliverables,
        )
    ):
        if (
            task.completion is not None
            and task.completion.kind == TaskCompletionKind.EVIDENCED_NOOP
        ):
            return "outcome_evidenced_noop_accepted"
        return "outcome_deliverable_accepted"
    return None


def _unresolved_readiness(
    *,
    task: AgentTask,
    unresolved_descendant_ids: Sequence[str],
    unsatisfied_dependency_ids: Sequence[str],
    local_acceptance_ids: Sequence[str],
    local_review_ids: Sequence[str],
    local_blocker_ids: Sequence[str],
    pending_accepted_rework_ids: Sequence[str],
) -> tuple[TaskAssessmentReadiness, str]:
    if pending_accepted_rework_ids:
        return (
            TaskAssessmentReadiness.ACTIONABLE,
            "accepted_deliverable_requires_replacement",
        )
    if task.state == TeamTaskState.REJECTED:
        return TaskAssessmentReadiness.BLOCKED, "rejected_requires_recovery"
    if task.state == TeamTaskState.BLOCKED or local_blocker_ids:
        return TaskAssessmentReadiness.BLOCKED, "intrinsic_blocker_open"
    if unresolved_descendant_ids:
        return (
            TaskAssessmentReadiness.WAITING_ON_CHILDREN,
            "unresolved_descendants",
        )
    if unsatisfied_dependency_ids:
        return (
            TaskAssessmentReadiness.WAITING_ON_DEPENDENCIES,
            "unsatisfied_dependencies",
        )
    if local_review_ids:
        return TaskAssessmentReadiness.WAITING_FOR_REVIEW, "active_formal_review"
    if local_acceptance_ids:
        return (
            TaskAssessmentReadiness.WAITING_FOR_ACCEPTANCE,
            "active_direct_acceptance",
        )
    return TaskAssessmentReadiness.ACTIONABLE, "task_actionable"


def _pending_accepted_deliverable_rework_ids(
    deliverables: Iterable[Deliverable],
) -> list[str]:
    return sorted(
        deliverable.id
        for deliverable in deliverables
        if deliverable.state == TeamRecordState.ACCEPTED
        and deliverable.rework_requested_at is not None
        and deliverable.superseded_by_deliverable_id is None
    )


def _active_acceptance_ids(
    deliverables: Sequence[Deliverable],
    reviews: Sequence[Any],
) -> list[str]:
    deliverables_with_active_reviews = {
        review.deliverable_id
        for review in reviews
        if review.state in _ACTIVE_RECORD_STATES
    }
    return sorted(
        deliverable.id
        for deliverable in deliverables
        if deliverable.state in _ACTIVE_RECORD_STATES
        and deliverable.acceptance_recipient_agent_id is not None
        and deliverable.acceptance_decision is None
        and deliverable.id not in deliverables_with_active_reviews
    )


def _active_review_ids(reviews: Sequence[Any]) -> list[str]:
    return sorted(
        review.id
        for review in reviews
        if review.state in _ACTIVE_RECORD_STATES
    )


def _task_contribution_coverage(
    state: TeamState,
    *,
    task: AgentTask,
    direct_child_ids: Sequence[str],
) -> dict[str, Any]:
    required_contribution_task_ids = _required_contribution_task_ids(
        task,
        direct_child_ids=direct_child_ids,
    )
    current_deliverables = [
        deliverable
        for deliverable in state.deliverables.values()
        if deliverable.task_id == task.id
        and deliverable.superseded_by_deliverable_id is None
        and deliverable.state in _CURRENT_COMPOSITION_STATES
    ]
    if not required_contribution_task_ids:
        invalid_source_ids = sorted(
            {
                source_id
                for deliverable in current_deliverables
                for source_id in deliverable.source_deliverable_ids
            }
        )
        return {
            "task_id": task.id,
            "status": "incomplete" if invalid_source_ids else "not_applicable",
            "required_contribution_task_ids": [],
            "missing_contribution_task_ids": [],
            "required_source_deliverable_ids": [],
            "provided_source_deliverable_ids": invalid_source_ids,
            "missing_source_deliverable_ids": [],
            "unexpected_source_deliverable_ids": invalid_source_ids,
            "invalid_source_deliverable_ids": invalid_source_ids,
            "current_deliverable_ids": sorted(
                deliverable.id for deliverable in current_deliverables
            ),
            "by_deliverable": {},
            "complete": not invalid_source_ids,
        }

    by_deliverable: dict[str, dict[str, Any]] = {}
    for deliverable in sorted(current_deliverables, key=lambda item: item.id):
        by_deliverable[deliverable.id] = _coverage_for_source_ids(
            state,
            task=task,
            required_contribution_task_ids=required_contribution_task_ids,
            source_deliverable_ids=deliverable.source_deliverable_ids,
        )
    required_ids = _required_source_deliverable_ids(
        state,
        task=task,
        required_contribution_task_ids=required_contribution_task_ids,
    )
    missing_contribution_task_ids = _missing_contribution_task_ids(
        state,
        task=task,
        required_contribution_task_ids=required_contribution_task_ids,
    )
    provided_ids = sorted(
        {
            source_id
            for deliverable in current_deliverables
            for source_id in deliverable.source_deliverable_ids
        }
    )
    missing_ids = sorted(
        {
            source_id
            for coverage in by_deliverable.values()
            for source_id in coverage["missing_source_deliverable_ids"]
        }
        or (set(required_ids) if not by_deliverable else set())
    )
    unexpected_ids = sorted(
        {
            source_id
            for coverage in by_deliverable.values()
            for source_id in coverage["unexpected_source_deliverable_ids"]
        }
    )
    invalid_ids = sorted(
        {
            source_id
            for coverage in by_deliverable.values()
            for source_id in coverage["invalid_source_deliverable_ids"]
        }
    )
    complete = (
        bool(by_deliverable)
        and not missing_contribution_task_ids
        and all(coverage["complete"] for coverage in by_deliverable.values())
    )
    return {
        "task_id": task.id,
        "status": (
            "complete"
            if complete
            else ("not_submitted" if not by_deliverable else "incomplete")
        ),
        "required_contribution_task_ids": required_contribution_task_ids,
        "missing_contribution_task_ids": missing_contribution_task_ids,
        "required_source_deliverable_ids": required_ids,
        "provided_source_deliverable_ids": provided_ids,
        "missing_source_deliverable_ids": missing_ids,
        "unexpected_source_deliverable_ids": unexpected_ids,
        "invalid_source_deliverable_ids": invalid_ids,
        "current_deliverable_ids": sorted(by_deliverable),
        "by_deliverable": by_deliverable,
        "complete": complete,
    }


def _coverage_for_source_ids(
    state: TeamState,
    *,
    task: AgentTask,
    required_contribution_task_ids: Sequence[str],
    source_deliverable_ids: Sequence[str],
) -> dict[str, Any]:
    provided_ids = _unique_strings(source_deliverable_ids)
    required_ids = _required_source_deliverable_ids(
        state,
        task=task,
        required_contribution_task_ids=required_contribution_task_ids,
    )
    missing_contribution_task_ids = _missing_contribution_task_ids(
        state,
        task=task,
        required_contribution_task_ids=required_contribution_task_ids,
    )
    contribution_task_set = set(required_contribution_task_ids)
    invalid_ids: set[str] = set()
    for source_id in provided_ids:
        source = state.deliverables.get(source_id)
        if (
            source is None
            or source.task_id not in contribution_task_set
            or source.state != TeamRecordState.ACCEPTED
            or source.superseded_by_deliverable_id is not None
        ):
            invalid_ids.add(source_id)
    missing_ids = sorted(set(required_ids) - set(provided_ids))
    unexpected_ids = sorted(set(provided_ids) - set(required_ids))
    complete = (
        not missing_contribution_task_ids
        and not missing_ids
        and not unexpected_ids
        and not invalid_ids
    )
    return {
        "task_id": task.id,
        "required_contribution_task_ids": list(
            required_contribution_task_ids
        ),
        "missing_contribution_task_ids": missing_contribution_task_ids,
        "required_source_deliverable_ids": required_ids,
        "provided_source_deliverable_ids": provided_ids,
        "missing_source_deliverable_ids": missing_ids,
        "unexpected_source_deliverable_ids": unexpected_ids,
        "invalid_source_deliverable_ids": sorted(invalid_ids),
        "complete": complete,
    }


def _required_contribution_task_ids(
    task: AgentTask,
    *,
    direct_child_ids: Sequence[str],
) -> list[str]:
    if task.integration_boundary and task.deliverable_contract is not None:
        return list(
            task.deliverable_contract.required_contribution_task_ids
        )
    return sorted(set(direct_child_ids))


def _accepted_contribution_deliverables(
    state: TeamState,
    *,
    task: AgentTask,
    contribution_task_id: str,
) -> list[Deliverable]:
    contribution_task = state.tasks.get(contribution_task_id)
    if contribution_task is None:
        return []
    legacy_direct_child_policy = not task.integration_boundary
    if (
        legacy_direct_child_policy
        and contribution_task.state == TeamTaskState.CANCELLED
    ):
        return []
    if (
        legacy_direct_child_policy
        and (
            contribution_task.task_kind == TeamTaskKind.OUTCOME
            and contribution_task.state == TeamTaskState.ACCEPTED
            and contribution_task.completion is not None
            and contribution_task.completion.kind
            == TaskCompletionKind.EVIDENCED_NOOP
        )
    ):
        return []
    return sorted(
        (
            deliverable
            for deliverable in state.deliverables.values()
            if deliverable.task_id == contribution_task_id
            and deliverable.state == TeamRecordState.ACCEPTED
            and deliverable.superseded_by_deliverable_id is None
            and (
                not legacy_direct_child_policy
                or deliverable.completion is None
                or deliverable.completion.kind
                != TaskCompletionKind.EVIDENCED_NOOP
            )
        ),
        key=lambda deliverable: deliverable.id,
    )


def _required_source_deliverable_ids(
    state: TeamState,
    *,
    task: AgentTask,
    required_contribution_task_ids: Sequence[str],
) -> list[str]:
    return sorted(
        deliverable.id
        for contribution_task_id in required_contribution_task_ids
        for deliverable in _accepted_contribution_deliverables(
            state,
            task=task,
            contribution_task_id=contribution_task_id,
        )
    )


def _missing_contribution_task_ids(
    state: TeamState,
    *,
    task: AgentTask,
    required_contribution_task_ids: Sequence[str],
) -> list[str]:
    if not task.integration_boundary:
        return []
    return sorted(
        contribution_task_id
        for contribution_task_id in required_contribution_task_ids
        if not _accepted_contribution_deliverables(
            state,
            task=task,
            contribution_task_id=contribution_task_id,
        )
    )


def _empty_contribution_coverage(
    *,
    task_id: str,
    has_children: bool,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "status": "unknown" if has_children else "not_applicable",
        "required_contribution_task_ids": [],
        "missing_contribution_task_ids": [],
        "required_source_deliverable_ids": [],
        "provided_source_deliverable_ids": [],
        "missing_source_deliverable_ids": [],
        "unexpected_source_deliverable_ids": [],
        "invalid_source_deliverable_ids": [],
        "current_deliverable_ids": [],
        "by_deliverable": {},
        "complete": not has_children,
    }


def _descendant_ids(
    task_id: str,
    *,
    children: Mapping[str, Sequence[str]],
) -> list[str]:
    descendants: set[str] = set()
    stack = list(children.get(task_id, ()))
    while stack:
        descendant_id = stack.pop()
        if descendant_id == task_id or descendant_id in descendants:
            continue
        descendants.add(descendant_id)
        stack.extend(children.get(descendant_id, ()))
    return sorted(descendants)


def _invalid_assessment(
    *,
    task_id: str,
    direct_child_ids: Sequence[str],
    contribution_coverage: Mapping[str, Any],
    invalid_reason_codes: Sequence[str],
    unresolved_descendant_ids: Sequence[str] = (),
    unsatisfied_dependency_ids: Sequence[str] = (),
    active_acceptance_ids: Sequence[str] = (),
    active_review_request_ids: Sequence[str] = (),
    active_blocker_ids: Sequence[str] = (),
    integration_ready: bool = False,
) -> TaskAssessment:
    codes = sorted(set(invalid_reason_codes)) or ["invalid_task_state"]
    return TaskAssessment(
        task_id=task_id,
        resolution_status=TaskResolutionStatus.INVALID,
        resolution_reason="invalid:" + ",".join(codes),
        readiness=TaskAssessmentReadiness.INVALID,
        direct_child_ids=sorted(set(direct_child_ids)),
        unresolved_descendant_ids=sorted(set(unresolved_descendant_ids)),
        unsatisfied_dependency_ids=sorted(set(unsatisfied_dependency_ids)),
        active_acceptance_ids=sorted(set(active_acceptance_ids)),
        active_review_request_ids=sorted(set(active_review_request_ids)),
        active_blocker_ids=sorted(set(active_blocker_ids)),
        integration_ready=integration_ready,
        contribution_coverage=dict(contribution_coverage),
        invalid_reason_codes=codes,
    )


def _boundary_error(
    assessment: TaskAssessment,
    *,
    boundary: str,
    code: str,
    message: str,
    extra_context: Mapping[str, Any] | None = None,
) -> TaskAssessmentBoundaryError:
    context = {
        "boundary": boundary,
        "task_id": assessment.task_id,
        "resolution_status": assessment.resolution_status.value,
        "resolution_reason": assessment.resolution_reason,
        "readiness": assessment.readiness.value,
        "unresolved_descendant_ids": list(
            assessment.unresolved_descendant_ids
        ),
        "unsatisfied_dependency_ids": list(
            assessment.unsatisfied_dependency_ids
        ),
        "active_acceptance_ids": list(assessment.active_acceptance_ids),
        "active_review_request_ids": list(
            assessment.active_review_request_ids
        ),
        "active_blocker_ids": list(assessment.active_blocker_ids),
        "invalid_reason_codes": list(assessment.invalid_reason_codes),
        "contribution_coverage": dict(assessment.contribution_coverage),
    }
    context.update(extra_context or {})
    return TaskAssessmentBoundaryError(
        code=code,
        message=message,
        correction_context=context,
    )


def _unique_strings(values: Iterable[str]) -> list[str]:
    return sorted({str(value).strip() for value in values if str(value).strip()})


__all__ = [
    "TaskAssessmentBoundaryError",
    "TaskAssessmentService",
    "assess_task",
    "assess_tasks",
    "inherited_task_blocker_payloads",
    "task_payload_with_assessment",
    "assessment_review_ids_for_deliverable",
    "contribution_coverage_for_sources",
    "deliverable_source_lineage",
    "recursive_source_deliverable_ids",
    "require_deliverable_acceptance",
    "require_deliverable_submission",
    "require_release_scope",
    "require_task_boundary_clear",
    "require_task_can_finish",
    "require_task_deliverable_contract_provenance",
    "require_task_resolved",
]
