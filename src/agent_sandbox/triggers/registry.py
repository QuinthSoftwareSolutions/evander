from __future__ import annotations

import os
from collections.abc import Callable, Mapping

from agent_sandbox.provider_config import ProviderConfigResolver
from agent_sandbox.events.models import TriggerDefinitionDiscriminated, TriggerType
from agent_sandbox.triggers.providers.base import TriggerProviderBase, TriggerRuntimeContext
from agent_sandbox.triggers.providers.agent_prompt_new import AgentPromptNewTriggerProvider
from agent_sandbox.triggers.providers.agent_run_completed import AgentRunCompletedTriggerProvider
from agent_sandbox.triggers.providers.cron import CronTriggerProvider
from agent_sandbox.triggers.providers.discord_new_message import DiscordNewMessageTriggerProvider
from agent_sandbox.triggers.providers.email_new_message import EmailNewMessageTriggerProvider
from agent_sandbox.triggers.providers.git_push import GitPushTriggerProvider
from agent_sandbox.triggers.providers.pull_request_new_feedback import PullRequestNewFeedbackTriggerProvider
from agent_sandbox.triggers.providers.review_new_request import ReviewNewRequestTriggerProvider
from agent_sandbox.triggers.providers.script_run_request import ScriptRunRequestTriggerProvider
from agent_sandbox.triggers.providers.teams_new_message import TeamsNewMessageTriggerProvider
from agent_sandbox.triggers.providers.ticket_changed import TicketChangedTriggerProvider
from agent_sandbox.triggers.providers.ticket_created import TicketCreatedTriggerProvider

TriggerProviderClass = type[TriggerProviderBase]


class NoopTriggerProvider(TriggerProviderBase):
    """Default provider scaffold for trigger types awaiting concrete implementations."""


DEFAULT_TRIGGER_PROVIDER_REGISTRY: dict[TriggerType, TriggerProviderClass] = {
    "trigger:cron": CronTriggerProvider,
    "trigger:ticket:created": TicketCreatedTriggerProvider,
    "trigger:ticket:changed": TicketChangedTriggerProvider,
    "trigger:script-run-request": ScriptRunRequestTriggerProvider,
    "trigger:discord:new-message": DiscordNewMessageTriggerProvider,
    "trigger:teams:new-message": TeamsNewMessageTriggerProvider,
    "trigger:email:new-message": EmailNewMessageTriggerProvider,
    "trigger:pull-request:new-feedback": PullRequestNewFeedbackTriggerProvider,
    "trigger:review:new-request": ReviewNewRequestTriggerProvider,
    "trigger:git:push": GitPushTriggerProvider,
    "trigger:agent:prompt:new": AgentPromptNewTriggerProvider,
    "trigger:agent:run:completed": AgentRunCompletedTriggerProvider,
}


class TriggerProviderRegistry:
    def __init__(self, initial: Mapping[TriggerType, TriggerProviderClass] | None = None) -> None:
        self._providers: dict[TriggerType, TriggerProviderClass] = dict(initial or DEFAULT_TRIGGER_PROVIDER_REGISTRY)

    def register(self, trigger_type: TriggerType, provider_class: TriggerProviderClass) -> None:
        self._providers[trigger_type] = provider_class

    def resolve(self, trigger_type: TriggerType) -> TriggerProviderClass:
        provider_class = self._providers.get(trigger_type)
        if provider_class is None:
            raise KeyError(f"No trigger provider registered for type {trigger_type!r}")
        return provider_class


class TriggerProviderFactory:
    """Centralized provider constructor to keep env/secret handling host-side."""

    def __init__(
        self,
        provider_config_resolver: ProviderConfigResolver,
        *,
        registry: TriggerProviderRegistry | None = None,
        admin_service: object | None = None,
        env: Mapping[str, str] | None = None,
        secret_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        self.provider_config_resolver = provider_config_resolver
        self.registry = registry or TriggerProviderRegistry()
        self.admin_service = admin_service
        self.env = env or dict(os.environ)
        self.secret_resolver = secret_resolver

    def build(
        self,
        trigger_definition: TriggerDefinitionDiscriminated,
        *,
        directory_path: str | None = None,
    ) -> TriggerProviderBase:
        provider_class = self.registry.resolve(trigger_definition.type)
        resolved_config = self.provider_config_resolver.resolve_for_directory(directory_path)
        runtime_context = TriggerRuntimeContext(
            resolved_config=resolved_config,
            admin_service=self.admin_service,
            env=self.env,
            secret_resolver=self.secret_resolver,
            metadata={"directory_path": directory_path or "root"},
        )
        provider = provider_class()
        validated_definition = provider_class.validate_definition(trigger_definition)
        provider.initialize(trigger_definition=validated_definition, runtime_context=runtime_context)
        return provider
