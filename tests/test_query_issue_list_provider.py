from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from evander.events.provider_models import IssueListQueryDefinition
from evander.models import LocalIssueRecord, RemoteIssueReference
from evander.queries.providers.base import QueryRuntimeContext
from evander.queries.providers.issue_list import IssueListQueryProvider


@dataclass
class _IssueReader:
    issues: list[Any]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def list_issues(
        self,
        *,
        directory_path: str | None = None,
        direct_only: bool = False,
        states: set[str] | None = None,
        exclude_states: set[str] | None = None,
    ) -> list[Any]:
        self.calls.append(
            {
                "directory_path": directory_path,
                "direct_only": direct_only,
                "states": None if states is None else set(states),
                "exclude_states": None if exclude_states is None else set(exclude_states),
            }
        )
        items = list(self.issues)
        if states is not None:
            items = [_issue for _issue in items if _record_value(_issue, "state") in states]
        if exclude_states is not None:
            items = [
                _issue for _issue in items if _record_value(_issue, "state") not in exclude_states
            ]
        if directory_path is not None:
            items = [
                _issue
                for _issue in items
                if _record_value(_issue, "directory_path") == directory_path
            ]
        return items


def _record_value(record: Any, key: str) -> Any:
    if isinstance(record, dict):
        return record.get(key)
    return getattr(record, key, None)


def _issue(
    issue_id: str,
    issue_number: str,
    *,
    directory_path: str,
    state: str | None,
    source: str,
    source_type: str,
    tags: list[str],
    metadata: dict[str, Any] | None = None,
    remote_reference: RemoteIssueReference | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> LocalIssueRecord:
    now = created_at or datetime(2026, 5, 18, 12, 0, tzinfo=UTC)
    return LocalIssueRecord(
        issue_id=issue_id,
        issue_number=issue_number,
        title=f"Issue {issue_number}",
        body=f"Body {issue_number}",
        description=f"Description {issue_number}",
        reason="Reason",
        rejection_reason=None,
        yaml_path=f"/tmp/{issue_number}.yaml",
        directory_path=directory_path,
        managed_repo_id="mr_11111111111111111111",
        source_type=source_type,
        remote_reference=remote_reference,
        tags=list(tags),
        state=state,
        url=f"https://example.test/issues/{issue_number}",
        comments=[{"body": f"Comment {issue_number}", "created_at": now}],
        source=source,
        metadata=metadata or {},
        created_at=now,
        updated_at=updated_at or now,
    )


def _execute(
    query: IssueListQueryDefinition,
    reader: _IssueReader,
    *,
    runtime_directory: str = "root",
    resolved_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    provider = IssueListQueryProvider()
    provider.initialize(
        query,
        QueryRuntimeContext(
            provider_config={},
            issue_reader=reader,
            metadata={"directory_path": runtime_directory},
        ),
    )
    return provider.execute(resolved_config=resolved_config)


def test_issue_list_query_uses_runtime_directory_and_direct_only_true_by_default() -> None:
    direct_issue = _issue(
        "root::local::1",
        "1",
        directory_path="root/team-a",
        state="Open",
        source="local_gui",
        source_type="local_manual",
        tags=["triage", "team-a"],
        metadata={"fields": {"priority": "high", "title": "Fix direct"}},
    )
    descendant_issue = _issue(
        "root::local::2",
        "2",
        directory_path="root/team-a/sub",
        state="Open",
        source="local_gui",
        source_type="proxy",
        tags=["triage", "team-a", "sub"],
        metadata={"fields": {"priority": "high", "title": "Fix descendant"}},
    )
    sibling_issue = _issue(
        "root::local::3",
        "3",
        directory_path="root/team-b",
        state="Open",
        source="local_gui",
        source_type="local_manual",
        tags=["triage", "team-b"],
        metadata={"fields": {"priority": "low", "title": "Fix sibling"}},
    )
    reader = _IssueReader([direct_issue, descendant_issue, sibling_issue])

    output = _execute(
        IssueListQueryDefinition(
            id="query-issues",
            display_name="Issue list",
            type="query:issue:list",
        ),
        reader,
        runtime_directory="root/team-a",
    )

    assert reader.calls == [
        {
            "directory_path": "root/team-a",
            "direct_only": True,
            "states": None,
            "exclude_states": None,
        }
    ]
    assert output["count"] == 1
    assert [item["issue_id"] for item in output["issues"]] == ["root::local::1"]


def test_issue_list_query_applies_resolved_filters_and_descendant_matching() -> None:
    direct_issue = _issue(
        "root::local::1",
        "1",
        directory_path="root/team-a",
        state="Open",
        source="local_gui",
        source_type="local_manual",
        tags=["triage", "team-a"],
        metadata={
            "fields": {
                "priority": "high",
                "owner": "alice",
                "title": "Fix direct",
            }
        },
    )
    descendant_issue = _issue(
        "root::local::2",
        "2",
        directory_path="root/team-a/sub",
        state="Open",
        source="local_gui",
        source_type="proxy",
        tags=["triage", "team-a", "sub"],
        metadata={
            "fields": {
                "priority": "high",
                "owner": "bob",
                "title": "Fix descendant",
            }
        },
        remote_reference=RemoteIssueReference(
            provider="azure_devops",
            organization="https://dev.azure.com/example",
            project="sample",
            work_item_id="42",
            work_item_url="https://dev.azure.com/example/sample/_workitems/edit/42",
            last_synced_at=datetime(2026, 5, 18, 12, 1, tzinfo=UTC),
            last_remote_updated_at=datetime(2026, 5, 18, 12, 2, tzinfo=UTC),
            last_known_remote_state="Active",
        ),
    )
    sibling_issue = _issue(
        "root::local::3",
        "3",
        directory_path="root/team-b",
        state="Open",
        source="local_gui",
        source_type="proxy",
        tags=["triage", "team-b"],
        metadata={
            "fields": {
                "priority": "high",
                "owner": "carol",
                "title": "Fix sibling",
            }
        },
    )
    done_issue = _issue(
        "root::local::4",
        "4",
        directory_path="root/team-a/sub",
        state="Done",
        source="local_gui",
        source_type="proxy",
        tags=["triage", "team-a", "sub"],
        metadata={
            "fields": {
                "priority": "high",
                "owner": "dana",
                "title": "Fix done",
            }
        },
    )
    reader = _IssueReader([direct_issue, descendant_issue, sibling_issue, done_issue])

    output = _execute(
        IssueListQueryDefinition(
            id="query-issues",
            display_name="Issue list",
            type="query:issue:list",
            directory_path="root/team-b",
            direct_only=True,
            states=["Closed"],
            exclude_states=["Rejected"],
            source="local_gui",
            source_type="local_manual",
            tags_any=["unused"],
            tags_all=["unused"],
            metadata_equals={"fields.priority": "low"},
            metadata_exists=["fields.missing"],
            metadata_prefix={"fields.title": "Skip"},
            limit=9,
        ),
        reader,
        resolved_config={
            "directory_path": "root/team-a",
            "direct_only": False,
            "states": ["Open"],
            "exclude_states": ["Done"],
            "source": "local_gui",
            "source_type": "proxy",
            "tags_any": ["triage"],
            "tags_all": ["triage", "team-a", "sub"],
            "metadata_equals": {"fields.priority": "high"},
            "metadata_exists": ["fields.owner"],
            "metadata_prefix": {"fields.title": "Fix"},
            "limit": 1,
        },
    )

    assert reader.calls == [
        {
            "directory_path": None,
            "direct_only": False,
            "states": {"Open"},
            "exclude_states": {"Done"},
        }
    ]
    assert output["count"] == 1
    assert [item["issue_id"] for item in output["issues"]] == ["root::local::2"]


def test_issue_list_query_applies_limit_after_preserving_reader_order() -> None:
    first_match = _issue(
        "root::local::2",
        "2",
        directory_path="root/team-a/sub",
        state="Open",
        source="local_gui",
        source_type="proxy",
        tags=["triage", "team-a", "sub"],
        metadata={"fields": {"priority": "high", "title": "Fix first"}},
    )
    second_match = _issue(
        "root::local::1",
        "1",
        directory_path="root/team-a",
        state="Open",
        source="local_gui",
        source_type="local_manual",
        tags=["triage", "team-a"],
        metadata={"fields": {"priority": "high", "title": "Fix second"}},
    )
    reader = _IssueReader([first_match, second_match])

    output = _execute(
        IssueListQueryDefinition(
            id="query-issues",
            display_name="Issue list",
            type="query:issue:list",
        ),
        reader,
        runtime_directory="root/team-a",
        resolved_config={"directory_path": "root/team-a", "direct_only": False, "limit": 1},
    )

    assert reader.calls == [
        {
            "directory_path": None,
            "direct_only": False,
            "states": None,
            "exclude_states": None,
        }
    ]
    assert output["count"] == 1
    assert [item["issue_id"] for item in output["issues"]] == ["root::local::2"]


def test_issue_list_query_serializes_mapping_and_model_records() -> None:
    issue_reference = RemoteIssueReference(
        provider="azure_devops",
        organization="https://dev.azure.com/example",
        project="sample",
        work_item_id="42",
        work_item_url="https://dev.azure.com/example/sample/_workitems/edit/42",
        last_synced_at=datetime(2026, 5, 18, 12, 3, tzinfo=UTC),
        last_remote_updated_at=datetime(2026, 5, 18, 12, 4, tzinfo=UTC),
        last_known_remote_state="Resolved",
    )
    model_issue = _issue(
        "root::local::10",
        "10",
        directory_path="root/team-a",
        state="Open",
        source="local_gui",
        source_type="local_manual",
        tags=["triage", "team-a"],
        metadata={
            "fields": {
                "priority": "high",
                "title": "Fix model",
            }
        },
        remote_reference=issue_reference,
    )
    mapping_issue = {
        "issue_id": "root::local::11",
        "issue_number": "11",
        "title": "Mapping issue",
        "body": "Mapping body",
        "description": "Mapping description",
        "reason": "Mapping reason",
        "rejection_reason": "Rejected by mapping",
        "yaml_path": "/tmp/11.yaml",
        "directory_path": "root/team-a/sub",
        "managed_repo_id": "mr_11111111111111111111",
        "source_type": "proxy",
        "state": "Open",
        "url": "https://example.test/issues/11",
        "tags": ["triage", "team-a", "sub"],
        "comments": [
            {
                "author": issue_reference,
                "created_at": datetime(2026, 5, 18, 12, 5, tzinfo=UTC),
                "message": "Comment body",
            }
        ],
        "source": "local_gui",
        "metadata": {
            "fields": {
                "priority": "high",
                "owner": "bob",
                "title": "Fix mapping",
                "remote_reference": issue_reference,
            },
            "recorded_at": datetime(2026, 5, 18, 12, 6, tzinfo=UTC),
        },
        "created_at": datetime(2026, 5, 18, 12, 7, tzinfo=UTC),
        "updated_at": datetime(2026, 5, 18, 12, 8, tzinfo=UTC),
        "remote_reference": issue_reference,
    }
    reader = _IssueReader([model_issue, mapping_issue])

    output = _execute(
        IssueListQueryDefinition(
            id="query-issues",
            display_name="Issue list",
            type="query:issue:list",
        ),
        reader,
        runtime_directory="root/team-a",
        resolved_config={"direct_only": False},
    )

    model_payload = next(item for item in output["issues"] if item["issue_id"] == "root::local::10")
    mapping_payload = next(
        item for item in output["issues"] if item["issue_id"] == "root::local::11"
    )

    assert set(model_payload) == {
        "issue_id",
        "issue_number",
        "title",
        "body",
        "description",
        "reason",
        "rejection_reason",
        "yaml_path",
        "directory_path",
        "managed_repo_id",
        "source_type",
        "state",
        "url",
        "tags",
        "comments",
        "source",
        "metadata",
        "created_at",
        "updated_at",
        "remote_reference",
        "can_edit",
        "can_delete",
        "can_create_vm",
    }
    assert model_payload["created_at"] == "2026-05-18T12:00:00+00:00"
    assert model_payload["updated_at"] == "2026-05-18T12:00:00+00:00"
    assert model_payload["remote_reference"] == {
        "provider": "azure_devops",
        "organization": "https://dev.azure.com/example",
        "project": "sample",
        "work_item_id": "42",
        "work_item_url": "https://dev.azure.com/example/sample/_workitems/edit/42",
        "last_synced_at": "2026-05-18T12:03:00+00:00",
        "last_remote_updated_at": "2026-05-18T12:04:00+00:00",
        "last_known_remote_state": "Resolved",
    }
    assert model_payload["can_edit"] is True
    assert model_payload["can_delete"] is True
    assert model_payload["can_create_vm"] is True

    assert mapping_payload["remote_reference"] == {
        "provider": "azure_devops",
        "organization": "https://dev.azure.com/example",
        "project": "sample",
        "work_item_id": "42",
        "work_item_url": "https://dev.azure.com/example/sample/_workitems/edit/42",
        "last_synced_at": "2026-05-18T12:03:00+00:00",
        "last_remote_updated_at": "2026-05-18T12:04:00+00:00",
        "last_known_remote_state": "Resolved",
    }
    assert mapping_payload["metadata"]["fields"]["remote_reference"] == {
        "provider": "azure_devops",
        "organization": "https://dev.azure.com/example",
        "project": "sample",
        "work_item_id": "42",
        "work_item_url": "https://dev.azure.com/example/sample/_workitems/edit/42",
        "last_synced_at": "2026-05-18T12:03:00+00:00",
        "last_remote_updated_at": "2026-05-18T12:04:00+00:00",
        "last_known_remote_state": "Resolved",
    }
    assert mapping_payload["metadata"]["recorded_at"] == "2026-05-18T12:06:00+00:00"
    assert mapping_payload["comments"][0]["author"] == {
        "provider": "azure_devops",
        "organization": "https://dev.azure.com/example",
        "project": "sample",
        "work_item_id": "42",
        "work_item_url": "https://dev.azure.com/example/sample/_workitems/edit/42",
        "last_synced_at": "2026-05-18T12:03:00+00:00",
        "last_remote_updated_at": "2026-05-18T12:04:00+00:00",
        "last_known_remote_state": "Resolved",
    }
    assert mapping_payload["comments"][0]["created_at"] == "2026-05-18T12:05:00+00:00"
    assert mapping_payload["can_edit"] is False
    assert mapping_payload["can_delete"] is True
    assert mapping_payload["can_create_vm"] is True


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        (
            "metadata_equals",
            ["bad"],
            "query:issue:list metadata filters must be JSON objects",
        ),
        (
            "metadata_prefix",
            ["bad"],
            "query:issue:list metadata filters must be JSON objects",
        ),
        (
            "metadata_exists",
            1,
            "query:issue:list metadata_exists must be a string, array, or object",
        ),
    ],
)
def test_issue_list_query_rejects_invalid_metadata_filter_shapes(
    field_name: str, value: Any, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        IssueListQueryDefinition(
            id="query-issues",
            display_name="Issue list",
            type="query:issue:list",
            **{field_name: value},
        )


@pytest.mark.parametrize(
    ("resolved_config", "message"),
    [
        (
            {"direct_only": "maybe"},
            "query:issue:list direct_only must resolve to a boolean",
        ),
        ({"limit": 0}, "query:issue:list limit must be at least 1"),
        ({"limit": "not-an-integer"}, "query:issue:list limit must resolve to an integer"),
    ],
)
def test_issue_list_query_rejects_invalid_boolean_and_limit_values(
    resolved_config: dict[str, Any], message: str
) -> None:
    reader = _IssueReader([])
    with pytest.raises(ValueError, match=message):
        _execute(
            IssueListQueryDefinition(
                id="query-issues",
                display_name="Issue list",
                type="query:issue:list",
            ),
            reader,
            resolved_config=resolved_config,
        )


def test_issue_list_query_requires_issue_reader() -> None:
    provider = IssueListQueryProvider()
    provider.initialize(
        IssueListQueryDefinition(
            id="query-issues",
            display_name="Issue list",
            type="query:issue:list",
        ),
        QueryRuntimeContext(provider_config={}, metadata={"directory_path": "root/team-a"}),
    )

    with pytest.raises(RuntimeError, match="query:issue:list.*issue_reader"):
        provider.execute()
