from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class IssueGetQueryConfigSchema(BaseModel):
    issue_id: str = Field(min_length=1)

    @field_validator("issue_id", mode="before")
    @classmethod
    def normalize_issue_id(cls, value: object) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError("query:issue:get requires issue_id")
        return text
