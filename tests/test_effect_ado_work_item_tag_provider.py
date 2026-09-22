from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from evander.config import GatewayConfig
from evander.effects.providers import ado_work_item_tag as provider_module
from evander.effects.providers.ado_work_item_tag import (
    AdoAddWorkItemTagEffectProvider,
    AdoRemoveWorkItemTagEffectProvider,
    AdoWorkItemTagEffectProvider,
)
from evander.effects.providers.base import EffectRuntimeContext


class _IssueServiceStub:
    def __init__(
        self,
        *,
        read_payload: dict[str, Any],
        update_result: dict[str, Any] | None = None,
    ) -> None:
        self.read_payload = read_payload
        self.update_result = update_result or {}
        self.read_calls: list[dict[str, Any]] = []
        self.update_calls: list[dict[str, Any]] = []

    def read_remote_issue(self, **kwargs: Any) -> dict[str, Any]:
        self.read_calls.append(kwargs)
        return dict(self.read_payload)

    def update_remote_issue(self, **kwargs: Any) -> dict[str, Any]:
        self.update_calls.append(kwargs)
        return dict(self.update_result)


def _provider(
    provider_class: type[AdoWorkItemTagEffectProvider],
    tmp_path: Path,
    *,
    tag: str,
) -> AdoWorkItemTagEffectProvider:
    provider = provider_class()
    provider.initialize(
        effect_definition=SimpleNamespace(
            id="effect-ado-work-item-tag",
            type=provider_class.NODE_TYPE,
            directory_path="root/example/evander",
            project="Example Industry App",
            work_item_id="123",
            tag=tag,
            sync_proxy_cache=False,
        ),
        runtime_context=EffectRuntimeContext(
            resolved_config=GatewayConfig(runtime_dir=tmp_path / "runtime"),
            provider_config_resolver=object(),
            env={},
        ),
    )
    return provider


def test_ado_add_work_item_tag_updates_when_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _IssueServiceStub(
        read_payload={"remote_issue_id": "123", "fields": {"System.Tags": "auto-dev; backend"}},
        update_result={"provider": "azure_devops", "remote_issue_id": "123", "updated": True},
    )
    monkeypatch.setattr(provider_module, "IssueService", lambda resolver: service)
    provider = _provider(AdoAddWorkItemTagEffectProvider, tmp_path, tag="ready")

    output = provider.execute()

    assert output["success"] is True
    assert output["updated"] is True
    assert output["previous_tags"] == ["auto-dev", "backend"]
    assert output["tags"] == ["auto-dev", "backend", "ready"]
    assert output["added_tags"] == ["ready"]
    assert output["removed_tags"] == []
    assert service.read_calls == [
        {
            "provider": "azure_devops",
            "remote_issue_id": "123",
            "directory_path": "root/example/evander",
            "project": "Example Industry App",
            "include_comments": False,
        }
    ]
    assert service.update_calls == [
        {
            "provider": "azure_devops",
            "remote_issue_id": "123",
            "directory_path": "root/example/evander",
            "project": "Example Industry App",
            "state": None,
            "fields": {"System.Tags": "auto-dev; backend; ready"},
        }
    ]


def test_ado_add_work_item_tag_is_noop_when_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _IssueServiceStub(
        read_payload={"remote_issue_id": "123", "fields": {"System.Tags": "Auto-Dev; backend"}},
    )
    monkeypatch.setattr(provider_module, "IssueService", lambda resolver: service)
    provider = _provider(AdoAddWorkItemTagEffectProvider, tmp_path, tag="auto-dev")

    output = provider.execute()

    assert output["updated"] is False
    assert output["tags"] == ["Auto-Dev", "backend"]
    assert output["added_tags"] == []
    assert output["removed_tags"] == []
    assert service.update_calls == []


def test_ado_remove_work_item_tag_updates_when_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _IssueServiceStub(
        read_payload={"remote_issue_id": "123", "fields": {"System.Tags": "Auto-Dev; backend"}},
        update_result={"provider": "azure_devops", "remote_issue_id": "123", "updated": True},
    )
    monkeypatch.setattr(provider_module, "IssueService", lambda resolver: service)
    provider = _provider(AdoRemoveWorkItemTagEffectProvider, tmp_path, tag="auto-dev")

    output = provider.execute()

    assert output["updated"] is True
    assert output["previous_tags"] == ["Auto-Dev", "backend"]
    assert output["tags"] == ["backend"]
    assert output["added_tags"] == []
    assert output["removed_tags"] == ["Auto-Dev"]
    assert service.update_calls[0]["fields"] == {"System.Tags": "backend"}


def test_ado_remove_work_item_tag_is_noop_when_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _IssueServiceStub(
        read_payload={"remote_issue_id": "123", "fields": {"System.Tags": "backend"}},
    )
    monkeypatch.setattr(provider_module, "IssueService", lambda resolver: service)
    provider = _provider(AdoRemoveWorkItemTagEffectProvider, tmp_path, tag="auto-dev")

    output = provider.execute()

    assert output["updated"] is False
    assert output["tags"] == ["backend"]
    assert output["added_tags"] == []
    assert output["removed_tags"] == []
    assert service.update_calls == []
