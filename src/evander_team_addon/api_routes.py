"""Ordered Team API route registration.

Keep the route order and privilege overrides here. Domain handler factories
receive the same services and response helpers from api_context."""

from __future__ import annotations

from collections.abc import (
    Sequence,
)

from starlette.routing import (
    Route,
)

from .api_context import TeamApiDependencies
from .api_routes_avatars import build_avatars_handlers
from .api_routes_chat import build_chat_handlers
from .api_routes_evidence import build_evidence_handlers
from .api_routes_human_reviews import build_human_reviews_handlers
from .api_routes_memory import build_memory_handlers
from .api_routes_organization import build_organization_handlers
from .api_routes_people import build_people_handlers
from .api_routes_reads import build_reads_handlers
from .api_routes_releases import build_releases_handlers
from .api_routes_reviews import build_reviews_handlers
from .api_routes_tasks import build_tasks_handlers
from .api_routes_voice import build_voice_handlers
from .case_context_api import case_context_routes


def build_routes(dependencies: TeamApiDependencies) -> Sequence[Route]:
    context = dependencies.context
    repository = dependencies.repository
    route = dependencies.route
    handlers = {
        **build_avatars_handlers(dependencies),
        **build_chat_handlers(dependencies),
        **build_evidence_handlers(dependencies),
        **build_human_reviews_handlers(dependencies),
        **build_memory_handlers(dependencies),
        **build_organization_handlers(dependencies),
        **build_people_handlers(dependencies),
        **build_reads_handlers(dependencies),
        **build_releases_handlers(dependencies),
        **build_reviews_handlers(dependencies),
        **build_tasks_handlers(dependencies),
        **build_voice_handlers(dependencies),
    }
    return [
        *case_context_routes(context, repository, route),
        route("/bootstrap", handlers["get_bootstrap"], methods=["GET"]),
        route("/views/{view}", handlers["get_view"], methods=["GET"]),
        route("/state", handlers["get_state"], methods=["GET"]),
        route("/state/stream", handlers["stream_state"], methods=["GET"]),
        route("/inbox", handlers["get_inbox_history"], methods=["GET"]),
        route("/dispatch-events", handlers["get_dispatch_events"], methods=["GET"]),
        route("/templates", handlers["list_templates"], methods=["GET"]),
        route("/templates/{template_id}", handlers["get_template_details"], methods=["GET"]),
        route(
            "/templates/{template_id}/materialize",
            handlers["materialize_template"],
            methods=["POST"],
        ),
        route("/timeline", handlers["get_timeline"], methods=["GET"]),
        route("/costs", handlers["get_costs"], methods=["GET"]),
        route("/costs/backfill", handlers["backfill_costs"], methods=["POST"]),
        route("/teams", handlers["create_team"], methods=["POST"]),
        route("/teams/{team_id}/copy", handlers["copy_team"], methods=["POST"]),
        route("/teams/{team_id}", handlers["patch_team"], methods=["PATCH"]),
        route(
            "/teams/{team_id}/cost-budget/reset",
            handlers["reset_team_cost_budget"],
            methods=["POST"],
        ),
        route("/governance-groups", handlers["create_governance_group"], methods=["POST"]),
        route(
            "/governance-groups/{governance_group_id}",
            handlers["patch_governance_group"],
            methods=["PATCH"],
        ),
        route("/governance-agenda", handlers["create_governance_agenda_item"], methods=["POST"]),
        route(
            "/governance-agenda/{agenda_item_id}/handle",
            handlers["handle_governance_agenda_item"],
            methods=["POST"],
        ),
        route("/projects", handlers["create_project"], methods=["POST"]),
        route("/projects/git/import", handlers["import_project_git_repo"], methods=["POST"]),
        route("/projects/{project_id}/lead", handlers["transfer_project_lead"], methods=["POST"]),
        route("/projects/{project_id}", handlers["patch_project"], methods=["PATCH"]),
        route("/roles", handlers["create_role"], methods=["POST"]),
        route("/roles", handlers["list_roles"], methods=["GET"]),
        route("/roles/{role_id}", handlers["get_role"], methods=["GET"]),
        route("/roles/{role_id}", handlers["patch_role"], methods=["PATCH"]),
        route("/conversations", handlers["list_conversations"], methods=["GET"]),
        route(
            "/conversations",
            handlers["create_conversation"],
            methods=["POST"],
            require_privileged=False,
        ),
        route("/conversations/{conversation_id}", handlers["get_conversation"], methods=["GET"]),
        route(
            "/conversations/{conversation_id}/stream",
            handlers["stream_conversation"],
            methods=["GET"],
        ),
        route(
            "/conversations/{conversation_id}/messages",
            handlers["list_conversation_messages"],
            methods=["GET"],
        ),
        route(
            "/conversations/{conversation_id}/messages",
            handlers["send_conversation_message"],
            methods=["POST"],
            require_privileged=False,
        ),
        route(
            "/conversations/{conversation_id}/attachments/{attachment_id}",
            handlers["get_chat_attachment"],
            methods=["GET"],
        ),
        route("/chat/attachment-policy", handlers["get_chat_attachment_policy"], methods=["GET"]),
        route("/chat/attachment-policy", handlers["put_chat_attachment_policy"], methods=["PUT"]),
        route("/agents", handlers["register_agent"], methods=["POST"]),
        route("/agents/{agent_id}", handlers["patch_agent"], methods=["PATCH"]),
        route(
            "/agents/{agent_id}/voice-sessions",
            handlers["create_agent_voice_session"],
            methods=["POST"],
        ),
        route(
            "/voice-sessions/{voice_session_id}/events",
            handlers["record_agent_voice_event"],
            methods=["POST"],
        ),
        route(
            "/voice-sessions/{voice_session_id}/end",
            handlers["end_agent_voice_session"],
            methods=["POST"],
        ),
        route(
            "/voice-session-requests/{request_id}/end",
            handlers["end_agent_voice_session_by_request"],
            methods=["POST"],
        ),
        route(
            "/voice-sessions/{voice_session_id}/queries",
            handlers["create_agent_voice_query"],
            methods=["POST"],
        ),
        route(
            "/voice-sessions/{voice_session_id}/queries/{query_id}",
            handlers["get_agent_voice_query"],
            methods=["GET"],
        ),
        route(
            "/voice-sessions/{voice_session_id}/queries/{query_id}/timeout",
            handlers["timeout_agent_voice_query"],
            methods=["POST"],
        ),
        route(
            "/agents/{agent_id}/avatar/previews",
            handlers["create_avatar_preview"],
            methods=["POST"],
        ),
        route(
            "/agents/{agent_id}/avatar/upload", handlers["upload_agent_avatar"], methods=["POST"]
        ),
        route(
            "/agents/{agent_id}/avatar/accept-preview",
            handlers["accept_agent_avatar_preview"],
            methods=["POST"],
        ),
        route("/agents/{agent_id}/avatar", handlers["delete_agent_avatar"], methods=["DELETE"]),
        route("/avatars/{asset_id}", handlers["get_avatar_asset"], methods=["GET"]),
        route("/avatar-previews/{preview_id}", handlers["get_avatar_preview"], methods=["GET"]),
        route(
            "/agents/{agent_id}/context-rotation",
            handlers["request_agent_context_rotation"],
            methods=["POST"],
        ),
        route("/agents/{agent_id}/stand-down", handlers["stand_down_agent"], methods=["POST"]),
        route(
            "/agents/{agent_id}/stand-down/revoke",
            handlers["revoke_agent_stand_down"],
            methods=["POST"],
        ),
        route("/cases", handlers["create_case"], methods=["POST"]),
        route("/cases/{case_id}", handlers["patch_case"], methods=["PATCH"]),
        route("/tasks", handlers["create_task"], methods=["POST"]),
        route("/tasks/{task_id}", handlers["patch_task"], methods=["PATCH"]),
        route("/tasks/{task_id}/repair-state", handlers["repair_task_state"], methods=["POST"]),
        route("/tasks/{task_id}/cancel", handlers["cancel_task"], methods=["POST"]),
        route(
            "/tasks/{task_id}/repair-cancelled-dependencies",
            handlers["repair_cancelled_task_dependencies"],
            methods=["POST"],
        ),
        route(
            "/tasks/{task_id}/recover-submitted-integration-boundary",
            handlers["recover_submitted_integration_boundary"],
            methods=["POST"],
        ),
        route("/deliverables", handlers["record_deliverable"], methods=["POST"]),
        route(
            "/deliverables/{deliverable_id}/acceptance-verification-waiver",
            handlers["authorize_deliverable_acceptance_verification_waiver"],
            methods=["POST"],
        ),
        route("/review-requests", handlers["record_review_request"], methods=["POST"]),
        route(
            "/review-requests/{review_request_id}/decision",
            handlers["decide_review_request"],
            methods=["POST"],
        ),
        route(
            "/review-requests/{review_request_id}/withdraw",
            handlers["withdraw_review_request"],
            methods=["POST"],
        ),
        route(
            "/review-requests/{review_request_id}/retire",
            handlers["retire_review_request"],
            methods=["POST"],
        ),
        route("/operator-directives", handlers["create_operator_directive"], methods=["POST"]),
        route(
            "/external-source-evidence-snapshots",
            handlers["list_external_source_evidence_snapshots"],
            methods=["GET"],
        ),
        route(
            "/external-source-evidence-snapshots",
            handlers["create_external_source_evidence_snapshot"],
            methods=["POST"],
        ),
        route(
            "/external-source-evidence-snapshots/{snapshot_id}",
            handlers["get_external_source_evidence_snapshot"],
            methods=["GET"],
        ),
        route(
            "/external-source-evidence-snapshots/{snapshot_id}/attachments",
            handlers["attach_external_source_evidence_snapshot"],
            methods=["POST"],
        ),
        route("/memory", handlers["list_memories"], methods=["GET"]),
        route("/memory", handlers["create_memory"], methods=["POST"]),
        route("/memory/{memory_id}", handlers["get_memory"], methods=["GET"]),
        route("/memory/{memory_id}", handlers["patch_memory"], methods=["PATCH"]),
        route("/memory/{memory_id}/archive", handlers["archive_memory"], methods=["POST"]),
        route(
            "/action-records/{action_record_id}/dismiss",
            handlers["dismiss_action_record"],
            methods=["POST"],
        ),
        route(
            "/blockers/{blocker_id}/resolve", handlers["resolve_terminal_blocker"], methods=["POST"]
        ),
        route(
            "/blockers/{blocker_id}/dependency-composition-recovery",
            handlers["start_dependency_composition_recovery"],
            methods=["POST"],
        ),
        route(
            "/candidate-correction-recoveries/{blocker_id}",
            handlers["recover_candidate_correction_successor"],
            methods=["POST"],
        ),
        route("/outcome-kinds", handlers["create_outcome_kind_definition"], methods=["POST"]),
        route("/outcome-kinds", handlers["list_outcome_kind_definitions"], methods=["GET"]),
        route(
            "/outcome-kinds/{definition_id}",
            handlers["get_outcome_kind_definition"],
            methods=["GET"],
        ),
        route(
            "/outcome-kinds/{definition_id}",
            handlers["patch_outcome_kind_definition"],
            methods=["PATCH"],
        ),
        route("/review-profiles", handlers["create_review_profile"], methods=["POST"]),
        route("/review-profiles", handlers["list_review_profiles"], methods=["GET"]),
        route(
            "/review-profiles/{review_profile_id}", handlers["get_review_profile"], methods=["GET"]
        ),
        route(
            "/review-profiles/{review_profile_id}",
            handlers["patch_review_profile"],
            methods=["PATCH"],
        ),
        route("/release-packages", handlers["create_release_package"], methods=["POST"]),
        route("/release-packages", handlers["list_release_packages"], methods=["GET"]),
        route("/human-review-requests", handlers["list_human_review_requests"], methods=["GET"]),
        route(
            "/human-review-requests/{human_review_request_id}",
            handlers["get_human_review_request"],
            methods=["GET"],
        ),
        route(
            "/human-review-requests/{human_review_request_id}/review-bundle",
            handlers["download_human_review_bundle"],
            methods=["GET"],
        ),
        route(
            "/human-review-requests/{human_review_request_id}/decisions",
            handlers["decide_human_review"],
            methods=["POST"],
        ),
        route(
            "/human-review-requests/{human_review_request_id}/reassign",
            handlers["reassign_human_review_request"],
            methods=["POST"],
        ),
        route(
            "/human-review-requests/{human_review_request_id}/resume",
            handlers["resume_human_review_request"],
            methods=["POST"],
        ),
        route(
            "/human-review-requests/{human_review_request_id}/preparation-result",
            handlers["record_human_review_preparation"],
            methods=["POST"],
        ),
        route(
            "/human-review-findings/{human_review_finding_id}/resolution-proposal",
            handlers["propose_human_finding_resolution"],
            methods=["POST"],
        ),
        route(
            "/human-review-findings/{human_review_finding_id}/route",
            handlers["route_human_finding_correction"],
            methods=["POST"],
        ),
        route(
            "/cases/{case_id}/close-post-release",
            handlers["close_post_release_feedback_case"],
            methods=["POST"],
        ),
        route(
            "/release-packages/{release_package_id}/report-issue",
            handlers["report_release_issue"],
            methods=["POST"],
        ),
        route(
            "/release-packages/{release_package_id}/retry-gate",
            handlers["retry_release_gate"],
            methods=["POST"],
        ),
        route(
            "/release-packages/{release_package_id}/cancel",
            handlers["cancel_release_package"],
            methods=["POST"],
        ),
        route(
            "/release-packages/{release_package_id}/gate-obligation-waivers",
            handlers["authorize_release_gate_obligation_waiver"],
            methods=["POST"],
        ),
        route(
            "/worktree-cleanup-candidates/{cleanup_candidate_id}/retry",
            handlers["retry_worktree_cleanup"],
            methods=["POST"],
        ),
        route(
            "/release-packages/{release_package_id}/verification-live",
            handlers["get_release_verification_live"],
            methods=["GET"],
        ),
        route(
            "/release-packages/{release_package_id}",
            handlers["get_release_package"],
            methods=["GET"],
        ),
        route("/git-promotions", handlers["list_git_promotions"], methods=["GET"]),
        route("/git-promotions/{promotion_id}", handlers["get_git_promotion"], methods=["GET"]),
    ]
