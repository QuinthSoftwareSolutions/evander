"""Source credit requires positive evidence; hypotheses remain unverified."""

from copy import deepcopy

import pytest

from evander_codex_security_addon import coverage as c
from evander_codex_security_addon.coverage_review import review_facts
from test_coverage import entry, finish, mapping, proof, receipt
from test_coverage_batching import resume
from test_coverage_file_controls import tracked


def surface(label, disposition="no_issue_found", notes="Assigned source and input flow inspected"):
    return {"label": label, "disposition": disposition, "notes": notes}


def candidate(paths):
    return {
        "id": "candidate-renderer", "paths": paths,
        "candidate": {"title": "Potential unsafe rendering", "source": "message -> vendor renderer"},
        "reason": "Vendor implementation and deployment controls are unavailable offline",
    }


def accept_native(s, native):
    item, = c.pending(s)
    attempt = c.begin(s, item)
    facts = review_facts(native, item["paths"])
    evidence = proof(attempt["scan_id"])
    c.accept(s, item, attempt["scan_id"], receipt(
        s, item, closed=False, deferred=native["deferred"], **facts,
        dispositions=facts["source_dispositions"],
    ), evidence)
    attempt["cleanup_confirmed"] = True
    return item, evidence


def test_unverified_dependency_candidate_does_not_reopen_reviewed_source_or_become_a_finding():
    s = tracked([entry("src/alerts.ts"), entry("deploy.yml")], incomplete_review_policy="isolate_blockers")
    mapping(s)
    question = candidate(["src/alerts.ts", "deploy.yml"])
    item, evidence = accept_native(s, {
        "completeness": "partial", "deferred": [question],
        "surfaces": [surface("file:src/alerts.ts"), surface("file:deploy.yml", "reported"),
                     surface("candidate:vendor renderer", "needs_follow_up")],
    })
    assert item["status"] == "completed"
    assert c.progress(s)["files_reviewed"] == 2
    assert c.progress(s)["files_isolated"] == 0
    assert c.progress(s)["required_questions_pending"] == 0
    assert c.progress(s)["validation_questions_pending"] == 1
    key = evidence["provider_scan_id"] + ":candidate-renderer"
    assert s["deferred"][key]["resolution"]["validation_status"] == "unverified"
    assert s["open_questions"]["validation:" + key]["candidate"] == question["candidate"]
    assert c.aggregate(s)[0] == []
    assert not c.required_work_complete(s)  # Boundary source review is still required.
    c.plan_boundaries(s)
    for scope in c.pending(s):
        finish(s, scope)
    assert c.required_work_complete(s)
    assert c.complete(s)  # Complete source assessment, despite a retained unverified hypothesis.


def test_mixed_partial_batch_keeps_certified_files_and_isolates_only_unread_content():
    s = tracked([entry("a.py"), entry("b.py"), entry("locked.bin", kind="binary")], incomplete_review_policy="isolate_blockers")
    mapping(s)
    item, evidence = accept_native(s, {
        "completeness": "partial", "deferred": [{"id": "decoder", "paths": ["locked.bin"], "reason": "Decoder unavailable"}],
        "surfaces": [surface("file:a.py"), surface("file:locked.bin", "needs_follow_up")],
    })
    assert item["status"] == "split"
    assert s["files"]["a.py"]["receipt"] == evidence["archive_key"]
    assert s["files"]["a.py"]["native_coverage"] == "partial"
    assert s["files"]["b.py"]["disposition"] == "pending"
    assert s["files"]["locked.bin"]["disposition"] == "blocked"
    assert [scope["paths"] for scope in c.pending(s)] == [["b.py"]]


@pytest.mark.parametrize("missing", ["unavailable", "binary", "text"])
def test_candidate_alone_never_establishes_file_review(missing):
    s = tracked([entry("unknown", kind=missing)], incomplete_review_policy="isolate_blockers")
    mapping(s)
    item, _ = accept_native(s, {"completeness": "partial", "deferred": [candidate(["unknown"])], "surfaces": []})
    assert item["status"] == "blocked"
    assert c.progress(s)["files_reviewed"] == 0
    assert c.progress(s)["required_questions_pending"] == 1
    assert c.progress(s)["validation_questions_pending"] == 0


@pytest.mark.parametrize("extra", [
    {"deferred": [{"id": "source", "paths": ["a.py"], "reason": "Trace the remaining source"}]},
    {"deferred": [{"id": "source", "paths": [], "reason": "Remaining work unknown"}]},
    {"deferred": [candidate(["*"])]},
    {"deferred": [candidate(["../a.py"])]},
    {"deferred": [candidate(["outside.py"])]},
    {"surfaces": [surface("file:a.py", "needs_follow_up")]},
    {"surfaces": [surface("unmapped trust boundary", "needs_follow_up")]},
    {"explicitExclusions": [{"path": "a.py"}]},
    {"explicitExclusions": [{"pattern": "*.py"}]},
    {"explicitExclusions": [{"reason": "Unknown omitted work"}]},
    {},
])
def test_unknown_or_contradictory_partial_source_cannot_receive_credit(extra):
    native = {"completeness": "partial", "deferred": [], "surfaces": [surface("file:a.py")]}
    native.update({k: v for k, v in extra.items() if k != "surfaces"})
    native["surfaces"].extend(extra.get("surfaces", []))
    facts = review_facts(native, ["a.py"])
    assert not facts["source_review_complete"]
    assert facts["source_dispositions"] == {}


@pytest.mark.parametrize("kind,disposition", [("text", "not_applicable"), ("unavailable", "no_issue_found")])
def test_positive_file_notes_cannot_bypass_snapshot_content_requirements(kind, disposition):
    s = tracked([entry("a.py", kind=kind)], incomplete_review_policy="isolate_blockers")
    mapping(s)
    item, _ = accept_native(s, {"completeness": "partial", "deferred": [candidate(["a.py"])], "surfaces": [surface("file:a.py", disposition)]})
    assert item["status"] == "blocked"
    assert c.progress(s)["files_reviewed"] == 0
    assert c.progress(s)["files_not_applicable"] == 0


@pytest.mark.parametrize("version", [1, 2, 3])
def test_continuation_preserves_certified_credit_and_unverified_candidate_across_journal_versions(version):
    s = tracked([entry("a.py"), entry("b.py")])
    mapping(s)
    accept_native(s, {"completeness": "partial", "deferred": [candidate(["a.py"])], "surfaces": [surface("file:a.py")]})
    s.update(version=version, finished_at=c.now().isoformat(), cleanup_confirmed=True)
    before = deepcopy(s)
    continued = resume(s, rebuild_pending_batches=True)
    assert continued["version"] == 3
    assert continued["files"]["a.py"] == s["files"]["a.py"]
    assert [scope["paths"] for scope in c.pending(continued)] == [["b.py"]]
    assert c.progress(continued)["validation_questions_pending"] == 1
    assert s == before


@pytest.mark.parametrize("marker,host_reviewed,hard_followup", [(False, True, False), (True, False, False), (True, True, True), (True, True, False)])
def test_boundary_validation_classification_requires_positive_local_source_completion(marker, host_reviewed, hard_followup):
    native = {"completeness": "partial", "deferred": [{"id": "saved:checkpoint", "paths": ["a.py"], "reason": "Need deployment access"}],
              "surfaces": [surface("validation:saved:checkpoint", "needs_follow_up")]}
    if marker:
        native["surfaces"].append(surface("review:boundary"))
    if hard_followup:
        native["surfaces"].append(surface("unread auth handler", "needs_follow_up"))
    facts = review_facts(native, ["a.py"], kind="boundary", reviewed_paths=["a.py"] if host_reviewed else [], checkpoint_keys=["saved:checkpoint"])
    accepted = marker and host_reviewed and not hard_followup
    assert facts["source_review_complete"] == accepted
    assert bool(facts["validation_resolutions"]) == accepted


def test_boundary_cannot_classify_another_scopes_checkpoint():
    facts = review_facts({"completeness": "partial", "deferred": [], "surfaces": [surface("review:boundary"), surface("validation:other", "needs_follow_up")]},
                        ["a.py"], kind="boundary", reviewed_paths=["a.py"], checkpoint_keys=["own"])
    assert facts["validation_resolutions"] == {}
    assert not facts["source_review_complete"]
