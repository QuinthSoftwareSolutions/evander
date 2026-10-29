from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

ADDON_ID = "ado-activity-addon"
STATE_DB_FILENAME = "state.db"

AdoActivityKind = Literal[
    "work_item_created",
    "work_item_changed",
    "work_item_commented",
    "pr_commented",
    "pr_committed",
]

AdoActivitySyncScope = Literal["work_items", "pr_comments", "pr_commits", "all"]


class AdoActivityQueryDefinition(BaseModel):
    type: Literal["query:ado:activity"]
    since: datetime
    until: datetime | None = None
    directory_path: str | None = None
    actor: str | None = None
    activity_kinds: list[str] = Field(default_factory=list)
    project: str | None = None
    repo: str | None = None
    work_item_id: str | None = None
    pr_id: str | None = None
    top: int = Field(default=100, ge=1, le=200)
    cursor: str | None = None
    include_raw: bool = False
    outputs: dict[str, Any] | None = None

    @field_validator(
        "directory_path",
        "actor",
        "project",
        "repo",
        "work_item_id",
        "pr_id",
        "cursor",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @field_validator("activity_kinds", mode="before")
    @classmethod
    def normalize_activity_kinds(cls, value: object) -> list[str]:
        return _normalize_string_list(value)


class AdoActivitySyncEffectDefinition(BaseModel):
    type: Literal["effect:ado:sync-activity-index"]
    since: datetime
    until: datetime | None = None
    directory_path: str | None = None
    scope: AdoActivitySyncScope = "all"
    actor: str | None = None
    project: str | None = None
    repo: str | None = None
    states: list[str] = Field(default_factory=list)
    types: list[str] = Field(default_factory=list)
    tags_any: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)
    include_system_comments: bool = False
    outputs: dict[str, Any] | None = None

    @field_validator("directory_path", "scope", "actor", "project", "repo", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @field_validator("states", "types", "tags_any", "statuses", mode="before")
    @classmethod
    def normalize_string_lists(cls, value: object) -> list[str]:
        return _normalize_string_list(value)


def _normalize_string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise ValueError("value must be a string or list of strings")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(text)
    return normalized
