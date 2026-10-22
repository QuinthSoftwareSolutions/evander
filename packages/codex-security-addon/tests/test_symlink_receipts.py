"""A native link receipt must bind to the host's exact source projection."""

from copy import deepcopy
from datetime import timedelta
import json
import tarfile
from types import SimpleNamespace

import pytest

from evander.security_scans import SecurityScanHandle
from evander_codex_security_addon import coverage as c
from evander_codex_security_addon.coverage_projection import (
    git_bytes,
    symlink_projection,
    tree_entries,
)

from test_coverage import request as scan_request, receipt
from test_coverage_controller import native_archive, running  # noqa: F401
from test_coverage_projection import links  # noqa: F401


@pytest.fixture(params=[("readme-link",), ("README.md", "readme-link")], ids=["link", "mixed"])
def review(running, links, request):  # noqa: F811
    controller, _, _, _ = running
    repo, revision, _ = links
    entries = [
        {**x, "kind": "symlink" if x["mode"] == "120000" else "text"}
        for x in tree_entries(repo, revision)
    ]
    state = c.make_state(
        "b" * 32,
        scan_request().model_copy(update={"managed_repo_id": "managed-links"}),
        "root/project",
        {"revision": revision, "entries": entries},
        {"model": "example"},
    )
    # Real Git objects stand in for the already authorized managed bare repo.
    controller.runtime.admin_service = SimpleNamespace(
        managed_git_admin=SimpleNamespace(resolve_internal_repository=lambda *args: (repo, None))
    )
    state["architecture"] = {"paths": list(state["files"])}
    item = c.task("files", request.param)
    remaining = c.task("files", set(state["files"]) - set(item["paths"]))
    state["tasks"] = {scope["id"]: scope for scope in (item, remaining)}
    attempt = c.begin(state, item)
    projection = symlink_projection(repo, revision, item["paths"])
    unit = receipt(state, item, source_projection=projection)
    child = SecurityScanHandle(
        execution_id="scan-" + attempt["scan_id"], metadata={"scan_id": attempt["scan_id"]}
    )
    return controller, state, item, attempt, unit, child, repo


def test_accepted_link_receipt_survives_restart_resume_and_publication(review):
    controller, state, item, attempt, unit, child, repo = review
    native_archive(
        controller.artifacts,
        attempt["scan_id"],
        unit,
        native_coverage={
            "surfaces": [
                {
                    "label": "file:readme-link",
                    "disposition": "no_issue_found",
                    "notes": "The stored target is README.md; the link is used only as documentation.",
                }
            ]
        },
    )
    proof = controller.proof(
        {"status": "completed", "coverage": "complete"}, child, unit, state=state, item=item
    )
    assert c.accept(state, item, attempt["scan_id"], unit, proof)
    assert state["files"]["readme-link"]["disposition"] == "reviewed"
    assert state["files"]["README.md"]["disposition"] == (
        "reviewed" if "README.md" in item["paths"] else "pending"
    )
    assert state["files"]["dir/file.txt"]["disposition"] == "pending"
    assert c.progress(state)["files_reviewed"] == len(item["paths"])
    findings, _, _ = c.aggregate(state)
    assert findings[0]["sources"][0]["source_projection"] == {
        k: unit["source_projection"][k] for k in ("source_revision", "revision", "paths")
    }
    attempt["cleanup_confirmed"] = True
    state.update(
        finished_at=c.now().isoformat(),
        cleanup_confirmed=True,
        phase="partial",
        stop_reason="Controlled interruption",
    )
    controller.store.create(state)
    saved = controller.store.get(state["scan_id"], "root/project")
    controller.verify_proofs(saved)
    controller.publish(saved)
    with tarfile.open(controller.artifacts.get(saved["archive_key"]).blob_path) as archive:
        prefix = f"security-scans/{state['scan_id']}/reports/aggregate/"
        manifest = json.load(archive.extractfile(prefix + "scan-manifest.json"))
        assert manifest["revision"] == state["revision"]
        assert manifest["sources"][0]["source_projection"] == unit["source_projection"]
        native = json.load(
            archive.extractfile(
                prefix
                + "sources/"
                + proof["archive_key"]
                + "/"
                + proof["report_prefix"]
                + "scan-manifest.json"
            )
        )
        assert native["scan"]["target"]["revision"] == unit["source_projection"]["revision"]
        assert "derived Git snapshots" in archive.extractfile(prefix + "report.md").read().decode()
    before = deepcopy(saved)
    (repo / "README.md").write_text("Later branch revision\n")
    git_bytes(
        repo,
        "-c",
        "user.name=Synthetic",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qam",
        "Advance branch",
    )
    resumed = c.make_state(
        "c" * 32,
        scan_request().model_copy(update={"managed_repo_id": saved["managed_repo_id"]}),
        "root/project",
        {"revision": saved["revision"], "entries": saved["inventory"]},
        saved["route"],
        previous=saved,
        at=c.now() + timedelta(seconds=1),
    )
    assert resumed["files"]["readme-link"] == saved["files"]["readme-link"]
    assert resumed["proofs"] == saved["proofs"]
    assert resumed["revision"] != git_bytes(repo, "rev-parse", "HEAD").decode().strip()
    assert saved == before


@pytest.mark.parametrize(
    "failure",
    [
        "wrong_revision",
        "destination_scope",
        "forged_projection",
        "missing_projection",
        "wrong_source_scope",
    ],
)
def test_mismatched_native_or_wrapper_projection_cannot_earn_credit(review, failure):
    controller, state, item, attempt, unit, child, _ = review
    native_archive(
        controller.artifacts,
        attempt["scan_id"],
        unit,
        bad_revision=failure == "wrong_revision",
        native_paths=["README.md"] if failure == "destination_scope" else None,
    )
    if failure == "forged_projection":
        unit["source_projection"]["descriptors"][0]["target_text"] = "/forged-target"
    elif failure == "missing_projection":
        unit.pop("source_projection")
    elif failure == "wrong_source_scope":
        unit["paths"] = ["README.md"]
    with pytest.raises(ValueError):
        controller.proof(
            {"status": "completed", "coverage": "complete"}, child, unit, state=state, item=item
        )
    assert c.progress(state)["files_reviewed"] == 0


@pytest.mark.parametrize(
    "surface",
    [
        None,
        {
            "label": "file:README.md",
            "disposition": "no_issue_found",
            "notes": "Destination reviewed",
        },
        {
            "label": "file:readme-link",
            "disposition": "needs_follow_up",
            "notes": "Usage unresolved",
        },
    ],
)
def test_native_destination_review_or_unresolved_link_does_not_close_link(review, surface):
    controller, state, item, attempt, unit, child, _ = review
    # A wrapper-supplied disposition is deliberately dishonest. Only the sealed
    # native surface is allowed to grant credit.
    unit["dispositions"] = {
        "readme-link": {"disposition": "reviewed", "reason": "unsealed assertion"}
    }
    native_archive(
        controller.artifacts,
        attempt["scan_id"],
        unit,
        native_coverage={"surfaces": [surface] if surface else []},
    )
    proof = controller.proof(
        {"status": "completed", "coverage": "complete"}, child, unit, state=state, item=item
    )
    c.accept(state, item, attempt["scan_id"], unit, proof)
    assert state["files"]["readme-link"]["disposition"] == "pending"
    assert state["files"]["README.md"]["disposition"] == "pending"
    assert c.progress(state)["files_reviewed"] == 0
