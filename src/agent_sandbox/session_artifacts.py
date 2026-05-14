from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

AGENT_ARTIFACTS_ENV = "AGENT_SANDBOX_ARTIFACTS_DIR"
DEFAULT_AGENT_ARTIFACTS_DIR = "/artifacts"


def normalize_artifact_relative_path(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        raise ValueError("artifact path is required")
    path = PurePosixPath(text)
    if path.is_absolute():
        raise ValueError("artifact path must be relative")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("artifact path must not contain empty, dot, or parent segments")
    return path.as_posix()


def resolve_artifact_file(root: Path, relative_path: Any) -> Path:
    normalized_relative_path = normalize_artifact_relative_path(relative_path)
    resolved_root = root.resolve(strict=True)
    candidate = (root / normalized_relative_path).resolve(strict=True)
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("artifact path must stay within the session artifacts folder") from exc
    if not candidate.is_file():
        raise FileNotFoundError(f"artifact path is not a regular file: {normalized_relative_path}")
    return candidate
