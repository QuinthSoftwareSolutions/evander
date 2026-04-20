from __future__ import annotations

import subprocess
from pathlib import Path

from agent_sandbox.config import GatewayConfig
from agent_sandbox.events.provider_models import GitPushTriggerDefinition
from agent_sandbox.triggers.providers.base import TriggerRuntimeContext
from agent_sandbox.triggers.providers.git_push import GitPushTriggerProvider, build_git_push_payloads


def test_git_push_provider_generates_event_for_matching_repo_branch(tmp_path: Path) -> None:
    repo_path = (tmp_path / "repo.git").resolve()
    subprocess.run(["git", "init", "--bare", str(repo_path)], check=True, capture_output=True, text=True)
    subprocess.run(["git", f"--git-dir={repo_path}", "config", "agent-sandbox.trustedRepoPath", "/src/repo"], check=True)
    subprocess.run(["git", f"--git-dir={repo_path}", "config", "agent-sandbox.baseBranch", "main"], check=True)
    trigger = GitPushTriggerDefinition(
        id="git-main",
        display_name="Git main",
        type="trigger:git:push",
        path_glob=str(tmp_path / "*.git"),
    )
    provider = GitPushTriggerProvider()
    provider.initialize(
        trigger_definition=trigger,
        runtime_context=TriggerRuntimeContext(resolved_config=GatewayConfig(runtime_dir=tmp_path / "runtime")),
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
    assert event.payload["repo_name"] == "repo"
    assert event.payload["trusted_repo_path"] == "/src/repo"
    assert event.payload["base_branch"] == "main"


def test_git_push_provider_ignores_non_matching_repo_or_ref(tmp_path: Path) -> None:
    repo_path = (tmp_path / "repo.git").resolve()
    repo_path.mkdir(parents=True)
    other_repo = (tmp_path / "other.git").resolve()
    other_repo.mkdir(parents=True)
    trigger = GitPushTriggerDefinition(
        id="git-main",
        display_name="Git main",
        type="trigger:git:push",
        path_to_repo=str(repo_path),
    )
    provider = GitPushTriggerProvider()
    provider.initialize(
        trigger_definition=trigger,
        runtime_context=TriggerRuntimeContext(resolved_config=GatewayConfig(runtime_dir=tmp_path / "runtime")),
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
    subprocess.run(["git", "init", "--bare", str(repo_path)], check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", f"--git-dir={repo_path}", "config", "agent-sandbox.canonicalOriginUrl", "https://example.com/org/repo.git"],
        check=True,
    )
    trigger = GitPushTriggerDefinition(
        id="git-main",
        display_name="Git main",
        type="trigger:git:push",
        path_to_repo=str(repo_path),
    )
    provider = GitPushTriggerProvider()
    provider.initialize(
        trigger_definition=trigger,
        runtime_context=TriggerRuntimeContext(resolved_config=GatewayConfig(runtime_dir=tmp_path / "runtime")),
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
    assert event.payload["canonical_origin_url"] == "https://example.com/org/repo.git"


def test_build_git_push_payloads_maps_notes_push_to_matching_agent_branch_tip(tmp_path: Path) -> None:
    repo_path = (tmp_path / "repo.git").resolve()
    worktree = (tmp_path / "worktree").resolve()
    subprocess.run(["git", "init", "--bare", str(repo_path)], check=True, capture_output=True, text=True)
    subprocess.run(["git", "init", str(worktree)], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(worktree), "config", "user.name", "Test User"], check=True)
    subprocess.run(["git", "-C", str(worktree), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(worktree), "remote", "add", "origin", str(repo_path)], check=True)
    subprocess.run(["git", f"--git-dir={repo_path}", "config", "agent-sandbox.trustedRepoPath", "/trusted/repo"], check=True)
    subprocess.run(["git", f"--git-dir={repo_path}", "config", "agent-sandbox.baseBranch", "main"], check=True)

    (worktree / "hello.txt").write_text("Hello World", encoding="utf-8")
    subprocess.run(["git", "-C", str(worktree), "add", "hello.txt"], check=True)
    subprocess.run(["git", "-C", str(worktree), "commit", "-m", "Initial"], check=True, capture_output=True, text=True)
    tip_sha = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "-C", str(worktree), "push", "origin", f"HEAD:refs/heads/agents/session-1/topic"],
        check=True,
        capture_output=True,
        text=True,
    )

    subprocess.run(["git", "-C", str(worktree), "notes", "--ref=agent-pr", "add", "-m", "Summary", "HEAD"], check=True)
    old_notes_rev = "0" * 40
    subprocess.run(
        ["git", "-C", str(worktree), "push", "origin", "refs/notes/agent-pr"],
        check=True,
        capture_output=True,
        text=True,
    )
    new_notes_rev = subprocess.run(
        ["git", f"--git-dir={repo_path}", "rev-parse", "refs/notes/agent-pr"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    payloads = build_git_push_payloads(
        repo_path=str(repo_path),
        old_rev=old_notes_rev,
        new_rev=new_notes_rev,
        ref_name="refs/notes/agent-pr",
    )

    assert len(payloads) == 1
    payload = payloads[0]
    assert payload["branch_name"] == "agents/session-1/topic"
    assert payload["session_id"] == "session-1"
    assert payload["review_branch"] == "review/session-1/topic"
    assert payload["commit_hash"] == tip_sha
    assert payload["event_hash"] == new_notes_rev
    assert payload["note_target_commit_hash"] == tip_sha
