"""Team provider composition and compatibility exports.

Register workflow providers here; implement each provider in its domain module.
Shared runtime, Git, artifact, prompt, and projection helpers depend on the
narrower modules directly. Compatibility exports preserve existing imports;
new callers and tests should import the module that owns the implementation."""

from __future__ import annotations

from evander.workflow.contracts import (
    register_node_output_schema,
)
from evander.workflow.plugins import (
    ProviderPluginRegistration,
)

from .provider_agents import (
    TeamAcceptEphemeralDeliverableEffectProvider,
    TeamFinalizeAgentDispatchEffectProvider,
    TeamMarkEphemeralCleanedUpEffectProvider,
    TeamMarkEphemeralSpawnedEffectProvider,
    TeamRegisterAgentEffectProvider,
    TeamRequestEphemeralAgentsEffectProvider,
    TeamRestoreAgentEffectProvider,
    TeamRetireAgentEffectProvider,
)
from .provider_artifacts import (
    _append_unique_text as _append_unique_text,
)
from .provider_artifacts import (
    _artifact_space_path_from_ref as _artifact_space_path_from_ref,
)
from .provider_artifacts import (
    _artifact_space_path_segment as _artifact_space_path_segment,
)
from .provider_artifacts import (
    _bounded_generated_identifier as _bounded_generated_identifier,
)
from .provider_artifacts import (
    _ensure_deliverable_artifact_space_version as _ensure_deliverable_artifact_space_version,
)
from .provider_artifacts import (
    _ensure_memory_artifact_space_version as _ensure_memory_artifact_space_version,
)
from .provider_artifacts import (
    _import_review_artifact_version as _import_review_artifact_version,
)
from .provider_artifacts import (
    _import_team_artifact_for_session as _import_team_artifact_for_session,
)
from .provider_artifacts import (
    _prepare_review_artifacts_for_lease as _prepare_review_artifacts_for_lease,
)

# Stable imports retained for integrations using the original provider module.
from .provider_artifacts import (
    _publish_deliverable_artifact_bundle as _publish_deliverable_artifact_bundle,
)
from .provider_artifacts import (
    _publish_memory_artifact_bundle as _publish_memory_artifact_bundle,
)
from .provider_artifacts import (
    _require_artifact_space_read as _require_artifact_space_read,
)
from .provider_artifacts import (
    _require_team_artifact_space_read as _require_team_artifact_space_read,
)
from .provider_artifacts import (
    _review_artifact_destination_path as _review_artifact_destination_path,
)
from .provider_artifacts import (
    _review_artifact_ref_requests as _review_artifact_ref_requests,
)
from .provider_artifacts import (
    _review_artifact_ref_version_segment as _review_artifact_ref_version_segment,
)
from .provider_artifacts import (
    _review_artifact_request_payload as _review_artifact_request_payload,
)
from .provider_artifacts import (
    _team_artifact_import_destination_path as _team_artifact_import_destination_path,
)
from .provider_catalog import (
    _TEAM_INTERNAL_EVENT_CATALOG,
    _TEAM_WORKFLOW_EDITOR_CATALOG,
    _catalog_entry,
    _catalog_field,
)
from .provider_catalog import (
    _revision_field as _revision_field,
)
from .provider_catalog import (
    _team_internal_event as _team_internal_event,
)
from .provider_catalog import (
    _valid_field as _valid_field,
)
from .provider_chat import (
    TeamEnqueueOperatorChatMessageEffectProvider,
    TeamRecordExternalChatDeliveryEffectProvider,
    TeamRecordOperatorEscalationDeliveryEffectProvider,
)
from .provider_chat import (
    _bridge_author_ids as _bridge_author_ids,
)
from .provider_chat import (
    _resolve_bridge_communication_target as _resolve_bridge_communication_target,
)
from .provider_completion import (
    TeamAgentPromptCompletionFinalizeHook as TeamAgentPromptCompletionFinalizeHook,
)
from .provider_constants import (
    _ARTIFACT_SPACE_COMPONENT_MAX_BYTES as _ARTIFACT_SPACE_COMPONENT_MAX_BYTES,
)
from .provider_constants import (
    _ARTIFACT_SPACE_ENCODED_SEGMENT_PREFIX as _ARTIFACT_SPACE_ENCODED_SEGMENT_PREFIX,
)
from .provider_constants import (
    _ARTIFACT_SPACE_REF_PREFIX as _ARTIFACT_SPACE_REF_PREFIX,
)
from .provider_constants import (
    _ARTIFACT_SPACE_SAFE_SEGMENT_RE as _ARTIFACT_SPACE_SAFE_SEGMENT_RE,
)
from .provider_constants import (
    _TEAM_CODE_INDEX_TOOL_NAMES as _TEAM_CODE_INDEX_TOOL_NAMES,
)
from .provider_constants import (
    _TEAM_GOVERNANCE_WAKE_DEFINITION_PAYLOAD_FIELDS as _TEAM_GOVERNANCE_WAKE_DEFINITION_PAYLOAD_FIELDS,
)
from .provider_constants import (
    _TEAM_REVIEW_ARTIFACT_ID_DEFAULT as _TEAM_REVIEW_ARTIFACT_ID_DEFAULT,
)
from .provider_constants import (
    _TEAM_WAKE_DEFINITION_PAYLOAD_FIELDS as _TEAM_WAKE_DEFINITION_PAYLOAD_FIELDS,
)
from .provider_constants import (
    LEGACY_TEAM_AGENT_GUIDANCE_SECTION_IDS as LEGACY_TEAM_AGENT_GUIDANCE_SECTION_IDS,
)
from .provider_constants import (
    TEAM_AGENT_GUIDANCE_PATH as TEAM_AGENT_GUIDANCE_PATH,
)
from .provider_constants import (
    TEAM_AGENT_GUIDANCE_SECTION_ID as TEAM_AGENT_GUIDANCE_SECTION_ID,
)
from .provider_constants import (
    TEAM_AGENT_GUIDANCE_VERSION as TEAM_AGENT_GUIDANCE_VERSION,
)
from .provider_constants import (
    TEAM_GOVERNANCE_GUIDANCE_SECTION_ID as TEAM_GOVERNANCE_GUIDANCE_SECTION_ID,
)
from .provider_constants import (
    TEAM_GOVERNANCE_GUIDANCE_VERSION as TEAM_GOVERNANCE_GUIDANCE_VERSION,
)
from .provider_constants import (
    TEAM_WAKE_OUTPUT_MAX_ERROR_CHARS as TEAM_WAKE_OUTPUT_MAX_ERROR_CHARS,
)
from .provider_constants import (
    TEAM_WAKE_OUTPUT_MAX_ID_CHARS as TEAM_WAKE_OUTPUT_MAX_ID_CHARS,
)
from .provider_constants import (
    TEAM_WAKE_OUTPUT_MAX_IDS_PER_FIELD as TEAM_WAKE_OUTPUT_MAX_IDS_PER_FIELD,
)
from .provider_constants import (
    TEAM_WAKE_OUTPUT_MAX_LEASE_SUMMARIES as TEAM_WAKE_OUTPUT_MAX_LEASE_SUMMARIES,
)
from .provider_constants import (
    TEAM_WAKE_STATE_CONFLICT_MAX_ATTEMPTS as TEAM_WAKE_STATE_CONFLICT_MAX_ATTEMPTS,
)
from .provider_constants import (
    TEAM_WAKE_STATE_CONFLICT_RETRY_DELAY_SECONDS as TEAM_WAKE_STATE_CONFLICT_RETRY_DELAY_SECONDS,
)
from .provider_constants import (
    logger as logger,
)
from .provider_events import (
    TeamPublishOutboxEventsEffectProvider,
    _materialize,
)
from .provider_events import (
    _internal_event_bus as _internal_event_bus,
)
from .provider_events import (
    _publish_human_review_admin_notification as _publish_human_review_admin_notification,
)
from .provider_events import (
    _publish_team_events as _publish_team_events,
)
from .provider_events import (
    _publish_team_outbox_events as _publish_team_outbox_events,
)
from .provider_events import (
    _scheduled_internal_event_store as _scheduled_internal_event_store,
)
from .provider_events import (
    _team_event_payload as _team_event_payload,
)
from .provider_events import (
    _team_event_run_at as _team_event_run_at,
)
from .provider_failures import (
    _new_preparation_blocker_ids as _new_preparation_blocker_ids,
)
from .provider_failures import (
    _record_dependency_git_preparation_blocker as _record_dependency_git_preparation_blocker,
)
from .provider_failures import (
    _record_project_git_preparation_blockers as _record_project_git_preparation_blockers,
)
from .provider_failures import (
    _record_review_artifact_preparation_blockers as _record_review_artifact_preparation_blockers,
)
from .provider_failures import (
    _record_review_repository_preparation_blockers as _record_review_repository_preparation_blockers,
)
from .provider_failures import (
    _record_wake_failure as _record_wake_failure,
)
from .provider_failures import (
    _review_artifact_preparation_errors as _review_artifact_preparation_errors,
)
from .provider_git_contexts import (
    _build_dependency_composition_manifest as _build_dependency_composition_manifest,
)
from .provider_git_contexts import (
    _create_project_repo_git_work_contexts as _create_project_repo_git_work_contexts,
)
from .provider_git_contexts import (
    _project_repo_agent_branch as _project_repo_agent_branch,
)
from .provider_git_contexts import (
    _project_repo_git_work_context_id as _project_repo_git_work_context_id,
)
from .provider_git_contexts import (
    _validate_project_repo_git_work_context as _validate_project_repo_git_work_context,
)
from .provider_git_errors import (
    DependencyGitPreparationError as DependencyGitPreparationError,
)
from .provider_git_errors import (
    ProjectGitPreparationError as ProjectGitPreparationError,
)
from .provider_git_errors import (
    ReviewRepositoryPreparationError as ReviewRepositoryPreparationError,
)
from .provider_git_metadata import (
    _ensure_project_repo_git_preparation_attempt as _ensure_project_repo_git_preparation_attempt,
)
from .provider_git_metadata import (
    _git_work_context_prompt_payload as _git_work_context_prompt_payload,
)
from .provider_git_metadata import (
    _project_repo_context_tasks as _project_repo_context_tasks,
)
from .provider_git_metadata import (
    _project_repo_git_preparation_attempt_id as _project_repo_git_preparation_attempt_id,
)
from .provider_git_metadata import (
    _project_repo_payload as _project_repo_payload,
)
from .provider_git_metadata import (
    _project_repo_prepare_record as _project_repo_prepare_record,
)
from .provider_git_metadata import (
    _project_repo_task_preparation_generation as _project_repo_task_preparation_generation,
)
from .provider_git_metadata import (
    _project_repo_task_worktree_name as _project_repo_task_worktree_name,
)
from .provider_git_metadata import (
    _record_project_repo_git_preparation_receipt as _record_project_repo_git_preparation_receipt,
)
from .provider_git_metadata import (
    _resolved_project_git_identity as _resolved_project_git_identity,
)
from .provider_git_repositories import (
    _managed_repository_default_branch as _managed_repository_default_branch,
)
from .provider_git_repositories import (
    _pin_review_repository_snapshots as _pin_review_repository_snapshots,
)
from .provider_git_repositories import (
    _prepare_project_repo as _prepare_project_repo,
)
from .provider_git_repositories import (
    _prepare_project_repos as _prepare_project_repos,
)
from .provider_git_repositories import (
    _review_repository_snapshots_for_project as _review_repository_snapshots_for_project,
)
from .provider_git_repositories import (
    _validate_review_repository_dispatch_compatibility as _validate_review_repository_dispatch_compatibility,
)
from .provider_git_sources import (
    _accepted_deliverable_pinned_ref as _accepted_deliverable_pinned_ref,
)
from .provider_git_sources import (
    _deliverable_has_git_state as _deliverable_has_git_state,
)
from .provider_git_sources import (
    _managed_git_base_tree_hash as _managed_git_base_tree_hash,
)
from .provider_git_sources import (
    _merge_dependency_git_source_rows as _merge_dependency_git_source_rows,
)
from .provider_git_sources import (
    _prior_project_repo_task_base_commit as _prior_project_repo_task_base_commit,
)
from .provider_git_sources import (
    _project_repo_task_dependency_git_sources as _project_repo_task_dependency_git_sources,
)
from .provider_git_sources import (
    _project_repo_task_dependency_git_sources_unchecked as _project_repo_task_dependency_git_sources_unchecked,
)
from .provider_git_sources import (
    _rejected_deliverable_continuation_dependency_source as _rejected_deliverable_continuation_dependency_source,
)
from .provider_git_sources import (
    _rejected_deliverable_continuation_identity_commit as _rejected_deliverable_continuation_identity_commit,
)
from .provider_git_sources import (
    _verified_human_review_correction_git_source as _verified_human_review_correction_git_source,
)
from .provider_git_sources import (
    _verified_rejected_candidate_git_source as _verified_rejected_candidate_git_source,
)
from .provider_git_sources import (
    _verified_rejected_deliverable_continuation_git_source as _verified_rejected_deliverable_continuation_git_source,
)
from .provider_git_sources import (
    _verified_rejected_deliverable_continuation_git_sources as _verified_rejected_deliverable_continuation_git_sources,
)
from .provider_git_sources import (
    _verify_rejected_candidate_git_source as _verify_rejected_candidate_git_source,
)
from .provider_git_sources import (
    _verify_rejected_deliverable_continuation_git_source as _verify_rejected_deliverable_continuation_git_source,
)
from .provider_git_worktrees import (
    _prepare_project_repo_task_worktree as _prepare_project_repo_task_worktree,
)
from .provider_guidance import (
    _mark_agent_guidance_seeded as _mark_agent_guidance_seeded,
)
from .provider_guidance import (
    _mark_governance_guidance_seeded as _mark_governance_guidance_seeded,
)
from .provider_guidance import (
    _mark_project_repo_prepared as _mark_project_repo_prepared,
)
from .provider_guidance import (
    _mark_project_repos_prepared as _mark_project_repos_prepared,
)
from .provider_guidance import (
    _metadata_has_current_agent_guidance as _metadata_has_current_agent_guidance,
)
from .provider_guidance import (
    _metadata_has_current_governance_guidance as _metadata_has_current_governance_guidance,
)
from .provider_guidance import (
    _metadata_has_current_inactive_agent_guidance as _metadata_has_current_inactive_agent_guidance,
)
from .provider_guidance import (
    _seed_team_agent_guidance as _seed_team_agent_guidance,
)
from .provider_guidance import (
    _seed_team_governance_guidance as _seed_team_governance_guidance,
)
from .provider_guidance import (
    _session_metadata as _session_metadata,
)
from .provider_memory import (
    TeamIndexMemoryEffectProvider,
    TeamMemoryQueryProvider,
    TeamReindexMemoryEffectProvider,
)
from .provider_memory import (
    _sync_team_memory_records_to_semantic_index as _sync_team_memory_records_to_semantic_index,
)
from .provider_memory import (
    _team_memory_id_selection as _team_memory_id_selection,
)
from .provider_memory import (
    _team_memory_matches_project_filter as _team_memory_matches_project_filter,
)
from .provider_memory import (
    _team_memory_records_by_ids as _team_memory_records_by_ids,
)
from .provider_memory import (
    _team_memory_records_for_team as _team_memory_records_for_team,
)
from .provider_projection import (
    _activity_summary_refs_are_visible as _activity_summary_refs_are_visible,
)
from .provider_projection import (
    _agent_payload_with_filtered_activity_summary as _agent_payload_with_filtered_activity_summary,
)
from .provider_projection import (
    _blocker_payload_is_visible as _blocker_payload_is_visible,
)
from .provider_projection import (
    _dispatch_work_scope_survives as _dispatch_work_scope_survives,
)
from .provider_projection import (
    _filter_agent_activity_summaries as _filter_agent_activity_summaries,
)
from .provider_projection import (
    _filter_deliverable_git_refs as _filter_deliverable_git_refs,
)
from .provider_projection import (
    _filter_ids as _filter_ids,
)
from .provider_projection import (
    _filter_runtime_operations as _filter_runtime_operations,
)
from .provider_projection import (
    _filter_wake_work_record_fingerprints as _filter_wake_work_record_fingerprints,
)
from .provider_projection import (
    _filtered_team_state_payload as _filtered_team_state_payload,
)
from .provider_projection import (
    _mapping_items as _mapping_items,
)
from .provider_projection import (
    _runtime_row_is_visible as _runtime_row_is_visible,
)
from .provider_projection import (
    _scoped_blocker_event_payload as _scoped_blocker_event_payload,
)
from .provider_projection import (
    _scoped_blocker_payload as _scoped_blocker_payload,
)
from .provider_projection import (
    _scoped_runtime_attention_row as _scoped_runtime_attention_row,
)
from .provider_projection import (
    _string_items as _string_items,
)
from .provider_prompt_queue import (
    _agent_prompt_records_for_dispatches as _agent_prompt_records_for_dispatches,
)
from .provider_prompt_queue import (
    _coerce_mapping as _coerce_mapping,
)
from .provider_prompt_queue import (
    _completed_agent_prompt_records as _completed_agent_prompt_records,
)
from .provider_prompt_queue import (
    _completed_agent_prompt_records_from_records as _completed_agent_prompt_records_from_records,
)
from .provider_prompt_queue import (
    _completed_prompt_session_id as _completed_prompt_session_id,
)
from .provider_prompt_queue import (
    _discard_failed_team_prompt_queue_record as _discard_failed_team_prompt_queue_record,
)
from .provider_prompt_queue import (
    _effect_context_from_worker as _effect_context_from_worker,
)
from .provider_prompt_queue import (
    _enqueue_team_agent_prompt as _enqueue_team_agent_prompt,
)
from .provider_prompt_queue import (
    _reconcile_team_queued_prompt_reservations as _reconcile_team_queued_prompt_reservations,
)
from .provider_prompt_queue import (
    _record_is_queued_prompt_marker as _record_is_queued_prompt_marker,
)
from .provider_prompt_queue import (
    _record_outputs as _record_outputs,
)
from .provider_prompt_queue import (
    _result_reports_failure as _result_reports_failure,
)
from .provider_prompt_queue import (
    _resume_failure_message as _resume_failure_message,
)
from .provider_prompt_queue import (
    _resume_failure_reports_missing_thread as _resume_failure_reports_missing_thread,
)
from .provider_prompt_queue import (
    _runtime_dir_from_worker as _runtime_dir_from_worker,
)
from .provider_prompt_queue import (
    _team_dispatch_execution_is_live as _team_dispatch_execution_is_live,
)
from .provider_prompt_queue import (
    _team_native_thread_receipt as _team_native_thread_receipt,
)
from .provider_prompt_queue import (
    _team_queue_dispatch_is_current as _team_queue_dispatch_is_current,
)
from .provider_prompt_queue import (
    _team_queue_governance_group_id as _team_queue_governance_group_id,
)
from .provider_prompt_queue import (
    _truthy_result_success as _truthy_result_success,
)
from .provider_prompt_queue import (
    _waiting_agent_prompt_records as _waiting_agent_prompt_records,
)
from .provider_prompts import (
    _agent_guidance_identity as _agent_guidance_identity,
)
from .provider_prompts import (
    _agent_prompt_payload as _agent_prompt_payload,
)
from .provider_prompts import (
    _assigned_deliverable_review_guidance as _assigned_deliverable_review_guidance,
)
from .provider_prompts import (
    _build_governance_prompt as _build_governance_prompt,
)
from .provider_prompts import (
    _build_prompt as _build_prompt,
)
from .provider_prompts import (
    _build_team_chat_prompt as _build_team_chat_prompt,
)
from .provider_prompts import (
    _compact_team_chat_history_body as _compact_team_chat_history_body,
)
from .provider_prompts import (
    _deliverable_review_procedure as _deliverable_review_procedure,
)
from .provider_prompts import (
    _manager_fallback_procedure as _manager_fallback_procedure,
)
from .provider_prompts import (
    _object_field as _object_field,
)
from .provider_prompts import (
    _object_mapping_field as _object_mapping_field,
)
from .provider_prompts import (
    _operator_chat_participant_scope_guidance as _operator_chat_participant_scope_guidance,
)
from .provider_prompts import (
    _project_repo_prompt_guidance as _project_repo_prompt_guidance,
)
from .provider_prompts import (
    _project_repos_prompt_guidance as _project_repos_prompt_guidance,
)
from .provider_prompts import (
    _prompt_context_key as _prompt_context_key,
)
from .provider_prompts import (
    _prompt_context_payload as _prompt_context_payload,
)
from .provider_prompts import (
    _prompt_runtime_attention as _prompt_runtime_attention,
)
from .provider_prompts import (
    _source_evidence_procedure as _source_evidence_procedure,
)
from .provider_prompts import (
    _superior_acceptance_guidance as _superior_acceptance_guidance,
)
from .provider_prompts import (
    _task_creation_mandate_guidance as _task_creation_mandate_guidance,
)
from .provider_prompts import (
    _team_agent_guidance_content as _team_agent_guidance_content,
)
from .provider_prompts import (
    _team_agent_guidance_inactive_content as _team_agent_guidance_inactive_content,
)
from .provider_prompts import (
    _team_chat_agent_display_name as _team_chat_agent_display_name,
)
from .provider_prompts import (
    _team_governance_guidance_content as _team_governance_guidance_content,
)
from .provider_queries import (
    TeamSessionRoutingQueryProvider,
    TeamStateQueryProvider,
)
from .provider_releases import (
    TeamConsolidateGitBranchesEffectProvider,
    TeamMarkReleasePublishedEffectProvider,
    TeamPublishReviewBranchEffectProvider,
    TeamRecordGitPromotionEffectProvider,
    TeamRecordHumanReviewPreparationResultEffectProvider,
    TeamVerifyDeliverableAcceptanceEffectProvider,
    TeamVerifyReleaseCandidateEffectProvider,
)
from .provider_results import (
    _case_effect_error_result as _case_effect_error_result,
)
from .provider_results import (
    _case_effect_success_result as _case_effect_success_result,
)
from .provider_results import (
    _register_agent_effect_error_result as _register_agent_effect_error_result,
)
from .provider_results import (
    _register_agent_effect_success_result as _register_agent_effect_success_result,
)
from .provider_results import (
    _restore_agent_effect_error_result as _restore_agent_effect_error_result,
)
from .provider_results import (
    _restore_agent_effect_success_result as _restore_agent_effect_success_result,
)
from .provider_results import (
    _result_outputs_payload as _result_outputs_payload,
)
from .provider_results import (
    _retire_agent_effect_error_result as _retire_agent_effect_error_result,
)
from .provider_results import (
    _retire_agent_effect_success_result as _retire_agent_effect_success_result,
)
from .provider_results import (
    _review_cohort_effect_error_result as _review_cohort_effect_error_result,
)
from .provider_results import (
    _review_cohort_effect_success_result as _review_cohort_effect_success_result,
)
from .provider_results import (
    _review_request_effect_error_result as _review_request_effect_error_result,
)
from .provider_results import (
    _review_request_effect_success_result as _review_request_effect_success_result,
)
from .provider_results import (
    _task_effect_error_result as _task_effect_error_result,
)
from .provider_results import (
    _task_effect_success_result as _task_effect_success_result,
)
from .provider_results import (
    _validate_create_task_effect_scope as _validate_create_task_effect_scope,
)
from .provider_reviews import (
    TeamCreateOperatorDirectiveEffectProvider,
    TeamCreateReviewCohortEffectProvider,
    TeamCreateReviewRequestEffectProvider,
)
from .provider_runtime import (
    _active_team_mcp_tools_for_other_dispatches as _active_team_mcp_tools_for_other_dispatches,
)
from .provider_runtime import (
    _context_metadata_text as _context_metadata_text,
)
from .provider_runtime import (
    _cost_guard_blocked_teams as _cost_guard_blocked_teams,
)
from .provider_runtime import (
    _cost_guard_candidate_team_ids as _cost_guard_candidate_team_ids,
)
from .provider_runtime import (
    _ordinary_team_tools_for_agent as _ordinary_team_tools_for_agent,
)
from .provider_runtime import (
    _prompt_session_health_agent_ids as _prompt_session_health_agent_ids,
)
from .provider_runtime import (
    _register_team_dispatch_model_usage_context as _register_team_dispatch_model_usage_context,
)
from .provider_runtime import (
    _review_mcp_capability_observation as _review_mcp_capability_observation,
)
from .provider_runtime import (
    _session_records_by_id as _session_records_by_id,
)
from .provider_runtime import (
    _session_repository_for_manifest_updates as _session_repository_for_manifest_updates,
)
from .provider_runtime import (
    _session_repository_from_context as _session_repository_from_context,
)
from .provider_runtime import (
    _set_session_team_mcp_tools as _set_session_team_mcp_tools,
)
from .provider_runtime import (
    _team_chat_mutation_allowed as _team_chat_mutation_allowed,
)
from .provider_runtime import (
    _team_chat_mutation_scope_lost as _team_chat_mutation_scope_lost,
)
from .provider_runtime import (
    _team_runtime_lease_active as _team_runtime_lease_active,
)
from .provider_runtime import (
    _team_session_has_active_prompt as _team_session_has_active_prompt,
)
from .provider_runtime import (
    _unique_requested_tools as _unique_requested_tools,
)
from .provider_schemas import (
    TEAM_OUTPUT_SCHEMAS,
    TeamAcceptEphemeralDeliverableEffectDefinition,
    TeamConsolidateGitBranchesEffectDefinition,
    TeamCreateCaseEffectDefinition,
    TeamCreateOperatorDirectiveEffectDefinition,
    TeamCreateReviewCohortEffectDefinition,
    TeamCreateReviewRequestEffectDefinition,
    TeamCreateTaskEffectDefinition,
    TeamCreateTaskForRoleEffectDefinition,
    TeamDeliverableCreatedTriggerDefinition,
    TeamEnqueueOperatorChatMessageEffectDefinition,
    TeamFinalizeAgentDispatchEffectDefinition,
    TeamIndexMemoryEffectDefinition,
    TeamMarkEphemeralCleanedUpEffectDefinition,
    TeamMarkEphemeralSpawnedEffectDefinition,
    TeamMarkReleasePublishedEffectDefinition,
    TeamMemoryQueryDefinition,
    TeamPublishOutboxEventsEffectDefinition,
    TeamPublishReviewBranchEffectDefinition,
    TeamRecordExternalChatDeliveryEffectDefinition,
    TeamRecordGitPromotionEffectDefinition,
    TeamRecordHumanReviewPreparationResultEffectDefinition,
    TeamRecordOperatorEscalationDeliveryEffectDefinition,
    TeamRegisterAgentEffectDefinition,
    TeamReindexMemoryEffectDefinition,
    TeamRequestEphemeralAgentsEffectDefinition,
    TeamRestoreAgentEffectDefinition,
    TeamRetireAgentEffectDefinition,
    TeamSessionRoutingQueryDefinition,
    TeamSetTaskWorkStateEffectDefinition,
    TeamStateQueryDefinition,
    TeamUpdateCaseEffectDefinition,
    TeamVerifyDeliverableAcceptanceEffectDefinition,
    TeamVerifyReleaseCandidateEffectDefinition,
    TeamWakeDueAgentsEffectDefinition,
    TeamWakeGovernanceManagersEffectDefinition,
)
from .provider_tasks import (
    TeamCreateCaseEffectProvider,
    TeamCreateTaskEffectProvider,
    TeamSetTaskWorkStateEffectProvider,
    TeamUpdateCaseEffectProvider,
)
from .provider_role_tasks import TeamCreateTaskForRoleEffectProvider
from .provider_triggers import (
    TeamDeliverableCreatedTriggerProvider,
)
from .provider_triggers import (
    _team_deliverable_created_payload as _team_deliverable_created_payload,
)
from .provider_triggers import (
    _team_deliverable_trigger_matches as _team_deliverable_trigger_matches,
)
from .provider_triggers import (
    _team_payload_datetime as _team_payload_datetime,
)
from .provider_triggers import (
    _team_trigger_directory_scope as _team_trigger_directory_scope,
)
from .provider_values import (
    _configured_definition_fields as _configured_definition_fields,
)
from .provider_values import (
    _consolidation_failure as _consolidation_failure,
)
from .provider_values import (
    _definition_payload as _definition_payload,
)
from .provider_values import (
    _json_dumps as _json_dumps,
)
from .provider_values import (
    _list_of_mappings as _list_of_mappings,
)
from .provider_values import (
    _number_or_none as _number_or_none,
)
from .provider_values import (
    _number_or_zero as _number_or_zero,
)
from .provider_values import (
    _optional_bool as _optional_bool,
)
from .provider_values import (
    _optional_float as _optional_float,
)
from .provider_values import (
    _optional_int as _optional_int,
)
from .provider_values import (
    _optional_mapping as _optional_mapping,
)
from .provider_values import (
    _optional_text as _optional_text,
)
from .provider_values import (
    _payload_field_configured as _payload_field_configured,
)
from .provider_values import (
    _required_text as _required_text,
)
from .provider_values import (
    _review_publish_failure as _review_publish_failure,
)
from .provider_values import (
    _runtime_dir as _runtime_dir,
)
from .provider_values import (
    _string_list as _string_list,
)
from .provider_values import (
    _team_datetime_utc as _team_datetime_utc,
)
from .provider_wake import (
    TeamWakeDueAgentsEffectProvider,
)
from .provider_wake_chat import (
    TeamWakeGovernanceManagersEffectProvider,
)
from .provider_wake_chat import (
    _activate_delivered_team_prompt as _activate_delivered_team_prompt,
)
from .provider_wake_chat import (
    _copy_team_chat_attachments_for_dispatch as _copy_team_chat_attachments_for_dispatch,
)
from .provider_wake_chat import (
    _dispatch_queued_team_chat_messages as _dispatch_queued_team_chat_messages,
)
from .provider_wake_chat import (
    _team_chat_wake_lease as _team_chat_wake_lease,
)
from .provider_wake_chat import (
    build_voice_reconciliation_context as build_voice_reconciliation_context,
)
from .provider_wake_payloads import (
    _team_wake_collection_count as _team_wake_collection_count,
)
from .provider_wake_payloads import (
    _team_wake_dispatch_status as _team_wake_dispatch_status,
)
from .provider_wake_payloads import (
    _team_wake_dispatch_summary as _team_wake_dispatch_summary,
)
from .provider_wake_payloads import (
    _team_wake_final_dispatch_payload as _team_wake_final_dispatch_payload,
)
from .provider_wake_payloads import (
    _team_wake_governance_dispatch_payload as _team_wake_governance_dispatch_payload,
)
from .provider_wake_payloads import (
    _team_wake_has_chat_claim_candidate as _team_wake_has_chat_claim_candidate,
)
from .provider_wake_payloads import (
    _team_wake_id_items as _team_wake_id_items,
)
from .provider_wake_payloads import (
    _team_wake_invocation_key as _team_wake_invocation_key,
)
from .provider_wake_payloads import (
    _team_wake_lease_summary as _team_wake_lease_summary,
)
from .provider_wake_payloads import (
    _team_wake_output_text as _team_wake_output_text,
)
from .provider_wake_payloads import (
    _team_wake_record_ids as _team_wake_record_ids,
)


def register_providers() -> ProviderPluginRegistration:
    from .case_context_providers import (
        CONTEXT_DEFINITIONS,
        CONTEXT_EFFECT_PROVIDERS,
        CONTEXT_OUTPUTS,
        CONTEXT_QUERY_PROVIDERS,
        context_catalog,
        materialize,
    )

    for node_type in CONTEXT_DEFINITIONS:
        register_node_output_schema(node_type=node_type, outputs=CONTEXT_OUTPUTS)
    for node_type, outputs in TEAM_OUTPUT_SCHEMAS.items():
        register_node_output_schema(node_type=node_type, outputs=outputs)
    return ProviderPluginRegistration(
        trigger_providers={
            "trigger:team:deliverable-created": TeamDeliverableCreatedTriggerProvider,
        },
        effect_providers={
            **CONTEXT_EFFECT_PROVIDERS,
            "effect:team:wake-due-agents": TeamWakeDueAgentsEffectProvider,
            "effect:team:wake-governance-managers": TeamWakeGovernanceManagersEffectProvider,
            "effect:team:publish-outbox-events": TeamPublishOutboxEventsEffectProvider,
            "effect:team:finalize-agent-dispatch": (TeamFinalizeAgentDispatchEffectProvider),
            "effect:team:enqueue-operator-chat-message": (
                TeamEnqueueOperatorChatMessageEffectProvider
            ),
            "effect:team:record-external-chat-delivery": (
                TeamRecordExternalChatDeliveryEffectProvider
            ),
            "effect:team:record-operator-escalation-delivery": (
                TeamRecordOperatorEscalationDeliveryEffectProvider
            ),
            "effect:team:create-operator-directive": (TeamCreateOperatorDirectiveEffectProvider),
            "effect:team:create-review-request": TeamCreateReviewRequestEffectProvider,
            "effect:team:create-review-cohort": TeamCreateReviewCohortEffectProvider,
            "effect:team:create-task": TeamCreateTaskEffectProvider,
            "effect:team:create-task-for-role": TeamCreateTaskForRoleEffectProvider,
            "effect:team:set-task-work-state": TeamSetTaskWorkStateEffectProvider,
            "effect:team:create-case": TeamCreateCaseEffectProvider,
            "effect:team:update-case": TeamUpdateCaseEffectProvider,
            "effect:team:register-agent": TeamRegisterAgentEffectProvider,
            "effect:team:restore-agent": TeamRestoreAgentEffectProvider,
            "effect:team:retire-agent": TeamRetireAgentEffectProvider,
            "effect:team:request-ephemeral-agents": TeamRequestEphemeralAgentsEffectProvider,
            "effect:team:mark-ephemeral-spawned": TeamMarkEphemeralSpawnedEffectProvider,
            "effect:team:accept-ephemeral-deliverable": (
                TeamAcceptEphemeralDeliverableEffectProvider
            ),
            "effect:team:mark-ephemeral-cleaned-up": (TeamMarkEphemeralCleanedUpEffectProvider),
            "effect:team:record-git-promotion": TeamRecordGitPromotionEffectProvider,
            "effect:team:record-human-review-preparation-result": (
                TeamRecordHumanReviewPreparationResultEffectProvider
            ),
            "effect:team:verify-release-candidate": TeamVerifyReleaseCandidateEffectProvider,
            "effect:team:verify-deliverable-acceptance": (
                TeamVerifyDeliverableAcceptanceEffectProvider
            ),
            "effect:team:publish-review-branch": TeamPublishReviewBranchEffectProvider,
            "effect:team:mark-release-published": TeamMarkReleasePublishedEffectProvider,
            "effect:team:consolidate-git-branches": (TeamConsolidateGitBranchesEffectProvider),
            "effect:team:index-memory": TeamIndexMemoryEffectProvider,
            "effect:team:reindex-memory": TeamReindexMemoryEffectProvider,
        },
        query_providers={
            **CONTEXT_QUERY_PROVIDERS,
            "query:team:state": TeamStateQueryProvider,
            "query:team:memory": TeamMemoryQueryProvider,
            "query:team:session-routing": TeamSessionRoutingQueryProvider,
        },
        node_definition_resolvers={
            **{
                name: (lambda node, model=model: materialize(model, node))
                for name, model in CONTEXT_DEFINITIONS.items()
            },
            "trigger:team:deliverable-created": (
                lambda node: _materialize(TeamDeliverableCreatedTriggerDefinition, node)
            ),
            "effect:team:wake-due-agents": (
                lambda node: _materialize(TeamWakeDueAgentsEffectDefinition, node)
            ),
            "effect:team:wake-governance-managers": (
                lambda node: _materialize(
                    TeamWakeGovernanceManagersEffectDefinition,
                    node,
                )
            ),
            "effect:team:publish-outbox-events": (
                lambda node: _materialize(TeamPublishOutboxEventsEffectDefinition, node)
            ),
            "effect:team:finalize-agent-dispatch": (
                lambda node: _materialize(
                    TeamFinalizeAgentDispatchEffectDefinition,
                    node,
                )
            ),
            "effect:team:enqueue-operator-chat-message": (
                lambda node: _materialize(
                    TeamEnqueueOperatorChatMessageEffectDefinition,
                    node,
                )
            ),
            "effect:team:record-external-chat-delivery": (
                lambda node: _materialize(
                    TeamRecordExternalChatDeliveryEffectDefinition,
                    node,
                )
            ),
            "effect:team:record-operator-escalation-delivery": (
                lambda node: _materialize(
                    TeamRecordOperatorEscalationDeliveryEffectDefinition,
                    node,
                )
            ),
            "effect:team:create-operator-directive": (
                lambda node: _materialize(
                    TeamCreateOperatorDirectiveEffectDefinition,
                    node,
                )
            ),
            "effect:team:create-review-request": (
                lambda node: _materialize(TeamCreateReviewRequestEffectDefinition, node)
            ),
            "effect:team:create-review-cohort": (
                lambda node: _materialize(TeamCreateReviewCohortEffectDefinition, node)
            ),
            "effect:team:create-task": (
                lambda node: _materialize(TeamCreateTaskEffectDefinition, node)
            ),
            "effect:team:create-task-for-role": (
                lambda node: _materialize(TeamCreateTaskForRoleEffectDefinition, node)
            ),
            "effect:team:set-task-work-state": (
                lambda node: _materialize(TeamSetTaskWorkStateEffectDefinition, node)
            ),
            "effect:team:create-case": (
                lambda node: _materialize(TeamCreateCaseEffectDefinition, node)
            ),
            "effect:team:update-case": (
                lambda node: _materialize(TeamUpdateCaseEffectDefinition, node)
            ),
            "effect:team:register-agent": (
                lambda node: _materialize(TeamRegisterAgentEffectDefinition, node)
            ),
            "effect:team:restore-agent": (
                lambda node: _materialize(TeamRestoreAgentEffectDefinition, node)
            ),
            "effect:team:retire-agent": (
                lambda node: _materialize(TeamRetireAgentEffectDefinition, node)
            ),
            "effect:team:request-ephemeral-agents": (
                lambda node: _materialize(TeamRequestEphemeralAgentsEffectDefinition, node)
            ),
            "effect:team:mark-ephemeral-spawned": (
                lambda node: _materialize(TeamMarkEphemeralSpawnedEffectDefinition, node)
            ),
            "effect:team:accept-ephemeral-deliverable": (
                lambda node: _materialize(TeamAcceptEphemeralDeliverableEffectDefinition, node)
            ),
            "effect:team:mark-ephemeral-cleaned-up": (
                lambda node: _materialize(TeamMarkEphemeralCleanedUpEffectDefinition, node)
            ),
            "effect:team:record-git-promotion": (
                lambda node: _materialize(TeamRecordGitPromotionEffectDefinition, node)
            ),
            "effect:team:record-human-review-preparation-result": (
                lambda node: _materialize(
                    TeamRecordHumanReviewPreparationResultEffectDefinition,
                    node,
                )
            ),
            "effect:team:verify-release-candidate": (
                lambda node: _materialize(TeamVerifyReleaseCandidateEffectDefinition, node)
            ),
            "effect:team:verify-deliverable-acceptance": (
                lambda node: _materialize(
                    TeamVerifyDeliverableAcceptanceEffectDefinition,
                    node,
                )
            ),
            "effect:team:publish-review-branch": (
                lambda node: _materialize(TeamPublishReviewBranchEffectDefinition, node)
            ),
            "effect:team:mark-release-published": (
                lambda node: _materialize(TeamMarkReleasePublishedEffectDefinition, node)
            ),
            "effect:team:consolidate-git-branches": (
                lambda node: _materialize(TeamConsolidateGitBranchesEffectDefinition, node)
            ),
            "effect:team:index-memory": (
                lambda node: _materialize(TeamIndexMemoryEffectDefinition, node)
            ),
            "effect:team:reindex-memory": (
                lambda node: _materialize(TeamReindexMemoryEffectDefinition, node)
            ),
            "query:team:state": (lambda node: _materialize(TeamStateQueryDefinition, node)),
            "query:team:memory": (lambda node: _materialize(TeamMemoryQueryDefinition, node)),
            "query:team:session-routing": (
                lambda node: _materialize(TeamSessionRoutingQueryDefinition, node)
            ),
        },
        workflow_editor_catalog_entries=(
            *_TEAM_WORKFLOW_EDITOR_CATALOG,
            *context_catalog(_catalog_entry, _catalog_field),
        ),
        internal_event_catalog_entries=_TEAM_INTERNAL_EVENT_CATALOG,
    )
