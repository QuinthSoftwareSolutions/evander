from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_sandbox.operator_steering import (
    OPERATOR_STEERING_NOTE_MAX_LENGTH,
    OperatorSteeringNoteStore,
)


def test_operator_steering_store_claim_release_and_deliver(tmp_path: Path) -> None:
    store = OperatorSteeringNoteStore(tmp_path / "operator-steering.db")
    created = store.create_note(
        session_id="session-1",
        dispatch_id="dispatch-1",
        worker_execution_id="worker-1",
        message="Please finish the implementation.",
        created_by="operator@example.com",
        now=datetime(2026, 6, 8, 12, 0, tzinfo=UTC),
    )

    claimed = store.claim_next_pending(
        session_id="session-1",
        dispatch_id="dispatch-1",
        now=datetime(2026, 6, 8, 12, 1, tzinfo=UTC),
    )
    assert claimed is not None
    assert claimed.note_id == created.note_id
    assert claimed.status == "injecting"
    assert claimed.delivery_attempts == 1

    released = store.release_claim(
        created.note_id,
        error="transport failed",
        now=datetime(2026, 6, 8, 12, 2, tzinfo=UTC),
    )
    assert released is not None
    assert released.status == "pending"
    assert released.error == "transport failed"

    reclaimed = store.claim_next_pending(
        session_id="session-1",
        dispatch_id="dispatch-1",
        now=datetime(2026, 6, 8, 12, 3, tzinfo=UTC),
    )
    assert reclaimed is not None
    assert reclaimed.delivery_attempts == 2

    delivered = store.mark_delivered(
        created.note_id,
        provider="openai",
        provider_endpoint="responses",
        now=datetime(2026, 6, 8, 12, 4, tzinfo=UTC),
    )
    assert delivered is not None
    assert delivered.status == "delivered"
    assert delivered.provider == "openai"
    assert delivered.provider_endpoint == "responses"
    assert delivered.delivered_at == datetime(2026, 6, 8, 12, 4, tzinfo=UTC)


def test_operator_steering_store_cancel_only_pending_notes(tmp_path: Path) -> None:
    store = OperatorSteeringNoteStore(tmp_path / "operator-steering.db")
    injecting = store.create_note(
        session_id="session-1",
        dispatch_id="dispatch-1",
        message="Do not cancel while injecting.",
    )
    store.claim_next_pending(session_id="session-1", dispatch_id="dispatch-1")
    pending = store.create_note(
        session_id="session-1",
        dispatch_id="dispatch-1",
        message="Cancel me.",
    )

    cancelled = store.cancel_note(pending.note_id)
    assert cancelled is not None
    assert cancelled.status == "cancelled"
    assert cancelled.cancelled_at is not None

    still_injecting = store.cancel_note(injecting.note_id)
    assert still_injecting is not None
    assert still_injecting.status == "injecting"


def test_operator_steering_store_creates_delivered_note(tmp_path: Path) -> None:
    store = OperatorSteeringNoteStore(tmp_path / "operator-steering.db")
    delivered = store.create_delivered_note(
        session_id="session-1",
        dispatch_id="dispatch-1",
        worker_execution_id="worker-replacement",
        message="Interrupt and steer now.",
        created_by="operator@example.com",
        provider="container_worker_vm",
        provider_endpoint="interrupt-and-resume",
        now=datetime(2026, 6, 8, 12, 5, tzinfo=UTC),
    )

    listed = store.list_notes(session_id="session-1", dispatch_id="dispatch-1")

    assert delivered.status == "delivered"
    assert delivered.delivered_at == datetime(2026, 6, 8, 12, 5, tzinfo=UTC)
    assert delivered.delivery_attempts == 1
    assert listed == [delivered]


def test_operator_steering_store_reclaims_stale_injecting_notes(tmp_path: Path) -> None:
    store = OperatorSteeringNoteStore(tmp_path / "operator-steering.db")
    note = store.create_note(
        session_id="session-1",
        dispatch_id="dispatch-1",
        message="Retry me.",
        now=datetime(2026, 6, 8, 12, 0, tzinfo=UTC),
    )
    store.claim_next_pending(
        session_id="session-1",
        dispatch_id="dispatch-1",
        now=datetime(2026, 6, 8, 12, 1, tzinfo=UTC),
    )

    reclaimed = store.claim_next_pending(
        session_id="session-1",
        dispatch_id="dispatch-1",
        now=datetime(2026, 6, 8, 12, 10, tzinfo=UTC),
        stale_after=timedelta(minutes=5),
    )

    assert reclaimed is not None
    assert reclaimed.note_id == note.note_id
    assert reclaimed.status == "injecting"
    assert reclaimed.delivery_attempts == 2


def test_operator_steering_store_rejects_overlong_messages(tmp_path: Path) -> None:
    store = OperatorSteeringNoteStore(tmp_path / "operator-steering.db")

    with pytest.raises(ValueError, match="message must be at most"):
        store.create_note(
            session_id="session-1",
            dispatch_id="dispatch-1",
            message="x" * (OPERATOR_STEERING_NOTE_MAX_LENGTH + 1),
        )
