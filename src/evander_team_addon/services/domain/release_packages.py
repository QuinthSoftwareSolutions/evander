from __future__ import annotations

from typing import Any
from uuid import uuid4
from ...git_naming import (
    canonical_release_branch,
    correction_aggregate_branch,
    feature_branch,
    release_branch,
    validate_branch_name,
)
from ...models import (
    Deliverable,
    DeliverableReviewManifest,
    GitPromotionRecord,
    GitPromotionSource,
    InitialReleaseDispositionState,
    PersistentAgent,
    Project,
    ReleasePackage,
    ReleaseScopeValidation,
    ReleaseVerificationWaiver,
    RolePermission,
    TeamBlockerSourceKind,
    TeamBlockerState,
    TeamRecordState,
    TeamState,
    TeamTaskState,
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
    _release_candidate_branch,
    _required_text,
    _string_list,
    utc_now,
)
from .blockers import open_release_blocker
from .git_common import (
    _emit_git_promotion_event,
    _managed_repo_fields,
    _normalize_agent_branch_ref,
    _project_git_or_error,
)
from .git_work_contexts import authoritative_deliverable_git_base
from .initial_release import mark_initial_release_requested
from .task_assessment import (
    TaskAssessmentBoundaryError,
    require_release_scope,
    require_task_resolved,
)


GIT_RELEASE_RECOVERY_EVENT_TYPE = "team.git.release_recovery_requested"
_TERMINAL_RELEASE_STATES = {TeamRecordState.ACCEPTED, TeamRecordState.REJECTED}
_HUMAN_CORRECTION_AGGREGATION_TASK_ID_KEY = "human_correction_aggregation_task_id"


class ReleasePackageValidationError(ValueError):
    def __init__(self, *, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def allowed_release_parent_task_ids(
    state: TeamState,
    *,
    project_id: str,
    predecessor_release_package_id: str | None,
) -> set[str]:
    """Return validated coordination parents allowed in a successor release."""

    if predecessor_release_package_id is None:
        return set()
    predecessor_request_ids = {
        request.id
        for request in state.human_review_requests.values()
        if request.release_package_id == predecessor_release_package_id
        and request.state == "changes_requested"
    }
    allowed: set[str] = set()
    for case in state.cases.values():
        if (
            case.source != "human_release_review"
            or case.source_ref not in predecessor_request_ids
            or case.project_id != project_id
        ):
            continue
        parent_task_id = _optional_text(
            case.metadata.get(_HUMAN_CORRECTION_AGGREGATION_TASK_ID_KEY)
        )
        parent = state.tasks.get(parent_task_id) if parent_task_id is not None else None
        if (
            parent is not None
            and parent.case_id == case.id
            and parent.project_id == project_id
            and parent.parent_task_id is None
        ):
            allowed.add(parent.id)
    return allowed


def _release_waiver_requests(value: object) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("verification_waivers must be a JSON array")
    normalized: list[dict[str, str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"verification_waivers[{index}] must be a JSON object")
        normalized.append(
            {
                "deliverable_id": _required_text(
                    item.get("deliverable_id"),
                    f"verification_waivers[{index}].deliverable_id",
                ),
                "reason": _required_text(
                    item.get("reason"),
                    f"verification_waivers[{index}].reason",
                ),
            }
        )
    return normalized


def _reviewed_commit_for_release(
    state: TeamState,
    deliverable: Deliverable,
) -> tuple[str, str | None]:
    accepted_reviews = [
        state.review_requests[review_id]
        for review_id in deliverable.review_request_ids
        if review_id in state.review_requests
        and state.review_requests[review_id].state == TeamRecordState.ACCEPTED
        and state.review_requests[review_id].superseded_by_review_request_id is None
        and state.review_requests[review_id].retired_at is None
    ]
    if not accepted_reviews:
        return "", (
            f"git-backed deliverable {deliverable.id} has no accepted review with an "
            "immutable reviewed commit"
        )
    missing_snapshots = [
        review.id for review in accepted_reviews if review.reviewed_commit_sha is None
    ]
    if missing_snapshots:
        return "", (
            f"git-backed deliverable {deliverable.id} must be reopened and reviewed "
            "under the immutable-commit policy; missing snapshots on "
            + ", ".join(missing_snapshots)
        )
    commits = sorted(
        {review.reviewed_commit_sha for review in accepted_reviews if review.reviewed_commit_sha}
    )
    if len(commits) != 1:
        return "", (
            f"git-backed deliverable {deliverable.id} was accepted at multiple commits: "
            + ", ".join(commits)
        )
    if any(finding.state == "open" for finding in deliverable.carried_findings):
        return "", f"deliverable {deliverable.id} still has open carried review findings"
    if (
        deliverable.review_manifest is not None
        and deliverable.review_manifest.reviewed_commit_sha != commits[0]
    ):
        return "", (
            f"deliverable {deliverable.id} review manifest targets "
            f"{deliverable.review_manifest.reviewed_commit_sha}, not accepted reviewed "
            f"commit {commits[0]}"
        )
    return commits[0], None


def _emit_git_release_recovery_event(
    result: dict[str, Any],
    *,
    release_package_id: str,
    promotion_id: str,
    agent_branch: str,
    payload: dict[str, Any],
) -> None:
    recovery_key = f"{release_package_id}:{promotion_id}:{agent_branch}"
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
    waiver_actor_authorized = _is_project_lead_for(state, actor.id, project_id) or (
        _is_team_manager(state, actor) and project.team_id == actor.team_id
    )
    if request.get("verification_waivers") and not waiver_actor_authorized:
        result["errors"].append(
            "verification waivers require the project lead or top-level team manager"
        )
        return None
    request["waiver_actor_authorized"] = waiver_actor_authorized
    request["waiver_actor_kind"] = "agent"
    request["waiver_actor_id"] = actor.id
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


def _release_recovery_permitted(
    state: TeamState,
    *,
    actor: PersistentAgent,
    project: Project,
    release: ReleasePackage,
) -> bool:
    if _release_request_permitted(state, actor=actor, project=project):
        return True
    active_task_states = {
        TeamTaskState.PENDING,
        TeamTaskState.IN_PROGRESS,
        TeamTaskState.BLOCKED,
        TeamTaskState.WAITING_FOR_REVIEW,
    }
    for blocker in state.blockers.values():
        if (
            blocker.state != TeamBlockerState.OPEN
            or blocker.source_kind != TeamBlockerSourceKind.RELEASE_PACKAGE
            or blocker.source_id != release.id
        ):
            continue
        for task_id in blocker.affected_task_ids:
            task = state.tasks.get(task_id)
            if (
                task is not None
                and task.project_id == project.id
                and task.owner_agent_id == actor.id
                and task.created_by_actor == "system:git-merge-recovery"
                and task.state in active_task_states
            ):
                return True
    return False


def _failed_release_promotion_for_recovery(
    state: TeamState,
    release: ReleasePackage,
) -> GitPromotionRecord | None:
    promotion_ids = set(release.git_promotion_record_ids)
    promotions = [
        promotion
        for promotion in state.git_promotions.values()
        if promotion.promotion_kind
        in {
            "feature",
            "correction_aggregate",
            "release",
        }
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
    if not _release_recovery_permitted(
        state,
        actor=actor,
        project=project,
        release=release,
    ):
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
            f"release package {release.id} has no failed correction-aggregate, "
            "feature, or release promotion to recover"
        )
        return None
    target_branch = failed_promotion.target_branch
    if failed_promotion.promotion_kind == "release":
        target_branch = release.release_branch or failed_promotion.target_branch
        if release.release_branch and release.release_branch != failed_promotion.target_branch:
            result["errors"].append(
                f"release package {release.id} release_branch {release.release_branch} "
                f"does not match failed promotion target {failed_promotion.target_branch}"
            )
            return None
    target_branch = validate_branch_name(target_branch)
    expected_commit_sha = _required_text(request.get("commit_sha"), "commit_sha")
    _repair_release_review_manifests_from_promotion_evidence(
        state,
        release=release,
        result=result,
    )
    allowed_changed_paths, missing_manifest_ids = (
        _release_final_allowed_changed_paths(state, release)
        if failed_promotion.promotion_kind == "release"
        else _release_allowed_changed_paths(state, release)
    )
    if release.policy_version >= 2 and missing_manifest_ids:
        result["errors"].append(
            "release recovery cannot bypass missing reviewed manifests for: "
            + ", ".join(sorted(missing_manifest_ids))
        )
        return None
    payload: dict[str, Any] = {
        "release_package_id": release.id,
        "promotion_id": failed_promotion.id,
        "promotion_kind": failed_promotion.promotion_kind,
        "project_id": project.id,
        "managed_repo_id": git.managed_repo_id,
        "agent_branch": agent_branch,
        "target_branch": target_branch,
        "release_branch": release.release_branch,
        "canonical_branch": release.canonical_branch
        or canonical_release_branch(project, release.id),
        "base_branch": failed_promotion.git_base_branch or release.git_base_branch,
        "expected_commit_sha": expected_commit_sha,
        "source_branches": [agent_branch],
        "expected_source_commits": {agent_branch: expected_commit_sha},
        "allowed_changed_paths": (allowed_changed_paths if release.policy_version >= 2 else None),
        "commit_message": f"Integrate corrections for release {release.id}",
    }
    pinned_recovery_base = _optional_text(
        failed_promotion.base_commit_sha
        or failed_promotion.outputs.get("base_commit_sha")
        or release.base_commit_sha
    )
    if pinned_recovery_base is not None:
        payload["base_commit_sha"] = pinned_recovery_base
    if (
        failed_promotion.promotion_kind == "correction_aggregate"
        and release.base_commit_sha is not None
    ):
        payload["required_source_ancestor_commit_sha"] = release.base_commit_sha
    payload.update(_managed_repo_fields(git))
    summary = _optional_text(request.get("summary"))
    if summary is not None:
        payload["summary"] = summary
    _emit_git_release_recovery_event(
        result,
        release_package_id=release.id,
        promotion_id=failed_promotion.id,
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
    requested_deliverable_ids = _string_list(
        request.get("deliverable_ids"),
        "deliverable_ids",
    )
    if not requested_deliverable_ids:
        result["errors"].append(
            f"git release request for project {project_id} requires deliverable_ids"
        )
        return None
    supersedes_release_package_id = _optional_text(request.get("supersedes_release_package_id"))
    post_release_feedback_case_id = _optional_text(request.get("post_release_feedback_case_id"))
    if post_release_feedback_case_id is not None and supersedes_release_package_id is None:
        result["errors"].append("post_release_feedback_case_id requires a predecessor release")
        return None
    predecessor: ReleasePackage | None = None
    correction_deliverable_ids = list(requested_deliverable_ids)
    if supersedes_release_package_id is not None:
        predecessor = state.release_packages.get(supersedes_release_package_id)
        successor_error = _successor_release_request_error(
            state,
            project_id=project_id,
            predecessor=predecessor,
            correction_deliverable_ids=correction_deliverable_ids,
            post_release_feedback_case_id=post_release_feedback_case_id,
        )
        if successor_error is not None:
            result["errors"].append(successor_error)
            return None
        assert predecessor is not None
        deliverable_ids = [
            *predecessor.deliverable_ids,
            *(
                deliverable_id
                for deliverable_id in correction_deliverable_ids
                if deliverable_id not in predecessor.deliverable_ids
            ),
        ]
        result["predecessor_release_package_id"] = predecessor.id
        result["correction_deliverable_ids"] = correction_deliverable_ids
        if post_release_feedback_case_id is not None:
            result["post_release_feedback_case_id"] = post_release_feedback_case_id
    else:
        deliverable_ids = requested_deliverable_ids
        not_planned_ids = sorted(
            deliverable_id
            for deliverable_id in deliverable_ids
            if (
                (deliverable := state.deliverables.get(deliverable_id)) is not None
                and deliverable.initial_release_disposition is not None
                and deliverable.initial_release_disposition.state
                == InitialReleaseDispositionState.NOT_PLANNED
            )
        )
        if not_planned_ids:
            result["errors"].append(
                "initial release is marked not planned for: "
                + ", ".join(not_planned_ids)
                + "; reopen the initial release decision before requesting a package"
            )
            return None

    predecessor_candidate_branch = (
        _release_candidate_branch(predecessor) if predecessor is not None else None
    )

    def _release_input_git_base(
        deliverable: Deliverable,
    ) -> tuple[str | None, str | None]:
        branch, commit = authoritative_deliverable_git_base(
            state,
            deliverable=deliverable,
        )
        if predecessor is None:
            return branch, commit
        downstream_recovery_base = _verification_downstream_recovery_base(
            state,
            predecessor=predecessor,
            deliverable=deliverable,
        )
        if downstream_recovery_base is not None:
            expected_branch, expected_commit = downstream_recovery_base
        else:
            expected_branch = predecessor_candidate_branch
            expected_commit = predecessor.candidate_commit_sha
        if expected_branch is None or expected_commit is None:
            raise ValueError(
                f"predecessor release {predecessor.id} has no exact rejected "
                "candidate Git provenance"
            )
        task = state.tasks.get(deliverable.task_id)
        if (
            task is not None
            and task.git_base_branch is not None
            and task.git_base_branch != expected_branch
        ):
            raise ValueError(
                f"correction task {task.id} base {task.git_base_branch!r} does not "
                f"match rejected candidate branch {expected_branch!r}"
            )
        if commit is not None and commit.casefold() != expected_commit.casefold():
            raise ValueError(
                f"correction deliverable {deliverable.id} base commit {commit} does "
                f"not match rejected candidate {expected_commit}"
            )
        return expected_branch, expected_commit

    allowed_parent_task_ids = allowed_release_parent_task_ids(
        state,
        project_id=project_id,
        predecessor_release_package_id=(predecessor.id if predecessor is not None else None),
    )
    try:
        release_scope = require_release_scope(
            state,
            project_id=project_id,
            deliverable_ids=deliverable_ids,
            allowed_parent_task_ids=allowed_parent_task_ids,
        )
    except TaskAssessmentBoundaryError as exc:
        result["errors"].append(exc.message)
        result["task_assessment_error"] = {
            "code": exc.code,
            **exc.correction_context,
        }
        return None
    result["source_deliverable_lineage"] = release_scope["source_lineage"]
    preflight_git_base_branches: set[str] = set()
    for deliverable_id in (
        correction_deliverable_ids if predecessor is not None else deliverable_ids
    ):
        deliverable = state.deliverables.get(deliverable_id)
        if deliverable is None:
            continue
        refs = deliverable.git_refs
        if not any(
            (
                refs.branch_context_id,
                refs.agent_branch,
                refs.review_branch,
                refs.feature_branch,
                refs.release_branch,
                refs.commit_sha,
            )
        ):
            continue
        if project.git is None:
            result["errors"].append(
                f"project {project.id} has no git configuration for release promotion"
            )
            return None
        try:
            branch, _commit = _release_input_git_base(deliverable)
        except ValueError as exc:
            result["errors"].append(str(exc))
            return None
        if branch is not None:
            preflight_git_base_branches.add(branch)
    if len(preflight_git_base_branches) > 1:
        result["error_code"] = "mixed_git_base_branches"
        result["errors"].append(
            "mixed_git_base_branches: Git release inputs use incompatible task "
            "base branches; create an explicit integration task on the selected "
            "base and release only its accepted deliverable"
        )
        return None
    artifact_refs: list[str] = []
    feature_branches: list[str] = []
    feature_sources_by_task: dict[str, list[str]] = {}
    source_expected_commits: dict[str, str] = {}
    waiver_requests = _release_waiver_requests(request.get("verification_waivers"))
    waivers_by_deliverable = {item["deliverable_id"]: item for item in waiver_requests}
    if len(waivers_by_deliverable) != len(waiver_requests):
        result["errors"].append("verification_waivers contains duplicate deliverable_id")
        return None
    verification_waivers: list[ReleaseVerificationWaiver] = []
    if predecessor is not None:
        verification_waivers.extend(
            waiver.model_copy(deep=True)
            for waiver in predecessor.verification_waivers
            if waiver.deliverable_id in predecessor.deliverable_ids
        )
    reviewed_commit_shas: list[str] = []
    git_base_snapshots: set[tuple[str | None, str | None]] = set()
    valid = True
    promotion_deliverable_ids = set(
        correction_deliverable_ids if predecessor is not None else deliverable_ids
    )
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
        if deliverable.review_manifest is None:
            _repair_deliverable_review_manifest_from_promotion_evidence(
                state,
                deliverable=deliverable,
                result=result,
            )
        verification_status = (
            deliverable.verification.status if deliverable.verification is not None else "missing"
        )
        waiver_request = waivers_by_deliverable.get(deliverable_id)
        inherited_waiver = next(
            (waiver for waiver in verification_waivers if waiver.deliverable_id == deliverable_id),
            None,
        )
        if verification_status == "failed":
            result["errors"].append(
                f"deliverable {deliverable_id} has failed verification and cannot be released"
            )
            valid = False
            continue
        if verification_status == "passed" and waiver_request is not None:
            result["errors"].append(
                f"deliverable {deliverable_id} passed verification; its waiver is unnecessary"
            )
            valid = False
            continue
        if verification_status in {"partial", "not_run", "unknown", "missing"}:
            if waiver_request is None and inherited_waiver is None:
                result["errors"].append(
                    f"deliverable {deliverable_id} has {verification_status} verification; "
                    "an authorized verification waiver with a reason is required"
                )
                result.setdefault("verification_waiver_requirements", []).append(
                    {
                        "deliverable_id": deliverable_id,
                        "verification_status": verification_status,
                        "caller_authorized": (
                            request.get("waiver_actor_kind") == "operator"
                            or bool(request.get("waiver_actor_authorized"))
                        ),
                        "authorized_actor_roles": [
                            "project_lead",
                            "top_level_team_manager",
                            "operator",
                        ],
                        "required_input": {
                            "verification_waivers": [
                                {
                                    "deliverable_id": deliverable_id,
                                    "reason": "<concrete evidence-based reason>",
                                }
                            ]
                        },
                        "effect": (
                            "Admits incomplete deliverable evidence at package intake; "
                            "configured release verification and review gates still apply."
                        ),
                    }
                )
                valid = False
                continue
            if waiver_request is not None:
                actor_kind = _optional_text(request.get("waiver_actor_kind"))
                actor_id = _optional_text(request.get("waiver_actor_id"))
                if actor_kind not in {"agent", "operator"} or actor_id is None:
                    result["errors"].append("verification waiver actor identity is required")
                    valid = False
                    continue
                verification_waivers.append(
                    ReleaseVerificationWaiver(
                        deliverable_id=deliverable_id,
                        verification_status=verification_status,
                        reason=waiver_request["reason"],
                        actor_kind=actor_kind,
                        actor_id=actor_id,
                    )
                )
        if deliverable_id not in promotion_deliverable_ids:
            for artifact_ref in deliverable.artifact_refs:
                artifact_refs = _append_unique(artifact_refs, artifact_ref)
            continue
        feature = deliverable.git_refs.feature_branch
        if feature:
            try:
                git_base_snapshots.add(_release_input_git_base(deliverable))
            except ValueError as exc:
                result["errors"].append(str(exc))
                valid = False
                continue
            reviewed_commit_sha, review_error = _reviewed_commit_for_release(
                state,
                deliverable,
            )
            if review_error is not None:
                result["errors"].append(review_error)
                valid = False
                continue
            reviewed_commit_shas = _append_unique(
                reviewed_commit_shas,
                reviewed_commit_sha,
            )
            try:
                feature = validate_branch_name(feature)
            except ValueError as exc:
                result["errors"].append(
                    f"deliverable {deliverable_id} has invalid feature branch for release: {exc}"
                )
                valid = False
                continue
            if deliverable.git_refs.feature_commit_sha is None:
                result["errors"].append(
                    f"deliverable {deliverable_id} has no immutable feature commit; "
                    "reconsolidate it under the current release policy"
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
                try:
                    git_base_snapshots.add(_release_input_git_base(deliverable))
                except ValueError as exc:
                    result["errors"].append(str(exc))
                    valid = False
                    continue
                reviewed_commit_sha, review_error = _reviewed_commit_for_release(
                    state,
                    deliverable,
                )
                if review_error is not None:
                    result["errors"].append(review_error)
                    valid = False
                    continue
                feature_sources_by_task[task.id] = _append_unique(
                    feature_sources_by_task.get(task.id, []),
                    source_branch,
                )
                previous_expected = source_expected_commits.get(source_branch)
                if previous_expected is not None and previous_expected != reviewed_commit_sha:
                    result["errors"].append(
                        f"source branch {source_branch} has multiple reviewed commits in one release"
                    )
                    valid = False
                    continue
                source_expected_commits[source_branch] = reviewed_commit_sha
                reviewed_commit_shas = _append_unique(
                    reviewed_commit_shas,
                    reviewed_commit_sha,
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
    needs_git_release = bool(feature_branches or feature_sources_by_task)
    if project.git is not None and not needs_git_release:
        result["error_code"] = "candidate_git_inputs_required"
        result["errors"].append(
            "candidate_git_inputs_required: Git-backed project "
            f"{project.id} requires at least one accepted Git-backed deliverable "
            "as a direct release input; artifact refs and source_deliverable_ids "
            "are supporting evidence and cannot produce a pinned candidate. "
            "Create an explicit integration task when accepted contributions use "
            "different Git bases, then release its accepted Git-backed deliverable"
        )
        return None
    release_git_base_branch: str | None = None
    release_base_commit_sha: str | None = None
    if needs_git_release:
        base_branches = {branch for branch, _commit in git_base_snapshots if branch is not None}
        base_commits = {commit for _branch, commit in git_base_snapshots if commit is not None}
        if len(base_branches) != 1 or len(base_commits) != 1 or len(git_base_snapshots) != 1:
            result["error_code"] = "mixed_git_base_branches"
            result["errors"].append(
                "mixed_git_base_branches: Git release inputs must share one "
                "authoritative task base branch and commit; create an explicit "
                "integration task on the selected base and release only its "
                "accepted deliverable"
            )
            return None
        release_git_base_branch = next(iter(base_branches))
        release_base_commit_sha = next(iter(base_commits))
    if predecessor is not None and not needs_git_release:
        result["errors"].append(
            "successor release correction deliverables must include reviewed Git "
            "content so a changed candidate can enter release gates"
        )
        return None
    unknown_waiver_ids = sorted(set(waivers_by_deliverable) - promotion_deliverable_ids)
    if unknown_waiver_ids:
        result["errors"].append(
            "verification waivers reference deliverables outside the release: "
            + ", ".join(unknown_waiver_ids)
        )
        return None
    release_id = _optional_text(request.get("id")) or f"release-{uuid4().hex}"
    if release_id in state.release_packages:
        result["errors"].append(
            _duplicate_release_package_error(state.release_packages[release_id])
        )
        return None
    duplicate = _active_release_package_for_deliverables(
        state,
        project_id=project_id,
        deliverable_ids=deliverable_ids,
    )
    if duplicate is not None:
        disposition_ids = mark_initial_release_requested(
            state,
            deliverable_ids=deliverable_ids,
            release_package_id=duplicate.id,
            decided_by_actor=(
                _optional_text(request.get("release_requested_by_actor"))
                or f"agent:{actor_agent_id}"
            ),
            now=utc_now(),
        )
        result["updated_initial_release_disposition_ids"] = disposition_ids
        result["duplicate"] = True
        result["created"] = False
        result["existing_release_package_id"] = duplicate.id
        result["release_package_id"] = duplicate.id
        result.setdefault("next_action_hints", []).append(
            _same_deliverable_release_package_hint(duplicate)
        )
        return duplicate.id
    target_release_branch: str | None = None
    target_canonical_branch: str | None = None
    git = None
    effective_team_id = team_id or project.team_id
    if needs_git_release:
        git = _project_git_or_error(project, result)
        if git is None:
            return None
        if project.release_policy is None:
            result["errors"].append(
                f"project {project.id} has no release policy; configure candidate "
                "verification and release review gates before requesting a Git release"
            )
            return None
        target_release_branch = validate_branch_name(
            _optional_text(request.get("release_branch"))
            or release_branch(effective_team_id, release_id)
        )
        target_canonical_branch = validate_branch_name(
            _optional_text(request.get("canonical_branch"))
            or (predecessor.canonical_branch if predecessor is not None else None)
            or canonical_release_branch(project, release_id)
        )
    canonical_base_commit_sha = release_base_commit_sha
    if predecessor is not None and target_canonical_branch == predecessor.canonical_branch:
        if predecessor.state == TeamRecordState.ACCEPTED:
            canonical_base_commit_sha = (
                predecessor.published_commit_sha
                or predecessor.approved_commit_sha
                or predecessor.candidate_commit_sha
            )
        else:
            canonical_base_commit_sha = (
                predecessor.canonical_base_commit_sha or predecessor.base_commit_sha
            )
    promotion_ids: list[str] = []
    now = utc_now()
    is_candidate_correction_successor = (
        predecessor is not None
        and predecessor.state == TeamRecordState.REJECTED
        and predecessor.gate_failure is not None
        and (
            (
                predecessor.gate_failure.stage == "human_review"
                and predecessor.gate_failure.kind == "changes_requested"
            )
            or (
                predecessor.gate_failure.stage == "verification"
                and predecessor.gate_failure.kind == "candidate"
            )
            or (
                predecessor.gate_failure.stage == "review"
                and predecessor.gate_failure.kind == "rejected"
            )
        )
    )
    if feature_sources_by_task and not is_candidate_correction_successor:
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
                git_base_branch=release_git_base_branch,
                base_commit_sha=release_base_commit_sha,
            )
            promotion_ids.append(feature_promotion_id)
    state.release_packages[release_id] = ReleasePackage(
        id=release_id,
        project_id=project.id,
        owner_agent_id=actor_agent_id,
        summary=_optional_text(request.get("summary")),
        supersedes_release_package_id=(predecessor.id if predecessor is not None else None),
        deliverable_ids=deliverable_ids,
        artifact_refs=artifact_refs,
        feature_branches=feature_branches,
        release_branch=target_release_branch,
        canonical_branch=target_canonical_branch,
        git_base_branch=(
            predecessor_candidate_branch if predecessor is not None else release_git_base_branch
        ),
        base_commit_sha=(
            predecessor.candidate_commit_sha if predecessor is not None else release_base_commit_sha
        ),
        canonical_base_commit_sha=canonical_base_commit_sha,
        git_promotion_record_ids=promotion_ids,
        policy_version=2,
        verification_waivers=verification_waivers,
        scope_validation=(
            ReleaseScopeValidation(
                state="pending",
                reviewed_commit_shas=reviewed_commit_shas,
            )
            if needs_git_release
            else ReleaseScopeValidation(state="not_required")
        ),
        state=TeamRecordState.PENDING,
        created_at=now,
        updated_at=now,
    )
    disposition_ids = mark_initial_release_requested(
        state,
        deliverable_ids=deliverable_ids,
        release_package_id=release_id,
        decided_by_actor=(
            _optional_text(request.get("release_requested_by_actor")) or f"agent:{actor_agent_id}"
        ),
        now=now,
    )
    result["updated_initial_release_disposition_ids"] = disposition_ids
    result["created"] = True
    result["duplicate"] = False
    result["release_package_id"] = release_id
    release = state.release_packages[release_id]
    if is_candidate_correction_successor:
        assert predecessor is not None
        assert git is not None
        aggregate = _create_correction_aggregate_promotion(
            state,
            release=release,
            predecessor=predecessor,
            correction_deliverable_ids=correction_deliverable_ids,
            actor_agent_id=actor_agent_id,
            result=result,
        )
        if aggregate is None:
            state.release_packages.pop(release_id, None)
            for promotion_id in promotion_ids:
                state.git_promotions.pop(promotion_id, None)
            return None
        promotion_ids = [aggregate.id]
        release.git_promotion_record_ids = promotion_ids
        result["git_promotion_event_ids"].append(aggregate.id)
    elif feature_sources_by_task:
        assert git is not None
        for promotion_id in promotion_ids:
            promotion = state.git_promotions[promotion_id]
            assert promotion.task_id is not None
            payload = {
                "promotion_id": promotion_id,
                "promotion_kind": "feature",
                "project_id": project.id,
                "release_package_id": release_id,
                "managed_repo_id": git.managed_repo_id,
                "base_branch": promotion.git_base_branch,
                "base_commit_sha": promotion.base_commit_sha,
                "source_branches": list(feature_sources_by_task[promotion.task_id]),
                "expected_source_commits": {
                    branch: source_expected_commits[branch]
                    for branch in feature_sources_by_task[promotion.task_id]
                },
                "target_branch": promotion.target_branch,
                "task_id": promotion.task_id,
                "commit_message": (f"Integrate accepted work for task {promotion.task_id}"),
            }
            payload.update(_managed_repo_fields(git))
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


def _ordered_correction_deliverable_ids(
    state: TeamState,
    *,
    predecessor: ReleasePackage,
    correction_deliverable_ids: list[str],
) -> list[str]:
    """Order correction sources by finding and then deliverable identity."""

    request_ids = {
        request.id
        for request in state.human_review_requests.values()
        if request.release_package_id == predecessor.id and request.state == "changes_requested"
    }
    correction_set = set(correction_deliverable_ids)
    finding_keys: dict[str, tuple[str, str]] = {}
    for finding in state.human_review_findings.values():
        if finding.source_human_review_request_id not in request_ids:
            continue
        for deliverable_id in finding.proposed_by_deliverable_ids:
            if deliverable_id not in correction_set:
                continue
            key = (finding.id, deliverable_id)
            previous = finding_keys.get(deliverable_id)
            if previous is None or key < previous:
                finding_keys[deliverable_id] = key
    return sorted(
        correction_set,
        key=lambda deliverable_id: (
            finding_keys.get(deliverable_id, ("\uffff", deliverable_id)),
            deliverable_id,
        ),
    )


def _create_correction_aggregate_promotion(
    state: TeamState,
    *,
    release: ReleasePackage,
    predecessor: ReleasePackage,
    correction_deliverable_ids: list[str],
    actor_agent_id: str,
    result: dict[str, Any],
) -> GitPromotionRecord | None:
    """Queue one rejected-candidate-rooted aggregate for all reviewed corrections."""

    project = state.projects.get(release.project_id)
    git = project.git if project is not None else None
    rejected_candidate = predecessor.candidate_commit_sha
    if project is None or git is None or rejected_candidate is None:
        result["errors"].append(
            f"successor release {release.id} has no immutable rejected Git candidate"
        )
        return None
    ordered_ids = _ordered_correction_deliverable_ids(
        state,
        predecessor=predecessor,
        correction_deliverable_ids=correction_deliverable_ids,
    )
    source_rows: list[tuple[str, str, str]] = []
    seen_branches: set[str] = set()
    for deliverable_id in ordered_ids:
        deliverable = state.deliverables.get(deliverable_id)
        if deliverable is None:
            result["errors"].append(f"successor correction deliverable {deliverable_id} is missing")
            return None
        try:
            branch = _feature_promotion_source_branch_for_release(deliverable)
        except ValueError as exc:
            result["errors"].append(
                f"correction deliverable {deliverable_id} has invalid Git refs: {exc}"
            )
            return None
        reviewed_commit_sha, review_error = _reviewed_commit_for_release(
            state,
            deliverable,
        )
        if review_error is not None:
            result["errors"].append(review_error)
            return None
        if branch is None:
            feature = deliverable.git_refs.feature_branch
            feature_commit = deliverable.git_refs.feature_commit_sha
            if feature is None or feature_commit != reviewed_commit_sha:
                result["errors"].append(
                    f"correction deliverable {deliverable_id} has no reviewed source "
                    "branch pinned to its accepted commit"
                )
                return None
            branch = validate_branch_name(feature)
        if branch in seen_branches:
            result["errors"].append(
                f"correction source branch {branch} is reused by multiple deliverables"
            )
            return None
        seen_branches.add(branch)
        source_rows.append((deliverable_id, branch, reviewed_commit_sha))
    if not source_rows:
        result["errors"].append(
            f"successor release {release.id} has no reviewed correction sources"
        )
        return None

    promotion_id = f"promotion-{uuid4().hex}"
    target_branch = correction_aggregate_branch(project.team_id, release.id)
    promotion = GitPromotionRecord(
        id=promotion_id,
        project_id=project.id,
        release_package_id=release.id,
        actor_agent_id=actor_agent_id,
        source_branch=source_rows[0][1],
        sources=[
            GitPromotionSource(
                source_branch=branch,
                expected_commit_sha=commit_sha,
            )
            for _deliverable_id, branch, commit_sha in source_rows
        ],
        target_branch=target_branch,
        promotion_kind="correction_aggregate",
        git_base_branch=release.git_base_branch,
        base_commit_sha=rejected_candidate,
        outputs={
            "correction_deliverable_ids": [
                deliverable_id for deliverable_id, _branch, _sha in source_rows
            ],
            "required_source_ancestor_commit_sha": rejected_candidate,
        },
    )
    state.git_promotions[promotion.id] = promotion
    release.git_promotion_record_ids = _append_unique(
        release.git_promotion_record_ids,
        promotion.id,
    )
    payload: dict[str, Any] = {
        "promotion_id": promotion.id,
        "promotion_kind": "correction_aggregate",
        "project_id": project.id,
        "release_package_id": release.id,
        "managed_repo_id": git.managed_repo_id,
        "base_branch": release.git_base_branch,
        "base_commit_sha": rejected_candidate,
        "required_source_ancestor_commit_sha": rejected_candidate,
        "source_branches": [branch for _deliverable_id, branch, _sha in source_rows],
        "expected_source_commits": {
            branch: commit_sha for _deliverable_id, branch, commit_sha in source_rows
        },
        "target_branch": target_branch,
        "commit_message": f"Integrate corrections for release {release.id}",
    }
    payload.update(_managed_repo_fields(git))
    _emit_git_promotion_event(result, promotion_id=promotion.id, payload=payload)
    return promotion


def _successor_release_request_error(
    state: TeamState,
    *,
    project_id: str,
    predecessor: ReleasePackage | None,
    correction_deliverable_ids: list[str],
    post_release_feedback_case_id: str | None = None,
) -> str | None:
    if predecessor is None:
        return "superseded release package does not exist"
    if predecessor.project_id != project_id:
        return (
            f"predecessor release {predecessor.id} belongs to project "
            f"{predecessor.project_id}, not {project_id}"
        )
    requested_correction_set = set(correction_deliverable_ids)
    for successor in sorted(
        state.release_packages.values(),
        key=lambda item: (item.created_at, item.id),
    ):
        if (
            successor.supersedes_release_package_id != predecessor.id
            or successor.state in _TERMINAL_RELEASE_STATES
        ):
            continue
        existing_correction_set = set(successor.deliverable_ids) - set(predecessor.deliverable_ids)
        if existing_correction_set != requested_correction_set:
            return (
                f"predecessor release {predecessor.id} already has active "
                f"successor {successor.id}; complete or recover it before "
                "requesting a different successor"
            )
    if predecessor.state == TeamRecordState.ACCEPTED:
        return _post_release_successor_request_error(
            state,
            predecessor=predecessor,
            correction_deliverable_ids=correction_deliverable_ids,
            post_release_feedback_case_id=post_release_feedback_case_id,
        )
    if post_release_feedback_case_id is not None:
        return "post_release_feedback_case_id can authorize only a successor of a published release"
    if (
        predecessor.state == TeamRecordState.REJECTED
        and predecessor.gate_failure is not None
        and predecessor.gate_failure.stage == "verification"
        and predecessor.gate_failure.kind == "candidate"
    ):
        return _verification_correction_successor_request_error(
            state,
            predecessor=predecessor,
            correction_deliverable_ids=correction_deliverable_ids,
        )
    if (
        predecessor.state == TeamRecordState.REJECTED
        and predecessor.gate_failure is not None
        and predecessor.gate_failure.stage == "review"
        and predecessor.gate_failure.kind == "rejected"
    ):
        return _release_review_correction_successor_request_error(
            state,
            predecessor=predecessor,
            correction_deliverable_ids=correction_deliverable_ids,
        )
    if (
        predecessor.state != TeamRecordState.REJECTED
        or predecessor.gate_failure is None
        or predecessor.gate_failure.stage != "human_review"
        or predecessor.gate_failure.kind != "changes_requested"
    ):
        return f"predecessor release {predecessor.id} did not end in human changes_requested"
    if (
        predecessor.candidate_commit_sha is None
        or predecessor.candidate_tree_hash is None
        or predecessor.manifest is None
    ):
        return f"predecessor release {predecessor.id} has no immutable candidate"
    predecessor_request = next(
        (
            request
            for request in state.human_review_requests.values()
            if request.release_package_id == predecessor.id
        ),
        None,
    )
    if predecessor_request is None or predecessor_request.state != "changes_requested":
        return (
            f"predecessor release {predecessor.id} has no terminal changes-requested human review"
        )
    correction_cases = sorted(
        (
            case
            for case in state.cases.values()
            if case.source == "human_release_review"
            and case.source_ref == predecessor_request.id
            and case.project_id == project_id
        ),
        key=lambda item: item.id,
    )
    if len(correction_cases) != 1:
        return (
            f"predecessor human review {predecessor_request.id} requires exactly "
            "one correction case"
        )
    correction_case = correction_cases[0]
    coordination_task_id = _optional_text(
        correction_case.metadata.get("human_correction_aggregation_task_id")
    )
    correction_case_task_ids = sorted(
        set(correction_case.task_ids)
        | {task.id for task in state.tasks.values() if task.case_id == correction_case.id}
    )
    for task_id in correction_case_task_ids:
        if task_id == coordination_task_id:
            continue
        try:
            require_task_resolved(
                state,
                task_id,
                boundary=(f"successor release for human review case {correction_case.id}"),
            )
        except TaskAssessmentBoundaryError as exc:
            return exc.message
    if not correction_deliverable_ids:
        return "successor release requires accepted correction deliverables"
    duplicate_ids = sorted(
        set(correction_deliverable_ids).intersection(predecessor.deliverable_ids)
    )
    if duplicate_ids:
        return "successor correction deliverables are already in predecessor scope: " + ", ".join(
            duplicate_ids
        )
    open_findings = [
        finding
        for finding in state.human_review_findings.values()
        if finding.source_human_review_request_id == predecessor_request.id
        and finding.state == "open"
    ]
    if not open_findings:
        return f"predecessor human review {predecessor_request.id} has no open findings to carry"
    correction_set = set(correction_deliverable_ids)
    proposed_correction_deliverable_ids = {
        deliverable_id
        for finding in open_findings
        for deliverable_id in finding.proposed_by_deliverable_ids
    }
    for finding in open_findings:
        try:
            require_task_resolved(
                state,
                finding.correction_task_id,
                boundary=f"successor release for finding {finding.id}",
            )
        except TaskAssessmentBoundaryError as exc:
            return exc.message
        if not finding.proposed_resolution:
            return f"human finding {finding.id} has no proposed resolution"
        if not finding.resolution_evidence_refs:
            return f"human finding {finding.id} has no resolution evidence"
        if not finding.proposed_by_deliverable_ids:
            return f"human finding {finding.id} has no accepted correction deliverable"
        missing = sorted(set(finding.proposed_by_deliverable_ids) - correction_set)
        if missing:
            return (
                f"human finding {finding.id} proposes deliverables outside the "
                f"successor correction scope: {', '.join(missing)}"
            )
    for deliverable_id in correction_deliverable_ids:
        deliverable = state.deliverables.get(deliverable_id)
        if (
            deliverable is None
            or deliverable.state != TeamRecordState.ACCEPTED
            or deliverable.superseded_by_deliverable_id is not None
        ):
            return (
                f"successor correction deliverable {deliverable_id} is not a "
                "current accepted deliverable"
            )
        if deliverable_id not in proposed_correction_deliverable_ids:
            return (
                f"successor correction deliverable {deliverable_id} is not "
                f"authorized by human review case {correction_case.id}"
            )
    return None


def _verification_downstream_recovery_base(
    state: TeamState,
    *,
    predecessor: ReleasePackage,
    deliverable: Deliverable,
) -> tuple[str, str] | None:
    """Return the immutable accepted-correction base for a safe recovery task."""

    gate_failure = predecessor.gate_failure
    if (
        gate_failure is None
        or gate_failure.stage != "verification"
        or gate_failure.kind != "candidate"
    ):
        return None
    verification_run = state.release_verification_runs.get(gate_failure.source_id or "")
    if verification_run is None or verification_run.correction_task_id is None:
        return None
    correction_task = state.tasks.get(verification_run.correction_task_id)
    recovery_task = state.tasks.get(deliverable.task_id)
    if (
        correction_task is None
        or recovery_task is None
        or recovery_task.originating_task_id != correction_task.id
        or recovery_task.parent_task_id is not None
        or deliverable.state != TeamRecordState.ACCEPTED
        or deliverable.superseded_by_deliverable_id is not None
        or deliverable.verification is None
        or deliverable.verification.status != "passed"
    ):
        return None
    try:
        require_task_resolved(
            state,
            recovery_task.id,
            boundary=(f"verified downstream recovery for correction task {correction_task.id}"),
        )
    except TaskAssessmentBoundaryError:
        return None
    recovery_branch, recovery_commit = authoritative_deliverable_git_base(
        state,
        deliverable=deliverable,
    )
    if recovery_branch is None or recovery_commit is None:
        return None
    recovery_context = (
        state.git_work_contexts.get(deliverable.git_refs.branch_context_id)
        if deliverable.git_refs.branch_context_id is not None
        else None
    )
    for correction_deliverable in state.deliverables.values():
        if (
            correction_deliverable.task_id != correction_task.id
            or correction_deliverable.state != TeamRecordState.ACCEPTED
            or correction_deliverable.superseded_by_deliverable_id is not None
        ):
            continue
        correction_branch = correction_deliverable.git_refs.agent_branch
        correction_commit = (
            correction_deliverable.git_refs.submitted_commit_sha
            or correction_deliverable.git_refs.commit_sha
        )
        if (
            correction_branch is None
            or correction_commit is None
            or correction_commit.casefold() != recovery_commit.casefold()
            or recovery_task.git_base_branch != recovery_branch
            or deliverable.created_at < correction_deliverable.created_at
        ):
            continue
        if not recovery_task.dependency_task_ids:
            if correction_branch == recovery_branch:
                return recovery_branch, correction_commit
            continue
        if (
            recovery_task.dependency_task_ids != [correction_task.id]
            or recovery_context is None
            or recovery_context.manual_dependency_git_sources
            or len(recovery_context.dependency_git_sources) != 1
        ):
            continue
        dependency_source = recovery_context.dependency_git_sources[0]
        if (
            dependency_source.dependency_task_id == correction_task.id
            and dependency_source.deliverable_id == correction_deliverable.id
            and dependency_source.branch == correction_branch
            and dependency_source.commit_sha == correction_commit.casefold()
        ):
            return recovery_branch, correction_commit
    return None


def _verification_correction_successor_request_error(
    state: TeamState,
    *,
    predecessor: ReleasePackage,
    correction_deliverable_ids: list[str],
) -> str | None:
    if (
        predecessor.candidate_commit_sha is None
        or predecessor.candidate_tree_hash is None
        or predecessor.manifest is None
        or predecessor.manifest.evidence_status != "complete"
    ):
        return f"predecessor release {predecessor.id} has no immutable candidate"
    gate_failure = predecessor.gate_failure
    assert gate_failure is not None
    verification_run = state.release_verification_runs.get(gate_failure.source_id or "")
    if (
        verification_run is None
        or verification_run.release_package_id != predecessor.id
        or verification_run.candidate_commit_sha != predecessor.candidate_commit_sha
        or verification_run.state != "failed"
        or verification_run.failure_kind != "candidate"
        or verification_run.correction_task_id is None
    ):
        return (
            f"predecessor release {predecessor.id} has no linked candidate "
            "verification correction task"
        )
    correction_task = state.tasks.get(verification_run.correction_task_id)
    if correction_task is None:
        return (
            f"verification run {verification_run.id} references missing correction "
            f"task {verification_run.correction_task_id}"
        )
    try:
        require_task_resolved(
            state,
            correction_task.id,
            boundary=(f"successor release for verification run {verification_run.id}"),
        )
    except TaskAssessmentBoundaryError as exc:
        return exc.message
    if not correction_deliverable_ids:
        return "successor release requires an accepted verification correction deliverable"
    duplicate_ids = sorted(
        set(correction_deliverable_ids).intersection(predecessor.deliverable_ids)
    )
    if duplicate_ids:
        return "successor correction deliverables are already in predecessor scope: " + ", ".join(
            duplicate_ids
        )
    invalid_ids: list[str] = []
    for deliverable_id in correction_deliverable_ids:
        deliverable = state.deliverables.get(deliverable_id)
        if deliverable is None:
            invalid_ids.append(deliverable_id)
            continue
        if deliverable.task_id == correction_task.id:
            continue
        if (
            _verification_downstream_recovery_base(
                state,
                predecessor=predecessor,
                deliverable=deliverable,
            )
            is None
        ):
            invalid_ids.append(deliverable_id)
    if invalid_ids:
        return (
            f"verification correction task {correction_task.id} did not produce "
            "correction deliverables or lineage-verified downstream "
            "recovery deliverables: " + ", ".join(invalid_ids)
        )
    return None


def _release_review_correction_successor_request_error(
    state: TeamState,
    *,
    predecessor: ReleasePackage,
    correction_deliverable_ids: list[str],
) -> str | None:
    if (
        predecessor.candidate_commit_sha is None
        or predecessor.candidate_tree_hash is None
        or predecessor.manifest is None
        or predecessor.manifest.evidence_status != "complete"
    ):
        return f"predecessor release {predecessor.id} has no immutable candidate"
    gate_failure = predecessor.gate_failure
    assert gate_failure is not None
    review = state.review_requests.get(gate_failure.source_id or "")
    if (
        review is None
        or review.release_package_id != predecessor.id
        or review.candidate_commit_sha != predecessor.candidate_commit_sha
        or review.state != TeamRecordState.REJECTED
        or predecessor.review_correction_task_id is None
    ):
        return (
            f"predecessor release {predecessor.id} has no linked rejected "
            "release-review correction task"
        )
    correction_task = state.tasks.get(predecessor.review_correction_task_id)
    if correction_task is None:
        return (
            f"release {predecessor.id} references missing review correction task "
            f"{predecessor.review_correction_task_id}"
        )
    try:
        require_task_resolved(
            state,
            correction_task.id,
            boundary=(f"successor release for rejected release review {review.id}"),
        )
    except TaskAssessmentBoundaryError as exc:
        return exc.message
    if not correction_deliverable_ids:
        return "successor release requires an accepted review correction deliverable"
    duplicate_ids = sorted(
        set(correction_deliverable_ids).intersection(predecessor.deliverable_ids)
    )
    if duplicate_ids:
        return "successor correction deliverables are already in predecessor scope: " + ", ".join(
            duplicate_ids
        )
    invalid_ids = sorted(
        deliverable_id
        for deliverable_id in correction_deliverable_ids
        if (
            (deliverable := state.deliverables.get(deliverable_id)) is None
            or deliverable.task_id != correction_task.id
        )
    )
    if invalid_ids:
        return (
            f"release review correction task {correction_task.id} did not produce "
            "correction deliverables: " + ", ".join(invalid_ids)
        )
    return None


def _post_release_successor_request_error(
    state: TeamState,
    *,
    predecessor: ReleasePackage,
    correction_deliverable_ids: list[str],
    post_release_feedback_case_id: str | None,
) -> str | None:
    if (
        predecessor.published_at is None
        or predecessor.published_commit_sha is None
        or predecessor.candidate_commit_sha is None
        or predecessor.candidate_tree_hash is None
        or predecessor.manifest is None
        or predecessor.approved_commit_sha != predecessor.candidate_commit_sha
        or predecessor.published_commit_sha != predecessor.candidate_commit_sha
    ):
        return f"predecessor release {predecessor.id} is not fully published"
    if post_release_feedback_case_id is None:
        return "a published-release successor requires an explicit post_release_feedback_case_id"
    case = state.cases.get(post_release_feedback_case_id)
    if (
        case is None
        or case.source != "post_release_feedback"
        or case.project_id != predecessor.project_id
        or case.metadata.get("release_package_id") != predecessor.id
    ):
        return (
            f"post-release case {post_release_feedback_case_id} does not authorize "
            f"a successor of release {predecessor.id}"
        )
    if case.status.value in {"resolved", "closed"}:
        return (
            f"post-release case {case.id} is {case.status.value}; closed feedback "
            "cannot authorize a successor"
        )
    if not correction_deliverable_ids:
        return "post-release successor requires accepted correction deliverables"
    duplicate_ids = sorted(
        set(correction_deliverable_ids).intersection(predecessor.deliverable_ids)
    )
    if duplicate_ids:
        return (
            "post-release correction deliverables are already in predecessor scope: "
            + ", ".join(duplicate_ids)
        )
    for task_id in case.task_ids:
        try:
            require_task_resolved(
                state,
                task_id,
                boundary=f"post-release successor for case {case.id}",
            )
        except TaskAssessmentBoundaryError as exc:
            return exc.message
    for deliverable_id in correction_deliverable_ids:
        deliverable = state.deliverables.get(deliverable_id)
        task = state.tasks.get(deliverable.task_id) if deliverable is not None else None
        if (
            deliverable is None
            or deliverable.state != TeamRecordState.ACCEPTED
            or deliverable.superseded_by_deliverable_id is not None
            or task is None
            or task.case_id != case.id
        ):
            return (
                f"post-release correction deliverable {deliverable_id} is not a "
                f"current accepted contribution from case {case.id}"
            )
    return None


def _active_release_package_for_deliverables(
    state: TeamState,
    *,
    project_id: str,
    deliverable_ids: list[str],
) -> ReleasePackage | None:
    normalized = _normalized_release_deliverable_set(deliverable_ids)
    for release in sorted(
        state.release_packages.values(),
        key=lambda item: (item.created_at, item.id),
        reverse=True,
    ):
        if release.project_id != project_id:
            continue
        if release.state in _TERMINAL_RELEASE_STATES:
            continue
        if _normalized_release_deliverable_set(release.deliverable_ids) == normalized:
            return release
    return None


def _normalized_release_deliverable_set(deliverable_ids: list[str]) -> tuple[str, ...]:
    return tuple(sorted({item for item in deliverable_ids if item}))


def _same_deliverable_release_package_hint(release: ReleasePackage) -> str:
    parts = [
        f"Release package {release.id} already covers this deliverable set",
        f"state {release.state.value}",
    ]
    if release.release_branch:
        parts.append(f"release_branch {release.release_branch}")
    if release.canonical_branch:
        parts.append(f"canonical_branch {release.canonical_branch}")
    if release.error:
        parts.append(f"error {release.error}")
    parts.append(
        "do not create another release package for the same deliverables; inspect "
        "the existing package and follow its next_action_hint"
    )
    return "; ".join(parts)


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
        "only when the hint identifies a failed correction-aggregate, feature, or "
        "release promotion and the resolved commit is pushed to an agents/... ref, "
        "or choose a new id only for a "
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
    aggregate = _correction_aggregate_promotion(state, release)
    if (
        aggregate is not None
        and aggregate.state == "succeeded"
        and aggregate.commit_sha is not None
    ):
        return [aggregate.target_branch]
    feature_branches = list(release.feature_branches)
    for deliverable_id in _release_promotion_deliverable_ids(state, release):
        deliverable = state.deliverables.get(deliverable_id)
        if deliverable is None:
            continue
        feature_branch_value = deliverable.git_refs.feature_branch
        if feature_branch_value:
            feature_branches = _append_unique(feature_branches, feature_branch_value)
    return feature_branches


def _release_expected_feature_commits(
    state: TeamState,
    release: ReleasePackage,
) -> dict[str, str]:
    aggregate = _correction_aggregate_promotion(state, release)
    if (
        aggregate is not None
        and aggregate.state == "succeeded"
        and aggregate.commit_sha is not None
    ):
        return {aggregate.target_branch: aggregate.commit_sha}
    expected: dict[str, str] = {}
    for deliverable_id in _release_promotion_deliverable_ids(state, release):
        deliverable = state.deliverables.get(deliverable_id)
        if deliverable is None:
            continue
        branch = deliverable.git_refs.feature_branch
        commit_sha = deliverable.git_refs.feature_commit_sha
        if branch and commit_sha:
            expected[branch] = commit_sha
    for promotion_id in release.git_promotion_record_ids:
        promotion = state.git_promotions.get(promotion_id)
        if (
            promotion is not None
            and promotion.promotion_kind == "feature"
            and promotion.state == "succeeded"
            and promotion.commit_sha
        ):
            expected[promotion.target_branch] = promotion.commit_sha
    return expected


def _release_allowed_changed_paths(
    state: TeamState,
    release: ReleasePackage,
) -> tuple[list[str], list[str]]:
    allowed: list[str] = []
    missing_manifest_ids: list[str] = []
    for deliverable_id in _release_promotion_deliverable_ids(state, release):
        deliverable = state.deliverables.get(deliverable_id)
        if deliverable is None:
            continue
        has_git_source = bool(
            deliverable.git_refs.agent_branch
            or deliverable.git_refs.review_branch
            or deliverable.git_refs.feature_branch
        )
        if not has_git_source:
            continue
        if deliverable.review_manifest is None:
            missing_manifest_ids.append(deliverable_id)
            continue
        for path in deliverable.review_manifest.changed_paths:
            allowed = _append_unique(allowed, path)
    return allowed, missing_manifest_ids


def _release_final_allowed_changed_paths(
    state: TeamState,
    release: ReleasePackage,
) -> tuple[list[str], list[str]]:
    """Return the complete reviewed scope for a main-based final consolidation."""

    allowed: list[str] = []
    missing_manifest_ids: list[str] = []
    predecessor = state.release_packages.get(release.supersedes_release_package_id or "")
    predecessor_evidence = {
        row.deliverable_id: row
        for row in (
            predecessor.manifest.deliverables
            if predecessor is not None and predecessor.manifest is not None
            else []
        )
    }
    for deliverable_id in release.deliverable_ids:
        deliverable = state.deliverables.get(deliverable_id)
        if deliverable is None:
            continue
        manifest = deliverable.review_manifest
        if manifest is None:
            evidence = predecessor_evidence.get(deliverable_id)
            manifest = evidence.review_manifest if evidence is not None else None
        has_git_source = bool(
            deliverable.git_refs.agent_branch
            or deliverable.git_refs.review_branch
            or deliverable.git_refs.feature_branch
            or (
                predecessor_evidence.get(deliverable_id) is not None
                and predecessor_evidence[deliverable_id].feature_branch
            )
        )
        if not has_git_source:
            continue
        if manifest is None:
            missing_manifest_ids.append(deliverable_id)
            continue
        for path in manifest.changed_paths:
            allowed = _append_unique(allowed, path)
    return allowed, missing_manifest_ids


def _review_manifest_branch_aliases(branch: str | None) -> set[str]:
    value = _optional_text(branch)
    if value is None:
        return set()
    aliases = {value}
    prefixes = ("refs/heads/", "refs/remotes/", "origin/", "local-evander/")
    changed = True
    while changed:
        changed = False
        for alias in list(aliases):
            for prefix in prefixes:
                if alias.startswith(prefix):
                    stripped = alias[len(prefix) :]
                    if stripped and stripped not in aliases:
                        aliases.add(stripped)
                        changed = True
    return aliases


def _deliverable_review_source_aliases(deliverable: Deliverable) -> set[str]:
    refs = deliverable.git_refs
    aliases: set[str] = set()
    for branch in (
        refs.agent_branch,
        refs.review_branch,
        refs.feature_branch,
    ):
        aliases.update(_review_manifest_branch_aliases(branch))
    return aliases


def _repair_deliverable_review_manifest_from_promotion_evidence(
    state: TeamState,
    *,
    deliverable: Deliverable,
    result: dict[str, Any],
) -> bool:
    """Backfill a missing manifest only from pinned, review-matching Git evidence."""

    if deliverable.review_manifest is not None:
        return False
    reviewed_commit_sha, review_error = _reviewed_commit_for_release(
        state,
        deliverable,
    )
    if review_error is not None:
        return False
    task = state.tasks.get(deliverable.task_id)
    if task is None:
        return False
    source_aliases = _deliverable_review_source_aliases(deliverable)
    candidates: list[tuple[GitPromotionRecord, DeliverableReviewManifest]] = []
    for promotion in state.git_promotions.values():
        if (
            promotion.project_id != task.project_id
            or promotion.state == "retired"
            or promotion.promotion_kind not in {"feature", "correction_aggregate", "release"}
        ):
            continue
        if promotion.deliverable_id != deliverable.id and promotion.task_id != deliverable.task_id:
            promotion_source_branches = [
                promotion.source_branch,
                *(item.source_branch for item in promotion.sources),
            ]
            promotion_sources = {
                alias
                for source in promotion_source_branches
                for alias in _review_manifest_branch_aliases(source)
            }
            if not source_aliases.intersection(promotion_sources):
                continue
        raw_manifests = promotion.outputs.get("source_manifests")
        if not isinstance(raw_manifests, dict):
            continue
        for source_branch, raw_manifest in raw_manifests.items():
            if not isinstance(source_branch, str) or not isinstance(raw_manifest, dict):
                continue
            try:
                manifest = DeliverableReviewManifest.model_validate(raw_manifest)
            except ValueError:
                continue
            if manifest.reviewed_commit_sha != reviewed_commit_sha:
                continue
            manifest_aliases = _review_manifest_branch_aliases(source_branch)
            if source_aliases and not source_aliases.intersection(manifest_aliases):
                continue
            candidates.append((promotion, manifest))
    if not candidates:
        return False
    unique_manifests = {
        candidate.model_dump_json(exclude={"generated_at"}) for _promotion, candidate in candidates
    }
    if len(unique_manifests) != 1:
        result.setdefault("diagnostics", []).append(
            {
                "code": "ambiguous_review_manifest_evidence",
                "deliverable_id": deliverable.id,
                "promotion_ids": sorted({promotion.id for promotion, _ in candidates}),
                "message": (
                    "Multiple Git promotion results reported different manifests for "
                    f"accepted deliverable {deliverable.id}; the platform did not choose "
                    "between them."
                ),
            }
        )
        return False
    candidates.sort(key=lambda item: (item[0].updated_at, item[0].created_at, item[0].id))
    deliverable.review_manifest = candidates[-1][1]
    result["updated_deliverable_ids"] = _append_unique(
        result.setdefault("updated_deliverable_ids", []),
        deliverable.id,
    )
    result.setdefault("repaired_review_manifest_ids", []).append(deliverable.id)
    return True


def _repair_release_review_manifests_from_promotion_evidence(
    state: TeamState,
    *,
    release: ReleasePackage,
    result: dict[str, Any],
) -> list[str]:
    for deliverable_id in _release_promotion_deliverable_ids(state, release):
        deliverable = state.deliverables.get(deliverable_id)
        if deliverable is None or deliverable.review_manifest is not None:
            continue
        _repair_deliverable_review_manifest_from_promotion_evidence(
            state,
            deliverable=deliverable,
            result=result,
        )
    _allowed, missing_manifest_ids = _release_allowed_changed_paths(state, release)
    return missing_manifest_ids


def _release_promotion_deliverable_ids(
    state: TeamState,
    release: ReleasePackage,
) -> list[str]:
    predecessor_id = release.supersedes_release_package_id
    predecessor = state.release_packages.get(predecessor_id or "")
    if predecessor is None:
        return list(release.deliverable_ids)
    predecessor_ids = set(predecessor.deliverable_ids)
    return [
        deliverable_id
        for deliverable_id in release.deliverable_ids
        if deliverable_id not in predecessor_ids
    ]


def _release_feature_prerequisites_ready(
    state: TeamState,
    release: ReleasePackage,
) -> bool:
    aggregate = _correction_aggregate_promotion(state, release)
    if aggregate is not None:
        return aggregate.state == "succeeded" and aggregate.commit_sha is not None
    for promotion_id in release.git_promotion_record_ids:
        promotion = state.git_promotions.get(promotion_id)
        if promotion is None or promotion.promotion_kind != "feature":
            continue
        if promotion.state != "succeeded":
            return False
    return True


def _correction_aggregate_promotion(
    state: TeamState,
    release: ReleasePackage,
) -> GitPromotionRecord | None:
    candidates = [
        promotion
        for promotion_id in release.git_promotion_record_ids
        if (promotion := state.git_promotions.get(promotion_id)) is not None
        and promotion.promotion_kind == "correction_aggregate"
        and promotion.state != "retired"
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item.created_at, item.id))
    return candidates[-1]


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
        open_release_blocker(
            state,
            release_package_id=release.id,
            summary=f"Release promotion blocked: {release.id}",
            details=release.error,
            reporter_agent_id=actor_agent_id,
            result=result,
        )
        return
    allowed_changed_paths, missing_manifest_ids = _release_final_allowed_changed_paths(
        state,
        release,
    )
    if release.policy_version >= 2 and missing_manifest_ids:
        release.state = TeamRecordState.BLOCKED
        release.error = "release scope validation is missing reviewed manifests for: " + ", ".join(
            sorted(missing_manifest_ids)
        )
        if release.scope_validation is not None:
            release.scope_validation.state = "blocked"
            release.scope_validation.validated_at = utc_now()
        result["error"] = release.error
        open_release_blocker(
            state,
            release_package_id=release.id,
            summary=f"Release promotion blocked: {release.id}",
            details=release.error,
            reporter_agent_id=actor_agent_id,
            result=result,
        )
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
        sources=[
            GitPromotionSource(
                source_branch=branch,
                expected_commit_sha=expected_commit,
            )
            for branch, expected_commit in _release_expected_feature_commits(
                state,
                release,
            ).items()
        ],
        target_branch=target_release_branch,
        promotion_kind="release",
        git_base_branch=release.git_base_branch,
        base_commit_sha=release.base_commit_sha,
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
        "managed_repo_id": git.managed_repo_id,
        "base_branch": release.git_base_branch,
        "base_commit_sha": release.base_commit_sha,
        "source_branches": list(feature_branches),
        "expected_source_commits": _release_expected_feature_commits(state, release),
        "allowed_changed_paths": allowed_changed_paths,
        "target_branch": target_release_branch,
        "commit_message": f"Assemble release {release.id}",
    }
    payload.update(_managed_repo_fields(git))
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
    "ReleasePackageValidationError",
    "GIT_RELEASE_RECOVERY_EVENT_TYPE",
    "_emit_git_release_recovery_event",
    "_apply_git_release_request",
    "_release_request_permitted",
    "_failed_release_promotion_for_recovery",
    "_apply_git_release_recovery",
    "_build_release_package",
    "_correction_aggregate_promotion",
    "_create_correction_aggregate_promotion",
    "_duplicate_release_package_error",
    "_feature_promotion_source_branch_for_release",
    "_release_feature_branches_from_deliverables",
    "_release_expected_feature_commits",
    "_release_final_allowed_changed_paths",
    "_release_feature_prerequisites_ready",
    "_queue_release_promotion_if_ready",
    "_release_failure_recipient",
]
