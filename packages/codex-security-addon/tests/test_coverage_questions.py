"""Checkpoint identity, cross-component closure and exclusions are host-owned."""

from copy import deepcopy

import pytest

from evander_codex_security_addon import coverage as c
from evander_codex_security_addon.coverage_questions import (
    blocking_exclusions,
    ensure_question_scopes,
    normalize_questions,
    scoped_questions,
)
from test_coverage import entry, finish, mapping, proof, receipt, request, state
from test_coverage_batching import resume


def boundaries(paths=("a/a.py", "b/b.py"), *, questions=None):
    value = state(paths, file_size=600_000)
    mapping(value)
    for item in c.pending(value):
        finish(value, item)
    value["deferred"] = questions or {}
    c.plan_boundaries(value)
    return value


def resolution():
    return {
        "disposition": "no_issue_found",
        "reason": "Verified this component's part of the required source review",
    }


def test_repository_checkpoint_is_partitioned_and_only_closes_after_every_part():
    s = boundaries(
        questions={
            "native:all": {
                "paths": ["."],
                "reason": "Review the remaining repository files",
                "resolution": None,
            }
        }
    )
    a, b = c.pending(s)
    assert scoped_questions(s, a)["native:all"]["paths"] == ["a/a.py"]
    assert scoped_questions(s, b)["native:all"]["paths"] == ["b/b.py"]
    assert "separately assigns all other paths" in scoped_questions(s, a)["native:all"]["reason"]
    slow = c.begin(s, b)
    finish(s, a, resolutions={"native:all": resolution()})
    assert a["status"] == "completed"
    assert s["deferred"]["native:all"]["resolution"] is None
    assert not c.complete(s)
    c.accept(
        s,
        b,
        slow["scan_id"],
        receipt(s, b, resolutions={"native:all": resolution()}),
        proof(slow["scan_id"]),
    )
    slow["cleanup_confirmed"] = True
    assert b["status"] == "completed"
    assert c.complete(s)
    closed = s["deferred"]["native:all"]["scope_resolutions"]
    assert [value["paths"] for value in closed] == [["a/a.py"], ["b/b.py"]]
    assert len(s["deferred"]["native:all"]["resolution"]["scope_receipts"]) == 2


def test_returned_saved_checkpoint_updates_one_question_instead_of_doubling_it():
    s = boundaries(
        ("app.py",),
        questions={
            "native:pending": {
                "id": "pending",
                "paths": ["app.py"],
                "reason": "Verify input authorization",
                "resolution": None,
            }
        },
    )
    s["incomplete_review_policy"] = "retry_scope"
    (item,) = c.pending(s)
    for number in range(3):
        finish(
            s,
            item,
            closed=False,
            deferred=[
                {
                    "id": "native:pending",
                    "paths": ["app.py"],
                    "reason": f"The same authorization question remains open ({number})",
                }
            ],
        )
        assert len(s["deferred"]) == 1
        assert item["deferred_keys"] == ["native:pending"]
        assert len(scoped_questions(s, item)) == 1
    assert item["status"] == "blocked"
    assert len(item["attempts"]) == 3
    assert len(s["deferred_aliases"]) == 3
    assert c.progress(s)["required_questions_pending"] == 1


def test_local_checkpoint_ids_are_stable_for_retries_but_isolated_between_tasks():
    s = boundaries(questions={})
    s["incomplete_review_policy"] = "retry_scope"
    a, b = c.pending(s)
    for _ in range(2):
        finish(
            s,
            a,
            closed=False,
            deferred=[
                {"id": "candidate", "paths": ["a/a.py"], "reason": "Verify input validation"}
            ],
        )
    finish(
        s,
        b,
        closed=False,
        deferred=[{"id": "candidate", "paths": ["b/b.py"], "reason": "Verify input validation"}],
    )
    assert len(s["deferred"]) == 2
    assert a["deferred_keys"] != b["deferred_keys"]
    assert c.progress(s)["required_questions_pending"] == 2


def test_continuation_folds_legacy_id_chains_without_losing_unresolved_paths_or_native_proofs():
    source = boundaries(
        questions={
            "native:all": {
                "id": "all",
                "paths": ["a/a.py", "b/b.py"],
                "reason": "Review the remaining files",
                "resolution": None,
            },
            "again:native:all": {
                "id": "native:all",
                "paths": [],
                "reason": "Other components still need review",
                "resolution": None,
            },
            "third:again:native:all": {
                "id": "again:native:all",
                "reason": "Other components remain outside this scan",
                "resolution": None,
            },
        }
    )
    source.update(finished_at=c.now().isoformat(), cleanup_confirmed=True)
    original = deepcopy(source)
    continued = resume(source)
    assert list(continued["deferred"]) == ["native:all"]
    assert set(continued["deferred"]["native:all"]["paths"]) == {"a/a.py", "b/b.py"}
    assert continued["deferred_aliases"]["third:again:native:all"] == "native:all"
    for item in c.pending(continued):
        assert scoped_questions(continued, item)["native:all"]["paths"] == item["paths"]
        finish(continued, item, resolutions={"native:all": resolution()})
    assert c.complete(continued)
    assert continued["proofs"][: len(source["proofs"])] == source["proofs"]
    assert source == original


def test_legacy_partial_scope_resolution_cannot_close_a_repository_wide_requirement():
    s = boundaries(
        questions={
            "native:all": {
                "paths": ["."],
                "reason": "Review all assigned source",
                "resolution": {**resolution(), "receipt": "archive-a"},
            },
        }
    )
    normalize_questions(s, {"archive-a": ["a/a.py"]})
    assert s["deferred"]["native:all"]["resolution"] is None
    assert s["deferred"]["native:all"]["scope_resolutions"][0]["paths"] == ["a/a.py"]
    ensure_question_scopes(s)
    owned = [
        (item["paths"], item.get("deferred_paths", {}).get("native:all")) for item in c.pending(s)
    ]
    assert (["b/b.py"], ["b/b.py"]) in owned
    assert all(paths != ["a/a.py"] for _, paths in owned)


def test_new_required_work_gets_a_bounded_owner_without_reopening_completed_scopes():
    s = boundaries()
    first, second = c.pending(s)
    finish(s, first)
    original = deepcopy(first)
    s["deferred"]["new:required"] = {
        "paths": ["a/a.py"],
        "reason": "A newly discovered source-boundary obligation",
        "resolution": None,
    }
    ensure_question_scopes(s)
    assert first == original
    followup = next(item for item in c.pending(s) if item["id"] != second["id"])
    assert followup["paths"] == ["a/a.py"]
    assert followup["deferred_keys"] == ["new:required"]
    finish(s, followup, resolutions={"new:required": resolution()})
    finish(s, second)
    assert c.complete(s)


def test_excluded_files_are_absent_from_both_queues_and_mixed_checkpoint_inputs():
    s = c.make_state(
        "a" * 32,
        request(exclude_patterns=["*.pfx"]),
        "root/project",
        {
            "revision": "d" * 40,
            "entries": [entry("app.py"), entry("certs/secret.PFX", kind="binary")],
        },
        {},
    )
    mapping(s)
    (files,) = c.pending(s)
    assert files["paths"] == ["app.py"]
    finish(s, files)
    s["deferred"]["native:all"] = {
        "paths": ["."],
        "reason": "Review all content",
        "resolution": None,
    }
    c.plan_boundaries(s)
    (boundary,) = c.pending(s)
    assert boundary["paths"] == ["app.py"]
    assert scoped_questions(s, boundary)["native:all"]["paths"] == ["app.py"]
    finish(s, boundary, resolutions={"native:all": resolution()})
    assert c.required_work_complete(s)
    assert not c.complete(s)
    assert s["files"]["certs/secret.PFX"]["disposition"] == "excluded"
    assert s["deferred"]["native:all"]["resolution"]["excluded_paths"] == ["certs/secret.PFX"]


@pytest.mark.parametrize(
    "references", [["outside.py"], ["app.py", "../outside.py"], ["unknown/*.py"]]
)
def test_unknown_cross_scope_references_stay_required_without_being_given_to_an_unrelated_component(
    references,
):
    s = boundaries(
        ("app.py",),
        questions={
            "native:unknown": {
                "paths": references,
                "reason": "Unsupported reference requires scope clarification",
                "resolution": None,
            }
        },
    )
    (item,) = c.pending(s)
    assert scoped_questions(s, item) == {}
    finish(s, item)
    assert not c.required_work_complete(s)
    assert s["deferred"]["native:unknown"]["resolution"] is None
    assert c.pending(s) == []


def test_invalid_alias_cycles_cannot_satisfy_required_coverage():
    s = boundaries(
        questions={
            "one": {"id": "two", "resolution": None},
            "two": {"id": "one", "resolution": None},
        }
    )
    with pytest.raises(ValueError, match="cycle"):
        normalize_questions(s)


def test_only_approved_exclusions_entirely_outside_assignment_are_nonblocking():
    s = state(("app.py", "excluded.pfx", "other.pfx"))
    s["exclude_paths"] = ["excluded.pfx"]
    approved = {"pattern": "excluded.pfx", "reason": "Excluded by the host request"}
    assert blocking_exclusions(s, ["app.py"], [approved]) == []
    assert blocking_exclusions(s, ["excluded.pfx"], [approved]) == [approved]
    broad = {"pattern": "*.pfx", "reason": "A provider cannot exclude another unapproved file"}
    assert blocking_exclusions(s, ["app.py"], [broad]) == [broad]
    unknown = {"pattern": "missing.pfx", "reason": "Unknown exclusions are not guessed away"}
    assert blocking_exclusions(s, ["app.py"], [unknown]) == [unknown]


def test_other_components_blocked_file_does_not_quarantine_a_valid_local_checkpoint_retry():
    s = state(("a/a.py", "b/b.py"), file_size=600_000)
    mapping(s)
    a, b = c.pending(s)
    finish(s, a)
    for _ in range(3):
        finish(s, b, closed=False)
    s["deferred"] = {
        "native:wide": {
            "paths": ["."],
            "reason": "Review each source component",
            "resolution": None,
        }
    }
    c.plan_boundaries(s)
    s["incomplete_review_policy"] = "isolate_blockers"
    s["file_blockers"] = {
        "b/b.py": {
            "reason": "Unavailable content",
            "receipt": s["proofs"][-1]["archive_key"],
            "provider_scan_id": "native",
        }
    }
    from evander_codex_security_addon.coverage_file_controls import refresh_blocked_work

    refresh_blocked_work(s)
    (item,) = c.pending(s)
    finish(
        s,
        item,
        closed=False,
        deferred=[
            {
                "id": "native:wide",
                "paths": ["a/a.py"],
                "reason": "An assigned source control still needs checking",
            }
        ],
    )
    assert item["status"] == "pending"
    assert not item.get("quarantined")
    assert item["deferred_paths"]["native:wide"] == ["a/a.py"]


@pytest.mark.parametrize("original_paths", [["a/a.py"], []])
def test_expanded_checkpoint_reopens_only_the_new_required_scope(original_paths):
    s = boundaries(
        questions={
            "native:check": {
                "paths": original_paths,
                "reason": "Required source review",
                "resolution": None,
            }
        }
    )
    a, b = c.pending(s)
    finish(s, a, resolutions={"native:check": resolution()})
    assert s["deferred"]["native:check"]["resolution"]
    earlier = deepcopy(s["deferred"]["native:check"].get("scope_resolutions", []))
    finish(
        s,
        b,
        closed=False,
        deferred=[
            {
                "id": "native:check",
                "paths": ["b/b.py"],
                "reason": "This additional component still needs the check",
            }
        ],
    )
    assert s["deferred"]["native:check"]["resolution"] is None
    assert scoped_questions(s, b)["native:check"]["paths"] == ["b/b.py"]
    assert s["deferred"]["native:check"].get("scope_resolutions", []) == earlier
    finish(s, b)
    assert not c.complete(s)
    assert b["status"] != "completed"
    finish(s, b, resolutions={"native:check": resolution()})
    assert c.complete(s)


def test_wholly_excluded_checkpoint_reopens_when_an_included_path_is_added():
    s = boundaries(questions={})
    s["exclude_paths"] = ["a/a.py"]
    s["deferred"]["native:check"] = {
        "paths": ["a/a.py"],
        "reason": "Required review",
        "resolution": None,
    }
    normalize_questions(s)
    assert s["deferred"]["native:check"]["resolution"]["explicit_exclusion"]
    b = next(item for item in c.pending(s) if item["paths"] == ["b/b.py"])
    finish(
        s,
        b,
        closed=False,
        deferred=[
            {"id": "native:check", "paths": ["b/b.py"], "reason": "Included source needs review"}
        ],
    )
    assert s["deferred"]["native:check"]["resolution"] is None
    assert scoped_questions(s, b)["native:check"]["paths"] == ["b/b.py"]


def test_frozen_legacy_owner_cannot_resolve_unknown_nonempty_references():
    s = boundaries(
        ("app.py",),
        questions={
            "legacy:unknown": {
                "paths": ["outside.py"],
                "reason": "Required external source",
                "resolution": None,
            }
        },
    )
    (item,) = c.pending(s)
    item["deferred_keys"] = ["legacy:unknown"]
    item.pop("deferred_paths", None)
    attempt = c.begin(s, item)
    normalize_questions(s)
    c.accept(
        s,
        item,
        attempt["scan_id"],
        receipt(s, item, resolutions={"legacy:unknown": resolution()}),
        proof(attempt["scan_id"]),
    )
    assert s["deferred"]["legacy:unknown"]["resolution"] is None
    assert not c.complete(s)
    assert scoped_questions(s, item) == {}


def test_two_pass_migration_retains_canonical_and_alias_resolution_receipts():
    s = boundaries(
        questions={
            "native:check": {
                "id": "check",
                "paths": ["a/a.py", "b/b.py"],
                "reason": "Required source review",
                "resolution": {**resolution(), "receipt": "archive-a"},
            },
            "echo:native:check": {
                "id": "native:check",
                "paths": ["b/b.py"],
                "reason": "Required source review",
                "resolution": {**resolution(), "receipt": "archive-b"},
            },
        }
    )
    normalize_questions(s)
    normalize_questions(s, {"archive-a": ["a/a.py"], "archive-b": ["b/b.py"]})
    question = s["deferred"]["native:check"]
    assert question["resolution"]["scope_receipts"] == ["archive-a", "archive-b"]
    assert {result["receipt"] for result in question["scope_resolutions"]} == {
        "archive-a",
        "archive-b",
    }


@pytest.mark.parametrize(
    "value",
    [
        {"path": "src/[x].py", "reason": "Assigned literal filename was omitted"},
        "src/[x].py",
        {
            "path": "src/[x].py",
            "pattern": "src/x.py",
            "reason": "Conflicting selectors must not hide an omission",
        },
    ],
)
def test_native_exact_paths_are_not_reinterpreted_as_exclusion_patterns(value):
    s = state(("src/[x].py", "src/x.py"))
    s["exclude_paths"] = ["src/x.py"]
    assert blocking_exclusions(s, ["src/[x].py"], [value]) == [value]


def test_checkpoint_over_a_retained_split_parent_gets_a_real_pending_owner():
    s = state(("a.py", "b.py"))
    mapping(s)
    finish(s, c.pending(s)[0])
    c.plan_boundaries(s)
    (parent,) = c.pending(s)
    finish(s, parent, closed=False)
    for child in c.pending(s):
        finish(s, child)
    assert parent["status"] == "split"
    accepted = {
        item["id"]
        for item in s["tasks"].values()
        if item["kind"] == "boundary" and item["status"] == "completed"
    }
    s["deferred"]["new:check"] = {
        "paths": ["a.py", "b.py"],
        "reason": "New required boundary validation",
        "resolution": None,
    }
    ensure_question_scopes(s)
    (item,) = c.pending(s)
    assert item["paths"] == ["a.py", "b.py"]
    assert item["id"] != parent["id"]
    assert item["deferred_keys"] == ["new:check"]
    finish(s, item, closed=False)
    ensure_question_scopes(s)
    children = c.pending(s)
    assert len(children) == 2
    assert all(child["id"] not in accepted for child in children)
    assert {tuple(child["paths"]) for child in children} == {("a.py",), ("b.py",)}
    task_count = len(s["tasks"])
    ensure_question_scopes(s)
    assert len(s["tasks"]) == task_count
    for child in children:
        finish(s, child, resolutions={"new:check": resolution()})
    assert c.complete(s)


def test_failed_checkpoint_leaf_remains_blocked_instead_of_receiving_new_generations():
    s = boundaries(
        ("app.py",),
        questions={
            "native:check": {
                "paths": ["app.py"],
                "reason": "Required source validation",
                "resolution": None,
            }
        },
    )
    (item,) = c.pending(s)
    for _ in range(3):
        finish(s, item, closed=False)
    assert item["status"] == "blocked"
    original = deepcopy(s["tasks"])
    for _ in range(4):
        ensure_question_scopes(s)
    assert s["tasks"] == original
    assert c.pending(s) == []
    assert not c.complete(s)
