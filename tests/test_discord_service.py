from __future__ import annotations

import hashlib
import json
import threading
import traceback
from datetime import UTC, datetime
from inspect import signature
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from PIL import Image

from evander.discord_webhooks import discord_webhook_credential_key
from evander.models import DiscordCommandEnvelope
from evander.outbound_identity import OutboundAvatar, OutboundIdentity
from evander.services import DiscordBotApiService, StubDiscordService
from evander.storage import JsonStore


def patch_httpx_client(
    monkeypatch: pytest.MonkeyPatch,
    transport: httpx.BaseTransport,
) -> None:
    real_client = httpx.Client
    monkeypatch.setattr(
        "evander.services.httpx.Client",
        lambda timeout: real_client(timeout=timeout, transport=transport),
    )


class RecordingDiscordCommandStore:
    def __init__(self) -> None:
        self.records: dict[str, DiscordCommandEnvelope] = {}
        self.put_calls = 0
        self.put_many_calls = 0

    def get(self, command_id: str) -> DiscordCommandEnvelope | None:
        return self.records.get(command_id)

    def put(self, command_id: str, envelope: DiscordCommandEnvelope) -> None:
        self.put_calls += 1
        self.records[command_id] = envelope

    def put_many(self, records: dict[str, DiscordCommandEnvelope]) -> None:
        self.put_many_calls += 1
        self.records.update(records)

    def read_all(self) -> dict[str, DiscordCommandEnvelope]:
        return dict(self.records)


def make_service(tmp_path: Path, transport: httpx.BaseTransport) -> DiscordBotApiService:
    command_store = JsonStore(tmp_path / "discord.json", DiscordCommandEnvelope)
    return DiscordBotApiService(
        command_store,
        token="test-token",
        api_base_url="https://discord.test/api/v10",
        poll_limit=25,
        timeout_seconds=5.0,
        retry_count=3,
        retry_base_backoff_seconds=0.01,
        retry_max_backoff_seconds=0.05,
        allowed_channel_ids_provider=lambda: ["chan-a"],
    )


def make_webp_avatar(color: str = "#336699") -> OutboundAvatar:
    output = BytesIO()
    Image.new("RGB", (8, 8), color).save(output, format="WEBP")
    return OutboundAvatar.from_bytes(output.getvalue(), mime_type="image/webp")


def test_discord_webhook_generic_credential_key_remains_backward_compatible() -> None:
    api_base_url = "https://discord.test/api/v10"
    bot_token_env = "DISCORD_BOT_TOKEN"
    channel_id = "chan-a"
    expected = hashlib.sha256(
        f"{api_base_url}\n{bot_token_env}\n{channel_id}".encode("utf-8")
    ).hexdigest()

    assert (
        discord_webhook_credential_key(
            api_base_url=api_base_url,
            bot_token_env=bot_token_env,
            channel_id=channel_id,
        )
        == expected
    )
    assert (
        discord_webhook_credential_key(
            api_base_url=api_base_url,
            bot_token_env=bot_token_env,
            channel_id=channel_id,
            avatar_sha256="a" * 64,
        )
        != expected
    )


def test_discord_services_default_and_override_read_reaction_emoji(tmp_path: Path) -> None:
    command_store = JsonStore(tmp_path / "discord.json", DiscordCommandEnvelope)
    stub_default = StubDiscordService(command_store)
    stub_override = StubDiscordService(command_store, read_reaction_emoji="✅")
    api_default = DiscordBotApiService(
        command_store,
        token="test-token",
        api_base_url="https://discord.test/api/v10",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_ids_provider=lambda: ["chan-a"],
    )
    api_override = DiscordBotApiService(
        command_store,
        token="test-token",
        api_base_url="https://discord.test/api/v10",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_ids_provider=lambda: ["chan-a"],
        read_reaction_emoji="✅",
    )

    assert stub_default.read_reaction_emoji == "👀"
    assert stub_override.read_reaction_emoji == "✅"
    assert api_default.read_reaction_emoji == "👀"
    assert api_override.read_reaction_emoji == "✅"


def test_get_commands_polls_only_allowlisted_channels_and_filters_since_cursor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command_store = JsonStore(tmp_path / "discord.json", DiscordCommandEnvelope)
    command_store.put(
        "100",
        DiscordCommandEnvelope(
            command_id="100",
            author_id="user-a",
            channel_id="chan-a",
            content="older",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            metadata={"source": "discord_bot_api", "channel_id": "chan-a", "author_id": "user-a"},
        ),
    )
    command_store.put(
        "150",
        DiscordCommandEnvelope(
            command_id="150",
            author_id="user-b",
            channel_id="chan-b",
            content="middle",
            created_at=datetime(2026, 1, 2, tzinfo=UTC),
            metadata={"source": "discord_bot_api", "channel_id": "chan-b", "author_id": "user-b"},
        ),
    )

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/channels/chan-a/messages"):
            return httpx.Response(
                200,
                json=[{"id": "200", "content": "new-a", "author": {"id": "user-a"}}],
                request=request,
            )
        if request.url.path.endswith("/channels/chan-b/messages"):
            return httpx.Response(
                200,
                json=[{"id": "300", "content": "new-b", "author": {"id": "user-b"}}],
                request=request,
            )
        raise AssertionError(f"Unexpected path: {request.url.path}")

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)

    service = DiscordBotApiService(
        command_store,
        token="test-token",
        api_base_url="https://discord.test/api/v10",
        poll_limit=25,
        timeout_seconds=5.0,
        retry_count=2,
        retry_base_backoff_seconds=0.01,
        retry_max_backoff_seconds=0.05,
        allowed_channel_ids_provider=lambda: ["chan-a", "chan-b"],
    )

    commands = service.get_commands(since_cursor="180")

    assert [command.command_id for command in commands] == ["200", "300"]
    assert all(request.headers["Authorization"] == "Bot test-token" for request in requests)
    assert all(request.url.params.get("limit") == "25" for request in requests)


def test_get_commands_uses_live_poll_subset_while_archived_channels_remain_authorized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command_store = JsonStore(tmp_path / "discord.json", DiscordCommandEnvelope)
    command_store.put(
        "100",
        DiscordCommandEnvelope(
            command_id="100",
            author_id="user-a",
            channel_id="thread-archived",
            content="archived evidence",
            metadata={
                "source": "discord_bot_api",
                "channel_id": "thread-archived",
                "author_id": "user-a",
                "attachments": [{"id": "att-1", "filename": "evidence.txt"}],
            },
        ),
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path.endswith("/channels/thread-active/messages")
        return httpx.Response(200, json=[], request=request)

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    service = DiscordBotApiService(
        command_store,
        token="test-token",
        api_base_url="https://discord.test/api/v10",
        poll_limit=25,
        timeout_seconds=5.0,
        retry_count=0,
        allowed_channel_ids_provider=lambda: ["thread-active", "thread-archived"],
        poll_channel_ids_provider=lambda: ["thread-active"],
    )

    service.get_commands()

    assert [request.url.path for request in requests] == [
        "/api/v10/channels/thread-active/messages"
    ]
    assert service.list_attachments("100", channel_id="thread-archived") == [
        {"id": "att-1", "filename": "evidence.txt"}
    ]


def test_get_commands_batches_store_writes_after_successful_poll(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command_store = JsonStore(tmp_path / "discord.json", DiscordCommandEnvelope)
    put_calls = 0
    put_many_calls = 0
    original_put_many = command_store.put_many

    def fake_put(command_id: str, envelope: DiscordCommandEnvelope) -> None:
        nonlocal put_calls
        put_calls += 1
        raise AssertionError("get_commands() should batch Discord command writes")

    def fake_put_many(items: dict[str, DiscordCommandEnvelope]) -> None:
        nonlocal put_many_calls
        put_many_calls += 1
        original_put_many(items)

    monkeypatch.setattr(command_store, "put", fake_put)
    monkeypatch.setattr(command_store, "put_many", fake_put_many)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/channels/chan-a/messages"):
            return httpx.Response(
                200,
                json=[{"id": "200", "content": "new-a", "author": {"id": "user-a"}}],
                request=request,
            )
        if request.url.path.endswith("/channels/chan-b/messages"):
            return httpx.Response(
                200,
                json=[{"id": "300", "content": "new-b", "author": {"id": "user-b"}}],
                request=request,
            )
        raise AssertionError(f"Unexpected path: {request.url.path}")

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)

    service = DiscordBotApiService(
        command_store,
        token="test-token",
        api_base_url="https://discord.test/api/v10",
        poll_limit=25,
        timeout_seconds=5.0,
        retry_count=2,
        retry_base_backoff_seconds=0.01,
        retry_max_backoff_seconds=0.05,
        allowed_channel_ids_provider=lambda: ["chan-a", "chan-b"],
    )

    commands = service.get_commands()

    assert [command.command_id for command in commands] == ["200", "300"]
    assert put_many_calls == 1
    assert put_calls == 0
    assert [command.command_id for command in command_store.read_all().values()] == [
        "200",
        "300",
    ]


def test_get_commands_does_not_persist_partial_results_on_http_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command_store = JsonStore(tmp_path / "discord.json", DiscordCommandEnvelope)
    put_calls = 0
    put_many_calls = 0
    original_put_many = command_store.put_many

    def fake_put(command_id: str, envelope: DiscordCommandEnvelope) -> None:
        nonlocal put_calls
        put_calls += 1
        raise AssertionError("get_commands() should not write partial results")

    def fake_put_many(items: dict[str, DiscordCommandEnvelope]) -> None:
        nonlocal put_many_calls
        put_many_calls += 1
        original_put_many(items)

    monkeypatch.setattr(command_store, "put", fake_put)
    monkeypatch.setattr(command_store, "put_many", fake_put_many)

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/channels/chan-a/messages"):
            return httpx.Response(
                200,
                json=[{"id": "200", "content": "new-a", "author": {"id": "user-a"}}],
                request=request,
            )
        if request.url.path.endswith("/channels/chan-b/messages"):
            raise httpx.ReadTimeout("timed out", request=request)
        raise AssertionError(f"Unexpected path: {request.url.path}")

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)

    service = DiscordBotApiService(
        command_store,
        token="test-token",
        api_base_url="https://discord.test/api/v10",
        poll_limit=25,
        timeout_seconds=5.0,
        retry_count=0,
        allowed_channel_ids_provider=lambda: ["chan-a", "chan-b"],
    )

    with pytest.raises(httpx.ReadTimeout):
        service.get_commands()

    assert put_many_calls == 0
    assert put_calls == 0
    assert command_store.read_all() == {}
    assert [request.url.path for request in requests] == [
        "/api/v10/channels/chan-a/messages",
        "/api/v10/channels/chan-b/messages",
    ]


def test_get_commands_preserves_self_authored_webhook_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command_store = JsonStore(tmp_path / "discord.json", DiscordCommandEnvelope)
    command_store.put(
        "100",
        DiscordCommandEnvelope(
            command_id="100",
            author_id="webhook-1",
            channel_id="chan-a",
            content="persona post",
            metadata={
                "source": "discord_webhook_status",
                "session_id": "session-1",
                "transport": "webhook",
                "identity_status": "resolved",
            },
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "id": "100",
                    "content": "persona post",
                    "author": {"id": "webhook-1"},
                }
            ],
            request=request,
        )

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    service = DiscordBotApiService(
        command_store,
        token="test-token",
        api_base_url="https://discord.test/api/v10",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_ids_provider=lambda: ["chan-a"],
    )

    command = service.get_commands()[0]

    assert command.metadata["source"] == "discord_webhook_status"
    assert command.metadata["session_id"] == "session-1"
    assert command.metadata["identity_status"] == "resolved"


def test_get_commands_never_polls_stale_or_non_allowlisted_channels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command_store = JsonStore(tmp_path / "discord.json", DiscordCommandEnvelope)
    command_store.put(
        "100",
        DiscordCommandEnvelope(
            command_id="100",
            author_id="user-stale",
            channel_id="chan-stale",
            content="stale",
            metadata={
                "source": "discord_bot_api",
                "channel_id": "chan-stale",
                "author_id": "user-stale",
            },
        ),
    )

    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path.endswith("/channels/chan-allow/messages"):
            return httpx.Response(
                200,
                json=[{"id": "200", "content": "new", "author": {"id": "user-new"}}],
                request=request,
            )
        raise AssertionError(f"Unexpected path: {request.url.path}")

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)

    service = DiscordBotApiService(
        command_store,
        token="test-token",
        api_base_url="https://discord.test/api/v10",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_ids_provider=lambda: ["chan-allow"],
    )

    commands = service.get_commands()

    assert [command.command_id for command in commands] == ["100", "200"]
    assert requested_paths == ["/api/v10/channels/chan-allow/messages"]


def test_get_commands_polls_operator_dms_only_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path.endswith("/users/@me/channels"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "dm-operator",
                        "recipients": [{"id": "operator-user"}],
                    },
                    {
                        "id": "dm-other",
                        "recipients": [{"id": "not-operator"}],
                    },
                ],
                request=request,
            )
        if request.url.path.endswith("/channels/dm-operator/messages"):
            return httpx.Response(
                200,
                json=[{"id": "300", "content": "dm cmd", "author": {"id": "operator-user"}}],
                request=request,
            )
        raise AssertionError(f"Unexpected path: {request.url.path}")

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)
    service = DiscordBotApiService(
        JsonStore(tmp_path / "discord.json", DiscordCommandEnvelope),
        token="test-token",
        api_base_url="https://discord.test/api/v10",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_ids_provider=lambda: [],
        operator_user_ids_provider=lambda: ["operator-user"],
        include_operator_dms=True,
    )

    commands = service.get_commands()

    assert [command.command_id for command in commands] == ["300"]
    assert requested_paths == [
        "/api/v10/users/@me/channels",
        "/api/v10/channels/dm-operator/messages",
    ]


def test_get_commands_batches_fetched_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = RecordingDiscordCommandStore()
    store.records["100"] = DiscordCommandEnvelope(
        command_id="100",
        author_id="seed-user",
        channel_id="chan-a",
        content="seed",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        metadata={"source": "discord_bot_api", "channel_id": "chan-a", "author_id": "seed-user"},
    )

    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path.endswith("/users/@me/channels"):
            return httpx.Response(
                200,
                json=[
                    {"id": "dm-operator", "recipients": [{"id": "operator-user"}]},
                    {"id": "dm-other", "recipients": [{"id": "not-operator"}]},
                ],
                request=request,
            )
        if request.url.path.endswith("/channels/chan-a/messages"):
            return httpx.Response(
                200,
                json=[
                    {"id": "200", "content": "new-a", "author": {"id": "user-a"}},
                ],
                request=request,
            )
        if request.url.path.endswith("/channels/dm-operator/messages"):
            return httpx.Response(
                200,
                json=[
                    {"id": "300", "content": "dm", "author": {"id": "operator-user"}},
                ],
                request=request,
            )
        raise AssertionError(f"Unexpected path: {request.url.path}")

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    service = DiscordBotApiService(
        store,
        token="test-token",
        api_base_url="https://discord.test/api/v10",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_ids_provider=lambda: ["chan-a"],
        operator_user_ids_provider=lambda: ["operator-user"],
        include_operator_dms=True,
    )

    commands = service.get_commands(since_cursor="180")

    assert [command.command_id for command in commands] == ["200", "300"]
    assert store.put_many_calls == 1
    assert store.put_calls == 0
    assert requested_paths == [
        "/api/v10/users/@me/channels",
        "/api/v10/channels/chan-a/messages",
        "/api/v10/channels/dm-operator/messages",
    ]


def test_get_commands_does_not_write_when_no_new_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = RecordingDiscordCommandStore()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/channels/chan-a/messages"):
            return httpx.Response(200, json=[], request=request)
        raise AssertionError(f"Unexpected path: {request.url.path}")

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    service = DiscordBotApiService(
        store,
        token="test-token",
        api_base_url="https://discord.test/api/v10",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_ids_provider=lambda: ["chan-a"],
    )

    commands = service.get_commands()

    assert commands == []
    assert store.put_many_calls == 0
    assert store.put_calls == 0


def test_post_status_raises_on_rate_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"message": "You are being rate limited."}, request=request)

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)

    service = make_service(tmp_path, transport)

    with pytest.raises(httpx.HTTPStatusError):
        service.post_status("status", "chan-a", "session-1")


def test_post_status_success_returns_payload_and_persists_envelope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "500",
                "content": "relay online",
                "author": {"id": "bot-1"},
                "guild_id": "guild-1",
            },
            request=request,
        )

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)

    service = make_service(tmp_path, transport)

    result = service.post_status("relay online", "chan-a", "session-1")

    assert result == {
        "status": "posted",
        "channel_id": "chan-a",
        "session_id": "session-1",
        "message_id": "500",
    }
    assert len(requests) == 1
    assert requests[0].headers["Authorization"] == "Bot test-token"

    stored = service.command_store.get("500")
    assert stored is not None
    assert stored.content == "relay online"
    assert stored.channel_id == "chan-a"
    assert stored.metadata["source"] == "discord_bot_api_status"
    assert stored.metadata["session_id"] == "session-1"
    assert stored.metadata["guild_id"] == "guild-1"


@pytest.mark.parametrize("status_code", [401, 403])
def test_post_status_auth_failures_raise_without_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status_code, json={"message": "nope"}, request=request)

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)

    service = make_service(tmp_path, transport)

    with pytest.raises(httpx.HTTPStatusError):
        service.post_status("status", "chan-a", "session-1")

    assert len(requests) == 1


@pytest.mark.parametrize("status_code", [401, 403])
def test_get_commands_auth_failures_raise_without_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
) -> None:
    command_store = JsonStore(tmp_path / "discord.json", DiscordCommandEnvelope)
    command_store.put(
        "100",
        DiscordCommandEnvelope(
            command_id="100",
            author_id="user-a",
            channel_id="chan-a",
            content="older",
            metadata={"source": "discord_bot_api", "channel_id": "chan-a", "author_id": "user-a"},
        ),
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status_code, json={"message": "nope"}, request=request)

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)

    service = DiscordBotApiService(
        command_store,
        token="test-token",
        api_base_url="https://discord.test/api/v10",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_ids_provider=lambda: ["chan-a"],
    )

    with pytest.raises(httpx.HTTPStatusError):
        service.get_commands()

    assert len(requests) == 1


def test_get_commands_cursor_regression_numeric_monotonicity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command_store = JsonStore(tmp_path / "discord.json", DiscordCommandEnvelope)
    command_store.put(
        "9",
        DiscordCommandEnvelope(
            command_id="9",
            author_id="seed",
            channel_id="chan-a",
            content="seed",
            metadata={"source": "discord_bot_api", "channel_id": "chan-a", "author_id": "seed"},
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"id": "10", "content": "ten", "author": {"id": "user-10"}},
                {"id": "11", "content": "eleven", "author": {"id": "user-11"}},
            ],
            request=request,
        )

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)

    service = DiscordBotApiService(
        command_store,
        token="test-token",
        api_base_url="https://discord.test/api/v10",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_ids_provider=lambda: ["chan-a"],
    )

    commands = service.get_commands(since_cursor="9")

    assert [command.command_id for command in commands] == ["10", "11"]


def test_get_commands_cursor_restart_safe_ordering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command_store = JsonStore(tmp_path / "discord.json", DiscordCommandEnvelope)
    command_store.put(
        "2",
        DiscordCommandEnvelope(
            command_id="2",
            author_id="seed",
            channel_id="chan-a",
            content="seed",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            metadata={"source": "discord_bot_api", "channel_id": "chan-a", "author_id": "seed"},
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"id": "4", "content": "four", "author": {"id": "user-4"}},
                {"id": "3", "content": "three", "author": {"id": "user-3"}},
            ],
            request=request,
        )

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)

    service = DiscordBotApiService(
        command_store,
        token="test-token",
        api_base_url="https://discord.test/api/v10",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_ids_provider=lambda: ["chan-a"],
    )

    first_poll = service.get_commands(since_cursor="2")
    second_poll = service.get_commands(since_cursor="4")

    assert [command.command_id for command in first_poll] == ["3", "4"]
    assert second_poll == []


def test_discord_service_contract_signatures_are_stable() -> None:
    get_commands_sig = signature(DiscordBotApiService.get_commands)
    post_status_sig = signature(DiscordBotApiService.post_status)
    add_reaction_sig = signature(DiscordBotApiService.add_reaction)

    assert list(get_commands_sig.parameters) == ["self", "since_cursor"]
    assert get_commands_sig.parameters["since_cursor"].default is None
    assert list(post_status_sig.parameters) == ["self", "message", "channel_id", "session_id"]
    assert list(add_reaction_sig.parameters) == [
        "self",
        "channel_id",
        "message_id",
        "emoji",
        "session_id",
    ]


def test_add_reaction_success_returns_normalized_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204, request=request)

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)
    service = make_service(tmp_path, transport)

    result = service.add_reaction("chan-a", "msg-123", "✅", "session-1")

    assert result == {
        "status": "reacted",
        "channel_id": "chan-a",
        "session_id": "session-1",
        "message_id": "msg-123",
        "emoji": "✅",
        "reaction": "✅",
    }
    assert requests[0].method == "PUT"
    assert str(requests[0].url).endswith(
        "/channels/chan-a/messages/msg-123/reactions/%E2%9C%85/@me"
    )


def test_stub_discord_add_reaction_returns_deterministic_success_payload(tmp_path: Path) -> None:
    command_store = JsonStore(tmp_path / "discord.json", DiscordCommandEnvelope)
    service = StubDiscordService(command_store)

    result = service.add_reaction("chan-a", "msg-123", "👍", "session-1")

    assert result == {
        "status": "stubbed",
        "channel_id": "chan-a",
        "session_id": "session-1",
        "message_id": "msg-123",
        "emoji": "👍",
        "reaction": "👍",
    }


def test_get_commands_raises_on_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    command_store = JsonStore(tmp_path / "discord.json", DiscordCommandEnvelope)
    command_store.put(
        "100",
        DiscordCommandEnvelope(
            command_id="100",
            author_id="user-a",
            channel_id="chan-a",
            content="older",
            metadata={"source": "discord_bot_api", "channel_id": "chan-a", "author_id": "user-a"},
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)

    service = DiscordBotApiService(
        command_store,
        token="test-token",
        api_base_url="https://discord.test/api/v10",
        poll_limit=25,
        timeout_seconds=5.0,
        retry_count=2,
        retry_base_backoff_seconds=0.01,
        retry_max_backoff_seconds=0.05,
        allowed_channel_ids_provider=lambda: ["chan-a"],
    )

    with pytest.raises(httpx.ReadTimeout):
        service.get_commands()


def test_post_status_retries_transient_5xx_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempt_count = {"value": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempt_count["value"] += 1
        if attempt_count["value"] < 3:
            return httpx.Response(503, json={"message": "temporarily unavailable"}, request=request)
        return httpx.Response(
            200,
            json={
                "id": "500",
                "content": "ok",
                "author": {"id": "bot-user"},
            },
            request=request,
        )

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)
    monkeypatch.setattr("evander.services.time.sleep", lambda _: None)
    monkeypatch.setattr("evander.services.random.uniform", lambda low, high: low)

    service = make_service(tmp_path, transport)
    result = service.post_status("status", "chan-a", "session-1")

    assert result["status"] == "posted"
    assert result["message_id"] == "500"
    assert attempt_count["value"] == 3


def test_post_status_fails_fast_on_401_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(401, json={"message": "401: Unauthorized"}, request=request)

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)

    monkeypatch.setattr("evander.services.time.sleep", lambda _: None)

    service = make_service(tmp_path, transport)

    with pytest.raises(httpx.HTTPStatusError):
        service.post_status("status", "chan-a", "session-1")

    assert len(requests) == 1


def test_get_commands_sorts_by_snowflake_not_lexicographic_or_created_at(tmp_path: Path) -> None:
    command_store = JsonStore(tmp_path / "discord.json", DiscordCommandEnvelope)
    command_store.put(
        "1000",
        DiscordCommandEnvelope(
            command_id="1000",
            author_id="user-a",
            channel_id="chan-a",
            content="newer by id",
            created_at=datetime(2026, 1, 3, tzinfo=UTC),
            metadata={"source": "stub", "channel_id": "chan-a", "author_id": "user-a"},
        ),
    )
    command_store.put(
        "999",
        DiscordCommandEnvelope(
            command_id="999",
            author_id="user-b",
            channel_id="chan-b",
            content="older by id",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            metadata={"source": "stub", "channel_id": "chan-b", "author_id": "user-b"},
        ),
    )

    service = StubDiscordService(command_store)

    all_commands = service.get_commands()
    filtered_commands = service.get_commands(since_cursor="999")

    assert [command.command_id for command in all_commands] == ["999", "1000"]
    assert [command.command_id for command in filtered_commands] == ["1000"]


def test_get_commands_rejects_non_numeric_since_cursor(tmp_path: Path) -> None:
    command_store = JsonStore(tmp_path / "discord.json", DiscordCommandEnvelope)
    command_store.put(
        "100",
        DiscordCommandEnvelope(
            command_id="100",
            author_id="user-a",
            channel_id="chan-a",
            content="older",
            metadata={"source": "stub", "channel_id": "chan-a", "author_id": "user-a"},
        ),
    )
    service = StubDiscordService(command_store)

    with pytest.raises(ValueError, match=r"Invalid since_cursor 'abc'"):
        service.get_commands(since_cursor="abc")


def test_get_commands_returns_deterministic_cross_channel_order_by_snowflake(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command_store = JsonStore(tmp_path / "discord.json", DiscordCommandEnvelope)
    command_store.put(
        "5000",
        DiscordCommandEnvelope(
            command_id="5000",
            author_id="seed-user",
            channel_id="chan-z",
            content="seed command to discover channel",
            metadata={
                "source": "discord_bot_api",
                "channel_id": "chan-z",
                "author_id": "seed-user",
            },
        ),
    )
    command_store.put(
        "4000",
        DiscordCommandEnvelope(
            command_id="4000",
            author_id="seed-user",
            channel_id="chan-a",
            content="seed command to discover channel",
            metadata={
                "source": "discord_bot_api",
                "channel_id": "chan-a",
                "author_id": "seed-user",
            },
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/channels/chan-a/messages"):
            return httpx.Response(
                200,
                json=[{"id": "1000", "content": "from-a", "author": {"id": "user-a"}}],
                request=request,
            )
        if request.url.path.endswith("/channels/chan-z/messages"):
            return httpx.Response(
                200,
                json=[{"id": "999", "content": "from-z", "author": {"id": "user-z"}}],
                request=request,
            )
        raise AssertionError(f"Unexpected path: {request.url.path}")

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)

    service = DiscordBotApiService(
        command_store,
        token="test-token",
        api_base_url="https://discord.test/api/v10",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_ids_provider=lambda: ["chan-a", "chan-z"],
    )

    commands = service.get_commands(since_cursor="998")

    assert [command.command_id for command in commands] == ["999", "1000", "4000", "5000"]


def test_discord_bot_api_preserves_attachment_metadata_and_downloads_host_side(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/channels/chan-a/messages"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "1000",
                        "content": "see attached",
                        "author": {"id": "user-a"},
                        "guild_id": "guild-1",
                        "attachments": [
                            {
                                "id": "att-1",
                                "filename": "report.txt",
                                "content_type": "text/plain",
                                "size": 11,
                                "url": "https://cdn.discord.test/att-1",
                                "content": "must-not-store",
                            }
                        ],
                    }
                ],
                request=request,
            )
        if str(request.url) == "https://cdn.discord.test/att-1":
            return httpx.Response(
                200,
                content=b"hello world",
                headers={"content-type": "text/plain"},
                request=request,
            )
        raise AssertionError(f"Unexpected URL: {request.url}")

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)
    service = make_service(tmp_path, transport)

    messages = service.get_commands()

    assert messages[0].metadata["attachments"] == [
        {
            "id": "att-1",
            "filename": "report.txt",
            "content_type": "text/plain",
            "size": 11,
            "url": "https://cdn.discord.test/att-1",
        }
    ]
    assert "content" not in messages[0].metadata["attachments"][0]
    assert service.list_attachments("1000", channel_id="chan-a")[0]["filename"] == "report.txt"
    downloaded = service.download_attachment("1000", "att-1", channel_id="chan-a")
    assert downloaded["content"] == b"hello world"
    assert downloaded["mime_type"] == "text/plain"
    assert requests[-1].headers["Authorization"] == "Bot test-token"

    with pytest.raises(ValueError, match="maximum size of 10 bytes"):
        service.download_attachment(
            "1000",
            "att-1",
            channel_id="chan-a",
            max_bytes=10,
        )


def test_discord_attachment_listing_enforces_channel_allowlist(tmp_path: Path) -> None:
    command_store = JsonStore(tmp_path / "discord.json", DiscordCommandEnvelope)
    command_store.put(
        "1000",
        DiscordCommandEnvelope(
            command_id="1000",
            author_id="user-a",
            channel_id="chan-b",
            content="blocked",
            metadata={"attachments": [{"id": "att-1", "filename": "x.txt"}]},
        ),
    )
    service = DiscordBotApiService(
        command_store,
        token="test-token",
        api_base_url="https://discord.test/api/v10",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_ids_provider=lambda: ["chan-a"],
    )

    with pytest.raises(ValueError, match="not allowlisted"):
        service.list_attachments("1000", channel_id="chan-b")


def test_discord_post_file_uses_multipart_bot_api_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    artifact_path = tmp_path / "upload.txt"
    artifact_path.write_text("upload-body", encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path.endswith("/channels/chan-a/messages")
        assert request.headers["Authorization"] == "Bot test-token"
        assert request.headers["content-type"].startswith("multipart/form-data")
        body = request.read()
        assert b'name="payload_json"' in body
        assert b'"content": "file incoming"' in body
        assert b'name="files[0]"' in body
        assert b'filename="upload.txt"' in body
        assert b"upload-body" in body
        return httpx.Response(
            200,
            json={
                "id": "9000",
                "content": "file incoming",
                "author": {"id": "bot-1"},
                "attachments": [{"id": "new-att", "filename": "upload.txt", "size": 11}],
            },
            request=request,
        )

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)
    service = make_service(tmp_path, transport)

    result = service.post_file(
        "file incoming",
        "chan-a",
        "session-1",
        [{"path": str(artifact_path), "filename": "upload.txt", "mime_type": "text/plain"}],
    )

    assert result["status"] == "posted"
    assert result["message_id"] == "9000"
    assert result["artifact_count"] == 1
    assert len(requests) == 1


def test_discord_resolved_identity_lazily_provisions_and_reuses_avatar_webhook(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    provisioning_payloads: list[dict[str, object]] = []
    webhook_payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/channels/chan-a/webhooks"):
            assert request.headers["Authorization"] == "Bot test-token"
            provisioning_payloads.append(json.loads(request.read()))
            return httpx.Response(
                200,
                json={"id": "webhook-1", "token": "webhook-token"},
                request=request,
            )
        if request.url.path.endswith("/webhooks/webhook-1/webhook-token"):
            assert "Authorization" not in request.headers
            payload = json.loads(request.read())
            webhook_payloads.append(payload)
            return httpx.Response(
                200,
                json={
                    "id": str(9000 + len(webhook_payloads)),
                    "content": payload["content"],
                    "author": {"id": "webhook-1"},
                },
                request=request,
            )
        raise AssertionError(f"Unexpected URL: {request.url}")

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)
    command_store = JsonStore(tmp_path / "discord.json", DiscordCommandEnvelope)
    webhook_store_path = tmp_path / "discord-webhooks.json"
    service = DiscordBotApiService(
        command_store,
        token="test-token",
        api_base_url="https://discord.test/api/v10",
        poll_limit=25,
        timeout_seconds=5.0,
        retry_count=0,
        allowed_channel_ids_provider=lambda: ["chan-a"],
        bot_token_env="DISCORD_BOT_TOKEN",
        webhook_store_path=webhook_store_path,
    )
    identity = OutboundIdentity(
        display_name="Build Agent",
        source="test",
        avatar=make_webp_avatar(),
    )

    first = service.post_status_with_identity(
        "first",
        "chan-a",
        "session-1",
        identity=identity,
        identity_status="resolved",
    )
    second = service.post_status_with_identity(
        "second",
        "chan-a",
        "session-1",
        identity=identity,
        identity_status="resolved",
    )

    assert first["transport"] == "webhook"
    assert first["persona_name_applied"] is True
    assert first["persona_avatar_applied"] is True
    assert second["transport"] == "webhook"
    assert len([request for request in requests if "/channels/" in request.url.path]) == 1
    assert len(provisioning_payloads) == 1
    assert str(provisioning_payloads[0]["avatar"]).startswith("data:image/png;base64,")
    assert [payload["username"] for payload in webhook_payloads] == [
        "Build Agent",
        "Build Agent",
    ]
    assert all("avatar_url" not in payload for payload in webhook_payloads)
    status = service.webhook_status("chan-a")
    assert status["persona_webhook_count"] == 1
    assert status["persona_webhooks"][0]["avatar_sha256"] == identity.avatar.sha256
    assert webhook_store_path.stat().st_mode & 0o777 == 0o600


def test_discord_resolved_identity_routes_parent_webhook_to_child_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/channels/parent-a/webhooks"):
            return httpx.Response(
                200,
                json={"id": "webhook-1", "token": "webhook-token"},
                request=request,
            )
        if request.url.path.endswith("/webhooks/webhook-1/webhook-token"):
            return httpx.Response(
                200,
                json={"id": "message-1", "content": "reply", "author": {"id": "webhook-1"}},
                request=request,
            )
        raise AssertionError(str(request.url))

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    service = DiscordBotApiService(
        JsonStore(tmp_path / "discord.json", DiscordCommandEnvelope),
        token="test-token",
        api_base_url="https://discord.test/api/v10",
        poll_limit=25,
        timeout_seconds=5.0,
        retry_count=0,
        allowed_channel_ids_provider=lambda: ["thread-a"],
        allowed_webhook_channel_ids_provider=lambda: ["parent-a"],
        bot_token_env="DISCORD_BOT_TOKEN",
        webhook_store_path=tmp_path / "discord-webhooks.json",
    )

    result = service.post_status_with_identity(
        "reply",
        "thread-a",
        "session-1",
        identity=OutboundIdentity(display_name="Support", source="test"),
        identity_status="resolved",
        webhook_channel_id="parent-a",
        thread_id="thread-a",
    )

    webhook_request = next(
        request for request in requests if "/webhooks/webhook-1/webhook-token" in request.url.path
    )
    assert webhook_request.url.params["wait"] == "true"
    assert webhook_request.url.params["thread_id"] == "thread-a"
    assert result["channel_id"] == "thread-a"
    with pytest.raises(ValueError, match="Discord channel is not allowlisted"):
        service.post_status("not allowed", "parent-a", "session-1")
    with pytest.raises(ValueError, match="Discord webhook channel is not allowlisted"):
        service.post_status_with_identity(
            "wrong parent",
            "thread-a",
            "session-1",
            identity=OutboundIdentity(display_name="Support", source="test"),
            identity_status="resolved",
            webhook_channel_id="parent-b",
            thread_id="thread-a",
        )


def test_discord_resolved_identity_file_routes_parent_webhook_to_child_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    artifact_path = tmp_path / "evidence.txt"
    artifact_path.write_text("evidence", encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/channels/parent-a/webhooks"):
            return httpx.Response(
                200,
                json={"id": "webhook-1", "token": "webhook-token"},
                request=request,
            )
        if request.url.path.endswith("/webhooks/webhook-1/webhook-token"):
            assert request.headers["content-type"].startswith("multipart/form-data")
            body = request.read()
            assert b'filename="evidence.txt"' in body
            assert b"evidence" in body
            return httpx.Response(
                200,
                json={"id": "message-1", "content": "reply", "author": {"id": "webhook-1"}},
                request=request,
            )
        raise AssertionError(str(request.url))

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    service = DiscordBotApiService(
        JsonStore(tmp_path / "discord.json", DiscordCommandEnvelope),
        token="test-token",
        api_base_url="https://discord.test/api/v10",
        poll_limit=25,
        timeout_seconds=5.0,
        retry_count=0,
        allowed_channel_ids_provider=lambda: ["thread-a"],
        allowed_webhook_channel_ids_provider=lambda: ["parent-a"],
        bot_token_env="DISCORD_BOT_TOKEN",
        webhook_store_path=tmp_path / "discord-webhooks.json",
    )

    result = service.post_file_with_identity(
        "reply",
        "thread-a",
        "session-1",
        [
            {
                "path": str(artifact_path),
                "filename": "evidence.txt",
                "mime_type": "text/plain",
            }
        ],
        identity=OutboundIdentity(display_name="Support", source="test"),
        identity_status="resolved",
        webhook_channel_id="parent-a",
        thread_id="thread-a",
    )

    webhook_request = next(
        request for request in requests if "/webhooks/webhook-1/webhook-token" in request.url.path
    )
    assert webhook_request.url.params["thread_id"] == "thread-a"
    assert result["channel_id"] == "thread-a"
    assert result["transport"] == "webhook"
    assert result["artifact_count"] == 1


def test_discord_in_flight_send_does_not_restore_admin_recreated_webhook(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_webhooks = 0
    old_send_started = threading.Event()
    release_old_send = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal created_webhooks
        if request.url.path.endswith("/channels/chan-a/webhooks"):
            created_webhooks += 1
            return httpx.Response(
                200,
                json={
                    "id": f"webhook-{created_webhooks}",
                    "token": f"token-{created_webhooks}",
                },
                request=request,
            )
        if request.url.path.endswith("/webhooks/webhook-1/token-1"):
            old_send_started.set()
            if not release_old_send.wait(timeout=5.0):
                raise AssertionError("Timed out waiting to complete the old webhook send")
            return httpx.Response(
                200,
                json={"id": "old-message", "content": "old"},
                request=request,
            )
        if request.method == "DELETE" and request.url.path.endswith("/webhooks/webhook-1"):
            return httpx.Response(204, request=request)
        raise AssertionError(f"Unexpected URL: {request.url}")

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    service = DiscordBotApiService(
        JsonStore(tmp_path / "discord.json", DiscordCommandEnvelope),
        token="test-token",
        api_base_url="https://discord.test/api/v10",
        poll_limit=25,
        timeout_seconds=5.0,
        retry_count=0,
        allowed_channel_ids_provider=lambda: ["chan-a"],
        bot_token_env="DISCORD_BOT_TOKEN",
        webhook_store_path=tmp_path / "discord-webhooks.json",
    )
    service.provision_webhook("chan-a")
    send_results: list[dict[str, object]] = []
    send_failures: list[Exception] = []

    def send_with_old_webhook() -> None:
        try:
            send_results.append(service.test_webhook("chan-a", message="old"))
        except Exception as exc:  # pragma: no cover - asserted in the parent thread
            send_failures.append(exc)

    sender = threading.Thread(target=send_with_old_webhook)
    sender.start()
    try:
        assert old_send_started.wait(timeout=5.0)
        replacement = service.provision_webhook("chan-a", force=True)
        assert replacement["webhook_id"] == "webhook-2"
    finally:
        release_old_send.set()
        sender.join(timeout=5.0)

    assert not sender.is_alive()
    assert send_failures == []
    assert send_results[0]["message_id"] == "old-message"
    assert service.webhook_status("chan-a")["webhook_id"] == "webhook-2"


def test_discord_persona_webhook_cache_evicts_least_recently_used_avatar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_webhooks = 0
    deleted_webhooks: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal created_webhooks
        if request.url.path.endswith("/channels/chan-a/webhooks"):
            created_webhooks += 1
            return httpx.Response(
                200,
                json={
                    "id": f"webhook-{created_webhooks}",
                    "token": f"token-{created_webhooks}",
                },
                request=request,
            )
        if request.method == "DELETE" and request.url.path.startswith("/api/v10/webhooks/webhook-"):
            deleted_webhooks.append(request.url.path.rsplit("/", 1)[-1])
            return httpx.Response(204, request=request)
        if "/webhooks/webhook-" in request.url.path:
            payload = json.loads(request.read())
            webhook_id = request.url.path.split("/webhooks/", 1)[1].split("/", 1)[0]
            return httpx.Response(
                200,
                json={
                    "id": f"message-{len(payload['content'])}-{webhook_id}",
                    "content": payload["content"],
                    "author": {"id": webhook_id},
                },
                request=request,
            )
        raise AssertionError(f"Unexpected URL: {request.url}")

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    service = DiscordBotApiService(
        JsonStore(tmp_path / "discord.json", DiscordCommandEnvelope),
        token="test-token",
        api_base_url="https://discord.test/api/v10",
        poll_limit=25,
        timeout_seconds=5.0,
        retry_count=0,
        allowed_channel_ids_provider=lambda: ["chan-a"],
        bot_token_env="DISCORD_BOT_TOKEN",
        webhook_store_path=tmp_path / "discord-webhooks.json",
        persona_webhook_cache_limit=2,
    )
    first = OutboundIdentity(
        display_name="First",
        source="test",
        avatar=make_webp_avatar("#ff0000"),
    )
    second = OutboundIdentity(
        display_name="Second",
        source="test",
        avatar=make_webp_avatar("#00ff00"),
    )
    third = OutboundIdentity(
        display_name="Third",
        source="test",
        avatar=make_webp_avatar("#0000ff"),
    )

    for message, identity in (("one", first), ("two", second), ("one-again", first)):
        service.post_status_with_identity(
            message,
            "chan-a",
            "session-1",
            identity=identity,
            identity_status="resolved",
        )
    service.post_status_with_identity(
        "three",
        "chan-a",
        "session-1",
        identity=third,
        identity_status="resolved",
    )

    status = service.webhook_status("chan-a")
    assert created_webhooks == 3
    assert deleted_webhooks == ["webhook-2"]
    assert status["persona_webhook_count"] == 2
    assert {item["avatar_sha256"] for item in status["persona_webhooks"]} == {
        first.avatar.sha256,
        third.avatar.sha256,
    }


def test_discord_in_flight_send_does_not_restore_lru_evicted_webhook(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_webhooks = 0
    first_webhook_sends = 0
    old_send_started = threading.Event()
    release_old_send = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal created_webhooks, first_webhook_sends
        if request.url.path.endswith("/channels/chan-a/webhooks"):
            created_webhooks += 1
            return httpx.Response(
                200,
                json={
                    "id": f"webhook-{created_webhooks}",
                    "token": f"token-{created_webhooks}",
                },
                request=request,
            )
        if request.url.path.endswith("/webhooks/webhook-1/token-1"):
            first_webhook_sends += 1
            if first_webhook_sends > 1:
                old_send_started.set()
                if not release_old_send.wait(timeout=5.0):
                    raise AssertionError("Timed out waiting to complete the evicted webhook send")
            return httpx.Response(
                200,
                json={"id": f"old-message-{first_webhook_sends}", "content": "old"},
                request=request,
            )
        if request.method == "DELETE" and request.url.path.endswith("/webhooks/webhook-1"):
            return httpx.Response(204, request=request)
        if request.url.path.endswith("/webhooks/webhook-2/token-2"):
            return httpx.Response(
                200,
                json={"id": "new-message", "content": "new"},
                request=request,
            )
        raise AssertionError(f"Unexpected URL: {request.url}")

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    service = DiscordBotApiService(
        JsonStore(tmp_path / "discord.json", DiscordCommandEnvelope),
        token="test-token",
        api_base_url="https://discord.test/api/v10",
        poll_limit=25,
        timeout_seconds=5.0,
        retry_count=0,
        allowed_channel_ids_provider=lambda: ["chan-a"],
        bot_token_env="DISCORD_BOT_TOKEN",
        webhook_store_path=tmp_path / "discord-webhooks.json",
        persona_webhook_cache_limit=1,
    )
    first = OutboundIdentity(
        display_name="First",
        source="test",
        avatar=make_webp_avatar("#ff0000"),
    )
    second = OutboundIdentity(
        display_name="Second",
        source="test",
        avatar=make_webp_avatar("#00ff00"),
    )
    service.post_status_with_identity(
        "initial",
        "chan-a",
        "session-1",
        identity=first,
        identity_status="resolved",
    )
    send_failures: list[Exception] = []

    def send_with_old_webhook() -> None:
        try:
            service.post_status_with_identity(
                "old",
                "chan-a",
                "session-1",
                identity=first,
                identity_status="resolved",
            )
        except Exception as exc:  # pragma: no cover - asserted in the parent thread
            send_failures.append(exc)

    sender = threading.Thread(target=send_with_old_webhook)
    sender.start()
    try:
        assert old_send_started.wait(timeout=5.0)
        service.post_status_with_identity(
            "new",
            "chan-a",
            "session-1",
            identity=second,
            identity_status="resolved",
        )
    finally:
        release_old_send.set()
        sender.join(timeout=5.0)

    assert not sender.is_alive()
    assert send_failures == []
    status = service.webhook_status("chan-a")
    assert status["persona_webhook_count"] == 1
    assert status["persona_webhooks"][0]["avatar_sha256"] == second.avatar.sha256


def test_discord_invalid_webp_avatar_keeps_resolved_name_without_public_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provisioning_payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/channels/chan-a/webhooks"):
            provisioning_payloads.append(json.loads(request.read()))
            return httpx.Response(
                200,
                json={"id": "webhook-1", "token": "webhook-token"},
                request=request,
            )
        if request.url.path.endswith("/webhooks/webhook-1/webhook-token"):
            payload = json.loads(request.read())
            return httpx.Response(
                200,
                json={
                    "id": "message-1",
                    "content": payload["content"],
                    "author": {"id": "webhook-1"},
                },
                request=request,
            )
        raise AssertionError(f"Unexpected URL: {request.url}")

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    service = DiscordBotApiService(
        JsonStore(tmp_path / "discord.json", DiscordCommandEnvelope),
        token="test-token",
        api_base_url="https://discord.test/api/v10",
        poll_limit=25,
        timeout_seconds=5.0,
        retry_count=0,
        allowed_channel_ids_provider=lambda: ["chan-a"],
        bot_token_env="DISCORD_BOT_TOKEN",
        webhook_store_path=tmp_path / "discord-webhooks.json",
    )

    result = service.post_status_with_identity(
        "hello",
        "chan-a",
        "session-1",
        identity=OutboundIdentity(
            display_name="Named Agent",
            source="test",
            avatar=OutboundAvatar.from_bytes(b"not-webp", mime_type="image/webp"),
        ),
        identity_status="resolved",
    )

    assert result["transport"] == "webhook"
    assert result["persona_name_applied"] is True
    assert result["persona_avatar_applied"] is False
    assert result["fallback_reason"] == "persona_avatar_unavailable:ValueError"
    assert provisioning_payloads == [{"name": "Evander"}]


def test_discord_resolved_identity_falls_back_to_bot_on_definite_webhook_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/channels/chan-a/webhooks"):
            return httpx.Response(403, json={"message": "forbidden"}, request=request)
        if request.url.path.endswith("/channels/chan-a/messages"):
            return httpx.Response(
                200,
                json={
                    "id": "bot-message-1",
                    "content": "fallback",
                    "author": {"id": "bot-1"},
                },
                request=request,
            )
        raise AssertionError(f"Unexpected URL: {request.url}")

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    service = DiscordBotApiService(
        JsonStore(tmp_path / "discord.json", DiscordCommandEnvelope),
        token="test-token",
        api_base_url="https://discord.test/api/v10",
        poll_limit=25,
        timeout_seconds=5.0,
        retry_count=0,
        allowed_channel_ids_provider=lambda: ["chan-a"],
        bot_token_env="DISCORD_BOT_TOKEN",
        webhook_store_path=tmp_path / "discord-webhooks.json",
    )

    result = service.post_status_with_identity(
        "fallback",
        "chan-a",
        "session-1",
        identity=OutboundIdentity(display_name="Agent", source="test"),
        identity_status="resolved",
    )

    assert result["transport"] == "bot_api"
    assert result["identity_status"] == "resolved"
    assert result["persona_name_applied"] is False
    assert result["fallback_reason"] == "webhook_http_403"


def test_discord_missing_resolved_identity_uses_bot_without_provisioning_webhook(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path.endswith("/channels/chan-a/messages")
        return httpx.Response(
            200,
            json={
                "id": "bot-message-2",
                "content": "fallback",
                "author": {"id": "bot-1"},
            },
            request=request,
        )

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    service = DiscordBotApiService(
        JsonStore(tmp_path / "discord.json", DiscordCommandEnvelope),
        token="test-token",
        api_base_url="https://discord.test/api/v10",
        poll_limit=25,
        timeout_seconds=5.0,
        retry_count=0,
        allowed_channel_ids_provider=lambda: ["chan-a"],
        bot_token_env="DISCORD_BOT_TOKEN",
        webhook_store_path=tmp_path / "discord-webhooks.json",
    )

    result = service.post_status_with_identity(
        "fallback",
        "chan-a",
        "session-1",
        identity=None,
        identity_status="not_found",
    )

    assert result["transport"] == "bot_api"
    assert result["fallback_reason"] == "identity_not_found"
    assert len(requests) == 1


def test_discord_resolved_identity_recreates_stale_webhook_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_webhooks = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal created_webhooks
        if request.url.path.endswith("/channels/chan-a/webhooks"):
            created_webhooks += 1
            return httpx.Response(
                200,
                json={
                    "id": f"webhook-{created_webhooks}",
                    "token": f"token-{created_webhooks}",
                },
                request=request,
            )
        if request.url.path.endswith("/webhooks/webhook-1/token-1"):
            return httpx.Response(404, json={"message": "unknown webhook"}, request=request)
        if request.url.path.endswith("/webhooks/webhook-2/token-2"):
            return httpx.Response(
                200,
                json={
                    "id": "persona-message-1",
                    "content": "recovered",
                    "author": {"id": "webhook-2"},
                },
                request=request,
            )
        raise AssertionError(f"Unexpected URL: {request.url}")

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    service = DiscordBotApiService(
        JsonStore(tmp_path / "discord.json", DiscordCommandEnvelope),
        token="test-token",
        api_base_url="https://discord.test/api/v10",
        poll_limit=25,
        timeout_seconds=5.0,
        retry_count=0,
        allowed_channel_ids_provider=lambda: ["chan-a"],
        bot_token_env="DISCORD_BOT_TOKEN",
        webhook_store_path=tmp_path / "discord-webhooks.json",
    )

    result = service.post_status_with_identity(
        "recovered",
        "chan-a",
        "session-1",
        identity=OutboundIdentity(
            display_name="Agent",
            source="test",
            avatar=make_webp_avatar(),
        ),
        identity_status="resolved",
    )

    assert result["transport"] == "webhook"
    assert result["message_id"] == "persona-message-1"
    assert created_webhooks == 2
    assert service.webhook_status("chan-a")["webhook_id"] == "webhook-2"


def test_discord_stale_404_does_not_delete_concurrent_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_webhooks = 0
    webhook_store = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal created_webhooks
        if request.url.path.endswith("/channels/chan-a/webhooks"):
            created_webhooks += 1
            return httpx.Response(
                200,
                json={"id": "webhook-1", "token": "token-1"},
                request=request,
            )
        if request.url.path.endswith("/webhooks/webhook-1/token-1"):
            assert webhook_store is not None
            current = webhook_store.get(
                api_base_url="https://discord.test/api/v10",
                bot_token_env="DISCORD_BOT_TOKEN",
                channel_id="chan-a",
            )
            assert current is not None
            webhook_store.put(
                current.model_copy(update={"webhook_id": "webhook-2", "token": "token-2"})
            )
            return httpx.Response(404, json={"message": "unknown webhook"}, request=request)
        if request.url.path.endswith("/webhooks/webhook-2/token-2"):
            return httpx.Response(
                200,
                json={"id": "replacement-message", "content": "recovered"},
                request=request,
            )
        raise AssertionError(f"Unexpected URL: {request.url}")

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    service = DiscordBotApiService(
        JsonStore(tmp_path / "discord.json", DiscordCommandEnvelope),
        token="test-token",
        api_base_url="https://discord.test/api/v10",
        poll_limit=25,
        timeout_seconds=5.0,
        retry_count=0,
        allowed_channel_ids_provider=lambda: ["chan-a"],
        bot_token_env="DISCORD_BOT_TOKEN",
        webhook_store_path=tmp_path / "discord-webhooks.json",
    )
    webhook_store = service.webhook_store
    assert webhook_store is not None

    result = service.test_webhook("chan-a", message="recovered")

    assert result["message_id"] == "replacement-message"
    assert created_webhooks == 1
    assert service.webhook_status("chan-a")["webhook_id"] == "webhook-2"


def test_discord_webhook_errors_redact_credential_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/channels/chan-a/webhooks"):
            return httpx.Response(
                200,
                json={"id": "webhook-1", "token": "super-secret-webhook-token"},
                request=request,
            )
        if request.url.path.endswith("/webhooks/webhook-1/super-secret-webhook-token"):
            return httpx.Response(500, json={"message": "failure"}, request=request)
        raise AssertionError(f"Unexpected URL: {request.url}")

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    service = DiscordBotApiService(
        JsonStore(tmp_path / "discord.json", DiscordCommandEnvelope),
        token="test-token",
        api_base_url="https://discord.test/api/v10",
        poll_limit=25,
        timeout_seconds=5.0,
        retry_count=0,
        allowed_channel_ids_provider=lambda: ["chan-a"],
        bot_token_env="DISCORD_BOT_TOKEN",
        webhook_store_path=tmp_path / "discord-webhooks.json",
    )

    with pytest.raises(httpx.HTTPStatusError) as raised:
        service.post_status_with_identity(
            "failure",
            "chan-a",
            "session-1",
            identity=OutboundIdentity(display_name="Agent", source="test"),
            identity_status="resolved",
        )

    assert "super-secret-webhook-token" not in str(raised.value)
    assert "super-secret-webhook-token" not in str(raised.value.request.url)
    assert "super-secret-webhook-token" not in "".join(traceback.format_exception(raised.value))
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__ is True
    assert "redacted" in str(raised.value.request.url)
    assert service.webhook_status("chan-a")["last_error"] == "http_500"
