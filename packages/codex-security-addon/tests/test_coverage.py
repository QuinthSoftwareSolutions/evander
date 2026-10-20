from copy import deepcopy
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import subprocess

import pytest

from evander.security_scans import SecurityScanRequest
from evander_codex_security_addon import coverage as c


def entry(path, *, kind="text", size=20, mode="100644"):
    return {
        "path": path,
        "kind": kind,
        "oid": hashlib.sha1(path.encode()).hexdigest(),
        "size": size,
        "mode": mode,
    }


def request(**kwargs):
    return SecurityScanRequest(
        session_id="scan-session",
        repo_path="/home/agent/repo",
        coverage_policy="all_tracked_files",
        managed_repo_id="managed-example",
        **kwargs,
    )


def state(paths=("src/a.py", "src/b.py", "tests/a.py")):
    return c.make_state(
        "a" * 32,
        request(),
        "root/project",
        {"revision": "d" * 40, "entries": [entry(p) for p in paths]},
        {"model": "example"},
    )


def receipt(s, item, *, closed=True, **kwargs):
    return {
        "kind": item["kind"],
        "paths": item["paths"],
        "revision": s["revision"],
        "sdk_version": c.SDK_VERSION,
        "coverage": "complete" if closed else "partial",
        "deferred": [],
        "exclusions": [],
        "needs_follow_up": False,
        **kwargs,
    }


def proof(identifier="native-1", findings=None):
    return {
        "archive_key": "archive-" + identifier,
        "archive_sha256": "f" * 64,
        "provider_scan_id": identifier,
        "target_id": "target-1",
        "findings": findings or [],
    }


def finish(s, item, *, at=None, closed=True, **kwargs):
    at = at or c.now()
    a = c.begin(s, item, at=at)
    c.accept(
        s, item, a["scan_id"], receipt(s, item, closed=closed, **kwargs), proof(a["scan_id"]), at=at
    )
    a["cleanup_confirmed"] = True


def mapping(s):
    item = next(x for x in s["tasks"].values() if x["kind"] == "architecture")
    finish(
        s,
        item,
        closed=False,
        architecture={"paths": list(s["files"]), "threat_model": {"assets": ["application state"]}},
    )


def test_scopes_split_without_crediting_partial_or_late_results_and_keep_findings():
    s = state()
    mapping(s)
    assert c.progress(s)["files_reviewed"] == 0
    first, second = c.pending(s)
    a = c.begin(s, first)
    c.begin(s, second)
    assert c.pending(s) == []
    finding = {"findingId": "finding-1", "title": "Unchecked input", "severity": {"level": "high"}}
    old_receipt = receipt(s, first, closed=False)
    assert c.accept(s, first, a["scan_id"], old_receipt, proof(findings=[finding]))
    assert first["status"] == "split"
    assert c.progress(s)["files_reviewed"] == 0
    assert not c.accept(s, first, a["scan_id"], receipt(s, first), proof())
    for child in [x for x in s["tasks"].values() if x.get("parent") == first["id"]]:
        finish(s, child)
    assert c.progress(s)["files_reviewed"] == 2
    assert c.aggregate(s)[1]["high"] == 1
    assert not c.complete(s)


@pytest.mark.parametrize(
    "change",
    [
        {"revision": "e" * 40},
        {"paths": ["different.py"]},
        {"sdk_version": "unknown"},
        {"kind": "boundary"},
    ],
)
def test_wrong_target_or_scope_never_earns_coverage(change):
    s = state()
    mapping(s)
    item = c.pending(s)[0]
    a = c.begin(s, item)
    before = deepcopy(s["proofs"])
    with pytest.raises(ValueError):
        c.accept(s, item, a["scan_id"], {**receipt(s, item), **change}, proof())
    assert s["proofs"] == before
    assert c.progress(s)["files_reviewed"] == 0


def test_binary_requires_explicit_disposition_and_three_attempts_block_it():
    s = c.make_state(
        "a" * 32,
        request(),
        "root/project",
        {"revision": "d" * 40, "entries": [entry("logo.png", kind="binary")]},
        {},
    )
    mapping(s)
    item = c.pending(s)[0]
    for _ in range(3):
        finish(s, item)
    assert item["status"] == "blocked"
    assert c.progress(s)["files_blocked"] == 1
    assert c.progress(s)["files_not_applicable"] == 0
    assert not c.complete(s)


def test_binary_disposition_and_boundary_closure_are_both_required():
    s = c.make_state(
        "a" * 32,
        request(),
        "root/project",
        {"revision": "d" * 40, "entries": [entry("logo.png", kind="binary")]},
        {},
    )
    mapping(s)
    finish(
        s,
        c.pending(s)[0],
        dispositions={
            "logo.png": {
                "disposition": "not_applicable",
                "reason": "Reviewed image metadata; no executable source content",
            }
        },
    )
    c.plan_boundaries(s)
    assert not c.complete(s)
    finish(s, c.pending(s)[0])
    assert c.complete(s)
    assert c.progress(s)["files_not_applicable"] == 1


def test_deferred_validation_cannot_vanish_when_later_review_claims_complete():
    s = state(("src/a.py",))
    mapping(s)
    s["deferred"]["native-1:pending"] = {
        "paths": ["src/a.py"],
        "reason": "Parent validation pending",
        "resolution": None,
    }
    finish(s, c.pending(s)[0])
    c.plan_boundaries(s)
    item = c.pending(s)[0]
    finish(s, item)
    assert not c.complete(s)
    assert item["status"] == "pending"
    finish(
        s,
        item,
        resolutions={
            "native-1:pending": {
                "disposition": "rejected",
                "reason": "The source enforces the required authorization before the sink",
            }
        },
    )
    assert c.complete(s)


def test_parallel_boundaries_close_independently_and_keep_global_work_until_resolved(tmp_path):
    s = state(("a/a.py", "b/b.py"))
    mapping(s)
    for item in list(c.pending(s)):
        finish(s, item)
    s["deferred"] = {
        "native:a": {"paths": ["a/a.py"], "resolution": None},
        "native:b": {"paths": ["b/b.py"], "resolution": None},
        "native:question": {
            "reason": "Verify the shared authentication boundary",
            "resolution": None,
        },
    }
    c.plan_boundaries(s)
    store = c.CoverageStore(tmp_path)
    store.create(s)
    store.save(s)
    s = c.CoverageStore(tmp_path).get(s["scan_id"], "root/project")
    first, second = c.pending(s)
    slow = c.begin(s, second)
    finish(
        s,
        first,
        resolutions={
            key: {"disposition": "no_issue_found", "reason": "Source authorization verified"}
            for key in ("native:a", "native:question")
        },
    )
    assert first["status"] == "completed"
    assert first["deferred_keys"] == ["native:a", "native:question"]
    assert second["deferred_keys"] == ["native:b"]
    assert len(first["attempts"]) == 1
    assert not c.complete(s)
    assert s["deferred"]["native:b"]["resolution"] is None
    c.accept(
        s,
        second,
        slow["scan_id"],
        receipt(
            s,
            second,
            resolutions={
                "native:b": {
                    "disposition": "reported",
                    "reason": "Validated boundary finding retained",
                }
            },
        ),
        proof(slow["scan_id"]),
    )
    slow["cleanup_confirmed"] = True
    assert c.complete(s)


def test_partial_boundary_splits_keep_each_outstanding_item_assigned_once():
    s = state(("src/a.py", "src/b.py"))
    mapping(s)
    finish(s, c.pending(s)[0])
    s["deferred"] = {
        "native:a": {"paths": ["src/a.py"], "resolution": None},
        "native:b": {"paths": ["src/b.py"], "resolution": None},
    }
    c.plan_boundaries(s)
    parent = c.pending(s)[0]
    finish(
        s,
        parent,
        closed=False,
        deferred=[{"id": "followup", "paths": ["src/b.py"], "reason": "Additional source review"}],
    )
    children = c.pending(s)
    assert len(children) == 2
    assigned = [key for item in children for key in item["deferred_keys"]]
    assert len(assigned) == len(set(assigned)) == 3
    assert set(assigned) == set(s["deferred"])
    for item in children:
        finish(
            s,
            item,
            resolutions={
                key: {"disposition": "no_issue_found", "reason": "Source reviewed"}
                for key in item["deferred_keys"]
            },
        )
    assert c.complete(s)


def test_absolute_budget_and_two_review_cap_survive_journal_restart(tmp_path):
    s = state(("a/a.py", "b/a.py", "c/a.py"))
    mapping(s)
    store = c.CoverageStore(tmp_path)
    store.create(s)
    for item in c.pending(s):
        c.begin(s, item)
    store.save(s)
    loaded = c.CoverageStore(tmp_path).get(s["scan_id"], "root/project")
    assert len([x for x in loaded["tasks"].values() if x["status"] == "running"]) == 2
    assert c.pending(loaded) == []
    assert loaded["deadline_at"] == s["deadline_at"]
    after_deadline = c.now() + timedelta(hours=7)
    assert c.pending(loaded, at=after_deadline) == []


def test_explicit_resume_reuses_only_accepted_work_and_original_snapshot():
    s = state(("a/a.py", "b/a.py"))
    mapping(s)
    finish(s, c.pending(s)[0])
    s.update(finished_at=c.now().isoformat(), cleanup_confirmed=True)
    inventory = {"revision": s["revision"], "entries": s["inventory"]}
    resumed = c.make_state(
        "b" * 32,
        request(resume_from_scan_id=s["scan_id"]),
        "root/project",
        inventory,
        s["route"],
        previous=s,
    )
    assert resumed["scan_id"] != s["scan_id"]
    assert c.progress(resumed)["files_reviewed"] == 1
    assert resumed["lineage"] == [s["scan_id"]]
    assert resumed["deadline_at"] > s["deadline_at"]
    assert s["finished_at"] is not None
    with pytest.raises(ValueError, match="incompatible"):
        c.make_state(
            "c" * 32,
            request(),
            "root/project",
            {**inventory, "revision": "e" * 40},
            s["route"],
            previous=s,
        )
    with pytest.raises(ValueError, match="incompatible"):
        c.make_state(
            "c" * 32, request(), "root/project", inventory, {"model": "another"}, previous=s
        )


@pytest.mark.parametrize("condition", ["active", "expired", "cleanup"])
def test_resume_rejects_active_expired_and_uncertain_cleanup(condition):
    s = state()
    s.update(finished_at=c.now().isoformat(), cleanup_confirmed=True)
    if condition == "active":
        s["finished_at"] = None
    elif condition == "expired":
        s["finished_at"] = (c.now() - timedelta(days=8)).isoformat()
    else:
        s["cleanup_confirmed"] = False
    with pytest.raises(ValueError, match="active, complete, expired"):
        c.make_state(
            "b" * 32,
            request(),
            "root/project",
            {"revision": s["revision"], "entries": s["inventory"]},
            s["route"],
            previous=s,
        )


def test_repository_claim_is_atomic_and_directory_scoped(tmp_path):
    store = c.CoverageStore(tmp_path)

    def claim(number):
        value = state()
        value["scan_id"] = f"{number:032x}"
        try:
            return store.create(value)["scan_id"]
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(claim, range(1, 5)))
    assert sum(x is not None for x in results) == 1
    with pytest.raises(ValueError, match="this directory"):
        store.get(next(x for x in results if x), "root/other")


def test_exact_root_controls_deduplicate_across_sdk_checkouts_but_siblings_survive():
    s = state()
    base = {
        "findingId": "one",
        "ruleId": "authorization.missing-check",
        "identity": {"anchor": "request-authorization"},
        "title": "Missing guard",
        "severity": {"level": "high"},
        "locations": [{"path": "src/a.py", "role": "root_control", "startLine": 8, "endLine": 10}],
    }
    same = {**deepcopy(base), "findingId": "two"}
    sibling = {
        **deepcopy(base),
        "findingId": "three",
        "locations": [{"path": "src/b.py", "role": "root_control", "startLine": 8, "endLine": 10}],
    }
    s["proofs"] = [
        proof("one", [base]),
        {**proof("two", [same]), "target_id": "different-checkout"},
        proof("three", [sibling]),
    ]
    findings, counts, _ = c.aggregate(s)
    assert counts["high"] == 2
    assert len(findings[0]["sources"]) == 2


def test_real_git_inventory_preserves_tracked_ignored_generated_and_nontext_paths(
    tmp_path, monkeypatch
):
    runtime = Path(__file__).parents[1] / "evander_codex_security_addon"
    monkeypatch.syspath_prepend(str(runtime))
    from coverage_runner import inventory
    import runner

    monkeypatch.setattr(runner, "HOME", tmp_path)
    repo = tmp_path / "repository"
    repo.mkdir()

    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)

    git("init", "-q")
    for name, body in {
        ".gitignore": b"generated/\n",
        "docs/note.md": b"docs",
        "generated/code.js": b"export const a=1",
        "vendor/lib.py": b"x=1",
        "space and\nnewline.txt": b"text",
        "logo.png": b"\x89PNG\x00\xff",
    }.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)
    (repo / "external-link").symlink_to("/outside/this/repository")
    git("add", "--all", "--force")
    git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "inventory",
    )
    (repo / "late-binary.bin").write_bytes(b"x" * 9000 + b"\0")
    (repo / "large-unicode.txt").write_bytes(b"x" * (1024 * 1024 - 1) + "é".encode())
    git("add", "--all", "--force")
    commit = git("rev-parse", "HEAD").stdout.decode().strip()
    git("update-index", "--add", "--cacheinfo", "160000," + commit + ",vendor/module")
    (repo / "vendor/module").mkdir()
    git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "nontext entries",
    )
    result = inventory({"repo_path": str(repo)})
    rows = {x["path"]: x for x in result["entries"]}
    assert len(rows) == 10
    assert rows["vendor/module"]["mode"] == "160000"
    assert rows["vendor/module"]["kind"] == "gitlink"
    assert rows["late-binary.bin"]["kind"] == "binary"
    assert rows["large-unicode.txt"]["kind"] == "text"
    assert rows["generated/code.js"]["kind"] == "text"
    assert rows["logo.png"]["kind"] == "binary"
    assert rows["external-link"]["kind"] == "symlink"
    assert "space and\nnewline.txt" in rows
    assert len(c.inventory_digest(result["entries"])) == 64
    assert not (repo / "outside").exists()


def test_inventory_batch_caps_and_no_silent_omissions():
    entries = [entry(f"src/{i:03}.py", size=8000) for i in range(110)] + [
        entry("large.py", size=900000)
    ]
    chunks = list(c.batches(entries))
    assert sorted(p for chunk in chunks for p in chunk) == sorted(x["path"] for x in entries)
    assert all(len(chunk) <= 100 for chunk in chunks)
    assert ["large.py"] in chunks
    assert all(
        sum(next(x["size"] for x in entries if x["path"] == p) for p in chunk) <= 524288
        for chunk in chunks
        if chunk != ["large.py"]
    )


def test_text_cannot_be_dismissed_and_unavailable_content_remains_blocked():
    for kind in ("text", "unavailable"):
        s = c.make_state(
            "a" * 32,
            request(),
            "root/project",
            {"revision": "d" * 40, "entries": [entry("source.py", kind=kind)]},
            {},
        )
        mapping(s)
        item = c.pending(s)[0]
        for _ in range(3):
            finish(
                s,
                item,
                dispositions={
                    "source.py": {
                        "disposition": "not_applicable",
                        "reason": "Unsupported source format",
                    }
                },
            )
        assert c.progress(s)["files_blocked"] == 1
        assert not c.complete(s)


def test_retention_protects_source_pins_until_latest_continuation_expires(tmp_path):
    clock = c.now()
    at = clock - timedelta(days=9)
    s = c.make_state(
        "a" * 32,
        request(),
        "root/project",
        {"revision": "d" * 40, "entries": [entry("a.py")]},
        {},
        at=at,
    )
    s.update(finished_at=(clock - timedelta(days=8)).isoformat())
    store = c.CoverageStore(tmp_path)
    store.create(s)
    store.save(s)
    child = c.make_state(
        "b" * 32,
        request(resume_from_scan_id=s["scan_id"]),
        "root/project",
        {"revision": s["revision"], "entries": s["inventory"]},
        {},
        previous=s,
        at=clock - timedelta(days=7),
    )
    child.update(finished_at=(clock - timedelta(days=6)).isoformat())
    store.create(child)
    store.save(child)
    assert store.expired(at=clock) == []
    assert c.resumable(s, at=clock, until=store.retained_until(s))
    assert {x["scan_id"] for x in store.expired(at=clock + timedelta(days=2))} == {
        s["scan_id"],
        child["scan_id"],
    }


def test_repository_claim_is_shared_across_directory_scopes(tmp_path):
    store = c.CoverageStore(tmp_path)
    store.create(state())
    other = state()
    other.update(scan_id="b" * 32, directory_path="root/another")
    with pytest.raises(ValueError, match="active scan"):
        store.create(other)


def test_uncertain_descendant_protects_ancestors_and_retention_starts_after_cleanup(tmp_path):
    clock = c.now()
    store = c.CoverageStore(tmp_path)
    source = state()
    source.update(finished_at=(clock - timedelta(days=20)).isoformat())
    store.create(source)
    store.save(source)
    child = state()
    child.update(
        scan_id="b" * 32,
        lineage=[source["scan_id"]],
        finished_at=(clock - timedelta(days=10)).isoformat(),
        cleanup_confirmed=False,
    )
    store.create(child)
    store.save(child)
    assert store.expired(at=clock) == []
    child.update(cleanup_confirmed=True, cleanup_finished_at=clock.isoformat())
    store.save(child)
    assert store.expired(at=clock + timedelta(days=6)) == []
    assert store.retained_until(source) == clock + timedelta(days=7)
    assert {s["scan_id"] for s in store.expired(at=clock + timedelta(days=8))} == {
        source["scan_id"],
        child["scan_id"],
    }
