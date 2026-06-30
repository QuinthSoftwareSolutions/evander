from pathlib import Path

from evander.task_yaml import TaskYamlSerializer, WorkItemSource, WorkItemTask


def test_serialize_writes_expected_yaml_with_optional_fields(tmp_path: Path) -> None:
    serializer = TaskYamlSerializer(tmp_path / "runtime" / "issue-tasks")

    path = serializer.serialize(
        WorkItemTask(
            issue_number=123,
            name="Implement queue serializer",
            description="Write stable yaml output",
            source=WorkItemSource(
                organization="https://dev.azure.com/example-org",
                project="sandbox-project",
                work_item_url="https://dev.azure.com/example-org/sandbox-project/_workitems/edit/123",
            ),
            tags=("truman", "backend"),
            state="Committed",
            changed_date="2026-03-21T12:30:45Z",
        )
    )

    assert path == tmp_path / "runtime" / "issue-tasks" / "123.yaml"
    assert (
        path.read_text(encoding="utf-8")
        == """issue_number: 123
name: Implement queue serializer
description: Write stable yaml output
source:
  organization: https://dev.azure.com/example-org
  project: sandbox-project
  work_item_url: https://dev.azure.com/example-org/sandbox-project/_workitems/edit/123
tags:
- backend
- truman
state: Committed
changed_date: '2026-03-21T12:30:45+00:00'
"""
    )


def test_serialize_omits_optional_fields_when_empty(tmp_path: Path) -> None:
    serializer = TaskYamlSerializer(tmp_path / "runtime" / "issue-tasks")

    path = serializer.serialize(
        WorkItemTask(
            issue_number=999,
            name="No metadata",
            description="description",
            source=WorkItemSource(
                organization="org",
                project="proj",
                work_item_url="url",
            ),
        )
    )

    assert "tags:" not in path.read_text(encoding="utf-8")
    assert "state:" not in path.read_text(encoding="utf-8")
    assert "changed_date:" not in path.read_text(encoding="utf-8")
