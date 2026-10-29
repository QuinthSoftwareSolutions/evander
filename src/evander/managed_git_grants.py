from __future__ import annotations

from collections.abc import Mapping
import hashlib
from typing import Any


MANAGED_GIT_GRANTS_METADATA_KEY = "managed_git_access_grants"
LEGACY_MANAGED_GIT_GRANT_METADATA_KEYS = (
    "container_git_write_access",
    "managed_git_access",
)


def normalize_git_access_mode(value: object, *, default: str = "write") -> str:
    normalized = str(value or default).strip().casefold().replace("_", "-")
    if normalized in {"write", "writable"}:
        return "write"
    if normalized in {"read", "readonly", "read-only"}:
        return "read-only"
    raise ValueError("git access mode must be write or read-only")


def normalize_git_access_lifetime(value: object, *, default: str = "session") -> str:
    normalized = str(value or default).strip().casefold().replace("_", "-")
    if normalized in {"session", "persistent"}:
        return "session"
    if normalized in {"operation", "temporary", "one-shot", "oneshot"}:
        return "operation"
    raise ValueError("git access lifetime must be session or operation")


def managed_git_grant_slot(managed_repo_id: str) -> str:
    normalized = str(managed_repo_id or "").strip()
    if not normalized:
        raise ValueError("managed_repo_id is required")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]


def managed_git_key_name(managed_repo_id: str) -> str:
    return f"evander-git-{managed_git_grant_slot(managed_repo_id)}"


def managed_git_ssh_alias(base_alias: str, managed_repo_id: str) -> str:
    normalized_base = str(base_alias or "evander-git-host").strip().rstrip("-")
    return f"{normalized_base}-{managed_git_grant_slot(managed_repo_id)}"


def managed_git_grants_from_metadata(metadata: object) -> dict[str, dict[str, Any]]:
    if not isinstance(metadata, Mapping):
        return {}
    result: dict[str, dict[str, Any]] = {}
    grants = metadata.get(MANAGED_GIT_GRANTS_METADATA_KEY)
    if isinstance(grants, Mapping):
        for key, value in grants.items():
            if not isinstance(value, Mapping):
                continue
            managed_repo_id = str(value.get("managed_repo_id") or key or "").strip()
            if managed_repo_id:
                result[managed_repo_id] = dict(value) | {"managed_repo_id": managed_repo_id}
    for legacy_key in LEGACY_MANAGED_GIT_GRANT_METADATA_KEYS:
        legacy = metadata.get(legacy_key)
        if not isinstance(legacy, Mapping):
            continue
        managed_repo_id = str(legacy.get("managed_repo_id") or "").strip()
        if managed_repo_id and managed_repo_id not in result:
            result[managed_repo_id] = dict(legacy) | {
                "managed_repo_id": managed_repo_id,
                "lifetime": "session",
                "legacy_metadata_key": legacy_key,
            }
    return result


def upsert_managed_git_grant_metadata(
    metadata: object,
    grant: Mapping[str, Any],
) -> dict[str, Any]:
    managed_repo_id = str(grant.get("managed_repo_id") or "").strip()
    if not managed_repo_id:
        raise ValueError("managed Git grant requires managed_repo_id")
    result = dict(metadata) if isinstance(metadata, Mapping) else {}
    grants = managed_git_grants_from_metadata(result)
    canonical_grant = dict(grant) | {
        "managed_repo_id": managed_repo_id,
        "access_mode": normalize_git_access_mode(grant.get("access_mode")),
        "lifetime": normalize_git_access_lifetime(grant.get("lifetime")),
    }
    grants[managed_repo_id] = canonical_grant
    result[MANAGED_GIT_GRANTS_METADATA_KEY] = grants
    for legacy_key in LEGACY_MANAGED_GIT_GRANT_METADATA_KEYS:
        result.pop(legacy_key, None)
    return result


def remove_managed_git_grant_metadata(
    metadata: object,
    managed_repo_id: str,
    *,
    expected_public_key_body: str | None = None,
) -> tuple[dict[str, Any], bool]:
    normalized_repo_id = str(managed_repo_id or "").strip()
    result = dict(metadata) if isinstance(metadata, Mapping) else {}
    grants = managed_git_grants_from_metadata(result)
    existing = grants.get(normalized_repo_id)
    if existing is None:
        return result, False
    expected = str(expected_public_key_body or "").strip()
    if expected and str(existing.get("public_key_body") or "").strip() != expected:
        return result, False
    grants.pop(normalized_repo_id, None)
    if grants:
        result[MANAGED_GIT_GRANTS_METADATA_KEY] = grants
    else:
        result.pop(MANAGED_GIT_GRANTS_METADATA_KEY, None)
    for legacy_key in LEGACY_MANAGED_GIT_GRANT_METADATA_KEYS:
        result.pop(legacy_key, None)
    return result, True


def public_managed_git_grant(grant: Mapping[str, Any]) -> dict[str, Any]:
    result = {
        key: grant[key]
        for key in (
            "managed_repo_id",
            "access_mode",
            "lifetime",
            "execution_backend",
            "git_ssh_alias",
            "authorized_ref_prefix",
            "authorized_pr_notes_ref",
            "allowed_from",
        )
        if key in grant
    }
    result["access_mode"] = normalize_git_access_mode(grant.get("access_mode"))
    result["lifetime"] = normalize_git_access_lifetime(grant.get("lifetime"))
    return result
