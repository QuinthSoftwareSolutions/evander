from __future__ import annotations

from datetime import UTC, datetime

import pytest

from evander_team_addon.chat_attachments import (
    TeamChatAttachmentPolicyService,
    TeamChatAttachmentStorage,
)
from evander_team_addon.models import AgentRuntime
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.services.admin_service import TeamAdminService
from evander_team_addon.services.chat_service import TeamChatService
from evander_team_addon.services.commands.chat_commands import TeamChatCommandService


def _repo_with_chat(tmp_path):
    repo = TeamStateRepository(tmp_path)
    TeamAdminService(repo).create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
        expected_revision=0,
    )
    state, conversation, _created = TeamChatService(repo).create_or_get_conversation(
        agent_id="manager",
        operator_user_id="alice@example.com",
        operator_display_name="Alice",
        expected_revision=repo.read().revision,
    )
    state, message = TeamChatService(repo).record_operator_chat_message(
        conversation_id=conversation.id,
        body="Please reply with the result.",
        created_by="operator:alice@example.com",
        expected_revision=state.revision,
    )

    def lease(current):
        current.agent_runtimes["manager"] = AgentRuntime(
            agent_id="manager",
            active_dispatch_id=f"team-chat:{message.id}",
            wake_leased_until=datetime(2099, 1, 1, tzinfo=UTC),
            wake_reason="team_chat",
        )
        current.conversation_messages[message.id].status = "running"
        current.conversation_messages[message.id].dispatch_id = f"team-chat:{message.id}"
        return current

    repo.update(lease)
    return repo, conversation, message


def test_team_chat_attachment_policy_inherits_inside_addon_state(tmp_path) -> None:
    repo = TeamStateRepository(tmp_path)
    service = TeamChatAttachmentPolicyService(repo)
    root = service.effective_policy("root/product/backend")
    assert root.operator_to_agent_enabled is True
    assert root.agent_send_requires_approval is True

    state = service.set_override(
        directory_path="root/product",
        values={"max_attachment_bytes": 2048, "agent_send_requires_approval": False},
        updated_by="operator:alice",
        expected_revision=repo.read().revision,
    )
    inherited = service.effective_policy("root/product/backend", state=state)
    assert inherited.max_attachment_bytes == 2048
    assert inherited.agent_send_requires_approval is False
    assert inherited.allowed_mime_types == root.allowed_mime_types


def test_team_chat_staging_is_session_owned_and_promotes_deterministically(tmp_path) -> None:
    storage = TeamChatAttachmentStorage(tmp_path)
    staged = storage.stage_agent_file(
        session_id="session-a",
        source_path="/home/agent/report.txt",
        content=b"report",
        filename="report.txt",
        mime_type="text/plain",
    )
    with pytest.raises(PermissionError, match="another session"):
        storage.get_staged(staged.id, session_id="session-b")

    first = storage.promote_staged([staged.id], session_id="session-a")
    second = storage.promote_staged([staged.id], session_id="session-a")
    assert first == second
    assert storage.asset_path(first[0].id).read_bytes() == b"report"

    storage.staging_blob_path(staged.id).write_bytes(b"changed")
    with pytest.raises(ValueError, match="no longer matches"):
        storage.get_staged(staged.id, session_id="session-a")


def test_agent_chat_reply_persists_staged_attachment_with_message(tmp_path) -> None:
    repo, conversation, source_message = _repo_with_chat(tmp_path)
    storage = TeamChatAttachmentStorage(tmp_path)
    staged = storage.stage_agent_file(
        session_id="session-manager",
        source_path="/home/agent/result.json",
        content=b'{"ok":true}',
        filename="result.json",
        mime_type="application/json",
    )
    prepared = storage.promote_staged([staged.id], session_id="session-manager")

    state, result = TeamChatCommandService(repo).reply_to_chat_command(
        session_id="session-manager",
        conversation_id=conversation.id,
        message_id=source_message.id,
        body="",
        request_id="reply-with-file",
        attachments=prepared,
        source_attachment_ids=[staged.id],
    )

    reply = state.conversation_messages[result["chat_message_id"]]
    assert reply.body is None
    assert reply.attachment_ids == [prepared[0].id]
    attachment = state.chat_attachments[prepared[0].id]
    assert attachment.message_id == reply.id
    assert attachment.created_by_session_id == "session-manager"
    assert TeamStateRepository(tmp_path).read().chat_attachments[attachment.id].sha256
