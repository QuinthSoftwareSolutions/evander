from __future__ import annotations

from evander.code_index.providers import CodeIndexEffectProvider, CodeIndexQueryProvider

from evander.effects.providers.default_schema import effect_node_schema_resolvers
from evander.gates.providers.default_schema import gate_node_schema_resolvers
from evander.queries.providers.default_schema import query_node_schema_resolvers
from evander.triggers.providers.default_schema import trigger_node_schema_resolvers
from evander.effects.providers.ado_pipeline_run import AdoPipelineRunEffectProvider
from evander.effects.providers.ado_post_work_item_comment import (
    AdoPostWorkItemCommentEffectProvider,
)
from evander.effects.providers.ado_update_work_item import AdoUpdateWorkItemEffectProvider
from evander.effects.providers.ado_work_item_tag import (
    AdoAddWorkItemTagEffectProvider,
    AdoRemoveWorkItemTagEffectProvider,
)
from evander.effects.providers.agent_enqueue_prompt import AgentEnqueuePromptEffectProvider
from evander.effects.providers.agent_bootstrap_frontend import (
    AgentBootstrapFrontendEffectProvider,
)
from evander.effects.providers.agent_clone_repo import AgentCloneRepoEffectProvider
from evander.effects.providers.agent_prepare_reference_repositories import (
    AgentPrepareReferenceRepositoriesEffectProvider,
)
from evander.effects.providers.agent_repo_access import (
    AgentGrantRepoAccessEffectProvider,
    AgentRevokeRepoAccessEffectProvider,
)
from evander.effects.providers.agent_copy_files import AgentCopyFilesEffectProvider
from evander.effects.providers.agent_create import AgentCreateEffectProvider
from evander.effects.providers.agent_install_skills import AgentInstallSkillsEffectProvider
from evander.effects.providers.ado_build_wait import AdoBuildWaitEffectProvider
from evander.effects.providers.agent_destroy import AgentDestroyEffectProvider
from evander.effects.providers.artifacts import (
    ArtifactImportPublishedEffectProvider,
    ArtifactPublishArtifactsEffectProvider,
    ArtifactStageEffectProvider,
)
from evander.effects.providers.artifact_space import (
    ArtifactSpaceCreateEffectProvider,
    ArtifactSpacePublishVersionEffectProvider,
)
from evander.effects.providers.event_emit import EventEmitEffectProvider
from evander.effects.providers.event_schedule import EventScheduleEffectProvider
from evander.effects.providers.agent_exec import AgentExecEffectProvider
from evander.effects.providers.agent_prompt import AgentPromptEffectProvider
from evander.effects.providers.chat_assistant import ChatAssistantEnsureSessionEffectProvider
from evander.effects.providers.code_consultation import CodeConsultationEffectProvider
from evander.effects.providers.communication_send_message import (
    CommunicationSendMessageEffectProvider,
)
from evander.effects.providers.http_request import HttpRequestEffectProvider
from evander.effects.providers.git_promote_agent_branch import (
    GitPromoteAgentBranchEffectProvider,
)
from evander.effects.providers.git_ensure_managed_repo import (
    GitEnsureManagedRepoEffectProvider,
)
from evander.effects.providers.git_push_branch import GitPushBranchEffectProvider
from evander.effects.providers.issue_update_metadata import IssueUpdateMetadataEffectProvider
from evander.effects.providers.issue_update_state import IssueUpdateStateEffectProvider
from evander.effects.providers.issue_upsert_ado_proxy import (
    IssueUpsertAdoProxyEffectProvider,
)
from evander.effects.providers.notification_send import NotificationSendEffectProvider
from evander.effects.providers.pr_create import PrCreateEffectProvider
from evander.effects.providers.pull_request_update import (
    PullRequestAddTagEffectProvider,
    PullRequestRemoveTagEffectProvider,
    PullRequestUpdateEffectProvider,
)
from evander.effects.providers.pull_request_reply import PullRequestReplyEffectProvider
from evander.effects.providers.runtime_diagnostics_snapshot import (
    RuntimeDiagnosticsSnapshotEffectProvider,
)
from evander.effects.providers.semantic_delete import SemanticDeleteEffectProvider
from evander.effects.providers.semantic_ensure_collection import (
    SemanticEnsureCollectionEffectProvider,
)
from evander.effects.providers.semantic_upsert import SemanticUpsertEffectProvider
from evander.effects.providers.state_update import StateUpdateEffectProvider
from evander.effects.providers.session_update_metadata import (
    SessionUpdateMetadataEffectProvider,
)
from evander.effects.providers.session_set_image_generation_enabled import (
    SessionSetImageGenerationEnabledEffectProvider,
)
from evander.effects.providers.session_approve_mcp_tools import (
    SessionApproveMcpToolsForRunEffectProvider,
)
from evander.effects.providers.session_revoke_mcp_tool_approval_lease import (
    SessionRevokeMcpToolApprovalLeaseEffectProvider,
)
from evander.effects.providers.session_renew_if_expired import (
    SessionRenewIfExpiredEffectProvider,
)
from evander.effects.providers.vm_bootstrap_frontend import VmBootstrapFrontendEffectProvider
from evander.effects.providers.vm_bootstrap_python import VmBootstrapPythonEffectProvider
from evander.effects.providers.vm_copy_files import VmCopyFilesEffectProvider
from evander.effects.providers.vm_clone_repo import VmCloneRepoEffectProvider
from evander.effects.providers.vm_create import VmCreateEffectProvider
from evander.effects.providers.vm_delete_files import VmDeleteFilesEffectProvider
from evander.effects.providers.vm_exec import VmExecEffectProvider
from evander.effects.providers.vm_start import VmStartEffectProvider
from evander.effects.providers.vm_stop import VmStopEffectProvider
from evander.gates.providers.await_agent import AwaitAgentGateProvider
from evander.gates.providers.aggregate_gate import AggregateGateProvider
from evander.gates.providers.buffer_gate import BufferGateProvider
from evander.gates.providers.counter_gate import CounterGateProvider
from evander.gates.providers.debounce_gate import DebounceGateProvider
from evander.gates.providers.deserialize_gate import DeserializeGateProvider
from evander.gates.providers.dedupe_gate import DedupeGateProvider
from evander.gates.providers.filter_gate import FilterGateProvider
from evander.gates.providers.for_gate import ForGateProvider
from evander.gates.providers.map_gate import MapGateProvider
from evander.gates.providers.merge_gate import MergeGateProvider
from evander.gates.providers.serialize_gate import SerializeGateProvider
from evander.gates.providers.switch_gate import SwitchGateProvider
from evander.gates.providers.throttle_gate import ThrottleGateProvider
from evander.gates.providers.validate_gate import ValidateGateProvider
from evander.gates.providers.while_gate import WhileGateProvider
from evander.queries.providers.agent_artifact_text import AgentArtifactTextQueryProvider
from evander.queries.providers.artifact_space import (
    ArtifactSpaceLatestQueryProvider,
    ArtifactSpaceListQueryProvider,
)
from evander.queries.providers.ado_work_items import AdoWorkItemsQueryProvider
from evander.queries.providers.ado_builds import AdoBuildsQueryProvider
from evander.queries.providers.text_regex import TextRegexQueryProvider
from evander.queries.providers.communication_messages import (
    CommunicationMessagesQueryProvider,
)
from evander.queries.providers.git_merge_base import GitMergeBaseQueryProvider
from evander.queries.providers.git_pr_note import GitPrNoteQueryProvider
from evander.queries.providers.issue_get import IssueGetQueryProvider
from evander.queries.providers.issue_list import IssueListQueryProvider
from evander.queries.providers.model_usage_list import ModelUsageListQueryProvider
from evander.queries.providers.pull_request_feedback import (
    PullRequestFeedbackQueryProvider,
)
from evander.queries.providers.pull_request_get import PullRequestGetQueryProvider
from evander.queries.providers.pull_request_list import PullRequestListQueryProvider
from evander.queries.providers.pull_request_work_items import PullRequestWorkItemsQueryProvider
from evander.queries.providers.remote_issues import RemoteIssuesQueryProvider
from evander.queries.providers.semantic_get import SemanticGetQueryProvider
from evander.queries.providers.semantic_search import SemanticSearchQueryProvider
from evander.queries.providers.semantic_store_status import SemanticStoreStatusQueryProvider
from evander.queries.providers.state_get import StateGetQueryProvider
from evander.queries.providers.session_context import SessionContextQueryProvider
from evander.queries.providers.session_list import SessionListQueryProvider
from evander.queries.providers.vm_run_state import VmRunStateQueryProvider
from evander.triggers.providers.agent_prompt_new import AgentPromptNewTriggerProvider
from evander.triggers.providers.agent_artifact_staged import AgentArtifactStagedTriggerProvider
from evander.triggers.providers.agent_run_completed_event import (
    AgentRunCompletedEventTriggerProvider,
)
from evander.triggers.providers.ado_work_item_matching import (
    AdoWorkItemMatchingTriggerProvider,
)
from evander.triggers.providers.ado_work_item_comment_created import (
    AdoWorkItemCommentCreatedTriggerProvider,
)
from evander.triggers.providers.approval_pending_created import (
    ApprovalPendingCreatedTriggerProvider,
)
from evander.triggers.providers.communication_new_message import (
    CommunicationNewMessageTriggerProvider,
)
from evander.triggers.providers.cron import CronTriggerProvider
from evander.triggers.providers.ci_run_completed import CiRunCompletedTriggerProvider
from evander.triggers.providers.discord_new_message import DiscordNewMessageTriggerProvider
from evander.triggers.providers.email_new_message import EmailNewMessageTriggerProvider
from evander.triggers.providers.event import EventTriggerProvider
from evander.triggers.providers.git_push import GitPushTriggerProvider
from evander.triggers.providers.host_directory_file_added import (
    HostDirectoryFileAddedTriggerProvider,
)
from evander.triggers.providers.issue_comment_created import (
    IssueCommentCreatedTriggerProvider,
)
from evander.triggers.providers.pull_request_new_feedback import (
    PullRequestNewFeedbackTriggerProvider,
)
from evander.triggers.providers.pull_request_completed import (
    PullRequestCompletedTriggerProvider,
)
from evander.triggers.providers.remote_issue_comment_created import (
    RemoteIssueCommentCreatedTriggerProvider,
)
from evander.triggers.providers.remote_issue_matching import (
    RemoteIssueMatchingTriggerProvider,
)
from evander.triggers.providers.review_new_request import ReviewNewRequestTriggerProvider
from evander.triggers.providers.script_run_request import ScriptRunRequestTriggerProvider
from evander.triggers.providers.teams_direct_message import TeamsDirectMessageTriggerProvider
from evander.triggers.providers.teams_new_message import TeamsNewMessageTriggerProvider
from evander.triggers.providers.teams_meeting_new_transcript import (
    TeamsMeetingNewTranscriptTriggerProvider,
)
from evander.triggers.providers.ticket_changed import TicketChangedTriggerProvider
from evander.triggers.providers.ticket_created import TicketCreatedTriggerProvider
from evander.triggers.providers.webhook import WebhookTriggerProvider


def register_providers() -> dict[str, dict[str, type]]:
    return dict(
        trigger_providers={
            "trigger:event": EventTriggerProvider,
            "trigger:agent:artifact-staged": AgentArtifactStagedTriggerProvider,
            "trigger:cron": CronTriggerProvider,
            "trigger:ticket:created": TicketCreatedTriggerProvider,
            "trigger:ticket:changed": TicketChangedTriggerProvider,
            "trigger:issue:comment-created": IssueCommentCreatedTriggerProvider,
            "trigger:ado:work-item-matching": AdoWorkItemMatchingTriggerProvider,
            "trigger:ado:work-item-comment-created": AdoWorkItemCommentCreatedTriggerProvider,
            "trigger:remote-issue-matching": RemoteIssueMatchingTriggerProvider,
            "trigger:remote-issue-comment-created": RemoteIssueCommentCreatedTriggerProvider,
            "trigger:script-run-request": ScriptRunRequestTriggerProvider,
            "trigger:communication:new-message": CommunicationNewMessageTriggerProvider,
            "trigger:http:webhook": WebhookTriggerProvider,
            "trigger:discord:new-message": DiscordNewMessageTriggerProvider,
            "trigger:teams:new-message": TeamsNewMessageTriggerProvider,
            "trigger:teams:direct-message": TeamsDirectMessageTriggerProvider,
            "trigger:teams-meeting:new-transcript": TeamsMeetingNewTranscriptTriggerProvider,
            "trigger:email:new-message": EmailNewMessageTriggerProvider,
            "trigger:pull-request:new-feedback": PullRequestNewFeedbackTriggerProvider,
            "trigger:pull-request:completed": PullRequestCompletedTriggerProvider,
            "trigger:review:new-request": ReviewNewRequestTriggerProvider,
            "trigger:approval:pending-created": ApprovalPendingCreatedTriggerProvider,
            "trigger:git:push": GitPushTriggerProvider,
            "trigger:host-directory:file-added": HostDirectoryFileAddedTriggerProvider,
            "trigger:ci:run:completed": CiRunCompletedTriggerProvider,
            "trigger:agent:prompt:new": AgentPromptNewTriggerProvider,
            "trigger:agent:run:completed": AgentRunCompletedEventTriggerProvider,
        },
        gate_providers={
            "gate:aggregate": AggregateGateProvider,
            "gate:await-agent": AwaitAgentGateProvider,
            "gate:buffer": BufferGateProvider,
            "gate:counter": CounterGateProvider,
            "gate:debounce": DebounceGateProvider,
            "gate:deserialize": DeserializeGateProvider,
            "gate:dedupe": DedupeGateProvider,
            "gate:filter": FilterGateProvider,
            "gate:for": ForGateProvider,
            "gate:map": MapGateProvider,
            "gate:merge": MergeGateProvider,
            "gate:serialize": SerializeGateProvider,
            "gate:throttle": ThrottleGateProvider,
            "gate:switch": SwitchGateProvider,
            "gate:while": WhileGateProvider,
            "gate:validate": ValidateGateProvider,
        },
        effect_providers={
            "effect:code-index:ensure": CodeIndexEffectProvider,
            "effect:code-index:reconcile": CodeIndexEffectProvider,
            "effect:event:emit": EventEmitEffectProvider,
            "effect:event:schedule": EventScheduleEffectProvider,
            "effect:artifact-space:create": ArtifactSpaceCreateEffectProvider,
            "effect:artifact-space:publish-version": ArtifactSpacePublishVersionEffectProvider,
            "effect:vm:create": VmCreateEffectProvider,
            "effect:vm:clone-repo": VmCloneRepoEffectProvider,
            "effect:vm:bootstrap-python": VmBootstrapPythonEffectProvider,
            "effect:vm:bootstrap-frontend": VmBootstrapFrontendEffectProvider,
            "effect:vm:start": VmStartEffectProvider,
            "effect:vm:stop": VmStopEffectProvider,
            "effect:agent:create": AgentCreateEffectProvider,
            "effect:agent:destroy": AgentDestroyEffectProvider,
            "effect:agent:clone-repo": AgentCloneRepoEffectProvider,
            "effect:agent:prepare-reference-repositories": (
                AgentPrepareReferenceRepositoriesEffectProvider
            ),
            "effect:agent:grant-repo-access": AgentGrantRepoAccessEffectProvider,
            "effect:agent:revoke-repo-access": AgentRevokeRepoAccessEffectProvider,
            "effect:agent:bootstrap-frontend": AgentBootstrapFrontendEffectProvider,
            "effect:agent:copy-files": AgentCopyFilesEffectProvider,
            "effect:artifact:stage": ArtifactStageEffectProvider,
            "effect:artifact:publish-artifacts": ArtifactPublishArtifactsEffectProvider,
            "effect:artifact:import-published": ArtifactImportPublishedEffectProvider,
            "effect:agent:prompt": AgentPromptEffectProvider,
            "effect:agent:enqueue-prompt": AgentEnqueuePromptEffectProvider,
            "effect:chat-assistant:ensure-session": ChatAssistantEnsureSessionEffectProvider,
            "effect:agent:code-consultation": CodeConsultationEffectProvider,
            "effect:agent:exec": AgentExecEffectProvider,
            "effect:http:request": HttpRequestEffectProvider,
            "effect:runtime:diagnostics-snapshot": RuntimeDiagnosticsSnapshotEffectProvider,
            "effect:vm:exec": VmExecEffectProvider,
            "effect:vm:copy-files": VmCopyFilesEffectProvider,
            "effect:vm:delete-files": VmDeleteFilesEffectProvider,
            "effect:issue:update-state": IssueUpdateStateEffectProvider,
            "effect:issue:update-metadata": IssueUpdateMetadataEffectProvider,
            "effect:issue:upsert-ado-proxy": IssueUpsertAdoProxyEffectProvider,
            "effect:ado:pipeline-run": AdoPipelineRunEffectProvider,
            "effect:ado:build-wait": AdoBuildWaitEffectProvider,
            "effect:agent:install-skills": AgentInstallSkillsEffectProvider,
            "effect:ado:post-work-item-comment": AdoPostWorkItemCommentEffectProvider,
            "effect:ado:add-work-item-tag": AdoAddWorkItemTagEffectProvider,
            "effect:ado:remove-work-item-tag": AdoRemoveWorkItemTagEffectProvider,
            "effect:ado:update-work-item": AdoUpdateWorkItemEffectProvider,
            "effect:communication:send-message": CommunicationSendMessageEffectProvider,
            "effect:notification:send": NotificationSendEffectProvider,
            "effect:session:approve-mcp-tools-for-run": (
                SessionApproveMcpToolsForRunEffectProvider
            ),
            "effect:session:revoke-mcp-tool-approval-lease": (
                SessionRevokeMcpToolApprovalLeaseEffectProvider
            ),
            "effect:session:update-metadata": SessionUpdateMetadataEffectProvider,
            "effect:session:set-image-generation-enabled": (
                SessionSetImageGenerationEnabledEffectProvider
            ),
            "effect:session:renew-if-expired": SessionRenewIfExpiredEffectProvider,
            "effect:git:ensure-managed-repo": GitEnsureManagedRepoEffectProvider,
            "effect:git:promote-agent-branch": GitPromoteAgentBranchEffectProvider,
            "effect:git:push-branch": GitPushBranchEffectProvider,
            "effect:pr:create": PrCreateEffectProvider,
            "effect:pull-request:add-tag": PullRequestAddTagEffectProvider,
            "effect:pull-request:reply": PullRequestReplyEffectProvider,
            "effect:pull-request:remove-tag": PullRequestRemoveTagEffectProvider,
            "effect:pull-request:update": PullRequestUpdateEffectProvider,
            "effect:semantic:ensure-collection": SemanticEnsureCollectionEffectProvider,
            "effect:semantic:upsert": SemanticUpsertEffectProvider,
            "effect:semantic:delete": SemanticDeleteEffectProvider,
            "effect:state:update": StateUpdateEffectProvider,
        },
        query_providers={
            "query:code-index:status": CodeIndexQueryProvider,
            "query:code-index:search": CodeIndexQueryProvider,
            "query:code-index:navigate": CodeIndexQueryProvider,
            "query:vm:run-state": VmRunStateQueryProvider,
            "query:artifact-space:list": ArtifactSpaceListQueryProvider,
            "query:artifact-space:latest": ArtifactSpaceLatestQueryProvider,
            "query:git:merge-base": GitMergeBaseQueryProvider,
            "query:git:pr-note": GitPrNoteQueryProvider,
            "query:session-context": SessionContextQueryProvider,
            "query:session:list": SessionListQueryProvider,
            "query:model-usage:list": ModelUsageListQueryProvider,
            "query:agent:artifact-text": AgentArtifactTextQueryProvider,
            "query:issue:get": IssueGetQueryProvider,
            "query:issue:list": IssueListQueryProvider,
            "query:ado:work-items": AdoWorkItemsQueryProvider,
            "query:ado:builds": AdoBuildsQueryProvider,
            "query:text:regex": TextRegexQueryProvider,
            "query:remote-issues": RemoteIssuesQueryProvider,
            "query:pull-request:list": PullRequestListQueryProvider,
            "query:pull-request:feedback": PullRequestFeedbackQueryProvider,
            "query:communication:messages": CommunicationMessagesQueryProvider,
            "query:pull-request:get": PullRequestGetQueryProvider,
            "query:pull-request:work-items": PullRequestWorkItemsQueryProvider,
            "query:semantic:store-status": SemanticStoreStatusQueryProvider,
            "query:semantic:search": SemanticSearchQueryProvider,
            "query:semantic:get": SemanticGetQueryProvider,
            "query:state:get": StateGetQueryProvider,
        },
        node_definition_resolvers={
            **trigger_node_schema_resolvers(),
            **gate_node_schema_resolvers(),
            **effect_node_schema_resolvers(),
            **query_node_schema_resolvers(),
        },
    )
