from __future__ import annotations

from pathlib import Path

from agent_sandbox.config import GatewayConfig
from agent_sandbox.triggers.models import GitPushTriggerDefinition
from agent_sandbox.triggers.providers.base import TriggerRuntimeContext
from agent_sandbox.triggers.providers.git_push import GitPushTriggerProvider


def test_git_push_provider_generates_event_for_matching_repo_branch(tmp_path: Path) -> None:
    repo_path = (tmp_path / "repo.git").resolve()
    repo_path.mkdir(parents=True)
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
        ref_name="refs/heads/main",
    )

    assert event is not None
    assert event.event_id == f"git-main:main:{'1' * 40}"
    assert event.payload["branch_name"] == "main"
    assert event.payload["commit_hash"] == "1" * 40


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
