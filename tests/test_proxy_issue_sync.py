from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from pydantic import TypeAdapter

from agent_sandbox.config import GatewayConfig
from agent_sandbox.daemons.proxy_issue_sync import (
    sync_proxy_issues_once,
    upsert_ado_proxy_issue_from_remote_payload,
)
from agent_sandbox.events.models import TriggerDefinitionDiscriminated, materialize_node_definition
from agent_sandbox.models import LocalIssueRecord, RemoteIssueReference
from agent_sandbox.storage import JsonStore
from agent_sandbox.triggers.providers.base import TriggerRuntimeContext
from agent_sandbox.triggers.providers.ticket_changed import TicketChangedTriggerProvider


def _proxy_issue(
    issue_number: str,
    *,
    issue_id: str | None = None,
    directory_path: str = "root/team",
    project: str = "Project",
    state: str = "New",
    last_synced_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> LocalIssueRecord:
    return LocalIssueRecord(
        issue_id=issue_id or f"root/team::azure_devops::https://dev.azure.com/example::Project::{issue_number}",
        issue_number=issue_number,
        title=f"Local {issue_number}",
        body=f"Body {issue_number}",
        description=f"Body {issue_number}",
        yaml_path=f"runtime/ado-work-items/work-item-{issue_number}.yaml",
        directory_path=directory_path,
        source_type="remote_proxy",
        source="ado_worker",
        remote_reference=RemoteIssueReference(
            provider="azure_devops",
            organization="https://dev.azure.com/example",
            project=project,
            work_item_id=issue_number,
            work_item_url=f"https://dev.azure.com/example/{project}/_workitems/edit/{issue_number}",
            last_synced_at=last_synced_at,
            last_known_remote_state=state,
        ),
        state=state,
        url=f"https://dev.azure.com/example/{project}/_workitems/edit/{issue_number}",
        comments=[{"id": "c1", "text": "preserve comment cache"}],
        created_at=datetime(2026, 5, 15, 8, 0, tzinfo=UTC),
        updated_at=updated_at or datetime(2026, 5, 15, 8, 0, tzinfo=UTC),
    )


def _store_issue(runtime_dir: Path, issue: LocalIssueRecord) -> None:
    JsonStore(runtime_dir / "local-issues.json", LocalIssueRecord).put(issue.issue_id, issue)


class _BatchIssueService:
    def __init__(self, items: dict[str, dict[str, Any]], missing_ids: list[str] | None = None):
        self.items = items
        self.missing_ids = missing_ids or []
        self.calls: list[dict[str, Any]] = []

    def read_remote_issues_batch(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {
            "provider": kwargs["provider"],
            "project": kwargs.get("project"),
            "items": {item_id: self.items[item_id] for item_id in kwargs["remote_issue_ids"] if item_id in self.items},
            "missing_ids": self.missing_ids,
        }


def test_proxy_issue_sync_updates_multiple_issues_with_one_batch(tmp_path: Path) -> None:
    now = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)
    _store_issue(tmp_path, _proxy_issue("101"))
    _store_issue(tmp_path, _proxy_issue("102"))
    service = _BatchIssueService(
        {
            "101": {
                "remote_issue_id": "101",
                "title": "Remote 101",
                "description": "Remote body 101",
                "state": "In Progress",
                "url": "https://ado.example/101",
                "updated_at": "2026-05-15T11:00:00Z",
                "type": "Bug",
                "area": "Project\\Area",
                "iteration": "Project\\Sprint",
                "assigned_to": "Alice",
                "fields": {
                    "System.State": "In Progress",
                    "System.Tags": "auto-dev; backend",
                },
            },
            "102": {
                "remote_issue_id": "102",
                "title": "Remote 102",
                "description": "Remote body 102",
                "state": "To Do",
                "url": "https://ado.example/102",
                "updated_at": "2026-05-15T11:01:00Z",
                "tags": ["auto-dev"],
                "fields": {"System.State": "To Do"},
            },
        }
    )

    result = sync_proxy_issues_once(
        runtime_dir=tmp_path,
        issue_service=service,  # type: ignore[arg-type]
        now=now,
    )

    assert result.candidates == 2
    assert result.remote_requests == 1
    assert result.updated == 2
    assert len(service.calls) == 1
    assert service.calls[0]["remote_issue_ids"] == ["101", "102"]
    assert service.calls[0]["directory_path"] == "root/team"
    assert service.calls[0]["project"] == "Project"

    stored = JsonStore(tmp_path / "local-issues.json", LocalIssueRecord).read_all()
    issue_101 = next(issue for issue in stored.values() if issue.issue_number == "101")
    assert issue_101.title == "Remote 101"
    assert issue_101.body == "Remote body 101"
    assert issue_101.state == "In Progress"
    assert issue_101.tags == ["auto-dev", "backend"]
    assert issue_101.updated_at == now
    assert issue_101.comments == [{"id": "c1", "text": "preserve comment cache"}]
    assert issue_101.metadata["proxy_cache"]["comments"] == [
        {"id": "c1", "text": "preserve comment cache"}
    ]
    assert issue_101.metadata["proxy_cache"]["assigned_to"] == "Alice"
    assert issue_101.metadata["proxy_sync"]["status"] == "ok"
    assert issue_101.remote_reference is not None
    assert issue_101.remote_reference.last_synced_at == now
    assert issue_101.remote_reference.last_remote_updated_at == datetime(
        2026, 5, 15, 11, 0, tzinfo=UTC
    )


def test_upsert_ado_proxy_issue_materializes_yaml_and_reads_ado_field_tags(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)
    issue = upsert_ado_proxy_issue_from_remote_payload(
        runtime_dir=tmp_path,
        directory_path="root/team",
        project="Project",
        organization_url="https://dev.azure.com/example",
        now=now,
        remote_payload={
            "remote_issue_id": "101",
            "title": "Remote 101",
            "description": "Remote body 101",
            "state": "In Progress",
            "url": "https://ado.example/101",
            "updated_at": "2026-05-15T11:00:00Z",
            "comments": [{"id": "c1", "text": "cached comment"}],
            "fields": {
                "System.State": "In Progress",
                "System.Tags": "auto-dev; backend",
            },
        },
    )

    assert issue.tags == ["auto-dev", "backend"]
    yaml_path = Path(issue.yaml_path)
    assert yaml_path.exists()
    payload = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert payload["issue_number"] == 101
    assert payload["tags"] == ["auto-dev", "backend"]
    assert payload["source"]["organization"] == "https://dev.azure.com/example"
    assert payload["source"]["project"] == "Project"
    assert payload["comments"] == [{"id": "c1", "text": "cached comment"}]


def test_upsert_ado_proxy_issue_reuses_local_issue_linked_from_approval_reason(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)
    local_yaml = tmp_path / "issues" / "21.yaml"
    local_yaml.parent.mkdir(parents=True)
    local_yaml.write_text(
        "issue_number: 21\n"
        'title: "Remote 101"\n'
        "body: Local body\n"
        "state: Committed\n"
        "source: local_gui\n",
        encoding="utf-8",
    )
    local_issue = LocalIssueRecord(
        issue_id="root/team::local::21",
        issue_number="21",
        title="Remote 101",
        body="Local body",
        yaml_path=str(local_yaml),
        directory_path="root/team",
        source_type="local_manual",
        source="local_gui",
        state="Committed",
        metadata={
            "fields": {
                "Reason": (
                    "Committed after approval as Azure DevOps Bug 101: "
                    "https://ado.example/101"
                )
            }
        },
    )
    _store_issue(tmp_path, local_issue)

    issue = upsert_ado_proxy_issue_from_remote_payload(
        runtime_dir=tmp_path,
        directory_path="root/team",
        project="Project",
        organization_url="https://dev.azure.com/example",
        now=now,
        remote_payload={
            "remote_issue_id": "101",
            "title": "Remote 101",
            "description": "Remote body 101",
            "state": "In Progress",
            "url": "https://ado.example/101",
            "updated_at": "2026-05-15T11:00:00Z",
            "fields": {"System.State": "In Progress"},
        },
    )

    assert issue.issue_id == local_issue.issue_id
    assert issue.issue_number == "21"
    assert issue.source_type == "local_manual"
    assert issue.source == "local_gui"
    assert issue.state == "In Progress"
    assert issue.remote_reference is not None
    assert issue.remote_reference.work_item_id == "101"
    assert issue.metadata["remote"]["issue_id"] == "101"
    assert local_yaml.read_text(encoding="utf-8") == (
        "issue_number: 21\n"
        'title: "Remote 101"\n'
        "body: Local body\n"
        "state: Committed\n"
        "source: local_gui\n"
    )
    stored = JsonStore(tmp_path / "local-issues.json", LocalIssueRecord).read_all()
    assert list(stored) == [local_issue.issue_id]


def test_upsert_ado_proxy_issue_does_not_reuse_prefix_url_match(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)
    local_issue = LocalIssueRecord(
        issue_id="root/team::local::21",
        issue_number="21",
        title="Remote 101",
        body="Local body",
        yaml_path=str(tmp_path / "issues" / "21.yaml"),
        directory_path="root/team",
        source_type="local_manual",
        source="local_gui",
        state="Committed",
        metadata={
            "fields": {
                "Reason": (
                    "Committed after approval as Azure DevOps Bug 1010: "
                    "https://dev.azure.com/example/Project/_workitems/edit/1010"
                )
            }
        },
    )
    _store_issue(tmp_path, local_issue)

    issue = upsert_ado_proxy_issue_from_remote_payload(
        runtime_dir=tmp_path,
        directory_path="root/team",
        project="Project",
        organization_url="https://dev.azure.com/example",
        now=now,
        remote_payload={
            "remote_issue_id": "101",
            "title": "Remote 101",
            "description": "Remote body 101",
            "state": "In Progress",
            "url": "https://dev.azure.com/example/Project/_workitems/edit/101",
            "updated_at": "2026-05-15T11:00:00Z",
            "fields": {"System.State": "In Progress"},
        },
    )

    assert issue.issue_id != local_issue.issue_id
    assert issue.source_type == "remote_proxy"
    stored = JsonStore(tmp_path / "local-issues.json", LocalIssueRecord).read_all()
    assert set(stored) == {local_issue.issue_id, issue.issue_id}
    assert stored[local_issue.issue_id].remote_reference is None


def test_upsert_ado_proxy_issue_prunes_generated_duplicate_when_local_issue_is_canonical(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)
    local_issue = LocalIssueRecord(
        issue_id="root/team::local::21",
        issue_number="21",
        title="Remote 101",
        body="Local body",
        yaml_path=str(tmp_path / "issues" / "21.yaml"),
        directory_path="root/team",
        source_type="local_manual",
        source="local_gui",
        state="Committed",
        metadata={
            "fields": {
                "Reason": (
                    "Committed after approval as Azure DevOps Bug 101: "
                    "https://ado.example/101"
                )
            }
        },
    )
    proxy_yaml = tmp_path / "issues" / "proxy-azure-devops-101-test.yaml"
    proxy_yaml.parent.mkdir(parents=True)
    proxy_yaml.write_text("issue_number: 101\n", encoding="utf-8")
    proxy_issue = _proxy_issue(
        "101",
        issue_id="root/team::azure_devops::https://dev.azure.com/example::Project::101",
        directory_path="root/team",
    )
    proxy_issue.yaml_path = str(proxy_yaml)
    _store_issue(tmp_path, local_issue)
    _store_issue(tmp_path, proxy_issue)

    issue = upsert_ado_proxy_issue_from_remote_payload(
        runtime_dir=tmp_path,
        directory_path="root/team",
        project="Project",
        organization_url="https://dev.azure.com/example",
        now=now,
        remote_payload={
            "remote_issue_id": "101",
            "title": "Remote 101",
            "description": "Remote body 101",
            "state": "In Progress",
            "url": "https://ado.example/101",
            "updated_at": "2026-05-15T11:00:00Z",
            "fields": {"System.State": "In Progress"},
        },
    )

    assert issue.issue_id == local_issue.issue_id
    stored = JsonStore(tmp_path / "local-issues.json", LocalIssueRecord).read_all()
    assert list(stored) == [local_issue.issue_id]
    assert not proxy_yaml.exists()


def test_proxy_issue_sync_respects_freshness_window(tmp_path: Path) -> None:
    now = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)
    _store_issue(
        tmp_path,
        _proxy_issue("101", last_synced_at=now - timedelta(seconds=60)),
    )
    service = _BatchIssueService({})

    result = sync_proxy_issues_once(
        runtime_dir=tmp_path,
        issue_service=service,  # type: ignore[arg-type]
        freshness_window_seconds=300,
        now=now,
    )

    assert result.candidates == 0
    assert result.skipped_fresh == 1
    assert service.calls == []


def test_proxy_issue_sync_defaults_to_current_time(tmp_path: Path) -> None:
    _store_issue(tmp_path, _proxy_issue("101"))
    service = _BatchIssueService(
        {
            "101": {
                "remote_issue_id": "101",
                "title": "Remote 101",
                "description": "Remote body 101",
                "state": "In Progress",
                "url": "https://ado.example/101",
                "updated_at": "2026-05-15T11:00:00Z",
                "tags": [],
            }
        }
    )

    result = sync_proxy_issues_once(
        runtime_dir=tmp_path,
        issue_service=service,  # type: ignore[arg-type]
    )

    assert result.candidates == 1
    stored = JsonStore(tmp_path / "local-issues.json", LocalIssueRecord).read_all()
    issue = next(iter(stored.values()))
    assert issue.state == "In Progress"
    assert issue.remote_reference is not None
    assert issue.remote_reference.last_synced_at is not None


def test_proxy_issue_sync_records_missing_remote_without_deleting_local_issue(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)
    issue = _proxy_issue("101")
    _store_issue(tmp_path, issue)
    service = _BatchIssueService({}, missing_ids=["101"])

    result = sync_proxy_issues_once(
        runtime_dir=tmp_path,
        issue_service=service,  # type: ignore[arg-type]
        now=now,
    )

    assert result.missing == 1
    stored = JsonStore(tmp_path / "local-issues.json", LocalIssueRecord).read_all()
    assert list(stored) == [issue.issue_id]
    assert stored[issue.issue_id].metadata["proxy_sync"]["status"] == "missing_or_inaccessible"
    assert stored[issue.issue_id].state == "New"


def test_ticket_changed_sees_proxy_state_after_sync(tmp_path: Path) -> None:
    initial_updated_at = datetime(2026, 5, 15, 8, 0, tzinfo=UTC)
    now = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)
    issue = _proxy_issue("101", updated_at=initial_updated_at)
    _store_issue(tmp_path, issue)
    provider = _ticket_changed_provider(tmp_path)

    assert len(provider.poll()) == 1

    service = _BatchIssueService(
        {
            "101": {
                "remote_issue_id": "101",
                "title": issue.title,
                "description": issue.body,
                "state": "In Progress",
                "url": issue.url,
                "updated_at": "2026-05-15T11:00:00Z",
                "tags": [],
            }
        }
    )
    sync_proxy_issues_once(
        runtime_dir=tmp_path,
        issue_service=service,  # type: ignore[arg-type]
        now=now,
    )

    events = provider.poll()

    assert len(events) == 1
    assert events[0].trigger_type == "trigger:ticket:changed"
    assert events[0].payload["ticket"]["issue_id"] == issue.issue_id
    assert events[0].payload["ticket"]["state"] == "In Progress"


def _ticket_changed_provider(runtime_dir: Path) -> TicketChangedTriggerProvider:
    payload = {
        "id": "ticket-changed",
        "display_name": "Ticket changed",
        "type": "trigger:ticket:changed",
        "directories": ["root/team"],
    }
    adapter = TypeAdapter(TriggerDefinitionDiscriminated)
    trigger = materialize_node_definition(adapter.validate_python(payload))
    provider = TicketChangedTriggerProvider()
    provider.initialize(
        trigger,
        TriggerRuntimeContext(resolved_config=GatewayConfig(runtime_dir=runtime_dir)),
    )
    return provider
