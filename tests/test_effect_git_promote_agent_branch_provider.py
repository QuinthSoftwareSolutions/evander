from __future__ import annotations

from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from evander.config import GatewayConfig
from evander.effects.providers.base import EffectRuntimeContext
from evander.effects.providers.git_promote_agent_branch import (
    GitPromoteAgentBranchEffectProvider,
)


class _AdminServiceStub:
    def __init__(self, result: dict[str, object] | None = None) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def promote_agent_branch(self, **kwargs):
        self.calls.append(kwargs)
        if self.result is None:
            raise RuntimeError("result not configured")
        return dict(self.result)


def _build_context(tmp_path: Path, admin_service: _AdminServiceStub) -> EffectRuntimeContext:
    return EffectRuntimeContext(
        resolved_config=GatewayConfig(runtime_dir=tmp_path / "runtime"),
        admin_service=admin_service,
        env={},
    )


def test_git_promote_agent_branch_provider_calls_admin_service_with_normalized_arguments(
    tmp_path: Path,
) -> None:
    admin_service = _AdminServiceStub(
        result={
            "success": True,
            "action": "git_promote_agent_branch",
            "message": "Promoted branch",
            "host_repo_path": "/trusted/repo",
            "bare_repo_path": "/git-remotes/sample.git",
            "agent_branch": "agents/session-1/topic",
            "review_branch": "review/session-1/topic",
            "target_branch": "review/session-1/topic",
            "commit_sha": "cafef00d",
            "remote_name": "agent-dropbox",
        }
    )
    provider = GitPromoteAgentBranchEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(
            id="effect-promote",
            display_name="Promote",
            type="effect:git:promote-agent-branch",
            host_repo_path=" /trusted/repo ",
            bare_repo_name=" sample ",
            agent_branch=" agents/session-1/topic ",
            review_branch=None,
            target_branch=None,
            expected_commit_sha=None,
            remote_name=" agent-dropbox-auto ",
        ),
        runtime_context=_build_context(tmp_path, admin_service),
    )

    result = provider.execute(resolved_config={"review_branch": " review/session-1/topic "})

    assert admin_service.calls == [
        {
            "host_repo_path": "/trusted/repo",
            "bare_repo_path": None,
            "bare_repo_name": "sample",
            "agent_branch": "agents/session-1/topic",
            "review_branch": "review/session-1/topic",
            "target_branch": None,
            "expected_commit_sha": None,
            "remote_name": "agent-dropbox-auto",
        }
    ]
    assert result["success"] is True
    assert result["review_branch"] == "review/session-1/topic"
    assert result["commit_sha"] == "cafef00d"


def test_git_promote_agent_branch_provider_passes_target_branch_and_checks_commit(
    tmp_path: Path,
) -> None:
    admin_service = _AdminServiceStub(
        result={
            "success": True,
            "action": "git_promote_agent_branch",
            "message": "Promoted branch",
            "host_repo_path": "/trusted/repo",
            "bare_repo_path": "/git-remotes/sample.git",
            "agent_branch": "agents/session-1/release",
            "target_branch": "team/team/releases/release-1",
            "review_branch": "team/team/releases/release-1",
            "commit_sha": "708b1b89",
            "remote_name": "agent-dropbox",
        }
    )
    provider = GitPromoteAgentBranchEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(
            id="effect-promote",
            type="effect:git:promote-agent-branch",
            host_repo_path="/trusted/repo",
            bare_repo_path="/git-remotes/sample.git",
            agent_branch="agents/session-1/release",
            target_branch="team/team/releases/release-1",
            expected_commit_sha="708b1b89",
        ),
        runtime_context=_build_context(tmp_path, admin_service),
    )

    result = provider.execute()

    assert admin_service.calls == [
        {
            "host_repo_path": "/trusted/repo",
            "bare_repo_path": "/git-remotes/sample.git",
            "bare_repo_name": None,
            "agent_branch": "agents/session-1/release",
            "review_branch": None,
            "target_branch": "team/team/releases/release-1",
            "expected_commit_sha": "708b1b89",
            "remote_name": None,
        }
    ]
    assert result["success"] is True
    assert result["target_branch"] == "team/team/releases/release-1"


def test_git_promote_agent_branch_provider_fails_expected_commit_mismatch(
    tmp_path: Path,
) -> None:
    admin_service = _AdminServiceStub(
        result={
            "success": True,
            "action": "git_promote_agent_branch",
            "commit_sha": "badc0de",
        }
    )
    provider = GitPromoteAgentBranchEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(
            id="effect-promote",
            type="effect:git:promote-agent-branch",
            host_repo_path="/trusted/repo",
            bare_repo_path="/git-remotes/sample.git",
            agent_branch="agents/session-1/release",
            target_branch="team/team/releases/release-1",
            expected_commit_sha="708b1b89",
        ),
        runtime_context=_build_context(tmp_path, admin_service),
    )

    result = provider.execute()

    assert result["success"] is False
    assert result["error"] == "promoted commit badc0de did not match expected 708b1b89"


def test_git_promote_agent_branch_provider_publishes_exact_recovery_target_to_bare(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    bare = tmp_path / "source.git"
    subprocess.run(
        ["git", "init", "-b", "main", str(source)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(["git", "-C", str(source), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(source), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    (source / "value.txt").write_text("old\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "add", "value.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(source), "commit", "-m", "old"],
        check=True,
        capture_output=True,
        text=True,
    )
    old_commit = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (source / "value.txt").write_text("recovered\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "add", "value.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(source), "commit", "-m", "recovered"],
        check=True,
        capture_output=True,
        text=True,
    )
    recovered_commit = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "clone", "--bare", str(source), str(bare)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            "git",
            f"--git-dir={bare}",
            "update-ref",
            "refs/heads/team/team/releases/release-1",
            old_commit,
        ],
        check=True,
    )

    admin_service = _AdminServiceStub(
        result={
            "success": True,
            "commit_sha": recovered_commit,
            "target_branch": "team/team/releases/release-1",
        }
    )
    provider = GitPromoteAgentBranchEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(
            id="effect-promote-recovery",
            type="effect:git:promote-agent-branch",
            host_repo_path=str(source),
            bare_repo_path=str(bare),
            agent_branch="agents/session-1/recovered",
            target_branch="team/team/releases/release-1",
            expected_commit_sha=recovered_commit,
            publish_target_to_bare=True,
        ),
        runtime_context=_build_context(tmp_path, admin_service),
    )

    result = provider.execute()

    assert result["success"] is True
    assert result["published_target_to_bare"] is True
    assert result["published_bare_repo_path"] == str(bare)
    assert subprocess.run(
        [
            "git",
            f"--git-dir={bare}",
            "rev-parse",
            "refs/heads/team/team/releases/release-1",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == recovered_commit


def test_git_promote_agent_branch_provider_requires_admin_service_method(tmp_path: Path) -> None:
    provider = GitPromoteAgentBranchEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(
            id="effect-promote", type="effect:git:promote-agent-branch"
        ),
        runtime_context=EffectRuntimeContext(
            resolved_config=GatewayConfig(runtime_dir=tmp_path / "runtime"),
            admin_service=SimpleNamespace(),
            env={},
        ),
    )

    with pytest.raises(RuntimeError, match="Admin runtime promote_agent_branch is not available"):
        provider.execute(
            resolved_config={
                "host_repo_path": "/trusted/repo",
                "bare_repo_path": "/git-remotes/sample.git",
                "agent_branch": "agents/session-1/topic",
            }
        )
