"""File blockers must not consume retries or grant partial-report coverage."""

from copy import deepcopy

import pytest

from evander_codex_security_addon import coverage as c
from evander_codex_security_addon.coverage_file_controls import (
    matches_pattern,
    native_file_blockers,
    refresh_blocked_work,
)
from test_coverage import entry, finish, mapping, request
from test_coverage_batching import resume


def tracked(entries, **inputs):
    return c.make_state(
        "a" * 32,
        request(**inputs),
        "root/project",
        {"revision": "d" * 40, "entries": entries},
        {"model": "example"},
    )


def checkpoint(value):
    value.update(finished_at=c.now().isoformat(), cleanup_confirmed=True)
    return value


def isolate(s, paths):
    (item,) = c.pending(s)
    questions = [
        {"id": path, "paths": [path], "reason": "Content requires an unavailable decoder"}
        for path in paths
    ]
    finish(
        s,
        item,
        closed=False,
        deferred=questions,
        file_blockers={path: "Decoder unavailable" for path in paths},
    )
    return item


def test_partial_batch_parks_exact_blocker_and_remainder_needs_its_own_complete_proof():
    s = tracked(
        [entry("a.py"), entry("b.py"), entry("locked.pfx", kind="binary")],
        incomplete_review_policy="isolate_blockers",
    )
    mapping(s)
    original = isolate(s, ["locked.pfx"])
    original_proof = deepcopy(s["proofs"][-1])
    original_proof["findings"].append(
        {"findingId": "retained", "title": "Unchecked input", "severity": {"level": "high"}}
    )
    s["proofs"][-1] = original_proof

    (remainder,) = c.pending(s)
    assert remainder["paths"] == ["a.py", "b.py"]
    assert c.progress(s)["files_reviewed"] == 0
    assert c.progress(s)["files_isolated"] == 1
    assert c.progress(s)["isolated_files"] == [
        {
            "path": "locked.pfx",
            "reason": "Decoder unavailable",
            "receipt": original_proof["archive_key"],
            "provider_scan_id": original_proof["provider_scan_id"],
        }
    ]
    assert original["status"] == "split"
    assert len(original["attempts"]) == 1
    isolated = next(t for t in s["tasks"].values() if t["paths"] == ["locked.pfx"])
    assert isolated["status"] == "blocked"
    assert isolated["attempts"] == []

    finish(s, remainder)
    c.plan_boundaries(s)
    for boundary in c.pending(s):
        assert boundary["paths"] == ["a.py", "b.py"]
        assert boundary["deferred_keys"] == []
        finish(s, boundary)
    assert c.progress(s)["files_reviewed"] == 2
    assert c.progress(s)["files_blocked"] == 1
    assert c.progress(s)["files_not_applicable"] == 0
    assert c.aggregate(s)[1]["high"] == 1
    assert original_proof in s["proofs"]
    assert s["files"]["a.py"]["receipt"] != original_proof["archive_key"]
    assert not c.complete(s)
    assert not c.required_work_complete(s)
    assert all(q["resolution"] is None for q in s["deferred"].values())
    assert c.pending(s) == []


def test_resume_and_rebuild_keep_blockers_parked_and_completed_boundaries_accepted():
    s = tracked(
        [entry("app.py"), entry("locked.pfx", kind="binary")],
        incomplete_review_policy="isolate_blockers",
        max_parallel_reviews=3,
    )
    mapping(s)
    isolate(s, ["locked.pfx"])
    finish(s, c.pending(s)[0])
    c.plan_boundaries(s)
    (boundary,) = c.pending(s)
    finish(s, boundary)
    checkpoint(s)
    original = deepcopy(s)

    continued = resume(s, rebuild_pending_batches=True)
    assert c.pending(continued) == []
    assert continued["tasks"][boundary["id"]]["status"] == "completed"
    assert continued["file_blockers"] == s["file_blockers"]
    assert continued["proofs"] == s["proofs"]
    assert continued["files"]["app.py"] == s["files"]["app.py"]
    assert c.progress(continued)["files_reviewed"] == 1
    assert c.progress(continued)["files_isolated"] == 1
    assert c.review_parallelism(continued) == 3
    assert continued["inventory_digest"] == s["inventory_digest"]
    assert continued["route"] == s["route"]
    assert s == original


def test_explicit_retry_reopens_only_selected_blocker_and_its_boundary_obligations():
    s = tracked(
        [entry("app.py"), entry("first.pfx", kind="binary"), entry("second.pfx", kind="binary")],
        incomplete_review_policy="isolate_blockers",
    )
    mapping(s)
    isolate(s, ["first.pfx", "second.pfx"])
    finish(s, c.pending(s)[0])
    c.plan_boundaries(s)
    (boundary,) = c.pending(s)
    finish(s, boundary)
    checkpoint(s)

    continued = resume(s, retry_blocked_paths=["first.pfx"])
    (item,) = c.pending(continued)
    assert item["paths"] == ["first.pfx"]
    assert set(continued["file_blockers"]) == {"second.pfx"}
    assert continued["tasks"][boundary["id"]]["status"] == "completed"
    finish(
        continued,
        item,
        dispositions={
            "first.pfx": {
                "disposition": "reviewed",
                "reason": "Unlocked and inspected the entire content",
            }
        },
    )
    (retried_boundary,) = c.pending(continued)
    assert retried_boundary["paths"] == ["first.pfx"]
    assert len(retried_boundary["deferred_keys"]) == 1
    assert continued["deferred"][retried_boundary["deferred_keys"][0]]["paths"] == ["first.pfx"]
    with pytest.raises(ValueError, match="previously isolated"):
        resume(s, retry_blocked_paths=["app.py"])
    with pytest.raises(ValueError, match="excluded"):
        resume(s, retry_blocked_paths=["first.pfx"], exclude_patterns=["*.pfx"])


def test_retrying_every_isolated_file_does_not_schedule_the_original_batch_as_well():
    source = tracked(
        [entry("first.pfx", kind="binary"), entry("second.pfx", kind="binary")],
        incomplete_review_policy="isolate_blockers",
    )
    mapping(source)
    original = isolate(source, ["first.pfx", "second.pfx"])
    assert original["status"] == "split"
    checkpoint(source)
    parked = resume(source)
    checkpoint(parked)
    retried = resume(parked, scan_id="c" * 32, retry_blocked_paths=["first.pfx", "second.pfx"])
    scheduled = [path for scope in c.pending(retried) for path in scope["paths"]]
    assert sorted(scheduled) == ["first.pfx", "second.pfx"]
    assert len(scheduled) == len(set(scheduled))


@pytest.mark.parametrize(
    "references",
    [
        [],
        ["."],
        ["folder"],
        ["*.pfx"],
        ["missing.pfx"],
        ["../locked.pfx"],
        ["locked.pfx", "unknown"],
    ],
)
def test_blocker_attribution_requires_exact_in_scope_paths(references):
    coverage = {
        "deferred": [
            {"id": "decode", "paths": references, "reason": "locked.pfx cannot be decoded"}
        ]
    }
    assert native_file_blockers(coverage, ["locked.pfx", "app.py"]) == {}


def test_native_file_followup_surface_can_attribute_a_blocker_without_guessing_from_prose():
    result = native_file_blockers(
        {
            "surfaces": [
                {
                    "label": "file:./locked.pfx",
                    "disposition": "needs_follow_up",
                    "notes": "Password required",
                },
                {
                    "label": "file:outside.pfx",
                    "disposition": "needs_follow_up",
                    "notes": "Password required",
                },
                {"label": "file:app.py", "disposition": "no_issue_found", "notes": "Reviewed"},
            ]
        },
        ["locked.pfx", "app.py"],
    )
    assert result == {"locked.pfx": "Password required"}


def test_unattributed_partial_report_blocks_once_without_inventing_file_level_diagnoses():
    s = tracked([entry("a.py"), entry("b.py")], incomplete_review_policy="isolate_blockers")
    mapping(s)
    (item,) = c.pending(s)
    finish(s, item, closed=False)
    assert item["status"] == "blocked"
    assert "did not identify exact blocked files" in item["error"]
    assert len(item["attempts"]) == 1
    assert s["file_blockers"] == {}
    assert c.progress(s)["files_reviewed"] == 0
    assert c.pending(s) == []


@pytest.mark.parametrize("closed", [False, True])
def test_missing_nontext_disposition_is_attributed_without_crediting_adjacent_text(closed):
    s = tracked(
        [entry("app.py"), entry("locked.pfx", kind="binary")],
        incomplete_review_policy="isolate_blockers",
    )
    mapping(s)
    finish(s, c.pending(s)[0], closed=closed)
    assert set(s["file_blockers"]) == {"locked.pfx"}
    assert c.progress(s)["files_reviewed"] == 0
    assert c.pending(s)[0]["paths"] == ["app.py"]


def test_mixed_blocked_question_stays_unresolved_without_stealing_a_running_boundarys_work():
    s = tracked(
        [entry("a.py"), entry("b.py"), entry("locked.pfx", kind="binary")],
        incomplete_review_policy="isolate_blockers",
    )
    mapping(s)
    isolate(s, ["locked.pfx"])
    finish(s, c.pending(s)[0])
    s["deferred"].update(
        {
            "prior:mixed": {"paths": ["."], "resolution": None},
            "prior:a": {"paths": ["a.py"], "resolution": None},
            "prior:b": {"paths": ["b.py"], "resolution": None},
        }
    )
    boundaries = [c.task("boundary", [path]) for path in ["a.py", "b.py"]]
    s["tasks"].update({t["id"]: t for t in boundaries})
    s["boundary_planned"] = True
    refresh_blocked_work(s)
    a, b = boundaries
    assert a["deferred_keys"] == ["prior:a", "prior:mixed"]
    assert b["deferred_keys"] == ["prior:b", "prior:mixed"]
    assert a["deferred_paths"]["prior:mixed"] == ["a.py"]
    assert b["deferred_paths"]["prior:mixed"] == ["b.py"]
    c.begin(s, a)
    refresh_blocked_work(s)
    assert a["deferred_keys"] == ["prior:a", "prior:mixed"]
    assert b["deferred_keys"] == ["prior:b", "prior:mixed"]
    assert s["blocked_followups"]["prior:mixed"] == {"blocked_by": ["locked.pfx"]}
    assert s["deferred"]["prior:mixed"]["resolution"] is None


def test_boundary_reintroducing_known_blocker_is_parked_without_retry_or_false_completion():
    s = tracked(
        [entry("app.py"), entry("locked.pfx", kind="binary")],
        incomplete_review_policy="isolate_blockers",
    )
    mapping(s)
    isolate(s, ["locked.pfx"])
    finish(s, c.pending(s)[0])
    c.plan_boundaries(s)
    (boundary,) = c.pending(s)
    finish(
        s,
        boundary,
        closed=False,
        deferred=[{"id": "again", "paths": ["locked.pfx"], "reason": "Password still required"}],
    )
    refresh_blocked_work(s)
    assert boundary["status"] == "blocked"
    assert boundary["blocked_by_files"] == ["locked.pfx"]
    assert len(boundary["attempts"]) == 1
    checkpoint(s)
    continued = resume(s)
    assert c.pending(continued) == []
    assert not c.complete(continued)


def test_partitioning_an_existing_boundary_transfers_other_files_required_questions():
    s = tracked(
        [entry("app.py"), entry("locked.pfx", kind="binary")],
        incomplete_review_policy="isolate_blockers",
    )
    mapping(s)
    boundary = c.task("boundary", ["app.py", "locked.pfx"])
    boundary["deferred_keys"] = ["prior:app", "prior:locked"]
    s["tasks"][boundary["id"]] = boundary
    s["boundary_planned"] = True
    s["deferred"] = {
        "prior:app": {"paths": ["app.py"], "resolution": None},
        "prior:locked": {"paths": ["locked.pfx"], "resolution": None},
    }
    s["file_blockers"] = {
        "locked.pfx": {
            "reason": "Password required",
            "receipt": "native-archive",
            "provider_scan_id": "prior",
        }
    }
    refresh_blocked_work(s)
    finish(s, c.pending(s)[0])
    (remainder,) = c.pending(s)
    assert remainder["kind"] == "boundary"
    assert remainder["paths"] == ["app.py"]
    assert remainder["deferred_keys"] == ["prior:app"]
    finish(
        s,
        remainder,
        resolutions={
            "prior:app": {"disposition": "no_issue_found", "reason": "Verified the source boundary"}
        },
    )
    assert s["deferred"]["prior:app"]["resolution"]
    assert s["deferred"]["prior:locked"]["resolution"] is None
    assert c.pending(s) == []


def test_explicit_pattern_exclusion_can_retire_isolated_work_without_erasing_accepted_evidence():
    source = tracked(
        [entry("app.py"), entry("locked.pfx", kind="binary")],
        incomplete_review_policy="isolate_blockers",
    )
    mapping(source)
    isolate(source, ["locked.pfx"])
    finish(source, c.pending(source)[0])
    c.plan_boundaries(source)
    finish(source, c.pending(source)[0])
    checkpoint(source)
    original = deepcopy(source)
    continued = resume(source, exclude_patterns=["*.pfx"])
    assert c.progress(continued)["files_isolated"] == 0
    assert c.progress(continued)["files_excluded"] == 1
    assert c.progress(continued)["files_reviewed"] == 1
    assert c.required_work_complete(continued)
    assert not c.complete(continued)
    assert c.pending(continued) == []
    assert continued["proofs"] == source["proofs"]
    assert source == original


@pytest.mark.parametrize(
    ("pattern", "matched"),
    [
        ("*.pfx", {"root.pfx", "certs/UPPER.PFX"}),
        ("bin/*", {"bin/app", "bin/nested/debug.pdb"}),
        ("**/bin/**", {"bin/app", "bin/nested/debug.pdb", "src/bin/app"}),
        ("*.tar.gz", {"archives/backup.tar.gz"}),
        ("bin/", {"bin/app", "bin/nested/debug.pdb", "src/bin/app"}),
        ("never/*.missing", set()),
    ],
)
def test_patterns_resolve_against_frozen_tracked_inventory_case_insensitively(pattern, matched):
    paths = [
        "root.pfx",
        "certs/UPPER.PFX",
        "bin/app",
        "bin/nested/debug.pdb",
        "src/bin/app",
        "archives/backup.tar.gz",
        "src/app.py",
        "bin",
    ]
    s = tracked([entry(path) for path in paths if path != "bin"], exclude_patterns=[pattern])
    assert set(s["exclude_paths"]) == matched
    assert set(s["exclusion_matches"][pattern]) == matched
    assert c.progress(s)["files_excluded"] == len(matched)
    assert c.progress(s)["files_total"] == 7
    assert c.progress(s)["files_reviewed"] == 0
    assert all(not set(t["paths"]) & matched for t in s["tasks"].values())
    assert not matches_pattern("bin", "bin/")
    assert not matches_pattern("bin", "**/bin/**")
    assert not matches_pattern("src/bin", "**/bin/**")


def test_all_excluded_scan_has_no_worker_work_no_review_credit_and_versioned_controls():
    s = tracked([entry("certs/secret.PFX")], exclude_patterns=["*.pfx"])
    assert s["version"] == 3
    assert s["tasks"] == {}
    assert c.pending(s) == []
    assert c.progress(s)["files_reviewed"] == 0
    assert c.progress(s)["files_excluded"] == 1
    assert c.required_work_complete(s)
    assert not c.complete(s)


def test_new_patterns_after_boundary_planning_preserve_accepted_credit_and_completed_scope():
    s = tracked([entry("app.py"), entry("secret.pfx", kind="binary")])
    mapping(s)
    finish(
        s,
        c.pending(s)[0],
        dispositions={
            "secret.pfx": {
                "disposition": "not_applicable",
                "reason": "Inspected nonexecutable certificate metadata",
            }
        },
    )
    c.plan_boundaries(s)
    (boundary,) = c.pending(s)
    finish(s, boundary)
    # A separate unresolved requirement keeps this accepted snapshot resumable.
    s["deferred"]["prior:deploy"] = {"paths": ["app.py"], "resolution": None}
    checkpoint(s)
    original = deepcopy(s)
    continued = resume(s, exclude_patterns=["*.pfx"])
    assert continued["tasks"][boundary["id"]]["status"] == "completed"
    assert continued["files"] == s["files"]
    assert continued["proofs"] == s["proofs"]
    assert continued["exclude_paths"] == ["secret.pfx"]
    assert not c.complete(continued)
    assert s == original


def test_unmatched_patterns_and_omitted_controls_inherit_without_reintroducing_files():
    s = checkpoint(
        tracked([entry("app.py"), entry("secret.pfx")], exclude_patterns=["*.pfx", "*.never"])
    )
    continued = resume(s)
    assert continued["version"] == 3
    assert continued["exclude_paths"] == ["secret.pfx"]
    assert continued["exclusion_matches"] == {"*.never": [], "*.pfx": ["secret.pfx"]}
    with pytest.raises(ValueError, match="reintroduce"):
        resume(s, exclude_patterns=[])


def test_unmapped_continuation_can_exclude_paths_without_scheduling_them_for_mapping():
    source = checkpoint(tracked([entry("app.py"), entry("secret.pfx")]))
    source["version"] = 1
    original = deepcopy(source)
    continued = resume(source, exclude_patterns=["*.pfx"])
    (item,) = c.pending(continued)
    assert item["kind"] == "architecture"
    assert item["paths"] == ["app.py"]
    assert continued["version"] == 3
    all_excluded = resume(source, exclude_patterns=["*"])
    assert c.pending(all_excluded) == []
    assert c.required_work_complete(all_excluded)
    assert not c.complete(all_excluded)
    assert c.progress(all_excluded)["files_reviewed"] == 0
    assert source == original
