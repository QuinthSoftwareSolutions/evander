import os
import subprocess

import pytest

from evander.execution.git_salvage import capture_git_salvage


def git(path, *args, binary=False):
    result = subprocess.run(
        ["git", "-C", str(path), *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    return result.stdout if binary else result.stdout.decode().strip()


def checkout(tmp_path):
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    work = tmp_path / "work"
    remote.mkdir()
    git(remote, "init", "--bare")
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Example")
    git(repo, "config", "user.email", "example@example.invalid")
    git(repo, "remote", "add", "origin", str(remote))
    (repo / "file").write_bytes(b"original\n")
    (repo / ".gitignore").write_text("ignored\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    git(repo, "worktree", "add", "-b", "agents/session/task", str(work))
    return repo, work, remote


def capture(repo, work):
    return capture_git_salvage(
        repository=repo,
        worktree=work,
        expected_branch="agents/session/task",
        recovery_branch="agents/session/recovery",
    )


def test_salvage_transports_index_binary_untracked_and_symlinks_without_mutating_original(tmp_path):
    repo, work, remote = checkout(tmp_path)
    (work / "file").write_bytes(b"staged\n")
    git(work, "add", "file")
    (work / "file").write_bytes(b"working\x00\xff\n")
    (work / " leading space\n").write_bytes(b"untracked\n")
    (work / "ignored").write_text("retained only in original")
    (work / "outside-link").symlink_to(tmp_path / "private")
    (tmp_path / "private").write_text("outside data must not be read")
    status = git(work, "status", "--porcelain", "-z", binary=True)
    original_index = git(work, "write-tree")
    original_head = git(work, "rev-parse", "HEAD")
    receipt = capture(repo, work)
    assert git(remote, "show", f"{receipt['work_ref']}:file", binary=True) == b"working\x00\xff\n"
    assert git(remote, "show", f"{receipt['index_ref']}:file", binary=True) == b"staged\n"
    assert (
        git(remote, "show", f"{receipt['work_ref']}: leading space\n", binary=True)
        == b"untracked\n"
    )
    assert git(remote, "show", f"{receipt['work_ref']}:outside-link") == str(tmp_path / "private")
    assert "ignored" not in git(remote, "ls-tree", "--name-only", receipt["work_ref"]).splitlines()
    assert receipt["ignored_file_count"] == 1
    assert git(work, "status", "--porcelain", "-z", binary=True) == status
    assert git(work, "write-tree") == original_index
    assert git(work, "rev-parse", "HEAD") == original_head
    assert capture(repo, work) == receipt
    assert (work / "ignored").read_text() == "retained only in original"


def test_salvage_does_not_execute_guest_clean_filters(tmp_path):
    repo, work, remote = checkout(tmp_path)
    marker = tmp_path / "filter-ran"
    (work / ".gitattributes").write_text("file filter=guest\n")
    git(work, "config", "filter.guest.clean", f"touch {marker}")
    (work / "file").write_text("unfiltered work")
    receipt = capture(repo, work)
    assert not marker.exists()
    assert git(remote, "show", f"{receipt['work_ref']}:file") == "unfiltered work"


def test_salvage_rejects_symlink_parent_replacement(tmp_path):
    repo, work, _ = checkout(tmp_path)
    (work / "nested").mkdir()
    (work / "nested" / "file").write_text("tracked")
    git(work, "add", "nested/file")
    (work / "nested" / "file").unlink()
    (work / "nested").rmdir()
    (work / "nested").symlink_to(tmp_path)
    (tmp_path / "file").write_text("outside")
    with pytest.raises((ValueError, OSError)):
        capture(repo, work)
    assert os.readlink(work / "nested") == str(tmp_path)
