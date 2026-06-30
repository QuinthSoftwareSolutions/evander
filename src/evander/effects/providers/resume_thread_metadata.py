from __future__ import annotations

from collections.abc import Mapping
from typing import Any

DEFAULT_RESUME_THREAD_METADATA_COLLECTION = "agent_thread_ids"
DEFAULT_RESUME_THREAD_METADATA_KEY = "agent_thread_id"


def normalize_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_resume_thread_metadata_collection(value: Any) -> str:
    return normalize_optional_string(value) or DEFAULT_RESUME_THREAD_METADATA_COLLECTION


def resume_thread_metadata_key(value: Any) -> str | None:
    return normalize_optional_string(value)


def resume_thread_id_from_metadata(
    metadata: Any,
    *,
    collection: Any,
    key: Any,
) -> Any | None:
    normalized_key = resume_thread_metadata_key(key)
    if not normalized_key or not isinstance(metadata, Mapping):
        return None
    normalized_collection = normalize_resume_thread_metadata_collection(collection)
    values = metadata.get(normalized_collection)
    if not isinstance(values, Mapping):
        return None
    return values.get(normalized_key)


def resume_thread_metadata_patch(
    *,
    collection: Any,
    key: Any,
    thread_id: Any,
) -> dict[str, Any]:
    normalized_thread_id = normalize_optional_string(thread_id)
    if not normalized_thread_id:
        return {}
    normalized_key = resume_thread_metadata_key(key)
    if normalized_key:
        normalized_collection = normalize_resume_thread_metadata_collection(collection)
        return {normalized_collection: {normalized_key: normalized_thread_id}}
    return {DEFAULT_RESUME_THREAD_METADATA_KEY: normalized_thread_id}
