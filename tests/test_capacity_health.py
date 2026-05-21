from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_sandbox.capacity_health import (
    DiskThresholds,
    HealthState,
    SessionStorageSample,
    TelemetryStatus,
    collect_disk_usage,
    collect_recursive_directory_size,
    top_session_storage,
)


def test_collect_disk_usage_reports_available_health_and_threshold_metadata(
    tmp_path: Path,
) -> None:
    thresholds = DiskThresholds(
        source="test_override",
        warning_free_percent=0.0,
        critical_free_percent=0.0,
    )

    telemetry = collect_disk_usage(tmp_path, scope="control_plane_runtime", thresholds=thresholds)

    assert telemetry.scope == "control_plane_runtime"
    assert telemetry.status is TelemetryStatus.AVAILABLE
    assert telemetry.health is HealthState.OK
    assert telemetry.thresholds.source == "test_override"
    assert telemetry.total_bytes is not None
    assert telemetry.used_bytes is not None
    assert telemetry.free_bytes is not None
    assert telemetry.free_percent is not None
    assert telemetry.used_percent is not None
    assert telemetry.error_count == 0
    assert telemetry.errors == []


def test_collect_disk_usage_marks_missing_path_without_leaking_path(tmp_path: Path) -> None:
    missing = tmp_path / "missing"

    telemetry = collect_disk_usage(missing, scope="control_plane_runtime")

    assert telemetry.status is TelemetryStatus.MISSING
    assert telemetry.health is HealthState.UNKNOWN
    assert telemetry.total_bytes is None
    assert telemetry.used_bytes is None
    assert telemetry.free_bytes is None
    assert telemetry.error_count == 1
    assert telemetry.errors[0].status is TelemetryStatus.MISSING
    assert str(missing) not in telemetry.errors[0].message
    assert telemetry.errors[0].message == "path is missing"


def test_collect_disk_usage_marks_permission_denied_without_leaking_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_disk_usage(path: Path) -> shutil._ntuple_diskusage:
        raise PermissionError(f"permission denied: {path}")

    monkeypatch.setattr("agent_sandbox.capacity_health.shutil.disk_usage", fail_disk_usage)

    telemetry = collect_disk_usage(tmp_path, scope="control_plane_runtime")

    assert telemetry.status is TelemetryStatus.PERMISSION_DENIED
    assert telemetry.health is HealthState.UNKNOWN
    assert telemetry.error_count == 1
    assert telemetry.errors[0].message == "permission denied"
    assert str(tmp_path) not in telemetry.errors[0].message


def test_collect_recursive_directory_size_skips_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "session-root"
    nested = root / "nested"
    nested.mkdir(parents=True)
    payload = nested / "payload.txt"
    payload.write_text("abc123", encoding="utf-8")
    (root / "linked-payload.txt").symlink_to(payload)

    telemetry = collect_recursive_directory_size(root, scope="session_storage")

    assert telemetry.scope == "session_storage"
    assert telemetry.status is TelemetryStatus.AVAILABLE
    assert telemetry.health is HealthState.OK
    assert telemetry.file_count == 1
    assert telemetry.directory_count == 1
    assert telemetry.symlink_count == 1
    assert telemetry.size_bytes == payload.stat().st_size
    assert telemetry.error_count == 0
    assert telemetry.errors == []


def test_collect_recursive_directory_size_sanitizes_child_scan_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "session-root"
    blocked = root / "blocked"
    allowed = root / "allowed"
    blocked.mkdir(parents=True)
    allowed.mkdir(parents=True)
    (allowed / "payload.txt").write_text("payload", encoding="utf-8")

    real_scandir = os.scandir

    def fake_scandir(path: Path | str):
        current = Path(path)
        if current == blocked:
            raise PermissionError(f"permission denied while scanning {current}")
        return real_scandir(path)

    monkeypatch.setattr("agent_sandbox.capacity_health.os.scandir", fake_scandir)

    telemetry = collect_recursive_directory_size(root, scope="session_storage")

    assert telemetry.status is TelemetryStatus.AVAILABLE
    assert telemetry.health is HealthState.WARNING
    assert telemetry.file_count == 1
    assert telemetry.directory_count == 2
    assert telemetry.error_count == 1
    assert telemetry.errors[0].status is TelemetryStatus.PERMISSION_DENIED
    assert telemetry.errors[0].message == "permission denied while scanning directory"
    assert str(blocked) not in telemetry.errors[0].message


def test_top_session_storage_orders_largest_and_oldest() -> None:
    now = datetime.now(UTC)
    entries = [
        SessionStorageSample(
            session_id="session-small",
            directory_path="root/team-a",
            backend="libvirt_vm",
            size_bytes=10,
            created_at=now - timedelta(hours=3),
            updated_at=now - timedelta(hours=1),
        ),
        {
            "session_id": "session-large",
            "directory_path": "root/team-b",
            "backend": "container_worker_vm",
            "size_bytes": 90,
            "created_at": now - timedelta(hours=2),
            "updated_at": now - timedelta(minutes=30),
        },
        SessionStorageSample(
            session_id="session-middle",
            directory_path="root/team-c",
            backend="libvirt_vm",
            size_bytes=50,
            created_at=now - timedelta(hours=1),
            updated_at=now - timedelta(minutes=20),
        ),
    ]

    largest = top_session_storage(entries, limit=2, order="largest")
    oldest = top_session_storage(entries, limit=2, order="oldest")

    assert [entry.session_id for entry in largest] == ["session-large", "session-middle"]
    assert [entry.session_id for entry in oldest] == ["session-small", "session-large"]
    assert largest[0].directory_path == "root/team-b"
    assert largest[0].backend == "container_worker_vm"
    assert largest[0].size_bytes == 90

