from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, Field, field_validator


class BashScriptEffectConfigSchema(BaseModel):
    script_path: str = Field(min_length=1)
    args: list[Any] = Field(default_factory=list)
    working_directory: Any | None = None
    environment: dict[str, Any] | None = None
    stdin: Any | None = None
    timeout_seconds: Any | None = None
    check: Any | None = True

    @field_validator("script_path", mode="before")
    @classmethod
    def normalize_script_path(cls, value: object) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError("effect:bash:script requires a non-empty script_path")
        return text

    @field_validator("args", mode="before")
    @classmethod
    def validate_args(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
            raise ValueError("effect:bash:script args must be a sequence")
        return list(value)

    @field_validator("environment", mode="before")
    @classmethod
    def validate_environment(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise ValueError("effect:bash:script environment must be a mapping")
        return dict(value)
