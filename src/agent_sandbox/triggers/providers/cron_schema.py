from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class CronTriggerConfigSchema(BaseModel):
    expression: str = Field(min_length=1)

    @field_validator("expression", mode="before")
    @classmethod
    def normalize_expression(cls, value: object) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError("trigger:cron expression must be a non-empty string")
        return text
