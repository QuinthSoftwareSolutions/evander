from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from evander.addons import AddonContext
from evander.effects.providers.base import EffectRuntimeContext
from evander.queries.providers.base import QueryRuntimeContext
from evander_ado_activity_addon.mcp import register_mcp_tools
from evander_ado_activity_addon.models import (
    AdoActivityQueryDefinition,
    AdoActivitySyncEffectDefinition,
)
from evander_ado_activity_addon.providers import (
    AdoActivityQueryProvider,
    AdoActivitySyncEffectProvider,
)
from evander_ado_activity_addon.repository import AdoActivityRepository
from evander_ado_activity_addon.service import AdoActivityIndexService


def test_ado_activity_repository_filters_and_pages(tmp_path: Path) -> None:
    repo = AdoActivityRepository(tmp_path)
    counts = repo.upsert_records(
        [
            {
                "activity_id": "work-item:changed:101:1",
                "activity_kind": "work_item_changed",
                "occurred_at": datetime(2026, 7, 7, 10, tzinfo=UTC),
                "actor_display_name": "Alex Example",
                "actor_unique_name": "alex@example.com",
                "actor_email": "alex@example.com",
                "directory_path": "root",
                "project": "APP",
                "work_item_id": "101",
                "title": "Fix activity",
                "summary": "Updated fields: System.Tags",
                "changed_fields": {"System.Tags": {"newValue": "review"}},
            },
            {
                "activity_id": "pr:comment:repo:12:1:2",
                "activity_kind": "pr_commented",
                "occurred_at": datetime(2026, 7, 7, 11, tzinfo=UTC),
                "actor_display_name": "Alex Example",
                "actor_unique_name": "alex@example.com",
                "actor_email": "alex@example.com",
                "directory_path": "root",
                "project": "APP",
                "repo": "ExampleIndustryAPP",
                "pr_id": "12",
                "title": "PR title",
                "summary": "Looks good",
            },
            {
                "activity_id": "work-item:changed:202:1",
                "activity_kind": "work_item_changed",
                "occurred_at": datetime(2026, 7, 7, 12, tzinfo=UTC),
                "actor_display_name": "Other User",
                "actor_unique_name": "other@example.com",
                "actor_email": "other@example.com",
                "directory_path": "other-dir",
                "project": "APP",
                "work_item_id": "202",
            },
        ]
    )

    assert counts == {"inserted": 3, "updated": 0}

    first_page = repo.query(
        since=datetime(2026, 7, 7, tzinfo=UTC),
        actor="alex@example.com",
        directory_path="root",
        project="APP",
        top=1,
    )
    assert first_page["count"] == 1
    assert first_page["paging"]["has_more"] is True
    assert first_page["items"][0]["activity_kind"] == "pr_commented"

    second_page = repo.query(
        since=datetime(2026, 7, 7, tzinfo=UTC),
        actor="alex@example.com",
        directory_path="root",
        project="APP",
        cursor=first_page["paging"]["next_cursor"],
        top=1,
    )
    assert second_page["count"] == 1
    assert second_page["paging"]["has_more"] is False
    assert second_page["items"][0]["work_item_id"] == "101"

    other_dir = repo.query(
        since=datetime(2026, 7, 7, tzinfo=UTC),
        actor="alex@example.com",
        directory_path="other-dir",
    )
    assert other_dir["items"] == []


def test_ado_activity_repository_scopes_duplicate_activity_ids_by_directory(
    tmp_path: Path,
) -> None:
    repo = AdoActivityRepository(tmp_path)

    counts = repo.upsert_records(
        [
            {
                "activity_id": "work-item:changed:101:update:5",
                "activity_kind": "work_item_changed",
                "occurred_at": datetime(2026, 7, 7, 10, tzinfo=UTC),
                "directory_path": "root",
                "project": "APP",
                "work_item_id": "101",
                "summary": "Root update",
            },
            {
                "activity_id": "work-item:changed:101:update:5",
                "activity_kind": "work_item_changed",
                "occurred_at": datetime(2026, 7, 7, 11, tzinfo=UTC),
                "directory_path": "other-dir",
                "project": "APP",
                "work_item_id": "101",
                "summary": "Other directory update",
            },
        ]
    )

    assert counts == {"inserted": 2, "updated": 0}
    root_result = repo.query(
        since=datetime(2026, 7, 7, tzinfo=UTC),
        directory_path="root",
    )
    other_result = repo.query(
        since=datetime(2026, 7, 7, tzinfo=UTC),
        directory_path="other-dir",
    )
    assert root_result["items"][0]["summary"] == "Root update"
    assert other_result["items"][0]["summary"] == "Other directory update"


def test_ado_activity_repository_filters_sync_state_by_directory(tmp_path: Path) -> None:
    repo = AdoActivityRepository(tmp_path)
    repo.update_sync_state(
        sync_key="root:work_items:APP:*",
        directory_path="root",
        complete_through=datetime(2026, 7, 8, tzinfo=UTC),
        metadata={"actor": "alex@example.com"},
    )
    repo.update_sync_state(
        sync_key="other-dir:work_items:Secret:*",
        directory_path="other-dir",
        complete_through=datetime(2026, 7, 8, tzinfo=UTC),
        metadata={"actor": "other@example.com"},
    )

    result = repo.query(
        since=datetime(2026, 7, 7, tzinfo=UTC),
        directory_path="root",
    )

    assert result["index"]["sync_state"] == [
        {
            "sync_key": "root:work_items:APP:*",
            "complete_through": "2026-07-08T00:00:00.000000Z",
            "updated_at": result["index"]["sync_state"][0]["updated_at"],
            "metadata": {"actor": "alex@example.com"},
        }
    ]


def test_ado_activity_query_provider_reads_index_for_runtime_directory(tmp_path: Path) -> None:
    addon_dir = tmp_path / "addons" / "ado-activity-addon"
    repo = AdoActivityRepository(addon_dir)
    repo.upsert_records(
        [
            {
                "activity_id": "work-item:changed:101:1",
                "activity_kind": "work_item_changed",
                "occurred_at": datetime(2026, 7, 7, 10, tzinfo=UTC),
                "actor_display_name": "Alex Example",
                "actor_unique_name": "alex@example.com",
                "actor_email": "alex@example.com",
                "directory_path": "root",
                "project": "APP",
                "work_item_id": "101",
            }
        ]
    )
    provider = AdoActivityQueryProvider()
    provider.initialize(
        AdoActivityQueryDefinition(
            type="query:ado:activity",
            since=datetime(2026, 7, 7, tzinfo=UTC),
            actor="alex@example.com",
        ),
        QueryRuntimeContext(
            resolved_config={"runtime_dir": str(tmp_path)},
            metadata={"directory_path": "root"},
        ),
    )

    result = provider.execute()

    assert result["count"] == 1
    assert result["items"][0]["work_item_id"] == "101"
    assert result["items"][0]["directory_path"] == "root"


def test_ado_activity_sync_provider_coerces_json_datetime_values(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured = {}

    class FakeService:
        def __init__(self, *, addon_runtime_dir, provider_config_resolver) -> None:
            assert addon_runtime_dir == tmp_path / "addons" / "ado-activity-addon"

        def sync(self, **kwargs):
            captured.update(kwargs)
            return {"inserted": 0, "updated": 0, "records": 0}

    monkeypatch.setattr("evander_ado_activity_addon.providers.AdoActivityIndexService", FakeService)
    provider = AdoActivitySyncEffectProvider()
    provider.initialize(
        AdoActivitySyncEffectDefinition(
            type="effect:ado:sync-activity-index",
            since=datetime(2026, 7, 1, tzinfo=UTC),
            scope="work_items",
        ),
        EffectRuntimeContext(resolved_config={"runtime_dir": str(tmp_path)}),
    )

    provider.execute(
        resolved_config={
            "since": "2026-07-07T00:00:00Z",
            "until": "2026-07-08T00:00:00Z",
            "scope": "work_items",
        }
    )

    assert captured["since"] == datetime(2026, 7, 7, tzinfo=UTC)
    assert captured["until"] == datetime(2026, 7, 8, tzinfo=UTC)


def test_ado_activity_sync_indexes_work_item_activity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls = []

    class FakeIssueService:
        def __init__(self, config_resolver) -> None:
            assert config_resolver is fake_resolver

        def query_remote_work_item_activity(self, **kwargs):
            calls.append(kwargs)
            return {
                "items": [
                    {
                        "id": "101",
                        "project": "APP",
                        "title": "Activity item",
                        "state": "Active",
                        "type": "Task",
                        "web_url": "https://dev.azure.com/example/_workitems/edit/101",
                        "activities": [
                            {
                                "activity_type": "changed",
                                "provenance": "update",
                                "occurred_at": datetime(2026, 7, 7, 10, tzinfo=UTC),
                                "actor": "Alex Example",
                                "actor_identity": {
                                    "displayName": "Alex Example",
                                    "uniqueName": "alex@example.com",
                                },
                                "summary": "Updated fields: System.Tags",
                                "fields": {"System.Tags": {"newValue": "review"}},
                                "raw": {"id": 5},
                            }
                        ],
                    }
                ],
                "paging": {"has_more": False, "next_skip": None},
            }

    fake_resolver = object()
    monkeypatch.setattr("evander_ado_activity_addon.service.IssueService", FakeIssueService)
    service = AdoActivityIndexService(
        addon_runtime_dir=tmp_path,
        provider_config_resolver=fake_resolver,
    )

    result = service.sync(
        since=datetime(2026, 7, 7, tzinfo=UTC),
        until=datetime(2026, 7, 8, tzinfo=UTC),
        directory_path="root",
        scope="work_items",
        actor="alex@example.com",
        project="APP",
    )

    assert result["inserted"] == 1
    assert result["records"] == 1
    assert calls[0]["exhaustive"] is True
    indexed = service.query(
        since=datetime(2026, 7, 7, tzinfo=UTC),
        actor="alex@example.com",
        directory_path="root",
    )
    assert indexed["items"][0]["activity_kind"] == "work_item_changed"
    assert indexed["items"][0]["changed_fields"] == {
        "System.Tags": {"newValue": "review"}
    }
    assert indexed["index"]["sync_state"][0]["complete_through"] == "2026-07-08T00:00:00.000000Z"


def test_ado_activity_mcp_tools_register_and_query_index(tmp_path: Path) -> None:
    tools = {}

    class Server:
        def tool(self, *, name: str, description: str):
            assert description

            def decorator(func):
                tools[name] = func
                return func

            return decorator

    class Policy:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def require_tool(self, session_id: str, tool_name: str):
            self.calls.append((session_id, tool_name))
            return SimpleNamespace(directory_path="root")

    policy = Policy()
    context = AddonContext(
        addon_id="ado-activity-addon",
        runtime_dir=tmp_path,
        addon_runtime_dir=tmp_path / "addons" / "ado-activity-addon",
        service=SimpleNamespace(policy=policy, provider_config=None),
    )
    repo = AdoActivityRepository(context.addon_runtime_dir)
    repo.upsert_records(
        [
            {
                "activity_id": "pr:comment:repo:12:1:2",
                "activity_kind": "pr_commented",
                "occurred_at": datetime(2026, 7, 7, 11, tzinfo=UTC),
                "actor_display_name": "Alex Example",
                "actor_unique_name": "alex@example.com",
                "actor_email": "alex@example.com",
                "directory_path": "root",
                "project": "APP",
                "repo": "ExampleIndustryAPP",
                "pr_id": "12",
            }
        ]
    )

    names = register_mcp_tools(Server(), context)
    result = tools["query_ado_activity_index"](
        since="2026-07-07T00:00:00Z",
        actor="alex@example.com",
        session_id="session-1",
    )

    assert names == ("sync_ado_activity_index", "query_ado_activity_index")
    assert result["count"] == 1
    assert result["items"][0]["pr_id"] == "12"
    assert policy.calls == [("session-1", "query_ado_activity_index")]

    with pytest.raises(ToolError, match="directory_path must match"):
        tools["query_ado_activity_index"](
            since="2026-07-07T00:00:00Z",
            directory_path="other-dir",
            session_id="session-1",
        )
