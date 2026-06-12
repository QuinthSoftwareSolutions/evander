from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from agent_sandbox.models import digest_payload

DEFAULT_SENSITIVE_PAYLOAD_MARKERS: tuple[str, ...] = (
    "token",
    "secret",
    "password",
    "pat",
    "authorization",
    "cookie",
    "private_key",
    "ssh_key",
)


def build_payload_diagnostic(
    *,
    approved_payload: dict[str, Any],
    attempted_payload: dict[str, Any],
    redacted_keys: Iterable[str] | None = None,
    redacted_markers: Iterable[str] | None = None,
    status: str | None = None,
    reason: str | None = None,
    approved_payload_digest: str | None = None,
    attempted_payload_digest: str | None = None,
    max_diff_entries: int = 25,
) -> dict[str, Any]:
    """Build a redacted payload diagnostic without changing enforcement semantics."""
    normalized_redacted_keys = _normalize_redacted_keys(redacted_keys)
    normalized_redacted_markers = _normalize_redacted_markers(redacted_markers)
    if approved_payload_digest is None:
        approved_payload_digest = digest_payload(approved_payload)
    if attempted_payload_digest is None:
        attempted_payload_digest = digest_payload(attempted_payload)
    approved_payload_summary = _redact_payload(
        approved_payload,
        redacted_keys=normalized_redacted_keys,
        redacted_markers=normalized_redacted_markers,
    )
    attempted_payload_summary = _redact_payload(
        attempted_payload,
        redacted_keys=normalized_redacted_keys,
        redacted_markers=normalized_redacted_markers,
    )
    if approved_payload_digest == attempted_payload_digest:
        differences = _empty_differences()
    else:
        differences = _build_differences(
            approved_payload,
            attempted_payload,
            redacted_keys=normalized_redacted_keys,
            redacted_markers=normalized_redacted_markers,
            max_diff_entries=max_diff_entries,
        )
    return {
        "status": status,
        "reason": reason,
        "payload_match": not differences["truncated"]
        and not differences["changed"]["entries"]
        and not differences["missing"]["entries"]
        and not differences["extra"]["entries"],
        "approved_payload_digest": approved_payload_digest,
        "attempted_payload_digest": attempted_payload_digest,
        "approved_payload_summary": approved_payload_summary,
        "attempted_payload_summary": attempted_payload_summary,
        "differences": differences,
    }


def _empty_differences() -> dict[str, Any]:
    empty_bucket = {"entries": [], "truncated": False}
    return {
        "changed": dict(empty_bucket),
        "missing": dict(empty_bucket),
        "extra": dict(empty_bucket),
        "truncated": False,
    }


def _normalize_redacted_keys(redacted_keys: Iterable[str] | None) -> set[str]:
    normalized: set[str] = set()
    if redacted_keys is None:
        return normalized
    for key in redacted_keys:
        text = str(key or "").strip().lower()
        if text:
            normalized.add(text)
    return normalized


def _normalize_redacted_markers(redacted_markers: Iterable[str] | None) -> tuple[str, ...]:
    markers = redacted_markers if redacted_markers is not None else DEFAULT_SENSITIVE_PAYLOAD_MARKERS
    normalized: list[str] = []
    seen: set[str] = set()
    for marker in markers:
        text = str(marker or "").strip().lower()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return tuple(normalized)


def _is_sensitive_key(
    key_name: str | None,
    *,
    redacted_keys: set[str],
    redacted_markers: tuple[str, ...],
) -> bool:
    normalized_key = str(key_name or "").strip().lower()
    if not normalized_key:
        return False
    if normalized_key in redacted_keys:
        return True
    return any(marker in normalized_key for marker in redacted_markers)


def _redact_payload(
    value: Any,
    *,
    redacted_keys: set[str],
    redacted_markers: tuple[str, ...],
    key_name: str | None = None,
) -> Any:
    if _is_sensitive_key(
        key_name,
        redacted_keys=redacted_keys,
        redacted_markers=redacted_markers,
    ):
        return "<redacted>"
    if isinstance(value, Mapping):
        return {
            str(key): _redact_payload(
                nested_value,
                redacted_keys=redacted_keys,
                redacted_markers=redacted_markers,
                key_name=str(key),
            )
            for key, nested_value in value.items()
        }
    if isinstance(value, list):
        return [
            _redact_payload(
                nested_value,
                redacted_keys=redacted_keys,
                redacted_markers=redacted_markers,
            )
            for nested_value in value
        ]
    if isinstance(value, tuple):
        return [
            _redact_payload(
                nested_value,
                redacted_keys=redacted_keys,
                redacted_markers=redacted_markers,
            )
            for nested_value in value
        ]
    return _json_safe_value(value)


def _build_differences(
    approved_payload: Any,
    attempted_payload: Any,
    *,
    redacted_keys: set[str],
    redacted_markers: tuple[str, ...],
    max_diff_entries: int,
) -> dict[str, Any]:
    changed: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    extra: list[dict[str, Any]] = []
    truncated = False

    def append_changed(path: str, approved_value: Any, attempted_value: Any, *, redacted: bool) -> None:
        nonlocal truncated
        if len(changed) >= max_diff_entries:
            truncated = True
            return
        changed.append(
            {
                "path": _display_path(path),
                "approved_value": _display_value(approved_value, redacted=redacted),
                "attempted_value": _display_value(attempted_value, redacted=redacted),
            }
        )

    def append_missing(path: str, approved_value: Any, *, redacted: bool) -> None:
        nonlocal truncated
        if len(missing) >= max_diff_entries:
            truncated = True
            return
        missing.append(
            {
                "path": _display_path(path),
                "approved_value": _display_value(approved_value, redacted=redacted),
            }
        )

    def append_extra(path: str, attempted_value: Any, *, redacted: bool) -> None:
        nonlocal truncated
        if len(extra) >= max_diff_entries:
            truncated = True
            return
        extra.append(
            {
                "path": _display_path(path),
                "attempted_value": _display_value(attempted_value, redacted=redacted),
            }
        )

    def walk(path: str, approved_value: Any, attempted_value: Any, *, key_name: str | None = None) -> None:
        nonlocal truncated
        if truncated:
            return
        sensitive = _is_sensitive_key(
            key_name,
            redacted_keys=redacted_keys,
            redacted_markers=redacted_markers,
        )
        if sensitive:
            if approved_value != attempted_value:
                append_changed(path, approved_value, attempted_value, redacted=True)
            return

        approved_is_mapping = isinstance(approved_value, Mapping)
        attempted_is_mapping = isinstance(attempted_value, Mapping)
        if approved_is_mapping and attempted_is_mapping:
            approved_keys = {str(key): key for key in approved_value.keys()}
            attempted_keys = {str(key): key for key in attempted_value.keys()}
            for key in sorted(set(approved_keys) | set(attempted_keys)):
                child_path = _child_path(path, key)
                approved_key = approved_keys.get(key)
                attempted_key = attempted_keys.get(key)
                if approved_key is None:
                    extra_value = attempted_value[attempted_key]
                    append_extra(
                        child_path,
                        extra_value,
                        redacted=_contains_sensitive_value(
                            extra_value,
                            key_name=key,
                            redacted_keys=redacted_keys,
                            redacted_markers=redacted_markers,
                        ),
                    )
                    continue
                if attempted_key is None:
                    missing_value = approved_value[approved_key]
                    append_missing(
                        child_path,
                        missing_value,
                        redacted=_contains_sensitive_value(
                            missing_value,
                            key_name=key,
                            redacted_keys=redacted_keys,
                            redacted_markers=redacted_markers,
                        ),
                    )
                    continue
                walk(
                    child_path,
                    approved_value[approved_key],
                    attempted_value[attempted_key],
                    key_name=key,
                )
            return

        approved_is_list = _is_sequence_like(approved_value)
        attempted_is_list = _is_sequence_like(attempted_value)
        if approved_is_list and attempted_is_list:
            approved_list = list(approved_value)
            attempted_list = list(attempted_value)
            common_length = min(len(approved_list), len(attempted_list))
            for index in range(common_length):
                walk(
                    _index_path(path, index),
                    approved_list[index],
                    attempted_list[index],
                )
                if truncated:
                    return
            for index in range(common_length, len(approved_list)):
                missing_value = approved_list[index]
                append_missing(
                    _index_path(path, index),
                    missing_value,
                    redacted=_contains_sensitive_value(
                        missing_value,
                        redacted_keys=redacted_keys,
                        redacted_markers=redacted_markers,
                    ),
                )
                if truncated:
                    return
            for index in range(common_length, len(attempted_list)):
                extra_value = attempted_list[index]
                append_extra(
                    _index_path(path, index),
                    extra_value,
                    redacted=_contains_sensitive_value(
                        extra_value,
                        redacted_keys=redacted_keys,
                        redacted_markers=redacted_markers,
                    ),
                )
                if truncated:
                    return
            return

        if approved_value != attempted_value:
            append_changed(
                path,
                approved_value,
                attempted_value,
                redacted=_contains_sensitive_value(
                    approved_value,
                    key_name=key_name,
                    redacted_keys=redacted_keys,
                    redacted_markers=redacted_markers,
                )
                or _contains_sensitive_value(
                    attempted_value,
                    key_name=key_name,
                    redacted_keys=redacted_keys,
                    redacted_markers=redacted_markers,
                ),
            )

    walk("", approved_payload, attempted_payload)
    return {
        "changed": {"entries": changed, "truncated": truncated and len(changed) >= max_diff_entries},
        "missing": {"entries": missing, "truncated": truncated and len(missing) >= max_diff_entries},
        "extra": {"entries": extra, "truncated": truncated and len(extra) >= max_diff_entries},
        "truncated": truncated,
    }


def _is_sequence_like(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _display_path(path: str) -> str:
    return str(path or "").strip() or "<root>"


def _child_path(path: str, key: str) -> str:
    if not path:
        return key
    return f"{path}.{key}"


def _index_path(path: str, index: int) -> str:
    if not path:
        return f"[{index}]"
    return f"{path}[{index}]"


def _display_value(value: Any, *, redacted: bool) -> Any:
    if redacted:
        return "<redacted>"
    return _json_safe_value(value)


def _contains_sensitive_value(
    value: Any,
    *,
    redacted_keys: set[str],
    redacted_markers: tuple[str, ...],
    key_name: str | None = None,
) -> bool:
    if _is_sensitive_key(
        key_name,
        redacted_keys=redacted_keys,
        redacted_markers=redacted_markers,
    ):
        return True
    if isinstance(value, Mapping):
        return any(
            _contains_sensitive_value(
                nested_value,
                redacted_keys=redacted_keys,
                redacted_markers=redacted_markers,
                key_name=str(key),
            )
            for key, nested_value in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(
            _contains_sensitive_value(
                nested_value,
                redacted_keys=redacted_keys,
                redacted_markers=redacted_markers,
            )
            for nested_value in value
        )
    return False


def _json_safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe_value(nested_value) for key, nested_value in value.items()}
    if isinstance(value, list):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, set):
        return [_json_safe_value(item) for item in sorted(value, key=lambda item: repr(item))]
    return str(value)
