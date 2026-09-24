"""Team API entry point and compatibility imports for existing consumers.

Endpoint groups receive shared services from api_context; request helpers and
projections live in their owning domain modules.
"""

from __future__ import annotations

from collections.abc import Sequence

from starlette.routing import Route

from evander.addons import AddonContext
from evander.admin.web.rbac import ADMIN_OWNER_PERMISSION as ADMIN_OWNER_PERMISSION

from .api_authorization import (
    _add_team_chat_mutation_modes as _add_team_chat_mutation_modes,
)
from .api_authorization import (
    _authorized_external_source_evidence_snapshot_ids as _authorized_external_source_evidence_snapshot_ids,
)
from .api_authorization import (
    _can_read_external_source_evidence as _can_read_external_source_evidence,
)
from .api_authorization import (
    _can_read_human_review_request as _can_read_human_review_request,
)
from .api_authorization import (
    _can_read_release_human_review as _can_read_release_human_review,
)
from .api_authorization import (
    _can_view_all_team_chats as _can_view_all_team_chats,
)
from .api_authorization import (
    _chat_list_scope as _chat_list_scope,
)
from .api_authorization import (
    _chat_scope_for_operator as _chat_scope_for_operator,
)
from .api_authorization import (
    _chat_scope_payload as _chat_scope_payload,
)
from .api_authorization import (
    _conversation_agent_directory_path as _conversation_agent_directory_path,
)
from .api_authorization import (
    _current_human_review_decision_eligible_user_ids as _current_human_review_decision_eligible_user_ids,
)
from .api_authorization import (
    _external_source_evidence_project_directory_path as _external_source_evidence_project_directory_path,
)
from .api_authorization import (
    _filter_external_source_evidence_payload as _filter_external_source_evidence_payload,
)
from .api_authorization import (
    _human_review_is_assigned_to as _human_review_is_assigned_to,
)
from .api_authorization import (
    _is_current_configured_human_reviewer as _is_current_configured_human_reviewer,
)
from .api_authorization import (
    _operator_directory_options as _operator_directory_options,
)
from .api_authorization import (
    _operator_options as _operator_options,
)
from .api_authorization import (
    _reconcile_human_review_request_assignment as _reconcile_human_review_request_assignment,
)
from .api_authorization import (
    _reject_client_human_identity_fields as _reject_client_human_identity_fields,
)
from .api_authorization import (
    _release_human_review_directory as _release_human_review_directory,
)
from .api_authorization import (
    _require_conversation_chat_scope as _require_conversation_chat_scope,
)
from .api_authorization import (
    _require_human_review_decision_identity as _require_human_review_decision_identity,
)
from .api_authorization import (
    _require_human_review_owner_identity as _require_human_review_owner_identity,
)
from .api_authorization import (
    _resolved_release_policy_for_admin as _resolved_release_policy_for_admin,
)
from .api_authorization import (
    _strict_conversation_agent_directory_path as _strict_conversation_agent_directory_path,
)
from .api_authorization import (
    _strict_human_identity_from_request as _strict_human_identity_from_request,
)
from .api_authorization import (
    _team_chat_mutation_authorization as _team_chat_mutation_authorization,
)
from .api_authorization import (
    _team_chat_mutation_authorization_for_request as _team_chat_mutation_authorization_for_request,
)
from .api_authorization import (
    _validate_human_review_reassignment_targets as _validate_human_review_reassignment_targets,
)
from .api_chat import (
    _bounded_conversation_message_page as _bounded_conversation_message_page,
)
from .api_chat import (
    _chat_agent_is_eligible as _chat_agent_is_eligible,
)
from .api_chat import (
    _chat_agent_payload as _chat_agent_payload,
)
from .api_chat import (
    _chat_attachment_payload as _chat_attachment_payload,
)
from .api_chat import (
    _conversation_collection_payload as _conversation_collection_payload,
)
from .api_chat import (
    _conversation_message_page_fields as _conversation_message_page_fields,
)
from .api_chat import (
    _conversation_message_page_payload as _conversation_message_page_payload,
)
from .api_chat import (
    _conversation_message_payload as _conversation_message_payload,
)
from .api_chat import (
    _conversation_messages as _conversation_messages,
)
from .api_chat import (
    _conversation_messages_by_conversation as _conversation_messages_by_conversation,
)
from .api_chat import (
    _conversation_payload as _conversation_payload,
)
from .api_chat import (
    _conversation_snapshot_events as _conversation_snapshot_events,
)
from .api_chat import (
    _conversation_snapshot_payload as _conversation_snapshot_payload,
)
from .api_chat import (
    _conversation_snapshot_payload_from_state as _conversation_snapshot_payload_from_state,
)
from .api_chat import (
    _conversation_snapshot_state_and_payload as _conversation_snapshot_state_and_payload,
)
from .api_chat import (
    _conversation_stream_state as _conversation_stream_state,
)
from .api_chat import (
    _reconciled_chat_state as _reconciled_chat_state,
)
from .api_constants import (
    _BOOTSTRAP_COLLECTIONS as _BOOTSTRAP_COLLECTIONS,
)
from .api_constants import (
    _CHAT_VIEW_COLLECTIONS as _CHAT_VIEW_COLLECTIONS,
)
from .api_constants import (
    _COST_COLLECTIONS as _COST_COLLECTIONS,
)
from .api_constants import (
    _VIEW_RUNTIME_COLLECTIONS as _VIEW_RUNTIME_COLLECTIONS,
)
from .api_constants import (
    DEFAULT_CHAT_MESSAGE_LIMIT as DEFAULT_CHAT_MESSAGE_LIMIT,
)
from .api_constants import (
    DEFAULT_INBOX_HISTORY_LIMIT as DEFAULT_INBOX_HISTORY_LIMIT,
)
from .api_constants import (
    DEFAULT_STATE_HISTORY_LIMIT as DEFAULT_STATE_HISTORY_LIMIT,
)
from .api_constants import (
    DEFAULT_TIMELINE_LIMIT as DEFAULT_TIMELINE_LIMIT,
)
from .api_constants import (
    MAX_CHAT_MESSAGE_LIMIT as MAX_CHAT_MESSAGE_LIMIT,
)
from .api_constants import (
    MAX_INBOX_HISTORY_LIMIT as MAX_INBOX_HISTORY_LIMIT,
)
from .api_constants import (
    MAX_STATE_HISTORY_LIMIT as MAX_STATE_HISTORY_LIMIT,
)
from .api_constants import (
    MAX_TIMELINE_LIMIT as MAX_TIMELINE_LIMIT,
)
from .api_constants import (
    TEAM_RESOURCE_TOKEN_COLLECTIONS as TEAM_RESOURCE_TOKEN_COLLECTIONS,
)
from .api_constants import (
    TEAM_STATE_COLLECTION_KEYS as TEAM_STATE_COLLECTION_KEYS,
)
from .api_constants import (
    TEAM_STATE_HISTORY_COLLECTIONS as TEAM_STATE_HISTORY_COLLECTIONS,
)
from .api_constants import (
    TEAM_VIEW_COLLECTIONS as TEAM_VIEW_COLLECTIONS,
)
from .api_constants import (
    TEAM_VIEW_IDS as TEAM_VIEW_IDS,
)
from .api_constants import (
    JSONResponse as JSONResponse,
)
from .api_constants import (
    TeamJSONResponse as TeamJSONResponse,
)
from .api_constants import (
    _view_read_collections as _view_read_collections,
)
from .api_context import build_api_dependencies
from .api_projection import (
    _add_context_reset_usage as _add_context_reset_usage,
)
from .api_projection import (
    _add_release_outbox_metadata as _add_release_outbox_metadata,
)
from .api_projection import (
    _bootstrap_payload_for_request as _bootstrap_payload_for_request,
)
from .api_projection import (
    _filter_state_payload as _filter_state_payload,
)
from .api_projection import (
    _inbox_history_agent_payload as _inbox_history_agent_payload,
)
from .api_projection import (
    _inbox_history_item_payload as _inbox_history_item_payload,
)
from .api_projection import (
    _inbox_history_payload_for_request as _inbox_history_payload_for_request,
)
from .api_projection import (
    _inbox_history_search_text as _inbox_history_search_text,
)
from .api_projection import (
    _inbox_history_sort_key as _inbox_history_sort_key,
)
from .api_projection import (
    _latest_task_preparation_invalidation_ids as _latest_task_preparation_invalidation_ids,
)
from .api_projection import (
    _record_state_value as _record_state_value,
)
from .api_projection import (
    _registered_communication_target_options as _registered_communication_target_options,
)
from .api_projection import (
    _session_records_for_agent_ids as _session_records_for_agent_ids,
)
from .api_projection import (
    _state_collection_slice_payload as _state_collection_slice_payload,
)
from .api_projection import (
    _state_history_item_is_current as _state_history_item_is_current,
)
from .api_projection import (
    _state_history_keep_ids as _state_history_keep_ids,
)
from .api_projection import (
    _state_history_sort_key as _state_history_sort_key,
)
from .api_projection import (
    _state_slice_agent_ids as _state_slice_agent_ids,
)
from .api_projection import (
    _state_slice_payload_for_request as _state_slice_payload_for_request,
)
from .api_projection import (
    _task_board_case_task_ids as _task_board_case_task_ids,
)
from .api_projection import (
    _task_board_reference_task_ids as _task_board_reference_task_ids,
)
from .api_projection import (
    _task_board_selected_ids_by_collection as _task_board_selected_ids_by_collection,
)
from .api_projection import (
    _team_costs_payload as _team_costs_payload,
)
from .api_projection import (
    _team_state_payload_for_request as _team_state_payload_for_request,
)
from .api_projection import (
    _timeline_payload_for_request as _timeline_payload_for_request,
)
from .api_projection import (
    _view_ids_by_collection as _view_ids_by_collection,
)
from .api_projection import (
    _view_payload_for_request as _view_payload_for_request,
)
from .api_projection import (
    _window_state_history_payload as _window_state_history_payload,
)
from .api_release import (
    HUMAN_REVIEW_BUNDLE_MANIFEST_PATH as HUMAN_REVIEW_BUNDLE_MANIFEST_PATH,
)
from .api_release import (
    _append_human_review_bundle_manifest as _append_human_review_bundle_manifest,
)
from .api_release import (
    _delete_temporary_file as _delete_temporary_file,
)
from .api_release import (
    _human_review_bundle_filename as _human_review_bundle_filename,
)
from .api_release import (
    _human_review_bundle_manifest as _human_review_bundle_manifest,
)
from .api_release import (
    _human_review_bundle_member_reserves_manifest as _human_review_bundle_member_reserves_manifest,
)
from .api_release import (
    _release_candidate_checkout_metadata as _release_candidate_checkout_metadata,
)
from .api_release import (
    _release_verification_live_log_limit as _release_verification_live_log_limit,
)
from .api_release import (
    _release_verification_live_payload as _release_verification_live_payload,
)
from .api_requests import (
    _agent_display_name as _agent_display_name,
)
from .api_requests import (
    _agent_execution_records_by_session as _agent_execution_records_by_session,
)
from .api_requests import (
    _auth_can as _auth_can,
)
from .api_requests import (
    _coerce_int as _coerce_int,
)
from .api_requests import (
    _current_agent_avatar_asset_id as _current_agent_avatar_asset_id,
)
from .api_requests import (
    _datetime_text as _datetime_text,
)
from .api_requests import (
    _enum_value as _enum_value,
)
from .api_requests import (
    _expected_revision as _expected_revision,
)
from .api_requests import (
    _idle_context_rotation_outbox_publication_extra as _idle_context_rotation_outbox_publication_extra,
)
from .api_requests import (
    _limit_text as _limit_text,
)
from .api_requests import (
    _memory_artifact_refs as _memory_artifact_refs,
)
from .api_requests import (
    _mutation_request_id as _mutation_request_id,
)
from .api_requests import (
    _mutation_view_response_payload as _mutation_view_response_payload,
)
from .api_requests import (
    _normalize_operator_user_id as _normalize_operator_user_id,
)
from .api_requests import (
    _operator_actor_from_request as _operator_actor_from_request,
)
from .api_requests import (
    _operator_display_name as _operator_display_name,
)
from .api_requests import (
    _operator_identity_from_request as _operator_identity_from_request,
)
from .api_requests import (
    _optional as _optional,
)
from .api_requests import (
    _optional_bool as _optional_bool,
)
from .api_requests import (
    _optional_event_datetime as _optional_event_datetime,
)
from .api_requests import (
    _optional_event_float as _optional_event_float,
)
from .api_requests import (
    _optional_event_text as _optional_event_text,
)
from .api_requests import (
    _optional_git_config as _optional_git_config,
)
from .api_requests import (
    _optional_int as _optional_int,
)
from .api_requests import (
    _optional_iso_datetime as _optional_iso_datetime,
)
from .api_requests import (
    _optional_list as _optional_list,
)
from .api_requests import (
    _optional_mapping_list as _optional_mapping_list,
)
from .api_requests import (
    _optional_object as _optional_object,
)
from .api_requests import (
    _optional_present_text as _optional_present_text,
)
from .api_requests import (
    _optional_release_policy as _optional_release_policy,
)
from .api_requests import (
    _optional_string_list as _optional_string_list,
)
from .api_requests import (
    _privileged_operations_locked_response as _privileged_operations_locked_response,
)
from .api_requests import (
    _publish_team_events as _publish_team_events,
)
from .api_requests import (
    _query_bool as _query_bool,
)
from .api_requests import (
    _query_chat_message_limit as _query_chat_message_limit,
)
from .api_requests import (
    _query_limited_int as _query_limited_int,
)
from .api_requests import (
    _query_non_negative_int as _query_non_negative_int,
)
from .api_requests import (
    _query_state_history_limit as _query_state_history_limit,
)
from .api_requests import (
    _query_text as _query_text,
)
from .api_requests import (
    _query_values as _query_values,
)
from .api_requests import (
    _read_optional_payload as _read_optional_payload,
)
from .api_requests import (
    _read_payload as _read_payload,
)
from .api_requests import (
    _request_has_unlocked_privileged_operations as _request_has_unlocked_privileged_operations,
)
from .api_requests import (
    _request_wants_view_scoped_response as _request_wants_view_scoped_response,
)
from .api_requests import (
    _required as _required,
)
from .api_requests import (
    _required_event_text as _required_event_text,
)
from .api_requests import (
    _required_int as _required_int,
)
from .api_requests import (
    _required_iso_datetime as _required_iso_datetime,
)
from .api_requests import (
    _route_requires_privileged_operations as _route_requires_privileged_operations,
)
from .api_requests import (
    _runtime_from_context as _runtime_from_context,
)
from .api_requests import (
    _session_from_request as _session_from_request,
)
from .api_requests import (
    _session_repository_from_context as _session_repository_from_context,
)
from .api_requests import (
    _state_revision_payload as _state_revision_payload,
)
from .api_requests import (
    _state_snapshot_events as _state_snapshot_events,
)
from .api_requests import (
    _with_errors as _with_errors,
)
from .api_requests import (
    _with_privileged_operations as _with_privileged_operations,
)
from .api_revision import (
    _add_team_copy_configuration_tokens as _add_team_copy_configuration_tokens,
)
from .services.admin_service import (
    team_copy_configuration_token as team_copy_configuration_token,
)
from .api_revision import (
    _add_team_resource_tokens as _add_team_resource_tokens,
)
from .api_revision import (
    _add_team_resource_tokens_for_collections as _add_team_resource_tokens_for_collections,
)
from .api_revision import (
    _can_rebase_ui_mutation as _can_rebase_ui_mutation,
)
from .api_revision import (
    _team_resource_token as _team_resource_token,
)
from .api_revision import (
    _team_resource_token_for as _team_resource_token_for,
)
from .api_revision import (
    _team_resource_tokens as _team_resource_tokens,
)
from .api_revision import (
    _team_resource_tokens_for_collections as _team_resource_tokens_for_collections,
)
from .api_revision import (
    _team_resource_value_for_token as _team_resource_value_for_token,
)
from .api_revision import (
    _team_state_collection as _team_state_collection,
)
from .api_revision import (
    _trim_resource_tokens as _trim_resource_tokens,
)
from .api_routes import build_routes
from .api_voice import (
    _agent_voice_effective_prompt as _agent_voice_effective_prompt,
)
from .api_voice import (
    _agent_voice_profile_payload as _agent_voice_profile_payload,
)
from .api_voice import (
    _agent_voice_prompt_metadata as _agent_voice_prompt_metadata,
)
from .api_voice import (
    _browser_voice_reconciliation_context as _browser_voice_reconciliation_context,
)
from .api_voice import (
    _browser_voice_reconciliation_transcript as _browser_voice_reconciliation_transcript,
)
from .api_voice import (
    _browser_voice_transcript_speaker_label as _browser_voice_transcript_speaker_label,
)
from .api_voice import (
    _build_agent_voice_projection as _build_agent_voice_projection,
)
from .api_voice import (
    _realtime_calls_url as _realtime_calls_url,
)
from .api_voice import (
    _record_chat_voice_call_status as _record_chat_voice_call_status,
)
from .api_voice import (
    _require_browser_voice_session_operator as _require_browser_voice_session_operator,
)
from .api_voice import (
    _session_is_expired as _session_is_expired,
)
from .api_voice import (
    _team_voice_session_payload as _team_voice_session_payload,
)
from .api_voice import (
    _voice_participant_label as _voice_participant_label,
)
from .api_voice import (
    _voice_participant_text as _voice_participant_text,
)
from .costs import TeamCostService as TeamCostService
from .models import team_state_payload as team_state_payload
from .outbox_publication import request_team_outbox_publication

_request_team_outbox_publication = request_team_outbox_publication


def admin_api_routes(context: AddonContext) -> Sequence[Route]:
    return build_routes(build_api_dependencies(context))
