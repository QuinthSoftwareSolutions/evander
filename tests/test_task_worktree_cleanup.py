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
    (tmp_path / "repo").mkdir()
    (tmp_path / "worktrees" / "task").mkdir(parents=True)
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
        trusted_root=tmp_path,
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


def test_cleanup_refuses_symlink_swap_before_ignored_file_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "repo").mkdir()
    target = tmp_path / "worktrees" / "task"
    target.mkdir(parents=True)
    victim = tmp_path / "victim"
    victim.mkdir()
    victim_env = victim / ".env"
    victim_env.write_text("keep me\n", encoding="utf-8")
    moved_target = tmp_path / "moved-task"
    inspection = TaskWorktreeInspection(
        safe_to_remove=True,
        target_exists=True,
        registered=True,
        actual_branch="agents/session/task",
        actual_commit_sha="a" * 40,
        message="safe",
        ignored_paths=[".env"],
    )
    inspection_count = 0

    def _inspect(**_kwargs: object) -> TaskWorktreeInspection:
        nonlocal inspection_count
        inspection_count += 1
        if inspection_count == 2:
            target.rename(moved_target)
            target.symlink_to(victim, target_is_directory=True)
        return inspection

    monkeypatch.setattr(cleanup_module, "inspect_task_worktree", _inspect)

    result = cleanup_task_worktree(
        trusted_root=tmp_path,
        repo_dir=tmp_path / "repo",
        worktrees_root=tmp_path / "worktrees",
        target_dir=target,
        branch="agents/session/task",
        expected_commit_sha="a" * 40,
        lock_path=tmp_path / "locks" / "repo.lock",
    )

    assert result.state == "refused"
    assert result.failed_check == "worktree_path_changed"
    assert victim_env.read_text(encoding="utf-8") == "keep me\n"


def test_cleanup_refuses_parent_component_swap_before_destructive_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed = tmp_path / "seed"
    seed.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=seed, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=seed, check=True)
    subprocess.run(["git", "config", "user.name", "Cleanup Test"], cwd=seed, check=True)
    (seed / ".gitignore").write_text(".env\n", encoding="utf-8")
    (seed / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", ".gitignore", "README.md"], cwd=seed, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=seed, check=True)
    commit_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=seed,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    branch = "agents/session/task"

    def _create_session_checkout(session_root: Path) -> tuple[Path, Path]:
        agent_home = session_root / "agent_home"
        repo = agent_home / "repos" / "repo"
        repo.parent.mkdir(parents=True)
        subprocess.run(["git", "clone", "-q", str(seed), str(repo)], check=True)
        target = agent_home / "worktrees" / "repo" / "task"
        target.parent.mkdir(parents=True)
        subprocess.run(
            ["git", "worktree", "add", "-q", "-b", branch, str(target), commit_sha],
            cwd=repo,
            check=True,
        )
        (target / ".env").write_text("keep me\n", encoding="utf-8")
        return repo, target

    source_root = tmp_path / "source"
    victim_root = tmp_path / "victim"
    source_repo, source_target = _create_session_checkout(source_root)
    victim_repo, victim_target = _create_session_checkout(victim_root)
    victim_env = victim_target / ".env"
    moved_repos = tmp_path / "moved-repos"
    moved_worktrees = tmp_path / "moved-worktrees"
    original_open = cleanup_module.os.open
    swapped = False

    def _swap_parent_then_open(
        path: str | bytes | Path,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        if not swapped and dir_fd is None and Path(path) == source_root / "agent_home":
            swapped = True
            source_repo.parent.rename(moved_repos)
            source_repo.parent.symlink_to(victim_repo.parent, target_is_directory=True)
            source_target.parent.rename(moved_worktrees)
            source_target.parent.symlink_to(victim_target.parent, target_is_directory=True)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(cleanup_module.os, "open", _swap_parent_then_open)

    result = cleanup_task_worktree(
        trusted_root=source_root / "agent_home",
        repo_dir=source_repo,
        worktrees_root=source_target.parent,
        target_dir=source_target,
        branch=branch,
        expected_commit_sha=commit_sha,
        lock_path=tmp_path / "locks" / "repo.lock",
    )

    assert swapped is True
    assert result.state == "refused"
    assert result.failed_check == "worktree_path_changed"
    assert victim_env.read_text(encoding="utf-8") == "keep me\n"
    assert victim_target.exists()


@pytest.mark.parametrize("ignored_paths", [[], ["cache/generated.bin"]])
def test_cleanup_refuses_worktree_that_appears_during_final_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ignored_paths: list[str],
) -> None:
    (tmp_path / "repo").mkdir()
    (tmp_path / "worktrees").mkdir()
    inspections = iter(
        [
            TaskWorktreeInspection(
                safe_to_remove=True,
                target_exists=False,
                registered=True,
                actual_branch="agents/session/task",
                actual_commit_sha="a" * 40,
                message="already absent",
            ),
            TaskWorktreeInspection(
                safe_to_remove=True,
                target_exists=True,
                registered=True,
                actual_branch="agents/session/task",
                actual_commit_sha="a" * 40,
                message="safe",
                ignored_paths=ignored_paths,
            ),
        ]
    )
    monkeypatch.setattr(
        cleanup_module,
        "inspect_task_worktree",
        lambda **_kwargs: next(inspections),
    )

    def _unexpected_mutation(*_args: object, **_kwargs: object) -> None:
        pytest.fail("cleanup must not mutate a worktree without a bound descriptor")

    monkeypatch.setattr(cleanup_module, "_run", _unexpected_mutation)

    result = cleanup_task_worktree(
        trusted_root=tmp_path,
        repo_dir=tmp_path / "repo",
        worktrees_root=tmp_path / "worktrees",
        target_dir=tmp_path / "worktrees" / "task",
        branch="agents/session/task",
        expected_commit_sha="a" * 40,
        lock_path=tmp_path / "locks" / "repo.lock",
    )

    assert result.state == "refused"
    assert result.safe_to_remove is False
    assert result.removed is False
    assert result.validation_count == 2
    assert result.failed_check == "worktree_path_changed"
    assert result.message == "task worktree appeared during final validation"
    assert result.ignored_paths == ignored_paths


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
        trusted_root=str(tmp_path),
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


def test_cleanup_preserves_tracked_work_created_during_final_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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

    original_run = cleanup_module._run
    injected_late_work = False

    def _run_with_late_work(
        args: list[str],
        *,
        check: bool = True,
        timeout_seconds: float | None = None,
        pass_fds: tuple[int, ...] = (),
    ) -> subprocess.CompletedProcess[str]:
        nonlocal injected_late_work
        if (
            not injected_late_work
            and len(args) >= 5
            and args[0] == "git"
            and args[3:5] == ["worktree", "remove"]
        ):
            assert "--force" not in args
            (target / "README.md").write_text("late agent work\n", encoding="utf-8")
            injected_late_work = True
        return original_run(
            args,
            check=check,
            timeout_seconds=timeout_seconds,
            pass_fds=pass_fds,
        )

    monkeypatch.setattr(cleanup_module, "_run", _run_with_late_work)

    result = cleanup_task_worktree(
        trusted_root=tmp_path,
        repo_dir=repo,
        worktrees_root=worktrees_root,
        target_dir=target,
        branch=branch,
        expected_commit_sha=commit_sha,
        lock_path=tmp_path / "locks" / "repo.lock",
    )

    assert injected_late_work is True
    assert result.state == "refused"
    assert result.removed is False
    assert result.validation_count == 3
    assert result.failed_check == "tracked_changes"
    assert result.tracked_paths == ["README.md"]
    assert (target / "README.md").read_text(encoding="utf-8") == "late agent work\n"
    assert not (target / "cache" / "generated.bin").exists()


def test_ignored_cleanup_batches_paths_and_bounds_each_git_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], bool, float | None]] = []

    def _record_run(
        args: list[str],
        *,
        check: bool = True,
        timeout: float | None = None,
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((args, check, timeout))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(cleanup_module.subprocess, "run", _record_run)
    monkeypatch.setattr(cleanup_module, "_IGNORED_PATH_CLEAN_BATCH_COUNT", 2)

    directory_fd = cleanup_module.os.open(tmp_path, cleanup_module.os.O_RDONLY)
    try:
        error = cleanup_module._remove_proven_ignored_paths(
            directory_fd,
            ["cache/a.bin", "cache/b.bin", "cache/c.bin"],
        )
    finally:
        cleanup_module.os.close(directory_fd)

    assert error is None
    assert [call[0][-2:] for call in calls] == [
        ["cache/a.bin", "cache/b.bin"],
        ["--", "cache/c.bin"],
    ]
    assert all(call[1] is False for call in calls)
    assert all(call[2] == cleanup_module._IGNORED_PATH_CLEAN_TIMEOUT_SECONDS for call in calls)


def test_ignored_cleanup_reports_bounded_git_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _time_out(
        args: list[str],
        *,
        check: bool = True,
        timeout: float | None = None,
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(args, timeout or 0)

    monkeypatch.setattr(cleanup_module.subprocess, "run", _time_out)

    directory_fd = cleanup_module.os.open(tmp_path, cleanup_module.os.O_RDONLY)
    try:
        error = cleanup_module._remove_proven_ignored_paths(
            directory_fd,
            ["cache/generated.bin"],
        )
    finally:
        cleanup_module.os.close(directory_fd)

    assert error == "timed out after 60 seconds while removing 1 proven ignored path(s)"


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
        trusted_root=tmp_path,
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
