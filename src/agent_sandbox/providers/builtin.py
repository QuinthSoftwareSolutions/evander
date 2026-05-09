from __future__ import annotations

from agent_sandbox.effects.providers.default_schema import effect_node_schema_resolvers
from agent_sandbox.gates.providers.default_schema import gate_node_schema_resolvers
from agent_sandbox.queries.providers.default_schema import query_node_schema_resolvers
from agent_sandbox.triggers.providers.default_schema import trigger_node_schema_resolvers
from agent_sandbox.effects.providers.agent_enqueue_prompt import AgentEnqueuePromptEffectProvider
from agent_sandbox.effects.providers.agent_bootstrap_frontend import (
    AgentBootstrapFrontendEffectProvider,
)
from agent_sandbox.effects.providers.agent_clone_repo import AgentCloneRepoEffectProvider
from agent_sandbox.effects.providers.agent_copy_files import AgentCopyFilesEffectProvider
from agent_sandbox.effects.providers.agent_create import AgentCreateEffectProvider
from agent_sandbox.effects.providers.agent_destroy import AgentDestroyEffectProvider
from agent_sandbox.effects.providers.event_emit import EventEmitEffectProvider
from agent_sandbox.effects.providers.agent_exec import AgentExecEffectProvider
from agent_sandbox.effects.providers.agent_prompt import AgentPromptEffectProvider
from agent_sandbox.effects.providers.bash_script import BashScriptEffectProvider
from agent_sandbox.effects.providers.chat_assistant import ChatAssistantEnsureSessionEffectProvider
from agent_sandbox.effects.providers.git_promote_agent_branch import (
    GitPromoteAgentBranchEffectProvider,
)
from agent_sandbox.effects.providers.issue_update_metadata import IssueUpdateMetadataEffectProvider
from agent_sandbox.effects.providers.issue_update_state import IssueUpdateStateEffectProvider
from agent_sandbox.effects.providers.notification_send import NotificationSendEffectProvider
from agent_sandbox.effects.providers.pr_create import PrCreateEffectProvider
from agent_sandbox.effects.providers.session_update_metadata import (
    SessionUpdateMetadataEffectProvider,
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
from agent_sandbox.gates.providers.dedupe_gate import DedupeGateProvider
from agent_sandbox.gates.providers.filter_gate import FilterGateProvider
from agent_sandbox.gates.providers.for_gate import ForGateProvider
from agent_sandbox.gates.providers.map_gate import MapGateProvider
from agent_sandbox.gates.providers.merge_gate import MergeGateProvider
from agent_sandbox.queries.providers.ado_work_items import AdoWorkItemsQueryProvider
from agent_sandbox.queries.providers.git_pr_note import GitPrNoteQueryProvider
from agent_sandbox.queries.providers.issue_get import IssueGetQueryProvider
from agent_sandbox.queries.providers.session_context import SessionContextQueryProvider
from agent_sandbox.queries.providers.vm_run_state import VmRunStateQueryProvider
from agent_sandbox.triggers.providers.agent_prompt_new import AgentPromptNewTriggerProvider
from agent_sandbox.triggers.providers.agent_run_completed import AgentRunCompletedTriggerProvider
from agent_sandbox.triggers.providers.cron import CronTriggerProvider
from agent_sandbox.triggers.providers.discord_new_message import DiscordNewMessageTriggerProvider
from agent_sandbox.triggers.providers.email_new_message import EmailNewMessageTriggerProvider
from agent_sandbox.triggers.providers.event import EventTriggerProvider
from agent_sandbox.triggers.providers.git_push import GitPushTriggerProvider
from agent_sandbox.triggers.providers.issue_comment_created import IssueCommentCreatedTriggerProvider
from agent_sandbox.triggers.providers.pull_request_new_feedback import (
    PullRequestNewFeedbackTriggerProvider,
)
from agent_sandbox.triggers.providers.review_new_request import ReviewNewRequestTriggerProvider
from agent_sandbox.triggers.providers.script_run_request import ScriptRunRequestTriggerProvider
from agent_sandbox.triggers.providers.teams_new_message import TeamsNewMessageTriggerProvider
from agent_sandbox.triggers.providers.teams_meeting_new_transcript import (
    TeamsMeetingNewTranscriptTriggerProvider,
)
from agent_sandbox.triggers.providers.ticket_changed import TicketChangedTriggerProvider
from agent_sandbox.triggers.providers.ticket_created import TicketCreatedTriggerProvider


def register_providers() -> dict[str, dict[str, type]]:
    return dict(
        trigger_providers={
            "trigger:event": EventTriggerProvider,
            "trigger:cron": CronTriggerProvider,
            "trigger:ticket:created": TicketCreatedTriggerProvider,
            "trigger:ticket:changed": TicketChangedTriggerProvider,
            "trigger:issue:comment-created": IssueCommentCreatedTriggerProvider,
            "trigger:issue:new-comment": IssueCommentCreatedTriggerProvider,
            "trigger:script-run-request": ScriptRunRequestTriggerProvider,
            "trigger:discord:new-message": DiscordNewMessageTriggerProvider,
            "trigger:teams:new-message": TeamsNewMessageTriggerProvider,
            "trigger:teams-meeting:new-transcript": TeamsMeetingNewTranscriptTriggerProvider,
            "trigger:email:new-message": EmailNewMessageTriggerProvider,
            "trigger:pull-request:new-feedback": PullRequestNewFeedbackTriggerProvider,
            "trigger:review:new-request": ReviewNewRequestTriggerProvider,
            "trigger:git:push": GitPushTriggerProvider,
            "trigger:agent:prompt:new": AgentPromptNewTriggerProvider,
            "trigger:agent:run:completed": AgentRunCompletedTriggerProvider,
        },
        gate_providers={
            "gate:await-agent": AwaitAgentGateProvider,
            "gate:debounce": DebounceGateProvider,
            "gate:dedupe": DedupeGateProvider,
            "gate:filter": FilterGateProvider,
            "gate:for": ForGateProvider,
            "gate:map": MapGateProvider,
            "gate:merge": MergeGateProvider,
        },
        effect_providers={
            "effect:event:emit": EventEmitEffectProvider,
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
            "effect:agent:prompt": AgentPromptEffectProvider,
            "effect:agent:enqueue-prompt": AgentEnqueuePromptEffectProvider,
            "effect:chat-assistant:ensure-session": ChatAssistantEnsureSessionEffectProvider,
            "effect:agent:exec": AgentExecEffectProvider,
            "effect:bash:script": BashScriptEffectProvider,
            "effect:vm:exec": VmExecEffectProvider,
            "effect:vm:copy-files": VmCopyFilesEffectProvider,
            "effect:vm:delete-files": VmDeleteFilesEffectProvider,
            "effect:issue:update-state": IssueUpdateStateEffectProvider,
            "effect:issue:update-metadata": IssueUpdateMetadataEffectProvider,
            "effect:notification:send": NotificationSendEffectProvider,
            "effect:session:update-metadata": SessionUpdateMetadataEffectProvider,
            "effect:git:promote-agent-branch": GitPromoteAgentBranchEffectProvider,
            "effect:pr:create": PrCreateEffectProvider,
        },
        query_providers={
            "query:vm:run-state": VmRunStateQueryProvider,
            "query:git:pr-note": GitPrNoteQueryProvider,
            "query:session-context": SessionContextQueryProvider,
            "query:issue:get": IssueGetQueryProvider,
            "query:ado:work-items": AdoWorkItemsQueryProvider,
        },
        node_definition_resolvers={
            **trigger_node_schema_resolvers(),
            **gate_node_schema_resolvers(),
            **effect_node_schema_resolvers(),
            **query_node_schema_resolvers(),
        },
    )
