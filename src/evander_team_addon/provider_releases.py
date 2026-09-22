"""Provider releases."""

from __future__ import annotations

from collections.abc import (
    Mapping,
)
from typing import (
    Any,
)

from evander.admin.web.user_store import (
    AdminWebUserStore,
)
from evander.effects.providers.base import (
    EffectProviderBase,
    scoped_effect_directory_path,
)
from evander.events.models import (
    WorkflowNodeEnvelope,
)

from .git_naming import (
    validate_branch_name,
)
from .provider_events import (
    _materialize,
    _publish_team_events,
)
from .provider_results import (
    _result_outputs_payload,
)
from .provider_schemas import (
    TeamConsolidateGitBranchesEffectDefinition,
    TeamMarkReleasePublishedEffectDefinition,
    TeamPublishReviewBranchEffectDefinition,
    TeamRecordGitPromotionEffectDefinition,
    TeamRecordHumanReviewPreparationResultEffectDefinition,
    TeamVerifyDeliverableAcceptanceEffectDefinition,
    TeamVerifyReleaseCandidateEffectDefinition,
)
from .provider_values import (
    _consolidation_failure,
    _definition_payload,
    _optional_bool,
    _optional_int,
    _optional_mapping,
    _optional_text,
    _required_text,
    _review_publish_failure,
    _runtime_dir,
    _string_list,
)
from .repository import (
    TeamStateConflictError,
    TeamStateRepository,
)
from .services.admin_service import (
    TeamAdminService,
)
from .services.ado_attribution import (
    release_publication_context,
)
from .services.domain.human_reviews import (
    HUMAN_REVIEW_REQUESTED_EVENT_TYPE,
)
from .services.git_release_service import (
    TeamGitReleaseService,
)
from .services.human_review_assignment_service import (
    human_review_eligibility_resolver,
)
from .services.release_gate_service import (
    TeamReleaseGateService,
)
from .services.release_verification_effects import (
    EffectReleaseVerificationExecutor,
)
from .services.release_verification_runner import (
    ReleaseVerificationExecutor,
    TeamReleaseVerificationRunner,
)
from .sqlite_store import (
    TeamIdempotencyConflictError,
)


class TeamRecordGitPromotionEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamRecordGitPromotionEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "promotion_id",
            "success",
            "result_outputs",
            "error",
            "commit_sha",
            "expected_revision",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        git_release_service = TeamGitReleaseService(
            repo,
            human_review_eligibility_resolver=human_review_eligibility_resolver(
                AdminWebUserStore(_runtime_dir(self.runtime_context) / "admin-web.db")
            ),
        )
        try:
            _state, result = git_release_service.record_git_promotion_result(
                promotion_id=_required_text(payload.get("promotion_id"), "promotion_id"),
                success=_optional_bool(payload.get("success"), default=True),
                outputs=_optional_mapping(_result_outputs_payload(payload), "result_outputs") or {},
                error=_optional_text(payload.get("error")),
                commit_sha=_optional_text(payload.get("commit_sha")),
                expected_revision=_optional_int(payload.get("expected_revision"), default=None),
            )
        except TeamStateConflictError as exc:
            return {
                "success": False,
                "error": str(exc),
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            }
        published_event_ids = _publish_team_events(
            self.runtime_context,
            [
                event
                for event in (result.get("events") or [])
                if event.get("event_type") != HUMAN_REVIEW_REQUESTED_EVENT_TYPE
            ],
        )
        if published_event_ids:
            result["published_event_ids"] = published_event_ids
        return result


class TeamRecordHumanReviewPreparationResultEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(
                TeamRecordHumanReviewPreparationResultEffectDefinition,
                effect_definition,
            )
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "human_review_request_id",
            "candidate_commit_sha",
            "candidate_tree_hash",
            "preparation_generation",
            "target",
            "error",
            "request_id",
            "recorded_by",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        request_id = _required_text(
            payload.get("human_review_request_id"),
            "human_review_request_id",
        )
        generation = _optional_int(
            payload.get("preparation_generation"),
            default=None,
        )
        if generation is None or generation < 1:
            raise ValueError("preparation_generation must be greater than zero")
        idempotency_key = _optional_text(payload.get("request_id")) or (
            f"human-review-preparation:{request_id}:{generation}"
        )
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        service = TeamAdminService(repo)
        try:
            _state, result = service.record_human_review_preparation_result_with_result(
                human_review_request_id=request_id,
                recorded_by_user_id=(
                    _optional_text(payload.get("recorded_by"))
                    or "workflow:human-review-preparation"
                ),
                request_id=idempotency_key,
                expected_request_revision=None,
                candidate_commit_sha=_required_text(
                    payload.get("candidate_commit_sha"),
                    "candidate_commit_sha",
                ),
                candidate_tree_hash=_required_text(
                    payload.get("candidate_tree_hash"),
                    "candidate_tree_hash",
                ),
                preparation_generation=generation,
                target=_optional_mapping(payload.get("target"), "target"),
                error=_optional_text(payload.get("error")),
            )
        except (
            TeamIdempotencyConflictError,
            TeamStateConflictError,
            ValueError,
        ) as exc:
            return {
                "success": False,
                "human_review_request_id": request_id,
                "error": str(exc),
            }
        return result


class TeamVerifyReleaseCandidateEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamVerifyReleaseCandidateEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "release_package_id",
            "verification_run_id",
            "candidate_commit_sha",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        release_id = _required_text(payload.get("release_package_id"), "release_package_id")
        run_id = _required_text(payload.get("verification_run_id"), "verification_run_id")
        candidate_sha = _required_text(payload.get("candidate_commit_sha"), "candidate_commit_sha")
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        executor: ReleaseVerificationExecutor = EffectReleaseVerificationExecutor(
            self.runtime_context
        )
        result = TeamReleaseVerificationRunner(
            repo,
            executor,
            release_gate_service=TeamReleaseGateService(
                repo,
                human_review_eligibility_resolver=human_review_eligibility_resolver(
                    AdminWebUserStore(_runtime_dir(self.runtime_context) / "admin-web.db")
                ),
            ),
        ).run(
            release_package_id=release_id,
            verification_run_id=run_id,
            candidate_commit_sha=candidate_sha,
        )
        if not result.get("coalesced"):
            published_event_ids = _publish_team_events(
                self.runtime_context,
                [
                    event
                    for event in (result.get("events") or [])
                    if event.get("event_type") != HUMAN_REVIEW_REQUESTED_EVENT_TYPE
                ],
            )
            if published_event_ids:
                result["published_event_ids"] = published_event_ids
        return result


class TeamVerifyDeliverableAcceptanceEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(
                TeamVerifyDeliverableAcceptanceEffectDefinition,
                effect_definition,
            )
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "deliverable_id",
            "verification_run_id",
            "candidate_commit_sha",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        # One-release compatibility handler for events emitted before the
        # deliverable gate was retired. Never execute a hidden verifier and
        # never mutate the deliverable or wake state.
        return {
            "success": True,
            "deprecated": True,
            "deliverable_id": _optional_text(payload.get("deliverable_id")),
            "verification_run_id": _optional_text(payload.get("verification_run_id")),
            "candidate_commit_sha": _optional_text(payload.get("candidate_commit_sha")),
            "warning": (
                "Deliverable acceptance verification was retired; the release candidate "
                "verification run is authoritative."
            ),
        }


class TeamPublishReviewBranchEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamPublishReviewBranchEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "managed_repo_id",
            "directory_path",
            "source_branch",
            "expected_commit_sha",
            "target_branch",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        try:
            managed_repo_id = _required_text(payload.get("managed_repo_id"), "managed_repo_id")
            target_branch = validate_branch_name(
                _required_text(payload.get("target_branch"), "target_branch")
            )
            source_branch = validate_branch_name(
                _required_text(payload.get("source_branch"), "source_branch")
            )
            expected_commit_sha = _required_text(
                payload.get("expected_commit_sha"), "expected_commit_sha"
            )
            directory_path = scoped_effect_directory_path(
                self.runtime_context,
                _optional_text(payload.get("directory_path")),
            )
        except ValueError as exc:
            return _review_publish_failure(None, None, str(exc))
        admin_service = getattr(self.runtime_context, "admin_service", None)
        promoter = getattr(admin_service, "promote_agent_branch", None)
        if not callable(promoter):
            return _review_publish_failure(
                target_branch, None, "managed Git promotion service is unavailable"
            )
        try:
            result = promoter(
                managed_repo_id=managed_repo_id,
                agent_branch=source_branch,
                target_branch=target_branch,
                expected_commit_sha=expected_commit_sha,
                directory_path=directory_path,
            )
        except Exception as exc:
            return _review_publish_failure(target_branch, None, str(exc))
        output = dict(result) if isinstance(result, Mapping) else {"success": True}
        output.setdefault("success", True)
        output.setdefault("managed_repo_id", managed_repo_id)
        output.setdefault("target_branch", target_branch)
        output.setdefault("review_branch", target_branch)
        output.setdefault("commit_sha", expected_commit_sha)
        return output


class TeamMarkReleasePublishedEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamMarkReleasePublishedEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "release_package_id",
            "success",
            "canonical_branch",
            "published_commit_sha",
            "result_outputs",
            "error",
            "expected_revision",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        repo = TeamStateRepository(_runtime_dir(self.runtime_context))
        git_release_service = TeamGitReleaseService(repo)
        try:
            _state, result = git_release_service.mark_release_published(
                release_package_id=_required_text(
                    payload.get("release_package_id"), "release_package_id"
                ),
                success=_optional_bool(payload.get("success"), default=True),
                canonical_branch=_optional_text(payload.get("canonical_branch")),
                published_commit_sha=_optional_text(payload.get("published_commit_sha")),
                outputs=_optional_mapping(_result_outputs_payload(payload), "result_outputs") or {},
                error=_optional_text(payload.get("error")),
                expected_revision=_optional_int(payload.get("expected_revision"), default=None),
            )
        except TeamStateConflictError as exc:
            return {
                "success": False,
                "error": str(exc),
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            }
        published_event_ids = _publish_team_events(self.runtime_context, result.get("events"))
        if published_event_ids:
            result["published_event_ids"] = published_event_ids
        if result.get("success") and not result.get("error"):
            result["publication_context_id"] = release_publication_context(
                _state,
                release_package_id=payload["release_package_id"],
                managed_git_admin=getattr(
                    getattr(self.runtime_context, "admin_service", None), "managed_git_admin", None
                ),
            )
        return result


class TeamConsolidateGitBranchesEffectProvider(EffectProviderBase):
    @classmethod
    def validate_definition(cls, effect_definition):
        if isinstance(effect_definition, WorkflowNodeEnvelope):
            return _materialize(TeamConsolidateGitBranchesEffectDefinition, effect_definition)
        return effect_definition

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = _definition_payload(
            self.effect_definition,
            "managed_repo_id",
            "directory_path",
            "source_branches",
            "target_branch",
            "base_branch",
            "base_commit_sha",
            "required_source_ancestor_commit_sha",
            "expected_source_commits",
            "allowed_changed_paths",
            "commit_message",
        )
        if resolved_config:
            payload.update(dict(resolved_config))
        try:
            managed_repo_id = _required_text(payload.get("managed_repo_id"), "managed_repo_id")
            source_branches = [
                validate_branch_name(branch)
                for branch in _string_list(payload.get("source_branches"), "source_branches")
            ]
            target_branch = validate_branch_name(
                _required_text(payload.get("target_branch"), "target_branch")
            )
            base_value = _optional_text(payload.get("base_branch"))
            base_branch = validate_branch_name(base_value) if base_value else None
            base_commit_sha = _optional_text(payload.get("base_commit_sha"))
            required_source_ancestor_commit_sha = _optional_text(
                payload.get("required_source_ancestor_commit_sha")
            )
            expected_source_commits = {
                validate_branch_name(str(branch)): _required_text(
                    commit,
                    f"expected_source_commits.{branch}",
                )
                for branch, commit in (
                    _optional_mapping(
                        payload.get("expected_source_commits"),
                        "expected_source_commits",
                    )
                    or {}
                ).items()
            }
            if set(expected_source_commits) != set(source_branches):
                raise ValueError(
                    "expected_source_commits must contain every source branch exactly once"
                )
            allowed_changed_paths = (
                _string_list(
                    payload.get("allowed_changed_paths"),
                    "allowed_changed_paths",
                )
                if payload.get("allowed_changed_paths") is not None
                else None
            )
            directory_path = scoped_effect_directory_path(
                self.runtime_context,
                _optional_text(payload.get("directory_path")),
            )
            commit_message = _optional_text(payload.get("commit_message"))
        except ValueError as exc:
            return _consolidation_failure(None, str(exc))
        if not source_branches:
            return _consolidation_failure(target_branch, "source_branches is required")
        admin_service = getattr(self.runtime_context, "admin_service", None)
        managed_git = getattr(admin_service, "managed_git_admin", None)
        consolidate = getattr(managed_git, "consolidate_branches", None)
        if not callable(consolidate):
            return _consolidation_failure(
                target_branch, "managed Git consolidation service is unavailable"
            )
        try:
            return consolidate(
                managed_repo_id=managed_repo_id,
                directory_path=directory_path,
                source_branches=source_branches,
                target_branch=target_branch,
                base_branch=base_branch,
                base_commit_sha=base_commit_sha,
                required_source_ancestor_commit_sha=(required_source_ancestor_commit_sha),
                expected_source_commits=expected_source_commits,
                allowed_changed_paths=allowed_changed_paths,
                commit_message=commit_message,
            )
        except Exception as exc:
            return _consolidation_failure(target_branch, str(exc))
