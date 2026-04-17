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


def test_git_ssh_wrapper_refreshes_shared_mirror_before_upload_pack(tmp_path: Path) -> None:
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

    origin_bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(origin_bare)], check=True, text=True, capture_output=True)
    source_repo = tmp_path / "source"
    subprocess.run(["git", "clone", str(origin_bare), str(source_repo)], check=True, text=True, capture_output=True)
    _git(source_repo, "checkout", "-b", "main")
    (source_repo / "README.md").write_text("v1\n", encoding="utf-8")
    _git(source_repo, "add", "README.md", env=env)
    _git(source_repo, "commit", "-m", "Initial commit", env=env)
    _git(source_repo, "push", "-u", "origin", "main", env=env)
    subprocess.run(
        ["git", f"--git-dir={origin_bare}", "symbolic-ref", "HEAD", "refs/heads/main"],
        check=True,
        text=True,
        capture_output=True,
    )

    create_remote_script = scripts_dir / "create_git_remote.sh"
    subprocess.run([str(create_remote_script), "shared-repo"], check=True, text=True, capture_output=True, env=env)

    bare_repo = git_root / "shared-repo.git"
    _git_dir(
        bare_repo,
        "fetch",
        "--prune",
        str(source_repo),
        "+refs/heads/*:refs/heads/*",
        "+refs/tags/*:refs/tags/*",
    )
    _git_dir(bare_repo, "config", "agent-sandbox.mirrorSource", str(source_repo))
    _git_dir(bare_repo, "config", "agent-sandbox.mirrorRemoteName", "origin")
    _git_dir(bare_repo, "config", "agent-sandbox.mirrorSyncRemote", "1")
    _git_dir(bare_repo, "config", "agent-sandbox.mirrorForceLocal", "0")
    _git_dir(bare_repo, "config", "agent-sandbox.baseBranch", "main")
    _git_dir(bare_repo, "symbolic-ref", "HEAD", "refs/heads/main")

    initial_sha = _git(source_repo, "rev-parse", "HEAD").stdout.strip()
    assert _git_dir(bare_repo, "rev-parse", "refs/heads/main").stdout.strip() == initial_sha

    (source_repo / "README.md").write_text("v2\n", encoding="utf-8")
    _git(source_repo, "add", "README.md", env=env)
    _git(source_repo, "commit", "-m", "Update commit", env=env)
    _git(source_repo, "push", "origin", "main", env=env)
    updated_sha = _git(source_repo, "rev-parse", "HEAD").stdout.strip()

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
    subprocess.run([str(wrapper_script)], check=True, text=True, capture_output=True, env=wrapper_env)

    assert _git_dir(bare_repo, "rev-parse", "refs/heads/main").stdout.strip() == updated_sha
    assert git_shell_log.read_text(encoding="utf-8").strip() == f"git-upload-pack '{bare_repo}'"


def test_git_ssh_wrapper_preserves_existing_refs_when_shared_mirror_refresh_fails(tmp_path: Path) -> None:
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

    origin_bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(origin_bare)], check=True, text=True, capture_output=True)
    source_repo = tmp_path / "source"
    subprocess.run(["git", "clone", str(origin_bare), str(source_repo)], check=True, text=True, capture_output=True)
    _git(source_repo, "checkout", "-b", "main")
    (source_repo / "README.md").write_text("v1\n", encoding="utf-8")
    _git(source_repo, "add", "README.md", env=env)
    _git(source_repo, "commit", "-m", "Initial commit", env=env)
    _git(source_repo, "push", "-u", "origin", "main", env=env)
    subprocess.run(
        ["git", f"--git-dir={origin_bare}", "symbolic-ref", "HEAD", "refs/heads/main"],
        check=True,
        text=True,
        capture_output=True,
    )

    create_remote_script = scripts_dir / "create_git_remote.sh"
    subprocess.run([str(create_remote_script), "shared-repo"], check=True, text=True, capture_output=True, env=env)

    bare_repo = git_root / "shared-repo.git"
    _git_dir(
        bare_repo,
        "fetch",
        "--prune",
        str(source_repo),
        "+refs/heads/*:refs/heads/*",
        "+refs/tags/*:refs/tags/*",
    )
    _git_dir(bare_repo, "config", "agent-sandbox.mirrorSource", str(source_repo))
    _git_dir(bare_repo, "config", "agent-sandbox.mirrorRemoteName", "origin")
    _git_dir(bare_repo, "config", "agent-sandbox.mirrorSyncRemote", "0")
    _git_dir(
        bare_repo,
        "config",
        "--add",
        "agent-sandbox.mirrorRefspec",
        "+refs/heads/does-not-exist:refs/heads/does-not-exist",
    )
    _git_dir(bare_repo, "config", "agent-sandbox.baseBranch", "main")
    _git_dir(bare_repo, "symbolic-ref", "HEAD", "refs/heads/main")

    initial_sha = _git(source_repo, "rev-parse", "HEAD").stdout.strip()
    assert _git_dir(bare_repo, "rev-parse", "refs/heads/main").stdout.strip() == initial_sha

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_git_shell = fake_bin / "git-shell"
    fake_git_shell.write_text("#!/usr/bin/env bash\nset -euo pipefail\n", encoding="utf-8")
    os.chmod(fake_git_shell, 0o755)

    wrapper_script = scripts_dir / "git_ssh_command_wrapper.sh"
    wrapper_env = env.copy()
    wrapper_env.update(
        {
            "PATH": f"{fake_bin}:{wrapper_env['PATH']}",
            "SSH_ORIGINAL_COMMAND": f"git-upload-pack '{bare_repo}'",
        }
    )
    result = subprocess.run([str(wrapper_script)], text=True, capture_output=True, env=wrapper_env)

    assert result.returncode != 0
    assert _git_dir(bare_repo, "rev-parse", "refs/heads/main").stdout.strip() == initial_sha


def test_git_ssh_wrapper_preserves_pr_notes_during_shared_mirror_refresh(tmp_path: Path) -> None:
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

    origin_bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", str(origin_bare)], check=True, text=True, capture_output=True)
    source_repo = tmp_path / "source"
    subprocess.run(["git", "clone", str(origin_bare), str(source_repo)], check=True, text=True, capture_output=True)
    _git(source_repo, "checkout", "-b", "main")
    (source_repo / "README.md").write_text("v1\n", encoding="utf-8")
    _git(source_repo, "add", "README.md", env=env)
    _git(source_repo, "commit", "-m", "Initial commit", env=env)
    _git(source_repo, "push", "-u", "origin", "main", env=env)
    subprocess.run(
        ["git", f"--git-dir={origin_bare}", "symbolic-ref", "HEAD", "refs/heads/main"],
        check=True,
        text=True,
        capture_output=True,
    )

    create_remote_script = scripts_dir / "create_git_remote.sh"
    subprocess.run([str(create_remote_script), "shared-repo"], check=True, text=True, capture_output=True, env=env)

    bare_repo = git_root / "shared-repo.git"
    _git_dir(
        bare_repo,
        "fetch",
        "--prune",
        str(source_repo),
        "+refs/heads/*:refs/heads/*",
        "+refs/tags/*:refs/tags/*",
    )
    _git_dir(bare_repo, "config", "agent-sandbox.mirrorSource", str(source_repo))
    _git_dir(bare_repo, "config", "agent-sandbox.mirrorRemoteName", "origin")
    _git_dir(bare_repo, "config", "agent-sandbox.mirrorSyncRemote", "1")
    _git_dir(bare_repo, "config", "agent-sandbox.mirrorForceLocal", "0")
    _git_dir(bare_repo, "config", "agent-sandbox.baseBranch", "main")
    _git_dir(bare_repo, "symbolic-ref", "HEAD", "refs/heads/main")

    notes_clone = tmp_path / "notes-clone"
    subprocess.run(["git", "clone", str(bare_repo), str(notes_clone)], check=True, text=True, capture_output=True)
    _git(notes_clone, "notes", "--ref=agent-pr", "add", "-m", "PR summary", "HEAD", env=env)
    _git(notes_clone, "push", "origin", "refs/notes/agent-pr", env=env)
    expected_notes_sha = _git_dir(bare_repo, "rev-parse", "refs/notes/agent-pr").stdout.strip()

    (source_repo / "README.md").write_text("v2\n", encoding="utf-8")
    _git(source_repo, "add", "README.md", env=env)
    _git(source_repo, "commit", "-m", "Update commit", env=env)
    _git(source_repo, "push", "origin", "main", env=env)

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_git_shell = fake_bin / "git-shell"
    fake_git_shell.write_text("#!/usr/bin/env bash\nset -euo pipefail\n", encoding="utf-8")
    os.chmod(fake_git_shell, 0o755)

    wrapper_script = scripts_dir / "git_ssh_command_wrapper.sh"
    wrapper_env = env.copy()
    wrapper_env.update(
        {
            "PATH": f"{fake_bin}:{wrapper_env['PATH']}",
            "SSH_ORIGINAL_COMMAND": f"git-upload-pack '{bare_repo}'",
        }
    )
    subprocess.run([str(wrapper_script)], check=True, text=True, capture_output=True, env=wrapper_env)

    assert _git_dir(bare_repo, "rev-parse", "refs/notes/agent-pr").stdout.strip() == expected_notes_sha


def test_git_ssh_wrapper_refreshes_shared_mirror_with_non_origin_remote(tmp_path: Path) -> None:
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

    upstream_bare = tmp_path / "upstream.git"
    subprocess.run(["git", "init", "--bare", str(upstream_bare)], check=True, text=True, capture_output=True)
    source_repo = tmp_path / "source"
    subprocess.run(["git", "init", "-b", "main", str(source_repo)], check=True, text=True, capture_output=True)
    _git(source_repo, "remote", "add", "upstream", str(upstream_bare))
    (source_repo / "README.md").write_text("v1\n", encoding="utf-8")
    _git(source_repo, "add", "README.md", env=env)
    _git(source_repo, "commit", "-m", "Initial commit", env=env)
    _git(source_repo, "push", "-u", "upstream", "main", env=env)
    subprocess.run(
        ["git", f"--git-dir={upstream_bare}", "symbolic-ref", "HEAD", "refs/heads/main"],
        check=True,
        text=True,
        capture_output=True,
    )

    create_remote_script = scripts_dir / "create_git_remote.sh"
    subprocess.run([str(create_remote_script), "shared-repo"], check=True, text=True, capture_output=True, env=env)

    bare_repo = git_root / "shared-repo.git"
    _git_dir(
        bare_repo,
        "fetch",
        "--prune",
        str(source_repo),
        "+refs/heads/*:refs/heads/*",
        "+refs/tags/*:refs/tags/*",
    )
    _git_dir(bare_repo, "config", "agent-sandbox.mirrorSource", str(source_repo))
    _git_dir(bare_repo, "config", "agent-sandbox.mirrorRemoteName", "upstream")
    _git_dir(bare_repo, "config", "agent-sandbox.mirrorSyncRemote", "1")
    _git_dir(bare_repo, "config", "agent-sandbox.mirrorForceLocal", "0")
    _git_dir(bare_repo, "config", "agent-sandbox.baseBranch", "main")
    _git_dir(bare_repo, "symbolic-ref", "HEAD", "refs/heads/main")

    (source_repo / "README.md").write_text("v2\n", encoding="utf-8")
    _git(source_repo, "add", "README.md", env=env)
    _git(source_repo, "commit", "-m", "Update commit", env=env)
    _git(source_repo, "push", "upstream", "main", env=env)
    updated_sha = _git(source_repo, "rev-parse", "HEAD").stdout.strip()

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_git_shell = fake_bin / "git-shell"
    fake_git_shell.write_text("#!/usr/bin/env bash\nset -euo pipefail\n", encoding="utf-8")
    os.chmod(fake_git_shell, 0o755)

    wrapper_script = scripts_dir / "git_ssh_command_wrapper.sh"
    wrapper_env = env.copy()
    wrapper_env.update(
        {
            "PATH": f"{fake_bin}:{wrapper_env['PATH']}",
            "SSH_ORIGINAL_COMMAND": f"git-upload-pack '{bare_repo}'",
        }
    )
    subprocess.run([str(wrapper_script)], check=True, text=True, capture_output=True, env=wrapper_env)

    assert _git_dir(bare_repo, "rev-parse", "refs/heads/main").stdout.strip() == updated_sha
