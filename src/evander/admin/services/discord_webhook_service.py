from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from evander.messaging.targets import (
    CommunicationTargetDefinition,
    load_communication_targets_config,
)
from evander.services import DiscordBotApiService


@dataclass(frozen=True)
class DiscordWebhookAdminDependencies:
    runtime: Any
    normalize_directory_reference: Any


class DiscordWebhookAdminService:
    def __init__(self, dependencies: DiscordWebhookAdminDependencies) -> None:
        self._deps = dependencies

    def _target(
        self, *, directory_path: str | None, target_id: str
    ) -> tuple[str, CommunicationTargetDefinition]:
        directory = self._deps.normalize_directory_reference(directory_path)
        normalized_target_id = str(target_id or "").strip().casefold()
        if not normalized_target_id:
            raise ValueError("target_id is required")
        target = load_communication_targets_config(
            runtime_dir=self._deps.runtime.config.runtime_dir,
            directory_store=self._deps.runtime.directories,
            directory_path=directory,
        ).by_id().get(normalized_target_id)
        if target is None:
            raise ValueError(
                f"Communication target {normalized_target_id!r} is not configured for "
                f"directory {directory!r}."
            )
        if target.provider != "discord":
            raise ValueError(
                f"Communication target {target.id!r} uses provider {target.provider!r}, "
                "not Discord."
            )
        return directory, target

    def _service(
        self, *, directory_path: str | None, target_id: str
    ) -> tuple[str, CommunicationTargetDefinition, DiscordBotApiService]:
        directory, target = self._target(directory_path=directory_path, target_id=target_id)
        config = self._deps.runtime.provider_config.resolve_for_directory(directory)
        if config.discord_backend != "bot_api":
            raise ValueError(
                f"Directory {directory!r} is not configured for the Discord bot API."
            )
        token_env = str(config.discord_bot_token_env or "").strip()
        token = os.environ.get(token_env, "").strip()
        if not token:
            raise ValueError(
                f"Discord bot token environment variable {token_env!r} is not populated."
            )
        channel_id = target.address.split("/")[-1].strip()
        service = DiscordBotApiService(
            self._deps.runtime.discord_commands,
            token=token,
            api_base_url=config.discord_api_base_url,
            poll_limit=config.discord_poll_limit,
            timeout_seconds=config.discord_timeout_seconds,
            retry_count=config.discord_retry_count,
            retry_base_backoff_seconds=config.discord_retry_base_backoff_seconds,
            retry_max_backoff_seconds=config.discord_retry_max_backoff_seconds,
            allowed_channel_ids_provider=lambda: [channel_id],
            read_reaction_emoji=config.discord_read_reaction_emoji,
            bot_token_env=token_env,
            webhook_store_path=config.discord_webhook_store_path,
            persona_webhook_cache_limit=config.discord_persona_webhook_cache_limit,
        )
        return directory, target, service

    @staticmethod
    def _response_context(
        *, directory: str, target: CommunicationTargetDefinition
    ) -> dict[str, str]:
        return {
            "directory_path": directory,
            "target_id": target.id,
            "channel_id": target.address.split("/")[-1].strip(),
        }

    def status(self, *, directory_path: str | None, target_id: str) -> dict[str, Any]:
        directory, target, service = self._service(
            directory_path=directory_path, target_id=target_id
        )
        channel_id = target.address.split("/")[-1].strip()
        return {
            **self._response_context(directory=directory, target=target),
            **service.webhook_status(channel_id),
        }

    def provision(
        self, *, directory_path: str | None, target_id: str, replace: bool = False
    ) -> dict[str, Any]:
        directory, target, service = self._service(
            directory_path=directory_path, target_id=target_id
        )
        channel_id = target.address.split("/")[-1].strip()
        return {
            **self._response_context(directory=directory, target=target),
            **service.provision_webhook(channel_id, force=replace),
        }

    def test(self, *, directory_path: str | None, target_id: str) -> dict[str, Any]:
        directory, target, service = self._service(
            directory_path=directory_path, target_id=target_id
        )
        channel_id = target.address.split("/")[-1].strip()
        return {
            **self._response_context(directory=directory, target=target),
            **service.test_webhook(
                channel_id,
                message=f"Evander webhook test for communication target {target.id}",
            ),
        }
