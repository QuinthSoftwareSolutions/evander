from __future__ import annotations

from typing import Any
from uuid import uuid4
from ...git_naming import (
    canonical_release_branch,
    feature_branch,
    release_branch,
    validate_branch_name,
)
from ...models import (
    Deliverable,
    GitPromotionRecord,
    PersistentAgent,
    Project,
    ReleasePackage,
    RolePermission,
    TeamRecordState,
    TeamState,
    project_lead_agent_id,
)

from .agent_access import (
    _agent_has_role_permission_for_project,
    _is_project_lead_for,
    _is_team_manager,
)
from .common import (
    _append_unique,
    _optional_text,
    _required_text,
    _string_list,
    utc_now,
)
from .git_common import (
    _bare_repo_fields,
    _emit_git_promotion_event,
    _normalize_agent_branch_ref,
    _project_git_or_error,
)


GIT_RELEASE_RECOVERY_EVENT_TYPE = "team.git.release_recovery_requested"

def _emit_git_release_recovery_event(
    result: dict[str, Any],
    *,
    release_package_id: str,
    agent_branch: str,
    payload: dict[str, Any],
) -> None:
    recovery_key = f"{release_package_id}:{agent_branch}"
    commit_sha = _optional_text(payload.get("expected_commit_sha"))
    if commit_sha is not None:
        recovery_key = f"{recovery_key}:{commit_sha}"
    result["events"].append(
        {
            "event_type": GIT_RELEASE_RECOVERY_EVENT_TYPE,
            "correlation_key": release_package_id,
            "idempotency_key": (f"team-addon:{GIT_RELEASE_RECOVERY_EVENT_TYPE}:{recovery_key}"),
            "payload": payload,
        }
    )

def _apply_git_release_request(
    state: TeamState,
    *,
    actor: PersistentAgent,
    request: dict[str, Any],
    result: dict[str, Any],
    projects_to_recalc: set[str],
) -> str | None:
    project_id = _required_text(request.get("project_id"), "project_id")
    project = state.projects.get(project_id)
    if project is None:
        result["errors"].append(f"git release request references missing project {project_id}")
        return None
    permitted = (
        _is_project_lead_for(state, actor.id, project_id)
        or _agent_has_role_permission_for_project(
            state,
            actor,
            RolePermission.REQUEST_RELEASE,
            project_id,
        )
        or (_is_team_manager(state, actor) and project.team_id == actor.team_id)
    )
    if not permitted:
        result["errors"].append(
            f"agent {actor.id} is not permitted to request releases on project {project_id}"
        )
        return None
    return _build_release_package(
        state,
        project=project,
        request=request,
        result=result,
        projects_to_recalc=projects_to_recalc,
        actor_agent_id=actor.id,
        team_id=actor.team_id,
    )

def _release_request_permitted(
    state: TeamState,
    *,
    actor: PersistentAgent,
    project: Project,
) -> bool:
    return (
        _is_project_lead_for(state, actor.id, project.id)
        or _agent_has_role_permission_for_project(
            state,
            actor,
            RolePermission.REQUEST_RELEASE,
            project.id,
        )
        or (_is_team_manager(state, actor) and project.team_id == actor.team_id)
    )

def _failed_release_promotion_for_recovery(
    state: TeamState,
    release: ReleasePackage,
) -> GitPromotionRecord | None:
    promotion_ids = set(release.git_promotion_record_ids)
    promotions = [
        promotion
        for promotion in state.git_promotions.values()
        if promotion.promotion_kind == "release"
        and promotion.state == "failed"
        and (promotion.id in promotion_ids or promotion.release_package_id == release.id)
    ]
    if not promotions:
        return None
    promotions.sort(
        key=lambda promotion: (
            promotion.updated_at,
            promotion.created_at,
            promotion.id,
        ),
        reverse=True,
    )
    return promotions[0]

def _apply_git_release_recovery(
    state: TeamState,
    *,
    actor: PersistentAgent,
    request: dict[str, Any],
    result: dict[str, Any],
) -> str | None:
    release_id = _required_text(request.get("release_package_id"), "release_package_id")
    release = state.release_packages.get(release_id)
    if release is None:
        result["errors"].append(
            f"git release recovery references missing release package {release_id}"
        )
        return None
    project = state.projects.get(release.project_id)
    if project is None:
        result["errors"].append(
            f"git release recovery references missing project {release.project_id}"
        )
        return None
    if not _release_request_permitted(state, actor=actor, project=project):
        result["errors"].append(
            f"agent {actor.id} is not permitted to recover releases on project {project.id}"
        )
        return None
    if release.state != TeamRecordState.BLOCKED:
        result["errors"].append(
            f"release package {release.id} is {release.state.value}; only blocked "
            "release packages can be recovered"
        )
        return None
    git = _project_git_or_error(project, result)
    if git is None:
        return None
    try:
        agent_branch = _normalize_agent_branch_ref(request.get("agent_branch"))
    except ValueError as exc:
        result["errors"].append(f"git release recovery has invalid agent_branch: {exc}")
        return None
    failed_promotion = _failed_release_promotion_for_recovery(state, release)
    if failed_promotion is None:
        result["errors"].append(
            f"release package {release.id} has no failed release promotion to recover"
        )
        return None
    target_branch = release.release_branch or failed_promotion.target_branch
    if release.release_branch and release.release_branch != failed_promotion.target_branch:
        result["errors"].append(
            f"release package {release.id} release_branch {release.release_branch} "
            f"does not match failed promotion target {failed_promotion.target_branch}"
        )
        return None
    target_branch = validate_branch_name(target_branch)
    expected_commit_sha = _required_text(request.get("commit_sha"), "commit_sha")
    payload: dict[str, Any] = {
        "release_package_id": release.id,
        "promotion_id": failed_promotion.id,
        "project_id": project.id,
        "host_repo_path": git.host_repo_path,
        "agent_branch": agent_branch,
        "target_branch": target_branch,
        "release_branch": target_branch,
        "canonical_branch": release.canonical_branch
        or canonical_release_branch(project, release.id),
        "base_branch": git.base_branch,
        "expected_commit_sha": expected_commit_sha,
    }
    payload.update(_bare_repo_fields(git))
    summary = _optional_text(request.get("summary"))
    if summary is not None:
        payload["summary"] = summary
    _emit_git_release_recovery_event(
        result,
        release_package_id=release.id,
        agent_branch=agent_branch,
        payload=payload,
    )
    return release.id

def _build_release_package(
    state: TeamState,
    *,
    project: Project,
    request: dict[str, Any],
    result: dict[str, Any],
    projects_to_recalc: set[str],
    actor_agent_id: str,
    team_id: str,
) -> str | None:
    """Validate deliverables and build a pending ReleasePackage with git promotions.

    Shared by the ``git_release_requests`` agent action and the Admin API manual
    release-package endpoint so both enforce identical validation and branch naming.
    """

    project_id = project.id
    deliverable_ids = _string_list(request.get("deliverable_ids"), "deliverable_ids")
    if not deliverable_ids:
        result["errors"].append(
            f"git release request for project {project_id} requires deliverable_ids"
        )
        return None
    artifact_refs: list[str] = []
    feature_branches: list[str] = []
    feature_sources_by_task: dict[str, list[str]] = {}
    valid = True
    for deliverable_id in deliverable_ids:
        deliverable = state.deliverables.get(deliverable_id)
        if deliverable is None:
            result["errors"].append(
                f"git release request references missing deliverable {deliverable_id}"
            )
            valid = False
            continue
        task = state.tasks.get(deliverable.task_id)
        if task is None or task.project_id != project_id:
            result["errors"].append(
                f"deliverable {deliverable_id} does not belong to project {project_id}"
            )
            valid = False
            continue
        if deliverable.state != TeamRecordState.ACCEPTED:
            result["errors"].append(
                f"deliverable {deliverable_id} is not accepted and cannot be released"
            )
            valid = False
            continue
        feature = deliverable.git_refs.feature_branch
        if feature:
            try:
                feature = validate_branch_name(feature)
            except ValueError as exc:
                result["errors"].append(
                    f"deliverable {deliverable_id} has invalid feature branch for release: {exc}"
                )
                valid = False
                continue
            feature_branches = _append_unique(feature_branches, feature)
        else:
            try:
                source_branch = _feature_promotion_source_branch_for_release(deliverable)
            except ValueError as exc:
                result["errors"].append(
                    f"deliverable {deliverable_id} has invalid Git refs for release: {exc}"
                )
                valid = False
                continue
            if source_branch is not None:
                feature_sources_by_task[task.id] = _append_unique(
                    feature_sources_by_task.get(task.id, []),
                    source_branch,
                )
            elif not deliverable.artifact_refs:
                result["errors"].append(
                    f"deliverable {deliverable_id} has neither a feature branch, "
                    "review branch, agent branch, nor artifact refs; Git release "
                    "requests can auto-promote accepted deliverables from "
                    "git_refs.review_branch or git_refs.agent_branch, or release "
                    "artifact-only deliverables with artifact refs"
                )
                valid = False
        for artifact_ref in deliverable.artifact_refs:
            artifact_refs = _append_unique(artifact_refs, artifact_ref)
    if not valid:
        return None
    release_id = _optional_text(request.get("id")) or f"release-{uuid4().hex}"
    if release_id in state.release_packages:
        result["errors"].append(
            _duplicate_release_package_error(state.release_packages[release_id])
        )
        return None
    target_release_branch: str | None = None
    target_canonical_branch: str | None = None
    git = None
    needs_git_release = bool(feature_branches or feature_sources_by_task)
    effective_team_id = team_id or project.team_id
    if needs_git_release:
        git = _project_git_or_error(project, result)
        if git is None:
            return None
        target_release_branch = validate_branch_name(
            _optional_text(request.get("release_branch"))
            or release_branch(effective_team_id, release_id)
        )
        target_canonical_branch = validate_branch_name(
            _optional_text(request.get("canonical_branch"))
            or canonical_release_branch(project, release_id)
        )
    promotion_ids: list[str] = []
    now = utc_now()
    if feature_sources_by_task:
        assert git is not None
        for task_id, source_branches in feature_sources_by_task.items():
            feature_promotion_id = f"promotion-{uuid4().hex}"
            state.git_promotions[feature_promotion_id] = GitPromotionRecord(
                id=feature_promotion_id,
                project_id=project.id,
                release_package_id=release_id,
                task_id=task_id,
                actor_agent_id=actor_agent_id,
                source_branch=source_branches[0],
                target_branch=feature_branch(effective_team_id, task_id, actor_agent_id),
                promotion_kind="feature",
            )
            promotion_ids.append(feature_promotion_id)
    state.release_packages[release_id] = ReleasePackage(
        id=release_id,
        project_id=project.id,
        deliverable_ids=deliverable_ids,
        artifact_refs=artifact_refs,
        feature_branches=feature_branches,
        release_branch=target_release_branch,
        canonical_branch=target_canonical_branch,
        git_promotion_record_ids=promotion_ids,
        state=TeamRecordState.PENDING,
        created_at=now,
        updated_at=now,
    )
    if feature_sources_by_task:
        assert git is not None
        for promotion_id in promotion_ids:
            promotion = state.git_promotions[promotion_id]
            assert promotion.task_id is not None
            payload = {
                "promotion_id": promotion_id,
                "promotion_kind": "feature",
                "project_id": project.id,
                "release_package_id": release_id,
                "host_repo_path": git.host_repo_path,
                "base_branch": git.base_branch,
                "source_branches": list(feature_sources_by_task[promotion.task_id]),
                "target_branch": promotion.target_branch,
                "task_id": promotion.task_id,
            }
            payload.update(_bare_repo_fields(git))
            _emit_git_promotion_event(
                result,
                promotion_id=promotion_id,
                payload=payload,
            )
        result["git_promotion_event_ids"].extend(promotion_ids)
    elif feature_branches:
        _queue_release_promotion_if_ready(
            state,
            release=state.release_packages[release_id],
            actor_agent_id=actor_agent_id,
            result=result,
        )
    projects_to_recalc.add(project_id)
    return release_id

def _duplicate_release_package_error(release: ReleasePackage) -> str:
    parts = [
        f"release package {release.id} already exists",
        f"state {release.state.value}",
    ]
    if release.release_branch:
        parts.append(f"release_branch {release.release_branch}")
    if release.canonical_branch:
        parts.append(f"canonical_branch {release.canonical_branch}")
    if release.error:
        parts.append(f"error {release.error}")
    parts.append(
        "do not submit git_release_requests for this id again; resolve the existing "
        "release package and follow its next_action_hint. Submit git_release_recoveries "
        "only when the hint identifies a failed release promotion and the resolved "
        "commit is pushed to an agents/... ref, or choose a new id only for a "
        "separate release"
    )
    return "; ".join(parts)

def _feature_promotion_source_branch_for_release(deliverable: Deliverable) -> str | None:
    refs = deliverable.git_refs
    source_branch = refs.review_branch or refs.agent_branch
    if source_branch is None:
        return None
    return validate_branch_name(source_branch)

def _release_feature_branches_from_deliverables(
    state: TeamState,
    release: ReleasePackage,
) -> list[str]:
    feature_branches = list(release.feature_branches)
    for deliverable_id in release.deliverable_ids:
        deliverable = state.deliverables.get(deliverable_id)
        if deliverable is None:
            continue
        feature_branch_value = deliverable.git_refs.feature_branch
        if feature_branch_value:
            feature_branches = _append_unique(feature_branches, feature_branch_value)
    return feature_branches

def _release_feature_prerequisites_ready(
    state: TeamState,
    release: ReleasePackage,
) -> bool:
    for promotion_id in release.git_promotion_record_ids:
        promotion = state.git_promotions.get(promotion_id)
        if promotion is None or promotion.promotion_kind != "feature":
            continue
        if promotion.state != "succeeded":
            return False
    return True

def _queue_release_promotion_if_ready(
    state: TeamState,
    *,
    release: ReleasePackage,
    actor_agent_id: str,
    result: dict[str, Any],
) -> None:
    if release.state in {
        TeamRecordState.ACCEPTED,
        TeamRecordState.REJECTED,
        TeamRecordState.BLOCKED,
    }:
        return
    if not _release_feature_prerequisites_ready(state, release):
        return
    existing = next(
        (
            state.git_promotions[promotion_id]
            for promotion_id in release.git_promotion_record_ids
            if promotion_id in state.git_promotions
            and state.git_promotions[promotion_id].promotion_kind == "release"
        ),
        None,
    )
    if existing is not None:
        return
    project = state.projects.get(release.project_id)
    git = project.git if project is not None else None
    feature_branches = _release_feature_branches_from_deliverables(state, release)
    release.feature_branches = feature_branches
    release.updated_at = utc_now()
    if not feature_branches:
        return
    if project is None or git is None:
        release.state = TeamRecordState.BLOCKED
        release.error = (
            f"project {release.project_id} has no git configuration for release promotion"
        )
        result["error"] = release.error
        return
    target_release_branch = release.release_branch or release_branch(
        project.team_id,
        release.id,
    )
    release_promotion = GitPromotionRecord(
        id=f"promotion-{uuid4().hex}",
        project_id=project.id,
        release_package_id=release.id,
        actor_agent_id=actor_agent_id,
        source_branch=feature_branches[0],
        target_branch=target_release_branch,
        promotion_kind="release",
    )
    state.git_promotions[release_promotion.id] = release_promotion
    release.release_branch = target_release_branch
    release.canonical_branch = release.canonical_branch or canonical_release_branch(
        project,
        release.id,
    )
    release.git_promotion_record_ids = _append_unique(
        release.git_promotion_record_ids,
        release_promotion.id,
    )
    payload = {
        "promotion_id": release_promotion.id,
        "promotion_kind": "release",
        "project_id": project.id,
        "release_package_id": release.id,
        "host_repo_path": git.host_repo_path,
        "base_branch": git.base_branch,
        "source_branches": list(feature_branches),
        "target_branch": target_release_branch,
    }
    payload.update(_bare_repo_fields(git))
    _emit_git_promotion_event(
        result,
        promotion_id=release_promotion.id,
        payload=payload,
    )
    result["git_promotion_event_ids"].append(release_promotion.id)

def _release_failure_recipient(state: TeamState, release: ReleasePackage) -> str | None:
    project = state.projects.get(release.project_id)
    if project is None:
        return None
    lead_agent_id = project_lead_agent_id(state, release.project_id)
    if lead_agent_id in state.agents:
        return lead_agent_id
    team = state.teams.get(project.team_id)
    return team.manager_agent_id if team is not None else None

__all__ = [
    "GIT_RELEASE_RECOVERY_EVENT_TYPE",
    "_emit_git_release_recovery_event",
    "_apply_git_release_request",
    "_release_request_permitted",
    "_failed_release_promotion_for_recovery",
    "_apply_git_release_recovery",
    "_build_release_package",
    "_duplicate_release_package_error",
    "_feature_promotion_source_branch_for_release",
    "_release_feature_branches_from_deliverables",
    "_release_feature_prerequisites_ready",
    "_queue_release_promotion_if_ready",
    "_release_failure_recipient",
]
