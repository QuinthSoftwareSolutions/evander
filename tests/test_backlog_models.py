from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_sandbox.models import AdoBacklogCreateRequest, AdoBacklogItemType


def test_backlog_create_request_normalizes_fields() -> None:
    payload = AdoBacklogCreateRequest(
        type=" user_story ",
        project="  My Project  ",
        iteration="  Sprint 1  ",
        title="  Build dashboard  ",
        description="  <p>Keep html</p>  ",
        acceptance_criteria="  - must render  ",
        tags=["  Truman ", "truman", "", "Backend", "backend  "],
        assigned_user="  Ada Lovelace  ",
        parent_backlog_item_id="  1234  ",
    )

    assert payload.type == AdoBacklogItemType.USER_STORY
    assert payload.project == "My Project"
    assert payload.iteration == "Sprint 1"
    assert payload.title == "Build dashboard"
    assert payload.description == "<p>Keep html</p>"
    assert payload.acceptance_criteria == "- must render"
    assert payload.tags == ["Truman", "Backend"]
    assert payload.assigned_user == "Ada Lovelace"
    assert payload.parent_backlog_item_id == "1234"


@pytest.mark.parametrize(
    ("raw_type", "expected_type"),
    [
        ("task", AdoBacklogItemType.TASK),
        ("Epic", AdoBacklogItemType.EPIC),
        ("issue", AdoBacklogItemType.ISSUE),
        ("pbi", AdoBacklogItemType.PRODUCT_BACKLOG_ITEM),
        ("requirement", AdoBacklogItemType.REQUIREMENT),
        ("story", AdoBacklogItemType.USER_STORY),
    ],
)
def test_backlog_create_request_accepts_supported_type_aliases(
    raw_type: str,
    expected_type: AdoBacklogItemType,
) -> None:
    payload = AdoBacklogCreateRequest(
        type=raw_type,
        project="Project",
        iteration="Iteration",
        title="Title",
    )

    assert payload.type == expected_type


@pytest.mark.parametrize("field_name", ["project", "iteration", "title"])
def test_backlog_create_request_rejects_empty_required_strings(field_name: str) -> None:
    payload = {
        "type": "Feature",
        "project": "Project",
        "iteration": "Iteration",
        "title": "Title",
    }
    payload[field_name] = "   "

    with pytest.raises(ValidationError):
        AdoBacklogCreateRequest(**payload)


def test_backlog_create_request_rejects_invalid_type() -> None:
    with pytest.raises(ValidationError, match="type must be one of"):
        AdoBacklogCreateRequest(
            type="not-a-real-type",
            project="Project",
            iteration="Iteration",
            title="Title",
        )


def test_backlog_create_request_rejects_non_int_like_parent_id() -> None:
    with pytest.raises(ValidationError, match="int-like"):
        AdoBacklogCreateRequest(
            type="Bug",
            project="Project",
            iteration="Iteration",
            title="Title",
            parent_backlog_item_id="abc-123",
        )


@pytest.mark.parametrize("parent_id", [True, False])
def test_backlog_create_request_rejects_boolean_parent_id(parent_id: bool) -> None:
    with pytest.raises(ValidationError, match="int-like"):
        AdoBacklogCreateRequest(
            type="Bug",
            project="Project",
            iteration="Iteration",
            title="Title",
            parent_backlog_item_id=parent_id,
        )
