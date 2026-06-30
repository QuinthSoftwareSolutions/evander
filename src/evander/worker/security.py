from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

SENSITIVE_METADATA_KEYS = {
    "authorization",
    "token",
    "access_token",
    "api_key",
    "password",
    "connection_string",
    "secret",
    "pat",
    "env",
    "environment",
}
SAFE_TOKEN_USAGE_METADATA_KEYS = {
    "input_tokens",
    "last_token_usage",
    "output_tokens",
    "total_tokens",
}


def sanitize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in metadata.items():
        normalized = key.lower()
        if normalized not in SAFE_TOKEN_USAGE_METADATA_KEYS and (
            normalized in SENSITIVE_METADATA_KEYS
            or any(
                marker in normalized
                for marker in ("token", "secret", "password", "authorization")
            )
        ):
            sanitized[key] = "[redacted]"
            continue
        if isinstance(value, str):
            sanitized[key] = sanitize_text(value)
        elif isinstance(value, int | float | bool) or value is None:
            sanitized[key] = value
        elif isinstance(value, list):
            sanitized[key] = [_sanitize_metadata_value(item) for item in value]
        elif isinstance(value, dict):
            sanitized[key] = sanitize_metadata(value)
        else:
            sanitized[key] = str(value)
    return sanitized


def sanitize_text(value: str) -> str:
    return value[:500]


def safe_subprocess_error(exc: subprocess.CalledProcessError) -> str:
    parts = [f"command failed with exit code {exc.returncode}"]
    stderr = (exc.stderr or "").strip()
    if stderr:
        parts.append(sanitize_text(stderr))
    return ": ".join(parts)


def list_relative_files(root: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted(str(path.relative_to(root)) for path in root.rglob("*") if path.is_file())


def _sanitize_metadata_value(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, int | float | bool) or value is None:
        return value
    if isinstance(value, dict):
        return sanitize_metadata(value)
    return str(value)
