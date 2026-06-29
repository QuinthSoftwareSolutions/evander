from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_sandbox.config import GatewayConfig
from agent_sandbox.effects.providers.base import EffectRuntimeContext
from agent_sandbox.effects.providers.issue_update_state import IssueUpdateStateEffectProvider


class _AdminServiceStub:
    def __init__(self, result: dict[str, object] | None = None) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def update_issue(self, issue_id: str, **kwargs):
        self.calls.append({"issue_id": issue_id, **kwargs})
        if self.result is None:
            raise RuntimeError("result not configured")
        return dict(self.result)


def _build_context(tmp_path: Path, admin_service: _AdminServiceStub) -> EffectRuntimeContext:
    return EffectRuntimeContext(
        resolved_config=GatewayConfig(runtime_dir=tmp_path / "runtime"),
        admin_service=admin_service,
        env={},
    )


def test_issue_update_state_provider_calls_admin_service(tmp_path: Path) -> None:
    admin_service = _AdminServiceStub(
        result={
            "issue_number": "14084",
            "source_type": "local",
            "updated": True,
        }
    )
    provider = IssueUpdateStateEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(
            id="effect-issue-state",
            type="effect:issue:update-state",
            issue_id=" 14084 ",
            state=" In Progress ",
        ),
        runtime_context=_build_context(tmp_path, admin_service),
    )

    output = provider.execute()

    assert admin_service.calls == [
        {"issue_id": "14084", "state": "In Progress", "sync_proxy_cache": True}
    ]
    assert output["success"] is True
    assert output["issue_number"] == "14084"
    assert output["state"] == "In Progress"


def test_issue_update_state_provider_requires_admin_service_method(tmp_path: Path) -> None:
    provider = IssueUpdateStateEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(
            id="effect-issue-state", type="effect:issue:update-state"
        ),
        runtime_context=EffectRuntimeContext(
            resolved_config=GatewayConfig(runtime_dir=tmp_path / "runtime"),
            admin_service=SimpleNamespace(),
            env={},
        ),
    )

    with pytest.raises(RuntimeError, match="AdminRuntimeComponents\\.update_issue is not available"):
        provider.execute(resolved_config={"issue_id": "14084", "state": "In Progress"})


def test_issue_update_state_provider_requires_boolean_sync_proxy_cache(tmp_path: Path) -> None:
    provider = IssueUpdateStateEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(
            id="effect-issue-state",
            type="effect:issue:update-state",
            issue_id="14084",
            state="In Progress",
        ),
        runtime_context=_build_context(tmp_path, _AdminServiceStub(result={"updated": True})),
    )

    with pytest.raises(ValueError, match="sync_proxy_cache must resolve to a boolean"):
        provider.execute(resolved_config={"sync_proxy_cache": "maybe"})
