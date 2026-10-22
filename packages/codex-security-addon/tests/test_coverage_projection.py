"""Link reviews retain the exact source entry without dereferencing its target."""

import base64
import json
import os
import subprocess

import pytest

from evander_codex_security_addon.coverage_projection import (
    git_bytes,
    symlink_projection,
    tree_entries,
)
from evander_codex_security_addon import coverage_runner, runner


@pytest.fixture(params=["sha1", "sha256"])
def links(tmp_path, request):
    repo = tmp_path / "repo"
    repo.mkdir()
    git_bytes(repo, "init", "-q", "--object-format=" + request.param)
    (repo / "README.md").write_text("Source stays unchanged.\n")
    (repo / "dir").mkdir()
    (repo / "dir/file.txt").write_text("Supporting source.\n")
    targets = {
        "readme-link": "README.md",
        "directory-link": "dir",
        "broken-link": "missing",
        "external-link": str(tmp_path / "unavailable-private-target"),
        "cycle-a": "cycle-b",
        "cycle-b": "cycle-a",
        "chain-link": "readme-link",
        "dir/space and\nnewline-link": "../README.md",
    }
    for path, target in targets.items():
        (repo / path).symlink_to(target)
    git_bytes(repo, "add", ".")
    git_bytes(
        repo,
        "-c",
        "user.name=Synthetic",
        "-c",
        "user.email=synthetic@example.invalid",
        "commit",
        "-qm",
        "Source",
    )
    revision = git_bytes(repo, "rev-parse", "HEAD").decode().strip()
    return repo, revision, targets


def test_projection_is_exact_reproducible_and_only_changes_assigned_links(links):
    repo, revision, targets = links
    source_tree = tree_entries(repo, revision)
    selected = ["readme-link", "dir/space and\nnewline-link", "external-link"]
    expected = symlink_projection(repo, revision, selected)
    # Host-side verification creates no Git objects, refs or working-file changes.
    assert git_bytes(repo, "status", "--porcelain=v1") == b""
    assert git_bytes(repo, "rev-parse", "HEAD").decode().strip() == revision
    assert (
        subprocess.run(
            ["git", "-C", str(repo), "cat-file", "-e", expected["revision"]], capture_output=True
        ).returncode
        != 0
    )
    built = symlink_projection(repo, revision, list(reversed(selected)), materialize=True)
    assert built == expected
    assert git_bytes(repo, "rev-parse", built["revision"] + "^").decode().strip() == revision
    projected = {x["path"]: x for x in tree_entries(repo, built["revision"])}
    for entry in source_tree:
        path = entry["path"]
        if path not in selected:
            assert projected[path] == entry
            continue
        assert projected[path]["mode"] == "100644"
        descriptor = json.loads(git_bytes(repo, "show", built["revision"] + ":" + path))
        assert descriptor["path"] == path
        assert descriptor["mode"] == "120000"
        assert descriptor["source_revision"] == revision
        assert descriptor["object_id"] == entry["oid"]
        assert descriptor["target_text"] == targets[path]
    assert symlink_projection(repo, revision, ["README.md"]) is None
    assert git_bytes(repo, "status", "--porcelain=v1") == b""


def test_all_link_types_can_be_reviewed_without_resolving_destinations(links):
    repo, revision, targets = links
    projection = symlink_projection(repo, revision, list(targets), materialize=True)
    git_bytes(repo, "checkout", "--detach", projection["revision"])
    for path, target in targets.items():
        assert not (repo / path).is_symlink()
        assert json.loads((repo / path).read_text())["target_text"] == target
    assert git_bytes(repo, "show", revision + ":readme-link") == b"README.md"
    assert git_bytes(repo, "status", "--porcelain=v1") == b""


def test_link_payload_keeps_non_utf8_bytes_exactly(links):
    repo, _, _ = links
    os.symlink(b"\xff-target", os.fsencode(repo / "byte-link"))
    git_bytes(repo, "add", "byte-link")
    git_bytes(
        repo,
        "-c",
        "user.name=Synthetic",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "Byte target",
    )
    revision = git_bytes(repo, "rev-parse", "HEAD").decode().strip()
    projection = symlink_projection(repo, revision, ["byte-link"], materialize=True)
    descriptor = json.loads(git_bytes(repo, "show", projection["revision"] + ":byte-link"))
    assert base64.b64decode(descriptor["target_base64"]) == b"\xff-target"
    assert git_bytes(repo, "show", revision + ":byte-link") == b"\xff-target"


def test_projection_keeps_unassigned_empty_trees_and_gitlinks(links):
    repo, revision, _ = links
    empty = git_bytes(repo, "hash-object", "-w", "-t", "tree", "--stdin", data=b"").strip()
    entries = git_bytes(repo, "ls-tree", "-z", revision)
    entries += b"040000 tree " + empty + b"\tempty\0"
    entries += b"160000 commit " + revision.encode() + b"\tgitlink\0"
    root_tree = git_bytes(repo, "mktree", "-z", data=entries).decode().strip()
    source = (
        git_bytes(
            repo,
            "-c",
            "user.name=Synthetic",
            "-c",
            "user.email=test@example.invalid",
            "commit-tree",
            root_tree,
            "-p",
            revision,
            data=b"Structural entries\n",
        )
        .decode()
        .strip()
    )
    projection = symlink_projection(repo, source, ["readme-link"], materialize=True)
    assert git_bytes(
        repo, "ls-tree", "-z", projection["revision"], "--", "empty", "gitlink"
    ) == git_bytes(repo, "ls-tree", "-z", source, "--", "empty", "gitlink")


@pytest.mark.parametrize("tamper", [None, "after_validation", "dirty", "head"])
def test_link_review_recovery_revalidates_projection_before_granting_credit(
    links, tmp_path, monkeypatch, tamper
):
    repo, revision, _ = links
    root = tmp_path / "review"
    root.mkdir()
    git_bytes(tmp_path, "clone", "--no-local", str(repo), str(root / "checkout"))
    request = {
        "scan_id": "e" * 32,
        "snapshot_commit": revision,
        "coverage_task": {"kind": "files", "paths": ["readme-link"]},
    }
    projection = coverage_runner.prepare_review(root, request)
    monkeypatch.setattr(runner, "scan_directory", lambda _: root)
    runner.write_json(root / "request.json", request)
    saved = {
        "status": "completed",
        "coverage": "complete",
        "coverage_unit": {
            "kind": "files",
            "revision": revision,
            "paths": ["readme-link"],
            "source_projection": projection,
        },
    }
    runner.write_json(root / "sdk-result.json", saved)
    if tamper == "after_validation":
        import shutil

        coverage_runner.validate_snapshot(root, request)
        shutil.rmtree(root / "checkout")
    elif tamper == "dirty":
        (root / "checkout/readme-link").write_text("forged link record")
    elif tamper == "head":
        git_bytes(root / "checkout", "checkout", "--detach", revision)
    result = coverage_runner.recover("e" * 32)
    if tamper in {"dirty", "head"}:
        assert result["status"] == "failed"
        assert "coverage_unit" not in result
    else:
        assert result["status"] == "completed"
        assert result["coverage_unit"]["revision"] == revision
        assert result["coverage_unit"]["source_projection"] == projection
    assert git_bytes(repo, "rev-parse", "HEAD").decode().strip() == revision
