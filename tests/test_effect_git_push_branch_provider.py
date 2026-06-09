from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_sandbox.config import GatewayConfig
from agent_sandbox.effects.providers.base import EffectRuntimeContext
from agent_sandbox.effects.providers.git_push_branch import GitPushBranchEffectProvider


class _AdminServiceStub:
    def __init__(self, result: dict[str, object] | None = None) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def push_branch(self, **kwargs):
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


def test_git_push_branch_provider_calls_admin_service_with_normalized_arguments(
    tmp_path: Path,
) -> None:
    admin_service = _AdminServiceStub(
        result={
            "success": True,
            "action": "git_push_branch",
            "message": "Pushed branch",
            "local_branch": "review/session-1/work",
            "origin_branch": "feature/topic",
        }
    )
    provider = GitPushBranchEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(
            id="effect-push",
            display_name="Push branch",
            type="effect:git:push-branch",
            host_repo_path=" /trusted/repo ",
            local_branch=" review/session-1/work ",
            origin_branch=" refs/heads/feature/default ",
            canonical_origin_url=" git@ssh.dev.azure.com:v3/org/project/repo ",
            provider=" azure_devops ",
            base_branch=" refs/heads/main ",
            session_id=" session-1 ",
            work_item_id=" 123 ",
        ),
        runtime_context=_build_context(tmp_path, admin_service),
    )

    result = provider.execute(resolved_config={"origin_branch": " feature/topic "})

    assert admin_service.calls == [
        {
            "host_repo_path": "/trusted/repo",
            "local_branch": "review/session-1/work",
            "origin_branch": "feature/topic",
            "canonical_origin_url": "git@ssh.dev.azure.com:v3/org/project/repo",
            "provider": "azure_devops",
            "base_branch": "refs/heads/main",
            "session_id": "session-1",
            "work_item_id": "123",
        }
    ]
    assert result["success"] is True
    assert result["origin_branch"] == "feature/topic"


def test_git_push_branch_provider_requires_admin_service_method(tmp_path: Path) -> None:
    provider = GitPushBranchEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(
            id="effect-push",
            display_name="Push branch",
            type="effect:git:push-branch",
            host_repo_path="/trusted/repo",
            local_branch="review/session-1/work",
            origin_branch="feature/topic",
        ),
        runtime_context=EffectRuntimeContext(
            resolved_config=GatewayConfig(runtime_dir=tmp_path / "runtime"),
            admin_service=SimpleNamespace(),
            env={},
        ),
    )

    with pytest.raises(RuntimeError, match="AdminService\\.push_branch is not available"):
        provider.execute()
