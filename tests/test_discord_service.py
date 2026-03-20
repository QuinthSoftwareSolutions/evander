from __future__ import annotations

from datetime import UTC, datetime
from inspect import signature
from pathlib import Path

import httpx
import pytest

from agent_sandbox.models import DiscordCommandEnvelope
from agent_sandbox.services import DiscordBotApiService
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
    monkeypatch.setattr(
        "agent_sandbox.services.httpx.Client",
        lambda timeout: httpx.Client(timeout=timeout, transport=transport),
    )

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
    monkeypatch.setattr(
        "agent_sandbox.services.httpx.Client",
        lambda timeout: httpx.Client(timeout=timeout, transport=transport),
    )

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

    first_poll = service.get_commands(since_cursor="2")
    second_poll = service.get_commands(since_cursor="4")

    assert [command.command_id for command in first_poll] == ["3", "4"]
    assert second_poll == []


def test_discord_service_contract_signatures_are_stable() -> None:
    get_commands_sig = signature(DiscordBotApiService.get_commands)
    post_status_sig = signature(DiscordBotApiService.post_status)

    assert list(get_commands_sig.parameters) == ["self", "since_cursor"]
    assert get_commands_sig.parameters["since_cursor"].default is None
    assert list(post_status_sig.parameters) == ["self", "message", "channel_id", "session_id"]


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
