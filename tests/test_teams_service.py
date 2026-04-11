from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from agent_sandbox.models import TeamsMessageEnvelope
from agent_sandbox.services import GraphTeamsService, StubTeamsService, TeamsAuthenticationError
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
        timeout_seconds=5.0,
        allowed_channel_targets_provider=lambda: ["team-a/channel-1"],
    )

    messages = service.get_messages()

    assert [message.cursor for message in messages] == [
        "team-a/channel-1/msg-1",
        "team-a/channel-1/msg-2",
    ]
    assert len(requests) == 2
    assert requests[0].url.params.get("$top") == "1"
    assert requests[1].url.params.get("$skiptoken") == "page-2"
    assert requests[1].url.params.get("$top") is None


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

    with pytest.raises(httpx.HTTPStatusError, match="Graph error: code=Forbidden, message=Caller does not have access to this channel., request-id=req-123, date=2026-04-10T07:15:00Z"):
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


def test_graph_teams_service_posts_status_and_thread_reply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST" and request.url.path.endswith("/teams/team-a/channels/channel-1/messages"):
            return httpx.Response(
                201,
                json={
                    "id": "msg-root",
                    "createdDateTime": "2026-03-30T12:00:00Z",
                    "body": {"content": "root status", "contentType": "text"},
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
                    "body": {"content": "thread reply", "contentType": "text"},
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
    assert requests[0].content == b'{"body":{"content":"root status","contentType":"text"}}'
    assert requests[1].content == b'{"body":{"content":"thread reply","contentType":"text"}}'

    stored = store.read_all()
    assert stored["team-a/channel-1/msg-root"].metadata["source"] == "teams_graph_api_status"
    assert stored["team-a/channel-1/msg-root"].metadata["session_id"] == "session-1"
    assert stored["team-a/channel-1/msg-reply"].thread_id == "msg-root"
