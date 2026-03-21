from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from agent_sandbox.models import DiscordCommandEnvelope
from agent_sandbox.services import DiscordBotApiService, StubDiscordService
from agent_sandbox.storage import JsonStore


def make_service(tmp_path: Path, transport: httpx.BaseTransport) -> DiscordBotApiService:
    command_store = JsonStore(tmp_path / "discord.json", DiscordCommandEnvelope)
    return DiscordBotApiService(
        command_store,
        token="test-token",
        api_base_url="https://discord.test/api/v10",
        poll_limit=25,
        timeout_seconds=5.0,
    )


def test_get_commands_polls_known_channels_and_filters_since_cursor(
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
    monkeypatch.setattr(
        "agent_sandbox.services.httpx.Client",
        lambda timeout: httpx.Client(timeout=timeout, transport=transport),
    )

    service = DiscordBotApiService(
        command_store,
        token="test-token",
        api_base_url="https://discord.test/api/v10",
        poll_limit=25,
        timeout_seconds=5.0,
    )

    commands = service.get_commands(since_cursor="180")

    assert [command.command_id for command in commands] == ["200", "300"]
    assert all(request.headers["Authorization"] == "Bot test-token" for request in requests)
    assert all(request.url.params.get("limit") == "25" for request in requests)


def test_post_status_raises_on_rate_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"message": "You are being rate limited."}, request=request)

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        "agent_sandbox.services.httpx.Client",
        lambda timeout: httpx.Client(timeout=timeout, transport=transport),
    )

    service = make_service(tmp_path, transport)

    with pytest.raises(httpx.HTTPStatusError):
        service.post_status("status", "chan-a", "session-1")


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
    monkeypatch.setattr(
        "agent_sandbox.services.httpx.Client",
        lambda timeout: httpx.Client(timeout=timeout, transport=transport),
    )

    service = DiscordBotApiService(
        command_store,
        token="test-token",
        api_base_url="https://discord.test/api/v10",
        poll_limit=25,
        timeout_seconds=5.0,
    )

    with pytest.raises(httpx.ReadTimeout):
        service.get_commands()


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
    monkeypatch.setattr(
        "agent_sandbox.services.httpx.Client",
        lambda timeout: httpx.Client(timeout=timeout, transport=transport),
    )

    service = DiscordBotApiService(
        command_store,
        token="test-token",
        api_base_url="https://discord.test/api/v10",
        poll_limit=25,
        timeout_seconds=5.0,
    )

    commands = service.get_commands(since_cursor="998")

    assert [command.command_id for command in commands] == ["999", "1000", "4000", "5000"]
