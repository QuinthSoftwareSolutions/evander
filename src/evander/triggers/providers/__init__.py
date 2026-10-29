from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "CiRunCompletedTriggerProvider",
    "AdoWorkItemCommentCreatedTriggerProvider",
    "AgentRunCompletedEventTriggerProvider",
    "AgentArtifactStagedTriggerProvider",
    "ApprovalPendingCreatedTriggerProvider",
    "CronTriggerProvider",
    "CommunicationNewMessageTriggerProvider",
    "DiscordNewMessageTriggerProvider",
    "EmailNewMessageTriggerProvider",
    "GitPushTriggerProvider",
    "IssueCommentCreatedTriggerProvider",
    "PullRequestCompletedTriggerProvider",
    "PullRequestNewFeedbackTriggerProvider",
    "RemoteIssueCommentCreatedTriggerProvider",
    "RemoteIssueMatchingTriggerProvider",
    "ScriptRunRequestTriggerProvider",
    "PushTriggerProvider",
    "TeamsDirectMessageTriggerProvider",
    "TicketChangedTriggerProvider",
    "TicketCreatedTriggerProvider",
    "WebhookTriggerProvider",
    "TriggerHookHandler",
    "TriggerProvider",
    "TriggerProviderBase",
    "TriggerRuntimeContext",
]

_EXPORT_MODULES = {
    "CiRunCompletedTriggerProvider": ".ci_run_completed",
    "AdoWorkItemCommentCreatedTriggerProvider": ".ado_work_item_comment_created",
    "AgentRunCompletedEventTriggerProvider": ".agent_run_completed_event",
    "AgentArtifactStagedTriggerProvider": ".agent_artifact_staged",
    "ApprovalPendingCreatedTriggerProvider": ".approval_pending_created",
    "CronTriggerProvider": ".cron",
    "CommunicationNewMessageTriggerProvider": ".communication_new_message",
    "DiscordNewMessageTriggerProvider": ".discord_new_message",
    "EmailNewMessageTriggerProvider": ".email_new_message",
    "GitPushTriggerProvider": ".git_push",
    "IssueCommentCreatedTriggerProvider": ".issue_comment_created",
    "PullRequestCompletedTriggerProvider": ".pull_request_completed",
    "PullRequestNewFeedbackTriggerProvider": ".pull_request_new_feedback",
    "RemoteIssueCommentCreatedTriggerProvider": ".remote_issue_comment_created",
    "RemoteIssueMatchingTriggerProvider": ".remote_issue_matching",
    "PushTriggerProvider": ".base",
    "ScriptRunRequestTriggerProvider": ".script_run_request",
    "TeamsDirectMessageTriggerProvider": ".teams_direct_message",
    "TicketChangedTriggerProvider": ".ticket_changed",
    "TicketCreatedTriggerProvider": ".ticket_created",
    "WebhookTriggerProvider": ".webhook",
    "TriggerHookHandler": ".base",
    "TriggerProvider": ".base",
    "TriggerProviderBase": ".base",
    "TriggerRuntimeContext": ".base",
}


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value
