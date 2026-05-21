from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from agent_sandbox.git_mirror import ContainerGitMirrorResolver, _safe_worker_repo_name


ROOT = Path(__file__).resolve().parents[1]


def _run(cmd: list[str], *, cwd: Path) -> None:
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True)


def _write_commit(repo: Path, filename: str, content: str, message: str) -> None:
    (repo / filename).write_text(content, encoding="utf-8")
    _run(["git", "add", filename], cwd=repo)
    _run(
        [
            "git",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "user.name=Test",
            "commit",
            "-m",
            message,
        ],
        cwd=repo,
    )


def _source_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    _run(["git", "init"], cwd=path)
    _run(["git", "checkout", "-b", "main"], cwd=path)
    _write_commit(path, "README.md", "hello\n", "init")
    return path


def _resolver(tmp_path: Path) -> ContainerGitMirrorResolver:
    return ContainerGitMirrorResolver(
        project_dir=ROOT,
        git_root=tmp_path / "git-remotes",
        git_host="10.0.0.10",
        git_user="gitbot",
        git_ssh_alias="agent-sandbox-git-host",
    )


def _has_ref(bare_repo: Path, ref: str) -> bool:
    completed = subprocess.run(
        ["git", f"--git-dir={bare_repo}", "show-ref", "--verify", "--quiet", ref],
        cwd=ROOT,
        check=False,
    )
    return completed.returncode == 0


def test_container_git_mirror_resolves_remote_url_to_private_ssh_mirror(
    tmp_path: Path,
) -> None:
    source = _source_repo(tmp_path / "source")
    _run(["git", "checkout", "-b", "feature/test"], cwd=source)
    _write_commit(source, "feature.txt", "feature\n", "feature")
    remote = tmp_path / "remote.git"
    _run(["git", "clone", "--bare", str(source), str(remote)], cwd=tmp_path)
    remote_url = f"file://{remote}"

    mirror = _resolver(tmp_path).resolve(
        repo_source=remote_url,
        clone_branch="feature/test",
        directory_path="root/team",
        session_id="session-1",
    )

    assert mirror.canonical_repo_source == remote_url
    assert mirror.worker_repo_source.startswith("ssh://agent-sandbox-git-host")
    assert mirror.worker_repo_source.endswith(f"{mirror.mirror_name}.git")
    assert mirror.mirror_path.is_dir()
    trusted_repo_path = subprocess.run(
        ["git", f"--git-dir={mirror.mirror_path}", "config", "--get", "agent-sandbox.trustedRepoPath"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert trusted_repo_path == mirror.resolved_repo_path
    assert _has_ref(mirror.mirror_path, "refs/heads/main")
    assert _has_ref(mirror.mirror_path, "refs/heads/feature/test")


def test_container_git_mirror_uses_hash_to_avoid_same_basename_collisions(
    tmp_path: Path,
) -> None:
    first_source = _source_repo(tmp_path / "one" / "repo-worktree")
    second_source = _source_repo(tmp_path / "two" / "repo-worktree")
    first = tmp_path / "one" / "repo.git"
    second = tmp_path / "two" / "repo.git"
    _run(["git", "clone", "--bare", str(first_source), str(first)], cwd=tmp_path)
    _run(["git", "clone", "--bare", str(second_source), str(second)], cwd=tmp_path)
    resolver = _resolver(tmp_path)

    first_mirror = resolver.resolve(repo_source=f"file://{first}", clone_branch="main")
    second_mirror = resolver.resolve(repo_source=f"file://{second}", clone_branch="main")

    assert first_mirror.repo_name == "repo"
    assert second_mirror.repo_name == "repo"
    assert first_mirror.mirror_name != second_mirror.mirror_name
    assert first_mirror.mirror_path != second_mirror.mirror_path


def test_container_git_mirror_sanitizes_worker_repo_name(tmp_path: Path) -> None:
    source = _source_repo(tmp_path / "example_backup")

    mirror = _resolver(tmp_path).resolve(repo_source=str(source), clone_branch="main")

    assert mirror.repo_name == "example_backup"
    assert mirror.worker_repo_source.startswith("ssh://agent-sandbox-git-host")


def test_safe_worker_repo_name_decodes_url_escaped_segments() -> None:
    assert _safe_worker_repo_name("OT%20Backup") == "example_backup"


def test_container_git_mirror_missing_branch_fails_before_worker_clone(
    tmp_path: Path,
) -> None:
    source = _source_repo(tmp_path / "repo")

    with pytest.raises(RuntimeError, match="clone_branch 'missing' is not present"):
        _resolver(tmp_path).resolve(repo_source=str(source), clone_branch="missing")


def test_container_git_mirror_prunes_deleted_branch_before_clone_branch_validation(
    tmp_path: Path,
) -> None:
    source = _source_repo(tmp_path / "repo")
    _run(["git", "checkout", "-b", "feature/stale"], cwd=source)
    _write_commit(source, "feature.txt", "feature\n", "feature")
    resolver = _resolver(tmp_path)

    mirror = resolver.resolve(repo_source=str(source), clone_branch="feature/stale")

    assert _has_ref(mirror.mirror_path, "refs/heads/feature/stale")

    _run(["git", "checkout", "main"], cwd=source)
    _run(["git", "branch", "-D", "feature/stale"], cwd=source)

    with pytest.raises(RuntimeError, match="clone_branch 'feature/stale' is not present"):
        resolver.resolve(repo_source=str(source), clone_branch="feature/stale")

    assert not _has_ref(mirror.mirror_path, "refs/heads/feature/stale")


def test_container_git_mirror_waits_for_upload_pack_sync_lock(tmp_path: Path) -> None:
    source = _source_repo(tmp_path / "repo")
    resolver = _resolver(tmp_path)
    mirror = resolver.resolve(repo_source=str(source), clone_branch="main")
    lock_path = mirror.mirror_path / "agent-sandbox-sync.lock"
    ready_path = tmp_path / "lock-ready"
    release_path = tmp_path / "lock-release"
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import fcntl,pathlib,sys,time\n"
                "lock_path=pathlib.Path(sys.argv[1])\n"
                "ready_path=pathlib.Path(sys.argv[2])\n"
                "release_path=pathlib.Path(sys.argv[3])\n"
                "with lock_path.open('a+', encoding='utf-8') as handle:\n"
                "    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)\n"
                "    ready_path.write_text('ready', encoding='utf-8')\n"
                "    while not release_path.exists():\n"
                "        time.sleep(0.05)\n"
            ),
            str(lock_path),
            str(ready_path),
            str(release_path),
        ],
        text=True,
    )
    try:
        for _ in range(100):
            if ready_path.exists():
                break
            holder.poll()
            assert holder.returncode is None
            time.sleep(0.05)
        assert ready_path.exists()

        done = threading.Event()
        errors: list[BaseException] = []

        def resolve_again() -> None:
            try:
                resolver.resolve(repo_source=str(source), clone_branch="main")
            except BaseException as exc:  # pragma: no cover - surfaced after join
                errors.append(exc)
            finally:
                done.set()

        thread = threading.Thread(target=resolve_again)
        thread.start()
        assert not done.wait(0.25)
        release_path.write_text("release", encoding="utf-8")
        thread.join(timeout=5)
        assert done.is_set()
        assert errors == []
    finally:
        release_path.write_text("release", encoding="utf-8")
        try:
            holder.wait(timeout=5)
        except subprocess.TimeoutExpired:
            holder.terminate()
            holder.wait(timeout=5)


def test_container_git_mirror_refuses_stale_local_branch_before_mirroring(
    tmp_path: Path,
) -> None:
    upstream_worktree = _source_repo(tmp_path / "upstream-worktree")
    upstream = tmp_path / "upstream.git"
    _run(["git", "clone", "--bare", str(upstream_worktree), str(upstream)], cwd=tmp_path)
    source = tmp_path / "source"
    _run(["git", "clone", str(upstream), str(source)], cwd=tmp_path)

    _write_commit(upstream_worktree, "README.md", "fresh\n", "fresh")
    _run(["git", "push", str(upstream), "main"], cwd=upstream_worktree)

    with pytest.raises(
        RuntimeError,
        match="Refusing to mirror stale local refs.*local main is behind origin/main",
    ):
        _resolver(tmp_path).resolve(repo_source=str(source), clone_branch="main")

    mirror_paths = list((tmp_path / "git-remotes").glob("*.git"))
    assert len(mirror_paths) == 1
    assert not _has_ref(mirror_paths[0], "refs/heads/main")


def test_container_git_mirror_upstream_resolution_failure_fails_fast(
    tmp_path: Path,
) -> None:
    with pytest.raises(RuntimeError, match="Missing repo path"):
        _resolver(tmp_path).resolve(repo_source=str(tmp_path / "does-not-exist"))
