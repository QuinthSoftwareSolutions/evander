from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_sandbox.config import GatewayConfig
from agent_sandbox.effects.providers.base import EffectRuntimeContext
from agent_sandbox.effects.providers.pr_create import PrCreateEffectProvider


class _AdminServiceStub:
    def __init__(self, pr_create_result: dict[str, object] | None = None) -> None:
        self.pr_create_result = pr_create_result
        self.calls: list[dict[str, object]] = []

    def pr_create(self, **kwargs):
        self.calls.append(kwargs)
        if self.pr_create_result is None:
            raise RuntimeError("pr_create_result not configured")
        return dict(self.pr_create_result)


def _build_context(tmp_path: Path, admin_service: _AdminServiceStub) -> EffectRuntimeContext:
    return EffectRuntimeContext(
        resolved_config=GatewayConfig(runtime_dir=tmp_path / "runtime"),
        admin_service=admin_service,
        env={},
    )


def test_pr_create_provider_calls_admin_service_with_normalized_arguments(tmp_path: Path) -> None:
    admin_service = _AdminServiceStub(
        pr_create_result={
            "success": True,
            "action": "pr_create",
            "message": "Created PR",
            "pr_id": "123",
            "pr_url": "https://example.test/pr/123",
        }
    )
    provider = PrCreateEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(
            id="effect-pr",
            display_name="Create PR",
            type="effect:pr:create",
            host_repo_path="  /default/repo  ",
            local_branch="feature/default",
            canonical_origin_url="  git@ssh.dev.azure.com:v3/example/project/repo  ",
            provider="github",
            project="  Example Industry APP  ",
            base_branch="main",
            session_id="session-default",
            work_item_id="WI-1",
            title="Default title",
            body="Default body",
            draft=None,
            fill=True,
        ),
        runtime_context=_build_context(tmp_path, admin_service),
    )

    result = provider.execute(
        resolved_config={
            "repo_path": "  /override/repo  ",
            "origin_branch": "  review/session-1  ",
            "title": "  Override title  ",
            "body": "  Override body  ",
            "draft": "false",
            "fill": "0",
        }
    )

    assert admin_service.calls == [
        {
            "host_repo_path": "/override/repo",
            "local_branch": "feature/default",
            "origin_branch": "review/session-1",
            "canonical_origin_url": "git@ssh.dev.azure.com:v3/example/project/repo",
            "provider": "github",
            "project": "Example Industry APP",
            "base_branch": "main",
            "session_id": "session-default",
            "work_item_id": "WI-1",
            "title": "Override title",
            "body": "Override body",
            "body_file": None,
            "draft": False,
            "fill": False,
        }
    ]
    assert result["success"] is True
    assert result["action"] == "pr_create"
    assert result["message"] == "Created PR"
    assert result["pr_id"] == "123"
    assert result["pr_url"] == "https://example.test/pr/123"


def test_pr_create_provider_requires_admin_service_method(tmp_path: Path) -> None:
    admin_service = SimpleNamespace()
    provider = PrCreateEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(id="effect-pr", type="effect:pr:create"),
        runtime_context=EffectRuntimeContext(
            resolved_config=GatewayConfig(runtime_dir=tmp_path / "runtime"),
            admin_service=admin_service,
            env={},
        ),
    )

    with pytest.raises(RuntimeError, match="AdminService\\.pr_create is not available"):
        provider.execute(resolved_config={"repo_path": "/repo"})
