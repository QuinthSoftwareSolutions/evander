from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from agent_sandbox.config import GatewayConfig
from agent_sandbox.models import SessionManifest, TeamsMessageEnvelope, ToolName
from agent_sandbox.teams_ingest_worker import build_teams_ingest_worker


def patch_worker_httpx_client(
    monkeypatch: pytest.MonkeyPatch,
    transport: httpx.BaseTransport,
) -> None:
    real_client = httpx.Client
    monkeypatch.setattr(
        "agent_sandbox.teams_ingest_worker.httpx.Client",
        lambda timeout: real_client(timeout=timeout, transport=transport),
    )


def test_build_teams_ingest_worker_uses_worker_config_and_normalizes_directories(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    worker_config_path = project_dir / "config" / "teams-ingest" / "config.json"
    worker_config_path.parent.mkdir(parents=True, exist_ok=True)
    worker_config_path.write_text(
        json.dumps(
            {
                "directories": [" root/team-a ", "root/team-b", "  "],
                "poll_interval_seconds": 12.5,
                "jitter_seconds": 1.5,
                "backoff_seconds": 45.0,
            }
        ),
        encoding="utf-8",
    )

    config = GatewayConfig.model_validate({"runtime_dir": str(tmp_path / "runtime")})
    worker = build_teams_ingest_worker(config, project_dir=project_dir)

    assert worker.worker_config.directories == ["root/team-a", "root/team-b"]
    assert str(worker.worker_config.lock_path) == str(tmp_path / "runtime" / "teams-ingest.lock")
    result = worker.run_once()
    assert result.directories_polled == 2
    assert result.sessions_seen == 0
    assert result.channel_targets_polled == 0


def test_teams_ingest_worker_persists_inbound_messages_and_ignores_status_store_entries(
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
                            "from": {"user": {"id": "user-1", "displayName": "Ada"}},
                            "replies": [
                                {
                                    "id": "msg-2",
                                    "replyToId": "msg-1",
                                    "createdDateTime": "2026-03-30T11:01:00Z",
                                    "body": {"content": "reply", "contentType": "text"},
                                    "from": {"user": {"id": "user-2", "displayName": "Grace"}},
                                }
                            ],
                        }
                    ]
                },
                request=request,
            )
        raise AssertionError(f"Unexpected path: {request.url.path}")

    patch_worker_httpx_client(monkeypatch, httpx.MockTransport(handler))
    monkeypatch.setenv("GRAPH_TEAMS_ACCESS_TOKEN", "graph-token")
    config = GatewayConfig.model_validate(
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "teams_backend": "graph_api",
        }
    )
    worker = build_teams_ingest_worker(config, project_dir=tmp_path)
    worker.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.TEAMS_GET_MESSAGES],
            teams_channel_allowlist=["team-a/channel-1"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    worker.runtime.teams_messages.put(
        "team-a/channel-1/msg-status",
        TeamsMessageEnvelope(
            cursor="team-a/channel-1/msg-status",
            message_id="msg-status",
            author_id="bot-app",
            team_id="team-a",
            channel_id="channel-1",
            thread_id="msg-status",
            content="status",
            created_at=datetime(2026, 3, 30, 10, 59, tzinfo=UTC),
            metadata={"source": "teams_graph_api_status", "session_id": "session-1"},
        ),
    )

    result = worker.run_once()

    stored = worker.store.list_all_messages(channel_targets=["team-a/channel-1"])
    assert [message.cursor for message in stored] == [
        "team-a/channel-1/msg-1",
        "team-a/channel-1/msg-2",
    ]
    assert stored[0].author_name == "Ada"
    assert stored[1].thread_id == "msg-1"
    assert stored[0].metadata["source"] == "teams_graph_api"
    assert worker.store.get_cursor("team-a/channel-1") == "team-a/channel-1/msg-2"
    assert requests[0].headers["Authorization"] == "Bearer graph-token"
    assert result.channel_targets_polled == 1


def test_teams_ingest_worker_uses_history_page_budget_per_target(
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
        if request.url.path.endswith("/teams/team-a/channels/channel-2/messages"):
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
        raise AssertionError(f"Unexpected path: {request.url.path}")

    patch_worker_httpx_client(monkeypatch, httpx.MockTransport(handler))
    monkeypatch.setenv("GRAPH_TEAMS_ACCESS_TOKEN", "graph-token")
    config = GatewayConfig.model_validate(
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "teams_backend": "graph_api",
            "teams_history_page_cap": 1,
        }
    )
    worker = build_teams_ingest_worker(config, project_dir=tmp_path)
    worker.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.TEAMS_GET_MESSAGES],
            teams_channel_allowlist=["team-a/channel-1", "team-a/channel-2"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    result = worker.run_once()

    assert [request.url.path for request in requests] == [
        "/v1.0/teams/team-a/channels/channel-1/messages",
        "/v1.0/teams/team-a/channels/channel-2/messages",
    ]
    assert worker.store.get_cursor("team-a/channel-1") == "team-a/channel-1/msg-1"
    assert worker.store.get_cursor("team-a/channel-2") == "team-a/channel-2/msg-2"
    assert result.channel_targets_polled == 2


def test_teams_ingest_worker_uses_delegated_token_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"value": []}, request=request)

    patch_worker_httpx_client(monkeypatch, httpx.MockTransport(handler))
    monkeypatch.setattr(
        "agent_sandbox.teams_ingest_worker._build_delegated_teams_token_provider",
        lambda config: (lambda: "delegated-token"),
    )
    config = GatewayConfig.model_validate(
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "teams_backend": "delegated_graph_api",
            "teams_delegated_identity_id": "identity-1",
            "teams_delegated_client_id": "client-1",
        }
    )
    worker = build_teams_ingest_worker(config, project_dir=tmp_path)
    worker.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.TEAMS_GET_MESSAGES],
            teams_channel_allowlist=["team-a/channel-1"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    worker.run_once()

    assert requests[0].headers["Authorization"] == "Bearer delegated-token"


def test_teams_ingest_worker_ignores_graph_echo_of_agent_status_post(
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
                            "id": "msg-status",
                            "createdDateTime": "2026-03-30T11:02:00Z",
                            "body": {"content": "status", "contentType": "text"},
                            "from": {"application": {"id": "bot-app", "displayName": "Agent Sandbox"}},
                        }
                    ]
                },
                request=request,
            )
        raise AssertionError(f"Unexpected path: {request.url.path}")

    patch_worker_httpx_client(monkeypatch, httpx.MockTransport(handler))
    monkeypatch.setenv("GRAPH_TEAMS_ACCESS_TOKEN", "graph-token")
    config = GatewayConfig.model_validate(
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "teams_backend": "graph_api",
        }
    )
    worker = build_teams_ingest_worker(config, project_dir=tmp_path)
    worker.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.TEAMS_GET_MESSAGES],
            teams_channel_allowlist=["team-a/channel-1"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    worker.runtime.teams_messages.put(
        "team-a/channel-1/msg-status",
        TeamsMessageEnvelope(
            cursor="team-a/channel-1/msg-status",
            message_id="msg-status",
            author_id="bot-app",
            team_id="team-a",
            channel_id="channel-1",
            thread_id="msg-status",
            content="status",
            created_at=datetime(2026, 3, 30, 11, 2, tzinfo=UTC),
            metadata={"source": "teams_graph_api_status", "session_id": "session-1"},
        ),
    )

    worker.run_once()

    assert worker.store.list_all_messages(channel_targets=["team-a/channel-1"]) == []
    assert worker.store.get_cursor("team-a/channel-1") == "team-a/channel-1/msg-status"
