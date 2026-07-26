from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from evander.execution import task_worktree_cleanup as cleanup_module
from evander.execution.task_worktree_cleanup import (
    TaskWorktreeInspection,
    build_libvirt_task_worktree_cleanup_command,
    cleanup_task_worktree,
)


def test_cleanup_revalidates_under_lock_immediately_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inspections = iter(
        [
            TaskWorktreeInspection(
                safe_to_remove=True,
                target_exists=True,
                registered=True,
                actual_branch="agents/session/task",
                actual_commit_sha="a" * 40,
                message="safe",
            ),
            TaskWorktreeInspection(
                safe_to_remove=False,
                target_exists=True,
                registered=True,
                actual_branch="agents/session/task",
                actual_commit_sha="a" * 40,
                failed_check="untracked_files",
                message="work arrived after the first validation",
                untracked_paths=["late.txt"],
            ),
        ]
    )
    monkeypatch.setattr(
        cleanup_module,
        "inspect_task_worktree",
        lambda **_kwargs: next(inspections),
    )

    result = cleanup_task_worktree(
        repo_dir=tmp_path / "repo",
        worktrees_root=tmp_path / "worktrees",
        target_dir=tmp_path / "worktrees" / "task",
        branch="agents/session/task",
        expected_commit_sha="a" * 40,
        lock_path=tmp_path / "locks" / "repo.lock",
    )

    assert result.state == "refused"
    assert result.validation_count == 2
    assert result.failed_check == "untracked_files"
    assert result.untracked_paths == ["late.txt"]


def test_libvirt_cleanup_command_is_self_contained_and_removes_safe_worktree(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Cleanup Test"], cwd=repo, check=True)
    (repo / ".gitignore").write_text("cache/\n", encoding="utf-8")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", ".gitignore", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=repo, check=True)
    commit_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    branch = "agents/session/task"
    worktrees_root = tmp_path / "worktrees"
    target = worktrees_root / "task"
    worktrees_root.mkdir()
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", branch, str(target), commit_sha],
        cwd=repo,
        check=True,
    )
    (target / "cache").mkdir()
    (target / "cache" / "generated.bin").write_bytes(b"generated")
    command = build_libvirt_task_worktree_cleanup_command(
        repo_dir=str(repo),
        worktrees_root=str(worktrees_root),
        target_dir=str(target),
        branch=branch,
        expected_commit_sha=commit_sha,
        lock_path=str(tmp_path / "locks" / "repo.lock"),
    )

    completed = subprocess.run(
        ["bash", "-lc", command],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)

    assert result["state"] == "removed"
    assert result["validation_count"] == 2
    assert result["ignored_paths"] == ["cache/generated.bin"]
    assert result["reclaimed_bytes"] == result["worktree_bytes"]
    assert result["reclaimed_bytes"] >= len(b"generated")
    assert not target.exists()


def test_cleanup_refuses_worktree_with_unexpected_commit(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Cleanup Test"],
        cwd=repo,
        check=True,
    )
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=repo, check=True)
    retained_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    branch = "agents/session/task"
    worktrees_root = tmp_path / "worktrees"
    target = worktrees_root / "task"
    worktrees_root.mkdir()
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", branch, str(target), retained_commit],
        cwd=repo,
        check=True,
    )
    (target / "README.md").write_text("unexpected work\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=target, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "unexpected task commit"],
        cwd=target,
        check=True,
    )

    result = cleanup_task_worktree(
        repo_dir=repo,
        worktrees_root=worktrees_root,
        target_dir=target,
        branch=branch,
        expected_commit_sha=retained_commit,
        lock_path=tmp_path / "locks" / "repo.lock",
    )

    assert result.state == "refused"
    assert result.safe_to_remove is False
    assert result.removed is False
    assert result.failed_check == "retained_commit_mismatch"
    assert "does not retain expected commit" in result.message
    assert target.exists()
