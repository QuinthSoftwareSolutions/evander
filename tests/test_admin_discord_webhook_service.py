from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.testclient import TestClient

import evander.admin.services.discord_webhook_service as webhook_module
from evander.admin.services.discord_webhook_service import (
    DiscordWebhookAdminDependencies,
    DiscordWebhookAdminService,
)
from evander.admin.web.discord_webhooks import discord_webhook_routes
from evander.config import GatewayConfig
from evander.directories import DirectoryStore
from evander.messaging.targets import (
    SqliteCommunicationTargetsRepository,
    communication_targets_db_path,
)


class _ScopedOwnerAuth:
    def __init__(self) -> None:
        self.permission_paths: list[str | None] = []

    def require(self, _request: Request) -> dict[str, str]:
        return {"login": "team-a-owner"}

    def require_permission(
        self,
        _request: Request,
        _permission: object,
        directory_path: object | None = None,
    ) -> None:
        normalized = str(directory_path) if directory_path is not None else None
        self.permission_paths.append(normalized)
        if normalized != "root/team-a":
            raise PermissionError("Permission required: admin.owner")

    def privileged_for_request(self, _request: Request) -> bool:
        return True


class _FakeDiscordWebhookAdmin:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, bool | None]] = []

    def provision(
        self, *, directory_path: str, target_id: str, replace: bool
    ) -> dict[str, object]:
        self.calls.append(("provision", directory_path, target_id, replace))
        return {"configured": True, "replace": replace}

    def test(self, *, directory_path: str, target_id: str) -> dict[str, object]:
        self.calls.append(("test", directory_path, target_id, None))
        return {"status": "posted"}


def _discord_webhook_route_client() -> tuple[
    TestClient, _ScopedOwnerAuth, _FakeDiscordWebhookAdmin
]:
    auth = _ScopedOwnerAuth()
    webhook_admin = _FakeDiscordWebhookAdmin()

    def normalize_directory_reference(value: str | None) -> str:
        normalized = str(value or "root").strip()
        if normalized not in {"root", "root/team-a"}:
            raise ValueError(f"Unknown directory: {normalized}")
        return normalized

    service = SimpleNamespace(
        teams_admin=SimpleNamespace(
            normalize_directory_reference=normalize_directory_reference
        ),
        discord_webhook_admin=webhook_admin,
    )
    client = TestClient(Starlette(routes=discord_webhook_routes(service, auth)))
    return client, auth, webhook_admin


@pytest.mark.parametrize(
    ("suffix", "expected_call"),
    [
        ("provision", ("provision", "root/team-a", "discord-team", False)),
        ("recreate", ("provision", "root/team-a", "discord-team", True)),
        ("test", ("test", "root/team-a", "discord-team", None)),
    ],
)
def test_discord_webhook_mutations_authorize_the_target_directory(
    suffix: str,
    expected_call: tuple[str, str, str, bool | None],
) -> None:
    client, auth, webhook_admin = _discord_webhook_route_client()

    response = client.post(
        f"/api/discord-webhooks/targets/discord-team/{suffix}",
        params={"directory_path": "root/team-a"},
    )

    assert response.status_code == 200
    assert auth.permission_paths == ["root/team-a"]
    assert webhook_admin.calls == [expected_call]


def test_discord_webhook_mutation_rejects_directory_outside_owner_scope() -> None:
    client, auth, webhook_admin = _discord_webhook_route_client()

    response = client.post(
        "/api/discord-webhooks/targets/discord-team/provision",
        params={"directory_path": "root"},
    )

    assert response.status_code == 403
    assert auth.permission_paths == ["root"]
    assert webhook_admin.calls == []


def test_discord_webhook_mutation_validates_directory_before_authorizing() -> None:
    client, auth, webhook_admin = _discord_webhook_route_client()

    response = client.post(
        "/api/discord-webhooks/targets/discord-team/provision",
        params={"directory_path": "root/missing"},
    )

    assert response.status_code == 400
    assert response.json() == {"error": "Unknown directory: root/missing"}
    assert auth.permission_paths == []
    assert webhook_admin.calls == []


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
