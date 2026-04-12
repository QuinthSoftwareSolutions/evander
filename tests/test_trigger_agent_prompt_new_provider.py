from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_sandbox.agent_prompt_store import AgentPromptRecord, AgentPromptStore
from agent_sandbox.config import GatewayConfig
from agent_sandbox.events.models import AgentPromptNewTriggerDefinition
from agent_sandbox.models import SessionManifest
from agent_sandbox.storage import JsonStore
from agent_sandbox.triggers.providers.agent_prompt_new import AgentPromptNewTriggerProvider
from agent_sandbox.triggers.providers.base import TriggerRuntimeContext


def _context(runtime_dir: Path) -> TriggerRuntimeContext:
    config = GatewayConfig(
        runtime_dir=runtime_dir,
        session_store_path=runtime_dir / "sessions.json",
    )
    return TriggerRuntimeContext(resolved_config=config, metadata={"directory_path": "root/team-a"})


def test_agent_prompt_new_trigger_emits_once_per_unread_message(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    store = AgentPromptStore(runtime_dir / "agent-prompt-store.json")
    sessions = JsonStore(runtime_dir / "sessions.json", SessionManifest)
    sessions.put(
        "target-a",
        SessionManifest(
            session_id="target-a",
            directory_path="root/team-a",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    sessions.put(
        "target-b",
        SessionManifest(
            session_id="target-b",
            directory_path="root/team-b",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    created_at = datetime(2026, 4, 12, 10, 0, tzinfo=UTC)
    store.create_prompt_message(
        AgentPromptRecord(
            message_id="msg-1",
            sender_session_id="sender",
            target_session_id="target-a",
            prompt_payload={"prompt": "Fetch the prompt inbox.", "metadata": {"ticket": "123"}},
            issued_token_hash="token-hash-1",
            issued_token_claims={"nonce": "n-1"},
            created_at=created_at,
            updated_at=created_at,
        )
    )
    store.create_prompt_message(
        AgentPromptRecord(
            message_id="msg-2",
            sender_session_id="sender",
            target_session_id="target-b",
            prompt_payload={"prompt": "Other directory"},
            issued_token_hash="token-hash-2",
            issued_token_claims={"nonce": "n-2"},
            created_at=created_at + timedelta(seconds=1),
            updated_at=created_at + timedelta(seconds=1),
        )
    )

    provider = AgentPromptNewTriggerProvider()
    provider.initialize(
        trigger_definition=AgentPromptNewTriggerDefinition(
            id="agent-prompt-trigger",
            display_name="Agent prompt",
            type="trigger:agent:prompt:new",
        ),
        runtime_context=_context(runtime_dir),
    )

    events = provider.poll()
    assert len(events) == 1
    assert events[0].event_id == "agent-prompt-trigger:msg-1"
    assert events[0].payload["session_id"] == "target-a"
    assert events[0].payload["agent_prompt"]["message_id"] == "msg-1"
    assert provider.poll() == []

    store.record_response("msg-1", response_text="done", responder_session_id="target-a")
    store.create_prompt_message(
        AgentPromptRecord(
            message_id="msg-3",
            sender_session_id="sender",
            target_session_id="target-a",
            prompt_payload={"prompt": "Another unread"},
            issued_token_hash="token-hash-3",
            issued_token_claims={"nonce": "n-3"},
            created_at=created_at + timedelta(seconds=2),
            updated_at=created_at + timedelta(seconds=2),
        )
    )

    next_events = provider.poll()
    assert [event.event_id for event in next_events] == ["agent-prompt-trigger:msg-3"]
