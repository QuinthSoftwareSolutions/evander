"""Read literal key/value configuration files without shell expansion."""

from __future__ import annotations

from pathlib import Path


def parse_env_content(content: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        values[key] = value.strip().strip('"').strip("'")
    return values


def load_optional_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return parse_env_content(path.read_text(encoding="utf-8"))
