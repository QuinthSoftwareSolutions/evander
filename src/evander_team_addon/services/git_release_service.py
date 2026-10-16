from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from ..repository import TeamStateRepository
from ..models import (
    TeamTaskState,
    ReleaseGateKind,
    ReleasePackage,
    TeamBlockerSourceKind,
    TeamInboxItemState,
    TeamRecordState,
    TeamState,
)
from .domain.blockers import (
    active_blockers_for_source,
    open_release_blocker,
    resolve_active_blockers_for_source,
    resolve_blocker,
)
from .domain.common import (
    _append_unique,
    _optional_text,
    _require_present,
    _required_text,
    utc_now,
)
from .domain.git_promotion import (
    _accepted_source_deliverables_for_feature_promotion,
    _attach_feature_promotion_to_accepted_deliverables,
    _attach_promotion_failure,
    _attach_promotion_success,
    _is_safe_synthetic_feature_deliverable,
)
from .domain.project_status import _recalculate_project_status
from .domain.release_gates import (
    _pending_predecessor_gate_obligation,
    _predecessor_release_package_ids,
)
from .domain.release_events import TEAM_RELEASE_APPROVED_EVENT_TYPE
from .domain.release_packages import (
    RELEASE_REPOSITORY_SCOPE_OUTPUT_KEY,
    _normalized_release_deliverable_set,
    _repair_release_review_manifests_from_promotion_evidence,
    allowed_release_parent_task_ids,
)
from .domain.human_reviews import (
    HUMAN_REVIEW_ELIGIBILITY_RESOLVER_RESULT_KEY,
    HUMAN_REVIEW_PREPARATION_REQUESTED_EVENT_TYPE,
    HUMAN_REVIEW_REQUESTED_EVENT_TYPE,
)
from .domain.task_assessment import (
    TaskAssessmentBoundaryError,
    require_release_scope,
)
from .domain.wake import AGENT_WAKE_REQUESTED_EVENT_TYPE
from .commands.outbox import _team_event_outbox_records


class TeamGitReleaseService:
    def __init__(
        self,
        repository: TeamStateRepository,
        *,
        human_review_eligibility_resolver: Callable[[Any], list[str]] | None = None,
    ) -> None:
        self._repository = repository
        self._human_review_eligibility_resolver = human_review_eligibility_resolver

    @property
    def repository(self) -> TeamStateRepository:
        return self._repository

    def record_git_promotion_result(
        self,
        *,
        promotion_id: str,
        success: bool = True,
        outputs: dict[str, Any] | None = None,
        error: str | None = None,
        commit_sha: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_promotion_id = _required_text(promotion_id, "promotion_id")
        normalized_outputs = dict(outputs or {})
        normalized_error = _optional_text(error)
        normalized_commit_sha = _optional_text(commit_sha)
        result: dict[str, Any] = {
            "success": False,
            "promotion_id": normalized_promotion_id,
            "promotion_state": None,
            "deliverable_id": None,
            "release_package_id": None,
            "created_deliverable_ids": [],
            "updated_deliverable_ids": [],
            "created_inbox_item_ids": [],
            "events": [],
            "git_promotion_event_ids": [],
            "error": None,
        }
        if self._human_review_eligibility_resolver is not None:
            result[HUMAN_REVIEW_ELIGIBILITY_RESOLVER_RESULT_KEY] = (
                self._human_review_eligibility_resolver
            )

        def _mutate(state_model: TeamState) -> TeamState:
            promotion = _require_present(
                state_model.git_promotions,
                normalized_promotion_id,
                "git promotion",
            )
            release = state_model.release_packages.get(promotion.release_package_id or "")
            task = state_model.tasks.get(promotion.task_id or "")
            if (
                release is not None and release.state == TeamRecordState.CANCELLED
            ) or (task is not None and task.state == TeamTaskState.CANCELLED):
                result["promotion_state"] = promotion.state
                result["release_package_id"] = promotion.release_package_id
                result["ignored_terminal_state"] = True
                result["success"] = True
                return state_model
            if promotion.state == "retired":
                result["promotion_state"] = promotion.state
                result["deliverable_id"] = promotion.deliverable_id
                result["release_package_id"] = promotion.release_package_id
                result["success"] = True
                return state_model
            durable_output_keys = {
                "correction_aggregate": (
                    "correction_deliverable_ids",
                    "required_source_ancestor_commit_sha",
                ),
                "release": (RELEASE_REPOSITORY_SCOPE_OUTPUT_KEY,),
            }.get(promotion.promotion_kind, ())
            durable_outputs = {
                key: promotion.outputs[key]
                for key in durable_output_keys
                if key in promotion.outputs
            }
            result_outputs = dict(normalized_outputs)
            if promotion.promotion_kind == "release":
                result_outputs.pop(RELEASE_REPOSITORY_SCOPE_OUTPUT_KEY, None)
            promotion.outputs = {**result_outputs, **durable_outputs}
            if normalized_commit_sha is not None:
                promotion.commit_sha = normalized_commit_sha
            if success:
                promotion.state = "succeeded"
                promotion.error = None
                _attach_promotion_success(
                    state_model,
                    promotion=promotion,
                    commit_sha=normalized_commit_sha,
                    result=result,
                )
            else:
                promotion.state = "failed"
                promotion.error = normalized_error or "git promotion failed"
                result["error"] = promotion.error
                _attach_promotion_failure(
                    state_model,
                    promotion=promotion,
                    result=result,
                )
            promotion.updated_at = utc_now()
            _recalculate_project_status(state_model, promotion.project_id)
            result["promotion_state"] = promotion.state
            result["deliverable_id"] = promotion.deliverable_id
            result["release_package_id"] = promotion.release_package_id
            result["success"] = True
            return state_model

        state = self._repository.update_with_outbox(
            _mutate,
            expected_revision=expected_revision,
            outbox_records=lambda: _team_event_outbox_records(
                [
                    event
                    for event in result["events"]
                    if event.get("event_type")
                    in {
                        AGENT_WAKE_REQUESTED_EVENT_TYPE,
                        HUMAN_REVIEW_PREPARATION_REQUESTED_EVENT_TYPE,
                        HUMAN_REVIEW_REQUESTED_EVENT_TYPE,
                        TEAM_RELEASE_APPROVED_EVENT_TYPE,
                    }
                ]
            ),
        )
        result.pop(HUMAN_REVIEW_ELIGIBILITY_RESOLVER_RESULT_KEY, None)
        result["revision"] = state.revision
        return state, result

    def repair_feature_promotion_deliverables(
        self,
        *,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        """Move safe synthetic feature deliverables back onto accepted sources."""

        result: dict[str, Any] = {
            "success": False,
            "applied": False,
            "repaired_promotion_ids": [],
            "removed_deliverable_ids": [],
            "updated_deliverable_ids": [],
            "skipped_promotion_ids": [],
        }

        def _mutate(state_model: TeamState) -> TeamState:
            changed_project_ids: set[str] = set()
            for promotion in sorted(
                state_model.git_promotions.values(),
                key=lambda item: (item.created_at, item.id),
            ):
                if (
                    promotion.promotion_kind != "feature"
                    or promotion.state != "succeeded"
                    or promotion.deliverable_id is None
                ):
                    continue
                synthetic = state_model.deliverables.get(promotion.deliverable_id)
                if synthetic is None:
                    result["skipped_promotion_ids"].append(promotion.id)
                    continue
                if not _is_safe_synthetic_feature_deliverable(
                    state_model,
                    deliverable=synthetic,
                    promotion=promotion,
                ):
                    result["skipped_promotion_ids"].append(promotion.id)
                    continue
                matching_deliverables = _accepted_source_deliverables_for_feature_promotion(
                    state_model,
                    promotion,
                    exclude_deliverable_ids={synthetic.id},
                )
                if not matching_deliverables:
                    result["skipped_promotion_ids"].append(promotion.id)
                    continue
                repair_result: dict[str, Any] = {"updated_deliverable_ids": []}
                commit_sha = promotion.commit_sha or synthetic.git_refs.commit_sha
                if not _attach_feature_promotion_to_accepted_deliverables(
                    state_model,
                    promotion=promotion,
                    commit_sha=commit_sha,
                    result=repair_result,
                    deliverables=matching_deliverables,
                ):
                    result["skipped_promotion_ids"].append(promotion.id)
                    continue
                task = state_model.tasks.get(synthetic.task_id)
                if task is not None:
                    task.deliverable_ids = [
                        item_id for item_id in task.deliverable_ids if item_id != synthetic.id
                    ]
                    task.updated_at = utc_now()
                state_model.deliverables.pop(synthetic.id, None)
                result["repaired_promotion_ids"].append(promotion.id)
                result["removed_deliverable_ids"].append(synthetic.id)
                for deliverable_id in repair_result["updated_deliverable_ids"]:
                    result["updated_deliverable_ids"] = _append_unique(
                        result["updated_deliverable_ids"],
                        deliverable_id,
                    )
                changed_project_ids.add(promotion.project_id)
            for project_id in changed_project_ids:
                _recalculate_project_status(state_model, project_id)
            result["success"] = True
            result["applied"] = bool(result["repaired_promotion_ids"])
            return state_model

        state = self._repository.update(_mutate, expected_revision=expected_revision)
        result["revision"] = state.revision
        return state, result

    def repair_release_review_manifests(
        self,
        *,
        release_package_id: str,
        expected_revision: int,
    ) -> tuple[TeamState, dict[str, Any]]:
        """Hydrate missing reviewed manifests from durable pinned-promotion evidence."""

        normalized_release_id = _required_text(
            release_package_id,
            "release_package_id",
        )
        result: dict[str, Any] = {
            "success": False,
            "applied": False,
            "release_package_id": normalized_release_id,
            "repaired_review_manifest_ids": [],
            "updated_deliverable_ids": [],
            "missing_review_manifest_ids": [],
            "diagnostics": [],
        }

        def _mutate(state_model: TeamState) -> TeamState:
            release = _require_present(
                state_model.release_packages,
                normalized_release_id,
                "release package",
            )
            missing_manifest_ids = _repair_release_review_manifests_from_promotion_evidence(
                state_model,
                release=release,
                result=result,
            )
            result["missing_review_manifest_ids"] = missing_manifest_ids
            result["applied"] = bool(result["repaired_review_manifest_ids"])
            result["success"] = not missing_manifest_ids
            return state_model

        state = self._repository.update(
            _mutate,
            expected_revision=expected_revision,
        )
        result["revision"] = state.revision
        return state, result

    def mark_release_published(
        self,
        *,
        release_package_id: str,
        success: bool = True,
        canonical_branch: str | None = None,
        published_commit_sha: str | None = None,
        outputs: dict[str, Any] | None = None,
        error: str | None = None,
        expected_revision: int | None = None,
    ) -> tuple[TeamState, dict[str, Any]]:
        normalized_id = _required_text(release_package_id, "release_package_id")
        normalized_canonical = _optional_text(canonical_branch)
        normalized_commit = _optional_text(published_commit_sha)
        normalized_error = _optional_text(error)
        # outputs are accepted for parity with the git effect contract; they are
        # not persisted on the release package today.
        _ = dict(outputs or {})
        result: dict[str, Any] = {
            "success": False,
            "release_package_id": normalized_id,
            "state": None,
            "created_inbox_item_ids": [],
            "created_blocker_ids": [],
            "resolved_blocker_ids": [],
            "events": [],
            "superseded_release_package_ids": [],
            "resolved_inbox_item_ids": [],
            "ignored_terminal_state": False,
            "error": None,
        }

        def _mutate(state_model: TeamState) -> TeamState:
            release = _require_present(
                state_model.release_packages,
                normalized_id,
                "release package",
            )
            now = utc_now()
            if release.state == TeamRecordState.ACCEPTED:
                conflicting_commit = (
                    normalized_commit is not None
                    and normalized_commit != release.published_commit_sha
                )
                conflicting_branch = (
                    normalized_canonical is not None
                    and normalized_canonical != release.canonical_branch
                )
                if conflicting_commit or conflicting_branch:
                    result["error"] = (
                        f"published release {release.id} is immutable; "
                        "late publication result was ignored"
                    )
                result["state"] = release.state.value
                result["ignored_terminal_state"] = True
                result["success"] = True
                return state_model
            if release.state in {TeamRecordState.REJECTED, TeamRecordState.CANCELLED}:
                result["state"] = release.state.value
                result["ignored_terminal_state"] = True
                result["success"] = True
                return state_model
            if success:
                publication_error = _release_publication_validation_error(
                    state_model,
                    release=release,
                    canonical_branch=normalized_canonical,
                    published_commit_sha=normalized_commit,
                )
                if publication_error is not None:
                    release.state = TeamRecordState.BLOCKED
                    release.error = publication_error
                    release.updated_at = now
                    result["error"] = publication_error
                    open_release_blocker(
                        state_model,
                        release_package_id=release.id,
                        summary=f"Release publication blocked: {release.id}",
                        details=publication_error,
                        result=result,
                        now=now,
                    )
                    _recalculate_project_status(state_model, release.project_id)
                    result["state"] = release.state.value
                    result["success"] = True
                    return state_model
                release.state = TeamRecordState.ACCEPTED
                if normalized_canonical is not None:
                    release.canonical_branch = normalized_canonical
                if normalized_commit is not None:
                    release.published_commit_sha = normalized_commit
                release.published_at = now
                release.error = None
                for blocker in active_blockers_for_source(
                    state_model,
                    source_kind=TeamBlockerSourceKind.RELEASE_PACKAGE,
                    source_id=release.id,
                ):
                    resolve_blocker(
                        state_model,
                        blocker=blocker,
                        actor_agent_id=None,
                        dispatch_id=None,
                        note="Release package was published successfully.",
                        reference_ids=[release.id],
                        now=now,
                    )
                    result["resolved_blocker_ids"].append(blocker.id)
                for review_id in release.review_request_ids:
                    resolve_active_blockers_for_source(
                        state_model,
                        source_kind=TeamBlockerSourceKind.REVIEW_REQUEST,
                        source_id=review_id,
                        actor_agent_id=None,
                        dispatch_id=None,
                        note="Release package was published successfully.",
                        reference_ids=[release.id],
                        result=result,
                        now=now,
                    )
                deliverable_set = _normalized_release_deliverable_set(release.deliverable_ids)
                if deliverable_set:
                    predecessor_ids = _predecessor_release_package_ids(state_model, release)
                    for candidate in state_model.release_packages.values():
                        if (
                            candidate.id == release.id
                            or candidate.project_id != release.project_id
                            or candidate.state != TeamRecordState.BLOCKED
                            or (candidate.created_at, candidate.id)
                            >= (release.created_at, release.id)
                            or (
                                candidate.id not in predecessor_ids
                                and _normalized_release_deliverable_set(candidate.deliverable_ids)
                                != deliverable_set
                            )
                        ):
                            continue
                        candidate.state = TeamRecordState.REJECTED
                        candidate.error = None
                        candidate.updated_at = now
                        result["superseded_release_package_ids"].append(candidate.id)
                        result["resolved_inbox_item_ids"].extend(
                            _resolve_superseded_release_failure_inbox_items(
                                state_model,
                                release=candidate,
                                now=now,
                            )
                        )
                        for blocker in active_blockers_for_source(
                            state_model,
                            source_kind=TeamBlockerSourceKind.RELEASE_PACKAGE,
                            source_id=candidate.id,
                        ):
                            resolve_blocker(
                                state_model,
                                blocker=blocker,
                                actor_agent_id=None,
                                dispatch_id=None,
                                note=(
                                    "Release package was superseded by successfully published "
                                    f"package {release.id}."
                                ),
                                reference_ids=[release.id],
                                now=now,
                            )
                            result["resolved_blocker_ids"].append(blocker.id)
                        for review_id in candidate.review_request_ids:
                            resolve_active_blockers_for_source(
                                state_model,
                                source_kind=TeamBlockerSourceKind.REVIEW_REQUEST,
                                source_id=review_id,
                                actor_agent_id=None,
                                dispatch_id=None,
                                note=(
                                    "Release review was superseded by successfully published "
                                    f"package {release.id}."
                                ),
                                reference_ids=[release.id],
                                result=result,
                                now=now,
                            )
            else:
                release.state = TeamRecordState.BLOCKED
                release.error = normalized_error or "release publication failed"
                result["error"] = release.error
                publication_blocker, _ = open_release_blocker(
                    state_model,
                    release_package_id=release.id,
                    summary=f"Release publication blocked: {release.id}",
                    details=release.error,
                    result=result,
                    now=now,
                )
                from .domain.git_recovery_publication import record_publication_incident
                record_publication_incident(state_model, release=release, blocker=publication_blocker, outputs=outputs or {})
            release.updated_at = now
            _recalculate_project_status(state_model, release.project_id)
            result["state"] = release.state.value
            result["success"] = True
            from .domain.git_recovery_continuation import reconcile_git_recovery_successors
            reconcile_git_recovery_successors(state_model, result=result, now=now)
            return state_model

        state = self._repository.update(_mutate, expected_revision=expected_revision)
        result["revision"] = state.revision
        return state, result


def _release_publication_validation_error(
    state: TeamState,
    *,
    release: ReleasePackage,
    canonical_branch: str | None,
    published_commit_sha: str | None,
) -> str | None:
    try:
        require_release_scope(
            state,
            project_id=release.project_id,
            deliverable_ids=release.deliverable_ids,
            allowed_parent_task_ids=allowed_release_parent_task_ids(
                state,
                project_id=release.project_id,
                predecessor_release_package_id=(release.supersedes_release_package_id),
            ),
        )
    except TaskAssessmentBoundaryError as exc:
        return f"release publication scope is no longer valid: {exc.message}"

    candidate_commit_sha = _optional_text(getattr(release, "candidate_commit_sha", None))
    approved_commit_sha = _optional_text(getattr(release, "approved_commit_sha", None))
    if candidate_commit_sha is None:
        return "release publication requires candidate_commit_sha"
    if approved_commit_sha is None:
        return "release publication requires approved_commit_sha"
    if approved_commit_sha != candidate_commit_sha:
        return (
            f"approved commit {approved_commit_sha} does not match "
            f"release candidate {candidate_commit_sha}"
        )
    if published_commit_sha is None:
        return "release publication requires published_commit_sha"
    if published_commit_sha != approved_commit_sha:
        return (
            f"published commit {published_commit_sha} does not match "
            f"approved candidate {approved_commit_sha}"
        )

    human_review_error = _human_review_publication_validation_error(
        state,
        release=release,
        candidate_commit_sha=candidate_commit_sha,
    )
    if human_review_error is not None:
        return human_review_error

    release_branch = _optional_text(getattr(release, "release_branch", None))
    target_branch = canonical_branch or _optional_text(getattr(release, "canonical_branch", None))
    if release_branch is None:
        return "release publication requires release_branch"
    if target_branch is None:
        return "release publication requires canonical_branch"

    linked_promotion_ids = set(getattr(release, "git_promotion_record_ids", ()) or ())
    for promotion in state.git_promotions.values():
        if (
            promotion.id not in linked_promotion_ids
            or promotion.release_package_id != release.id
            or promotion.project_id != release.project_id
            or promotion.promotion_kind != "canonical_push"
            or promotion.state != "succeeded"
            or _optional_text(promotion.commit_sha) != published_commit_sha
            or _optional_text(promotion.source_branch) != release_branch
            or _optional_text(promotion.target_branch) != target_branch
        ):
            continue
        if any(
            _optional_text(getattr(source, "source_branch", None)) == release_branch
            and _optional_text(getattr(source, "expected_commit_sha", None)) == approved_commit_sha
            for source in (getattr(promotion, "sources", ()) or ())
        ):
            return None
    return (
        "release publication requires a linked succeeded canonical promotion "
        f"for {release_branch} -> {target_branch} at {published_commit_sha}"
    )


def _human_review_publication_validation_error(
    state: TeamState,
    *,
    release: ReleasePackage,
    candidate_commit_sha: str,
) -> str | None:
    manifest = getattr(release, "manifest", None)
    human_requirement = (
        getattr(manifest, "human_review_requirement", None) if manifest is not None else None
    )
    carried_findings = (
        getattr(manifest, "carried_human_findings", None) if manifest is not None else None
    )
    requests = _record_collection_values(state, "human_review_requests")
    linked_requests = [
        request
        for request in requests
        if _optional_text(getattr(request, "release_package_id", None)) == release.id
    ]
    has_human_obligation = (
        human_requirement is not None or bool(carried_findings) or bool(linked_requests)
    )
    gate_sequence = getattr(manifest, "gate_sequence", None)
    if gate_sequence is not None:
        has_human_obligation = any(
            stage.kind == ReleaseGateKind.HUMAN_REVIEW and stage.enabled
            for stage in gate_sequence
        )
        if not has_human_obligation:
            pending_obligation = _pending_predecessor_gate_obligation(state, release)
            if (
                pending_obligation is not None
                and pending_obligation[0] == ReleaseGateKind.HUMAN_REVIEW
            ):
                return (
                    f"release {release.id} requires a candidate-bound waiver for its "
                    "predecessor's human review obligation"
                )
    if not has_human_obligation:
        return None
    if len(linked_requests) != 1:
        return (
            f"release {release.id} has a human review obligation but requires "
            "exactly one human review request"
        )

    request = linked_requests[0]
    request_state = getattr(getattr(request, "state", None), "value", None)
    if request_state is None:
        request_state = getattr(request, "state", None)
    if str(request_state or "").strip().lower() != "approved":
        return f"human review request for release {release.id} is not approved"
    if _optional_text(getattr(request, "candidate_commit_sha", None)) != candidate_commit_sha:
        return f"human review request for release {release.id} does not match candidate commit"

    candidate_tree_hash = _optional_text(getattr(release, "candidate_tree_hash", None))
    if candidate_tree_hash is None:
        return f"human-reviewed release {release.id} requires candidate_tree_hash"
    if _optional_text(getattr(request, "candidate_tree_hash", None)) != candidate_tree_hash:
        return f"human review request for release {release.id} does not match candidate tree"

    approved_decisions = [
        decision
        for decision in _record_collection_values(state, "human_review_decisions")
        if _optional_text(getattr(decision, "human_review_request_id", None))
        == _optional_text(getattr(request, "id", None))
        and str(getattr(decision, "decision", "") or "").strip().lower() == "approved"
    ]
    if len(approved_decisions) != 1:
        return (
            f"approved human review request for release {release.id} requires "
            "exactly one durable approval decision"
        )

    if manifest is not None:
        manifest_commit_sha = _optional_text(getattr(manifest, "candidate_commit_sha", None))
        manifest_tree_hash = _optional_text(getattr(manifest, "candidate_tree_hash", None))
        if manifest_commit_sha is not None and manifest_commit_sha != candidate_commit_sha:
            return f"release manifest for {release.id} does not match candidate commit"
        if manifest_tree_hash is not None and manifest_tree_hash != candidate_tree_hash:
            return f"release manifest for {release.id} does not match candidate tree"
        carried_finding_ids = {
            finding_id
            for finding in (getattr(manifest, "carried_human_findings", None) or ())
            if (
                finding_id := _optional_text(
                    getattr(finding, "finding_id", None) or getattr(finding, "id", None)
                )
            )
            is not None
        }
        confirmed_finding_ids = set(
            getattr(approved_decisions[0], "confirmed_finding_ids", None) or ()
        )
        missing_confirmations = carried_finding_ids - confirmed_finding_ids
        if missing_confirmations:
            return (
                f"human approval for release {release.id} does not confirm carried "
                f"findings: {', '.join(sorted(missing_confirmations))}"
            )
    return None


def _record_collection_values(owner: object, field_name: str) -> list[object]:
    raw_records = getattr(owner, field_name, None)
    if isinstance(raw_records, Mapping):
        return list(raw_records.values())
    if isinstance(raw_records, (list, tuple, set)):
        return list(raw_records)
    return []


def _resolve_superseded_release_failure_inbox_items(
    state: TeamState,
    *,
    release: ReleasePackage,
    now: datetime,
) -> list[str]:
    promotion_ids = {
        promotion.id
        for promotion in state.git_promotions.values()
        if promotion.id in release.git_promotion_record_ids
        or promotion.release_package_id == release.id
    }
    resolved_ids: list[str] = []
    for item in state.inbox.values():
        if item.state != TeamInboxItemState.PENDING:
            continue
        kind = _optional_text(item.payload.get("kind"))
        linked = (
            kind == "release_publish_failed"
            and _optional_text(item.payload.get("release_package_id")) == release.id
        ) or (
            kind == "git_promotion_failed"
            and _optional_text(item.payload.get("promotion_id")) in promotion_ids
        )
        if not linked:
            continue
        item.state = TeamInboxItemState.HANDLED
        item.updated_at = now
        resolved_ids.append(item.id)
    return resolved_ids
