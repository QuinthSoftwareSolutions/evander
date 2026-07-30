from __future__ import annotations

from typing import Any

from .models import (
    HumanReviewRequest,
    ReleasePackage,
    TeamState,
    release_package_payload_with_derived,
)
from .services.domain.task_assessment import (
    assess_tasks,
    task_payload_with_assessment,
)

HUMAN_REVIEW_COLLECTION_KEYS = (
    "human_review_requests",
    "human_review_decisions",
    "human_review_findings",
)


def compact_human_review_release_summary(
    state: TeamState,
    release: ReleasePackage,
) -> dict[str, Any] | None:
    requests = _release_human_review_requests(state, release.id)
    manifest = release.manifest
    requirement = getattr(manifest, "human_review_requirement", None)
    carried_findings = list(
        getattr(manifest, "carried_human_findings", None) or ()
    )
    if requirement is None and not carried_findings and not requests:
        return None

    request = requests[-1] if requests else None
    request_ids = {item.id for item in requests}
    finding_rows = [
        finding
        for finding in state.human_review_findings.values()
        if finding.source_human_review_request_id in request_ids
        or finding.resolved_by_human_review_request_id in request_ids
    ]
    carried_finding_ids = [
        finding_id
        for finding in carried_findings
        if (finding_id := _finding_snapshot_id(finding)) is not None
    ]
    open_finding_ids = sorted(
        finding.id for finding in finding_rows if finding.state == "open"
    )
    state_value = request.state if request is not None else "required"
    return {
        "required": requirement is not None or bool(carried_findings),
        "state": state_value,
        "request_id": request.id if request is not None else None,
        "request_count": len(requests),
        "carried_finding_ids": carried_finding_ids,
        "open_finding_ids": open_finding_ids,
        "next_action": _human_review_next_action(release, state_value),
    }


def apply_compact_human_review_projection(
    payload: dict[str, Any],
    state: TeamState,
) -> None:
    for collection in HUMAN_REVIEW_COLLECTION_KEYS:
        payload.pop(collection, None)
    releases = payload.get("release_packages")
    if not isinstance(releases, dict):
        return
    for release_id, release_payload in releases.items():
        if not isinstance(release_payload, dict):
            continue
        release = state.release_packages.get(str(release_id))
        if release is None:
            continue
        summary = compact_human_review_release_summary(state, release)
        if summary is not None:
            release_payload["human_review"] = summary
            _redact_human_requirement_snapshot(release_payload)


def release_package_api_payload(
    state: TeamState,
    release: ReleasePackage,
    *,
    include_human_details: bool,
) -> dict[str, Any]:
    payload = release_package_payload_with_derived(state, release)
    human_review = (
        release_human_review_detail_payload(state, release)
        if include_human_details
        else compact_human_review_release_summary(state, release)
    )
    if human_review is not None:
        payload["human_review"] = human_review
        if not include_human_details:
            _redact_human_requirement_snapshot(payload)
    return payload


def human_review_request_detail_payload(
    state: TeamState,
    request: HumanReviewRequest,
) -> dict[str, Any]:
    payload = request.model_dump(mode="json")
    decisions = sorted(
        (
            decision
            for decision in state.human_review_decisions.values()
            if decision.human_review_request_id == request.id
        ),
        key=lambda decision: (decision.decided_at, decision.id),
    )
    findings = sorted(
        (
            finding
            for finding in state.human_review_findings.values()
            if finding.source_human_review_request_id == request.id
            or finding.resolved_by_human_review_request_id == request.id
        ),
        key=lambda finding: (finding.created_at, finding.id),
    )
    release = state.release_packages.get(request.release_package_id)
    project = (
        state.projects.get(release.project_id)
        if release is not None
        else None
    )
    cases = sorted(
        (
            case
            for case in state.cases.values()
            if case.source == "human_release_review" and case.source_ref == request.id
        ),
        key=lambda case: (case.created_at, case.id),
    )
    correction_task_ids = sorted(
        {
            finding.correction_task_id
            for finding in findings
            if finding.correction_task_id in state.tasks
        }
        | {
            task_id
            for case in cases
            for task_id in case.task_ids
            if task_id in state.tasks
        }
    )
    task_assessments = assess_tasks(state)
    payload.update(
        {
            "decisions": [
                decision.model_dump(mode="json") for decision in decisions
            ],
            "findings": [
                finding.model_dump(mode="json") for finding in findings
            ],
            "release": (
                release_package_payload_with_derived(state, release)
                if release is not None
                else None
            ),
            "project": (
                {
                    "id": project.id,
                    "team_id": project.team_id,
                    "name": project.name,
                    "status": project.status.value,
                }
                if project is not None
                else None
            ),
            "target_deliverable_options": (
                _human_review_target_deliverable_options(state, release)
                if release is not None
                else []
            ),
            "cases": [case.model_dump(mode="json") for case in cases],
            "correction_tasks": [
                task_payload_with_assessment(
                    state,
                    state.tasks[task_id],
                    assessment=task_assessments[task_id],
                    assessments=task_assessments,
                )
                for task_id in correction_task_ids
            ],
        }
    )
    return payload


def _human_review_target_deliverable_options(
    state: TeamState,
    release: ReleasePackage,
) -> list[dict[str, Any]]:
    manifest = release.manifest
    manifest_deliverable_ids = (
        [evidence.deliverable_id for evidence in manifest.deliverables]
        if manifest is not None
        else []
    )
    top_level_ids = manifest_deliverable_ids or list(release.deliverable_ids)
    top_level_id_set = set(top_level_ids)
    ordered_ids: list[str] = []
    pending_ids = list(top_level_ids)
    seen_ids: set[str] = set()
    while pending_ids:
        deliverable_id = pending_ids.pop(0)
        if deliverable_id in seen_ids:
            continue
        seen_ids.add(deliverable_id)
        ordered_ids.append(deliverable_id)
        if manifest is not None:
            pending_ids.extend(manifest.contribution_graph.get(deliverable_id, ()))

    options: list[dict[str, Any]] = []
    for deliverable_id in ordered_ids:
        deliverable = state.deliverables.get(deliverable_id)
        task = (
            state.tasks.get(deliverable.task_id)
            if deliverable is not None
            else None
        )
        producer = (
            state.agents.get(deliverable.producer_agent_id)
            if deliverable is not None
            else None
        )
        options.append(
            {
                "deliverable_id": deliverable_id,
                "task_id": deliverable.task_id if deliverable is not None else None,
                "task_title": task.title if task is not None else None,
                "producer": (
                    {
                        "agent_id": deliverable.producer_agent_id,
                        "display_name": (
                            producer.display_name or producer.id
                            if producer is not None
                            else deliverable.producer_agent_id
                        ),
                    }
                    if deliverable is not None
                    else None
                ),
                "summary": deliverable.summary if deliverable is not None else None,
                "is_top_level": deliverable_id in top_level_id_set,
            }
        )
    return options


def release_human_review_detail_payload(
    state: TeamState,
    release: ReleasePackage,
) -> dict[str, Any] | None:
    summary = compact_human_review_release_summary(state, release)
    if summary is None:
        return None
    requests = _release_human_review_requests(state, release.id)
    return {
        **summary,
        "requests": [
            human_review_request_detail_payload(state, request)
            for request in requests
        ],
    }


def _release_human_review_requests(
    state: TeamState,
    release_package_id: str,
) -> list[HumanReviewRequest]:
    return sorted(
        (
            request
            for request in state.human_review_requests.values()
            if request.release_package_id == release_package_id
        ),
        key=lambda request: (request.created_at, request.id),
    )


def _finding_snapshot_id(finding: object) -> str | None:
    value = getattr(finding, "finding_id", None) or getattr(finding, "id", None)
    text = str(value or "").strip()
    return text or None


def _human_review_next_action(release: ReleasePackage, state_value: str) -> str | None:
    if state_value == "preparing":
        return "wait_for_human_review_preparation"
    if state_value == "pending":
        return "await_human_review"
    if state_value == "blocked":
        return "resolve_human_review_blocker"
    if state_value == "changes_requested":
        return "complete_human_review_corrections"
    if state_value == "approved" and release.published_at is None:
        return "publish_approved_release"
    if state_value == "required":
        return "create_human_review_request"
    return None


def _redact_human_requirement_snapshot(release_payload: dict[str, Any]) -> None:
    manifest = release_payload.get("manifest")
    if not isinstance(manifest, dict):
        return
    requirement = manifest.get("human_review_requirement")
    if not isinstance(requirement, dict):
        return
    manifest["human_review_requirement"] = {
        "required": True,
        "preparation_mode": requirement.get("preparation_mode") or "manual",
    }
