from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_sandbox.config import GatewayConfig
from agent_sandbox.events.provider_models import CommunicationMessagesQueryDefinition
from agent_sandbox.messaging.discord_inbox_store import DiscordInboxStore
from agent_sandbox.messaging.targets import (
    SqliteCommunicationTargetsRepository,
    communication_targets_db_path,
)
from agent_sandbox.messaging.teams_inbox_store import TeamsInboxMessage, TeamsInboxStore
from agent_sandbox.models import DiscordMessageEnvelope
from agent_sandbox.queries.providers.base import QueryRuntimeContext
from agent_sandbox.queries.providers.communication_messages import (
    CommunicationMessagesQueryProvider,
)
from agent_sandbox.runtime import build_runtime


def _runtime(project_dir: Path):
    return build_runtime(GatewayConfig(runtime_dir=project_dir / "runtime"))


def _write_targets(project_dir: Path, targets: list[dict[str, object]]) -> None:
    repository = SqliteCommunicationTargetsRepository(
        communication_targets_db_path(project_dir / "runtime")
    )
    repository.write_overlay("root", {"targets": targets})


def _query(
    *,
    runtime: object,
    communication_target_id: str,
    query_id: str = "query-communication-messages",
    limit: int | None = None,
    cursor: str | None = None,
    before_cursor: str | None = None,
    thread_id: str | None = None,
    directory_path: str = "root",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": query_id,
        "display_name": "Communication messages",
        "type": "query:communication:messages",
        "communication_target_id": communication_target_id,
        "directory_path": directory_path,
    }
    if limit is not None:
        payload["limit"] = limit
    if cursor is not None:
        payload["cursor"] = cursor
    if before_cursor is not None:
        payload["before_cursor"] = before_cursor
    if thread_id is not None:
        payload["thread_id"] = thread_id

    provider = CommunicationMessagesQueryProvider()
    provider.initialize(
        CommunicationMessagesQueryDefinition.model_validate(payload),
        QueryRuntimeContext(
            provider_config={},
            admin_service=SimpleNamespace(runtime=runtime),
            metadata={"directory_path": directory_path},
        ),
    )
    return provider.execute()


def _teams_message(
    cursor: str,
    *,
    message_id: str,
    created_at: datetime,
    team_id: str = "team-a",
    channel_id: str = "channel-1",
    chat_id: str | None = None,
    thread_id: str | None = None,
    author_id: str = "author-1",
    author_name: str = "Ada",
    content: str = "hello",
    metadata: dict[str, object] | None = None,
) -> TeamsInboxMessage:
    return TeamsInboxMessage(
        cursor=cursor,
        message_id=message_id,
        team_id=team_id,
        channel_id=channel_id,
        chat_id=chat_id,
        thread_id=thread_id,
        author_id=author_id,
        author_name=author_name,
        content=content,
        created_at=created_at,
        metadata=metadata or {"source": "teams_graph_api"},
    )


def _discord_message(
    message_id: str,
    *,
    channel_id: str = "234567890123456789",
    created_at: datetime,
    author_id: str = "author-1",
    author_name: str = "Octocat",
    content: str = "hello",
    in_reply_to: str | None = None,
    raw: dict[str, object] | None = None,
) -> DiscordMessageEnvelope:
    return DiscordMessageEnvelope(
        message_id=message_id,
        channel_id=channel_id,
        author_id=author_id,
        author_name=author_name,
        content=content,
        created_at=created_at,
        in_reply_to=in_reply_to,
        raw=raw
        or {
            "id": message_id,
            "channel_id": channel_id,
            "content": content,
        },
    )


def test_query_communication_messages_returns_teams_messages_and_preserves_store(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    _write_targets(
        tmp_path,
        [
            {
                "id": "teams-primary",
                "provider": "teams",
                "kind": "teams",
                "address": "team-a/channel-1",
                "policy": {"capabilities": ["read"]},
            }
        ],
    )
    store = TeamsInboxStore(tmp_path / "runtime" / "teams-inbox-store.json")
    store.add_message_if_new(
        _teams_message(
            "cursor-1",
            message_id="msg-1",
            created_at=datetime(2026, 3, 21, 10, 0, tzinfo=UTC),
            thread_id="thread-1",
            content="first",
            metadata={"source": "teams_graph_api", "topic": "alpha"},
        )
    )
    store.add_message_if_new(
        _teams_message(
            "cursor-2",
            message_id="msg-2",
            created_at=datetime(2026, 3, 21, 10, 1, tzinfo=UTC),
            thread_id="thread-1",
            content="second",
            metadata={"source": "teams_graph_api", "topic": "alpha"},
        )
    )
    store.add_message_if_new(
        _teams_message(
            "cursor-3",
            message_id="msg-3",
            created_at=datetime(2026, 3, 21, 10, 2, tzinfo=UTC),
            thread_id="thread-2",
            content="third",
            metadata={"source": "teams_graph_api", "topic": "beta"},
        )
    )
    store.add_message_if_new(
        _teams_message(
            "cursor-4",
            message_id="msg-4",
            created_at=datetime(2026, 3, 21, 10, 3, tzinfo=UTC),
            team_id="team-b",
            channel_id="channel-9",
            content="ignore me",
        )
    )
    raw_before = store.read_raw()

    output = _query(
        runtime=runtime,
        communication_target_id="teams-primary",
    )

    assert store.read_raw() == raw_before
    assert output["success"] is True
    assert output["provider"] == "teams"
    assert output["communication_target_id"] == "teams-primary"
    assert output["target"] == "team-a/channel-1"
    assert output["target_type"] == "channel"
    assert output["count"] == 3
    assert output["next_cursor"] == "cursor-3"
    assert [message["message_id"] for message in output["messages"]] == [
        "msg-1",
        "msg-2",
        "msg-3",
    ]
    assert output["messages"][0]["channel_target"] == "team-a/channel-1"
    assert output["messages"][0]["target"] == "team-a/channel-1"
    assert output["messages"][0]["target_type"] == "channel"
    assert output["messages"][0]["thread_id"] == "thread-1"
    assert output["messages"][0]["metadata"]["topic"] == "alpha"


def test_query_communication_messages_teams_supports_cursor_before_cursor_limit_and_thread_id(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    _write_targets(
        tmp_path,
        [
            {
                "id": "teams-primary",
                "provider": "teams",
                "kind": "teams",
                "address": "team-a/channel-1",
                "policy": {"capabilities": ["read"]},
            }
        ],
    )
    store = TeamsInboxStore(tmp_path / "runtime" / "teams-inbox-store.json")
    for index, thread_id in enumerate(["thread-1", "thread-1", "thread-2"], start=1):
        store.add_message_if_new(
            _teams_message(
                f"cursor-{index}",
                message_id=f"msg-{index}",
                created_at=datetime(2026, 3, 21, 10, index, tzinfo=UTC),
                thread_id=thread_id,
                content=f"message-{index}",
            )
        )

    cursor_output = _query(
        runtime=runtime,
        communication_target_id="teams-primary",
        cursor="cursor-1",
        limit=1,
        thread_id="thread-1",
        query_id="query-communication-messages-cursor",
    )
    before_output = _query(
        runtime=runtime,
        communication_target_id="teams-primary",
        before_cursor="cursor-3",
        limit=2,
        query_id="query-communication-messages-before",
    )

    assert [message["message_id"] for message in cursor_output["messages"]] == ["msg-2"]
    assert cursor_output["count"] == 1
    assert cursor_output["next_cursor"] == "cursor-2"

    assert [message["message_id"] for message in before_output["messages"]] == [
        "msg-1",
        "msg-2",
    ]
    assert before_output["count"] == 2
    assert before_output["next_cursor"] == "cursor-2"


def test_query_communication_messages_defaults_and_bounds_limit(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    _write_targets(
        tmp_path,
        [
            {
                "id": "teams-primary",
                "provider": "teams",
                "kind": "teams",
                "address": "team-a/channel-1",
                "policy": {"capabilities": ["read"]},
            }
        ],
    )
    store = TeamsInboxStore(tmp_path / "runtime" / "teams-inbox-store.json")
    for index in range(1, 56):
        store.add_message_if_new(
            _teams_message(
                f"cursor-{index:03d}",
                message_id=f"msg-{index:03d}",
                created_at=datetime(2026, 3, 21, 10, index, tzinfo=UTC),
                content=f"message-{index}",
            )
        )

    output = _query(
        runtime=runtime,
        communication_target_id="teams-primary",
    )

    assert output["count"] == 50
    assert output["next_cursor"] == "cursor-050"
    assert [message["message_id"] for message in output["messages"]][-1] == "msg-050"

    with pytest.raises(ValueError, match="limit must be between 1 and 200"):
        _query(
            runtime=runtime,
            communication_target_id="teams-primary",
            limit=201,
            query_id="query-communication-messages-limit-too-large",
        )


def test_query_communication_messages_returns_discord_messages_and_preserves_store(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    _write_targets(
        tmp_path,
        [
            {
                "id": "discord-ops",
                "provider": "discord",
                "kind": "discord",
                "address": "123456789012345678/234567890123456789",
                "policy": {"capabilities": ["read"]},
            }
        ],
    )
    store = DiscordInboxStore(tmp_path / "runtime" / "discord-inbox-store.json")
    store.advance_session_cursor("session-1", "234567890123456789", "2026-03-21T00:00:00Z")
    store.add_message_if_new(
        _discord_message(
            "msg-1",
            created_at=datetime(2026, 3, 21, 0, 0, 1, tzinfo=UTC),
            content="first",
        )
    )
    store.add_message_if_new(
        _discord_message(
            "msg-2",
            created_at=datetime(2026, 3, 21, 0, 0, 2, tzinfo=UTC),
            content="second",
        )
    )
    store.add_message_if_new(
        _discord_message(
            "msg-3",
            created_at=datetime(2026, 3, 21, 0, 0, 3, tzinfo=UTC),
            content="third",
        )
    )
    store.add_message_if_new(
        _discord_message(
            "msg-4",
            channel_id="111111111111111111",
            created_at=datetime(2026, 3, 21, 0, 0, 4, tzinfo=UTC),
            content="ignore me",
        )
    )
    raw_before = store.read_raw()

    output = _query(
        runtime=runtime,
        communication_target_id="discord-ops",
    )

    assert store.read_raw() == raw_before
    assert output["success"] is True
    assert output["provider"] == "discord"
    assert output["communication_target_id"] == "discord-ops"
    assert output["target"] == "123456789012345678/234567890123456789"
    assert output["target_type"] == "discord"
    assert output["count"] == 3
    assert output["next_cursor"] == "2026-03-21T00:00:03Z"
    assert [message["message_id"] for message in output["messages"]] == [
        "msg-1",
        "msg-2",
        "msg-3",
    ]
    assert [message["cursor"] for message in output["messages"]] == [
        "2026-03-21T00:00:01Z",
        "2026-03-21T00:00:02Z",
        "2026-03-21T00:00:03Z",
    ]
    assert output["messages"][0]["metadata"]["content"] == "first"
    assert output["messages"][0]["channel_id"] == "234567890123456789"
    assert output["messages"][0]["team_id"] == ""
    assert output["messages"][0]["chat_id"] is None


def test_query_communication_messages_discord_supports_cursor_and_limit(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    _write_targets(
        tmp_path,
        [
            {
                "id": "discord-ops",
                "provider": "discord",
                "kind": "discord",
                "address": "123456789012345678/234567890123456789",
                "policy": {"capabilities": ["read"]},
            }
        ],
    )
    store = DiscordInboxStore(tmp_path / "runtime" / "discord-inbox-store.json")
    for index in range(1, 4):
        store.add_message_if_new(
            _discord_message(
                f"msg-{index}",
                created_at=datetime(2026, 3, 21, 0, 0, index, tzinfo=UTC),
                content=f"message-{index}",
            )
        )

    output = _query(
        runtime=runtime,
        communication_target_id="discord-ops",
        cursor="2026-03-21T00:00:01Z",
        limit=1,
    )

    assert output["count"] == 1
    assert [message["message_id"] for message in output["messages"]] == ["msg-2"]
    assert output["next_cursor"] == "2026-03-21T00:00:02Z"


def test_query_communication_messages_rejects_discord_thread_id(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    _write_targets(
        tmp_path,
        [
            {
                "id": "discord-ops",
                "provider": "discord",
                "kind": "discord",
                "address": "123456789012345678/234567890123456789",
                "policy": {"capabilities": ["read"]},
            }
        ],
    )

    with pytest.raises(ValueError, match="does not support thread_id for Discord"):
        _query(
            runtime=runtime,
            communication_target_id="discord-ops",
            thread_id="thread-1",
        )


@pytest.mark.parametrize(
    ("targets", "communication_target_id", "message"),
    [
        ([], "missing-target", "unknown communication target: missing-target"),
        (
            [
                {
                    "id": "teams-primary",
                    "provider": "teams",
                    "kind": "teams",
                    "address": "team-a/channel-1",
                    "policy": {"capabilities": ["write"]},
                }
            ],
            "teams-primary",
            "communication target 'teams-primary' does not allow read capability",
        ),
        (
            [
                {
                    "id": "email-ops",
                    "provider": "email",
                    "kind": "email",
                    "address": "operator@example.com",
                    "policy": {"capabilities": ["read"], "auth_profile_ref": "email.ops"},
                }
            ],
            "email-ops",
            "does not support communication target provider 'email'",
        ),
    ],
)
def test_query_communication_messages_rejects_invalid_targets(
    tmp_path: Path,
    targets: list[dict[str, object]],
    communication_target_id: str,
    message: str,
) -> None:
    runtime = _runtime(tmp_path)
    if targets:
        _write_targets(tmp_path, targets)

    with pytest.raises(ValueError, match=message):
        _query(runtime=runtime, communication_target_id=communication_target_id)
