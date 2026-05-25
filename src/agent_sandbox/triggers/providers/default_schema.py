from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel

from agent_sandbox.triggers.providers.schemas import (
    AdoWorkItemMatchingTriggerDefinition,
    AgentPromptNewTriggerDefinition,
    AgentRunCompletedTriggerDefinition,
    CronTriggerDefinition,
    EventTriggerDefinition,
    CommunicationNewMessageTriggerDefinition,
    DiscordNewMessageTriggerDefinition,
    EmailNewMessageTriggerDefinition,
    GitPushTriggerDefinition,
    IssueCommentCreatedTriggerDefinition,
    PullRequestCompletedTriggerDefinition,
    PullRequestNewFeedbackTriggerDefinition,
    ReviewNewRequestTriggerDefinition,
    ScriptRunRequestTriggerDefinition,
    TeamsNewMessageTriggerDefinition,
    TeamsMeetingNewTranscriptTriggerDefinition,
    TicketChangedTriggerDefinition,
    TicketCreatedTriggerDefinition,
)
from agent_sandbox.schema_bootstrap import _TRIGGER_OUTPUT_SCHEMAS
from agent_sandbox.workflow.contracts import WorkflowNodeEnvelope, register_node_output_schema

_TRIGGER_MODEL_BY_TYPE = {
    "trigger:event": EventTriggerDefinition,
    "trigger:cron": CronTriggerDefinition,
    "trigger:ticket:created": TicketCreatedTriggerDefinition,
    "trigger:ticket:changed": TicketChangedTriggerDefinition,
    "trigger:issue:comment-created": IssueCommentCreatedTriggerDefinition,
    "trigger:ado:work-item-matching": AdoWorkItemMatchingTriggerDefinition,
    "trigger:script-run-request": ScriptRunRequestTriggerDefinition,
    "trigger:communication:new-message": CommunicationNewMessageTriggerDefinition,
    "trigger:discord:new-message": DiscordNewMessageTriggerDefinition,
    "trigger:teams:new-message": TeamsNewMessageTriggerDefinition,
    "trigger:teams-meeting:new-transcript": TeamsMeetingNewTranscriptTriggerDefinition,
    "trigger:email:new-message": EmailNewMessageTriggerDefinition,
    "trigger:pull-request:new-feedback": PullRequestNewFeedbackTriggerDefinition,
    "trigger:pull-request:completed": PullRequestCompletedTriggerDefinition,
    "trigger:review:new-request": ReviewNewRequestTriggerDefinition,
    "trigger:git:push": GitPushTriggerDefinition,
    "trigger:agent:run:completed": AgentRunCompletedTriggerDefinition,
    "trigger:agent:prompt:new": AgentPromptNewTriggerDefinition,
}


def _materialize_with_model(model_class: type[BaseModel], node: WorkflowNodeEnvelope) -> BaseModel:
    payload = {
        "id": node.id,
        "display_name": node.display_name,
        "type": node.type,
        "outputs": node.outputs,
        **dict(node.config),
    }
    return model_class.model_validate(payload)


def trigger_node_schema_resolvers() -> dict[str, Callable[[WorkflowNodeEnvelope], BaseModel]]:
    return {
        node_type: (lambda node, model=model: _materialize_with_model(model, node))
        for node_type, model in _TRIGGER_MODEL_BY_TYPE.items()
    }


def register_trigger_output_schemas() -> None:
    for node_type, outputs in _TRIGGER_OUTPUT_SCHEMAS.items():
        register_node_output_schema(node_type=node_type, outputs=outputs)
