from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import time
from threading import Barrier, Thread

import pytest

from evander.messaging.agent_prompt_store import AgentPromptRecord, AgentPromptStore
from evander.storage import JsonObjectStore


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


def test_agent_prompt_store_creates_and_lists_unread_with_deterministic_sort(
    tmp_path: Path,
) -> None:
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
    first = store.validate_and_consume_token(
        "token-1", expected_message_id="m-1", consumed_at=consumed_at
    )
    second = store.validate_and_consume_token("token-1", expected_message_id="m-1")

    assert first is not None
    assert first.token_used_at == consumed_at
    assert second is None


def test_agent_prompt_store_replaces_active_token_hash(tmp_path: Path) -> None:
    store = AgentPromptStore(tmp_path / "agent-prompt-store.json")
    created = datetime(2026, 4, 10, 0, 0, 1, tzinfo=timezone.utc)
    store.create_prompt_message(_prompt("m-1", created_at=created, token_hash="token-1"))

    replaced = store.replace_issued_token(
        "m-1",
        issued_token_hash="token-2",
        issued_token_claims={"aud": "target-session", "nonce": "n-2"},
        updated_at=created + timedelta(seconds=5),
    )

    assert replaced is not None
    assert replaced.issued_token_hash == "token-2"
    assert replaced.issued_token_claims["nonce"] == "n-2"
    assert store.validate_and_consume_token("token-1", expected_message_id="m-1") is None
    assert store.validate_and_consume_token("token-2", expected_message_id="m-1") is not None


def test_agent_prompt_store_finds_message_by_sender_scoped_idempotency_key(tmp_path: Path) -> None:
    store = AgentPromptStore(tmp_path / "agent-prompt-store.json")
    created = datetime(2026, 4, 10, 0, 0, 1, tzinfo=timezone.utc)
    record = _prompt("m-1", created_at=created)
    record.prompt_payload["idempotency_key"] = "retry-key"
    store.create_prompt_message(record)

    found = store.find_prompt_by_idempotency_key(
        sender_session_id="sender-session",
        idempotency_key="retry-key",
    )

    assert found is not None
    assert found.message_id == "m-1"


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


def test_agent_prompt_store_initializes_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "agent-prompt-store.json"

    store = AgentPromptStore(path)

    assert store.read_raw() == {
        "messages": {},
        "token_index": {},
        "consumed_tokens": {},
        "idempotency_index": {},
    }


def test_agent_prompt_store_consumes_token_once_across_store_instances(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "agent-prompt-store.json"
    first_store = AgentPromptStore(path)
    second_store = AgentPromptStore(path)
    created = datetime(2026, 4, 10, 0, 0, 1, tzinfo=timezone.utc)
    first_store.create_prompt_message(_prompt("m-1", created_at=created, token_hash="token-1"))

    original_write = JsonObjectStore._write_json_payload

    def delayed_write(self, payload):
        time.sleep(0.05)
        return original_write(self, payload)

    monkeypatch.setattr(JsonObjectStore, "_write_json_payload", delayed_write)

    barrier = Barrier(3)
    results: dict[str, AgentPromptRecord | None] = {}
    consumed_at = datetime(2026, 4, 10, 0, 1, 0, tzinfo=timezone.utc)

    def consume(store: AgentPromptStore, name: str) -> None:
        barrier.wait(timeout=5)
        results[name] = store.validate_and_consume_token(
            "token-1", expected_message_id="m-1", consumed_at=consumed_at
        )

    threads = [
        Thread(target=consume, args=(first_store, "first")),
        Thread(target=consume, args=(second_store, "second")),
    ]

    for thread in threads:
        thread.start()
    barrier.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=5)

    for thread in threads:
        assert not thread.is_alive()

    assert sum(result is not None for result in results.values()) == 1
    assert sum(result is None for result in results.values()) == 1
    store = AgentPromptStore(path)
    assert store.read_raw()["consumed_tokens"]["token-1"] == "2026-04-10T00:01:00Z"


@pytest.mark.parametrize("payload_text", ["", "{not json"])
def test_agent_prompt_store_raises_on_empty_or_invalid_json(
    tmp_path: Path,
    payload_text: str,
) -> None:
    path = tmp_path / "agent-prompt-store.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload_text, encoding="utf-8")

    store = AgentPromptStore(path)

    with pytest.raises(json.JSONDecodeError):
        store.read_raw()

    with pytest.raises(json.JSONDecodeError):
        store.create_prompt_message(
            _prompt("m-1", created_at=datetime(2026, 4, 10, 0, 0, 1, tzinfo=timezone.utc))
        )
