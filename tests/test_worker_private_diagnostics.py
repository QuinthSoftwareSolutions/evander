import json
import os
import shutil
import stat

import pytest

from evander.worker.private_diagnostics import PrivateJobDiagnostics


@pytest.fixture
def store(tmp_path):
    instance = PrivateJobDiagnostics(
        tmp_path / "private", max_job_bytes=64 * 1024, max_total_bytes=128 * 1024
    )
    yield instance
    instance.close()


def test_recent_events_survive_rotation_with_private_permissions_and_bounded_storage(store):
    capture = store.begin("scan-1", metadata={"scan_id": "synthetic"})
    for index in range(100):
        capture.event("native.notification", {"index": index, "text": "x" * 1024})
    capture.finish(status="failed", exit_code=1)
    events = b"".join(path.read_bytes() for path in capture.directory.glob("events*.jsonl"))
    assert b'"index": 99' in events and b'"index": 0,' not in events
    summary = json.loads((capture.directory / "summary.json").read_text())
    assert summary["finished"] and summary["outcome"]["exit_code"] == 1
    assert (
        sum(path.stat().st_size for path in capture.directory.rglob("*") if path.is_file())
        <= store.max_job_bytes
    )
    for path in [store.root, *store.root.rglob("*")]:
        assert stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o600)


def test_capture_rejects_symlinks_hardlinks_and_devices_and_excludes_auth(store, tmp_path):
    artifacts = tmp_path / "artifacts"
    history = artifacts / "scan/history"
    history.mkdir(parents=True)
    (history / "rollout.jsonl").write_text("preserved native events")
    outside = tmp_path / "credential"
    outside.write_text("must not copy")
    (history / "symlink.jsonl").symlink_to(outside)
    os.link(outside, history / "hardlink.jsonl")
    os.mkfifo(history / "pipe.log")
    (history / "auth.json").write_text("subscription token")
    (artifacts / "escape").symlink_to(tmp_path, target_is_directory=True)
    capture = store.begin("scan-1")
    capture.collect(artifacts, ["scan/history", "escape/credential", "../credential"])
    capture.finish(status="failed")
    copied = list((capture.directory / "files").rglob("*"))
    assert [p.name for p in copied if p.is_file()] == ["rollout.jsonl"]
    assert (
        "ValueError"
        in json.loads((capture.directory / "summary.json").read_text())["capture_error_types"]
    )
    assert outside.read_text() == "must not copy"


def test_age_retention_survives_restart_and_protects_inflight_jobs(tmp_path):
    active = {"scan-active"}
    instant = [100.0]
    settings = dict(
        active_jobs=lambda: active,
        clock=lambda: instant[0],
        retention_days=1,
        max_job_bytes=64 * 1024,
        max_total_bytes=128 * 1024,
    )
    first = PrivateJobDiagnostics(tmp_path / "private", **settings)
    for job_id in ("scan-finished", "scan-active"):
        capture = first.begin(job_id)
        capture.event("native.stderr", {"text": "evidence"})
        capture.finish(status="failed")
        os.utime(capture.directory, (100, 100))
    first.close()
    instant[0] += 2 * 86400
    second = PrivateJobDiagnostics(first.root, **settings)
    try:
        assert not (second.root / "scan-finished").exists()
        assert (second.root / "scan-active/events.jsonl").exists()
        active.clear()
        second.prune()
        assert not (second.root / "scan-active").exists()
    finally:
        second.close()


def test_total_budget_prunes_oldest_finished_job_and_keeps_active_evidence(store):
    first = store.begin("scan-old")
    first.event("native.stderr", {"text": "old"})
    first.finish(status="completed")
    active = store.begin("scan-active")
    active.event("native.stderr", {"text": "active"})
    # Simulate evidence retained by an older worker with a larger byte budget.
    (first.directory / "legacy.log").write_bytes(b"x" * store.max_total_bytes)
    store.prune()
    assert not first.directory.exists()
    assert (active.directory / "events.jsonl").exists()


def test_unavailable_storage_does_not_break_event_observer(store):
    capture = store.begin("scan-1")
    shutil.rmtree(store.root)
    store.root.write_text("unavailable")
    capture.event("native.stderr", {"text": "diagnostic"})
    assert capture.errors


def test_large_terminal_details_cannot_displace_the_outcome_summary(store):
    capture = store.begin("scan-1")
    capture.finish(status="failed", exit_code=137, error="x" * (256 * 1024))
    summary = json.loads((capture.directory / "summary.json").read_text())
    assert summary["finished"] and summary["summary_truncated"]
    assert summary["outcome"]["exit_code"] == 137
    assert (capture.directory / "summary.json").stat().st_size <= 8192


def test_corrupt_summary_does_not_prevent_restart_recovery(tmp_path):
    root = tmp_path / "private"
    original = PrivateJobDiagnostics(root)
    capture = original.begin("scan-1")
    capture.event("native.notification", {"before_restart": True})
    original.close()
    (capture.directory / "summary.json").write_text('{"unfinished')
    restarted = PrivateJobDiagnostics(root)
    try:
        recovered = restarted.begin("scan-1")
        recovered.finish(status="failed")
        assert "before_restart" in (capture.directory / "events.jsonl").read_text()
        summary = json.loads((capture.directory / "summary.json").read_text())
        assert summary["finished"] and "JSONDecodeError" in summary["capture_error_types"]
    finally:
        restarted.close()
