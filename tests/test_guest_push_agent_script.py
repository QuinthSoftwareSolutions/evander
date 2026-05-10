from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUSH_HELPER = ROOT / "scripts" / "guest" / "push_agent.sh"
SYNC_BASE_HELPER = ROOT / "scripts" / "guest" / "sync_base.sh"
NOTE_HELPER = ROOT / "scripts" / "guest" / "agent_pr_note.sh"
ISSUE_BRANCH = "feature/issue-1"
SESSION_ID = "issue-1-session"
NOTES_REF = "refs/notes/agent-pr/test"
WORK_BRANCH = "work"


def _run(
    args: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=check, capture_output=True, text=True)


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return _run(["git", "-C", str(repo), *args], check=check)


def _git_dir(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return _run(["git", f"--git-dir={repo}", *args], check=check)


def _install_helpers(repo: Path) -> None:
    bin_dir = repo / ".agent-sandbox" / "bin"
    bin_dir.mkdir(parents=True)
    for source, destination_name in (
        (NOTE_HELPER, "agent-pr-note"),
        (PUSH_HELPER, "push-agent"),
        (SYNC_BASE_HELPER, "sync-base"),
    ):
        destination = bin_dir / destination_name
        destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        destination.chmod(0o755)
    _git(
        repo,
        "config",
        "alias.agent-pr-note",
        '!f(){ repo_root=$(git rev-parse --show-toplevel); "${repo_root}/.agent-sandbox/bin/agent-pr-note" "$@"; }; f',
    )
    _git(
        repo,
        "config",
        "alias.push-agent",
        '!f(){ repo_root=$(git rev-parse --show-toplevel); "${repo_root}/.agent-sandbox/bin/push-agent" "$@"; }; f',
    )
    _git(
        repo,
        "config",
        "alias.sync-base",
        '!f(){ repo_root=$(git rev-parse --show-toplevel); "${repo_root}/.agent-sandbox/bin/sync-base" "$@"; }; f',
    )


def _create_issue_repos(
    tmp_path: Path,
    *,
    configure_issue_number: bool = True,
    configure_base_branch: bool = True,
) -> tuple[Path, Path, Path]:
    source = tmp_path / "source"
    origin = tmp_path / "origin.git"
    agent = tmp_path / "agent"
    dropbox = tmp_path / "dropbox.git"
    source.mkdir()

    _git(source, "init")
    _git(source, "config", "user.name", "Test User")
    _git(source, "config", "user.email", "test@example.com")
    (source / "file.txt").write_text("base\n", encoding="utf-8")
    _git(source, "add", "file.txt")
    _git(source, "commit", "-m", "Initial")
    _git(source, "branch", "-M", "main")

    _run(["git", "init", "--bare", str(origin)])
    _git(source, "remote", "add", "origin", str(origin))
    _git(source, "push", "origin", "main")
    _git(source, "checkout", "-b", ISSUE_BRANCH)
    _git(source, "push", "origin", ISSUE_BRANCH)

    _run(["git", "clone", "--branch", ISSUE_BRANCH, str(origin), str(agent)])
    _git(agent, "switch", "-c", WORK_BRANCH)
    _git(agent, "config", "user.name", "Truman Bot")
    _git(agent, "config", "user.email", "truman@example.com")
    _git(agent, "config", "agent-sandbox.sessionId", SESSION_ID)
    if configure_issue_number:
        _git(agent, "config", "agent-sandbox.issueNumber", "1")
        _git(agent, "config", "agent-sandbox.destinationBranch", ISSUE_BRANCH)
    _git(agent, "config", "agent-sandbox.branchSuffix", "vm-test")
    _git(agent, "config", "agent-sandbox.prNotesRef", NOTES_REF)
    if configure_base_branch:
        _git(agent, "config", "agent-sandbox.baseBranch", "main")
    _git(agent, "config", "agent-sandbox.workBranch", WORK_BRANCH)
    _install_helpers(agent)

    _run(["git", "init", "--bare", str(dropbox)])
    _git(agent, "remote", "add", "agent", str(dropbox))
    return source, agent, dropbox


def _commit_agent_work(agent: Path) -> str:
    (agent / "agent.txt").write_text("agent work\n", encoding="utf-8")
    _git(agent, "add", "agent.txt")
    _git(agent, "commit", "-m", "Agent work")
    _git(agent, "agent-pr-note", "set", "-m", "Agent summary")
    return _git(agent, "rev-parse", "HEAD").stdout.strip()


def test_push_agent_pushes_branch_and_notes_when_destination_is_contained(
    tmp_path: Path,
) -> None:
    _, agent, dropbox = _create_issue_repos(tmp_path)
    agent_sha = _commit_agent_work(agent)

    _git(agent, "push-agent")

    pushed_ref = f"refs/heads/agents/{SESSION_ID}/{WORK_BRANCH}-vm-test"
    assert _git_dir(dropbox, "rev-parse", pushed_ref).stdout.strip() == agent_sha
    assert (
        _git_dir(dropbox, "notes", f"--ref={NOTES_REF}", "show", agent_sha).stdout.strip()
        == "Agent summary"
    )


def test_push_agent_refuses_when_destination_branch_has_unmerged_commits(
    tmp_path: Path,
) -> None:
    source, agent, dropbox = _create_issue_repos(
        tmp_path,
        configure_issue_number=False,
        configure_base_branch=False,
    )
    _commit_agent_work(agent)

    (source / "human.txt").write_text("human work\n", encoding="utf-8")
    _git(source, "add", "human.txt")
    _git(source, "commit", "-m", "Human work")
    _git(source, "push", "origin", ISSUE_BRANCH)

    completed = _git(agent, "push-agent", check=False)

    assert completed.returncode == 1
    assert (
        "Refusing to push: destination branch origin/feature/issue-1 has 1 commit(s)"
        in completed.stderr
    )
    assert "git merge origin/feature/issue-1" in completed.stderr
    refs = _git_dir(
        dropbox,
        "for-each-ref",
        "--format=%(refname)",
        "refs/heads/agents",
    ).stdout.strip()
    assert refs == ""


def test_push_agent_refuses_when_base_branch_has_unmerged_commits(
    tmp_path: Path,
) -> None:
    source, agent, dropbox = _create_issue_repos(tmp_path)
    _commit_agent_work(agent)

    _git(source, "checkout", "main")
    (source / "base.txt").write_text("base update\n", encoding="utf-8")
    _git(source, "add", "base.txt")
    _git(source, "commit", "-m", "Base work")
    _git(source, "push", "origin", "main")

    completed = _git(agent, "push-agent", check=False)

    assert completed.returncode == 1
    assert "Refusing to push: base branch origin/main has 1 commit(s)" in completed.stderr
    assert "git sync-base" in completed.stderr
    refs = _git_dir(
        dropbox,
        "for-each-ref",
        "--format=%(refname)",
        "refs/heads/agents",
    ).stdout.strip()
    assert refs == ""


def test_sync_base_merges_configured_base_branch_into_work_branch(tmp_path: Path) -> None:
    source, agent, _ = _create_issue_repos(tmp_path)
    _commit_agent_work(agent)

    _git(source, "checkout", "main")
    (source / "base.txt").write_text("base update\n", encoding="utf-8")
    _git(source, "add", "base.txt")
    _git(source, "commit", "-m", "Base work")
    _git(source, "push", "origin", "main")

    _git(agent, "sync-base")

    assert _git(agent, "branch", "--show-current").stdout.strip() == WORK_BRANCH
    assert (
        _git(agent, "merge-base", "--is-ancestor", "origin/main", "HEAD").returncode == 0
    )
