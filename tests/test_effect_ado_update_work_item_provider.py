from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from evander.config import GatewayConfig
from evander.effects.providers import ado_update_work_item as provider_module
from evander.effects.providers.ado_update_work_item import AdoUpdateWorkItemEffectProvider
from evander.effects.providers.base import EffectRuntimeContext
from evander.models import LocalIssueRecord
from evander.storage import JsonStore


class _IssueServiceStub:
    def __init__(
        self,
        *,
        read_payloads: list[dict[str, Any]],
        update_result: dict[str, Any] | None = None,
        update_error: Exception | None = None,
    ) -> None:
        self.read_payloads = list(read_payloads)
        self.update_result = update_result or {}
        self.update_error = update_error
        self.read_calls: list[dict[str, Any]] = []
        self.update_calls: list[dict[str, Any]] = []

    def read_remote_issue(self, **kwargs: Any) -> dict[str, Any]:
        self.read_calls.append(kwargs)
        if not self.read_payloads:
            raise AssertionError("unexpected read_remote_issue call")
        return dict(self.read_payloads.pop(0))

    def update_remote_issue(self, **kwargs: Any) -> dict[str, Any]:
        self.update_calls.append(kwargs)
        if self.update_error is not None:
            raise self.update_error
        return dict(self.update_result)


def _provider(
    tmp_path: Path,
    **effect_fields: Any,
) -> AdoUpdateWorkItemEffectProvider:
    provider = AdoUpdateWorkItemEffectProvider()
    provider.initialize(
        effect_definition=SimpleNamespace(
            id="effect-ado-update-work-item",
            type="effect:ado:update-work-item",
            **effect_fields,
        ),
        runtime_context=EffectRuntimeContext(
            resolved_config=GatewayConfig(runtime_dir=tmp_path / "runtime"),
            provider_config_resolver=object(),
            env={},
        ),
    )
    return provider


def test_ado_update_work_item_updates_state_and_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _IssueServiceStub(
        read_payloads=[{"remote_issue_id": "123", "state": "New"}],
        update_result={
            "remote_issue_id": "123",
            "updated": True,
            "state": "In Progress",
            "fields": {"Custom.Priority": 2},
        },
    )
    monkeypatch.setattr(provider_module, "IssueService", lambda resolver: service)
    provider = _provider(
        tmp_path,
        directory_path="root/example/truman",
        project="Example Industry APP",
        work_item_id="123",
        state="In Progress",
        fields={"Custom.Priority": 2},
        expected_states=["New", "To Do"],
    )

    output = provider.execute()

    assert output["success"] is True
    assert output["updated"] is True
    assert output["skipped"] is False
    assert output["previous_state"] == "New"
    assert service.update_calls == [
        {
            "provider": "azure_devops",
            "remote_issue_id": "123",
            "directory_path": "root/example/truman",
            "project": "Example Industry APP",
            "state": "In Progress",
            "fields": {"Custom.Priority": 2},
            "expected_state": "New",
        }
    ]


def test_ado_update_work_item_skips_when_current_state_is_not_expected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _IssueServiceStub(
        read_payloads=[{"remote_issue_id": "123", "state": "In Progress"}]
    )
    monkeypatch.setattr(provider_module, "IssueService", lambda resolver: service)
    provider = _provider(
        tmp_path,
        directory_path="root/example/truman",
        project="Example Industry APP",
        work_item_id="123",
        state="In Progress",
        expected_states=["New", "To Do"],
    )

    output = provider.execute()

    assert output["success"] is True
    assert output["updated"] is False
    assert output["skipped"] is True
    assert output["skipped_reason"] == "unexpected_state"
    assert output["previous_state"] == "In Progress"
    assert service.update_calls == []


def test_ado_update_work_item_returns_proxy_ticket_without_duplicates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote_payload = {
        "remote_issue_id": "123",
        "id": "123",
        "project": "Example Industry APP",
        "organization_url": "https://dev.azure.com/exampleindustry",
        "title": "Claimed auto-dev item",
        "description": "Remote body",
        "state": "In Progress",
        "url": "https://dev.azure.com/exampleindustry/Example%20Industry%20APP/_workitems/edit/123",
        "updated_at": "2026-05-15T10:00:00Z",
        "comments": [{"id": "1", "text": "cached comment"}],
        "fields": {
            "System.AssignedTo": {
                "displayName": "Ada Lovelace",
                "uniqueName": "ada@example.com",
            },
            "System.State": "In Progress",
            "System.Tags": "auto-dev; backend",
        },
        "rev": 7,
    }
    service = _IssueServiceStub(
        read_payloads=[
            {"remote_issue_id": "123", "state": "New"},
            remote_payload,
            {"remote_issue_id": "123", "state": "New"},
            {**remote_payload, "title": "Claimed auto-dev item updated"},
        ],
        update_result={"remote_issue_id": "123", "updated": True, "state": "In Progress"},
    )
    monkeypatch.setattr(provider_module, "IssueService", lambda resolver: service)
    provider = _provider(
        tmp_path,
        directory_path="root/example/truman",
        project="Example Industry APP",
        work_item_id="123",
        state="In Progress",
        expected_states=["New", "To Do"],
        sync_proxy_cache=True,
    )

    first = provider.execute()
    second = provider.execute()

    assert first["ticket"]["issue_number"] == "123"
    assert first["ticket"]["remote_reference"]["work_item_id"] == "123"
    assert first["remote_assigned_to_identity"] == {
        "display_name": "Ada Lovelace",
        "unique_name": "ada@example.com",
        "email": "ada@example.com",
        "id": None,
        "descriptor": None,
    }
    assert first["remote_work_item_rev"] == 7
    assert second["ticket"]["title"] == "Claimed auto-dev item updated"
    stored = JsonStore(
        tmp_path / "runtime" / "local-issues.json", LocalIssueRecord
    ).read_all()
    assert len(stored) == 1
    ticket = next(iter(stored.values()))
    assert ticket.issue_number == "123"
    assert ticket.title == "Claimed auto-dev item updated"
    assert ticket.tags == ["auto-dev", "backend"]
    assert ticket.comments == [{"id": "1", "text": "cached comment"}]
    yaml_path = Path(ticket.yaml_path)
    assert yaml_path.exists()
    yaml_payload = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert yaml_payload["tags"] == ["auto-dev", "backend"]


def test_ado_update_work_item_skips_expected_state_patch_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _IssueServiceStub(
        read_payloads=[
            {"remote_issue_id": "123", "state": "New"},
            {"remote_issue_id": "123", "state": "In Progress"},
        ],
        update_error=RuntimeError("ADO patch failed"),
    )
    monkeypatch.setattr(provider_module, "IssueService", lambda resolver: service)
    provider = _provider(
        tmp_path,
        directory_path="root/example/truman",
        project="Example Industry APP",
        work_item_id="123",
        state="In Progress",
        expected_states=["New", "To Do"],
    )

    output = provider.execute()

    assert output["updated"] is False
    assert output["skipped"] is True
    assert output["skipped_reason"] == "state_conflict"
    assert output["previous_state"] == "New"
    assert output["state"] == "In Progress"


def test_ado_update_work_item_surfaces_patch_failure_when_state_is_still_expected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _IssueServiceStub(
        read_payloads=[
            {"remote_issue_id": "123", "state": "New"},
            {"remote_issue_id": "123", "state": "New"},
        ],
        update_error=RuntimeError("ADO patch failed"),
    )
    monkeypatch.setattr(provider_module, "IssueService", lambda resolver: service)
    provider = _provider(
        tmp_path,
        directory_path="root/example/truman",
        project="Example Industry APP",
        work_item_id="123",
        state="In Progress",
        expected_states=["New", "To Do"],
    )

    with pytest.raises(RuntimeError, match="ADO patch failed"):
        provider.execute()
