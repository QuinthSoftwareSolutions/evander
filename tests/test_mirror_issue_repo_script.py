from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _git(
    repo: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )


def _git_dir(git_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", f"--git-dir={git_dir}", *args],
        check=True,
        text=True,
        capture_output=True,
    )


def test_mirror_issue_repo_excludes_stale_agent_and_review_refs(tmp_path: Path) -> None:
    sandbox_root = tmp_path / "sandbox"
    git_root = sandbox_root / "git-remotes"
    scripts_dir = sandbox_root / "scripts"
    lib_dir = scripts_dir / "lib"
    git_root.mkdir(parents=True)
    scripts_dir.mkdir(parents=True)
    lib_dir.mkdir(parents=True, exist_ok=True)

    repo_root = Path(__file__).resolve().parents[1]
    for name in ("mirror_issue_repo.sh", "create_git_remote.sh", "resolve_repo_source.sh"):
        source = repo_root / "scripts" / name
        target = scripts_dir / name
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        os.chmod(target, 0o755)
    runtime_config_source = repo_root / "scripts" / "lib" / "runtime_config.sh"
    (lib_dir / "runtime_config.sh").write_text(
        runtime_config_source.read_text(encoding="utf-8"), encoding="utf-8"
    )
    os.chmod(lib_dir / "runtime_config.sh", 0o755)
    auth_helper_source = repo_root / "scripts" / "lib" / "git_provider_auth.sh"
    (lib_dir / "git_provider_auth.sh").write_text(
        auth_helper_source.read_text(encoding="utf-8"), encoding="utf-8"
    )
    os.chmod(lib_dir / "git_provider_auth.sh", 0o755)
    script = scripts_dir / "mirror_issue_repo.sh"

    source_repo = tmp_path / "source"
    source_repo.mkdir()
    _git(source_repo, "init", "-b", "main")

    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "Test User",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test User",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "AGENT_SANDBOX_GIT_ROOT": str(git_root),
            "USER": os.environ.get("USER", "test-user"),
        }
    )

    (source_repo / "README.md").write_text("main\n", encoding="utf-8")
    _git(source_repo, "add", "README.md", env=env)
    _git(source_repo, "commit", "-m", "Initial commit", env=env)
    _git(source_repo, "branch", "feature/keep")
    _git(source_repo, "branch", "review/stale-session/main-vm-14357")
    _git(source_repo, "branch", "agents/stale-session/main-vm-14357")
    _git(source_repo, "tag", "v1.0.0")

    subprocess.run(
        [str(script), "--no-sync-remote", "14357", str(source_repo)],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )

    bare_repo = git_root / "14357_repo.git"
    main_sha = _git_dir(bare_repo, "rev-parse", "refs/heads/main").stdout.strip()
    _git_dir(
        bare_repo,
        "update-ref",
        "refs/heads/agents/old-session/main-vm-14357",
        main_sha,
    )
    _git_dir(
        bare_repo,
        "update-ref",
        "refs/heads/review/old-session/main-vm-14357",
        main_sha,
    )

    subprocess.run(
        [str(script), "--no-sync-remote", "14357", str(source_repo)],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )

    refs_output = _git_dir(
        bare_repo, "for-each-ref", "--format=%(refname)", "refs"
    ).stdout.splitlines()

    assert "refs/heads/main" in refs_output
    assert "refs/heads/feature/keep" in refs_output
    assert "refs/tags/v1.0.0" in refs_output
    assert "refs/heads/review/stale-session/main-vm-14357" not in refs_output
    assert "refs/heads/agents/stale-session/main-vm-14357" not in refs_output
    assert "refs/heads/review/old-session/main-vm-14357" not in refs_output
    assert "refs/heads/agents/old-session/main-vm-14357" not in refs_output


def test_mirror_issue_repo_persists_refresh_metadata(tmp_path: Path) -> None:
    sandbox_root = tmp_path / "sandbox"
    git_root = sandbox_root / "git-remotes"
    scripts_dir = sandbox_root / "scripts"
    lib_dir = scripts_dir / "lib"
    git_root.mkdir(parents=True)
    scripts_dir.mkdir(parents=True)
    lib_dir.mkdir(parents=True, exist_ok=True)

    repo_root = Path(__file__).resolve().parents[1]
    for name in ("mirror_issue_repo.sh", "create_git_remote.sh", "resolve_repo_source.sh"):
        source = repo_root / "scripts" / name
        target = scripts_dir / name
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        os.chmod(target, 0o755)
    runtime_config_source = repo_root / "scripts" / "lib" / "runtime_config.sh"
    (lib_dir / "runtime_config.sh").write_text(
        runtime_config_source.read_text(encoding="utf-8"), encoding="utf-8"
    )
    os.chmod(lib_dir / "runtime_config.sh", 0o755)
    auth_helper_source = repo_root / "scripts" / "lib" / "git_provider_auth.sh"
    (lib_dir / "git_provider_auth.sh").write_text(
        auth_helper_source.read_text(encoding="utf-8"), encoding="utf-8"
    )
    os.chmod(lib_dir / "git_provider_auth.sh", 0o755)
    script = scripts_dir / "mirror_issue_repo.sh"

    source_repo = tmp_path / "source"
    source_repo.mkdir()
    _git(source_repo, "init", "-b", "main")
    origin_bare = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", str(origin_bare)], check=True, text=True, capture_output=True
    )
    _git(source_repo, "remote", "add", "origin", str(origin_bare))

    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "Test User",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test User",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "AGENT_SANDBOX_GIT_ROOT": str(git_root),
            "USER": os.environ.get("USER", "test-user"),
        }
    )

    (source_repo / "README.md").write_text("main\n", encoding="utf-8")
    _git(source_repo, "add", "README.md", env=env)
    _git(source_repo, "commit", "-m", "Initial commit", env=env)
    _git(source_repo, "push", "-u", "origin", "main", env=env)

    subprocess.run(
        [
            str(script),
            "--branch",
            "main",
            "14357",
            str(source_repo),
        ],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )

    bare_repo = git_root / "14357_repo.git"
    assert _git_dir(
        bare_repo, "config", "--get", "agent-sandbox.mirrorSource"
    ).stdout.strip() == str(source_repo)
    assert (
        _git_dir(bare_repo, "config", "--get", "agent-sandbox.mirrorRemoteName").stdout.strip()
        == "origin"
    )
    assert (
        _git_dir(bare_repo, "config", "--get", "agent-sandbox.mirrorSyncRemote").stdout.strip()
        == "1"
    )
    assert (
        _git_dir(bare_repo, "config", "--get", "agent-sandbox.mirrorForceLocal").stdout.strip()
        == "0"
    )
    assert (
        _git_dir(bare_repo, "config", "--get-all", "agent-sandbox.mirrorRefspec").stdout.strip()
        == "refs/heads/main:refs/heads/main"
    )


def test_mirror_issue_repo_fast_forwards_stale_local_default_branch(tmp_path: Path) -> None:
    sandbox_root = tmp_path / "sandbox"
    git_root = sandbox_root / "git-remotes"
    scripts_dir = sandbox_root / "scripts"
    lib_dir = scripts_dir / "lib"
    git_root.mkdir(parents=True)
    scripts_dir.mkdir(parents=True)
    lib_dir.mkdir(parents=True, exist_ok=True)

    repo_root = Path(__file__).resolve().parents[1]
    for name in ("mirror_issue_repo.sh", "create_git_remote.sh", "resolve_repo_source.sh"):
        source = repo_root / "scripts" / name
        target = scripts_dir / name
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        os.chmod(target, 0o755)
    runtime_config_source = repo_root / "scripts" / "lib" / "runtime_config.sh"
    (lib_dir / "runtime_config.sh").write_text(
        runtime_config_source.read_text(encoding="utf-8"), encoding="utf-8"
    )
    os.chmod(lib_dir / "runtime_config.sh", 0o755)
    auth_helper_source = repo_root / "scripts" / "lib" / "git_provider_auth.sh"
    (lib_dir / "git_provider_auth.sh").write_text(
        auth_helper_source.read_text(encoding="utf-8"), encoding="utf-8"
    )
    os.chmod(lib_dir / "git_provider_auth.sh", 0o755)
    script = scripts_dir / "mirror_issue_repo.sh"

    source_repo = tmp_path / "source"
    source_repo.mkdir()
    _git(source_repo, "init", "-b", "main")
    origin_bare = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", str(origin_bare)], check=True, text=True, capture_output=True
    )
    _git(source_repo, "remote", "add", "origin", str(origin_bare))

    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "Test User",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test User",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "AGENT_SANDBOX_GIT_ROOT": str(git_root),
            "USER": os.environ.get("USER", "test-user"),
        }
    )

    (source_repo / "README.md").write_text("v1\n", encoding="utf-8")
    _git(source_repo, "add", "README.md", env=env)
    _git(source_repo, "commit", "-m", "Initial commit", env=env)
    _git(source_repo, "push", "-u", "origin", "main", env=env)
    _git_dir(origin_bare, "symbolic-ref", "HEAD", "refs/heads/main")

    upstream_worktree = tmp_path / "upstream-worktree"
    subprocess.run(
        ["git", "clone", str(origin_bare), str(upstream_worktree)],
        check=True,
        text=True,
        capture_output=True,
    )
    (upstream_worktree / "README.md").write_text("v2\n", encoding="utf-8")
    _git(upstream_worktree, "add", "README.md", env=env)
    _git(upstream_worktree, "commit", "-m", "Remote update", env=env)
    _git(upstream_worktree, "push", "origin", "main", env=env)
    updated_sha = _git(upstream_worktree, "rev-parse", "HEAD").stdout.strip()

    subprocess.run(
        [str(script), "14357", str(source_repo)],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )

    bare_repo = git_root / "14357_repo.git"
    assert _git(source_repo, "rev-parse", "main").stdout.strip() == updated_sha
    assert (source_repo / "README.md").read_text(encoding="utf-8") == "v2\n"
    assert _git_dir(bare_repo, "rev-parse", "refs/heads/main").stdout.strip() == updated_sha


def test_mirror_issue_repo_records_upstream_origin_for_repo_cache_worktree(tmp_path: Path) -> None:
    sandbox_root = tmp_path / "sandbox"
    git_root = sandbox_root / "git-remotes"
    scripts_dir = sandbox_root / "scripts"
    lib_dir = scripts_dir / "lib"
    runtime_repo_cache = sandbox_root / "runtime" / "repo-cache"
    git_root.mkdir(parents=True)
    scripts_dir.mkdir(parents=True)
    lib_dir.mkdir(parents=True, exist_ok=True)
    runtime_repo_cache.mkdir(parents=True, exist_ok=True)

    repo_root = Path(__file__).resolve().parents[1]
    for name in ("mirror_issue_repo.sh", "create_git_remote.sh", "resolve_repo_source.sh"):
        source = repo_root / "scripts" / name
        target = scripts_dir / name
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        os.chmod(target, 0o755)
    runtime_config_source = repo_root / "scripts" / "lib" / "runtime_config.sh"
    (lib_dir / "runtime_config.sh").write_text(
        runtime_config_source.read_text(encoding="utf-8"), encoding="utf-8"
    )
    os.chmod(lib_dir / "runtime_config.sh", 0o755)
    auth_helper_source = repo_root / "scripts" / "lib" / "git_provider_auth.sh"
    (lib_dir / "git_provider_auth.sh").write_text(
        auth_helper_source.read_text(encoding="utf-8"), encoding="utf-8"
    )
    os.chmod(lib_dir / "git_provider_auth.sh", 0o755)
    script = scripts_dir / "mirror_issue_repo.sh"

    source_repo = tmp_path / "source"
    source_repo.mkdir()
    _git(source_repo, "init", "-b", "main")
    canonical_origin = tmp_path / "canonical-origin.git"
    subprocess.run(
        ["git", "init", "--bare", str(canonical_origin)], check=True, text=True, capture_output=True
    )
    _git(source_repo, "remote", "add", "origin", str(canonical_origin))

    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "Test User",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test User",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "AGENT_SANDBOX_GIT_ROOT": str(git_root),
            "USER": os.environ.get("USER", "test-user"),
        }
    )

    (source_repo / "README.md").write_text("main\n", encoding="utf-8")
    _git(source_repo, "add", "README.md", env=env)
    _git(source_repo, "commit", "-m", "Initial commit", env=env)
    _git(source_repo, "push", "-u", "origin", "main", env=env)

    cached_mirror = runtime_repo_cache / "cached.git"
    cached_worktree = runtime_repo_cache / "cached-worktree"
    subprocess.run(
        ["git", "clone", "--mirror", str(canonical_origin), str(cached_mirror)],
        check=True,
        text=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "clone", str(cached_mirror), str(cached_worktree)],
        check=True,
        text=True,
        capture_output=True,
    )

    subprocess.run(
        [str(script), "14357", str(cached_worktree)],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )

    bare_repo = git_root / "14357_repo.git"
    assert _git_dir(
        bare_repo, "config", "--get", "agent-sandbox.canonicalOriginUrl"
    ).stdout.strip() == str(canonical_origin)


def test_mirror_issue_repo_allows_no_sync_remote_opt_out_for_local_source(tmp_path: Path) -> None:
    sandbox_root = tmp_path / "sandbox"
    git_root = sandbox_root / "git-remotes"
    scripts_dir = sandbox_root / "scripts"
    lib_dir = scripts_dir / "lib"
    git_root.mkdir(parents=True)
    scripts_dir.mkdir(parents=True)
    lib_dir.mkdir(parents=True, exist_ok=True)

    repo_root = Path(__file__).resolve().parents[1]
    for name in ("mirror_issue_repo.sh", "create_git_remote.sh", "resolve_repo_source.sh"):
        source = repo_root / "scripts" / name
        target = scripts_dir / name
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        os.chmod(target, 0o755)
    runtime_config_source = repo_root / "scripts" / "lib" / "runtime_config.sh"
    (lib_dir / "runtime_config.sh").write_text(
        runtime_config_source.read_text(encoding="utf-8"), encoding="utf-8"
    )
    os.chmod(lib_dir / "runtime_config.sh", 0o755)
    auth_helper_source = repo_root / "scripts" / "lib" / "git_provider_auth.sh"
    (lib_dir / "git_provider_auth.sh").write_text(
        auth_helper_source.read_text(encoding="utf-8"), encoding="utf-8"
    )
    os.chmod(lib_dir / "git_provider_auth.sh", 0o755)
    script = scripts_dir / "mirror_issue_repo.sh"

    source_repo = tmp_path / "source"
    source_repo.mkdir()
    _git(source_repo, "init", "-b", "main")
    origin_bare = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", str(origin_bare)], check=True, text=True, capture_output=True
    )
    _git(source_repo, "remote", "add", "origin", str(origin_bare))

    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "Test User",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test User",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "AGENT_SANDBOX_GIT_ROOT": str(git_root),
            "USER": os.environ.get("USER", "test-user"),
        }
    )

    (source_repo / "README.md").write_text("main\n", encoding="utf-8")
    _git(source_repo, "add", "README.md", env=env)
    _git(source_repo, "commit", "-m", "Initial commit", env=env)

    subprocess.run(
        [str(script), "--no-sync-remote", "14357", str(source_repo)],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )

    bare_repo = git_root / "14357_repo.git"
    assert (
        _git_dir(bare_repo, "config", "--get", "agent-sandbox.mirrorSyncRemote").stdout.strip()
        == "0"
    )


def test_mirror_issue_repo_uses_remote_tracking_branches_when_repo_cache_has_no_local_heads(
    tmp_path: Path,
) -> None:
    sandbox_root = tmp_path / "sandbox"
    git_root = sandbox_root / "git-remotes"
    scripts_dir = sandbox_root / "scripts"
    lib_dir = scripts_dir / "lib"
    runtime_repo_cache = sandbox_root / "runtime" / "repo-cache"
    git_root.mkdir(parents=True)
    scripts_dir.mkdir(parents=True)
    lib_dir.mkdir(parents=True, exist_ok=True)
    runtime_repo_cache.mkdir(parents=True, exist_ok=True)

    repo_root = Path(__file__).resolve().parents[1]
    for name in ("mirror_issue_repo.sh", "create_git_remote.sh", "resolve_repo_source.sh"):
        source = repo_root / "scripts" / name
        target = scripts_dir / name
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        os.chmod(target, 0o755)
    runtime_config_source = repo_root / "scripts" / "lib" / "runtime_config.sh"
    (lib_dir / "runtime_config.sh").write_text(
        runtime_config_source.read_text(encoding="utf-8"), encoding="utf-8"
    )
    os.chmod(lib_dir / "runtime_config.sh", 0o755)
    auth_helper_source = repo_root / "scripts" / "lib" / "git_provider_auth.sh"
    (lib_dir / "git_provider_auth.sh").write_text(
        auth_helper_source.read_text(encoding="utf-8"), encoding="utf-8"
    )
    os.chmod(lib_dir / "git_provider_auth.sh", 0o755)
    script = scripts_dir / "mirror_issue_repo.sh"

    source_repo = tmp_path / "source"
    source_repo.mkdir()
    _git(source_repo, "init", "-b", "main")
    canonical_origin = tmp_path / "canonical-origin.git"
    subprocess.run(
        ["git", "init", "--bare", str(canonical_origin)], check=True, text=True, capture_output=True
    )
    _git(source_repo, "remote", "add", "origin", str(canonical_origin))

    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "Test User",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test User",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "AGENT_SANDBOX_GIT_ROOT": str(git_root),
            "USER": os.environ.get("USER", "test-user"),
        }
    )

    (source_repo / "README.md").write_text("main\n", encoding="utf-8")
    _git(source_repo, "add", "README.md", env=env)
    _git(source_repo, "commit", "-m", "Initial commit", env=env)
    _git(source_repo, "branch", "feature/cache-only")
    _git(source_repo, "push", "-u", "origin", "main", "feature/cache-only", env=env)

    cached_mirror = runtime_repo_cache / "cached.git"
    cached_worktree = runtime_repo_cache / "cached-worktree"
    subprocess.run(
        ["git", "clone", "--mirror", str(canonical_origin), str(cached_mirror)],
        check=True,
        text=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "clone", str(cached_mirror), str(cached_worktree)],
        check=True,
        text=True,
        capture_output=True,
    )

    subprocess.run(
        [str(script), "14357", str(cached_worktree)],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )

    bare_repo = git_root / "14357_repo.git"
    refs_output = _git_dir(
        bare_repo, "for-each-ref", "--format=%(refname)", "refs/heads"
    ).stdout.splitlines()

    assert "refs/heads/main" in refs_output
    assert "refs/heads/feature/cache-only" in refs_output
    assert (
        _git_dir(bare_repo, "symbolic-ref", "HEAD").stdout.strip()
        == "refs/heads/feature/cache-only"
    )
