from __future__ import annotations

import base64
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


def _git_result(
    repo: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
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


def _copy_git_ssh_scripts(source_root: Path, target_root: Path) -> None:
    for relative_path in (
        "scripts/git_ssh_command_wrapper.sh",
        "scripts/create_git_remote.sh",
        "scripts/resolve_repo_source.sh",
        "scripts/lib/runtime_config.sh",
        "scripts/lib/git_provider_auth.sh",
    ):
        _copy_script(source_root, target_root, relative_path)


def _base_git_env(git_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "Test User",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test User",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "EVANDER_GIT_ROOT": str(git_root),
        }
    )
    return env


def _write_fake_ssh(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env bash
set -euo pipefail

host_seen=0
command_parts=()
while (($# > 0)); do
  case "$1" in
    -o|-p|-l|-i|-F)
      shift 2
      ;;
    -*)
      shift
      ;;
    *)
      if (( host_seen == 0 )); then
        host_seen=1
      else
        command_parts+=("$1")
      fi
      shift
      ;;
  esac
done

if (( ${#command_parts[@]} == 0 )); then
  echo "missing git ssh command" >&2
  exit 1
fi

printf -v original_command '%s ' "${command_parts[@]}"
export SSH_ORIGINAL_COMMAND="${original_command% }"
exec "${EVANDER_TEST_WRAPPER}"
""",
        encoding="utf-8",
    )
    os.chmod(path, 0o755)


def test_git_ssh_wrapper_enforces_repo_and_session_ref_prefix_for_pushes(
    tmp_path: Path,
) -> None:
    sandbox_root = tmp_path / "sandbox"
    git_root = sandbox_root / "git-remotes"
    scripts_dir = sandbox_root / "scripts"
    git_root.mkdir(parents=True)
    scripts_dir.mkdir(parents=True)

    repo_root = Path(__file__).resolve().parents[1]
    _copy_git_ssh_scripts(repo_root, sandbox_root)
    env = _base_git_env(git_root)

    create_remote_script = scripts_dir / "create_git_remote.sh"
    subprocess.run(
        [str(create_remote_script), "allowed"],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )
    subprocess.run(
        [str(create_remote_script), "other"],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )

    allowed_repo = git_root / "allowed.git"
    other_repo = git_root / "other.git"
    worktree = tmp_path / "worktree"
    subprocess.run(["git", "init", str(worktree)], check=True, text=True, capture_output=True)
    _git(worktree, "checkout", "-b", "main")
    (worktree / "README.md").write_text("agent work\n", encoding="utf-8")
    _git(worktree, "add", "README.md", env=env)
    _git(worktree, "commit", "-m", "Agent work", env=env)
    expected_sha = _git(worktree, "rev-parse", "HEAD", env=env).stdout.strip()

    fake_ssh = tmp_path / "fake-ssh"
    _write_fake_ssh(fake_ssh)
    wrapper_script = scripts_dir / "git_ssh_command_wrapper.sh"
    push_env = env.copy()
    push_env.update(
        {
            "GIT_SSH_COMMAND": str(fake_ssh),
            "GIT_SSH_VARIANT": "ssh",
            "EVANDER_TEST_WRAPPER": str(wrapper_script),
            "EVANDER_GIT_AUTH_ACCESS": "write",
            "EVANDER_GIT_AUTH_REPO": str(allowed_repo),
            "EVANDER_GIT_AUTH_SESSION_ID": "session-1",
            "EVANDER_GIT_AUTH_REF_PREFIX": "refs/heads/agents/session-1/",
        }
    )

    _git(worktree, "remote", "add", "agent", f"ssh://evander-git-host{allowed_repo}")
    _git(worktree, "remote", "add", "other", f"ssh://evander-git-host{other_repo}")

    _git(
        worktree,
        "push",
        "agent",
        "HEAD:refs/heads/agents/session-1/topic",
        env=push_env,
    )
    assert (
        _git_dir(allowed_repo, "rev-parse", "refs/heads/agents/session-1/topic").stdout.strip()
        == expected_sha
    )

    allowed_read = subprocess.run(
        [
            "git",
            "ls-remote",
            f"ssh://evander-git-host{allowed_repo}",
            "refs/heads/agents/session-1/topic",
        ],
        check=False,
        text=True,
        capture_output=True,
        env=push_env,
    )
    assert allowed_read.returncode == 0
    assert "refs/heads/agents/session-1/topic" in allowed_read.stdout

    denied_cross_repo_read = subprocess.run(
        ["git", "ls-remote", f"ssh://evander-git-host{other_repo}"],
        check=False,
        text=True,
        capture_output=True,
        env=push_env,
    )
    assert denied_cross_repo_read.returncode != 0
    assert "Git SSH key is not authorized for repository" in denied_cross_repo_read.stderr

    wrong_prefix = _git_result(
        worktree,
        "push",
        "agent",
        "HEAD:refs/heads/agents/session-2/topic",
        env=push_env,
    )
    assert wrong_prefix.returncode != 0
    assert "outside authorized prefix 'refs/heads/agents/session-1/'" in wrong_prefix.stderr
    missing_wrong_prefix_ref = subprocess.run(
        [
            "git",
            f"--git-dir={allowed_repo}",
            "rev-parse",
            "--verify",
            "refs/heads/agents/session-2/topic",
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    assert missing_wrong_prefix_ref.returncode != 0

    repo_mismatch = _git_result(
        worktree,
        "push",
        "other",
        "HEAD:refs/heads/agents/session-1/topic",
        env=push_env,
    )
    assert repo_mismatch.returncode != 0
    assert "Git SSH key is not authorized for repository" in repo_mismatch.stderr
    missing_other_ref = subprocess.run(
        [
            "git",
            f"--git-dir={other_repo}",
            "rev-parse",
            "--verify",
            "refs/heads/agents/session-1/topic",
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    assert missing_other_ref.returncode != 0


def test_git_ssh_wrapper_read_only_requires_repo_scope_and_denies_pushes(
    tmp_path: Path,
) -> None:
    sandbox_root = tmp_path / "sandbox"
    git_root = sandbox_root / "git-remotes"
    scripts_dir = sandbox_root / "scripts"
    git_root.mkdir(parents=True)
    scripts_dir.mkdir(parents=True)

    repo_root = Path(__file__).resolve().parents[1]
    _copy_git_ssh_scripts(repo_root, sandbox_root)
    env = _base_git_env(git_root)

    create_remote_script = scripts_dir / "create_git_remote.sh"
    subprocess.run(
        [str(create_remote_script), "readonly"],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )
    subprocess.run(
        [str(create_remote_script), "other"],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )

    bare_repo = git_root / "readonly.git"
    other_repo = git_root / "other.git"
    worktree = tmp_path / "worktree"
    subprocess.run(["git", "init", str(worktree)], check=True, text=True, capture_output=True)
    _git(worktree, "checkout", "-b", "main")
    (worktree / "README.md").write_text("seed\n", encoding="utf-8")
    _git(worktree, "add", "README.md", env=env)
    _git(worktree, "commit", "-m", "Seed", env=env)
    _git(worktree, "remote", "add", "origin", str(bare_repo))
    _git(worktree, "push", "origin", "HEAD:refs/heads/main", env=env)

    fake_ssh = tmp_path / "fake-ssh"
    _write_fake_ssh(fake_ssh)
    wrapper_script = scripts_dir / "git_ssh_command_wrapper.sh"
    read_env = env.copy()
    read_env.update(
        {
            "GIT_SSH_COMMAND": str(fake_ssh),
            "GIT_SSH_VARIANT": "ssh",
            "EVANDER_TEST_WRAPPER": str(wrapper_script),
        }
    )

    remote_url = f"ssh://evander-git-host{bare_repo}"
    ls_remote = subprocess.run(
        ["git", "ls-remote", remote_url, "refs/heads/main"],
        check=False,
        text=True,
        capture_output=True,
        env=read_env,
    )
    assert ls_remote.returncode != 0
    assert "Git SSH key is missing authorized repository metadata." in ls_remote.stderr

    scoped_read_env = read_env.copy()
    scoped_read_env.update(
        {
            "EVANDER_GIT_AUTH_ACCESS": "read-only",
            "EVANDER_GIT_AUTH_REPO": str(bare_repo),
        }
    )
    ls_remote = subprocess.run(
        ["git", "ls-remote", remote_url, "refs/heads/main"],
        check=False,
        text=True,
        capture_output=True,
        env=scoped_read_env,
    )
    assert ls_remote.returncode == 0
    assert "refs/heads/main" in ls_remote.stdout

    denied_cross_repo_read = subprocess.run(
        ["git", "ls-remote", f"ssh://evander-git-host{other_repo}"],
        check=False,
        text=True,
        capture_output=True,
        env=scoped_read_env,
    )
    assert denied_cross_repo_read.returncode != 0
    assert "Git SSH key is not authorized for repository" in denied_cross_repo_read.stderr

    denied_push = _git_result(
        worktree,
        "push",
        remote_url,
        "HEAD:refs/heads/agents/session-1/topic",
        env=scoped_read_env,
    )
    assert denied_push.returncode != 0
    assert "Git SSH key is read-only; repo/session metadata is required for pushes." in (
        denied_push.stderr
    )


def test_git_ssh_wrapper_read_only_prefix_allows_multiple_repos_and_denies_pushes(
    tmp_path: Path,
) -> None:
    sandbox_root = tmp_path / "sandbox"
    git_root = sandbox_root / "git-remotes"
    scripts_dir = sandbox_root / "scripts"
    git_root.mkdir(parents=True)
    scripts_dir.mkdir(parents=True)

    repo_root = Path(__file__).resolve().parents[1]
    _copy_git_ssh_scripts(repo_root, sandbox_root)
    env = _base_git_env(git_root)

    create_remote_script = scripts_dir / "create_git_remote.sh"
    subprocess.run(
        [str(create_remote_script), "first"],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )
    subprocess.run(
        [str(create_remote_script), "second"],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )

    first_repo = git_root / "first.git"
    second_repo = git_root / "second.git"
    worktree = tmp_path / "worktree"
    subprocess.run(["git", "init", str(worktree)], check=True, text=True, capture_output=True)
    _git(worktree, "checkout", "-b", "main")
    (worktree / "README.md").write_text("seed\n", encoding="utf-8")
    _git(worktree, "add", "README.md", env=env)
    _git(worktree, "commit", "-m", "Seed", env=env)
    _git(worktree, "remote", "add", "first", str(first_repo))
    _git(worktree, "remote", "add", "second", str(second_repo))
    _git(worktree, "push", "first", "HEAD:refs/heads/main", env=env)
    _git(worktree, "push", "second", "HEAD:refs/heads/main", env=env)

    fake_ssh = tmp_path / "fake-ssh"
    _write_fake_ssh(fake_ssh)
    wrapper_script = scripts_dir / "git_ssh_command_wrapper.sh"
    prefix_read_env = env.copy()
    prefix_read_env.update(
        {
            "GIT_SSH_COMMAND": str(fake_ssh),
            "GIT_SSH_VARIANT": "ssh",
            "EVANDER_TEST_WRAPPER": str(wrapper_script),
            "EVANDER_GIT_AUTH_ACCESS": "read-only",
            "EVANDER_GIT_AUTH_REPO_PREFIX_B64": base64.b64encode(
                str(git_root).encode("utf-8")
            ).decode("ascii"),
        }
    )

    for repo in (first_repo, second_repo):
        ls_remote = subprocess.run(
            ["git", "ls-remote", f"ssh://evander-git-host{repo}", "refs/heads/main"],
            check=False,
            text=True,
            capture_output=True,
            env=prefix_read_env,
        )
        assert ls_remote.returncode == 0
        assert "refs/heads/main" in ls_remote.stdout

    denied_push = _git_result(
        worktree,
        "push",
        f"ssh://evander-git-host{first_repo}",
        "HEAD:refs/heads/agents/session-1/topic",
        env=prefix_read_env,
    )
    assert denied_push.returncode != 0
    assert "Git SSH key is read-only; repo/session metadata is required for pushes." in (
        denied_push.stderr
    )

    string_prefix_env = prefix_read_env.copy()
    string_prefix_env["EVANDER_GIT_AUTH_REPO_PREFIX_B64"] = base64.b64encode(
        str(git_root / "fir").encode("utf-8")
    ).decode("ascii")
    denied_string_prefix = subprocess.run(
        ["git", "ls-remote", f"ssh://evander-git-host{first_repo}", "refs/heads/main"],
        check=False,
        text=True,
        capture_output=True,
        env=string_prefix_env,
    )
    assert denied_string_prefix.returncode != 0
    assert "Git SSH key is not authorized for repository" in denied_string_prefix.stderr


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
        "scripts/lib/git_provider_auth.sh",
    ):
        _copy_script(repo_root, sandbox_root, relative_path)

    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "Test User",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test User",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "EVANDER_GIT_ROOT": str(git_root),
        }
    )

    canonical_bare = tmp_path / "canonical.git"
    subprocess.run(
        ["git", "init", "--bare", str(canonical_bare)], check=True, text=True, capture_output=True
    )

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
        f"#!/usr/bin/env bash\nset -euo pipefail\nprintf '%s\\n' \"$2\" > {git_shell_log}\n",
        encoding="utf-8",
    )
    os.chmod(fake_git_shell, 0o755)

    wrapper_script = scripts_dir / "git_ssh_command_wrapper.sh"
    wrapper_env = env.copy()
    wrapper_env.update(
        {
            "PATH": f"{fake_bin}:{wrapper_env['PATH']}",
            "EVANDER_GIT_AUTH_ACCESS": "read-only",
            "EVANDER_GIT_AUTH_REPO": str(bare_repo),
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
        "scripts/lib/git_provider_auth.sh",
    ):
        _copy_script(repo_root, sandbox_root, relative_path)

    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "Test User",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test User",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "EVANDER_GIT_ROOT": str(git_root),
        }
    )

    origin_bare = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", str(origin_bare)], check=True, text=True, capture_output=True
    )
    source_repo = tmp_path / "source"
    subprocess.run(
        ["git", "clone", str(origin_bare), str(source_repo)],
        check=True,
        text=True,
        capture_output=True,
    )
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
    _git(source_repo, "checkout", "-b", "work")
    (source_repo / "work.txt").write_text("work\n", encoding="utf-8")
    _git(source_repo, "add", "work.txt", env=env)
    _git(source_repo, "commit", "-m", "Work branch", env=env)
    _git(source_repo, "push", "-u", "origin", "work", env=env)
    subprocess.run(
        ["git", "-C", str(source_repo), "update-ref", "-d", "refs/remotes/origin/HEAD"],
        check=False,
        text=True,
        capture_output=True,
    )
    create_remote_script = scripts_dir / "create_git_remote.sh"
    subprocess.run(
        [str(create_remote_script), "shared-repo"],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )

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

    initial_main_sha = _git(source_repo, "rev-parse", "main").stdout.strip()
    assert _git_dir(bare_repo, "rev-parse", "refs/heads/main").stdout.strip() == initial_main_sha

    upstream_worktree = tmp_path / "upstream-worktree"
    subprocess.run(
        ["git", "clone", str(origin_bare), str(upstream_worktree)],
        check=True,
        text=True,
        capture_output=True,
    )
    (upstream_worktree / "README.md").write_text("v2\n", encoding="utf-8")
    _git(upstream_worktree, "add", "README.md", env=env)
    _git(upstream_worktree, "commit", "-m", "Update commit", env=env)
    _git(upstream_worktree, "push", "origin", "main", env=env)
    updated_sha = _git(upstream_worktree, "rev-parse", "HEAD").stdout.strip()

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    git_shell_log = tmp_path / "git-shell.log"
    fake_git_shell = fake_bin / "git-shell"
    fake_git_shell.write_text(
        f"#!/usr/bin/env bash\nset -euo pipefail\nprintf '%s\\n' \"$2\" > {git_shell_log}\n",
        encoding="utf-8",
    )
    os.chmod(fake_git_shell, 0o755)

    wrapper_script = scripts_dir / "git_ssh_command_wrapper.sh"
    wrapper_env = env.copy()
    wrapper_env.update(
        {
            "PATH": f"{fake_bin}:{wrapper_env['PATH']}",
            "EVANDER_GIT_AUTH_ACCESS": "read-only",
            "EVANDER_GIT_AUTH_REPO": str(bare_repo),
            "SSH_ORIGINAL_COMMAND": f"git-upload-pack '{bare_repo}'",
        }
    )
    subprocess.run(
        [str(wrapper_script)], check=True, text=True, capture_output=True, env=wrapper_env
    )

    assert _git_dir(bare_repo, "rev-parse", "refs/heads/main").stdout.strip() == updated_sha
    assert _git_dir(bare_repo, "config", "--get", "evander.mirrorSource").stdout.strip() == str(
        source_repo
    )
    assert _git_dir(bare_repo, "config", "--get", "evander.mirrorRemoteName").stdout.strip() == (
        "origin"
    )
    assert _git_dir(bare_repo, "config", "--get", "evander.mirrorSyncRemote").stdout.strip() == "1"
    assert _git_dir(bare_repo, "config", "--get", "evander.mirrorForceLocal").stdout.strip() == "0"
    assert _git_dir(bare_repo, "config", "--get", "evander.baseBranch").stdout.strip() == "main"
    assert _git(source_repo, "branch", "--show-current").stdout.strip() == "work"
    assert _git(source_repo, "rev-parse", "main").stdout.strip() == updated_sha
    assert git_shell_log.read_text(encoding="utf-8").strip() == f"git-upload-pack '{bare_repo}'"


def test_git_ssh_wrapper_preserves_local_output_refs_during_shared_mirror_refresh(
    tmp_path: Path,
) -> None:
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
        "scripts/lib/git_provider_auth.sh",
    ):
        _copy_script(repo_root, sandbox_root, relative_path)

    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "Test User",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test User",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "EVANDER_GIT_ROOT": str(git_root),
        }
    )

    origin_bare = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", str(origin_bare)], check=True, text=True, capture_output=True
    )
    source_repo = tmp_path / "source"
    subprocess.run(
        ["git", "clone", str(origin_bare), str(source_repo)],
        check=True,
        text=True,
        capture_output=True,
    )
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
    subprocess.run(
        [str(create_remote_script), "shared-repo"],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )

    bare_repo = git_root / "shared-repo.git"
    _git_dir(
        bare_repo,
        "fetch",
        "--prune",
        str(source_repo),
        "+refs/heads/*:refs/heads/*",
        "+refs/tags/*:refs/tags/*",
    )
    _git_dir(bare_repo, "config", "evander.mirrorSource", str(source_repo))
    _git_dir(bare_repo, "config", "evander.mirrorRemoteName", "origin")
    _git_dir(bare_repo, "config", "evander.mirrorSyncRemote", "1")
    _git_dir(bare_repo, "config", "evander.mirrorForceLocal", "0")
    _git_dir(bare_repo, "config", "evander.baseBranch", "main")
    _git_dir(bare_repo, "symbolic-ref", "HEAD", "refs/heads/main")

    output_clone = tmp_path / "output-clone"
    subprocess.run(
        ["git", "clone", str(bare_repo), str(output_clone)],
        check=True,
        text=True,
        capture_output=True,
    )
    _git(output_clone, "checkout", "-b", "work")
    (output_clone / "agent.txt").write_text("agent output\n", encoding="utf-8")
    _git(output_clone, "add", "agent.txt", env=env)
    _git(output_clone, "commit", "-m", "Agent output", env=env)
    _git(output_clone, "push", "origin", "HEAD:refs/heads/agents/session/work", env=env)
    _git(output_clone, "push", "origin", "HEAD:refs/heads/review/session/work", env=env)
    _git(output_clone, "push", "origin", "HEAD:refs/heads/team/team/reviews/session-work", env=env)
    expected_output_sha = _git(output_clone, "rev-parse", "HEAD").stdout.strip()

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
        f"#!/usr/bin/env bash\nset -euo pipefail\nprintf '%s\\n' \"$2\" > {git_shell_log}\n",
        encoding="utf-8",
    )
    os.chmod(fake_git_shell, 0o755)

    wrapper_script = scripts_dir / "git_ssh_command_wrapper.sh"
    wrapper_env = env.copy()
    wrapper_env.update(
        {
            "PATH": f"{fake_bin}:{wrapper_env['PATH']}",
            "EVANDER_GIT_AUTH_ACCESS": "read-only",
            "EVANDER_GIT_AUTH_REPO": str(bare_repo),
            "SSH_ORIGINAL_COMMAND": f"git-upload-pack '{bare_repo}'",
        }
    )
    subprocess.run(
        [str(wrapper_script)], check=True, text=True, capture_output=True, env=wrapper_env
    )

    assert _git_dir(bare_repo, "rev-parse", "refs/heads/main").stdout.strip() == updated_sha
    assert (
        _git_dir(bare_repo, "rev-parse", "refs/heads/agents/session/work").stdout.strip()
        == expected_output_sha
    )
    assert (
        _git_dir(bare_repo, "rev-parse", "refs/heads/review/session/work").stdout.strip()
        == expected_output_sha
    )
    assert (
        _git_dir(bare_repo, "rev-parse", "refs/heads/team/team/reviews/session-work").stdout.strip()
        == expected_output_sha
    )
    assert git_shell_log.read_text(encoding="utf-8").strip() == f"git-upload-pack '{bare_repo}'"


def test_git_ssh_wrapper_can_disable_shared_mirror_auto_refresh(tmp_path: Path) -> None:
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
        "scripts/lib/git_provider_auth.sh",
    ):
        _copy_script(repo_root, sandbox_root, relative_path)

    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "Test User",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test User",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "EVANDER_GIT_ROOT": str(git_root),
        }
    )

    source_repo = tmp_path / "source"
    source_repo.mkdir()
    _git(source_repo, "init")
    _git(source_repo, "checkout", "-b", "main")
    (source_repo / "README.md").write_text("v1\n", encoding="utf-8")
    _git(source_repo, "add", "README.md", env=env)
    _git(source_repo, "commit", "-m", "Initial commit", env=env)

    create_remote_script = scripts_dir / "create_git_remote.sh"
    subprocess.run(
        [str(create_remote_script), "shared-repo"],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )

    bare_repo = git_root / "shared-repo.git"
    _git_dir(bare_repo, "fetch", str(source_repo), "refs/heads/main:refs/heads/main")
    _git_dir(bare_repo, "config", "evander.mirrorSource", str(source_repo))
    _git_dir(bare_repo, "config", "evander.mirrorRemoteName", "origin")
    _git_dir(bare_repo, "config", "evander.mirrorSyncRemote", "1")
    _git_dir(bare_repo, "config", "evander.mirrorAutoRefresh", "0")
    initial_sha = _git(source_repo, "rev-parse", "HEAD").stdout.strip()

    (source_repo / "README.md").write_text("v2\n", encoding="utf-8")
    _git(source_repo, "add", "README.md", env=env)
    _git(source_repo, "commit", "-m", "Update commit", env=env)

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    git_shell_log = tmp_path / "git-shell.log"
    fake_git_shell = fake_bin / "git-shell"
    fake_git_shell.write_text(
        f"#!/usr/bin/env bash\nset -euo pipefail\nprintf '%s\\n' \"$2\" > {git_shell_log}\n",
        encoding="utf-8",
    )
    os.chmod(fake_git_shell, 0o755)

    wrapper_script = scripts_dir / "git_ssh_command_wrapper.sh"
    wrapper_env = env.copy()
    wrapper_env.update(
        {
            "PATH": f"{fake_bin}:{wrapper_env['PATH']}",
            "EVANDER_GIT_AUTH_ACCESS": "read-only",
            "EVANDER_GIT_AUTH_REPO": str(bare_repo),
            "SSH_ORIGINAL_COMMAND": f"git-upload-pack '{bare_repo}'",
        }
    )
    subprocess.run(
        [str(wrapper_script)], check=True, text=True, capture_output=True, env=wrapper_env
    )

    assert _git_dir(bare_repo, "rev-parse", "refs/heads/main").stdout.strip() == initial_sha
    assert git_shell_log.read_text(encoding="utf-8").strip() == f"git-upload-pack '{bare_repo}'"


def test_git_ssh_wrapper_refreshes_remote_source_tracking_branches_before_upload_pack(
    tmp_path: Path,
) -> None:
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
        "scripts/lib/git_provider_auth.sh",
    ):
        _copy_script(repo_root, sandbox_root, relative_path)

    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "Test User",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test User",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "EVANDER_GIT_ROOT": str(git_root),
        }
    )

    canonical_bare = tmp_path / "canonical.git"
    subprocess.run(
        ["git", "init", "--bare", str(canonical_bare)], check=True, text=True, capture_output=True
    )

    canonical_worktree = tmp_path / "canonical-worktree"
    subprocess.run(
        ["git", "clone", str(canonical_bare), str(canonical_worktree)],
        check=True,
        text=True,
        capture_output=True,
    )
    _git(canonical_worktree, "checkout", "-b", "main")
    (canonical_worktree / "README.md").write_text("base\n", encoding="utf-8")
    _git(canonical_worktree, "add", "README.md", env=env)
    _git(canonical_worktree, "commit", "-m", "Base commit", env=env)
    _git(canonical_worktree, "push", "-u", "origin", "main", env=env)
    _git(canonical_worktree, "checkout", "-b", "feature/review", "main", env=env)
    (canonical_worktree / "feature.txt").write_text("review\n", encoding="utf-8")
    _git(canonical_worktree, "add", "feature.txt", env=env)
    _git(canonical_worktree, "commit", "-m", "Feature commit", env=env)
    feature_sha = _git(canonical_worktree, "rev-parse", "HEAD", env=env).stdout.strip()
    _git(canonical_worktree, "push", "-u", "origin", "feature/review", env=env)
    subprocess.run(
        ["git", f"--git-dir={canonical_bare}", "symbolic-ref", "HEAD", "refs/heads/main"],
        check=True,
        text=True,
        capture_output=True,
    )

    create_remote_script = scripts_dir / "create_git_remote.sh"
    subprocess.run(
        [str(create_remote_script), "shared-repo"],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )

    bare_repo = git_root / "shared-repo.git"
    _git_dir(
        bare_repo,
        "fetch",
        "--prune",
        str(canonical_worktree),
        "+refs/heads/main:refs/heads/main",
    )
    _git_dir(bare_repo, "config", "evander.mirrorSource", canonical_bare.as_uri())
    _git_dir(bare_repo, "config", "evander.mirrorRemoteName", "origin")
    _git_dir(bare_repo, "config", "evander.mirrorSyncRemote", "1")
    _git_dir(bare_repo, "config", "evander.mirrorForceLocal", "0")
    _git_dir(bare_repo, "config", "evander.baseBranch", "main")
    _git_dir(bare_repo, "symbolic-ref", "HEAD", "refs/heads/main")

    refs_before = _git_dir(
        bare_repo, "for-each-ref", "--format=%(refname)", "refs/heads"
    ).stdout.splitlines()
    assert "refs/heads/feature/review" not in refs_before

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    git_shell_log = tmp_path / "git-shell.log"
    fake_git_shell = fake_bin / "git-shell"
    fake_git_shell.write_text(
        f"#!/usr/bin/env bash\nset -euo pipefail\nprintf '%s\\n' \"$2\" > {git_shell_log}\n",
        encoding="utf-8",
    )
    os.chmod(fake_git_shell, 0o755)

    wrapper_script = scripts_dir / "git_ssh_command_wrapper.sh"
    wrapper_env = env.copy()
    wrapper_env.update(
        {
            "PATH": f"{fake_bin}:{wrapper_env['PATH']}",
            "EVANDER_GIT_AUTH_ACCESS": "read-only",
            "EVANDER_GIT_AUTH_REPO": str(bare_repo),
            "SSH_ORIGINAL_COMMAND": f"git-upload-pack '{bare_repo}'",
        }
    )
    subprocess.run(
        [str(wrapper_script)], check=True, text=True, capture_output=True, env=wrapper_env
    )

    assert (
        _git_dir(bare_repo, "rev-parse", "refs/heads/feature/review").stdout.strip() == feature_sha
    )
    assert git_shell_log.read_text(encoding="utf-8").strip() == f"git-upload-pack '{bare_repo}'"


def test_git_ssh_wrapper_preserves_existing_refs_when_shared_mirror_refresh_fails(
    tmp_path: Path,
) -> None:
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
        "scripts/lib/git_provider_auth.sh",
    ):
        _copy_script(repo_root, sandbox_root, relative_path)

    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "Test User",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test User",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "EVANDER_GIT_ROOT": str(git_root),
        }
    )

    origin_bare = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", str(origin_bare)], check=True, text=True, capture_output=True
    )
    source_repo = tmp_path / "source"
    subprocess.run(
        ["git", "clone", str(origin_bare), str(source_repo)],
        check=True,
        text=True,
        capture_output=True,
    )
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
    subprocess.run(
        [str(create_remote_script), "shared-repo"],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )

    bare_repo = git_root / "shared-repo.git"
    _git_dir(
        bare_repo,
        "fetch",
        "--prune",
        str(source_repo),
        "+refs/heads/*:refs/heads/*",
        "+refs/tags/*:refs/tags/*",
    )
    _git_dir(bare_repo, "config", "evander.mirrorSource", str(source_repo))
    _git_dir(bare_repo, "config", "evander.mirrorRemoteName", "origin")
    _git_dir(bare_repo, "config", "evander.mirrorSyncRemote", "0")
    _git_dir(
        bare_repo,
        "config",
        "--add",
        "evander.mirrorRefspec",
        "+refs/heads/does-not-exist:refs/heads/does-not-exist",
    )
    _git_dir(bare_repo, "config", "evander.baseBranch", "main")
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
            "EVANDER_GIT_AUTH_ACCESS": "read-only",
            "EVANDER_GIT_AUTH_REPO": str(bare_repo),
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
        "scripts/lib/git_provider_auth.sh",
    ):
        _copy_script(repo_root, sandbox_root, relative_path)

    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "Test User",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test User",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "EVANDER_GIT_ROOT": str(git_root),
        }
    )

    origin_bare = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", str(origin_bare)], check=True, text=True, capture_output=True
    )
    source_repo = tmp_path / "source"
    subprocess.run(
        ["git", "clone", str(origin_bare), str(source_repo)],
        check=True,
        text=True,
        capture_output=True,
    )
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
    subprocess.run(
        [str(create_remote_script), "shared-repo"],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )

    bare_repo = git_root / "shared-repo.git"
    _git_dir(
        bare_repo,
        "fetch",
        "--prune",
        str(source_repo),
        "+refs/heads/*:refs/heads/*",
        "+refs/tags/*:refs/tags/*",
    )
    _git_dir(bare_repo, "config", "evander.mirrorSource", str(source_repo))
    _git_dir(bare_repo, "config", "evander.mirrorRemoteName", "origin")
    _git_dir(bare_repo, "config", "evander.mirrorSyncRemote", "1")
    _git_dir(bare_repo, "config", "evander.mirrorForceLocal", "0")
    _git_dir(bare_repo, "config", "evander.baseBranch", "main")
    _git_dir(bare_repo, "symbolic-ref", "HEAD", "refs/heads/main")

    notes_clone = tmp_path / "notes-clone"
    subprocess.run(
        ["git", "clone", str(bare_repo), str(notes_clone)],
        check=True,
        text=True,
        capture_output=True,
    )
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
            "EVANDER_GIT_AUTH_ACCESS": "read-only",
            "EVANDER_GIT_AUTH_REPO": str(bare_repo),
            "SSH_ORIGINAL_COMMAND": f"git-upload-pack '{bare_repo}'",
        }
    )
    subprocess.run(
        [str(wrapper_script)], check=True, text=True, capture_output=True, env=wrapper_env
    )

    assert (
        _git_dir(bare_repo, "rev-parse", "refs/notes/agent-pr").stdout.strip() == expected_notes_sha
    )


def test_git_ssh_wrapper_accepts_same_name_branch_refspec_for_shared_mirror_refresh(
    tmp_path: Path,
) -> None:
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
        "scripts/lib/git_provider_auth.sh",
    ):
        _copy_script(repo_root, sandbox_root, relative_path)

    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "Test User",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test User",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "EVANDER_GIT_ROOT": str(git_root),
        }
    )

    origin_bare = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", str(origin_bare)], check=True, text=True, capture_output=True
    )
    source_repo = tmp_path / "source"
    subprocess.run(
        ["git", "clone", str(origin_bare), str(source_repo)],
        check=True,
        text=True,
        capture_output=True,
    )
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
    _git(source_repo, "checkout", "-b", "work")
    (source_repo / "work.txt").write_text("work\n", encoding="utf-8")
    _git(source_repo, "add", "work.txt", env=env)
    _git(source_repo, "commit", "-m", "Work branch", env=env)
    _git(source_repo, "push", "-u", "origin", "work", env=env)
    subprocess.run(
        ["git", "-C", str(source_repo), "update-ref", "-d", "refs/remotes/origin/HEAD"],
        check=False,
        text=True,
        capture_output=True,
    )

    create_remote_script = scripts_dir / "create_git_remote.sh"
    subprocess.run(
        [str(create_remote_script), "shared-repo"],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )

    bare_repo = git_root / "shared-repo.git"
    _git_dir(
        bare_repo,
        "fetch",
        "--prune",
        str(source_repo),
        "+refs/heads/*:refs/heads/*",
        "+refs/tags/*:refs/tags/*",
    )
    _git_dir(bare_repo, "config", "evander.mirrorSource", str(source_repo))
    _git_dir(bare_repo, "config", "evander.mirrorRemoteName", "origin")
    _git_dir(bare_repo, "config", "evander.mirrorSyncRemote", "1")
    _git_dir(bare_repo, "config", "evander.mirrorForceLocal", "0")
    _git_dir(
        bare_repo,
        "config",
        "--add",
        "evander.mirrorRefspec",
        "refs/heads/main:refs/heads/main",
    )
    _git_dir(bare_repo, "config", "evander.baseBranch", "main")
    _git_dir(bare_repo, "symbolic-ref", "HEAD", "refs/heads/main")

    upstream_worktree = tmp_path / "upstream-worktree"
    subprocess.run(
        ["git", "clone", "--branch", "main", str(origin_bare), str(upstream_worktree)],
        check=True,
        text=True,
        capture_output=True,
    )
    (upstream_worktree / "README.md").write_text("v2\n", encoding="utf-8")
    _git(upstream_worktree, "add", "README.md", env=env)
    _git(upstream_worktree, "commit", "-m", "Update commit", env=env)
    _git(upstream_worktree, "push", "origin", "main", env=env)
    updated_sha = _git(upstream_worktree, "rev-parse", "HEAD").stdout.strip()

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
            "EVANDER_GIT_AUTH_ACCESS": "read-only",
            "EVANDER_GIT_AUTH_REPO": str(bare_repo),
            "SSH_ORIGINAL_COMMAND": f"git-upload-pack '{bare_repo}'",
        }
    )
    subprocess.run(
        [str(wrapper_script)], check=True, text=True, capture_output=True, env=wrapper_env
    )

    assert _git(source_repo, "branch", "--show-current").stdout.strip() == "work"
    assert _git(source_repo, "rev-parse", "main").stdout.strip() == updated_sha
    assert _git_dir(bare_repo, "rev-parse", "refs/heads/main").stdout.strip() == updated_sha


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
        "scripts/lib/git_provider_auth.sh",
    ):
        _copy_script(repo_root, sandbox_root, relative_path)

    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "Test User",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test User",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "EVANDER_GIT_ROOT": str(git_root),
        }
    )

    upstream_bare = tmp_path / "upstream.git"
    subprocess.run(
        ["git", "init", "--bare", str(upstream_bare)], check=True, text=True, capture_output=True
    )
    source_repo = tmp_path / "source"
    subprocess.run(
        ["git", "init", "-b", "main", str(source_repo)], check=True, text=True, capture_output=True
    )
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
    subprocess.run(
        [str(create_remote_script), "shared-repo"],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )

    bare_repo = git_root / "shared-repo.git"
    _git_dir(
        bare_repo,
        "fetch",
        "--prune",
        str(source_repo),
        "+refs/heads/*:refs/heads/*",
        "+refs/tags/*:refs/tags/*",
    )
    _git_dir(bare_repo, "config", "evander.mirrorSource", str(source_repo))
    _git_dir(bare_repo, "config", "evander.mirrorRemoteName", "upstream")
    _git_dir(bare_repo, "config", "evander.mirrorSyncRemote", "1")
    _git_dir(bare_repo, "config", "evander.mirrorForceLocal", "0")
    _git_dir(bare_repo, "config", "evander.baseBranch", "main")
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
            "EVANDER_GIT_AUTH_ACCESS": "read-only",
            "EVANDER_GIT_AUTH_REPO": str(bare_repo),
            "SSH_ORIGINAL_COMMAND": f"git-upload-pack '{bare_repo}'",
        }
    )
    subprocess.run(
        [str(wrapper_script)], check=True, text=True, capture_output=True, env=wrapper_env
    )

    assert _git_dir(bare_repo, "rev-parse", "refs/heads/main").stdout.strip() == updated_sha
