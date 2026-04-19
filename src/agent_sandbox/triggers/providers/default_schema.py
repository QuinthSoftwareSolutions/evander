from __future__ import annotations

from agent_sandbox.events.models import (
    AgentPromptNewTriggerDefinition,
    AgentRunCompletedTriggerDefinition,
    CronTriggerDefinition,
    DiscordNewMessageTriggerDefinition,
    EmailNewMessageTriggerDefinition,
    GitPushTriggerDefinition,
    PullRequestNewFeedbackTriggerDefinition,
    ReviewNewRequestTriggerDefinition,
    ScriptRunRequestTriggerDefinition,
    TeamsNewMessageTriggerDefinition,
    TicketChangedTriggerDefinition,
    TicketCreatedTriggerDefinition,
    register_node_output_schema,
    register_node_schema,
)

_TRIGGER_MODEL_BY_TYPE = {
    "trigger:cron": CronTriggerDefinition,
    "trigger:ticket:created": TicketCreatedTriggerDefinition,
    "trigger:ticket:changed": TicketChangedTriggerDefinition,
    "trigger:script-run-request": ScriptRunRequestTriggerDefinition,
    "trigger:discord:new-message": DiscordNewMessageTriggerDefinition,
    "trigger:teams:new-message": TeamsNewMessageTriggerDefinition,
    "trigger:email:new-message": EmailNewMessageTriggerDefinition,
    "trigger:pull-request:new-feedback": PullRequestNewFeedbackTriggerDefinition,
    "trigger:review:new-request": ReviewNewRequestTriggerDefinition,
    "trigger:git:push": GitPushTriggerDefinition,
    "trigger:agent:run:completed": AgentRunCompletedTriggerDefinition,
    "trigger:agent:prompt:new": AgentPromptNewTriggerDefinition,
}

_TRIGGER_OUTPUT_SCHEMAS = {
    "trigger:ticket:created": {"ticket": {"type": "object"}},
    "trigger:ticket:changed": {"ticket": {"type": "object"}},
    "trigger:script-run-request": {
        "request_id": {"type": "string"},
        "session_id": {"type": "string"},
        "script_name": {"type": "string"},
        "parameters": {"type": "array", "items": {"type": "string"}},
    },
    "trigger:discord:new-message": {"message": {"type": "object"}},
    "trigger:teams:new-message": {},
    "trigger:email:new-message": {"message": {"type": "object"}},
    "trigger:pull-request:new-feedback": {
        "session_id": {"type": "string"},
        "pr": {"type": "object"},
        "feedback": {"type": "object"},
    },
    "trigger:review:new-request": {"review_request": {"type": "object"}},
    "trigger:git:push": {
        "branch_name": {"type": "string"},
        "commit_hash": {"type": "string"},
        "old_commit_hash": {"type": "string"},
        "ref_name": {"type": "string"},
        "repo_path": {"type": "string"},
        "repo_name": {"type": "string"},
        "session_id": {"type": "string"},
        "review_branch": {"type": "string"},
        "trusted_repo_path": {"type": "string"},
        "base_branch": {"type": "string"},
        "canonical_origin_url": {"type": "string"},
    },
    "trigger:agent:prompt:new": {
        "session_id": {"type": "string"},
        "agent_prompt": {"type": "object"},
    },
    "trigger:agent:run:completed": {
        "vm": {"type": "object"},
        "dispatch_id": {"type": "string"},
        "scope": {"type": "object"},
    },
}


def register_trigger_node_schemas() -> None:
    for node_type, model in _TRIGGER_MODEL_BY_TYPE.items():
        register_node_schema(node_type=node_type, model_class=model)
    for node_type, outputs in _TRIGGER_OUTPUT_SCHEMAS.items():
        register_node_output_schema(node_type=node_type, outputs=outputs)
