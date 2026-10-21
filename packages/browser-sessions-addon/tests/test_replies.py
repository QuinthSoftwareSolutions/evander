import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from evander_browser_sessions_addon import mcp
from evander_browser_sessions_addon.models import BrowserError, MessageRequest
from evander_browser_sessions_addon.service import Service
from evander_browser_sessions_addon.state import Store


def question(service, session, request_id="question"):
    return service.enqueue_message(
        session.id, "user:operator", MessageRequest(request_id=request_id, body="Move the button")
    )["id"]


@pytest.mark.parametrize("session_fixture", ["hosted", "local"])
def test_multiple_replies_to_one_message_remain_ordered_in_its_transcript(
    domain, request, session_fixture
):
    session = request.getfixturevalue(session_fixture)
    service, _, _ = domain
    message_id = question(service, session)
    progress = service.reply("agent-a", session.id, message_id, "Working on it.")
    final = service.reply("agent-a", session.id, message_id, "The button has moved.")
    assert progress["id"] != final["id"]
    assert (
        service.reply("agent-a", session.id, message_id, "Working on it.")["id"] == progress["id"]
    )
    rows = service.store.messages(session.id)
    assert [row["body"] for row in rows] == [
        "Move the button",
        "Working on it.",
        "The button has moved.",
    ]
    assert [row["reply_to"] for row in rows[1:]] == [message_id, message_id]
    assert all(row["status"] == "answered" for row in rows)
    assert service.store.messages(session.id, "teams") == []
    assert service.store.messages(session.id, "discord") == []


def test_explicit_reply_ids_allow_repeated_text_and_deduplicate_concurrent_retries(domain, hosted):
    service, bindings, peers = domain
    message_id = question(service, hosted)

    def reply(_):
        return service.reply(
            "agent-a", hosted.id, message_id, "Still working.", request_id="update-one"
        )["id"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(reply, range(8)))
    assert len(set(ids)) == 1
    reopened = Service(
        service.runtime_dir, store=Store(service.runtime_dir), bindings=bindings, peer_factory=peers
    )
    assert (
        reopened.reply("agent-a", hosted.id, message_id, "Still working.", request_id="update-one")[
            "id"
        ]
        == ids[0]
    )
    second = reopened.reply(
        "agent-a", hosted.id, message_id, "Still working.", request_id="update-two"
    )
    assert second["id"] != ids[0]
    assert [row["body"] for row in reopened.store.messages(hosted.id)] == [
        "Move the button",
        "Still working.",
        "Still working.",
    ]


def test_reusing_a_reply_id_for_changed_text_or_another_request_is_rejected(domain, hosted):
    service, _, _ = domain
    first = question(service, hosted)
    second = question(service, hosted, "another-question")
    service.reply("agent-a", hosted.id, first, "Working.", request_id="update")
    for message_id, body in [(first, "Done."), (second, "Working.")]:
        with pytest.raises(BrowserError) as error:
            service.reply("agent-a", hosted.id, message_id, body, request_id="update")
        assert error.value.code == "message_conflict"
    assert [row["status"] for row in service.store.messages(hosted.id)[:2]] == [
        "answered",
        "queued",
    ]


def test_replies_still_require_the_owner_and_an_original_message_in_this_session(
    domain, hosted, local
):
    service, _, _ = domain
    message_id = question(service, hosted)
    reply = service.reply("agent-a", hosted.id, message_id, "Working.", request_id="update")
    for agent, session_id, parent_id in [
        ("agent-b", hosted.id, message_id),
        ("agent-a", local.id, message_id),
        ("agent-a", hosted.id, reply["id"]),
    ]:
        with pytest.raises(BrowserError) as error:
            service.reply(agent, session_id, parent_id, "Another reply.", request_id="next")
        assert error.value.status == 404
    assert len(service.store.messages(hosted.id)) == 2
    assert service.store.messages(local.id) == []


def test_upgrade_preserves_legacy_reply_retries_and_allows_a_followup(domain, hosted):
    service, _, _ = domain
    message_id = question(service, hosted)
    legacy = service.store.message(
        hosted.id,
        "viewer",
        "agent:agent-a",
        "reply-" + message_id,
        "Working.",
        reply_to=message_id,
    )
    assert service.reply("agent-a", hosted.id, message_id, "Working.")["id"] == legacy["id"]
    final = service.reply("agent-a", hosted.id, message_id, "Done.")
    assert final["id"] != legacy["id"]
    assert [row["body"] for row in service.store.messages(hosted.id)] == [
        "Move the button",
        "Working.",
        "Done.",
    ]


@pytest.fixture
def reply_tool(domain, monkeypatch):
    service, _, _ = domain
    monkeypatch.setattr(mcp, "Service", lambda *args: service)
    monkeypatch.setattr(mcp, "_resolve_session_id_from_context", lambda *args, **kwargs: "agent-a")
    server = FastMCP("browser-replies")
    mcp.register_mcp_tools(server, SimpleNamespace(runtime_dir=service.runtime_dir, service=None))
    return server


def test_mcp_reply_accepts_followups_and_optional_bounded_retry_identity(
    domain, hosted, reply_tool
):
    service, _, _ = domain
    arguments = {
        "browser_session_id": hosted.id,
        "message_id": question(service, hosted),
        "body": "Working.",
        "request_id": "progress-one",
    }

    def call(payload):
        return asyncio.run(reply_tool._tool_manager.call_tool("browser_session_reply", payload))

    progress = call(arguments)
    assert call(arguments)["id"] == progress["id"]
    final = call({**arguments, "body": "Done.", "request_id": "final-one"})
    followup = call(
        {
            key: value
            for key, value in {**arguments, "body": "Anything else?"}.items()
            if key != "request_id"
        }
    )
    assert len({progress["id"], final["id"], followup["id"]}) == 3
    tool = next(
        item
        for item in asyncio.run(reply_tool.list_tools())
        if item.name == "browser_session_reply"
    )
    assert "request_id" in tool.inputSchema["properties"]
    assert "request_id" not in tool.inputSchema["required"]
    for invalid in ["", "x" * 129, "spaces are invalid"]:
        with pytest.raises(ToolError):
            call({**arguments, "request_id": invalid})
    for invalid in ["", "x" * 16001]:
        with pytest.raises(ToolError):
            call({**arguments, "body": invalid})
    assert len(service.store.messages(hosted.id)) == 4
