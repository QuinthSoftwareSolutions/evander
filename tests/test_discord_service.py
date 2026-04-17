from __future__ import annotations

from datetime import UTC, datetime
from inspect import signature
from pathlib import Path

import httpx
import pytest

from agent_sandbox.models import DiscordCommandEnvelope
from agent_sandbox.services import DiscordBotApiService, StubDiscordService
from agent_sandbox.storage import JsonStore


def patch_httpx_client(
    monkeypatch: pytest.MonkeyPatch,
    transport: httpx.BaseTransport,
) -> None:
    real_client = httpx.Client
    monkeypatch.setattr(
        "agent_sandbox.services.httpx.Client",
        lambda timeout: real_client(timeout=timeout, transport=transport),
    )


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
    assert requests[0].url.path.endswith(
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
    monkeypatch.setattr("agent_sandbox.services.time.sleep", lambda _: None)
    monkeypatch.setattr("agent_sandbox.services.random.uniform", lambda low, high: low)

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
    
    monkeypatch.setattr("agent_sandbox.services.time.sleep", lambda _: None)

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
