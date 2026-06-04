from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_sandbox.config import GatewayConfig
from agent_sandbox.effects.providers import ado_post_work_item_comment as provider_module
from agent_sandbox.effects.providers.ado_post_work_item_comment import (
    AdoPostWorkItemCommentEffectProvider,
)
from agent_sandbox.effects.providers.base import EffectRuntimeContext


class _IssueServiceStub:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.post_calls: list[dict[str, Any]] = []

    def post_remote_comment(self, **kwargs: Any) -> dict[str, Any]:
        self.post_calls.append(kwargs)
        return dict(self.result)


def _provider(tmp_path: Path, **effect_fields: Any) -> AdoPostWorkItemCommentEffectProvider:
    provider = AdoPostWorkItemCommentEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(
            id="effect-ado-post-work-item-comment",
            type="effect:ado:post-work-item-comment",
            **effect_fields,
        ),
        runtime_context=EffectRuntimeContext(
            resolved_config=GatewayConfig(runtime_dir=tmp_path / "runtime"),
            provider_config_resolver=object(),
            env={},
        ),
    )
    return provider


def test_ado_post_work_item_comment_posts_comment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _IssueServiceStub(
        {
            "provider": "azure_devops",
            "remote_issue_id": "123",
            "comment": {
                "id": "456",
                "url": "https://dev.azure.com/example/_workitems/edit/123#comment-456",
            },
        }
    )
    monkeypatch.setattr(provider_module, "IssueService", lambda resolver: service)
    provider = _provider(
        tmp_path,
        directory_path="root/example/truman",
        project="Example Industry APP",
        work_item_id="123",
        comment="Please assign this work item to the owner.",
    )

    output = provider.execute()

    assert output == {
        "success": True,
        "provider": "azure_devops",
        "work_item_id": "123",
        "comment_id": "456",
        "comment_url": "https://dev.azure.com/example/_workitems/edit/123#comment-456",
        "comment": {
            "id": "456",
            "url": "https://dev.azure.com/example/_workitems/edit/123#comment-456",
        },
        "message": "Posted comment to Azure DevOps work item 123",
    }
    assert service.post_calls == [
        {
            "provider": "azure_devops",
            "remote_issue_id": "123",
            "directory_path": "root/example/truman",
            "project": "Example Industry APP",
            "comment": "Please assign this work item to the owner.",
        }
    ]
