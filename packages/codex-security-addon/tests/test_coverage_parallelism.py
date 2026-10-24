from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from evander_codex_security_addon import coverage as c
from test_coverage import entry, finish, mapping, proof, receipt, request, subscription_route


def scan(limit=None):
    return c.make_state(
        "a" * 32, request(max_parallel_reviews=limit), "root/project",
        {
            "revision": "d" * 40,
            "entries": [entry(f"src/{i}.py", size=600_000) for i in range(6)],
        },
        subscription_route(),
    )


@pytest.mark.parametrize("limit", [None, 1, 2, 3, 4])
@pytest.mark.parametrize("phase", ["files", "boundary"])
def test_frozen_parallelism_bounds_slots_and_deadline_after_restart(tmp_path, limit, phase):
    state = scan(limit)
    assert len(c.pending(state)) == 1  # Architecture is serial at every limit.
    mapping(state)
    if phase == "boundary":
        while scopes := c.pending(state):
            for item in scopes:
                finish(state, item)
        for path in state["files"]:
            item = c.task("boundary", [path])
            state["tasks"][item["id"]] = item
    expected = 2 if limit is None else limit
    scopes = c.pending(state)
    assert len(scopes) == expected
    for item in scopes:
        c.begin(state, item)
    assert c.pending(state) == []
    # A saved result cannot free a slot until its owned worker is cleaned up.
    finished = scopes[0]
    attempt = finished["attempts"][-1]
    assert c.accept(
        state, finished, attempt["scan_id"], receipt(state, finished), proof(attempt["scan_id"])
    )
    store = c.CoverageStore(tmp_path)
    store.create(state)
    loaded = c.CoverageStore(tmp_path).get(state["scan_id"], "root/project")
    assert c.progress(loaded)["max_parallel_reviews"] == expected
    assert c.pending(loaded) == []
    loaded["tasks"][finished["id"]]["attempts"][-1]["cleanup_confirmed"] = True
    assert len(c.pending(loaded)) == 1
    assert loaded["deadline_at"] == state["deadline_at"]
    consolidation = datetime.fromisoformat(state["deadline_at"]) - timedelta(minutes=5)
    assert c.pending(loaded, at=consolidation) == []
    loaded["stop_reason"] = "Operator cancelled"
    assert c.pending(loaded) == []


def test_legacy_journal_keeps_two_slots_without_mutating_saved_policy(tmp_path):
    state = scan()
    state.pop("max_parallel_reviews")
    state["request"].pop("max_parallel_reviews")
    mapping(state)
    store = c.CoverageStore(tmp_path)
    store.create(state)
    loaded = c.CoverageStore(tmp_path).get(state["scan_id"], "root/project")
    assert c.progress(loaded)["max_parallel_reviews"] == 2
    assert len(c.pending(loaded)) == 2
    assert "max_parallel_reviews" not in loaded


@pytest.mark.parametrize("value", [None, 0, 5, True, 4.0, "4"])
def test_corrupt_saved_parallelism_is_not_silently_defaulted(value):
    state = scan()
    mapping(state)
    state["max_parallel_reviews"] = value
    with pytest.raises(ValueError, match="parallelism is incompatible"):
        c.pending(state)
    with pytest.raises(ValueError, match="parallelism is incompatible"):
        c.progress(state)


@pytest.mark.parametrize(
    ("source_limit", "requested", "expected"),
    [(None, None, 2), (None, 4, 4), (4, None, 4), (4, 1, 1), (1, 4, 4)],
)
def test_resume_inherits_or_explicitly_changes_parallelism_without_changing_evidence(
    source_limit, requested, expected
):
    source = scan(source_limit)
    if source_limit is None:
        source.pop("max_parallel_reviews")
        source["request"].pop("max_parallel_reviews")
    mapping(source)
    item = c.pending(source)[0]
    attempt = c.begin(source, item)
    finding = {"findingId": "retained-1", "title": "Unchecked boundary", "severity": {"level": "high"}}
    c.accept(source, item, attempt["scan_id"], receipt(source, item), proof(findings=[finding]))
    attempt["cleanup_confirmed"] = True
    source.update(finished_at=c.now().isoformat(), cleanup_confirmed=True)
    before = deepcopy(source)
    at = c.now()
    resumed = c.make_state(
        "b" * 32,
        request(resume_from_scan_id=source["scan_id"], max_parallel_reviews=requested, timeout_seconds=1800),
        "root/project", {"revision": source["revision"], "entries": source["inventory"]},
        source["route"], previous=source, at=at,
    )
    assert resumed["max_parallel_reviews"] == expected
    assert len(c.pending(resumed)) == expected
    assert c.progress(resumed)["files_reviewed"] == 1
    assert c.aggregate(resumed)[1]["high"] == 1
    assert resumed["proofs"] == source["proofs"]
    assert resumed["route"] == source["route"] and resumed["revision"] == source["revision"]
    assert resumed["inventory_digest"] == source["inventory_digest"]
    assert resumed["max_batch_text_bytes"] == source["max_batch_text_bytes"]
    assert {key: item["paths"] for key, item in resumed["tasks"].items()} == {
        key: item["paths"] for key, item in source["tasks"].items()
    }
    assert resumed["deadline_at"] == (at + timedelta(seconds=1800)).isoformat()
    assert source == before
