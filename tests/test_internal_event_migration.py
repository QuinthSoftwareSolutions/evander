from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from config_helpers import canonical_worker_config
from evander.events.internal import (
    InternalEvent,
    InternalEventBus,
    InternalEventSubscriptionSpec,
)
from evander.events.scheduled import ScheduledInternalEvent, ScheduledInternalEventStore
from evander.orchestration import internal_event_migration as migration


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _legacy_config(runtime_dir: Path):
    return canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "listener",
                    "display_name": "Listener",
                    "type": "trigger:event",
                    "event_type": "custom.operator_event",
                }
            ],
        }
    )


def _legacy_sources(runtime_dir: Path) -> tuple[InternalEvent, InternalEvent]:
    _write_json(
        runtime_dir.parent / "config" / "trigger-orchestrator" / "config.json",
        {
            "runtime_dir": str(runtime_dir),
            "internal_event_store_path": str(runtime_dir / "internal-events.json"),
            "scheduled_internal_event_store_path": str(
                runtime_dir / "scheduled-internal-events.json"
            ),
        },
    )
    delivered = InternalEvent(
        event_id="evt-delivered",
        event_type="custom.operator_event",
        payload={"value": "delivered"},
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    pending = InternalEvent(
        event_id="evt-pending",
        event_type="custom.operator_event",
        payload={"value": "pending"},
        created_at=datetime(2026, 8, 2, tzinfo=UTC),
    )
    _write_json(
        runtime_dir / "internal-events.json",
        {
            delivered.event_id: delivered.model_dump(mode="json"),
            pending.event_id: pending.model_dump(mode="json"),
        },
    )
    published_schedule = ScheduledInternalEvent(
        schedule_id="sched-published",
        event_type="custom.operator_event",
        run_at=datetime(2026, 8, 1, tzinfo=UTC),
        status="published",
        created_at=datetime(2026, 7, 31, tzinfo=UTC),
        published_at=datetime(2026, 8, 1, tzinfo=UTC),
        published_event_id=delivered.event_id,
    )
    pending_schedule = ScheduledInternalEvent(
        schedule_id="sched-pending",
        event_type="custom.operator_event",
        run_at=datetime.now(UTC) + timedelta(days=1),
        created_at=datetime.now(UTC),
    )
    _write_json(
        runtime_dir / "scheduled-internal-events.json",
        {
            published_schedule.schedule_id: published_schedule.model_dump(mode="json"),
            pending_schedule.schedule_id: pending_schedule.model_dump(mode="json"),
        },
    )
    _write_json(
        runtime_dir / "trigger-cursors-event.json",
        {"listener": {"emitted_internal_event_ids": [delivered.event_id]}},
    )
    _write_json(runtime_dir / "trigger-cursors-agent-artifact-staged.json", {})
    return delivered, pending


def test_offline_migration_preserves_pending_delivery_and_archives_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = _legacy_config(runtime_dir)
    monkeypatch.setattr(migration, "_worker_config", lambda *_args, **_kwargs: config)
    delivered, pending = _legacy_sources(runtime_dir)

    checked = migration.check_internal_event_migration(runtime_dir)

    assert checked.safe_to_apply is True
    assert checked.internal_event_count == 2
    assert checked.scheduled_event_count == 2
    assert checked.pending_schedule_count == 1
    assert checked.subscription_count == 1
    assert checked.delivered_cursor_count == 1
    assert checked.retired_config_keys == (
        "internal_event_store_path",
        "scheduled_internal_event_store_path",
    )

    applied = migration.apply_internal_event_migration(runtime_dir)

    assert applied.marker_state == "complete"
    assert applied.internal_event_count == 2
    assert not any(
        (runtime_dir / name).exists() for name in migration.LEGACY_INTERNAL_EVENT_FILENAMES
    )
    marker = json.loads(migration.migration_marker_path(runtime_dir).read_text(encoding="utf-8"))
    archive_path = Path(marker["archive_path"])
    assert marker["state"] == "complete"
    assert (archive_path / "internal-events.json").is_file()
    assert (archive_path / "manifest.json").is_file()
    assert (archive_path / "trigger-orchestrator-config.json").is_file()
    config_payload = json.loads(
        (runtime_dir.parent / "config" / "trigger-orchestrator" / "config.json").read_text(
            encoding="utf-8"
        )
    )
    assert "internal_event_store_path" not in config_payload
    assert "scheduled_internal_event_store_path" not in config_payload

    bus = InternalEventBus(config.orchestration_store_path)
    assert [event.event_id for event in bus.list_events()] == [
        delivered.event_id,
        pending.event_id,
    ]
    spec = InternalEventSubscriptionSpec(
        trigger_id="listener",
        trigger_type="trigger:event",
        event_type="custom.operator_event",
        filter_payload={
            "event_type": "custom.operator_event",
            "correlation_key": None,
        },
    )
    assert [
        event.event_id for event in bus.poll_subscription(spec, matcher=lambda _event: True)
    ] == [pending.event_id]
    schedules = ScheduledInternalEventStore(config.orchestration_store_path).list_all()
    assert [event.status for event in schedules] == ["published", "pending"]

    repeated = migration.apply_internal_event_migration(runtime_dir)
    assert repeated.marker_state == "complete"
    assert len(InternalEventBus(config.orchestration_store_path).list_events()) == 2


def test_offline_migration_uses_configured_legacy_store_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = _legacy_config(runtime_dir)
    monkeypatch.setattr(migration, "_worker_config", lambda *_args, **_kwargs: config)
    delivered, pending = _legacy_sources(runtime_dir)
    custom_event_path = tmp_path / "legacy" / "custom-events.json"
    custom_schedule_path = tmp_path / "legacy" / "custom-schedules.json"
    custom_event_path.parent.mkdir(parents=True)
    (runtime_dir / "internal-events.json").replace(custom_event_path)
    (runtime_dir / "scheduled-internal-events.json").replace(custom_schedule_path)
    config_path = runtime_dir.parent / "config" / "trigger-orchestrator" / "config.json"
    config_payload = json.loads(config_path.read_text(encoding="utf-8"))
    config_payload["internal_event_store_path"] = "legacy/custom-events.json"
    config_payload["scheduled_internal_event_store_path"] = str(custom_schedule_path)
    _write_json(config_path, config_payload)

    checked = migration.check_internal_event_migration(runtime_dir)

    assert checked.safe_to_apply is True
    assert checked.internal_event_count == 2
    assert checked.scheduled_event_count == 2

    applied = migration.apply_internal_event_migration(runtime_dir)

    assert applied.marker_state == "complete"
    assert not custom_event_path.exists()
    assert not custom_schedule_path.exists()
    marker = json.loads(migration.migration_marker_path(runtime_dir).read_text(encoding="utf-8"))
    assert marker["source_paths"]["internal-events.json"] == str(custom_event_path)
    assert marker["source_paths"]["scheduled-internal-events.json"] == str(custom_schedule_path)
    archive_path = Path(marker["archive_path"])
    assert (archive_path / "internal-events.json").is_file()
    assert (archive_path / "scheduled-internal-events.json").is_file()
    assert [
        event.event_id for event in InternalEventBus(config.orchestration_store_path).list_events()
    ] == [
        delivered.event_id,
        pending.event_id,
    ]


def test_offline_migration_allows_config_only_cutover(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = _legacy_config(runtime_dir)
    monkeypatch.setattr(migration, "_worker_config", lambda *_args, **_kwargs: config)
    config_path = runtime_dir.parent / "config" / "trigger-orchestrator" / "config.json"
    _write_json(
        config_path,
        {
            "runtime_dir": str(runtime_dir),
            "internal_event_store_path": str(runtime_dir / "never-created-events.json"),
            "scheduled_internal_event_store_path": str(
                runtime_dir / "never-created-schedules.json"
            ),
        },
    )

    checked = migration.check_internal_event_migration(runtime_dir)

    assert checked.safe_to_apply is True
    assert checked.source_hashes == {}
    assert checked.retired_config_keys == (
        "internal_event_store_path",
        "scheduled_internal_event_store_path",
    )

    applied = migration.apply_internal_event_migration(runtime_dir)

    assert applied.marker_state == "complete"
    assert applied.internal_event_count == 0
    sanitized = json.loads(config_path.read_text(encoding="utf-8"))
    assert "internal_event_store_path" not in sanitized
    assert "scheduled_internal_event_store_path" not in sanitized


def test_cutover_guard_fails_closed_for_legacy_or_incomplete_state(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    _write_json(runtime_dir / "internal-events.json", {})

    with pytest.raises(RuntimeError, match="offline SQLite migration"):
        migration.assert_internal_event_sqlite_cutover(runtime_dir)

    _write_json(
        migration.migration_marker_path(runtime_dir),
        {"migration": migration.MIGRATION_NAME, "state": "archived"},
    )
    with pytest.raises(RuntimeError, match="migration is incomplete"):
        migration.assert_internal_event_sqlite_cutover(runtime_dir)


def test_migration_check_rejects_cursor_references_to_missing_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = _legacy_config(runtime_dir)
    monkeypatch.setattr(migration, "_worker_config", lambda *_args, **_kwargs: config)
    _write_json(runtime_dir / "internal-events.json", {})
    _write_json(
        runtime_dir / "trigger-cursors-event.json",
        {"listener": {"emitted_internal_event_ids": ["evt-missing"]}},
    )

    checked = migration.check_internal_event_migration(runtime_dir)

    assert checked.safe_to_apply is False
    assert checked.errors == ("cursor listener references 1 missing internal events",)
