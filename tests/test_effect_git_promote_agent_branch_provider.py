from __future__ import annotations

from types import SimpleNamespace

import pytest

from evander.effects.providers.base import EffectRuntimeContext
from evander.effects.providers.git_promote_agent_branch import (
    GitPromoteAgentBranchEffectProvider,
)
from evander.events.provider_models import GitPromoteAgentBranchEffectDefinition


class _AdminServiceStub:
    def __init__(self, result: dict[str, object] | None = None) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def promote_agent_branch(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        if self.result is None:
            raise RuntimeError("result not configured")
        return dict(self.result)


def _definition(**overrides: object) -> GitPromoteAgentBranchEffectDefinition:
    payload: dict[str, object] = {
        "id": "effect-promote",
        "display_name": "Promote",
        "type": "effect:git:promote-agent-branch",
        "managed_repo_id": "mr_0123456789abcdefabcd",
        "agent_branch": "agents/session-1/topic",
        "target_branch": "review/session-1/topic",
        "expected_commit_sha": "a" * 40,
    }
    payload.update(overrides)
    return GitPromoteAgentBranchEffectDefinition.model_validate(payload)


def test_git_promote_agent_branch_provider_calls_admin_service_with_pinned_contract() -> None:
    admin_service = _AdminServiceStub(
        result={
            "success": True,
            "commit_sha": "a" * 40,
        }
    )
    provider = GitPromoteAgentBranchEffectProvider()
    provider.initialize(
        _definition(
            managed_repo_id=" mr_0123456789abcdefabcd ",
            agent_branch=" agents/session-1/topic ",
            target_branch=" review/session-1/topic ",
            expected_commit_sha=f" {'a' * 40} ",
        ),
        EffectRuntimeContext(admin_service=admin_service, env={}),
    )

    result = provider.execute()

    assert admin_service.calls == [
        {
            "managed_repo_id": "mr_0123456789abcdefabcd",
            "agent_branch": "agents/session-1/topic",
            "target_branch": "review/session-1/topic",
            "expected_commit_sha": "a" * 40,
            "directory_path": "root",
        }
    ]
    assert result["success"] is True
    assert result["managed_repo_id"] == "mr_0123456789abcdefabcd"
    assert result["target_branch"] == "review/session-1/topic"


def test_git_promote_agent_branch_provider_fails_returned_commit_mismatch() -> None:
    admin_service = _AdminServiceStub(
        result={
            "success": True,
            "commit_sha": "b" * 40,
        }
    )
    provider = GitPromoteAgentBranchEffectProvider()
    provider.initialize(_definition(), EffectRuntimeContext(admin_service=admin_service, env={}))

    result = provider.execute()

    assert result["success"] is False
    assert result["error"] == (f"promoted commit {'b' * 40} did not match expected {'a' * 40}")


def test_git_promote_agent_branch_provider_preserves_source_ref_moved_result() -> None:
    admin_service = _AdminServiceStub(
        result={
            "success": False,
            "reason": "source_ref_moved",
            "message": "Agent branch has moved since this event was queued.",
        }
    )
    provider = GitPromoteAgentBranchEffectProvider()
    provider.initialize(_definition(), EffectRuntimeContext(admin_service=admin_service, env={}))

    result = provider.execute()

    assert result["success"] is False
    assert result["reason"] == "source_ref_moved"


def test_git_promote_agent_branch_provider_requires_admin_service_method() -> None:
    provider = GitPromoteAgentBranchEffectProvider()
    provider.initialize(
        _definition(),
        EffectRuntimeContext(admin_service=SimpleNamespace(), env={}),
    )

    with pytest.raises(RuntimeError, match="Admin runtime promote_agent_branch is not available"):
        provider.execute()
