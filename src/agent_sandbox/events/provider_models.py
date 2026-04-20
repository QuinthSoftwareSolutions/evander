from __future__ import annotations

from typing import Literal

from agent_sandbox.effects.providers.schemas import (
    AgentEnqueuePromptEffectDefinition,
    AgentPromptEffectDefinition,
    BashScriptEffectDefinition,
    GitPromoteAgentBranchEffectDefinition,
    IssueUpdateStateEffectDefinition,
    PrCreateEffectDefinition,
    SessionUpdateMetadataEffectDefinition,
    VmCopyFilesEffectDefinition,
    VmCopyFilesItemDefinition,
    VmCreateEffectDefinition,
    VmDeleteFilesEffectDefinition,
    VmExecEffectDefinition,
    VmStartEffectDefinition,
    VmStopEffectDefinition,
)
from agent_sandbox.queries.providers.schemas import (
    GitPrNoteQueryDefinition,
    IssueGetQueryDefinition,
    SessionContextQueryDefinition,
    VmRunStateQueryDefinition,
)
from agent_sandbox.triggers.providers.schemas import (
    AgentPromptNewTriggerDefinition,
    AgentRunCompletedTriggerDefinition,
    CronTriggerDefinition,
    DiscordNewMessageTriggerDefinition,
    EmailNewMessageTriggerDefinition,
    GitPushTriggerDefinition,
    PullRequestNewFeedbackTriggerDefinition,
    ReviewNewRequestTriggerDefinition,
    ScriptRunRequestTriggerDefinition,
    TeamsContentFilterDefinition,
    TeamsMentionFilterDefinition,
    TeamsMessageFilterDefinition,
    TeamsNewMessageTriggerDefinition,
    TeamsTargetDefinition,
    TicketChangedTriggerDefinition,
    TicketCreatedTriggerDefinition,
)

EmailProvider = Literal["json_store", "gmail", "graph"]
PrFeedbackProvider = Literal["local", "github", "ado"]


def validate_email_provider(value: object) -> str:
    normalized = str(value).strip().lower()
    allowed = {"json_store", "gmail", "graph"}
    if normalized not in allowed:
        ordered = ", ".join(sorted(allowed))
        raise ValueError(f"Unsupported email provider: {normalized!r}. Allowed providers: {ordered}")
    return normalized


def validate_pr_feedback_provider(value: object) -> str:
    normalized = str(value).strip().lower()
    if normalized not in {"local", "github", "ado"}:
        raise ValueError(
            f"Unsupported pull-request feedback provider: {normalized!r}. Allowed providers: ado, github, local"
        )
    return normalized

