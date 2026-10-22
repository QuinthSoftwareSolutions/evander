"""Protect bounded packing and one-time continuation of legacy fragmented scopes."""

from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from evander_codex_security_addon import coverage as c
from test_coverage import entry, finish, mapping, proof, receipt, request, state


def legacy_file_scopes(scopes):
    value = state(tuple(path for scope in scopes for path in scope))
    value.pop("batching_version")
    value["tasks"] = {
        key: item for key, item in value["tasks"].items() if item["kind"] != "files"
    }
    for paths in scopes:
        item = c.task("files", paths)
        value["tasks"][item["id"]] = item
    mapping(value)
    return value


def resume(source, *, scan_id="b" * 32):
    return c.make_state(
        scan_id,
        request(resume_from_scan_id=source["scan_id"]),
        source["directory_path"],
        {"revision": source["revision"], "entries": source["inventory"]},
        source["route"],
        previous=source,
        at=datetime.fromisoformat(source["finished_at"]) + timedelta(minutes=1),
    )


def test_sparse_directory_inventory_packs_to_the_file_cap_in_stable_order():
    entries = [
        entry(f"component-{i:05}/source.py", size=200, kind="binary" if i % 7 == 0 else "text")
        for i in range(10_003)
    ]
    scopes = list(c.batches(entries))

    assert [len(scope) for scope in scopes] == [100] * 100 + [3]
    assert [path for scope in scopes for path in scope] == [item["path"] for item in entries]
    assert scopes == list(c.batches(reversed(entries)))


def test_mixed_scopes_obey_the_text_byte_cap_without_isolating_every_nontext_file():
    entries = [
        entry("a/source.py", size=200_000),
        entry("b/source.py", size=324_288),
        entry("c/image.png", kind="binary", size=400_000),
        entry("d/link", kind="symlink", mode="120000"),
        entry("e/module", kind="gitlink", mode="160000", size=0),
        entry("f/source.py", size=1),
    ]

    assert list(c.batches(entries)) == [
        ["a/source.py", "b/source.py", "c/image.png", "d/link", "e/module"],
        ["f/source.py"],
    ]


@pytest.mark.parametrize("kind", ["text", "binary", "unavailable"])
def test_oversized_or_unavailable_files_stay_intact_without_losing_adjacent_files(kind):
    entries = [
        entry("a/source.py"),
        entry("b/isolated", kind=kind, size=0 if kind == "unavailable" else 524_289),
        entry("c/source.py"),
        entry("d/space and\nnewline.md"),
    ]

    assert list(c.batches(entries)) == [
        ["a/source.py"],
        ["b/isolated"],
        ["c/source.py", "d/space and\nnewline.md"],
    ]


@pytest.mark.parametrize("missing", ["image.png", "link", "module"])
def test_mixed_batch_still_requires_every_nontext_disposition(missing):
    entries = [
        entry("image.png", kind="binary"),
        entry("link", kind="symlink", mode="120000"),
        entry("module", kind="gitlink", mode="160000", size=0),
        entry("source.py"),
    ]
    source = c.make_state(
        "a" * 32,
        request(),
        "root/project",
        {"revision": "d" * 40, "entries": entries},
        {},
    )
    mapping(source)
    item, = c.pending(source)
    dispositions = {
        path: {"disposition": "not_applicable", "reason": "Explicit source-backed assessment"}
        for path in ("image.png", "link", "module")
    }
    without_one = {path: value for path, value in dispositions.items() if path != missing}
    before = deepcopy(source)
    finish(source, item, dispositions=without_one)
    assert item["status"] == "split"
    assert c.progress(source)["files_reviewed"] == 0
    assert c.progress(source)["files_not_applicable"] == 0

    item, = c.pending(before)
    finish(before, item, dispositions=dispositions)
    assert c.progress(before)["files_reviewed"] == 1
    assert c.progress(before)["files_not_applicable"] == 3


def test_legacy_resume_regroups_only_unattempted_work_and_preserves_evidence(tmp_path):
    source = legacy_file_scopes([
        ["a/reviewed.py"],
        ["b/first.py", "b/second.py"],
        ["c/new.py"],
        ["d/new.py"],
        ["e/new.py"],
        ["f/interrupted.py"],
        ["g/blocked.py"],
    ])
    items = {tuple(item["paths"]): item for item in source["tasks"].values()}
    finish(source, items[("a/reviewed.py",)])
    partial = items[("b/first.py", "b/second.py")]
    attempt = c.begin(source, partial)
    finding = {
        "findingId": "retained-finding",
        "title": "Unchecked input",
        "severity": {"level": "high"},
    }
    c.accept(
        source,
        partial,
        attempt["scan_id"],
        receipt(source, partial, closed=False),
        proof(attempt["scan_id"], [finding]),
    )
    attempt["cleanup_confirmed"] = True
    interrupted = items[("f/interrupted.py",)]
    attempt = c.begin(source, interrupted)
    c.retry(source, interrupted, "Transport interrupted")
    attempt["cleanup_confirmed"] = True
    blocked = items[("g/blocked.py",)]
    for _ in range(3):
        finish(source, blocked, closed=False)
    assert blocked["status"] == "blocked"
    # Legacy cancellation could overwrite scheduling after accepting valid results.
    items[("a/reviewed.py",)]["status"] = "blocked"
    architecture = next(item for item in source["tasks"].values() if item["kind"] == "architecture")
    architecture["status"] = "blocked"
    source.update(finished_at=c.now().isoformat(), cleanup_confirmed=True)
    store = c.CoverageStore(tmp_path)
    store.create(source)
    original = store.get(source["scan_id"], source["directory_path"])

    continued = resume(original)
    new_scopes = [item for item in continued["tasks"].values() if item["kind"] == "files"]
    assert [item["paths"] for item in new_scopes if item["status"] == "pending"] == [
        ["f/interrupted.py"],
        ["g/blocked.py"],
        ["b/first.py"],
        ["b/second.py"],
        ["c/new.py", "d/new.py", "e/new.py"],
    ]
    assert continued["tasks"][partial["id"]]["status"] == "split"
    assert continued["tasks"][items[("a/reviewed.py",)]["id"]]["status"] == "completed"
    assert continued["tasks"][architecture["id"]]["status"] == "completed"
    assert continued["files"]["a/reviewed.py"] == original["files"]["a/reviewed.py"]
    assert continued["proofs"] == original["proofs"]
    assert c.aggregate(continued)[1]["high"] == 1
    assert continued["revision"] == original["revision"]
    assert continued["inventory_digest"] == original["inventory_digest"]
    assert continued["route"] == original["route"]
    assert continued["lineage"] == [original["scan_id"]]
    assert c.progress(continued)["files_reviewed"] == 1
    assert c.progress(continued)["files_blocked"] == 0
    assert all(not item["attempts"] for item in continued["tasks"].values())
    assert datetime.fromisoformat(continued["deadline_at"]) == (
        datetime.fromisoformat(source["finished_at"]) + timedelta(minutes=1, hours=6)
    )
    assert store.get(source["scan_id"], source["directory_path"]) == original

    # A later continuation must retain the refinements despite reset attempt counters.
    continued.update(
        finished_at=(datetime.fromisoformat(continued["created_at"]) + timedelta(minutes=5)).isoformat(),
        cleanup_confirmed=True,
    )
    second = resume(continued, scan_id="c" * 32)
    assert {key: value["paths"] for key, value in second["tasks"].items()} == {
        key: value["paths"] for key, value in continued["tasks"].items()
    }


def test_legacy_boundary_regrouping_keeps_outstanding_work_with_one_owner():
    source = legacy_file_scopes([[f"{name}/source.py"] for name in "abcd"])
    for item in list(source["tasks"].values()):
        if item["kind"] == "files":
            finish(source, item)
    scopes = [c.task("boundary", [f"{name}/source.py"]) for name in "abcd"]
    source["tasks"].update({item["id"]: item for item in scopes})
    source["boundary_planned"] = True
    source["deferred"] = {
        "review:a": {"paths": ["a/source.py"], "resolution": None},
        "review:b": {"paths": ["b/source.py"], "resolution": None},
        "review:c": {"paths": ["c/source.py"], "resolution": None},
        "review:d": {"paths": ["d/source.py"], "resolution": None},
        "review:pathless": {"resolution": None},
    }
    c.assign_deferred(source, scopes, source["deferred"])
    finish(source, scopes[0], resolutions={
        key: {"disposition": "no_issue_found", "reason": "Verified source boundary"}
        for key in scopes[0]["deferred_keys"]
    })
    finish(source, scopes[1], closed=False)
    source.update(finished_at=c.now().isoformat(), cleanup_confirmed=True)
    original = deepcopy(source)

    continued = resume(source)
    work = [item for item in continued["tasks"].values() if item["status"] == "pending"]
    assert [item["paths"] for item in work] == [
        ["b/source.py"], ["c/source.py", "d/source.py"],
    ]
    assert Counter(key for item in work for key in item["deferred_keys"]) == {
        "review:b": 1, "review:c": 1, "review:d": 1,
    }
    assert continued["deferred"] == original["deferred"]
    assert continued["proofs"] == original["proofs"]
    assert c.progress(continued)["files_reviewed"] == 4
    assert source == original
    for item in work:
        finish(continued, item, resolutions={
            key: {"disposition": "no_issue_found", "reason": "Verified source boundary"}
            for key in item["deferred_keys"]
        })
    assert c.complete(continued)


@pytest.mark.parametrize("version", [0, 3, True, None, "2"])
def test_resume_rejects_unknown_batching_versions_without_changing_source(version):
    source = state()
    source.update(batching_version=version, finished_at=c.now().isoformat(), cleanup_confirmed=True)
    original = deepcopy(source)
    with pytest.raises(ValueError, match="batching policy is incompatible"):
        resume(source)
    assert source == original
