from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
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
    git_root.mkdir(parents=True)
    scripts_dir.mkdir(parents=True)

    repo_root = Path(__file__).resolve().parents[1]
    for name in ("mirror_issue_repo.sh", "create_git_remote.sh", "resolve_repo_source.sh"):
        source = repo_root / "scripts" / name
        target = scripts_dir / name
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        os.chmod(target, 0o755)
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
        [str(script), "14357", str(source_repo)],
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
        [str(script), "14357", str(source_repo)],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )

    refs_output = _git_dir(bare_repo, "for-each-ref", "--format=%(refname)", "refs").stdout.splitlines()

    assert "refs/heads/main" in refs_output
    assert "refs/heads/feature/keep" in refs_output
    assert "refs/tags/v1.0.0" in refs_output
    assert "refs/heads/review/stale-session/main-vm-14357" not in refs_output
    assert "refs/heads/agents/stale-session/main-vm-14357" not in refs_output
    assert "refs/heads/review/old-session/main-vm-14357" not in refs_output
    assert "refs/heads/agents/old-session/main-vm-14357" not in refs_output
