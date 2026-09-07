from datetime import UTC, datetime, timedelta

import pytest

from evander.operator_steering import OPERATOR_STEERING_NOTE_MAX_LENGTH
from evander_team_addon.models import TeamConversation, TeamConversationMessage, TeamState
from evander_team_addon.operator_chat_context import operator_chat_conversation_context
from evander_team_addon.operator_chat_steering import _steering_prompt


def _conversation():
    conversation = TeamConversation(
        id="chat-" + "a" * 32,
        agent_id="backend",
        operator_user_id="jonathan",
        operator_display_name="Jonathan",
    )
    now = datetime(2026, 9, 7, tzinfo=UTC)
    messages = [
        TeamConversationMessage(
            id="chatmsg-" + str(index) * 32,
            conversation_id=conversation.id,
            sender=sender,
            body=body,
            created_at=now + timedelta(seconds=index),
        )
        for index, (sender, body) in enumerate(
            [
                ("operator", "Can we implement a sub-order splitting pilot?"),
                ("agent", "Yes, a configuration-driven sub-order pilot can proceed."),
                ("operator", "Sounds good."),
                ("operator", "Did you proceed with that?"),
            ]
        )
    ]
    messages[1].source_message_ids = [messages[0].id]
    state = TeamState.model_construct(
        conversations={conversation.id: conversation},
        conversation_messages={item.id: item for item in messages},
    )
    return state, messages


@pytest.mark.parametrize("max_chars", [0, 1, 100, 500, 4000])
def test_conversation_history_is_bounded_and_excludes_other_conversations_and_future(max_chars):
    state, messages = _conversation()
    source = messages[-1]
    foreign = messages[0].model_copy(
        update={
            "id": "foreign-message",
            "conversation_id": "other-chat",
            "body": "PRIVATE TOPIC",
        }
    )
    future = source.model_copy(
        update={
            "id": "future-message",
            "body": "LATER CORRECTION",
            "created_at": source.created_at + timedelta(seconds=1),
        }
    )
    state.conversation_messages.update({foreign.id: foreign, future.id: future})
    messages[1].source_message_ids.extend([foreign.id, future.id])

    history = operator_chat_conversation_context(state, source, max_chars=max_chars)

    assert len(history) <= max_chars
    assert "PRIVATE TOPIC" not in history
    assert "foreign-message" not in history
    assert "LATER CORRECTION" not in history
    assert "future-message" not in history
    assert source.body not in history
    if max_chars == 4000:
        assert messages[0].body in history
        assert messages[1].body in history
        assert f"reply to {messages[0].id}" in history
        assert history.index(messages[0].body) < history.index(messages[1].body)


def test_relay_followup_keeps_pilot_context_and_reply_links_with_realistic_ids():
    state, messages = _conversation()
    prompt = _steering_prompt(state, messages[-1], dispatch_id="team-dispatch-" + "b" * 32)

    assert len(prompt) <= OPERATOR_STEERING_NOTE_MAX_LENGTH
    assert "configuration-driven sub-order pilot" in prompt
    assert "Sounds good." in prompt
    assert f"reply to {messages[0].id}" in prompt
    assert "Current message:\nDid you proceed with that?" in prompt
    assert "not the current work task" in prompt
    assert f"source_message_ids={[messages[-1].id]!r}" in prompt


def test_long_relay_messages_preserve_reply_instructions_and_history_within_limit():
    state, messages = _conversation()
    messages[-1].body = "FOLLOW-UP " * 2000
    messages[1].body = "PILOT PLAN " * 2000
    prompt = _steering_prompt(state, messages[-1], dispatch_id="team-dispatch-" + "b" * 32)

    assert len(prompt) <= OPERATOR_STEERING_NOTE_MAX_LENGTH
    assert "PILOT PLAN" in prompt
    assert "Current message:\nFOLLOW-UP" in prompt
    assert "team_send_operator_chat_message" in prompt
    assert f"source_message_ids={[messages[-1].id]!r}" in prompt
