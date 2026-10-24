"""Continuation controls must conserve evidence and never invent file credit."""

from copy import deepcopy

import pytest

from evander_codex_security_addon import coverage as c
from test_coverage import finish
from test_coverage_batching import legacy_file_scopes, resume


def checkpoint(value):
    value.update(finished_at=c.now().isoformat(), cleanup_confirmed=True)
    return value


def test_rebuild_combines_split_and_blocked_work_without_repeating_excluded_paths():
    files = [f"src/{i:02}.py" for i in range(48)]
    excluded = [f"vendor/opaque-{i}.bin" for i in range(4)]
    source = legacy_file_scopes([[files[0]], [files[1]], files[2:4], *[[p] for p in files[4:] + excluded]])
    by_paths = {tuple(t["paths"]): t for t in source["tasks"].values() if t["kind"] == "files"}
    finish(source, by_paths[(files[0],)])
    finish(source, by_paths[(files[1],)])
    finish(source, by_paths[tuple(files[2:4])], closed=False)
    for _ in range(3):
        finish(source, by_paths[(excluded[0],)], closed=False)
    source["proofs"][-1]["findings"] = [{"findingId": "retained", "title": "Input reaches command", "severity": {"level": "high"}}]
    checkpoint(source)
    original = deepcopy(source)

    continued = resume(source, exclude_paths=excluded, rebuild_pending_batches=True, incomplete_review_policy="retry_scope")

    pending = c.pending(continued)
    assert len(pending) == 1
    assert pending[0]["paths"] == files[2:]
    assert c.progress(continued)["files_reviewed"] == 2
    assert c.progress(continued)["files_pending"] == 46
    assert c.progress(continued)["files_excluded"] == 4
    assert c.progress(continued)["files_total"] == 52
    assert continued["proofs"] == source["proofs"]
    assert c.aggregate(continued)[1]["high"] == 1
    assert continued["inventory_digest"] == source["inventory_digest"]
    assert continued["route"] == source["route"]
    assert continued["revision"] == source["revision"]
    assert continued["lineage"][-1] == source["scan_id"]
    assert source == original


def test_retry_scope_keeps_one_batch_and_still_stops_after_three_incomplete_attempts():
    source = checkpoint(legacy_file_scopes([["a.py"], ["b.py"], ["c.py"]]))
    continued = resume(source, rebuild_pending_batches=True, incomplete_review_policy="retry_scope")
    item, = c.pending(continued)
    for _ in range(3):
        finish(continued, item, closed=False)

    assert item["status"] == "blocked"
    assert item["paths"] == ["a.py", "b.py", "c.py"]
    assert len(item["attempts"]) == 3
    assert not [t for t in continued["tasks"].values() if t.get("parent") == item["id"]]
    assert c.progress(continued)["files_reviewed"] == 0
    assert c.progress(continued)["files_blocked"] == 3
    assert c.pending(continued) == []


def test_exclusions_do_not_discard_findings_or_other_files_required_followups():
    source = checkpoint(legacy_file_scopes([["opaque.bin"], ["other.py"]]))
    source["deferred"] = {
        "binary:own": {"paths": ["opaque.bin"], "resolution": None},
        "binary:mixed": {"paths": ["opaque.bin", "other.py"], "resolution": None},
        "binary:pathless": {"paths": [], "resolution": None},
        "other:own": {"paths": ["other.py"], "resolution": None},
    }
    original = deepcopy(source)
    continued = resume(source, exclude_paths=["opaque.bin"], rebuild_pending_batches=True)
    c.apply_exclusions(continued, {"binary": ["opaque.bin"]})

    for key in ("binary:own", "binary:pathless"):
        resolution = continued["deferred"][key]["resolution"]
        assert resolution["disposition"] == "excluded"
        assert resolution["explicit_exclusion"] is True
    assert continued["deferred"]["binary:mixed"]["resolution"] is None
    assert continued["deferred"]["other:own"]["resolution"] is None
    c.plan_boundaries(continued)
    assert all("opaque.bin" not in t["paths"] for t in c.pending(continued))
    assert source == original


def test_excluded_files_remain_partial_after_all_requested_work_is_done():
    source = checkpoint(legacy_file_scopes([["opaque.bin"], ["app.py"]]))
    continued = resume(source, exclude_paths=["opaque.bin"], rebuild_pending_batches=True)
    item, = c.pending(continued)
    finish(continued, item)
    c.plan_boundaries(continued)
    for item in c.pending(continued):
        assert "opaque.bin" not in item["paths"]
        finish(continued, item)

    assert c.required_work_complete(continued)
    assert not c.complete(continued)
    assert c.progress(continued)["files_reviewed"] == 1
    assert c.progress(continued)["files_excluded"] == 1
    checkpoint(continued)
    assert c.resumable(continued)


def test_exclusion_never_removes_previously_accepted_file_credit():
    source = legacy_file_scopes([["app.py"], ["pending.py"]])
    item = next(t for t in source["tasks"].values() if t["kind"] == "files" and t["paths"] == ["app.py"])
    finish(source, item)
    checkpoint(source)
    continued = resume(source, exclude_paths=["app.py"], rebuild_pending_batches=True)
    assert continued["files"]["app.py"] == source["files"]["app.py"]
    assert c.progress(continued)["files_reviewed"] == 1
    assert c.progress(continued)["files_excluded"] == 0
    assert [p for t in c.pending(continued) for p in t["paths"]] == ["pending.py"]


def test_resume_inherits_exclusions_and_retry_policy_without_reintroducing_work():
    source = checkpoint(legacy_file_scopes([["opaque.bin"], ["app.py"]]))
    first = resume(source, exclude_paths=["opaque.bin"], rebuild_pending_batches=True, incomplete_review_policy="retry_scope")
    checkpoint(first)
    second = resume(first, scan_id="c" * 32)
    assert second["exclude_paths"] == ["opaque.bin"]
    assert second["incomplete_review_policy"] == "retry_scope"
    assert c.progress(second)["files_excluded"] == 1
    assert all("opaque.bin" not in t["paths"] for t in c.pending(second))
    with pytest.raises(ValueError, match="reintroduce"):
        resume(first, scan_id="d" * 32, exclude_paths=[])


def test_exclusions_are_exact_inventory_entries_and_require_retained_mapping():
    source = checkpoint(legacy_file_scopes([["app.py"]]))
    with pytest.raises(ValueError, match="absent"):
        resume(source, exclude_paths=["missing.py"])
    source["architecture"] = None
    with pytest.raises(ValueError, match="architecture"):
        resume(source, exclude_paths=["app.py"])


def test_rebuild_honors_existing_byte_and_file_caps():
    source = legacy_file_scopes([[f"p/{i:03}.py"] for i in range(101)])
    checkpoint(source)
    continued = resume(source, rebuild_pending_batches=True)
    scopes = [t for t in continued["tasks"].values() if t["kind"] == "files" and t["status"] == "pending"]
    assert [len(t["paths"]) for t in scopes] == [100, 1]
    assert len({p for t in scopes for p in t["paths"]}) == 101

