from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from agent_sandbox.triggers.providers._cursor_state import (
    TriggerCursorStore,
    latest_cursor_timestamp_text,
    merge_cursor_ids,
)


def test_cursor_state_helpers_merge_unique_ids_and_latest_timestamp() -> None:
    newer = datetime(2026, 4, 3, 12, 5, tzinfo=UTC)

    assert merge_cursor_ids(
        ["cursor-1", "cursor-2"],
        ["cursor-2", "cursor-3", "", None, "cursor-4"],
        limit=3,
    ) == ["cursor-2", "cursor-3", "cursor-4"]
    assert latest_cursor_timestamp_text("2026-04-03T12:00:00Z", newer) == (
        "2026-04-03T12:05:00Z"
    )
    assert latest_cursor_timestamp_text("2026-04-03T12:10:00Z", newer) == (
        "2026-04-03T12:10:00Z"
    )


def test_trigger_cursor_store_update_merges_without_overwriting_other_keys(
    tmp_path: Path,
) -> None:
    path = tmp_path / "trigger-cursors.json"
    store = TriggerCursorStore(path)
    store.put("trigger-b", {"preserved": True})
    store.put(
        "trigger-a",
        {
            "emitted_unread_message_ids": ["message-1"],
            "last_received_at": "2026-04-03T12:00:00Z",
            "other": {"keep": True},
        },
    )

    published_at = datetime(2026, 4, 3, 12, 5, tzinfo=UTC)

    def acknowledge(state: dict[str, object]) -> dict[str, object]:
        state["emitted_unread_message_ids"] = merge_cursor_ids(
            state.get("emitted_unread_message_ids"),
            ["message-2", "message-3", "message-2"],
            limit=3,
        )
        state["last_received_at"] = latest_cursor_timestamp_text(
            state.get("last_received_at"),
            published_at,
        )
        return state

    updated = store.update("trigger-a", acknowledge)

    assert updated == {
        "emitted_unread_message_ids": ["message-1", "message-2", "message-3"],
        "last_received_at": "2026-04-03T12:05:00Z",
        "other": {"keep": True},
    }
    assert store.get("trigger-b") == {"preserved": True}
    assert store.get("trigger-a") == updated

    repeated = store.update("trigger-a", acknowledge)

    assert repeated == updated
    assert store.get("trigger-a") == updated
