from __future__ import annotations

from agent_sandbox.effects.providers.default_schema import effect_node_schema_resolvers
from agent_sandbox.gates.providers.default_schema import gate_node_schema_resolvers
from agent_sandbox.queries.providers.default_schema import query_node_schema_resolvers
from agent_sandbox.triggers.providers.default_schema import trigger_node_schema_resolvers
from agent_sandbox.effects.providers.ado_update_work_item import AdoUpdateWorkItemEffectProvider
from agent_sandbox.effects.providers.agent_enqueue_prompt import AgentEnqueuePromptEffectProvider
from agent_sandbox.effects.providers.agent_bootstrap_frontend import (
    AgentBootstrapFrontendEffectProvider,
)
from agent_sandbox.effects.providers.agent_clone_repo import AgentCloneRepoEffectProvider
from agent_sandbox.effects.providers.agent_copy_files import AgentCopyFilesEffectProvider
from agent_sandbox.effects.providers.agent_create import AgentCreateEffectProvider
from agent_sandbox.effects.providers.agent_destroy import AgentDestroyEffectProvider
from agent_sandbox.effects.providers.artifacts import (
    ArtifactPublishArtifactsEffectProvider,
    ArtifactStageEffectProvider,
)
from agent_sandbox.effects.providers.event_emit import EventEmitEffectProvider
from agent_sandbox.effects.providers.event_schedule import EventScheduleEffectProvider
from agent_sandbox.effects.providers.agent_exec import AgentExecEffectProvider
from agent_sandbox.effects.providers.agent_prompt import AgentPromptEffectProvider
from agent_sandbox.effects.providers.bash_script import BashScriptEffectProvider
from agent_sandbox.effects.providers.chat_assistant import ChatAssistantEnsureSessionEffectProvider
from agent_sandbox.effects.providers.communication_send_message import (
    CommunicationSendMessageEffectProvider,
)
from agent_sandbox.effects.providers.http_request import HttpRequestEffectProvider
from agent_sandbox.effects.providers.git_promote_agent_branch import (
    GitPromoteAgentBranchEffectProvider,
)
from agent_sandbox.effects.providers.issue_update_metadata import IssueUpdateMetadataEffectProvider
from agent_sandbox.effects.providers.issue_update_state import IssueUpdateStateEffectProvider
from agent_sandbox.effects.providers.issue_upsert_ado_proxy import (
    IssueUpsertAdoProxyEffectProvider,
)
from agent_sandbox.effects.providers.notification_send import NotificationSendEffectProvider
from agent_sandbox.effects.providers.pr_create import PrCreateEffectProvider
from agent_sandbox.effects.providers.pull_request_update import PullRequestUpdateEffectProvider
from agent_sandbox.effects.providers.semantic_delete import SemanticDeleteEffectProvider
from agent_sandbox.effects.providers.semantic_ensure_collection import (
    SemanticEnsureCollectionEffectProvider,
)
from agent_sandbox.effects.providers.semantic_upsert import SemanticUpsertEffectProvider
from agent_sandbox.effects.providers.state_update import StateUpdateEffectProvider
from agent_sandbox.effects.providers.session_update_metadata import (
    SessionUpdateMetadataEffectProvider,
)
from agent_sandbox.effects.providers.session_approve_mcp_tools import (
    SessionApproveMcpToolsForRunEffectProvider,
)
from agent_sandbox.effects.providers.session_renew_if_expired import (
    SessionRenewIfExpiredEffectProvider,
)
from agent_sandbox.effects.providers.vm_bootstrap_frontend import VmBootstrapFrontendEffectProvider
from agent_sandbox.effects.providers.vm_bootstrap_python import VmBootstrapPythonEffectProvider
from agent_sandbox.effects.providers.vm_copy_files import VmCopyFilesEffectProvider
from agent_sandbox.effects.providers.vm_clone_repo import VmCloneRepoEffectProvider
from agent_sandbox.effects.providers.vm_create import VmCreateEffectProvider
from agent_sandbox.effects.providers.vm_delete_files import VmDeleteFilesEffectProvider
from agent_sandbox.effects.providers.vm_exec import VmExecEffectProvider
from agent_sandbox.effects.providers.vm_start import VmStartEffectProvider
from agent_sandbox.effects.providers.vm_stop import VmStopEffectProvider
from agent_sandbox.gates.providers.await_agent import AwaitAgentGateProvider
from agent_sandbox.gates.providers.debounce_gate import DebounceGateProvider
from agent_sandbox.gates.providers.deserialize_gate import DeserializeGateProvider
from agent_sandbox.gates.providers.dedupe_gate import DedupeGateProvider
from agent_sandbox.gates.providers.filter_gate import FilterGateProvider
from agent_sandbox.gates.providers.for_gate import ForGateProvider
from agent_sandbox.gates.providers.map_gate import MapGateProvider
from agent_sandbox.gates.providers.merge_gate import MergeGateProvider
from agent_sandbox.gates.providers.serialize_gate import SerializeGateProvider
from agent_sandbox.queries.providers.agent_artifact_text import AgentArtifactTextQueryProvider
from agent_sandbox.queries.providers.ado_work_items import AdoWorkItemsQueryProvider
from agent_sandbox.queries.providers.git_pr_note import GitPrNoteQueryProvider
from agent_sandbox.queries.providers.issue_get import IssueGetQueryProvider
from agent_sandbox.queries.providers.pull_request_feedback import (
    PullRequestFeedbackQueryProvider,
)
from agent_sandbox.queries.providers.pull_request_list import PullRequestListQueryProvider
from agent_sandbox.queries.providers.semantic_get import SemanticGetQueryProvider
from agent_sandbox.queries.providers.semantic_search import SemanticSearchQueryProvider
from agent_sandbox.queries.providers.semantic_store_status import SemanticStoreStatusQueryProvider
from agent_sandbox.queries.providers.state_get import StateGetQueryProvider
from agent_sandbox.queries.providers.session_context import SessionContextQueryProvider
from agent_sandbox.queries.providers.session_list import SessionListQueryProvider
from agent_sandbox.queries.providers.vm_run_state import VmRunStateQueryProvider
from agent_sandbox.triggers.providers.agent_prompt_new import AgentPromptNewTriggerProvider
from agent_sandbox.triggers.providers.agent_run_completed import AgentRunCompletedTriggerProvider
from agent_sandbox.triggers.providers.ado_work_item_matching import (
    AdoWorkItemMatchingTriggerProvider,
)
from agent_sandbox.triggers.providers.communication_new_message import (
    CommunicationNewMessageTriggerProvider,
)
from agent_sandbox.triggers.providers.cron import CronTriggerProvider
from agent_sandbox.triggers.providers.discord_new_message import DiscordNewMessageTriggerProvider
from agent_sandbox.triggers.providers.email_new_message import EmailNewMessageTriggerProvider
from agent_sandbox.triggers.providers.event import EventTriggerProvider
from agent_sandbox.triggers.providers.git_push import GitPushTriggerProvider
from agent_sandbox.triggers.providers.issue_comment_created import IssueCommentCreatedTriggerProvider
from agent_sandbox.triggers.providers.pull_request_new_feedback import (
    PullRequestNewFeedbackTriggerProvider,
)
from agent_sandbox.triggers.providers.pull_request_completed import (
    PullRequestCompletedTriggerProvider,
)
from agent_sandbox.triggers.providers.review_new_request import ReviewNewRequestTriggerProvider
from agent_sandbox.triggers.providers.script_run_request import ScriptRunRequestTriggerProvider
from agent_sandbox.triggers.providers.teams_direct_message import TeamsDirectMessageTriggerProvider
from agent_sandbox.triggers.providers.teams_new_message import TeamsNewMessageTriggerProvider
from agent_sandbox.triggers.providers.teams_meeting_new_transcript import (
    TeamsMeetingNewTranscriptTriggerProvider,
)
from agent_sandbox.triggers.providers.ticket_changed import TicketChangedTriggerProvider
from agent_sandbox.triggers.providers.ticket_created import TicketCreatedTriggerProvider
from agent_sandbox.triggers.providers.webhook import WebhookTriggerProvider

def register_providers() -> dict[str, dict[str, type]]:
    return dict(
        trigger_providers={
            "trigger:event": EventTriggerProvider,
            "trigger:cron": CronTriggerProvider,
            "trigger:ticket:created": TicketCreatedTriggerProvider,
            "trigger:ticket:changed": TicketChangedTriggerProvider,
            "trigger:issue:comment-created": IssueCommentCreatedTriggerProvider,
            "trigger:ado:work-item-matching": AdoWorkItemMatchingTriggerProvider,
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
            "trigger:git:push": GitPushTriggerProvider,
            "trigger:agent:prompt:new": AgentPromptNewTriggerProvider,
            "trigger:agent:run:completed": AgentRunCompletedTriggerProvider,
        },
        gate_providers={
            "gate:await-agent": AwaitAgentGateProvider,
            "gate:debounce": DebounceGateProvider,
            "gate:deserialize": DeserializeGateProvider,
            "gate:dedupe": DedupeGateProvider,
            "gate:filter": FilterGateProvider,
            "gate:for": ForGateProvider,
            "gate:map": MapGateProvider,
            "gate:merge": MergeGateProvider,
            "gate:serialize": SerializeGateProvider,
        },
        effect_providers={
            "effect:event:emit": EventEmitEffectProvider,
            "effect:event:schedule": EventScheduleEffectProvider,
            "effect:vm:create": VmCreateEffectProvider,
            "effect:vm:clone-repo": VmCloneRepoEffectProvider,
            "effect:vm:bootstrap-python": VmBootstrapPythonEffectProvider,
            "effect:vm:bootstrap-frontend": VmBootstrapFrontendEffectProvider,
            "effect:vm:start": VmStartEffectProvider,
            "effect:vm:stop": VmStopEffectProvider,
            "effect:agent:create": AgentCreateEffectProvider,
            "effect:agent:destroy": AgentDestroyEffectProvider,
            "effect:agent:clone-repo": AgentCloneRepoEffectProvider,
            "effect:agent:bootstrap-frontend": AgentBootstrapFrontendEffectProvider,
            "effect:agent:copy-files": AgentCopyFilesEffectProvider,
            "effect:artifact:stage": ArtifactStageEffectProvider,
            "effect:artifact:publish-artifacts": ArtifactPublishArtifactsEffectProvider,
            "effect:agent:prompt": AgentPromptEffectProvider,
            "effect:agent:enqueue-prompt": AgentEnqueuePromptEffectProvider,
            "effect:chat-assistant:ensure-session": ChatAssistantEnsureSessionEffectProvider,
            "effect:agent:exec": AgentExecEffectProvider,
            "effect:bash:script": BashScriptEffectProvider,
            "effect:http:request": HttpRequestEffectProvider,
            "effect:vm:exec": VmExecEffectProvider,
            "effect:vm:copy-files": VmCopyFilesEffectProvider,
            "effect:vm:delete-files": VmDeleteFilesEffectProvider,
            "effect:issue:update-state": IssueUpdateStateEffectProvider,
            "effect:issue:update-metadata": IssueUpdateMetadataEffectProvider,
            "effect:issue:upsert-ado-proxy": IssueUpsertAdoProxyEffectProvider,
            "effect:ado:update-work-item": AdoUpdateWorkItemEffectProvider,
            "effect:communication:send-message": CommunicationSendMessageEffectProvider,
            "effect:notification:send": NotificationSendEffectProvider,
            "effect:session:approve-mcp-tools-for-run": (
                SessionApproveMcpToolsForRunEffectProvider
            ),
            "effect:session:update-metadata": SessionUpdateMetadataEffectProvider,
            "effect:session:renew-if-expired": SessionRenewIfExpiredEffectProvider,
            "effect:git:promote-agent-branch": GitPromoteAgentBranchEffectProvider,
            "effect:pr:create": PrCreateEffectProvider,
            "effect:pull-request:update": PullRequestUpdateEffectProvider,
            "effect:semantic:ensure-collection": SemanticEnsureCollectionEffectProvider,
            "effect:semantic:upsert": SemanticUpsertEffectProvider,
            "effect:semantic:delete": SemanticDeleteEffectProvider,
            "effect:state:update": StateUpdateEffectProvider,
        },
        query_providers={
            "query:vm:run-state": VmRunStateQueryProvider,
            "query:git:pr-note": GitPrNoteQueryProvider,
            "query:session-context": SessionContextQueryProvider,
            "query:session:list": SessionListQueryProvider,
            "query:agent:artifact-text": AgentArtifactTextQueryProvider,
            "query:issue:get": IssueGetQueryProvider,
            "query:ado:work-items": AdoWorkItemsQueryProvider,
            "query:pull-request:list": PullRequestListQueryProvider,
            "query:pull-request:feedback": PullRequestFeedbackQueryProvider,
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
