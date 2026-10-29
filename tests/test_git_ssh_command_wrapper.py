from __future__ import annotations

import base64
import os
from pathlib import Path
import subprocess
import threading

import pytest

from evander.git_pr_notes import agent_pr_notes_ref_for_session
from evander.managed_git_refresh import _RefreshServer


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts" / "git_ssh_command_wrapper.sh"


def _git(
    repo: Path,
    *args: str,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        text=True,
        capture_output=True,
        env=env,
    )


def _git_dir(
    repo: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", f"--git-dir={repo}", *args],
        check=check,
        text=True,
        capture_output=True,
    )


def _base_env(git_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "EVANDER_GIT_ROOT": str(git_root),
            "GIT_AUTHOR_NAME": "Managed Git Test",
            "GIT_AUTHOR_EMAIL": "managed-git@example.invalid",
            "GIT_COMMITTER_NAME": "Managed Git Test",
            "GIT_COMMITTER_EMAIL": "managed-git@example.invalid",
        }
    )
    return env


def _create_managed_bare(git_root: Path, name: str) -> Path:
    repo = git_root / f"{name}.git"
    subprocess.run(
        ["git", "init", "--bare", str(repo)],
        check=True,
        text=True,
        capture_output=True,
    )
    hook = repo / "hooks" / "pre-receive"
    hook.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
# EVANDER_GIT_AUTH_ENFORCE is intentionally named for wrapper verification.
while read -r old_sha new_sha ref_name; do
  : "$old_sha" "$new_sha"
  if [[ "${EVANDER_GIT_AUTH_ENFORCE:-0}" != "1" ]]; then
    continue
  fi
  case "$ref_name" in
    "${EVANDER_GIT_AUTH_REF_PREFIX}"*|"${EVANDER_GIT_AUTH_PR_NOTES_REF}")
      ;;
    *)
      echo "Ref '$ref_name' is outside authorized prefix '${EVANDER_GIT_AUTH_REF_PREFIX}'." >&2
      exit 1
      ;;
  esac
done
""",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    from evander.admin.services.managed_git_ref_migration import migrate_private_refs

    def run_git(command, *, input_text=None, check=True):
        return subprocess.run(command, input=input_text, check=check, capture_output=True, text=True)

    migrate_private_refs(run_git, repo)
    return repo


def _write_fake_ssh(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
host_seen=0
command_parts=()
while (($# > 0)); do
  case "$1" in
    -o|-p|-l|-i|-F) shift 2 ;;
    -*) shift ;;
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
printf -v original_command '%s ' "${command_parts[@]}"
export SSH_ORIGINAL_COMMAND="${original_command% }"
exec "${EVANDER_TEST_WRAPPER}"
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _ssh_env(
    env: dict[str, str],
    fake_ssh: Path,
    *,
    repo: Path,
    access: str,
    session_id: str | None = None,
) -> dict[str, str]:
    result = env.copy()
    result.update(
        {
            "GIT_SSH_COMMAND": str(fake_ssh),
            "GIT_SSH_VARIANT": "ssh",
            "EVANDER_TEST_WRAPPER": str(WRAPPER),
            "EVANDER_GIT_AUTH_ACCESS": access,
            "EVANDER_GIT_AUTH_REPO": str(repo),
        }
    )
    if session_id is not None:
        result.update(
            {
                "EVANDER_GIT_AUTH_SESSION_ID": session_id,
                "EVANDER_GIT_AUTH_REF_PREFIX": f"refs/evander/agents/{session_id}/",
                "EVANDER_GIT_AUTH_PR_NOTES_REF": agent_pr_notes_ref_for_session(session_id),
            }
        )
    return result


def _seed_worktree(tmp_path: Path, env: dict[str, str]) -> Path:
    worktree = tmp_path / "worktree"
    subprocess.run(
        ["git", "init", "-b", "main", str(worktree)],
        check=True,
        text=True,
        capture_output=True,
    )
    (worktree / "README.md").write_text("managed git\n", encoding="utf-8")
    _git(worktree, "add", "README.md", env=env)
    _git(worktree, "commit", "-m", "Initial commit", env=env)
    return worktree


def test_exact_read_key_cannot_access_sibling_repository(tmp_path: Path) -> None:
    git_root = tmp_path / "managed-git"
    git_root.mkdir()
    env = _base_env(git_root)
    allowed = _create_managed_bare(git_root, "allowed")
    sibling = _create_managed_bare(git_root, "sibling")
    worktree = _seed_worktree(tmp_path, env)
    _git(worktree, "push", str(allowed), "HEAD:refs/heads/main", env=env)
    _git(worktree, "push", str(sibling), "HEAD:refs/heads/main", env=env)
    fake_ssh = tmp_path / "fake-ssh"
    _write_fake_ssh(fake_ssh)
    read_env = _ssh_env(env, fake_ssh, repo=allowed, access="read-only")

    allowed_read = subprocess.run(
        ["git", "ls-remote", f"ssh://evander-git-host{allowed}", "refs/heads/main"],
        check=False,
        text=True,
        capture_output=True,
        env=read_env,
    )
    sibling_read = subprocess.run(
        ["git", "ls-remote", f"ssh://evander-git-host{sibling}", "refs/heads/main"],
        check=False,
        text=True,
        capture_output=True,
        env=read_env,
    )

    assert allowed_read.returncode == 0
    assert "refs/heads/main" in allowed_read.stdout
    assert sibling_read.returncode != 0
    assert "not authorized for repository" in sibling_read.stderr


def test_prefix_only_read_key_is_rejected(tmp_path: Path) -> None:
    git_root = tmp_path / "managed-git"
    git_root.mkdir()
    env = _base_env(git_root)
    repo = _create_managed_bare(git_root, "allowed")
    fake_ssh = tmp_path / "fake-ssh"
    _write_fake_ssh(fake_ssh)
    read_env = env.copy()
    read_env.update(
        {
            "GIT_SSH_COMMAND": str(fake_ssh),
            "GIT_SSH_VARIANT": "ssh",
            "EVANDER_TEST_WRAPPER": str(WRAPPER),
            "EVANDER_GIT_AUTH_ACCESS": "read-only",
            "EVANDER_GIT_AUTH_REPO_PREFIX_B64": base64.b64encode(str(git_root).encode()).decode(),
        }
    )

    result = subprocess.run(
        ["git", "ls-remote", f"ssh://evander-git-host{repo}"],
        check=False,
        text=True,
        capture_output=True,
        env=read_env,
    )

    assert result.returncode != 0
    assert "missing exact authorized repository metadata" in result.stderr


@pytest.mark.parametrize(
    "destination_ref",
    [
        "refs/evander/agents/other-session/topic",
        "refs/evander/review/topic",
        "refs/evander/team/topic",
        "refs/heads/main",
        "refs/heads/agents/session-1/topic",
        "refs/evander/layout/private-refs-v2",
        "refs/tags/v1",
    ],
)
def test_write_key_cannot_update_host_or_foreign_namespaces(
    tmp_path: Path,
    destination_ref: str,
) -> None:
    git_root = tmp_path / "managed-git"
    git_root.mkdir()
    env = _base_env(git_root)
    repo = _create_managed_bare(git_root, "allowed")
    worktree = _seed_worktree(tmp_path, env)
    fake_ssh = tmp_path / "fake-ssh"
    _write_fake_ssh(fake_ssh)
    write_env = _ssh_env(
        env,
        fake_ssh,
        repo=repo,
        access="write",
        session_id="session-1",
    )
    _git(worktree, "remote", "add", "agent", f"ssh://evander-git-host{repo}")

    before = _git_dir(repo, "show-ref", "--verify", destination_ref, check=False)
    denied = _git(
        worktree,
        "push",
        "--force",
        "agent",
        f"HEAD:{destination_ref}",
        env=write_env,
        check=False,
    )
    missing = _git_dir(repo, "show-ref", "--verify", destination_ref, check=False)

    assert denied.returncode != 0
    assert "outside authorized prefix" in denied.stderr
    assert (missing.returncode, missing.stdout) == (before.returncode, before.stdout)


@pytest.mark.parametrize("legacy_key_metadata", [False, True])
def test_write_key_accepts_only_its_agent_branch_and_session_note(tmp_path: Path, legacy_key_metadata: bool) -> None:
    git_root = tmp_path / "managed-git"
    git_root.mkdir()
    env = _base_env(git_root)
    repo = _create_managed_bare(git_root, "allowed")
    sibling = _create_managed_bare(git_root, "sibling")
    worktree = _seed_worktree(tmp_path, env)
    fake_ssh = tmp_path / "fake-ssh"
    _write_fake_ssh(fake_ssh)
    write_env = _ssh_env(
        env,
        fake_ssh,
        repo=repo,
        access="write",
        session_id="session-1",
    )
    if legacy_key_metadata:
        write_env["EVANDER_GIT_AUTH_REF_PREFIX"] = "refs/heads/agents/session-1/"
    _git(worktree, "remote", "add", "agent", f"ssh://evander-git-host{repo}")
    _git(worktree, "remote", "add", "sibling", f"ssh://evander-git-host{sibling}")
    agent_ref = "refs/evander/agents/session-1/topic"
    notes_ref = agent_pr_notes_ref_for_session("session-1")
    _git(worktree, "notes", f"--ref={notes_ref}", "add", "-m", "Pinned PR note", "HEAD", env=env)

    _git(worktree, "push", "agent", f"HEAD:{agent_ref}", notes_ref, env=write_env)
    sibling_push = _git(
        worktree,
        "push",
        "sibling",
        f"HEAD:{agent_ref}",
        env=write_env,
        check=False,
    )

    assert _git_dir(repo, "show-ref", "--verify", agent_ref).returncode == 0
    assert _git_dir(repo, "show-ref", "--verify", notes_ref).returncode == 0
    assert sibling_push.returncode != 0
    assert "not authorized for repository" in sibling_push.stderr


def test_read_key_cannot_push_or_run_non_git_service_commands(tmp_path: Path) -> None:
    git_root = tmp_path / "managed-git"
    git_root.mkdir()
    env = _base_env(git_root)
    repo = _create_managed_bare(git_root, "allowed")
    worktree = _seed_worktree(tmp_path, env)
    fake_ssh = tmp_path / "fake-ssh"
    _write_fake_ssh(fake_ssh)
    read_env = _ssh_env(env, fake_ssh, repo=repo, access="read-only")
    _git(worktree, "remote", "add", "agent", f"ssh://evander-git-host{repo}")

    push = _git(
        worktree,
        "push",
        "agent",
        "HEAD:refs/evander/agents/session-1/topic",
        env=read_env,
        check=False,
    )
    command_env = read_env.copy()
    command_env["SSH_ORIGINAL_COMMAND"] = f"git config --file '{repo}/config' user.name attacker"
    command = subprocess.run(
        [str(WRAPPER)],
        check=False,
        text=True,
        capture_output=True,
        env=command_env,
    )

    assert push.returncode != 0
    assert "read-only" in push.stderr
    assert command.returncode != 0
    assert "Unsupported Git SSH command" in command.stderr


def test_wrapper_does_not_load_secrets_or_refresh_mirrors(tmp_path: Path) -> None:
    git_root = tmp_path / "managed-git"
    git_root.mkdir()
    env = _base_env(git_root)
    repo = _create_managed_bare(git_root, "allowed")
    secret_file = tmp_path / "secrets.env"
    secret_file.write_text("AZURE_DEVOPS_PR_PAT=must-not-reach-git-shell\n", encoding="utf-8")
    _git_dir(repo, "config", "evander.secretsEnvFile", str(secret_file))
    _git_dir(repo, "config", "evander.mirrorSource", str(tmp_path / "source"))
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    shell_log = tmp_path / "git-shell.log"
    fake_shell = fake_bin / "git-shell"
    fake_shell.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"printf '%s' \"${{AZURE_DEVOPS_PR_PAT:-}}\" > '{shell_log}'\n",
        encoding="utf-8",
    )
    fake_shell.chmod(0o755)
    wrapper_env = env.copy()
    wrapper_env.pop("AZURE_DEVOPS_PR_PAT", None)
    wrapper_env.update(
        {
            "PATH": f"{fake_bin}:{wrapper_env['PATH']}",
            "EVANDER_GIT_AUTH_ACCESS": "read-only",
            "EVANDER_GIT_AUTH_REPO": str(repo),
            "EVANDER_SECRETS_ENV_FILE": str(secret_file),
            "SSH_ORIGINAL_COMMAND": f"git-upload-pack '{repo}'",
        }
    )

    result = subprocess.run(
        [str(WRAPPER)],
        check=False,
        text=True,
        capture_output=True,
        env=wrapper_env,
    )
    wrapper_text = WRAPPER.read_text(encoding="utf-8")

    assert result.returncode == 0
    assert shell_log.read_text(encoding="utf-8") == ""
    assert "mirror_issue_repo" not in wrapper_text
    assert "evander.mirrorSource" not in wrapper_text
    assert "EVANDER_SECRETS_ENV_FILE" not in wrapper_text


def test_required_refresh_runs_before_managed_git_read(tmp_path: Path) -> None:
    class FakeBroker:
        def __init__(self) -> None:
            self.requests: list[str] = []

        def authorize_peer(self, _connection: object) -> None:
            return None

        def refresh(self, *, managed_repo_id: str) -> dict[str, str]:
            self.requests.append(managed_repo_id)
            return {"managed_repo_id": managed_repo_id, "refreshed_at": "now"}

    git_root = tmp_path / "managed-git"
    git_root.mkdir()
    env = _base_env(git_root)
    repo_id = "mr_0123456789abcdef0123"
    repo = _create_managed_bare(git_root, repo_id)
    worktree = _seed_worktree(tmp_path, env)
    _git(worktree, "push", str(repo), "HEAD:refs/heads/main", env=env)
    fake_ssh = tmp_path / "fake-ssh"
    _write_fake_ssh(fake_ssh)
    broker = FakeBroker()
    socket_path = tmp_path / "refresh.sock"
    server = _RefreshServer(socket_path, broker)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    read_env = _ssh_env(env, fake_ssh, repo=repo, access="read-only")
    read_env.update(
        {
            "EVANDER_GIT_REFRESH_REQUIRED": "1",
            "EVANDER_GIT_REFRESH_SOCKET": str(socket_path),
        }
    )

    try:
        result = subprocess.run(
            ["git", "ls-remote", f"ssh://evander-git-host{repo}", "refs/heads/main"],
            check=False,
            text=True,
            capture_output=True,
            env=read_env,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result.returncode == 0, result.stderr
    assert broker.requests == [repo_id]


def test_required_refresh_failure_refuses_stale_refs(tmp_path: Path) -> None:
    git_root = tmp_path / "managed-git"
    git_root.mkdir()
    env = _base_env(git_root)
    repo = _create_managed_bare(git_root, "mr_0123456789abcdef0123")
    fake_ssh = tmp_path / "fake-ssh"
    _write_fake_ssh(fake_ssh)
    read_env = _ssh_env(env, fake_ssh, repo=repo, access="read-only")
    read_env.update(
        {
            "EVANDER_GIT_REFRESH_REQUIRED": "1",
            "EVANDER_GIT_REFRESH_SOCKET": str(tmp_path / "missing.sock"),
        }
    )

    result = subprocess.run(
        ["git", "ls-remote", f"ssh://evander-git-host{repo}"],
        check=False,
        text=True,
        capture_output=True,
        env=read_env,
    )

    assert result.returncode != 0
    assert "Refusing to serve stale managed Git refs" in result.stderr


def test_managed_repository_read_requires_refresh_without_new_key_metadata(tmp_path: Path) -> None:
    git_root = tmp_path / "managed-git"
    git_root.mkdir()
    env = _base_env(git_root)
    repo = _create_managed_bare(git_root, "mr_0123456789abcdef0123")
    fake_ssh = tmp_path / "fake-ssh"
    _write_fake_ssh(fake_ssh)
    read_env = _ssh_env(env, fake_ssh, repo=repo, access="read-only")
    read_env["EVANDER_GIT_REFRESH_SOCKET"] = str(tmp_path / "missing.sock")

    result = subprocess.run(
        ["git", "ls-remote", f"ssh://evander-git-host{repo}"],
        check=False,
        text=True,
        capture_output=True,
        env=read_env,
    )

    assert result.returncode != 0
    assert "Refusing to serve stale managed Git refs" in result.stderr
