from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from evander.models import TeamsMessageEnvelope
from evander.services import GraphTeamsService, StubTeamsService, TeamsAuthenticationError
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


class RecordingTeamsMessageStore:
    def __init__(self) -> None:
        self.records: dict[str, TeamsMessageEnvelope] = {}
        self.put_calls = 0
        self.put_many_calls = 0

    def get(self, cursor: str) -> TeamsMessageEnvelope | None:
        return self.records.get(cursor)

    def put(self, cursor: str, envelope: TeamsMessageEnvelope) -> None:
        self.put_calls += 1
        self.records[cursor] = envelope

    def put_many(self, records: dict[str, TeamsMessageEnvelope]) -> None:
        self.put_many_calls += 1
        self.records.update(records)

    def read_all(self) -> dict[str, TeamsMessageEnvelope]:
        return dict(self.records)


def test_teams_services_default_and_override_read_reaction_emoji(tmp_path: Path) -> None:
    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    stub_default = StubTeamsService(store)
    stub_override = StubTeamsService(store, read_reaction_emoji="heart")
    graph_default = GraphTeamsService(
        store,
        token="graph-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_targets_provider=lambda: ["team-a/channel-1"],
    )
    graph_override = GraphTeamsService(
        store,
        token="graph-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_targets_provider=lambda: ["team-a/channel-1"],
        read_reaction_emoji="heart",
    )

    assert stub_default.read_reaction_emoji == "👀"
    assert stub_override.read_reaction_emoji == "heart"
    assert graph_default.read_reaction_emoji == "👀"
    assert graph_override.read_reaction_emoji == "heart"


def test_graph_teams_service_creates_one_on_one_chat_for_allowlisted_user(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1.0/me":
            return httpx.Response(200, json={"id": "sender-user-id"}, request=request)
        if request.url.path == "/v1.0/chats":
            payload = json.loads(request.content.decode("utf-8"))
            assert payload["chatType"] == "oneOnOne"
            assert [
                member["user@odata.bind"] for member in payload["members"]
            ] == [
                "https://graph.microsoft.com/v1.0/users('sender-user-id')",
                "https://graph.microsoft.com/v1.0/users('operator@example.com')",
            ]
            return httpx.Response(201, json={"id": "19:one-on-one@thread.v2"}, request=request)
        raise AssertionError(f"Unexpected path: {request.url.path}")

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)
    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    cache: dict[str, str] = {}
    service = GraphTeamsService(
        store,
        token="graph-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_user_targets_provider=lambda: ["user:operator@example.com"],
        user_chat_cache=cache,
        user_chat_cache_namespace="test-identity",
    )

    assert service.ensure_one_on_one_chat("operator@example.com") == (
        "chat:19:one-on-one@thread.v2"
    )
    assert service.ensure_one_on_one_chat("user:operator@example.com") == (
        "chat:19:one-on-one@thread.v2"
    )
    assert [request.url.path for request in requests] == ["/v1.0/me", "/v1.0/chats"]


def test_graph_teams_service_resolves_user_refs_to_graph_ids(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1.0/users/user-allowed":
            return httpx.Response(
                200,
                json={"id": "user-allowed", "userPrincipalName": "allowed@example.com"},
                request=request,
            )
        if request.url.path == "/v1.0/users/operator@example.com":
            return httpx.Response(
                200,
                json={"id": "user-operator", "mail": "operator@example.com"},
                request=request,
            )
        raise AssertionError(f"Unexpected path: {request.url.path}")

    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    service = GraphTeamsService(
        store,
        token="graph-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=25,
        timeout_seconds=5.0,
    )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert service.resolve_user_id(client, "user-allowed") == "user-allowed"
        assert service.resolve_user_id(client, "user:operator@example.com") == "user-operator"

    assert [request.url.path for request in requests] == [
        "/v1.0/users/user-allowed",
        "/v1.0/users/operator@example.com",
    ]
    assert all(request.url.params.get("$select") == "id,userPrincipalName,mail" for request in requests)


def test_graph_teams_service_resolves_mail_with_filter_fallback(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1.0/users/primary@example.com":
            return httpx.Response(404, json={"error": {"code": "Request_ResourceNotFound"}}, request=request)
        if request.url.path == "/v1.0/users":
            assert request.url.params.get("$filter") == (
                "mail eq 'primary@example.com' or "
                "userPrincipalName eq 'primary@example.com'"
            )
            return httpx.Response(
                200,
                json={"value": [{"id": "user-primary", "mail": "primary@example.com"}]},
                request=request,
            )
        raise AssertionError(f"Unexpected path: {request.url.path}")

    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    service = GraphTeamsService(
        store,
        token="graph-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=25,
        timeout_seconds=5.0,
    )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert service.resolve_user_id(client, "primary@example.com") == "user-primary"

    assert [request.url.path for request in requests] == [
        "/v1.0/users/primary@example.com",
        "/v1.0/users",
    ]


def test_graph_teams_service_returns_none_for_unresolved_mail(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1.0/users/missing@example.com":
            return httpx.Response(404, json={"error": {"code": "Request_ResourceNotFound"}}, request=request)
        if request.url.path == "/v1.0/users":
            return httpx.Response(200, json={"value": []}, request=request)
        raise AssertionError(f"Unexpected path: {request.url.path}")

    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    service = GraphTeamsService(
        store,
        token="graph-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=25,
        timeout_seconds=5.0,
    )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert service.resolve_user_id(client, "missing@example.com") is None


def test_graph_teams_service_rejects_non_allowlisted_user_chat_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = httpx.MockTransport(lambda request: pytest.fail(f"Unexpected request: {request.url}"))
    patch_httpx_client(monkeypatch, transport)
    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    service = GraphTeamsService(
        store,
        token="graph-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_user_targets_provider=lambda: ["user:operator@example.com"],
    )

    with pytest.raises(ValueError, match="Teams user target is not allowlisted"):
        service.ensure_one_on_one_chat("other@example.com")


def test_graph_teams_service_polls_allowlisted_channels_and_flattens_replies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if (
            request.url.path.endswith("/teams/team-a/channels/channel-1/messages")
            and request.url.params.get("$skiptoken") is None
        ):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "msg-1",
                            "createdDateTime": "2026-03-30T11:00:00Z",
                            "body": {"content": "<p>hello</p>", "contentType": "html"},
                            "from": {"user": {"id": "user-1"}},
                            "replies": [
                                {
                                    "id": "msg-2",
                                    "replyToId": "msg-1",
                                    "createdDateTime": "2026-03-30T11:01:00Z",
                                    "body": {"content": "reply", "contentType": "text"},
                                    "from": {"user": {"id": "user-2"}},
                                }
                            ],
                        }
                    ]
                },
                request=request,
            )
        raise AssertionError(f"Unexpected path: {request.url.path}")

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)
    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    service = GraphTeamsService(
        store,
        token="graph-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_targets_provider=lambda: ["team-a/channel-1"],
    )

    messages = service.get_messages()

    assert [message.cursor for message in messages] == [
        "team-a/channel-1/msg-1",
        "team-a/channel-1/msg-2",
    ]
    assert messages[0].thread_id == "msg-1"
    assert messages[1].thread_id == "msg-1"
    assert requests[0].headers["Authorization"] == "Bearer graph-token"
    assert requests[0].url.params.get("$top") == "25"
    assert requests[0].url.params.get("$expand") == "replies"


def test_graph_teams_service_batches_fetched_message_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/teams/team-a/channels/channel-1/messages"):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "msg-1",
                            "createdDateTime": "2026-03-30T11:00:00Z",
                            "body": {"content": "first", "contentType": "text"},
                            "from": {"user": {"id": "user-1"}},
                        },
                        {
                            "id": "msg-2",
                            "createdDateTime": "2026-03-30T11:01:00Z",
                            "body": {"content": "second", "contentType": "text"},
                            "from": {"user": {"id": "user-2"}},
                        },
                    ]
                },
                request=request,
            )
        raise AssertionError(f"Unexpected path: {request.url.path}")

    patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    store = RecordingTeamsMessageStore()
    service = GraphTeamsService(
        store,
        token="graph-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_targets_provider=lambda: ["team-a/channel-1"],
    )

    messages = service.get_messages()

    assert [message.cursor for message in messages] == [
        "team-a/channel-1/msg-1",
        "team-a/channel-1/msg-2",
    ]
    assert store.put_many_calls == 1
    assert store.put_calls == 0
    assert set(store.records) == {
        "team-a/channel-1/msg-1",
        "team-a/channel-1/msg-2",
    }


def test_graph_teams_service_uses_token_provider_for_requests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    token_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"value": []},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)
    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    service = GraphTeamsService(
        store,
        token_provider=lambda: token_calls.append("called") or "delegated-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_targets_provider=lambda: ["team-a/channel-1"],
    )

    messages = service.get_messages()

    assert messages == []
    assert token_calls == ["called"]
    assert requests[0].headers["Authorization"] == "Bearer delegated-token"
    assert service.token == "delegated-token"


def test_graph_teams_service_follows_pagination_for_channel_messages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if (
            request.url.path.endswith("/teams/team-a/channels/channel-1/messages")
            and request.url.params.get("$skiptoken") is None
        ):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "msg-1",
                            "createdDateTime": "2026-03-30T11:00:00Z",
                            "body": {"content": "first", "contentType": "text"},
                            "from": {"user": {"id": "user-1"}},
                        }
                    ],
                    "@odata.nextLink": "https://graph.microsoft.com/v1.0/teams/team-a/channels/channel-1/messages?$skiptoken=page-2",
                },
                request=request,
            )
        if request.url.params.get("$skiptoken") == "page-2":
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "msg-2",
                            "createdDateTime": "2026-03-30T11:01:00Z",
                            "body": {"content": "second", "contentType": "text"},
                            "from": {"user": {"id": "user-2"}},
                        }
                    ]
                },
                request=request,
            )
        raise AssertionError(f"Unexpected request URL: {request.url}")

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)
    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    service = GraphTeamsService(
        store,
        token="graph-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=1,
        history_page_cap=2,
        timeout_seconds=5.0,
        allowed_channel_targets_provider=lambda: ["team-a/channel-1"],
    )

    messages = service.get_messages(limit=2)

    assert [message.cursor for message in messages] == [
        "team-a/channel-1/msg-1",
        "team-a/channel-1/msg-2",
    ]
    assert len(requests) == 2
    assert requests[0].url.params.get("$top") == "1"
    assert requests[1].url.params.get("$skiptoken") == "page-2"
    assert requests[1].url.params.get("$top") is None


def test_graph_teams_service_caps_cursorless_history_pages_across_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/teams/team-a/channels/channel-1/messages"):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "msg-1",
                            "createdDateTime": "2026-03-30T11:00:00Z",
                            "body": {"content": "first", "contentType": "text"},
                            "from": {"user": {"id": "user-1"}},
                        }
                    ]
                },
                request=request,
            )
        raise AssertionError(f"Unexpected request URL: {request.url}")

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)
    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    service = GraphTeamsService(
        store,
        token="graph-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=25,
        history_page_cap=1,
        timeout_seconds=5.0,
        allowed_channel_targets_provider=lambda: ["team-a/channel-1", "team-a/channel-2"],
    )

    messages = service.get_messages()

    assert [message.cursor for message in messages] == ["team-a/channel-1/msg-1"]
    assert len(requests) == 1


def test_graph_teams_service_stops_pagination_when_history_deadline_expires(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if (
            request.url.path.endswith("/teams/team-a/channels/channel-1/messages")
            and request.url.params.get("$skiptoken") is None
        ):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "msg-1",
                            "createdDateTime": "2026-03-30T11:00:00Z",
                            "body": {"content": "first", "contentType": "text"},
                            "from": {"user": {"id": "user-1"}},
                        }
                    ],
                    "@odata.nextLink": "https://graph.microsoft.com/v1.0/teams/team-a/channels/channel-1/messages?$skiptoken=page-2",
                },
                request=request,
            )
        if request.url.params.get("$skiptoken") == "page-2":
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "msg-2",
                            "createdDateTime": "2026-03-30T11:01:00Z",
                            "body": {"content": "second", "contentType": "text"},
                            "from": {"user": {"id": "user-2"}},
                        }
                    ]
                },
                request=request,
            )
        raise AssertionError(f"Unexpected request URL: {request.url}")

    calls = 0

    def fake_monotonic() -> float:
        nonlocal calls
        calls += 1
        return 2.0 if calls >= 5 else 0.0

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)
    monkeypatch.setattr("evander.services.time.monotonic", fake_monotonic)
    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    service = GraphTeamsService(
        store,
        token="graph-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=1,
        history_page_cap=10,
        history_deadline_seconds=1.0,
        timeout_seconds=5.0,
        allowed_channel_targets_provider=lambda: ["team-a/channel-1"],
    )

    messages = service.get_messages(limit=10)

    assert [message.cursor for message in messages] == ["team-a/channel-1/msg-1"]
    assert len(requests) == 1


def test_graph_teams_service_targeted_read_only_fetches_requested_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/chats/chat-1/messages"):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "msg-chat",
                            "createdDateTime": "2026-03-30T11:00:00Z",
                            "body": {"content": "chat", "contentType": "text"},
                            "from": {"user": {"id": "user-1"}},
                        }
                    ]
                },
                request=request,
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)
    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    store.put(
        "team-a/channel-1/msg-cached",
        TeamsMessageEnvelope(
            cursor="team-a/channel-1/msg-cached",
            message_id="msg-cached",
            author_id="user-2",
            team_id="team-a",
            channel_id="channel-1",
            thread_id="msg-cached",
            content="cached channel",
            created_at=datetime(2026, 3, 30, 10, 59, tzinfo=UTC),
            metadata={"source": "teams_graph_api"},
        ),
    )
    service = GraphTeamsService(
        store,
        token="graph-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=25,
        history_page_cap=5,
        timeout_seconds=5.0,
        allowed_channel_targets_provider=lambda: ["team-a/channel-1", "chat:chat-1"],
    )

    messages = service.get_messages(channel_target="chat:chat-1", limit=5)

    assert [message.cursor for message in messages] == ["chat:chat-1/msg-chat"]
    assert [request.url.path for request in requests] == ["/v1.0/chats/chat-1/messages"]


def test_graph_teams_service_fails_clearly_when_token_provider_cannot_resolve_token(
    tmp_path: Path,
) -> None:
    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    service = GraphTeamsService(
        store,
        token_provider=lambda: (_ for _ in ()).throw(
            TeamsAuthenticationError("No delegated Teams credential found for session 'session-1'")
        ),
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_targets_provider=lambda: ["team-a/channel-1"],
    )

    with pytest.raises(TeamsAuthenticationError, match="No delegated Teams credential found"):
        service.get_messages()


def test_graph_teams_service_includes_graph_error_body_in_http_status_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "error": {
                    "code": "Forbidden",
                    "message": "Caller does not have access to this channel.",
                    "innerError": {
                        "request-id": "req-123",
                        "date": "2026-04-10T07:15:00Z",
                    },
                }
            },
            request=request,
        )

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)
    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    service = GraphTeamsService(
        store,
        token="graph-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_targets_provider=lambda: ["team-a/channel-1"],
    )

    with pytest.raises(
        httpx.HTTPStatusError,
        match="Graph error: code=Forbidden, message=Caller does not have access to this channel., request-id=req-123, date=2026-04-10T07:15:00Z",
    ):
        service.get_messages()


def test_stub_teams_service_filters_by_cursor(tmp_path: Path) -> None:
    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    store.put(
        "team-a/channel-1/msg-1",
        TeamsMessageEnvelope(
            cursor="team-a/channel-1/msg-1",
            message_id="msg-1",
            author_id="user-1",
            team_id="team-a",
            channel_id="channel-1",
            thread_id="msg-1",
            content="one",
            created_at=datetime(2026, 3, 30, 11, 0, tzinfo=UTC),
            metadata={"source": "teams_graph_api"},
        ),
    )
    store.put(
        "team-a/channel-1/msg-2",
        TeamsMessageEnvelope(
            cursor="team-a/channel-1/msg-2",
            message_id="msg-2",
            author_id="user-2",
            team_id="team-a",
            channel_id="channel-1",
            thread_id="msg-1",
            content="two",
            created_at=datetime(2026, 3, 30, 11, 1, tzinfo=UTC),
            metadata={"source": "teams_graph_api"},
        ),
    )

    service = StubTeamsService(store)

    messages = service.get_messages(since_cursor="team-a/channel-1/msg-1")

    assert [message.cursor for message in messages] == ["team-a/channel-1/msg-2"]


def test_graph_teams_service_before_cursor_limits_history_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if (
            request.url.path.endswith("/chats/chat-1/messages")
            and request.url.params.get("$skiptoken") is None
        ):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "msg-5",
                            "createdDateTime": "2026-03-30T11:05:00Z",
                            "body": {"content": "<p>five</p>", "contentType": "html"},
                            "from": {"user": {"id": "user-5"}},
                        },
                        {
                            "id": "msg-4",
                            "createdDateTime": "2026-03-30T11:04:00Z",
                            "body": {"content": "<p>four</p>", "contentType": "html"},
                            "from": {"user": {"id": "user-4"}},
                        },
                    ],
                    "@odata.nextLink": "https://graph.microsoft.com/v1.0/chats/chat-1/messages?$skiptoken=page-2",
                },
                request=request,
            )
        if (
            request.url.path.endswith("/chats/chat-1/messages")
            and request.url.params.get("$skiptoken") == "page-2"
        ):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "msg-3",
                            "createdDateTime": "2026-03-30T11:03:00Z",
                            "body": {"content": "<p>three</p>", "contentType": "html"},
                            "from": {"user": {"id": "user-3"}},
                        },
                        {
                            "id": "msg-2",
                            "createdDateTime": "2026-03-30T11:02:00Z",
                            "body": {"content": "<p>two</p>", "contentType": "html"},
                            "from": {"user": {"id": "user-2"}},
                        },
                    ]
                },
                request=request,
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)
    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    service = GraphTeamsService(
        store,
        token="graph-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=2,
        history_page_cap=2,
        timeout_seconds=5.0,
        allowed_channel_targets_provider=lambda: ["chat:chat-1"],
    )

    messages = service.get_messages(before_cursor="chat:chat-1/msg-4", limit=1)

    assert [message.cursor for message in messages] == ["chat:chat-1/msg-3"]
    assert len(requests) == 2


def test_graph_teams_service_posts_status_and_thread_reply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST" and request.url.path.endswith(
            "/teams/team-a/channels/channel-1/messages"
        ):
            return httpx.Response(
                201,
                json={
                    "id": "msg-root",
                    "createdDateTime": "2026-03-30T12:00:00Z",
                    "body": {"content": "<p>root status</p>", "contentType": "html"},
                    "from": {"application": {"id": "bot-app"}},
                    "webUrl": "https://teams.example/root",
                },
                request=request,
            )
        if request.method == "POST" and request.url.path.endswith(
            "/teams/team-a/channels/channel-1/messages/msg-root/replies"
        ):
            return httpx.Response(
                201,
                json={
                    "id": "msg-reply",
                    "replyToId": "msg-root",
                    "createdDateTime": "2026-03-30T12:01:00Z",
                    "body": {"content": "<p>thread reply</p>", "contentType": "html"},
                    "from": {"application": {"id": "bot-app"}},
                    "webUrl": "https://teams.example/reply",
                },
                request=request,
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url.path}")

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)
    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    service = GraphTeamsService(
        store,
        token="graph-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_targets_provider=lambda: ["team-a/channel-1"],
    )

    post_result = service.post_status("root status", "team-a/channel-1", "session-1")
    reply_result = service.post_status(
        "thread reply",
        "team-a/channel-1",
        "session-1",
        thread_id="msg-root",
    )

    assert post_result == {
        "status": "posted",
        "channel_target": "team-a/channel-1",
        "team_id": "team-a",
        "channel_id": "channel-1",
        "thread_id": "msg-root",
        "reply_to_message_id": None,
        "session_id": "session-1",
        "message_id": "msg-root",
        "web_url": "https://teams.example/root",
    }
    assert reply_result == {
        "status": "posted",
        "channel_target": "team-a/channel-1",
        "team_id": "team-a",
        "channel_id": "channel-1",
        "thread_id": "msg-root",
        "reply_to_message_id": "msg-root",
        "session_id": "session-1",
        "message_id": "msg-reply",
        "web_url": "https://teams.example/reply",
    }
    assert requests[0].headers["Authorization"] == "Bearer graph-token"
    assert requests[0].content == b'{"body":{"content":"<p>root status</p>","contentType":"html"}}'
    assert requests[1].content == b'{"body":{"content":"<p>thread reply</p>","contentType":"html"}}'

    stored = store.read_all()
    assert stored["team-a/channel-1/msg-root"].metadata["source"] == "teams_graph_api_status"
    assert stored["team-a/channel-1/msg-root"].metadata["session_id"] == "session-1"
    assert stored["team-a/channel-1/msg-reply"].thread_id == "msg-root"


def test_graph_teams_service_posts_status_with_mentions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    posted_payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path == "/v1.0/users/operator@example.com":
            return httpx.Response(
                200,
                json={
                    "id": "user-operator",
                    "displayName": "Research & Ops",
                    "mail": "operator@example.com",
                },
                request=request,
            )
        if request.method == "POST" and request.url.path.endswith(
            "/teams/team-a/channels/channel-1/messages"
        ):
            posted_payloads.append(json.loads(request.content.decode("utf-8")))
            return httpx.Response(
                201,
                json={
                    "id": "msg-root",
                    "createdDateTime": "2026-03-30T12:00:00Z",
                    "body": {"content": "<p>root status</p>", "contentType": "html"},
                    "from": {"application": {"id": "bot-app"}},
                    "webUrl": "https://teams.example/root",
                },
                request=request,
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url.path}")

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)
    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    service = GraphTeamsService(
        store,
        token="graph-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_targets_provider=lambda: ["team-a/channel-1"],
    )

    service.post_status(
        "root status",
        "team-a/channel-1",
        "session-1",
        mentions=["operator@example.com"],
    )

    assert [request.url.path for request in requests] == [
        "/v1.0/users/operator@example.com",
        "/v1.0/teams/team-a/channels/channel-1/messages",
    ]
    assert posted_payloads == [
        {
            "body": {
                "content": (
                    '<p><at id="0">Research &amp; Ops</at></p><p>root status</p>'
                ),
                "contentType": "html",
            },
            "mentions": [
                {
                    "id": 0,
                    "mentionText": "Research & Ops",
                    "mentioned": {
                        "user": {
                            "id": "user-operator",
                            "displayName": "Research & Ops",
                            "userIdentityType": "aadUser",
                        }
                    },
                }
            ],
        }
    ]


def test_graph_teams_service_posts_thread_reply_with_mentions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posted_payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/v1.0/users/operator@example.com":
            return httpx.Response(
                200,
                json={
                    "id": "user-operator",
                    "displayName": "Operator User",
                    "mail": "operator@example.com",
                },
                request=request,
            )
        if request.method == "POST" and request.url.path.endswith(
            "/teams/team-a/channels/channel-1/messages/msg-root/replies"
        ):
            posted_payloads.append(json.loads(request.content.decode("utf-8")))
            return httpx.Response(
                201,
                json={
                    "id": "msg-reply",
                    "replyToId": "msg-root",
                    "createdDateTime": "2026-03-30T12:01:00Z",
                    "body": {"content": "<p>thread reply</p>", "contentType": "html"},
                    "from": {"application": {"id": "bot-app"}},
                    "webUrl": "https://teams.example/reply",
                },
                request=request,
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url.path}")

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)
    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    service = GraphTeamsService(
        store,
        token="graph-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_targets_provider=lambda: ["team-a/channel-1"],
    )

    service.post_status(
        "thread reply",
        "team-a/channel-1",
        "session-1",
        thread_id="msg-root",
        mentions=["operator@example.com"],
    )

    assert posted_payloads[0]["body"] == {
        "content": '<p><at id="0">Operator User</at></p><p>thread reply</p>',
        "contentType": "html",
    }
    assert posted_payloads[0]["mentions"] == [
        {
            "id": 0,
            "mentionText": "Operator User",
            "mentioned": {
                "user": {
                    "id": "user-operator",
                    "displayName": "Operator User",
                    "userIdentityType": "aadUser",
                }
            },
        }
    ]


def test_graph_teams_service_posts_chat_status_with_mentions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    posted_payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path == "/v1.0/users/operator@example.com":
            return httpx.Response(
                200,
                json={
                    "id": "user-operator",
                    "displayName": "Operator User",
                    "mail": "operator@example.com",
                },
                request=request,
            )
        if request.method == "POST" and request.url.path == "/v1.0/chats/chat-1/messages":
            posted_payloads.append(json.loads(request.content.decode("utf-8")))
            return httpx.Response(
                201,
                json={
                    "id": "msg-chat",
                    "createdDateTime": "2026-03-30T12:02:00Z",
                    "body": {"content": "<p>chat status</p>", "contentType": "html"},
                    "from": {"application": {"id": "bot-app"}},
                    "chatId": "chat-1",
                },
                request=request,
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url.path}")

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)
    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    service = GraphTeamsService(
        store,
        token="graph-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=25,
        timeout_seconds=5.0,
    )

    result = service.post_status(
        "chat status",
        "chat:chat-1",
        "session-1",
        mentions=["operator@example.com"],
    )

    assert result["status"] == "posted"
    assert result["chat_id"] == "chat-1"
    assert [request.url.path for request in requests] == [
        "/v1.0/users/operator@example.com",
        "/v1.0/chats/chat-1/messages",
    ]
    assert posted_payloads == [
        {
            "body": {
                "content": '<p><at id="0">Operator User</at></p><p>chat status</p>',
                "contentType": "html",
            },
            "mentions": [
                {
                    "id": 0,
                    "mentionText": "Operator User",
                    "mentioned": {
                        "user": {
                            "id": "user-operator",
                            "displayName": "Operator User",
                            "userIdentityType": "aadUser",
                        }
                    },
                }
            ],
        }
    ]


def test_graph_teams_service_fails_when_mention_user_is_unresolved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1.0/users/missing@example.com":
            return httpx.Response(
                404,
                json={"error": {"code": "Request_ResourceNotFound"}},
                request=request,
            )
        if request.url.path == "/v1.0/users":
            return httpx.Response(200, json={"value": []}, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url.path}")

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)
    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    service = GraphTeamsService(
        store,
        token="graph-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_targets_provider=lambda: ["team-a/channel-1"],
    )

    with pytest.raises(ValueError, match="Unable to resolve Teams mention user"):
        service.post_status(
            "status",
            "team-a/channel-1",
            "session-1",
            mentions=["missing@example.com"],
        )

    assert [request.url.path for request in requests] == [
        "/v1.0/users/missing@example.com",
        "/v1.0/users",
    ]


def test_graph_teams_service_surfaces_mention_lookup_permission_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1.0/users/operator@example.com":
            return httpx.Response(
                403,
                json={"error": {"code": "Authorization_RequestDenied"}},
                request=request,
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url.path}")

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)
    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    service = GraphTeamsService(
        store,
        token="graph-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_targets_provider=lambda: ["team-a/channel-1"],
    )

    with pytest.raises(httpx.HTTPStatusError):
        service.post_status(
            "status",
            "team-a/channel-1",
            "session-1",
            mentions=["operator@example.com"],
        )

    assert [request.url.path for request in requests] == ["/v1.0/users/operator@example.com"]


def test_graph_teams_service_get_messages_preserves_status_source_for_known_posts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST" and request.url.path.endswith(
            "/teams/team-a/channels/channel-1/messages"
        ):
            return httpx.Response(
                201,
                json={
                    "id": "msg-root",
                    "createdDateTime": "2026-03-30T12:00:00Z",
                    "body": {"content": "<p>root status</p>", "contentType": "html"},
                    "from": {"application": {"id": "bot-app"}},
                    "webUrl": "https://teams.example/root",
                },
                request=request,
            )
        if request.method == "GET" and request.url.path.endswith(
            "/teams/team-a/channels/channel-1/messages"
        ):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "msg-root",
                            "createdDateTime": "2026-03-30T12:00:00Z",
                            "body": {"content": "<p>root status</p>", "contentType": "html"},
                            "from": {"application": {"id": "bot-app"}},
                            "webUrl": "https://teams.example/root",
                            "replies": [],
                        }
                    ]
                },
                request=request,
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url.path}")

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)
    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    service = GraphTeamsService(
        store,
        token="graph-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_targets_provider=lambda: ["team-a/channel-1"],
    )

    service.post_status("root status", "team-a/channel-1", "session-1")
    messages = service.get_messages()

    assert [message.cursor for message in messages] == ["team-a/channel-1/msg-root"]
    assert messages[0].metadata["source"] == "teams_graph_api_status"
    assert messages[0].metadata["session_id"] == "session-1"


def test_graph_teams_service_renders_fenced_code_blocks_as_html(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            201,
            json={
                "id": "msg-root",
                "createdDateTime": "2026-03-30T12:00:00Z",
                "body": {"content": "<pre><code>var x = 1;\n</code></pre>", "contentType": "html"},
                "from": {"application": {"id": "bot-app"}},
                "webUrl": "https://teams.example/root",
            },
            request=request,
        )

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)
    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    service = GraphTeamsService(
        store,
        token="graph-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_targets_provider=lambda: ["team-a/channel-1"],
    )

    service.post_status("```csharp\nvar x = 1;\n```", "team-a/channel-1", "session-1")

    assert (
        requests[0].content
        == b'{"body":{"content":"<pre><code>var x = 1;</code></pre>","contentType":"html"}}'
    )


def test_graph_teams_service_renders_common_markdown_formatting_as_html(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            201,
            json={
                "id": "msg-root",
                "createdDateTime": "2026-03-30T12:00:00Z",
                "body": {"content": "<p>ok</p>", "contentType": "html"},
                "from": {"application": {"id": "bot-app"}},
                "webUrl": "https://teams.example/root",
            },
            request=request,
        )

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)
    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    service = GraphTeamsService(
        store,
        token="graph-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_targets_provider=lambda: ["team-a/channel-1"],
    )

    message = (
        "# Update\n"
        "- **Bold** item\n"
        "- *Italic* item with `inline_code`\n"
        "1. First\n"
        "2. Second with ~~strike~~\n"
        "> Quote line\n"
        "See [docs](https://example.com/docs)"
    )
    service.post_status(message, "team-a/channel-1", "session-1")

    assert requests[0].content == (
        b'{"body":{"content":"<h1>Update</h1><ul><li><strong>Bold</strong> item</li>'
        b"<li><em>Italic</em> item with <code>inline_code</code></li></ul>"
        b"<ol><li>First</li><li>Second with <del>strike</del></li></ol>"
        b'<blockquote><p>Quote line</p></blockquote><p>See <a href=\\"https://example.com/docs\\">docs</a></p>",'
        b'"contentType":"html"}}'
    )


def test_graph_teams_service_nests_bullets_inside_numbered_status_items(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            201,
            json={
                "id": "msg-root",
                "createdDateTime": "2026-03-30T12:00:00Z",
                "body": {"content": "<p>ok</p>", "contentType": "html"},
                "from": {"application": {"id": "bot-app"}},
                "webUrl": "https://teams.example/root",
            },
            request=request,
        )

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)
    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    service = GraphTeamsService(
        store,
        token="graph-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_targets_provider=lambda: ["team-a/channel-1"],
    )

    message = "1. Rubrik A\n- detalj A\n\n2. Rubrik B\n- detalj B"
    service.post_status(message, "team-a/channel-1", "session-1")

    assert requests[0].content == (
        b'{"body":{"content":"<ol><li>Rubrik A<ul><li>detalj A</li></ul></li>'
        b"<li>Rubrik B<ul><li>detalj B</li></ul></li></ol>\","
        b'"contentType":"html"}}'
    )


def test_graph_teams_service_add_reaction_for_channel_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204, request=request)

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)
    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    service = GraphTeamsService(
        store,
        token="graph-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_targets_provider=lambda: ["team-a/channel-1"],
    )

    result = service.add_reaction("team-a/channel-1", "msg-root", "like", "session-1")

    assert result == {
        "status": "reacted",
        "channel_target": "team-a/channel-1",
        "team_id": "team-a",
        "channel_id": "channel-1",
        "session_id": "session-1",
        "message_id": "msg-root",
        "reaction": "like",
        "emoji": "like",
        "graph_reaction_type": "👍",
    }
    assert requests[0].method == "POST"
    assert requests[0].url.path.endswith(
        "/teams/team-a/channels/channel-1/messages/msg-root/setReaction"
    )
    assert json.loads(requests[0].content.decode("utf-8")) == {"reactionType": "👍"}


@pytest.mark.parametrize(
    ("channel_target", "message_id", "reaction", "expected_graph_reaction_type"),
    [
        ("team-a/channel-1", "msg-root", "eyes", "👀"),
        ("chat:chat-1", "msg-chat", "thumbsup", "👍"),
        ("team-a/channel-1", "msg-root", "like", "👍"),
        ("chat:chat-1", "msg-chat", "heart", "❤️"),
    ],
)
def test_graph_teams_service_normalizes_deployed_reaction_aliases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    channel_target: str,
    message_id: str,
    reaction: str,
    expected_graph_reaction_type: str,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204, request=request)

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)
    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    service = GraphTeamsService(
        store,
        token="graph-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_targets_provider=lambda: ["team-a/channel-1", "chat:chat-1"],
    )

    result = service.add_reaction(channel_target, message_id, reaction, "session-1")

    expected_result = {
        "status": "reacted",
        "channel_target": channel_target,
        "session_id": "session-1",
        "message_id": message_id,
        "reaction": reaction,
        "emoji": reaction,
        "graph_reaction_type": expected_graph_reaction_type,
    }
    if channel_target.startswith("chat:"):
        expected_result["chat_id"] = channel_target.removeprefix("chat:")
    else:
        expected_result["team_id"] = "team-a"
        expected_result["channel_id"] = "channel-1"

    assert result == expected_result
    assert len(requests) == 1
    assert json.loads(requests[0].content.decode("utf-8")) == {
        "reactionType": expected_graph_reaction_type
    }


def test_graph_teams_service_preserves_direct_unicode_reaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204, request=request)

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)
    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    service = GraphTeamsService(
        store,
        token="graph-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_targets_provider=lambda: ["team-a/channel-1"],
    )

    result = service.add_reaction("team-a/channel-1", "msg-root", "👀", "session-1")

    assert result == {
        "status": "reacted",
        "channel_target": "team-a/channel-1",
        "team_id": "team-a",
        "channel_id": "channel-1",
        "session_id": "session-1",
        "message_id": "msg-root",
        "reaction": "👀",
        "emoji": "👀",
    }
    assert "graph_reaction_type" not in result
    assert json.loads(requests[0].content.decode("utf-8")) == {"reactionType": "👀"}


def test_graph_teams_service_add_reaction_for_chat_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204, request=request)

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)
    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    service = GraphTeamsService(
        store,
        token="graph-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_targets_provider=lambda: ["chat:chat-1"],
    )

    result = service.add_reaction("chat:chat-1", "msg-chat", "heart", "session-1")

    assert result == {
        "status": "reacted",
        "channel_target": "chat:chat-1",
        "chat_id": "chat-1",
        "session_id": "session-1",
        "message_id": "msg-chat",
        "reaction": "heart",
        "emoji": "heart",
        "graph_reaction_type": "❤️",
    }
    assert requests[0].url.path.endswith("/chats/chat-1/messages/msg-chat/setReaction")
    assert json.loads(requests[0].content.decode("utf-8")) == {"reactionType": "❤️"}


def test_graph_teams_service_rejects_unsupported_ascii_reaction_before_http(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        pytest.fail(f"Unexpected request: {request.url}")

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)
    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    service = GraphTeamsService(
        store,
        token="graph-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_targets_provider=lambda: ["team-a/channel-1"],
    )

    with pytest.raises(ValueError, match="Unsupported Teams reaction: shrug"):
        service.add_reaction("team-a/channel-1", "msg-root", "shrug", "session-1")

    assert requests == []


def test_stub_teams_add_reaction_returns_deterministic_success_payload(tmp_path: Path) -> None:
    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    service = StubTeamsService(store)

    channel_result = service.add_reaction("team-a/channel-1", "msg-root", "like", "session-1")
    chat_result = service.add_reaction("chat:chat-1", "msg-chat", "heart", "session-1")

    assert channel_result == {
        "status": "stubbed",
        "channel_target": "team-a/channel-1",
        "team_id": "team-a",
        "channel_id": "channel-1",
        "session_id": "session-1",
        "message_id": "msg-root",
        "reaction": "like",
        "emoji": "like",
    }
    assert chat_result == {
        "status": "stubbed",
        "channel_target": "chat:chat-1",
        "chat_id": "chat-1",
        "session_id": "session-1",
        "message_id": "msg-chat",
        "reaction": "heart",
        "emoji": "heart",
    }


def test_graph_teams_service_preserves_attachment_metadata_and_downloads_hosted_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/teams/team-a/channels/channel-1/messages"):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "msg-1",
                            "createdDateTime": "2026-03-30T11:00:00Z",
                            "body": {
                                "content": '<p>hello <img src="../hostedContents/hosted-1/$value"></p>',
                                "contentType": "html",
                            },
                            "from": {"user": {"id": "user-1"}},
                            "attachments": [
                                {
                                    "id": "file-1",
                                    "name": "doc.txt",
                                    "contentType": "reference",
                                    "contentUrl": "https://graph.microsoft.com/download/file-1",
                                    "contentBytes": "must-not-store",
                                },
                                {
                                    "id": "graph-hosted-1",
                                    "name": "inline.png",
                                    "contentType": "image/png",
                                    "contentUrl": "../hostedContents/hosted-1/$value",
                                    "contentBytes": "must-not-store",
                                }
                            ],
                        }
                    ]
                },
                request=request,
            )
        if request.url.path.endswith("/messages/msg-1/hostedContents/hosted-1/$value"):
            return httpx.Response(
                200,
                content=b"png-bytes",
                headers={"content-type": "image/png"},
                request=request,
            )
        raise AssertionError(f"Unexpected URL: {request.url}")

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)
    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    service = GraphTeamsService(
        store,
        token="graph-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_targets_provider=lambda: ["team-a/channel-1"],
    )

    messages = service.get_messages()

    attachments = messages[0].metadata["attachments"]
    assert [item["id"] for item in attachments] == ["file-1", "hosted-1"]
    assert "contentBytes" not in attachments[0]
    assert "contentBytes" not in attachments[1]
    assert attachments[1]["attachment_kind"] == "hosted_content"
    assert attachments[1]["graph_attachment_id"] == "graph-hosted-1"
    assert "contentBytes" not in messages[0].metadata["message"]["attachments"][0]
    downloaded = service.download_attachment(
        "msg-1", "hosted-1", channel_target="team-a/channel-1"
    )
    assert downloaded["content"] == b"png-bytes"
    assert downloaded["mime_type"] == "image/png"
    assert downloaded["filename"] == "inline.png"


def test_graph_teams_service_lists_hosted_content_on_demand_for_inline_images(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/teams/team-a/channels/channel-1/messages"):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "msg-1",
                            "createdDateTime": "2026-03-30T11:00:00Z",
                            "body": {
                                "content": '<p>inline <img src="../hostedContents/hosted-1/$value"></p>',
                                "contentType": "html",
                            },
                            "from": {"user": {"id": "user-1"}},
                            "attachments": [],
                        }
                    ]
                },
                request=request,
            )
        if request.url.path.endswith("/messages/msg-1/hostedContents"):
            return httpx.Response(
                200,
                json={"value": [{"id": "hosted-1", "contentType": "image/png"}]},
                request=request,
            )
        if request.url.path.endswith("/messages/msg-1/hostedContents/hosted-1/$value"):
            return httpx.Response(
                200,
                content=b"png-bytes",
                headers={"content-type": "image/png"},
                request=request,
            )
        raise AssertionError(f"Unexpected URL: {request.url}")

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)
    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    service = GraphTeamsService(
        store,
        token="graph-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_targets_provider=lambda: ["team-a/channel-1"],
    )

    messages = service.get_messages()

    assert messages[0].metadata["attachments"] == []
    attachments = service.list_attachments("msg-1", channel_target="team-a/channel-1")
    assert attachments == [
        {
            "id": "hosted-1",
            "attachment_id": "hosted-1",
            "hosted_content_id": "hosted-1",
            "contentType": "image/png",
            "attachment_kind": "hosted_content",
            "name": "hosted-content-hosted-1.png",
            "filename": "hosted-content-hosted-1.png",
        }
    ]
    downloaded = service.download_attachment(
        "msg-1", "hosted-1", channel_target="team-a/channel-1"
    )
    assert downloaded["content"] == b"png-bytes"
    assert downloaded["mime_type"] == "image/png"
    assert downloaded["filename"] == "hosted-content-hosted-1.png"
    assert [request.url.path for request in requests] == [
        "/v1.0/teams/team-a/channels/channel-1/messages",
        "/v1.0/teams/team-a/channels/channel-1/messages/msg-1/hostedContents",
        "/v1.0/teams/team-a/channels/channel-1/messages/msg-1/hostedContents/hosted-1/$value",
    ]


def test_graph_teams_attachment_download_does_not_send_token_to_external_content_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert str(request.url) == "https://files.example.test/download/doc.txt"
        return httpx.Response(
            200,
            content=b"document",
            headers={"content-type": "text/plain"},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)
    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    store.put(
        "team-a/channel-1/msg-1",
        TeamsMessageEnvelope(
            cursor="team-a/channel-1/msg-1",
            message_id="msg-1",
            author_id="user-a",
            team_id="team-a",
            channel_id="channel-1",
            content="file",
            metadata={
                "target": "team-a/channel-1",
                "attachments": [
                    {
                        "id": "att-1",
                        "name": "doc.txt",
                        "contentType": "text/plain",
                        "contentUrl": "https://files.example.test/download/doc.txt",
                    }
                ],
            },
        ),
    )
    service = GraphTeamsService(
        store,
        token="graph-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_targets_provider=lambda: ["team-a/channel-1"],
    )

    downloaded = service.download_attachment("msg-1", "att-1", channel_target="team-a/channel-1")

    assert downloaded["content"] == b"document"
    assert downloaded["mime_type"] == "text/plain"
    assert requests[0].headers.get("Authorization") is None


def test_graph_teams_attachment_download_uses_graph_for_sharepoint_content_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    sharepoint_url = (
        "https://example.invalid/sites/dev46/Delade dokument/Truman/Picture.png"
    )
    encoded_share = (
        base64.urlsafe_b64encode(sharepoint_url.encode("utf-8")).decode("ascii").rstrip("=")
    )
    graph_download_url = (
        f"https://graph.microsoft.com/v1.0/shares/u!{encoded_share}/driveItem/content"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert str(request.url) == graph_download_url
        assert request.headers["Authorization"] == "Bearer graph-token"
        return httpx.Response(
            200,
            content=b"png-bytes",
            headers={"content-type": "image/png"},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)
    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    store.put(
        "team-a/channel-1/msg-1",
        TeamsMessageEnvelope(
            cursor="team-a/channel-1/msg-1",
            message_id="msg-1",
            author_id="user-a",
            team_id="team-a",
            channel_id="channel-1",
            content="file",
            metadata={
                "target": "team-a/channel-1",
                "attachments": [
                    {
                        "id": "att-1",
                        "name": "Picture.png",
                        "contentType": "reference",
                        "contentUrl": sharepoint_url,
                    }
                ],
            },
        ),
    )
    service = GraphTeamsService(
        store,
        token="graph-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_targets_provider=lambda: ["team-a/channel-1"],
    )

    downloaded = service.download_attachment("msg-1", "att-1", channel_target="team-a/channel-1")

    assert downloaded["content"] == b"png-bytes"
    assert downloaded["mime_type"] == "image/png"
    assert len(requests) == 1


def test_graph_teams_attachment_download_follows_graph_redirect_without_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    sharepoint_url = "https://example.invalid/sites/dev46/Docs/Picture.png"
    encoded_share = (
        base64.urlsafe_b64encode(sharepoint_url.encode("utf-8")).decode("ascii").rstrip("=")
    )
    graph_download_url = (
        f"https://graph.microsoft.com/v1.0/shares/u!{encoded_share}/driveItem/content"
    )
    redirected_url = "https://files.example.test/preauthorized/Picture.png"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) == graph_download_url:
            assert request.headers["Authorization"] == "Bearer graph-token"
            return httpx.Response(302, headers={"location": redirected_url}, request=request)
        if str(request.url) == redirected_url:
            assert request.headers.get("Authorization") is None
            return httpx.Response(
                200,
                content=b"png-bytes",
                headers={"content-type": "image/png"},
                request=request,
            )
        raise AssertionError(f"Unexpected URL: {request.url}")

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)
    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    store.put(
        "team-a/channel-1/msg-1",
        TeamsMessageEnvelope(
            cursor="team-a/channel-1/msg-1",
            message_id="msg-1",
            author_id="user-a",
            team_id="team-a",
            channel_id="channel-1",
            content="file",
            metadata={
                "target": "team-a/channel-1",
                "attachments": [
                    {
                        "id": "att-1",
                        "name": "Picture.png",
                        "contentType": "reference",
                        "contentUrl": sharepoint_url,
                    }
                ],
            },
        ),
    )
    service = GraphTeamsService(
        store,
        token="graph-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_targets_provider=lambda: ["team-a/channel-1"],
    )

    downloaded = service.download_attachment("msg-1", "att-1", channel_target="team-a/channel-1")

    assert downloaded["content"] == b"png-bytes"
    assert downloaded["mime_type"] == "image/png"
    assert [str(request.url) for request in requests] == [graph_download_url, redirected_url]


def test_graph_teams_attachment_listing_enforces_channel_allowlist(tmp_path: Path) -> None:
    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    store.put(
        "team-a/channel-2/msg-1",
        TeamsMessageEnvelope(
            cursor="team-a/channel-2/msg-1",
            message_id="msg-1",
            author_id="user-a",
            team_id="team-a",
            channel_id="channel-2",
            content="blocked",
            metadata={
                "target": "team-a/channel-2",
                "attachments": [{"id": "att-1", "name": "x"}],
            },
        ),
    )
    service = GraphTeamsService(
        store,
        token="graph-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_targets_provider=lambda: ["team-a/channel-1"],
    )

    with pytest.raises(ValueError, match="not allowlisted"):
        service.list_attachments("msg-1", channel_target="team-a/channel-2")


def test_graph_teams_post_file_forms_share_link_reference_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path.endswith(
            "/teams/team-a/channels/channel-1/messages/root-1/replies"
        )
        payload = json.loads(request.read())
        assert payload["body"]["contentType"] == "html"
        assert payload["body"]["content"] == (
            '<p>file incoming</p><p><attachment id="1"></attachment></p>'
        )
        assert payload["attachments"] == [
            {
                "id": "1",
                "name": "upload.txt",
                "contentType": "reference",
                "contentUrl": "https://contoso.sharepoint.com/sites/dev/Shared%20Documents/upload.txt",
            }
        ]
        assert "hostedContents" not in payload
        return httpx.Response(
            200,
            json={
                "id": "reply-1",
                "replyToId": "root-1",
                "createdDateTime": "2026-03-30T11:00:00Z",
                "body": {"content": "<p>file incoming</p>", "contentType": "html"},
                "from": {"user": {"id": "bot-1"}},
                "attachments": [{"id": "att-1", "name": "upload.txt"}],
            },
            request=request,
        )

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)
    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    service = GraphTeamsService(
        store,
        token="graph-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_targets_provider=lambda: ["team-a/channel-1"],
    )

    result = service.post_file(
        "file incoming",
        "team-a/channel-1",
        "session-1",
        [
            {
                "filename": "upload.txt",
                "mime_type": "text/plain",
                "metadata": {
                    "attachment_ref": {
                        "metadata": {
                            "contentUrl": (
                                "https://contoso.sharepoint.com/sites/dev/"
                                "Shared Documents/upload.txt"
                            )
                        }
                    }
                },
            }
        ],
        thread_id="root-1",
    )

    assert result["status"] == "posted"
    assert result["message_id"] == "reply-1"
    assert result["artifact_count"] == 1
    assert len(requests) == 1


def test_graph_teams_post_file_inlines_image_hosted_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    artifact_path = tmp_path / "photo.png"
    artifact_path.write_bytes(b"png-bytes")

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = json.loads(request.read())
        assert payload["body"]["content"] == (
            '<p>image incoming</p><p><img src="../hostedContents/1/$value" '
            'alt="photo.png"></p>'
        )
        assert "attachments" not in payload
        assert payload["hostedContents"] == [
            {
                "@microsoft.graph.temporaryId": "1",
                "contentBytes": "cG5nLWJ5dGVz",
                "contentType": "image/png",
            }
        ]
        return httpx.Response(
            200,
            json={
                "id": "msg-1",
                "createdDateTime": "2026-03-30T11:00:00Z",
                "body": {"content": "<p>image incoming</p>", "contentType": "html"},
                "from": {"user": {"id": "bot-1"}},
                "attachments": [{"id": "att-1", "name": "photo.png"}],
            },
            request=request,
        )

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)
    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    service = GraphTeamsService(
        store,
        token="graph-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_targets_provider=lambda: ["team-a/channel-1"],
    )

    result = service.post_file(
        "image incoming",
        "team-a/channel-1",
        "session-1",
        [{"path": str(artifact_path), "filename": "photo.png", "mime_type": "image/png"}],
    )

    assert result["status"] == "posted"
    assert result["artifact_count"] == 1
    assert len(requests) == 1


def test_graph_teams_post_file_links_non_image_control_plane_download(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = json.loads(request.read())
        assert payload["body"]["content"] == (
            '<p>file incoming</p><p><a href="'
            'https://control.example.test/public/communication-artifacts/comm_123?token=abc">'
            "upload.txt</a></p>"
        )
        assert "attachments" not in payload
        assert "hostedContents" not in payload
        return httpx.Response(
            200,
            json={
                "id": "msg-1",
                "createdDateTime": "2026-03-30T11:00:00Z",
                "body": {"content": "<p>file incoming</p>", "contentType": "html"},
                "from": {"user": {"id": "bot-1"}},
                "attachments": [],
            },
            request=request,
        )

    transport = httpx.MockTransport(handler)
    patch_httpx_client(monkeypatch, transport)
    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    service = GraphTeamsService(
        store,
        token="graph-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_targets_provider=lambda: ["team-a/channel-1"],
    )

    result = service.post_file(
        "file incoming",
        "team-a/channel-1",
        "session-1",
        [
            {
                "filename": "upload.txt",
                "mime_type": "text/plain",
                "download_url": (
                    "https://control.example.test/public/communication-artifacts/"
                    "comm_123?token=abc"
                ),
            }
        ],
    )

    assert result["status"] == "posted"
    assert result["artifact_count"] == 1
    assert len(requests) == 1


def test_graph_teams_post_file_rejects_non_image_bytes_without_share_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact_path = tmp_path / "upload.txt"
    artifact_path.write_text("upload-body", encoding="utf-8")

    transport = httpx.MockTransport(
        lambda request: pytest.fail(f"Unexpected request: {request.url}")
    )
    patch_httpx_client(monkeypatch, transport)
    store = JsonStore(tmp_path / "teams.json", TeamsMessageEnvelope)
    service = GraphTeamsService(
        store,
        token="graph-token",
        api_base_url="https://graph.microsoft.com/v1.0",
        poll_limit=25,
        timeout_seconds=5.0,
        allowed_channel_targets_provider=lambda: ["team-a/channel-1"],
    )

    with pytest.raises(ValueError, match="SharePoint/OneDrive contentUrl"):
        service.post_file(
            "file incoming",
            "team-a/channel-1",
            "session-1",
            [{"path": str(artifact_path), "filename": "upload.txt", "mime_type": "text/plain"}],
        )
