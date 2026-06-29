from __future__ import annotations

from typing import Any
from uuid import uuid4

from ..repository import TeamStateRepository
from ..models import (
    AgentInboxItem,
    TeamInboxItemType,
    TeamInboxPriority,
    TeamRecordState,
    TeamState,
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
from .domain.release_packages import _release_failure_recipient


class TeamGitReleaseService:
    def __init__(self, repository: TeamStateRepository) -> None:
        self._repository = repository

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

        def _mutate(state_model: TeamState) -> TeamState:
            promotion = _require_present(
                state_model.git_promotions,
                normalized_promotion_id,
                "git promotion",
            )
            promotion.outputs = normalized_outputs
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

        state = self._repository.update(_mutate, expected_revision=expected_revision)
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
            "error": None,
        }

        def _mutate(state_model: TeamState) -> TeamState:
            release = _require_present(
                state_model.release_packages,
                normalized_id,
                "release package",
            )
            now = utc_now()
            if success:
                release.state = TeamRecordState.ACCEPTED
                if normalized_canonical is not None:
                    release.canonical_branch = normalized_canonical
                if normalized_commit is not None:
                    release.published_commit_sha = normalized_commit
                release.published_at = now
                release.error = None
            else:
                release.state = TeamRecordState.BLOCKED
                release.error = normalized_error or "release publication failed"
                result["error"] = release.error
                recipient_id = _release_failure_recipient(state_model, release)
                if recipient_id is not None and recipient_id in state_model.agents:
                    inbox_id = f"inbox-{uuid4().hex}"
                    state_model.inbox[inbox_id] = AgentInboxItem(
                        id=inbox_id,
                        recipient_agent_id=recipient_id,
                        sender_agent_id=None,
                        item_type=TeamInboxItemType.PROGRESS_REPORT,
                        response_requested=True,
                        priority=TeamInboxPriority.NORMAL,
                        payload={
                            "kind": "release_publish_failed",
                            "release_package_id": release.id,
                            "project_id": release.project_id,
                            "error": release.error,
                        },
                    )
                    result["created_inbox_item_ids"].append(inbox_id)
            release.updated_at = now
            _recalculate_project_status(state_model, release.project_id)
            result["state"] = release.state.value
            result["success"] = True
            return state_model

        state = self._repository.update(_mutate, expected_revision=expected_revision)
        result["revision"] = state.revision
        return state, result
