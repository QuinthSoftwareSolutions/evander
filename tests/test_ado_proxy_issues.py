from __future__ import annotations

from pathlib import Path

import yaml

from evander.ado_proxy_issues import upsert_ado_proxy_issue_items
from evander.models import LocalIssueRecord
from evander.storage import JsonStore


def test_upsert_ado_proxy_issue_items_accepts_query_items_and_writes_legacy_yaml(
    tmp_path: Path,
) -> None:
    result = upsert_ado_proxy_issue_items(
        runtime_dir=tmp_path,
        directory_path="root/team-a",
        organization_url="https://dev.azure.com/example",
        project="Project",
        repo_source="/repo",
        items=[
            {
                "id": "101",
                "title": "Fix picker",
                "state": "Committed",
                "url": "https://ado.example/101",
                "fields": {
                    "System.Title": "Fix picker",
                    "System.Description": "Main body",
                    "Microsoft.VSTS.TCM.ReproSteps": "Open picker",
                    "Microsoft.VSTS.Common.AcceptanceCriteria": "Selection persists",
                    "System.State": "Committed",
                    "System.Tags": "truman; backend",
                    "System.ChangedDate": "2026-05-22T10:00:00Z",
                },
            }
        ],
    )

    assert result.created == 1
    assert result.updated == 0
    ticket = result.tickets[0]
    assert ticket.issue_number == "101"
    assert ticket.repo_source == "/repo"
    assert ticket.source_type == "remote_proxy"
    assert ticket.remote_reference is not None
    assert ticket.remote_reference.work_item_id == "101"
    assert "Repro Steps" in (ticket.description or "")
    assert "Acceptance Criteria" in (ticket.description or "")

    yaml_path = tmp_path / "ado-work-items" / "work-item-101.yaml"
    assert Path(ticket.yaml_path) == yaml_path
    payload = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert payload["issue_number"] == 101
    assert payload["repo_source"] == "/repo"
    assert payload["updated_date"] == "2026-05-22T10:00:00Z"

    stored = JsonStore(tmp_path / "local-issues.json", LocalIssueRecord).read_all()
    assert set(stored) == {ticket.issue_id}


def test_upsert_ado_proxy_issue_items_updates_existing_proxy_record(tmp_path: Path) -> None:
    first = upsert_ado_proxy_issue_items(
        runtime_dir=tmp_path,
        directory_path="root",
        organization_url="https://dev.azure.com/example",
        project="Project",
        items=[
            {
                "id": "101",
                "title": "Initial",
                "fields": {"System.ChangedDate": "2026-05-22T10:00:00Z"},
            }
        ],
    )
    second = upsert_ado_proxy_issue_items(
        runtime_dir=tmp_path,
        directory_path="root",
        organization_url="https://dev.azure.com/example",
        project="Project",
        items=[
            {
                "id": "101",
                "title": "Updated",
                "fields": {"System.ChangedDate": "2026-05-22T11:00:00Z"},
            }
        ],
    )

    assert first.created == 1
    assert second.created == 0
    assert second.updated == 1
    assert second.tickets[0].issue_id == first.tickets[0].issue_id
    assert second.tickets[0].title == "Updated"
