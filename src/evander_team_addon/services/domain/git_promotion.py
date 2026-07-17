from __future__ import annotations

from typing import Any
from uuid import uuid4
from ...git_naming import (
    canonical_release_branch,
    feature_branch,
    review_branch,
    validate_branch_name,
)
from ...models import (
    AgentInboxItem,
    Deliverable,
    DeliverableGitRefs,
    DeliverableReviewManifest,
    EphemeralAssignmentState,
    GitPromotionRecord,
    PersistentAgent,
    ReleasePackage,
    TeamInboxItemType,
    TeamInboxPriority,
    TeamRecordState,
    TeamState,
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
from .git_common import (
    GIT_PROMOTION_EVENT_TYPE,
    _emit_git_promotion_event,
    _managed_repo_fields,
    _normalize_agent_branch_ref,
    _project_git_or_error,
)
from .release_packages import _queue_release_promotion_if_ready


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
    promotion_id = f"promotion-{uuid4().hex}"
    state.git_promotions[promotion_id] = GitPromotionRecord(
        id=promotion_id,
        project_id=project.id,
        task_id=task_id,
        actor_agent_id=actor.id,
        source_branch=source_branches[0],
        target_branch=target_branch,
        promotion_kind="feature",
    )
    payload: dict[str, Any] = {
        "promotion_id": promotion_id,
        "promotion_kind": "feature",
        "project_id": project.id,
        **_managed_repo_fields(git),
        "base_branch": git.base_branch,
        "source_branches": source_branches,
        "expected_source_commits": expected_source_commits,
        "target_branch": target_branch,
        "task_id": task_id,
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
    deliverable = (
        state.deliverables.get(promotion.deliverable_id)
        if promotion.deliverable_id is not None
        else None
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
        task.deliverable_ids = _append_unique(task.deliverable_ids, deliverable_id)
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
        _clear_recovered_release_block(state, release=release, promotion=promotion)
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
        elif kind == "feature":
            _queue_release_promotion_if_ready(
                state,
                release=release,
                actor_agent_id=promotion.actor_agent_id,
                result=result,
            )
        elif kind == "canonical_push":
            release.canonical_branch = release.canonical_branch or promotion.target_branch


def _clear_recovered_release_block(
    state: TeamState,
    *,
    release: ReleasePackage,
    promotion: GitPromotionRecord,
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
        return
    if release_promotion.commit_sha != approved_commit_sha:
        release.state = TeamRecordState.BLOCKED
        release.error = (
            f"release promotion commit {release_promotion.commit_sha} does not match "
            f"approved candidate {approved_commit_sha}"
        )
        result["error"] = release.error
        return
    if release.policy_version >= 2 and (
        release.scope_validation is None or release.scope_validation.state != "passed"
    ):
        release.state = TeamRecordState.BLOCKED
        release.error = "release scope validation did not pass"
        result["error"] = release.error
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
    release.canonical_branch = target_branch

    if existing.state != "pending":
        return

    payload: dict[str, Any] = {
        "promotion_id": existing.id,
        "promotion_kind": "canonical_push",
        "project_id": project.id,
        "release_package_id": release.id,
        "managed_repo_id": git.managed_repo_id,
        "base_branch": git.base_branch,
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
    recipient_id = _promotion_failure_recipient(state, promotion)
    if recipient_id is not None and recipient_id in state.agents:
        inbox_id = f"inbox-{uuid4().hex}"
        state.inbox[inbox_id] = AgentInboxItem(
            id=inbox_id,
            recipient_agent_id=recipient_id,
            sender_agent_id=None,
            task_id=(
                promotion.task_id
                if promotion.task_id is not None and promotion.task_id in state.tasks
                else None
            ),
            item_type=TeamInboxItemType.PROGRESS_REPORT,
            response_requested=True,
            priority=TeamInboxPriority.NORMAL,
            payload={
                "kind": "git_promotion_failed",
                "promotion_id": promotion.id,
                "promotion_kind": promotion.promotion_kind,
                "project_id": promotion.project_id,
                "source_branch": promotion.source_branch,
                "target_branch": promotion.target_branch,
                "error": promotion.error,
            },
        )
        result["created_inbox_item_ids"].append(inbox_id)


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
