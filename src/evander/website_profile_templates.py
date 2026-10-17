"""Host-approved credential delegation for disposable website environments."""
from __future__ import annotations

import json
import os
import re
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from evander.models import DirectoryPath
from evander.website_sessions import website_origin


WEBSITE_PROFILE_TEMPLATES_ENV = "EVANDER_MANAGED_WEBSITE_PROFILE_TEMPLATES"
PROFILE_ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}"


def profile_origin(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise ValueError("Website profile destinations must be HTTPS origins")
    value = value.strip()
    parsed = urlsplit(value)
    if parsed.path not in ("", "/") or parsed.query or parsed.port == 0:
        raise ValueError("Website profile destinations must be HTTPS origins")
    return website_origin(value)


class WebsiteProfileTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    directory_path: str
    source_profile_id: str = Field(pattern="^" + PROFILE_ID_PATTERN + "$")
    browser_origins: list[str] = Field(min_length=1, max_length=32)
    login_origins: list[str] = Field(min_length=1, max_length=32)

    @field_validator("directory_path")
    @classmethod
    def normalize_directory(cls, value: str) -> str:
        return DirectoryPath(segments=value).as_posix()

    @field_validator("browser_origins", "login_origins")
    @classmethod
    def validate_origin_patterns(cls, values: list[str]) -> list[str]:
        result = []
        for value in values:
            origin = profile_origin(value)
            host = urlsplit(origin).hostname or ""
            if "*" in origin and (
                origin.count("*") != 1
                or "*" not in host.split(".")[0]
                or len(host.split(".")) < 3
                or not re.fullmatch(r"[a-z0-9*-]+(?:\.[a-z0-9-]+)+", host)
            ):
                raise ValueError("Origin wildcards must be confined to the first DNS label")
            result.append(origin)
        return result

    def permits(self, scope: str, browser_origin: str, login_origin: str) -> bool:
        def matches(origin: str, patterns: list[str]) -> bool:
            return any(re.fullmatch(re.escape(pattern).replace(r"\*", "[a-z0-9-]+"), origin)
                       for pattern in patterns)

        return (
            (scope == self.directory_path or scope.startswith(self.directory_path + "/"))
            and matches(browser_origin, self.browser_origins)
            and matches(login_origin, self.login_origins)
        )


def website_profile_template(template_id: str) -> WebsiteProfileTemplate:
    """Read only host policy, never workflow-supplied template definitions."""
    raw = os.environ.get(WEBSITE_PROFILE_TEMPLATES_ENV, "{}")
    try:
        if len(raw) > 65536:
            raise ValueError
        payload = json.loads(raw)
        if not isinstance(payload, dict) or len(payload) > 256:
            raise ValueError
        templates = {}
        for key, value in payload.items():
            if not re.fullmatch(PROFILE_ID_PATTERN, key):
                raise ValueError
            templates[key] = WebsiteProfileTemplate.model_validate(value)
    except (ValueError, TypeError, ValidationError):
        raise ValueError("Host website profile template configuration is invalid") from None
    if template_id not in templates:
        raise ValueError("Website profile template is not host-approved")
    return templates[template_id]
