from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "AgentRunCompletedTriggerProvider",
    "CronTriggerProvider",
    "CommunicationNewMessageTriggerProvider",
    "DiscordNewMessageTriggerProvider",
    "EmailNewMessageTriggerProvider",
    "GitPushTriggerProvider",
    "IssueCommentCreatedTriggerProvider",
    "PullRequestCompletedTriggerProvider",
    "PullRequestNewFeedbackTriggerProvider",
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
    "AgentRunCompletedTriggerProvider": ".agent_run_completed",
    "CronTriggerProvider": ".cron",
    "CommunicationNewMessageTriggerProvider": ".communication_new_message",
    "DiscordNewMessageTriggerProvider": ".discord_new_message",
    "EmailNewMessageTriggerProvider": ".email_new_message",
    "GitPushTriggerProvider": ".git_push",
    "IssueCommentCreatedTriggerProvider": ".issue_comment_created",
    "PullRequestCompletedTriggerProvider": ".pull_request_completed",
    "PullRequestNewFeedbackTriggerProvider": ".pull_request_new_feedback",
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
