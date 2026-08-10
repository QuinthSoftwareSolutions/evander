from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import evander.admin.services.discord_webhook_service as webhook_module
from evander.admin.services.discord_webhook_service import (
    DiscordWebhookAdminDependencies,
    DiscordWebhookAdminService,
)
from evander.config import GatewayConfig
from evander.directories import DirectoryStore
from evander.messaging.targets import (
    SqliteCommunicationTargetsRepository,
    communication_targets_db_path,
)


def test_discord_webhook_admin_resolves_saved_target_and_redacts_credentials(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        discord_backend="bot_api",
        discord_bot_token_env="TEST_DISCORD_BOT_TOKEN",
    )
    monkeypatch.setenv("TEST_DISCORD_BOT_TOKEN", "secret-bot-token")
    directories = DirectoryStore(config.directories_store_path)
    directories.ensure_root()
    targets = SqliteCommunicationTargetsRepository(
        communication_targets_db_path(config.runtime_dir)
    )
    targets.write_overlay(
        "root",
        {
            "targets": [
                {
                    "id": "discord-team",
                    "provider": "discord",
                    "kind": "discord",
                    "address": "111111111111111111/222222222222222222",
                    "policy": {
                        "capabilities": ["write"],
                        "sender_identity": "resolved",
                    },
                }
            ]
        },
    )
    constructed: list[dict[str, object]] = []

    class FakeDiscordService:
        def __init__(self, command_store, **kwargs) -> None:
            constructed.append({"command_store": command_store, **kwargs})

        def webhook_status(self, channel_id: str):
            return {
                "configured": True,
                "channel_id": channel_id,
                "webhook_id": "webhook-1",
            }

        def provision_webhook(self, channel_id: str, *, force: bool = False):
            return {
                "configured": True,
                "channel_id": channel_id,
                "webhook_id": "webhook-2" if force else "webhook-1",
            }

        def test_webhook(self, channel_id: str, *, message: str):
            return {
                "status": "posted",
                "channel_id": channel_id,
                "message_id": "message-1",
                "message": message,
            }

    monkeypatch.setattr(webhook_module, "DiscordBotApiService", FakeDiscordService)
    runtime = SimpleNamespace(
        config=config,
        directories=directories,
        discord_commands=object(),
        provider_config=SimpleNamespace(resolve_for_directory=lambda _: config),
    )
    service = DiscordWebhookAdminService(
        DiscordWebhookAdminDependencies(
            runtime=runtime,
            normalize_directory_reference=lambda value: value or "root",
        )
    )

    status = service.status(directory_path="root", target_id="DISCORD-TEAM")
    provisioned = service.provision(
        directory_path="root", target_id="discord-team", replace=False
    )
    recreated = service.provision(
        directory_path="root", target_id="discord-team", replace=True
    )
    tested = service.test(directory_path="root", target_id="discord-team")

    assert status == {
        "directory_path": "root",
        "target_id": "discord-team",
        "channel_id": "222222222222222222",
        "configured": True,
        "webhook_id": "webhook-1",
    }
    assert provisioned["webhook_id"] == "webhook-1"
    assert recreated["webhook_id"] == "webhook-2"
    assert tested["message_id"] == "message-1"
    assert constructed[0]["token"] == "secret-bot-token"
    assert all("webhook-token" not in str(payload) for payload in (status, provisioned, recreated))
