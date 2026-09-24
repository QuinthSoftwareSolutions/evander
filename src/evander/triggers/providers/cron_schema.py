from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from evander.cron import normalize_cron_timezone


class CronTriggerConfigSchema(BaseModel):
    expression: str = Field(min_length=1)
    timezone: str = "UTC"

    @field_validator("expression", mode="before")
    @classmethod
    def normalize_expression(cls, value: object) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError("trigger:cron expression must be a non-empty string")
        return text

    @field_validator("timezone", mode="before")
    @classmethod
    def normalize_timezone(cls, value: object) -> str:
        return normalize_cron_timezone(value)
