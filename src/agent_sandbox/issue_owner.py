from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class IssueOwnerGitIdentity:
    name: str
    email: str


def issue_owner_git_identity(metadata: Mapping[str, Any] | None) -> IssueOwnerGitIdentity | None:
    if not isinstance(metadata, Mapping):
        return None
    owner = metadata.get("issue_owner")
    if not isinstance(owner, Mapping):
        return None
    name = _non_empty_string(owner.get("display_name") or owner.get("name"))
    email = _non_empty_string(owner.get("email") or owner.get("unique_name"))
    if name is None or email is None or not _looks_like_email(email):
        return None
    return IssueOwnerGitIdentity(name=name, email=email)


def assigned_to_identity(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, str):
        email = _non_empty_string(value)
        if email is None:
            return None
        return {
            "display_name": email,
            "unique_name": email,
            "email": email if _looks_like_email(email) else None,
            "id": None,
            "descriptor": None,
        }
    if not isinstance(value, Mapping):
        text = _non_empty_string(value)
        if text is None:
            return None
        return {
            "display_name": text,
            "unique_name": text,
            "email": text if _looks_like_email(text) else None,
            "id": None,
            "descriptor": None,
        }

    display_name = _first_non_empty(
        value.get("displayName"),
        value.get("name"),
        value.get("uniqueName"),
        value.get("mailAddress"),
        value.get("signInAddress"),
    )
    unique_name = _first_non_empty(
        value.get("uniqueName"),
        value.get("signInAddress"),
        value.get("mailAddress"),
        value.get("displayName"),
        value.get("name"),
    )
    email = _first_email(value.get("uniqueName"), value.get("mailAddress"), value.get("signInAddress"))
    if display_name is None and unique_name is None and email is None:
        return None
    return {
        "display_name": display_name or unique_name or email,
        "unique_name": unique_name,
        "email": email,
        "id": _non_empty_string(value.get("id")),
        "descriptor": _non_empty_string(value.get("descriptor")),
    }


def _first_non_empty(*values: object) -> str | None:
    for value in values:
        normalized = _non_empty_string(value)
        if normalized is not None:
            return normalized
    return None


def _first_email(*values: object) -> str | None:
    for value in values:
        normalized = _non_empty_string(value)
        if normalized is not None and _looks_like_email(normalized):
            return normalized
    return None


def _non_empty_string(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _looks_like_email(value: str) -> bool:
    return "@" in value and not any(char.isspace() for char in value)
