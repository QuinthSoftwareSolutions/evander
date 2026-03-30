from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from agent_sandbox.models import TeamsMessageEnvelope
from agent_sandbox.services import GraphTeamsService, StubTeamsService
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
        if request.url.path.endswith("/teams/team-a/channels/channel-1/messages"):
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

