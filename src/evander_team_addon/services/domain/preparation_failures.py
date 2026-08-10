from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from evander.models import utc_now

from ...models import (
    AgentInboxItem,
    ReviewBlockKind,
    ReviewRequest,
    TeamBlocker,
    TeamBlockerSourceKind,
    TeamBlockerState,
    TeamInboxItemState,
    TeamInboxItemType,
    TeamInboxPriority,
    TeamRecordState,
    TeamState,
    TeamTaskState,
)
from .common import _ensure_utc, _optional_text, _required_text
from .correction_git_source import is_rejected_candidate_correction_task
from .work_due import _mark_agent_work_due


TASK_EXECUTION_PREPARATION_BLOCKER_REASON_CODES = frozenset(
    {
        "dependency_git_composition_conflict",
        "dependency_git_preparation_failed",
        # A candidate-rooted correction must never continue from a worktree
        # whose immutable source differs from its pinned rejected candidate.
        # This is a preparation failure, even when the agent reports it through
        # the generic operator-escalation route rather than the preparer.
        "source_provenance_mismatch",
        "task_execution_preparation_failed",
    }
)
INBOX_EXECUTION_PREPARATION_BLOCKER_REASON_CODE = "inbox_execution_preparation_failed"
REVIEW_PREPARATION_BLOCKER_REASON_CODE = "review_preparation_failed"
EXECUTION_PREPARATION_BLOCKER_REASON_CODES = frozenset(
    {
        *TASK_EXECUTION_PREPARATION_BLOCKER_REASON_CODES,
        INBOX_EXECUTION_PREPARATION_BLOCKER_REASON_CODE,
        REVIEW_PREPARATION_BLOCKER_REASON_CODE,
    }
)
_TASK_PREPARATION_SOURCE_PREFIX = "execution-preparation:task:"
_INBOX_PREPARATION_SOURCE_PREFIX = "execution-preparation:inbox:"
TASK_PREPARATION_CONTRACT_VERSION = 2
_TASK_PREPARATION_GENERATION_FIELDS = (
    "id",
    "owner_agent_id",
    "project_id",
    "state",
    "task_kind",
    "outcome_contract",
    "integration_boundary",
    "deliverable_contract",
    "completion",
    "title",
    "description",
    "parent_task_id",
    "originating_task_id",
    "git_base_branch",
    "dependency_task_ids",
    "cancellation",
)
_DELIVERABLE_PREPARATION_GENERATION_FIELDS = (
    "id",
    "task_id",
    "producer_agent_id",
    "artifact_refs",
    "summary",
    "state",
    "git_refs",
    "verification",
    "source_deliverable_ids",
    "completion",
    "acceptance_recipient_agent_id",
    "acceptance_decision",
    "supersedes_deliverable_id",
    "superseded_by_deliverable_id",
    "supersession_reason",
    "superseded_by_actor",
    "rework_requested_by_agent_id",
    "rework_reason",
)


def preparation_cause_fingerprint(error: object) -> str:
    normalized = " ".join(str(error or "").split()).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]


def _semantic_generation_payload(value: object) -> object:
    """Remove volatile lifecycle bookkeeping from a preparation generation."""

    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {
            str(key): _semantic_generation_payload(item)
            for key, item in value.items()
            if not str(key).endswith("_at")
            and str(key)
            not in {
                # These are denormalized/review bookkeeping. The current
                # deliverable rows below are the authoritative candidates.
                "deliverable_ids",
                "review_request_ids",
                "review_cohort_id",
            }
        }
    if isinstance(value, (list, tuple)):
        return [_semantic_generation_payload(item) for item in value]
    return value


def _allowlisted_generation_payload(
    value: object,
    *,
    fields: Sequence[str],
) -> dict[str, object]:
    if not hasattr(value, "model_dump"):
        raise TypeError("preparation generation records must support model_dump")
    payload = value.model_dump(mode="json")
    return {field: _semantic_generation_payload(payload.get(field)) for field in fields}


def _task_generation_payload(task: object) -> dict[str, object]:
    payload = _allowlisted_generation_payload(
        task,
        fields=_TASK_PREPARATION_GENERATION_FIELDS,
    )
    payload["dependency_task_ids"] = sorted(
        str(item) for item in payload.get("dependency_task_ids") or []
    )
    return payload


def _deliverable_generation_payload(deliverable: object) -> dict[str, object]:
    payload = _allowlisted_generation_payload(
        deliverable,
        fields=_DELIVERABLE_PREPARATION_GENERATION_FIELDS,
    )
    for field in ("artifact_refs", "source_deliverable_ids"):
        payload[field] = sorted(str(item) for item in payload.get(field) or [])
    return payload


def task_preparation_generation(state: TeamState, task_id: str) -> str:
    """Fingerprint task and candidate state that can legitimately unblock setup.

    The fingerprint deliberately excludes blocker and dispatch state. Task
    instructions, ownership/state, project Git configuration, direct dependency
    state, and current deliverable Git candidates are included so a meaningful
    planning/candidate change starts a fresh generation without an operator
    having to erase the prior incident.
    """

    task = state.tasks.get(task_id)
    if task is None:
        return "missing"
    dependency_ids = sorted(set(task.dependency_task_ids))
    relevant_task_ids = {task.id, *dependency_ids}
    payload = {
        # Bump this contract version when a durable repository/worktree
        # preparation defect is repaired. Existing generation-keyed blockers
        # then receive one bounded retry under the corrected implementation;
        # a still-persistent cause opens a fresh blocker for the new version.
        "preparation_contract_version": TASK_PREPARATION_CONTRACT_VERSION,
        "task": _task_generation_payload(task),
        "project_git": (
            _semantic_generation_payload(project.git)
            if (project := state.projects.get(task.project_id)) is not None
            and project.git is not None
            else None
        ),
        "dependencies": [
            _task_generation_payload(state.tasks[dependency_id])
            for dependency_id in dependency_ids
            if dependency_id in state.tasks
        ],
        "deliverables": [
            _deliverable_generation_payload(deliverable)
            for deliverable in sorted(
                (
                    item
                    for item in state.deliverables.values()
                    if item.task_id in relevant_task_ids
                    and item.superseded_by_deliverable_id is None
                ),
                key=lambda item: item.id,
            )
        ],
    }
    correction_source = _human_correction_generation_payload(
        state,
        task_id=task.id,
    )
    if correction_source is not None:
        payload["human_review_correction_source"] = correction_source
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:20]


def _human_correction_generation_payload(
    state: TeamState,
    *,
    task_id: str,
) -> dict[str, object] | None:
    """Tolerantly fingerprint correction provenance, including broken states."""

    findings = sorted(
        (
            finding
            for finding in state.human_review_findings.values()
            if finding.correction_task_id == task_id
        ),
        key=lambda finding: finding.id,
    )
    if not findings:
        return None
    request_ids = sorted({finding.source_human_review_request_id for finding in findings})
    requests = [
        state.human_review_requests[request_id]
        for request_id in request_ids
        if request_id in state.human_review_requests
    ]
    release_ids = sorted({request.release_package_id for request in requests})
    releases = [
        state.release_packages[release_id]
        for release_id in release_ids
        if release_id in state.release_packages
    ]
    source_deliverable_ids = sorted(
        {
            evidence.deliverable_id
            for release in releases
            if release.manifest is not None
            for evidence in release.manifest.deliverables
        }
    )
    return {
        "finding_ids": [finding.id for finding in findings],
        "findings": [_semantic_generation_payload(finding) for finding in findings],
        "missing_request_ids": sorted(set(request_ids) - set(state.human_review_requests)),
        "requests": [_semantic_generation_payload(request) for request in requests],
        "missing_release_ids": sorted(set(release_ids) - set(state.release_packages)),
        "releases": [_semantic_generation_payload(release) for release in releases],
        "missing_source_deliverable_ids": sorted(
            set(source_deliverable_ids) - set(state.deliverables)
        ),
        "source_deliverables": [
            _semantic_generation_payload(state.deliverables[deliverable_id])
            for deliverable_id in source_deliverable_ids
            if deliverable_id in state.deliverables
        ],
    }


def task_preparation_source_id(
    state: TeamState,
    *,
    task_id: str,
    error: object,
) -> str:
    return (
        f"{_TASK_PREPARATION_SOURCE_PREFIX}{task_id}:"
        f"{task_preparation_generation(state, task_id)}:"
        f"{preparation_cause_fingerprint(error)}"
    )


def task_preparation_blocker_is_current(
    state: TeamState,
    blocker: TeamBlocker,
) -> bool:
    if blocker.reason_code not in TASK_EXECUTION_PREPARATION_BLOCKER_REASON_CODES:
        return False
    task_id = blocker.task_id
    if task_id is None:
        return True
    source_prefix = f"{_TASK_PREPARATION_SOURCE_PREFIX}{task_id}:"
    if not blocker.source_id.startswith(source_prefix):
        # Historical dependency-preparation incidents had no generation. Keep
        # them fail-closed until an authorized actor resolves them.
        return True
    generation_prefix = f"{source_prefix}{task_preparation_generation(state, task_id)}:"
    return blocker.source_id.startswith(generation_prefix)


def task_has_current_preparation_blocker(
    state: TeamState,
    *,
    task_id: str,
) -> bool:
    return any(
        blocker.state == TeamBlockerState.OPEN
        and (blocker.task_id == task_id or task_id in blocker.affected_task_ids)
        and task_preparation_blocker_is_current(state, blocker)
        for blocker in state.blockers.values()
    )


def inbox_has_current_preparation_blocker(
    state: TeamState,
    *,
    item: AgentInboxItem,
) -> bool:
    if _inbox_targets_blocked_review(state, item):
        return True
    task_ids = _inbox_task_ids(state, item)
    if any(
        blocker.state == TeamBlockerState.OPEN
        and blocker.reporter_agent_id == item.recipient_agent_id
        and (blocker.task_id in task_ids or bool(set(blocker.affected_task_ids) & task_ids))
        and task_preparation_blocker_is_current(state, blocker)
        for blocker in state.blockers.values()
    ):
        return True
    source_prefix = f"{_INBOX_PREPARATION_SOURCE_PREFIX}{item.id}:"
    return any(
        blocker.state == TeamBlockerState.OPEN
        and blocker.reason_code == INBOX_EXECUTION_PREPARATION_BLOCKER_REASON_CODE
        and blocker.reporter_agent_id == item.recipient_agent_id
        and blocker.source_id.startswith(source_prefix)
        for blocker in state.blockers.values()
    )


def reconcile_stale_task_preparation_blockers(
    state: TeamState,
    *,
    result: dict[str, Any],
    now: datetime | None = None,
) -> list[str]:
    from .blockers import resolve_blocker
    from .dependency_composition_recovery import (
        supersede_stale_dependency_recoveries,
    )

    effective_now = _ensure_utc(now or utc_now())
    superseded_recovery_ids = supersede_stale_dependency_recoveries(
        state,
        now=effective_now,
    )
    if superseded_recovery_ids:
        result.setdefault("superseded_dependency_recovery_ids", []).extend(superseded_recovery_ids)
    resolved_ids: list[str] = []
    for blocker in sorted(state.blockers.values(), key=lambda item: item.id):
        if (
            blocker.state != TeamBlockerState.OPEN
            or blocker.reason_code not in TASK_EXECUTION_PREPARATION_BLOCKER_REASON_CODES
            or blocker.task_id is None
            or task_preparation_blocker_is_current(state, blocker)
        ):
            continue
        resolve_blocker(
            state,
            blocker=blocker,
            actor_agent_id=None,
            dispatch_id=None,
            note=(
                "The task preparation generation changed; the prior failure "
                "no longer suppresses a fresh preparation attempt."
            ),
            reference_ids=[task_preparation_generation(state, blocker.task_id)],
            now=effective_now,
        )
        task = state.tasks.get(blocker.task_id)
        if task is not None:
            _mark_agent_work_due(
                state,
                agent_id=task.owner_agent_id,
                now=effective_now,
            )
        resolved_ids.append(blocker.id)
    if resolved_ids:
        result.setdefault("resolved_blocker_ids", []).extend(resolved_ids)
    return resolved_ids


def record_task_preparation_failure(
    state: TeamState,
    *,
    task_id: str,
    reporter_agent_id: str,
    error: str,
    reason_code: str = "task_execution_preparation_failed",
    result: dict[str, Any],
    now: datetime | None = None,
) -> TeamBlocker:
    from .blockers import open_or_get_blocker

    task = state.tasks.get(_required_text(task_id, "task_id"))
    reporter = state.agents.get(_required_text(reporter_agent_id, "reporter_agent_id"))
    if task is None or reporter is None or reporter.team_id is None:
        raise ValueError(f"cannot record task preparation failure for task {task_id}")
    generation = task_preparation_generation(state, task.id)
    cause = preparation_cause_fingerprint(error)
    blocker, _created = open_or_get_blocker(
        state,
        source_kind=TeamBlockerSourceKind.EXPLICIT,
        source_id=task_preparation_source_id(
            state,
            task_id=task.id,
            error=error,
        ),
        reason_code=reason_code,
        summary=f"Execution preparation failed for task {task.id}",
        details=(
            f"Preparation generation: {generation}\n"
            f"Stable cause fingerprint: {cause}\n"
            f"Error: {error}"
        ),
        priority=TeamInboxPriority.HIGH,
        reporter_agent_id=reporter.id,
        team_id=reporter.team_id,
        project_id=task.project_id,
        task_id=task.id,
        affected_task_ids=[task.id],
        result=result,
        now=now,
    )
    return blocker


def create_rejected_candidate_correction_preparation_recovery_inbox(
    state: TeamState,
    *,
    blocker: TeamBlocker,
    result: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> AgentInboxItem | None:
    """Atomically make a repaired candidate-rooted correction runnable again."""

    if (
        blocker.reason_code not in TASK_EXECUTION_PREPARATION_BLOCKER_REASON_CODES
        or blocker.task_id is None
        or not is_rejected_candidate_correction_task(
            state,
            task_id=blocker.task_id,
        )
    ):
        return None
    task = state.tasks.get(blocker.task_id)
    if task is None or task.state in {
        TeamTaskState.FINISHED,
        TeamTaskState.ACCEPTED,
        TeamTaskState.REJECTED,
        TeamTaskState.CANCELLED,
    }:
        return None
    owner = state.agents.get(task.owner_agent_id)
    if owner is None:
        raise ValueError(f"rejected-candidate correction task {task.id} has no valid owner")
    inbox_id = (
        "inbox-preparation-recovery-"
        + hashlib.sha256(f"{blocker.id}\0{task.id}".encode("utf-8")).hexdigest()[:24]
    )
    existing = state.inbox.get(inbox_id)
    if existing is not None:
        return existing
    effective_now = _ensure_utc(now or utc_now())
    item = AgentInboxItem(
        id=inbox_id,
        recipient_agent_id=owner.id,
        sender_agent_id=None,
        task_id=task.id,
        item_type=TeamInboxItemType.STATUS_REQUEST,
        response_requested=False,
        priority=TeamInboxPriority.HIGH,
        payload={
            "kind": "rejected_candidate_correction_preparation_recovered",
            "task_id": task.id,
            "project_id": task.project_id,
            "blocker_id": blocker.id,
            "reason_code": blocker.reason_code,
            "message": (
                "Execution preparation was repaired. Resume this correction "
                "task from its pinned rejected-candidate worktree."
            ),
        },
        created_at=effective_now,
        updated_at=effective_now,
    )
    state.inbox[item.id] = item
    _mark_agent_work_due(state, agent_id=owner.id, now=effective_now)
    if result is not None:
        from .wake import _append_agent_wake_requested_event

        result.setdefault("created_inbox_item_ids", []).append(item.id)
        _append_agent_wake_requested_event(
            state,
            agent_id=owner.id,
            result=result,
            reason="inbox",
            now=effective_now,
            delay_seconds=0,
            idempotency_suffix=(f"preparation-recovery:{blocker.id}:{task.id}"),
        )
    return item


def create_human_correction_preparation_recovery_inbox(
    state: TeamState,
    *,
    blocker: TeamBlocker,
    result: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> AgentInboxItem | None:
    """Backward-compatible alias for candidate-rooted recovery."""

    return create_rejected_candidate_correction_preparation_recovery_inbox(
        state,
        blocker=blocker,
        result=result,
        now=now,
    )


def record_inbox_preparation_failure(
    state: TeamState,
    *,
    inbox_item_id: str,
    reporter_agent_id: str,
    project_id: str | None,
    error: str,
    result: dict[str, Any],
    now: datetime | None = None,
) -> TeamBlocker:
    from .blockers import open_or_get_blocker

    item = state.inbox.get(_required_text(inbox_item_id, "inbox_item_id"))
    reporter = state.agents.get(_required_text(reporter_agent_id, "reporter_agent_id"))
    if (
        item is None
        or reporter is None
        or reporter.team_id is None
        or item.recipient_agent_id != reporter.id
    ):
        raise ValueError(f"cannot record inbox preparation failure for item {inbox_item_id}")
    cause = preparation_cause_fingerprint(error)
    blocker, _created = open_or_get_blocker(
        state,
        source_kind=TeamBlockerSourceKind.EXPLICIT,
        source_id=f"{_INBOX_PREPARATION_SOURCE_PREFIX}{item.id}:{cause}",
        reason_code=INBOX_EXECUTION_PREPARATION_BLOCKER_REASON_CODE,
        summary=f"Execution preparation failed for inbox item {item.id}",
        details=f"Stable cause fingerprint: {cause}\nError: {error}",
        priority=TeamInboxPriority.HIGH,
        reporter_agent_id=reporter.id,
        team_id=reporter.team_id,
        project_id=_optional_text(project_id),
        result=result,
        now=now,
    )
    return blocker


def record_review_preparation_failure(
    state: TeamState,
    *,
    review_request_id: str,
    error: str,
    result: dict[str, Any],
) -> ReviewRequest:
    """Record an immutable system-blocked review before model inference starts."""

    from .project_status import _recalculate_project_status
    from .review import _cascade_review_decision, _close_review_inbox_items

    review = state.review_requests.get(_required_text(review_request_id, "review_request_id"))
    if review is None:
        raise ValueError(f"review request {review_request_id} does not exist")
    if review.state == TeamRecordState.BLOCKED:
        return review
    if review.state not in {TeamRecordState.PENDING, TeamRecordState.IN_PROGRESS}:
        raise ValueError(f"review request {review.id} is not eligible for preparation failure")
    message = _required_text(error, "error")
    review.block_kind = ReviewBlockKind.SYSTEM
    project_ids: set[str] = set()
    _cascade_review_decision(
        state,
        review=review,
        decision_state=TeamRecordState.BLOCKED,
        findings=[message],
        projects_to_recalc=project_ids,
        result=result,
    )
    closed_ids = _close_review_inbox_items(
        state,
        review_request_id=review.id,
        reviewer_agent_id=review.reviewer_agent_id,
        now=review.decision_at or utc_now(),
    )
    if closed_ids:
        result.setdefault("closed_inbox_item_ids", []).extend(closed_ids)
    for project_id in project_ids:
        _recalculate_project_status(state, project_id)
    blocker = next(
        (
            candidate
            for candidate in state.blockers.values()
            if candidate.state == TeamBlockerState.OPEN
            and candidate.source_kind == TeamBlockerSourceKind.REVIEW_REQUEST
            and candidate.source_id == review.id
        ),
        None,
    )
    if blocker is not None:
        blocker.reason_code = REVIEW_PREPARATION_BLOCKER_REASON_CODE
        blocker.summary = f"Review preparation failed for {review.id}"
        blocker.details = (
            f"Stable cause fingerprint: {preparation_cause_fingerprint(message)}\nError: {message}"
        )
        for item in state.inbox.values():
            if (
                item.state == TeamInboxItemState.PENDING
                and item.payload.get("blocker_id") == blocker.id
            ):
                item.payload["reason_code"] = blocker.reason_code
                item.payload["summary"] = blocker.summary
                item.payload["details"] = blocker.details
                item.updated_at = blocker.updated_at
    return review


def dispatch_project_work_ids(
    state: TeamState,
    *,
    dispatch: Mapping[str, Any],
    project_id: str,
) -> tuple[list[str], list[str], list[str]]:
    task_ids = [
        task_id
        for task_id in _text_items(dispatch.get("task_ids"))
        if (task := state.tasks.get(task_id)) is not None and task.project_id == project_id
    ]
    review_ids = [
        review_id
        for review_id in _text_items(dispatch.get("review_request_ids"))
        if (review := state.review_requests.get(review_id)) is not None
        and _review_project_id(state, review) == project_id
    ]
    inbox_ids = [
        inbox_id
        for inbox_id in _text_items(dispatch.get("inbox_item_ids"))
        if (item := state.inbox.get(inbox_id)) is not None
        and project_id in _inbox_project_ids(state, item)
        and not _inbox_references_any(item, task_ids=task_ids, review_ids=review_ids)
    ]
    return task_ids, review_ids, inbox_ids


def _inbox_targets_blocked_review(
    state: TeamState,
    item: AgentInboxItem,
) -> bool:
    review = state.review_requests.get(str(item.payload.get("review_request_id") or "").strip())
    return bool(
        review is not None
        and review.reviewer_agent_id == item.recipient_agent_id
        and review.state == TeamRecordState.BLOCKED
        and review.superseded_by_review_request_id is None
        and review.retired_at is None
    )


def _inbox_task_ids(state: TeamState, item: AgentInboxItem) -> set[str]:
    task_ids = {
        value
        for raw in (item.task_id, item.payload.get("task_id"))
        if (value := str(raw or "").strip())
    }
    deliverable = state.deliverables.get(str(item.payload.get("deliverable_id") or "").strip())
    if deliverable is not None:
        task_ids.add(deliverable.task_id)
    review = state.review_requests.get(str(item.payload.get("review_request_id") or "").strip())
    if review is not None and review.deliverable_id is not None:
        review_deliverable = state.deliverables.get(review.deliverable_id)
        if review_deliverable is not None:
            task_ids.add(review_deliverable.task_id)
    return task_ids


def _review_project_id(state: TeamState, review: ReviewRequest) -> str | None:
    if review.deliverable_id is not None:
        deliverable = state.deliverables.get(review.deliverable_id)
        task = state.tasks.get(deliverable.task_id) if deliverable is not None else None
        return task.project_id if task is not None else None
    release = state.release_packages.get(review.release_package_id or "")
    return release.project_id if release is not None else None


def _inbox_project_ids(state: TeamState, item: AgentInboxItem) -> set[str]:
    project_ids = {
        value for raw in (item.payload.get("project_id"),) if (value := str(raw or "").strip())
    }
    for task_id in _inbox_task_ids(state, item):
        task = state.tasks.get(task_id)
        if task is not None:
            project_ids.add(task.project_id)
    review = state.review_requests.get(str(item.payload.get("review_request_id") or "").strip())
    if review is not None and (project_id := _review_project_id(state, review)):
        project_ids.add(project_id)
    release = state.release_packages.get(str(item.payload.get("release_package_id") or "").strip())
    if release is not None:
        project_ids.add(release.project_id)
    return project_ids


def _inbox_references_any(
    item: AgentInboxItem,
    *,
    task_ids: Sequence[str],
    review_ids: Sequence[str],
) -> bool:
    return bool(
        (item.task_id is not None and item.task_id in task_ids)
        or str(item.payload.get("task_id") or "").strip() in task_ids
        or str(item.payload.get("review_request_id") or "").strip() in review_ids
    )


def _text_items(value: object) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return sorted({text for item in value if (text := str(item or "").strip())})


__all__ = [
    "EXECUTION_PREPARATION_BLOCKER_REASON_CODES",
    "INBOX_EXECUTION_PREPARATION_BLOCKER_REASON_CODE",
    "REVIEW_PREPARATION_BLOCKER_REASON_CODE",
    "TASK_EXECUTION_PREPARATION_BLOCKER_REASON_CODES",
    "TASK_PREPARATION_CONTRACT_VERSION",
    "create_rejected_candidate_correction_preparation_recovery_inbox",
    "create_human_correction_preparation_recovery_inbox",
    "dispatch_project_work_ids",
    "inbox_has_current_preparation_blocker",
    "preparation_cause_fingerprint",
    "reconcile_stale_task_preparation_blockers",
    "record_inbox_preparation_failure",
    "record_review_preparation_failure",
    "record_task_preparation_failure",
    "task_has_current_preparation_blocker",
    "task_preparation_blocker_is_current",
    "task_preparation_generation",
    "task_preparation_source_id",
]
