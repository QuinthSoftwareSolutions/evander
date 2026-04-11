from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_sandbox.agent_prompt_store import AgentPromptRecord, AgentPromptStore


def _prompt(
    message_id: str,
    *,
    created_at: datetime,
    target_session_id: str = "target-session",
    target_vm_id: str | None = "vm-target",
    token_hash: str | None = None,
) -> AgentPromptRecord:
    return AgentPromptRecord(
        message_id=message_id,
        sender_session_id="sender-session",
        sender_vm_id="vm-sender",
        target_session_id=target_session_id,
        target_vm_id=target_vm_id,
        prompt_payload={"prompt": f"payload for {message_id}"},
        issued_token_hash=token_hash or f"hash-{message_id}",
        issued_token_claims={"aud": target_session_id},
        created_at=created_at,
        updated_at=created_at,
    )


def test_agent_prompt_store_creates_and_lists_unread_with_deterministic_sort(tmp_path: Path) -> None:
    store = AgentPromptStore(tmp_path / "agent-prompt-store.json")
    late = datetime(2026, 4, 10, 0, 0, 2, tzinfo=timezone.utc)
    early = datetime(2026, 4, 10, 0, 0, 1, tzinfo=timezone.utc)

    assert store.create_prompt_message(_prompt("b", created_at=late)) is True
    assert store.create_prompt_message(_prompt("a", created_at=early)) is True

    unread = store.list_unread_prompts(target_session_id="target-session", target_vm_id="vm-target")

    assert [item.message_id for item in unread] == ["a", "b"]


def test_agent_prompt_store_marks_message_as_responded(tmp_path: Path) -> None:
    store = AgentPromptStore(tmp_path / "agent-prompt-store.json")
    created = datetime(2026, 4, 10, 0, 0, 1, tzinfo=timezone.utc)
    store.create_prompt_message(_prompt("m-1", created_at=created))

    updated = datetime(2026, 4, 10, 0, 5, 0, tzinfo=timezone.utc)
    changed = store.mark_prompt_status("m-1", status="responded", updated_at=updated)

    assert changed is not None
    assert changed.status == "responded"
    assert changed.updated_at == updated
    assert store.list_unread_prompts(target_session_id="target-session") == []


def test_agent_prompt_store_rejects_token_replay(tmp_path: Path) -> None:
    store = AgentPromptStore(tmp_path / "agent-prompt-store.json")
    created = datetime(2026, 4, 10, 0, 0, 1, tzinfo=timezone.utc)
    store.create_prompt_message(_prompt("m-1", created_at=created, token_hash="token-1"))

    consumed_at = datetime(2026, 4, 10, 0, 1, 0, tzinfo=timezone.utc)
    first = store.validate_and_consume_token("token-1", expected_message_id="m-1", consumed_at=consumed_at)
    second = store.validate_and_consume_token("token-1", expected_message_id="m-1")

    assert first is not None
    assert first.token_used_at == consumed_at
    assert second is None


def test_agent_prompt_store_rejects_duplicate_token_hash_during_create(tmp_path: Path) -> None:
    store = AgentPromptStore(tmp_path / "agent-prompt-store.json")
    created = datetime(2026, 4, 10, 0, 0, 1, tzinfo=timezone.utc)
    assert store.create_prompt_message(_prompt("m-1", created_at=created, token_hash="token-1"))

    with pytest.raises(ValueError, match="Token hash is already tracked: token-1"):
        store.create_prompt_message(
            _prompt("m-2", created_at=created + timedelta(seconds=1), token_hash="token-1")
        )


def test_agent_prompt_store_compacts_old_closed_messages(tmp_path: Path) -> None:
    store = AgentPromptStore(tmp_path / "agent-prompt-store.json")
    old = datetime.now(timezone.utc) - timedelta(days=120)
    recent = datetime.now(timezone.utc)

    store.create_prompt_message(_prompt("old", created_at=old, token_hash="token-old"))
    store.create_prompt_message(_prompt("new", created_at=recent, token_hash="token-new"))

    store.mark_prompt_status("old", status="closed", updated_at=old)

    # Trigger compaction on subsequent write.
    store.create_prompt_message(
        _prompt(
            "newer",
            created_at=recent + timedelta(seconds=1),
            token_hash="token-newer",
        )
    )

    payload = store.read_raw()
    assert "old" not in payload["messages"]
    assert "token-old" not in payload["token_index"]
