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


def _copy_script(source_root: Path, target_root: Path, relative_path: str) -> None:
    source = source_root / relative_path
    target = target_root / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    os.chmod(target, 0o755)


def test_git_ssh_wrapper_refreshes_issue_mirror_before_upload_pack(tmp_path: Path) -> None:
    sandbox_root = tmp_path / "sandbox"
    git_root = sandbox_root / "git-remotes"
    scripts_dir = sandbox_root / "scripts"
    git_root.mkdir(parents=True)
    scripts_dir.mkdir(parents=True)

    repo_root = Path(__file__).resolve().parents[1]
    for relative_path in (
        "scripts/git_ssh_command_wrapper.sh",
        "scripts/mirror_issue_repo.sh",
        "scripts/create_git_remote.sh",
        "scripts/resolve_repo_source.sh",
        "scripts/lib/runtime_config.sh",
    ):
        _copy_script(repo_root, sandbox_root, relative_path)

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

    canonical_bare = tmp_path / "canonical.git"
    subprocess.run(["git", "init", "--bare", str(canonical_bare)], check=True, text=True, capture_output=True)

    canonical_worktree = tmp_path / "canonical-worktree"
    subprocess.run(
        ["git", "clone", str(canonical_bare), str(canonical_worktree)],
        check=True,
        text=True,
        capture_output=True,
    )
    _git(canonical_worktree, "checkout", "-b", "main")
    (canonical_worktree / "README.md").write_text("v1\n", encoding="utf-8")
    _git(canonical_worktree, "add", "README.md", env=env)
    _git(canonical_worktree, "commit", "-m", "Initial commit", env=env)
    _git(canonical_worktree, "push", "-u", "origin", "main", env=env)
    subprocess.run(
        ["git", f"--git-dir={canonical_bare}", "symbolic-ref", "HEAD", "refs/heads/main"],
        check=True,
        text=True,
        capture_output=True,
    )

    mirror_script = scripts_dir / "mirror_issue_repo.sh"
    subprocess.run(
        [str(mirror_script), "14357", canonical_bare.as_uri()],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )

    initial_sha = _git(canonical_worktree, "rev-parse", "HEAD").stdout.strip()
    bare_repo = git_root / "14357_repo.git"
    assert _git_dir(bare_repo, "rev-parse", "refs/heads/main").stdout.strip() == initial_sha

    (canonical_worktree / "README.md").write_text("v2\n", encoding="utf-8")
    _git(canonical_worktree, "add", "README.md", env=env)
    _git(canonical_worktree, "commit", "-m", "Update commit", env=env)
    _git(canonical_worktree, "push", "origin", "main", env=env)
    updated_sha = _git(canonical_worktree, "rev-parse", "HEAD").stdout.strip()

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    git_shell_log = tmp_path / "git-shell.log"
    fake_git_shell = fake_bin / "git-shell"
    fake_git_shell.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"printf '%s\\n' \"$2\" > {git_shell_log}\n",
        encoding="utf-8",
    )
    os.chmod(fake_git_shell, 0o755)

    wrapper_script = scripts_dir / "git_ssh_command_wrapper.sh"
    wrapper_env = env.copy()
    wrapper_env.update(
        {
            "PATH": f"{fake_bin}:{wrapper_env['PATH']}",
            "SSH_ORIGINAL_COMMAND": f"git-upload-pack '{bare_repo}'",
        }
    )
    subprocess.run(
        [str(wrapper_script)],
        check=True,
        text=True,
        capture_output=True,
        env=wrapper_env,
    )

    assert _git_dir(bare_repo, "rev-parse", "refs/heads/main").stdout.strip() == updated_sha
    assert git_shell_log.read_text(encoding="utf-8").strip() == f"git-upload-pack '{bare_repo}'"
