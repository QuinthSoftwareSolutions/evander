from __future__ import annotations

import subprocess
from pathlib import Path

from evander.config import GatewayConfig
from evander.events.provider_models import GitPushTriggerDefinition
from evander.git_pr_notes import agent_pr_notes_ref_for_session
from evander.triggers.providers.base import TriggerRuntimeContext
from evander.triggers.providers.git_push import (
    GitPushTriggerProvider,
    build_git_push_payloads,
)


_MANAGED_REPO_ID = "mr_0123456789abcdefabcd"


def _configure_managed_repo(
    repo_path: Path,
    *,
    managed_repo_id: str = _MANAGED_REPO_ID,
    default_branch: str = "main",
) -> None:
    for key, value in (
        ("evander.managedRepoId", managed_repo_id),
        ("evander.baseBranch", default_branch),
        ("evander.directoryPath", "root"),
    ):
        subprocess.run(
            ["git", f"--git-dir={repo_path}", "config", key, value],
            check=True,
        )


def test_git_push_provider_generates_event_for_matching_repo_branch(tmp_path: Path) -> None:
    repo_path = (tmp_path / "repo.git").resolve()
    subprocess.run(
        ["git", "init", "--bare", str(repo_path)], check=True, capture_output=True, text=True
    )
    _configure_managed_repo(repo_path)
    trigger = GitPushTriggerDefinition(
        id="git-main",
        display_name="Git main",
        type="trigger:git:push",
        managed_repo_id_glob="mr_012345*",
    )
    provider = GitPushTriggerProvider()
    provider.initialize(
        trigger_definition=trigger,
        runtime_context=TriggerRuntimeContext(
            resolved_config=GatewayConfig(runtime_dir=tmp_path / "runtime")
        ),
    )

    event = provider.handle_ref_update(
        repo_path=str(repo_path),
        old_rev="0" * 40,
        new_rev="1" * 40,
        ref_name="refs/heads/main",
    )

    assert event is not None
    assert event.event_id == f"git-main:main:{'1' * 40}"
    assert event.payload["branch_name"] == "main"
    assert event.payload["commit_hash"] == "1" * 40
    assert event.payload["new_commit_hash"] == "1" * 40
    assert event.payload["old_commit_hash"] == "0" * 40
    assert event.payload["managed_repo_id"] == _MANAGED_REPO_ID
    assert event.payload["default_branch"] == "main"
    assert not any("path" in key for key in event.payload)


def test_git_push_provider_ignores_non_matching_repo_or_ref(tmp_path: Path) -> None:
    repo_path = (tmp_path / "repo.git").resolve()
    subprocess.run(
        ["git", "init", "--bare", str(repo_path)], check=True, capture_output=True, text=True
    )
    other_repo = (tmp_path / "other.git").resolve()
    subprocess.run(
        ["git", "init", "--bare", str(other_repo)], check=True, capture_output=True, text=True
    )
    _configure_managed_repo(repo_path)
    _configure_managed_repo(
        other_repo,
        managed_repo_id="mr_ffffffffffffffffffff",
    )
    trigger = GitPushTriggerDefinition(
        id="git-main",
        display_name="Git main",
        type="trigger:git:push",
        managed_repo_id=_MANAGED_REPO_ID,
    )
    provider = GitPushTriggerProvider()
    provider.initialize(
        trigger_definition=trigger,
        runtime_context=TriggerRuntimeContext(
            resolved_config=GatewayConfig(runtime_dir=tmp_path / "runtime")
        ),
    )

    ignored_repo = provider.handle_ref_update(
        repo_path=str(other_repo),
        old_rev="0" * 40,
        new_rev="1" * 40,
        ref_name="refs/heads/main",
    )
    ignored_tag = provider.handle_ref_update(
        repo_path=str(repo_path),
        old_rev="0" * 40,
        new_rev="2" * 40,
        ref_name="refs/tags/v1.0.0",
    )

    assert ignored_repo is None
    assert ignored_tag is None


def test_git_push_provider_derives_session_metadata_from_agent_branch(tmp_path: Path) -> None:
    repo_path = (tmp_path / "repo.git").resolve()
    subprocess.run(
        ["git", "init", "--bare", str(repo_path)], check=True, capture_output=True, text=True
    )
    _configure_managed_repo(repo_path)
    trigger = GitPushTriggerDefinition(
        id="git-main",
        display_name="Git main",
        type="trigger:git:push",
        managed_repo_id=_MANAGED_REPO_ID,
    )
    provider = GitPushTriggerProvider()
    provider.initialize(
        trigger_definition=trigger,
        runtime_context=TriggerRuntimeContext(
            resolved_config=GatewayConfig(runtime_dir=tmp_path / "runtime")
        ),
    )

    event = provider.handle_ref_update(
        repo_path=str(repo_path),
        old_rev="0" * 40,
        new_rev="1" * 40,
        ref_name="refs/heads/agents/session-1/topic-branch",
    )

    assert event is not None
    assert event.payload["session_id"] == "session-1"
    assert event.payload["review_branch"] == "review/session-1/topic-branch"
    assert event.payload["notes_ref"] == agent_pr_notes_ref_for_session("session-1")
    assert event.payload["managed_repo_id"] == _MANAGED_REPO_ID
    assert event.payload["default_branch"] == "main"


def test_build_git_push_payloads_ignores_shared_notes_ref(
    tmp_path: Path,
) -> None:
    repo_path = (tmp_path / "repo.git").resolve()
    worktree = (tmp_path / "worktree").resolve()
    subprocess.run(
        ["git", "init", "--bare", str(repo_path)], check=True, capture_output=True, text=True
    )
    subprocess.run(["git", "init", str(worktree)], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(worktree), "config", "user.name", "Test User"], check=True)
    subprocess.run(
        ["git", "-C", str(worktree), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(
        ["git", "-C", str(worktree), "remote", "add", "origin", str(repo_path)], check=True
    )
    _configure_managed_repo(repo_path)

    (worktree / "hello.txt").write_text("Hello World", encoding="utf-8")
    subprocess.run(["git", "-C", str(worktree), "add", "hello.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(worktree), "commit", "-m", "Initial"],
        check=True,
        capture_output=True,
        text=True,
    )
    tip_sha = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "-C", str(worktree), "push", "origin", "HEAD:refs/heads/agents/session-1/topic"],
        check=True,
        capture_output=True,
        text=True,
    )

    notes_ref = "refs/notes/agent-pr"
    subprocess.run(
        ["git", "-C", str(worktree), "notes", f"--ref={notes_ref}", "add", "-m", "Summary", "HEAD"],
        check=True,
    )
    old_notes_rev = "0" * 40
    subprocess.run(
        ["git", "-C", str(worktree), "push", "origin", notes_ref],
        check=True,
        capture_output=True,
        text=True,
    )
    new_notes_rev = subprocess.run(
        ["git", f"--git-dir={repo_path}", "rev-parse", notes_ref],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    payloads = build_git_push_payloads(
        repo_path=str(repo_path),
        old_rev=old_notes_rev,
        new_rev=new_notes_rev,
        ref_name=notes_ref,
    )

    assert payloads == []


def test_build_git_push_payloads_maps_per_session_notes_push_to_matching_agent_branch_tip(
    tmp_path: Path,
) -> None:
    repo_path = (tmp_path / "repo.git").resolve()
    worktree = (tmp_path / "worktree").resolve()
    subprocess.run(
        ["git", "init", "--bare", str(repo_path)], check=True, capture_output=True, text=True
    )
    subprocess.run(["git", "init", str(worktree)], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(worktree), "config", "user.name", "Test User"], check=True)
    subprocess.run(
        ["git", "-C", str(worktree), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(
        ["git", "-C", str(worktree), "remote", "add", "origin", str(repo_path)], check=True
    )
    _configure_managed_repo(repo_path)

    (worktree / "hello.txt").write_text("Hello World", encoding="utf-8")
    subprocess.run(["git", "-C", str(worktree), "add", "hello.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(worktree), "commit", "-m", "Initial"],
        check=True,
        capture_output=True,
        text=True,
    )
    tip_sha = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "-C", str(worktree), "push", "origin", "HEAD:refs/heads/agents/session-1/topic"],
        check=True,
        capture_output=True,
        text=True,
    )

    notes_ref = agent_pr_notes_ref_for_session("session-1")
    subprocess.run(
        ["git", "-C", str(worktree), "notes", f"--ref={notes_ref}", "add", "-m", "Summary", "HEAD"],
        check=True,
    )
    old_notes_rev = "0" * 40
    subprocess.run(
        ["git", "-C", str(worktree), "push", "origin", notes_ref],
        check=True,
        capture_output=True,
        text=True,
    )
    new_notes_rev = subprocess.run(
        ["git", f"--git-dir={repo_path}", "rev-parse", notes_ref],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    payloads = build_git_push_payloads(
        repo_path=str(repo_path),
        old_rev=old_notes_rev,
        new_rev=new_notes_rev,
        ref_name=notes_ref,
    )

    assert len(payloads) == 1
    payload = payloads[0]
    assert payload["branch_name"] == "agents/session-1/topic"
    assert payload["notes_ref"] == notes_ref
    assert payload["commit_hash"] == tip_sha
    assert payload["managed_repo_id"] == _MANAGED_REPO_ID
    assert payload["default_branch"] == "main"
    assert payload["event_hash"] == new_notes_rev
