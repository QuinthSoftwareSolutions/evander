from __future__ import annotations

from hashlib import sha256
from typing import Any
from uuid import uuid4
from .task_authorship import combined_deliverable_attributions, common_deliverable_author
from ...git_naming import (
    canonical_release_branch,
    feature_branch,
    review_branch,
    validate_branch_name,
)
from ...models import (
    AgentStatus,
    AgentTask,
    Deliverable,
    DeliverableGitRefs,
    DeliverableReviewManifest,
    EphemeralAssignmentState,
    GitPromotionRecord,
    PersistentAgent,
    ReleasePackage,
    TeamBlocker,
    TeamBlockerSourceKind,
    TeamInboxItemState,
    TeamRecordState,
    TeamState,
    TeamTaskState,
    agent_has_project_assignment,
    project_lead_agent_id,
)

from .agent_access import (
    _is_project_lead_for,
    _is_team_manager,
    _supervises,
)
from .common import (
    _append_unique,
    _optional_text,
    _required_text,
    _string_list,
    utc_now,
)
from .blockers import (
    assign_blocker_owner,
    open_deliverable_blocker,
    open_or_get_blocker,
    open_release_blocker,
    resolve_active_blockers_for_source,
)
from .git_common import (
    GIT_PROMOTION_EVENT_TYPE,
    _emit_git_promotion_event,
    _managed_repo_fields,
    _normalize_agent_branch_ref,
    _project_git_or_error,
)
from .git_work_contexts import authoritative_deliverable_git_base
from .release_packages import (
    _queue_release_promotion_if_ready,
    _repair_release_review_manifests_from_promotion_evidence,
)
from .stand_down import _active_stand_downs_for_agent
from .work_lineage import ensure_release_work_lineage, ensure_task_work_lineage


def _apply_git_review_promotion(
    state: TeamState,
    *,
    actor: PersistentAgent,
    request: dict[str, Any],
    result: dict[str, Any],
) -> str | None:
    assignment_id = _optional_text(request.get("assignment_id"))
    if assignment_id is None:
        return _apply_persistent_git_review_promotion(
            state,
            actor=actor,
            request=request,
            result=result,
        )
    return _apply_ephemeral_git_review_promotion(
        state,
        actor=actor,
        request=request,
        result=result,
        assignment_id=assignment_id,
    )


def _apply_ephemeral_git_review_promotion(
    state: TeamState,
    *,
    actor: PersistentAgent,
    request: dict[str, Any],
    result: dict[str, Any],
    assignment_id: str,
) -> str | None:
    assignment = state.ephemeral_assignments.get(assignment_id)
    if assignment is None:
        result["errors"].append(
            f"git review promotion references missing ephemeral assignment {assignment_id}"
        )
        return None
    if assignment.parent_agent_id != actor.id:
        result["errors"].append(
            f"agent {actor.id} cannot promote ephemeral assignment {assignment_id}"
        )
        return None
    if assignment.state not in {
        EphemeralAssignmentState.COMPLETED,
        EphemeralAssignmentState.ACCEPTED,
    }:
        result["errors"].append(
            f"ephemeral assignment {assignment_id} is not completed or accepted"
        )
        return None
    if not assignment.agent_branch:
        result["errors"].append(
            f"ephemeral assignment {assignment_id} has no agent_branch to promote"
        )
        return None
    deliverable_id = _optional_text(request.get("deliverable_id"))
    if deliverable_id is not None:
        deliverable = state.deliverables.get(deliverable_id)
        if deliverable is None:
            result["errors"].append(
                f"git review promotion references missing deliverable {deliverable_id}"
            )
            return None
        deliverable_task = state.tasks.get(deliverable.task_id)
        if (
            deliverable_task is None
            or deliverable_task.id != assignment.task_id
            or deliverable_task.project_id != assignment.project_id
        ):
            result["errors"].append(
                f"deliverable {deliverable_id} does not belong to ephemeral assignment {assignment_id}"
            )
            return None
    project = state.projects.get(assignment.project_id)
    if project is None:
        result["errors"].append(
            f"git review promotion references missing project {assignment.project_id}"
        )
        return None
    git = _project_git_or_error(project, result)
    if git is None:
        return None
    try:
        source_branch = validate_branch_name(assignment.agent_branch)
    except ValueError as exc:
        result["errors"].append(
            f"ephemeral assignment {assignment_id} has invalid agent_branch: {exc}"
        )
        return None
    expected_commit_sha = _optional_text(assignment.commit_sha)
    if expected_commit_sha is None:
        result["errors"].append(
            f"ephemeral assignment {assignment_id} requires commit_sha for promotion"
        )
        return None
    target_branch = review_branch(actor.team_id, assignment_id)
    task = state.tasks.get(assignment.task_id)
    git_base_branch = task.git_base_branch if task is not None else None
    task_contexts = {
        context.base_commit
        for context in state.git_work_contexts.values()
        if context.task_id == assignment.task_id
        and context.base_branch == git_base_branch
        and context.base_commit is not None
    }
    base_commit_sha = next(iter(task_contexts)) if len(task_contexts) == 1 else None
    promotion_id = f"promotion-{uuid4().hex}"
    state.git_promotions[promotion_id] = GitPromotionRecord(
        id=promotion_id,
        project_id=project.id,
        task_id=assignment.task_id,
        deliverable_id=deliverable_id,
        actor_agent_id=actor.id,
        source_branch=source_branch,
        target_branch=target_branch,
        promotion_kind="review",
        git_base_branch=git_base_branch,
        base_commit_sha=base_commit_sha,
        commit_sha=expected_commit_sha,
    )
    payload: dict[str, Any] = {
        "promotion_id": promotion_id,
        "promotion_kind": "review",
        "project_id": project.id,
        **_managed_repo_fields(git),
        "source_branch": source_branch,
        "expected_commit_sha": expected_commit_sha,
        "target_branch": target_branch,
        "assignment_id": assignment_id,
        "base_branch": git_base_branch,
        "base_commit_sha": base_commit_sha,
    }
    if deliverable_id is not None:
        payload["deliverable_id"] = deliverable_id
    _emit_git_promotion_event(result, promotion_id=promotion_id, payload=payload)
    return promotion_id


def _apply_persistent_git_review_promotion(
    state: TeamState,
    *,
    actor: PersistentAgent,
    request: dict[str, Any],
    result: dict[str, Any],
) -> str | None:
    deliverable_id = _required_text(request.get("deliverable_id"), "deliverable_id")
    deliverable = state.deliverables.get(deliverable_id)
    if deliverable is None:
        result["errors"].append(
            f"git review promotion references missing deliverable {deliverable_id}"
        )
        return None
    task = state.tasks.get(deliverable.task_id)
    if task is None:
        result["errors"].append(
            f"git review promotion references missing task {deliverable.task_id}"
        )
        return None
    project = state.projects.get(task.project_id)
    if project is None:
        result["errors"].append(
            f"git review promotion references missing project {task.project_id}"
        )
        return None
    permitted = (
        actor.id == deliverable.producer_agent_id
        or _supervises(state, actor.id, deliverable.producer_agent_id)
        or _is_project_lead_for(state, actor.id, project.id)
        or (_is_team_manager(state, actor) and project.team_id == actor.team_id)
    )
    if not permitted:
        result["errors"].append(
            f"agent {actor.id} is not permitted to promote deliverable {deliverable_id}"
        )
        return None
    git = _project_git_or_error(project, result)
    if git is None:
        return None

    commit_sha = _optional_text(request.get("commit_sha")) or deliverable.git_refs.commit_sha
    source_branch_value = (
        _optional_text(request.get("source_branch")) or deliverable.git_refs.agent_branch
    )
    if source_branch_value is not None:
        try:
            source_branch = validate_branch_name(source_branch_value)
        except ValueError as exc:
            result["errors"].append(f"git review promotion has invalid source_branch: {exc}")
            return None
    else:
        result["errors"].append(
            f"git review promotion for deliverable {deliverable_id} requires source_branch"
        )
        return None
    if commit_sha is None:
        result["errors"].append(
            f"git review promotion for deliverable {deliverable_id} requires commit_sha"
        )
        return None

    try:
        git_base_branch, base_commit_sha = authoritative_deliverable_git_base(
            state,
            deliverable=deliverable,
        )
    except ValueError as exc:
        result["errors"].append(str(exc))
        return None

    target_branch = review_branch(actor.team_id, deliverable_id)
    promotion_id = f"promotion-{uuid4().hex}"
    state.git_promotions[promotion_id] = GitPromotionRecord(
        id=promotion_id,
        project_id=project.id,
        task_id=task.id,
        deliverable_id=deliverable_id,
        actor_agent_id=actor.id,
        source_branch=source_branch,
        target_branch=target_branch,
        promotion_kind="review",
        git_base_branch=git_base_branch,
        base_commit_sha=base_commit_sha,
        commit_sha=commit_sha,
    )
    payload: dict[str, Any] = {
        "promotion_id": promotion_id,
        "promotion_kind": "review",
        "project_id": project.id,
        **_managed_repo_fields(git),
        "source_branch": source_branch,
        "expected_commit_sha": commit_sha,
        "target_branch": target_branch,
        "deliverable_id": deliverable_id,
        "base_branch": git_base_branch,
        "base_commit_sha": base_commit_sha,
    }
    if source_branch_value is not None:
        payload["source_branch"] = source_branch_value
    if commit_sha is not None:
        payload["commit_sha"] = commit_sha
    _emit_git_promotion_event(result, promotion_id=promotion_id, payload=payload)
    return promotion_id


def _commit_source_branch(commit_sha: str) -> str:
    slug = "".join(ch if ch.isalnum() else "-" for ch in commit_sha).strip("-")
    if not slug:
        slug = "unknown"
    return validate_branch_name(f"commit/{slug[:64]}")


def _apply_git_feature_update(
    state: TeamState,
    *,
    actor: PersistentAgent,
    update: dict[str, Any],
    result: dict[str, Any],
) -> str | None:
    task_id = _required_text(update.get("task_id"), "task_id")
    task = state.tasks.get(task_id)
    if task is None:
        result["errors"].append(f"git feature update references missing task {task_id}")
        return None
    if task.owner_agent_id != actor.id and not _supervises(state, actor.id, task.owner_agent_id):
        result["errors"].append(f"agent {actor.id} is not permitted to consolidate task {task_id}")
        return None
    project = state.projects.get(task.project_id)
    if project is None:
        result["errors"].append(f"git feature update references missing project {task.project_id}")
        return None
    git = _project_git_or_error(project, result)
    if git is None:
        return None
    raw_sources = _string_list(update.get("source_review_branches"), "source_review_branches")
    if not raw_sources:
        result["errors"].append(
            f"git feature update for task {task_id} requires source_review_branches"
        )
        return None
    try:
        source_branches = [validate_branch_name(branch) for branch in raw_sources]
        target_branch = validate_branch_name(
            _optional_text(update.get("feature_branch"))
            or feature_branch(actor.team_id, task_id, actor.id)
        )
    except ValueError as exc:
        result["errors"].append(f"git feature update has invalid branch name: {exc}")
        return None
    expected_source_commits: dict[str, str] = {}
    source_base_snapshots: set[tuple[str | None, str | None]] = set()
    from .release_packages import _reviewed_commit_for_release

    for source_branch in source_branches:
        source_aliases = _branch_aliases(source_branch)
        matching = [
            deliverable
            for deliverable_id in task.deliverable_ids
            if (deliverable := state.deliverables.get(deliverable_id)) is not None
            and deliverable.state == TeamRecordState.ACCEPTED
            and deliverable.superseded_by_deliverable_id is None
            and source_aliases & _deliverable_git_branch_aliases(deliverable)
        ]
        if len(matching) != 1:
            result["errors"].append(
                f"git feature update source {source_branch} must match exactly one "
                "accepted, current deliverable"
            )
            return None
        reviewed_commit_sha, review_error = _reviewed_commit_for_release(
            state,
            matching[0],
        )
        if review_error is not None:
            result["errors"].append(review_error)
            return None
        expected_source_commits[source_branch] = reviewed_commit_sha
        try:
            source_base_snapshots.add(
                authoritative_deliverable_git_base(
                    state,
                    deliverable=matching[0],
                )
            )
        except ValueError as exc:
            result["errors"].append(str(exc))
            return None
    if len(source_base_snapshots) != 1:
        result["errors"].append(
            f"git feature update for task {task_id} requires one authoritative "
            "Git base branch and commit"
        )
        return None
    git_base_branch, base_commit_sha = next(iter(source_base_snapshots))
    if git_base_branch is None or base_commit_sha is None:
        result["errors"].append(
            f"git feature update for task {task_id} has no pinned task base commit"
        )
        return None
    promotion_id = f"promotion-{uuid4().hex}"
    state.git_promotions[promotion_id] = GitPromotionRecord(
        id=promotion_id,
        project_id=project.id,
        task_id=task_id,
        actor_agent_id=actor.id,
        source_branch=source_branches[0],
        target_branch=target_branch,
        promotion_kind="feature",
        git_base_branch=git_base_branch,
        base_commit_sha=base_commit_sha,
    )
    payload: dict[str, Any] = {
        "promotion_id": promotion_id,
        "promotion_kind": "feature",
        "project_id": project.id,
        **_managed_repo_fields(git),
        "base_branch": git_base_branch,
        "base_commit_sha": base_commit_sha,
        "source_branches": source_branches,
        "expected_source_commits": expected_source_commits,
        "target_branch": target_branch,
        "task_id": task_id,
        "commit_message": f"Integrate accepted work for task {task_id}",
    }
    _emit_git_promotion_event(result, promotion_id=promotion_id, payload=payload)
    return promotion_id


def _branch_aliases(branch: str | None) -> set[str]:
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


def _promotion_source_branch_aliases(promotion: GitPromotionRecord) -> set[str]:
    aliases = _branch_aliases(promotion.source_branch)
    for key in ("source_branches", "merged_branches"):
        value = promotion.outputs.get(key)
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, str):
                aliases.update(_branch_aliases(item))
    return aliases


def _deliverable_git_branch_aliases(deliverable: Deliverable) -> set[str]:
    refs = deliverable.git_refs
    aliases: set[str] = set()
    aliases.update(_branch_aliases(refs.agent_branch))
    aliases.update(_branch_aliases(refs.review_branch))
    aliases.update(_branch_aliases(refs.feature_branch))
    aliases.update(_branch_aliases(refs.release_branch))
    return aliases


def _deliverable_has_git_source_refs(deliverable: Deliverable) -> bool:
    refs = deliverable.git_refs
    return any(
        (
            refs.agent_branch,
            refs.review_branch,
            refs.feature_branch,
            refs.release_branch,
            refs.commit_sha,
            refs.promotion_record_ids,
        )
    )


def _accepted_source_deliverables_for_feature_promotion(
    state: TeamState,
    promotion: GitPromotionRecord,
    *,
    exclude_deliverable_ids: set[str] | None = None,
) -> list[Deliverable]:
    if promotion.task_id is None:
        return []
    task = state.tasks.get(promotion.task_id)
    if task is None:
        return []
    excluded = set(exclude_deliverable_ids or set())
    candidates = [
        deliverable
        for deliverable in (
            state.deliverables[item_id]
            for item_id in task.deliverable_ids
            if item_id in state.deliverables
        )
        if deliverable.id not in excluded
        and deliverable.state == TeamRecordState.ACCEPTED
        and deliverable.superseded_by_deliverable_id is None
        and _deliverable_has_git_source_refs(deliverable)
    ]
    source_aliases = _promotion_source_branch_aliases(promotion)
    matching = [
        deliverable
        for deliverable in candidates
        if source_aliases and source_aliases & _deliverable_git_branch_aliases(deliverable)
    ]
    if matching:
        return matching
    return []


def _attach_feature_promotion_to_accepted_deliverables(
    state: TeamState,
    *,
    promotion: GitPromotionRecord,
    commit_sha: str | None,
    result: dict[str, Any],
    deliverables: list[Deliverable] | None = None,
) -> bool:
    matching_deliverables = deliverables or _accepted_source_deliverables_for_feature_promotion(
        state,
        promotion,
    )
    if not matching_deliverables:
        return False
    now = utc_now()
    for deliverable in matching_deliverables:
        refs = deliverable.git_refs
        raw_manifests = promotion.outputs.get("source_manifests")
        source_manifests = raw_manifests if isinstance(raw_manifests, dict) else {}
        source_branch = refs.review_branch or refs.agent_branch
        raw_manifest = source_manifests.get(source_branch) if source_branch else None
        if isinstance(raw_manifest, dict):
            deliverable.review_manifest = DeliverableReviewManifest.model_validate(raw_manifest)
        refs.feature_branch = promotion.target_branch
        if commit_sha is not None:
            refs.feature_commit_sha = commit_sha
        refs.promotion_record_ids = _append_unique(refs.promotion_record_ids, promotion.id)
        task = state.tasks.get(deliverable.task_id)
        if task is not None:
            task.updated_at = now
        result["updated_deliverable_ids"] = _append_unique(
            result.setdefault("updated_deliverable_ids", []),
            deliverable.id,
        )
    promotion.deliverable_id = matching_deliverables[0].id
    return True


def _is_safe_synthetic_feature_deliverable(
    state: TeamState,
    *,
    deliverable: Deliverable,
    promotion: GitPromotionRecord,
) -> bool:
    refs = deliverable.git_refs
    extra_promotion_ids = [
        promotion_id for promotion_id in refs.promotion_record_ids if promotion_id != promotion.id
    ]
    return (
        deliverable.state == TeamRecordState.PENDING
        and deliverable.summary.startswith("Consolidated feature branch ")
        and not deliverable.artifact_refs
        and not deliverable.review_request_ids
        and deliverable.supersedes_deliverable_id is None
        and deliverable.superseded_by_deliverable_id is None
        and refs.agent_branch is None
        and refs.review_branch is None
        and refs.feature_branch == promotion.target_branch
        and refs.release_branch is None
        and not extra_promotion_ids
        and all(
            deliverable.id not in release.deliverable_ids
            for release in state.release_packages.values()
        )
    )


def _attach_promotion_success(
    state: TeamState,
    *,
    promotion: GitPromotionRecord,
    commit_sha: str | None,
    result: dict[str, Any],
) -> None:
    """Attach a succeeded promotion's branch + record id to its linked records."""

    kind = promotion.promotion_kind
    if kind == "correction_aggregate":
        validation_error = _attach_correction_aggregate_manifests(
            state,
            promotion=promotion,
            result=result,
        )
        if validation_error is not None:
            promotion.state = "failed"
            promotion.error = validation_error
            promotion.outputs["error_code"] = "scope_mismatch"
            result["error"] = validation_error
            _attach_promotion_failure(
                state,
                promotion=promotion,
                result=result,
            )
            return
    deliverable = (
        state.deliverables.get(promotion.deliverable_id)
        if promotion.deliverable_id is not None
        else None
    )
    if deliverable is not None and deliverable.state == TeamRecordState.BLOCKED:
        resolved_deliverable_blocker_ids = resolve_active_blockers_for_source(
            state,
            source_kind=TeamBlockerSourceKind.DELIVERABLE,
            source_id=deliverable.id,
            actor_agent_id=promotion.actor_agent_id,
            dispatch_id=None,
            note=f"Git promotion {promotion.id} succeeded.",
            reference_ids=[promotion.id],
            result=result,
        )
        if resolved_deliverable_blocker_ids:
            deliverable.state = (
                TeamRecordState.PENDING if kind == "review" else TeamRecordState.ACCEPTED
            )
    resolve_active_blockers_for_source(
        state,
        source_kind=TeamBlockerSourceKind.EXPLICIT,
        source_id=promotion.id,
        actor_agent_id=promotion.actor_agent_id,
        dispatch_id=None,
        note=f"Git promotion {promotion.id} succeeded.",
        reference_ids=[promotion.id],
        result=result,
    )
    if deliverable is not None:
        refs = deliverable.git_refs
        if kind == "review":
            refs.review_branch = promotion.target_branch
            if not promotion.source_branch.startswith("commit/"):
                refs.agent_branch = refs.agent_branch or promotion.source_branch
            if commit_sha is not None:
                refs.reviewed_commit_sha = refs.reviewed_commit_sha or commit_sha
        elif kind == "feature":
            raw_manifests = promotion.outputs.get("source_manifests")
            source_manifests = raw_manifests if isinstance(raw_manifests, dict) else {}
            source_branch = refs.review_branch or refs.agent_branch
            raw_manifest = source_manifests.get(source_branch) if source_branch else None
            if isinstance(raw_manifest, dict):
                deliverable.review_manifest = DeliverableReviewManifest.model_validate(raw_manifest)
            refs.feature_branch = promotion.target_branch
            if commit_sha is not None:
                refs.feature_commit_sha = commit_sha
        elif kind == "release":
            refs.release_branch = promotion.target_branch
            if commit_sha is not None:
                refs.release_commit_sha = commit_sha
        refs.promotion_record_ids = _append_unique(refs.promotion_record_ids, promotion.id)
    elif (
        kind == "feature"
        and promotion.task_id is not None
        and promotion.task_id in state.tasks
        and not _attach_feature_promotion_to_accepted_deliverables(
            state,
            promotion=promotion,
            commit_sha=commit_sha,
            result=result,
        )
    ):
        # A feature consolidation has no deliverable yet; capture the consolidated
        # branch on a new deliverable so later release requests can find it.
        task = state.tasks[promotion.task_id]
        if task.integration_boundary:
            result.setdefault("diagnostics", []).append(
                {
                    "code": "integration_deliverable_submission_required",
                    "task_id": task.id,
                    "promotion_id": promotion.id,
                    "message": (
                        "Feature promotion succeeded, but an integration-boundary "
                        "deliverable must be submitted explicitly so its full "
                        "deliverable contract can be validated."
                    ),
                    "recommended_tool": "team_submit_deliverable_for_review",
                }
            )
        else:
            deliverable_id = f"deliverable-{uuid4().hex}"
            new_deliverable = Deliverable(
                id=deliverable_id,
                task_id=task.id,
                producer_agent_id=promotion.actor_agent_id,
                summary=f"Consolidated feature branch {promotion.target_branch}",
                state=TeamRecordState.PENDING,
                git_refs=DeliverableGitRefs(
                    feature_branch=promotion.target_branch,
                    commit_sha=commit_sha,
                    promotion_record_ids=[promotion.id],
                ),
            )
            state.deliverables[deliverable_id] = new_deliverable
            task.deliverable_ids = _append_unique(
                task.deliverable_ids,
                deliverable_id,
            )
            task.updated_at = utc_now()
            promotion.deliverable_id = deliverable_id
            result["created_deliverable_ids"].append(deliverable_id)

    release = (
        state.release_packages.get(promotion.release_package_id)
        if promotion.release_package_id is not None
        else None
    )
    if release is not None:
        if release.state == TeamRecordState.REJECTED:
            return
        _clear_recovered_release_block(
            state,
            release=release,
            promotion=promotion,
            result=result,
        )
        release.git_promotion_record_ids = _append_unique(
            release.git_promotion_record_ids,
            promotion.id,
        )
        if release.state == TeamRecordState.BLOCKED:
            return
        if kind == "release":
            release.release_branch = release.release_branch or promotion.target_branch
            if release.policy_version >= 2 and release.scope_validation is not None:
                release.scope_validation.state = "passed"
                release.scope_validation.release_commit_sha = commit_sha
                release.scope_validation.validated_at = utc_now()
            if commit_sha is None:
                release.state = TeamRecordState.BLOCKED
                release.error = "release consolidation did not return a candidate commit SHA"
                result["error"] = release.error
                open_release_blocker(
                    state,
                    release_package_id=release.id,
                    summary=f"Release promotion blocked: {release.id}",
                    details=release.error,
                    reporter_agent_id=promotion.actor_agent_id,
                    result=result,
                )
                return
            raw_base_commit = promotion.outputs.get("base_commit_sha")
            if isinstance(raw_base_commit, str) and raw_base_commit.strip():
                release.base_commit_sha = raw_base_commit.strip()
            raw_tree_hash = promotion.outputs.get("tree_hash")
            candidate_tree_hash = (
                raw_tree_hash.strip()
                if isinstance(raw_tree_hash, str) and raw_tree_hash.strip()
                else None
            )
            from .release_gates import begin_release_gates

            begin_release_gates(
                state,
                release=release,
                release_promotion=promotion,
                candidate_commit_sha=commit_sha,
                candidate_tree_hash=candidate_tree_hash,
                result=result,
            )
        elif kind in {"feature", "correction_aggregate"}:
            if kind == "correction_aggregate":
                release.feature_branches = [promotion.target_branch]
            _queue_release_promotion_if_ready(
                state,
                release=release,
                actor_agent_id=promotion.actor_agent_id,
                result=result,
            )
        elif kind == "canonical_push":
            release.canonical_branch = release.canonical_branch or promotion.target_branch


def _attach_correction_aggregate_manifests(
    state: TeamState,
    *,
    promotion: GitPromotionRecord,
    result: dict[str, Any],
) -> str | None:
    """Validate and persist correction deltas derived from the rejected candidate."""

    release = state.release_packages.get(promotion.release_package_id or "")
    if release is None:
        return f"correction aggregate {promotion.id} has no release package"
    predecessor = state.release_packages.get(release.supersedes_release_package_id or "")
    rejected_candidate = predecessor.candidate_commit_sha if predecessor is not None else None
    if rejected_candidate is None:
        return f"correction aggregate {promotion.id} has no immutable rejected candidate base"
    raw_manifests = promotion.outputs.get("source_manifests")
    if not isinstance(raw_manifests, dict):
        return f"correction aggregate {promotion.id} returned no source manifests"
    expected_sources = {
        source.source_branch: source.expected_commit_sha for source in promotion.sources
    }
    if set(raw_manifests) != set(expected_sources):
        return _validate_recovered_correction_aggregate_manifest(
            state,
            release=release,
            predecessor_commit_sha=rejected_candidate,
            promotion=promotion,
            raw_manifests=raw_manifests,
        )
    deliverable_ids = [
        str(item)
        for item in promotion.outputs.get("correction_deliverable_ids", [])
        if str(item).strip()
    ]
    if len(deliverable_ids) != len(promotion.sources):
        return f"correction aggregate {promotion.id} has inconsistent deliverable source provenance"
    for deliverable_id, source in zip(
        deliverable_ids,
        promotion.sources,
        strict=True,
    ):
        raw_manifest = raw_manifests.get(source.source_branch)
        if not isinstance(raw_manifest, dict):
            return f"correction aggregate {promotion.id} has no manifest for {source.source_branch}"
        try:
            manifest = DeliverableReviewManifest.model_validate(raw_manifest)
        except ValueError as exc:
            return (
                f"correction aggregate {promotion.id} returned an invalid manifest "
                f"for {source.source_branch}: {exc}"
            )
        if (
            manifest.base_commit_sha != rejected_candidate
            or manifest.reviewed_commit_sha != source.expected_commit_sha
        ):
            return (
                f"correction aggregate {promotion.id} manifest for "
                f"{source.source_branch} is not the accepted delta from rejected "
                f"candidate {rejected_candidate}"
            )
        deliverable = state.deliverables.get(deliverable_id)
        if deliverable is None:
            return (
                f"correction aggregate {promotion.id} references missing "
                f"deliverable {deliverable_id}"
            )
        reviewed_commits = {
            state.review_requests[review_id].reviewed_commit_sha
            for review_id in deliverable.review_request_ids
            if review_id in state.review_requests
            and state.review_requests[review_id].state == TeamRecordState.ACCEPTED
            and state.review_requests[review_id].retired_at is None
            and state.review_requests[review_id].superseded_by_review_request_id is None
        }
        if reviewed_commits != {source.expected_commit_sha}:
            return (
                f"correction aggregate {promotion.id} source {source.source_branch} "
                "does not match one accepted immutable review"
            )
        deliverable.review_manifest = manifest
        result["updated_deliverable_ids"] = _append_unique(
            result.setdefault("updated_deliverable_ids", []),
            deliverable.id,
        )
    return None


def _validate_recovered_correction_aggregate_manifest(
    state: TeamState,
    *,
    release: ReleasePackage,
    predecessor_commit_sha: str,
    promotion: GitPromotionRecord,
    raw_manifests: dict[str, Any],
) -> str | None:
    """Validate the one resolved aggregate submitted after a merge conflict."""

    if len(raw_manifests) != 1:
        return f"correction aggregate {promotion.id} returned manifests for a different source set"
    raw_manifest = next(iter(raw_manifests.values()))
    if not isinstance(raw_manifest, dict):
        return f"correction aggregate {promotion.id} returned an invalid recovery manifest"
    try:
        manifest = DeliverableReviewManifest.model_validate(raw_manifest)
    except ValueError as exc:
        return f"correction aggregate {promotion.id} returned an invalid recovery manifest: {exc}"
    if manifest.base_commit_sha != predecessor_commit_sha:
        return (
            f"correction aggregate {promotion.id} recovery is not rooted at "
            f"rejected candidate {predecessor_commit_sha}"
        )
    allowed_paths: set[str] = set()
    predecessor = state.release_packages.get(release.supersedes_release_package_id or "")
    predecessor_ids = set(predecessor.deliverable_ids if predecessor is not None else [])
    correction_deliverables = [
        state.deliverables[deliverable_id]
        for deliverable_id in release.deliverable_ids
        if deliverable_id not in predecessor_ids and deliverable_id in state.deliverables
    ]
    for deliverable in correction_deliverables:
        reviewed_manifest = deliverable.review_manifest
        if reviewed_manifest is None or reviewed_manifest.base_commit_sha != predecessor_commit_sha:
            return (
                f"correction aggregate {promotion.id} recovery lacks trusted "
                f"rejected-candidate scope for deliverable {deliverable.id}"
            )
        allowed_paths.update(_manifest_path_names(reviewed_manifest.changed_paths))
    recovery_paths = _manifest_path_names(manifest.changed_paths)
    unexpected = sorted(recovery_paths - allowed_paths)
    if unexpected:
        return (
            f"correction aggregate {promotion.id} recovery changes paths outside "
            "the reviewed correction manifests: " + ", ".join(unexpected)
        )
    return None


def _manifest_path_names(rows: list[str]) -> set[str]:
    paths: set[str] = set()
    for row in rows:
        fields = row.split("\t")
        for value in fields[1:] if len(fields) > 1 else fields:
            if value:
                paths.add(value)
    return paths


def _clear_recovered_release_block(
    state: TeamState,
    *,
    release: ReleasePackage,
    promotion: GitPromotionRecord,
    result: dict[str, Any],
) -> None:
    if release.state != TeamRecordState.BLOCKED:
        return
    related_promotion_ids = set(release.git_promotion_record_ids)
    remaining_failed_promotions = [
        candidate
        for candidate in state.git_promotions.values()
        if candidate.id != promotion.id
        and candidate.state == "failed"
        and (candidate.id in related_promotion_ids or candidate.release_package_id == release.id)
    ]
    if remaining_failed_promotions:
        remaining_failed_promotions.sort(
            key=lambda candidate: (
                candidate.updated_at,
                candidate.created_at,
                candidate.id,
            ),
            reverse=True,
        )
        release.error = remaining_failed_promotions[0].error
        release.updated_at = utc_now()
        return
    release.state = TeamRecordState.PENDING
    release.error = None
    release.updated_at = utc_now()
    now = utc_now()
    active_recovery_task_ids = {
        task_id
        for blocker in state.blockers.values()
        if blocker.source_kind == TeamBlockerSourceKind.RELEASE_PACKAGE
        and blocker.source_id == release.id
        and blocker.state.value == "open"
        for task_id in blocker.affected_task_ids
    }
    for task_id in active_recovery_task_ids:
        task = state.tasks.get(task_id)
        if (
            task is not None
            and task.created_by_actor == "system:git-merge-recovery"
            and task.state
            not in {
                TeamTaskState.FINISHED,
                TeamTaskState.ACCEPTED,
                TeamTaskState.REJECTED,
                TeamTaskState.CANCELLED,
            }
        ):
            task.state = TeamTaskState.FINISHED
            task.state_entered_at = now
            task.updated_at = now
            result.setdefault("finished_merge_recovery_task_ids", []).append(task.id)
    resolve_active_blockers_for_source(
        state,
        source_kind=TeamBlockerSourceKind.RELEASE_PACKAGE,
        source_id=release.id,
        actor_agent_id=promotion.actor_agent_id,
        dispatch_id=None,
        note=f"Git promotion {promotion.id} recovered the release.",
        reference_ids=[promotion.id],
        result=result,
        now=now,
    )


def _queue_canonical_push_after_release_success(
    state: TeamState,
    *,
    release: ReleasePackage,
    release_promotion: GitPromotionRecord,
    result: dict[str, Any],
) -> None:
    approved_commit_sha = release.approved_commit_sha
    if approved_commit_sha is None or approved_commit_sha != release.candidate_commit_sha:
        release.state = TeamRecordState.BLOCKED
        release.error = "release candidate has not passed all configured approval gates"
        result["error"] = release.error
        open_release_blocker(
            state,
            release_package_id=release.id,
            summary=f"Canonical release push blocked: {release.id}",
            details=release.error,
            reporter_agent_id=release_promotion.actor_agent_id,
            result=result,
        )
        return
    if release_promotion.commit_sha != approved_commit_sha:
        release.state = TeamRecordState.BLOCKED
        release.error = (
            f"release promotion commit {release_promotion.commit_sha} does not match "
            f"approved candidate {approved_commit_sha}"
        )
        result["error"] = release.error
        open_release_blocker(
            state,
            release_package_id=release.id,
            summary=f"Canonical release push blocked: {release.id}",
            details=release.error,
            reporter_agent_id=release_promotion.actor_agent_id,
            result=result,
        )
        return
    if release.policy_version >= 2 and (
        release.scope_validation is None or release.scope_validation.state != "passed"
    ):
        release.state = TeamRecordState.BLOCKED
        release.error = "release scope validation did not pass"
        result["error"] = release.error
        open_release_blocker(
            state,
            release_package_id=release.id,
            summary=f"Canonical release push blocked: {release.id}",
            details=release.error,
            reporter_agent_id=release_promotion.actor_agent_id,
            result=result,
        )
        return
    canonical_base_commit_sha = _optional_text(release.canonical_base_commit_sha)
    if canonical_base_commit_sha is None:
        release.state = TeamRecordState.BLOCKED
        release.error = "release has no immutable canonical destination base commit for publication"
        result["error"] = release.error
        open_release_blocker(
            state,
            release_package_id=release.id,
            summary=f"Canonical release push blocked: {release.id}",
            details=release.error,
            reporter_agent_id=release_promotion.actor_agent_id,
            result=result,
        )
        return
    project = state.projects.get(release_promotion.project_id)
    git = project.git if project is not None else None
    if project is None or git is None:
        release.state = TeamRecordState.BLOCKED
        release.error = (
            f"project {release_promotion.project_id} has no git configuration "
            "for canonical release push"
        )
        result["error"] = release.error
        open_release_blocker(
            state,
            release_package_id=release.id,
            summary=f"Canonical release push blocked: {release.id}",
            details=release.error,
            reporter_agent_id=release_promotion.actor_agent_id,
            result=result,
        )
        return

    source_branch = release.release_branch or release_promotion.target_branch
    target_branch = release.canonical_branch or canonical_release_branch(project, release.id)
    existing = next(
        (
            state.git_promotions[promotion_id]
            for promotion_id in release.git_promotion_record_ids
            if promotion_id in state.git_promotions
            and state.git_promotions[promotion_id].promotion_kind == "canonical_push"
        ),
        None,
    )
    if existing is None:
        existing = GitPromotionRecord(
            id=f"promotion-{uuid4().hex}",
            project_id=project.id,
            release_package_id=release.id,
            actor_agent_id=release_promotion.actor_agent_id,
            source_branch=source_branch,
            target_branch=target_branch,
            promotion_kind="canonical_push",
            git_base_branch=release.git_base_branch,
            base_commit_sha=canonical_base_commit_sha,
            sources=[
                {
                    "source_branch": source_branch,
                    "expected_commit_sha": approved_commit_sha,
                }
            ],
        )
        state.git_promotions[existing.id] = existing
        release.git_promotion_record_ids = _append_unique(
            release.git_promotion_record_ids,
            existing.id,
        )
    existing_base_commit_sha = _optional_text(existing.base_commit_sha)
    if (
        existing_base_commit_sha is not None
        and existing_base_commit_sha != canonical_base_commit_sha
    ):
        release.state = TeamRecordState.BLOCKED
        release.error = (
            f"canonical promotion base {existing_base_commit_sha} does not match "
            f"canonical destination base {canonical_base_commit_sha}"
        )
        result["error"] = release.error
        open_release_blocker(
            state,
            release_package_id=release.id,
            summary=f"Canonical release push blocked: {release.id}",
            details=release.error,
            reporter_agent_id=release_promotion.actor_agent_id,
            result=result,
        )
        return
    existing.base_commit_sha = canonical_base_commit_sha
    release.canonical_branch = target_branch

    if existing.state != "pending":
        return

    payload: dict[str, Any] = {
        "promotion_id": existing.id,
        "promotion_kind": "canonical_push",
        "project_id": project.id,
        "release_package_id": release.id,
        "managed_repo_id": git.managed_repo_id,
        "base_branch": release.git_base_branch,
        "base_commit_sha": canonical_base_commit_sha,
        "source_branch": existing.source_branch,
        "expected_commit_sha": approved_commit_sha,
        "target_branch": existing.target_branch,
    }
    payload.update(_managed_repo_fields(git))
    _emit_git_promotion_event(result, promotion_id=existing.id, payload=payload)
    result["git_promotion_event_ids"].append(existing.id)


def _attach_promotion_failure(
    state: TeamState,
    *,
    promotion: GitPromotionRecord,
    result: dict[str, Any],
) -> None:
    """Block linked records and notify the actor's superior or project lead."""

    deliverable = (
        state.deliverables.get(promotion.deliverable_id)
        if promotion.deliverable_id is not None
        else None
    )
    if deliverable is not None:
        deliverable.state = TeamRecordState.BLOCKED
    release = (
        state.release_packages.get(promotion.release_package_id)
        if promotion.release_package_id is not None
        else None
    )
    release_was_recoverable = release is not None and release.state in {
        TeamRecordState.PENDING,
        TeamRecordState.IN_PROGRESS,
        TeamRecordState.BLOCKED,
    }
    if release_was_recoverable and promotion.promotion_kind == "correction_aggregate":
        manifest_error = _attach_correction_aggregate_manifests(
            state,
            promotion=promotion,
            result=result,
        )
        if manifest_error is not None:
            # A failed consolidation can reject a moved or non-descendant source
            # before it can construct manifests. Preserve that precise Git error
            # instead of replacing it with a secondary manifest-shape diagnostic.
            raw_manifests = promotion.outputs.get("source_manifests")
            if not promotion.error or (
                isinstance(raw_manifests, dict) and bool(raw_manifests)
            ):
                promotion.error = manifest_error
            promotion.outputs["error_code"] = "scope_mismatch"
            result["error"] = promotion.error
    if release_was_recoverable and release is not None:
        _repair_release_review_manifests_from_promotion_evidence(
            state,
            release=release,
            result=result,
        )
    merge_recovery_assignment = (
        _merge_recovery_assignment(
            state,
            release=release,
            promotion=promotion,
        )
        if release_was_recoverable and release is not None
        else None
    )
    if release is not None:
        release.state = TeamRecordState.BLOCKED
        release.error = promotion.error
        if (
            release.policy_version >= 2
            and release.scope_validation is not None
            and promotion.outputs.get("error_code") == "scope_mismatch"
        ):
            release.scope_validation.state = "blocked"
            release.scope_validation.unexpected_commit_shas = _string_list(
                promotion.outputs.get("unexpected_commit_shas"),
                "unexpected_commit_shas",
            )
            release.scope_validation.unexpected_paths = _string_list(
                promotion.outputs.get("unexpected_paths"),
                "unexpected_paths",
            )
            release.scope_validation.validated_at = utc_now()
    details = promotion.error or f"{promotion.promotion_kind} promotion failed"
    if release is not None:
        blocker, _created = open_release_blocker(
            state,
            release_package_id=release.id,
            summary=f"Release {release.id} promotion failed",
            details=details,
            reporter_agent_id=promotion.actor_agent_id,
            initial_owner_agent_id=(
                merge_recovery_assignment[1] if merge_recovery_assignment is not None else None
            ),
            route_initial_owner_even_if_unavailable=(merge_recovery_assignment is not None),
            wake_owner=(
                merge_recovery_assignment is None
                or not _active_stand_downs_for_agent(
                    state,
                    merge_recovery_assignment[1],
                )
            ),
            result=result,
        )
        if merge_recovery_assignment is not None:
            _materialize_merge_recovery_task(
                state,
                release=release,
                failed_promotions=merge_recovery_assignment[0],
                owner_agent_id=merge_recovery_assignment[1],
                assignment_reason=merge_recovery_assignment[2],
                blocker=blocker,
                result=result,
            )
    if deliverable is not None:
        open_deliverable_blocker(
            state,
            deliverable_id=deliverable.id,
            summary=f"Deliverable {deliverable.id} promotion failed",
            details=details,
            reporter_agent_id=promotion.actor_agent_id,
            result=result,
        )
    if release is None and deliverable is None:
        project = state.projects.get(promotion.project_id)
        actor = state.agents.get(promotion.actor_agent_id)
        if project is not None and actor is not None:
            open_or_get_blocker(
                state,
                source_kind=TeamBlockerSourceKind.EXPLICIT,
                source_id=promotion.id,
                summary=f"Git promotion failed: {promotion.id}",
                details=details,
                reporter_agent_id=actor.id,
                team_id=project.team_id,
                project_id=project.id,
                task_id=promotion.task_id,
                result=result,
            )


def _materialize_merge_recovery_task(
    state: TeamState,
    *,
    release: ReleasePackage,
    failed_promotions: list[GitPromotionRecord],
    owner_agent_id: str,
    assignment_reason: str,
    blocker: TeamBlocker,
    result: dict[str, Any],
) -> None:
    now = utc_now()
    if release.work_lineage_id is None:
        ensure_release_work_lineage(state, release)
    boundary_kind = (
        failed_promotions[0].promotion_kind
        if len({promotion.promotion_kind for promotion in failed_promotions}) == 1
        and failed_promotions[0].promotion_kind in {"correction_aggregate", "release"}
        else None
    )
    task_id = _merge_recovery_task_id(
        release.id,
        boundary_kind=boundary_kind,
    )
    description = _merge_recovery_task_description(
        release=release,
        promotions=failed_promotions,
        assignment_reason=assignment_reason,
    )
    boundary_label = (
        "correction aggregation"
        if len(failed_promotions) == 1
        and failed_promotions[0].promotion_kind == "correction_aggregate"
        else "release merge"
    )
    task = state.tasks.get(task_id)
    if task is None:
        task = AgentTask(
            id=task_id,
            owner_agent_id=owner_agent_id,
            project_id=release.project_id,
            authored_by=common_deliverable_author(state, release.deliverable_ids),
            work_item_attributions=combined_deliverable_attributions(state, release.deliverable_ids),
            git_base_branch=release.git_base_branch,
            priority=0,
            state=TeamTaskState.PENDING,
            title=f"Resolve {boundary_label} conflicts for {release.id}",
            description=description,
            created_by_actor="system:git-merge-recovery",
            release_base_package_id=release.id,
            created_at=now,
            updated_at=now,
        )
        state.tasks[task.id] = task
        result.setdefault("created_task_ids", []).append(task.id)
    else:
        if task.state == TeamTaskState.CANCELLED:
            task.cancellation = None
            task.completion = None
            task.owner_agent_id = owner_agent_id
            task.state = TeamTaskState.PENDING
            task.state_entered_at = now
            result.setdefault("reactivated_task_ids", []).append(task.id)
        if task.state == TeamTaskState.PENDING and task.owner_agent_id != owner_agent_id:
            task.owner_agent_id = owner_agent_id
        if task.description != description:
            task.description = description
        task.updated_at = now
        result.setdefault("updated_task_ids", []).append(task.id)

    task.release_base_package_id = release.id
    ensure_task_work_lineage(state, task)

    blocker.affected_task_ids = _append_unique(blocker.affected_task_ids, task.id)
    blocker.updated_at = now
    assign_blocker_owner(
        state,
        blocker=blocker,
        owner_agent_id=task.owner_agent_id,
        actor_agent_id=task.created_by_agent_id,
        reason=(
            f"Git merge recovery task {task.id} was routed to its selected "
            f"{assignment_reason} owner."
        ),
        result=result,
        wake_owner=not _active_stand_downs_for_agent(
            state,
            task.owner_agent_id,
        ),
        now=now,
    )
    for item in state.inbox.values():
        if (
            item.state == TeamInboxItemState.PENDING
            and item.payload.get("blocker_id") == blocker.id
        ):
            item.payload["affected_task_ids"] = list(blocker.affected_task_ids)
            item.updated_at = now
    result["merge_recovery_task_id"] = task.id
    result["merge_recovery_task_owner_agent_id"] = task.owner_agent_id
    result["merge_recovery_assignment_reason"] = assignment_reason


def _merge_recovery_assignment(
    state: TeamState,
    *,
    release: ReleasePackage,
    promotion: GitPromotionRecord,
) -> tuple[list[GitPromotionRecord], str, str] | None:
    if (
        promotion.promotion_kind not in {"feature", "correction_aggregate", "release"}
        or _optional_text(promotion.outputs.get("error_code")) != "merge_conflict"
    ):
        return None
    project = state.projects.get(release.project_id)
    if project is None or project.git is None:
        return None
    failed_promotions = _release_merge_conflict_promotions(state, release)
    if not failed_promotions:
        return None
    owner_agent_id, assignment_reason = _merge_recovery_owner(
        state,
        release=release,
        promotions=failed_promotions,
    )
    if owner_agent_id is None:
        return None
    return failed_promotions, owner_agent_id, assignment_reason


def _release_merge_conflict_promotions(
    state: TeamState,
    release: ReleasePackage,
) -> list[GitPromotionRecord]:
    linked_ids = set(release.git_promotion_record_ids)
    rows = [
        candidate
        for candidate in state.git_promotions.values()
        if candidate.state == "failed"
        and candidate.promotion_kind in {"feature", "correction_aggregate", "release"}
        and _optional_text(candidate.outputs.get("error_code")) == "merge_conflict"
        and (candidate.release_package_id == release.id or candidate.id in linked_ids)
    ]
    rows.sort(key=lambda candidate: (candidate.created_at, candidate.id))
    return rows


def _merge_recovery_owner(
    state: TeamState,
    *,
    release: ReleasePackage,
    promotions: list[GitPromotionRecord],
) -> tuple[str | None, str]:
    owner_sets = [
        _promotion_source_owner_ids(
            state,
            release=release,
            promotion=promotion,
        )
        for promotion in promotions
    ]
    unique_owner_ids = set().union(*owner_sets) if owner_sets else set()
    if owner_sets and all(owner_ids for owner_ids in owner_sets) and len(unique_owner_ids) == 1:
        owner_agent_id = next(iter(unique_owner_ids))
        if _eligible_merge_recovery_owner(
            state,
            agent_id=owner_agent_id,
            project_id=release.project_id,
        ):
            return owner_agent_id, "single_source_owner"

    project = state.projects.get(release.project_id)
    if project is None:
        return None, "project_missing"
    lead_agent_id = project_lead_agent_id(state, project.id) or project.lead_agent_id
    if _eligible_merge_recovery_owner(
        state,
        agent_id=lead_agent_id,
        project_id=project.id,
    ):
        return lead_agent_id, "ambiguous_source_owners_project_lead"
    team = state.teams.get(project.team_id)
    manager_agent_id = team.manager_agent_id if team is not None else None
    if manager_agent_id is not None and _eligible_team_merge_recovery_owner(
        state,
        agent_id=manager_agent_id,
        team_id=project.team_id,
    ):
        return manager_agent_id, "ambiguous_source_owners_team_manager"
    return None, "no_eligible_owner"


def _promotion_source_owner_ids(
    state: TeamState,
    *,
    release: ReleasePackage,
    promotion: GitPromotionRecord,
) -> set[str]:
    source_aliases = _promotion_source_branch_aliases(promotion)
    for source in promotion.sources:
        source_aliases.update(_branch_aliases(source.source_branch))
    source_deliverables = [
        deliverable
        for deliverable_id in release.deliverable_ids
        if (deliverable := state.deliverables.get(deliverable_id)) is not None
        and source_aliases
        and source_aliases & _deliverable_git_branch_aliases(deliverable)
    ]
    if source_deliverables:
        return {deliverable.producer_agent_id for deliverable in source_deliverables}

    deliverable = (
        state.deliverables.get(promotion.deliverable_id)
        if promotion.deliverable_id is not None
        else None
    )
    if deliverable is not None:
        return {deliverable.producer_agent_id}

    task = state.tasks.get(promotion.task_id) if promotion.task_id is not None else None
    if task is not None and task.project_id == release.project_id:
        return {task.owner_agent_id}
    return set()


def _eligible_merge_recovery_owner(
    state: TeamState,
    *,
    agent_id: str,
    project_id: str,
) -> bool:
    agent = state.agents.get(agent_id)
    return bool(
        agent is not None
        and agent.status == AgentStatus.ACTIVE
        and agent_has_project_assignment(state, agent.id, project_id)
    )


def _eligible_team_merge_recovery_owner(
    state: TeamState,
    *,
    agent_id: str,
    team_id: str,
) -> bool:
    agent = state.agents.get(agent_id)
    return bool(
        agent is not None and agent.status == AgentStatus.ACTIVE and agent.team_id == team_id
    )


def _merge_recovery_task_id(
    release_package_id: str,
    *,
    boundary_kind: str | None = None,
) -> str:
    identity = (
        f"{release_package_id}:{boundary_kind}" if boundary_kind is not None else release_package_id
    )
    token = sha256(identity.encode("utf-8")).hexdigest()[:24]
    return f"git-merge-recovery-{token}"


def _merge_recovery_task_description(
    *,
    release: ReleasePackage,
    promotions: list[GitPromotionRecord],
    assignment_reason: str,
) -> str:
    details = [
        (
            f"- {promotion.id} ({promotion.promotion_kind}): "
            f"{_promotion_source_summary(promotion)} -> {promotion.target_branch}; "
            f"{promotion.error or 'merge conflict'}"
            f"{_promotion_conflict_detail(promotion)}"
        )
        for promotion in promotions
    ]
    single_boundary = len(promotions) == 1 and promotions[0].promotion_kind in {
        "correction_aggregate",
        "release",
    }
    recovery_steps = (
        [
            (
                "Resolve this single failed promotion boundary, push the scoped "
                "resolved commit on a new agents/... ref, and call "
                "team_recover_release with that exact ref and commit SHA."
            ),
            (
                "Do not recreate per-correction promotion branches or create a "
                "replacement release package."
            ),
        ]
        if single_boundary
        else [
            (
                "Resolve the failed promotions one at a time. For the promotion currently "
                "selected by team_recover_release, push a scoped resolved commit on a new "
                "agents/... ref and call team_recover_release with that exact ref and "
                "commit SHA."
            ),
            (
                "After each success, repeat for the next failed promotion until the package "
                "can continue through release gates. Do not combine sibling promotion "
                "histories, republish accepted source branches, or create a replacement "
                "release package."
            ),
        ]
    )
    return "\n".join(
        [
            (
                f"Resolve the semantic Git conflicts blocking release package "
                f"{release.id}. Preserve the intended behavior from every reviewed "
                "change in the package."
            ),
            "",
            f"Routing: {assignment_reason}.",
            "",
            "Conflict evidence:",
            *details,
            "",
            *recovery_steps,
            (
                "Escalate only if the intended semantics are genuinely ambiguous; do not "
                "silently discard either side of a conflict."
            ),
        ]
    )


def _promotion_source_summary(promotion: GitPromotionRecord) -> str:
    source_branches = [source.source_branch for source in promotion.sources]
    if not source_branches:
        source_branches = [promotion.source_branch]
    return ", ".join(source_branches)


def _promotion_conflict_detail(promotion: GitPromotionRecord) -> str:
    conflict_branch = _optional_text(promotion.outputs.get("conflict_branch"))
    raw_output = _optional_text(promotion.outputs.get("output"))
    conflict_lines = [
        line.strip()
        for line in (raw_output or "").splitlines()
        if line.strip().startswith("CONFLICT ")
    ][:20]
    parts: list[str] = []
    if conflict_branch is not None:
        parts.append(f"conflict branch {conflict_branch}")
    if conflict_lines:
        parts.append("conflicts: " + " | ".join(conflict_lines))
    return f"; {'; '.join(parts)}" if parts else ""


def _promotion_failure_recipient(
    state: TeamState,
    promotion: GitPromotionRecord,
) -> str | None:
    actor = state.agents.get(promotion.actor_agent_id)
    if actor is not None and actor.reports_to_agent_id is not None:
        return actor.reports_to_agent_id
    lead_agent_id = project_lead_agent_id(state, promotion.project_id)
    if lead_agent_id is not None:
        return lead_agent_id
    return None


__all__ = [
    "GIT_PROMOTION_EVENT_TYPE",
    "_project_git_or_error",
    "_managed_repo_fields",
    "_emit_git_promotion_event",
    "_normalize_agent_branch_ref",
    "_apply_git_review_promotion",
    "_apply_ephemeral_git_review_promotion",
    "_apply_persistent_git_review_promotion",
    "_commit_source_branch",
    "_apply_git_feature_update",
    "_branch_aliases",
    "_promotion_source_branch_aliases",
    "_deliverable_git_branch_aliases",
    "_deliverable_has_git_source_refs",
    "_accepted_source_deliverables_for_feature_promotion",
    "_attach_feature_promotion_to_accepted_deliverables",
    "_is_safe_synthetic_feature_deliverable",
    "_attach_promotion_success",
    "_queue_canonical_push_after_release_success",
    "_attach_promotion_failure",
    "_promotion_failure_recipient",
]
