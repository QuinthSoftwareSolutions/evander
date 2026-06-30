from __future__ import annotations

import re
from pathlib import Path

_ASSET_REFERENCE_RE = re.compile(r"""(?:src|href)=["'](?P<path>/?assets/[^"']+)["']""")


def admin_frontend_dist_dir(project_dir: Path) -> Path:
    return project_dir / "frontend" / "workflow-editor" / "dist"


def missing_admin_frontend_assets(dist_dir: Path) -> list[Path]:
    index_path = dist_dir / "index.html"
    if not index_path.is_file():
        return [index_path]

    html = index_path.read_text(encoding="utf-8")
    references = [
        _normalize_asset_reference(match.group("path"))
        for match in _ASSET_REFERENCE_RE.finditer(html)
    ]
    if not references:
        return [dist_dir / "assets" / "*.js"]
    return [dist_dir / reference for reference in references if not (dist_dir / reference).is_file()]


def admin_frontend_assets_ready(dist_dir: Path) -> bool:
    return not missing_admin_frontend_assets(dist_dir)


def _normalize_asset_reference(value: str) -> Path:
    path = value.split("?", 1)[0].split("#", 1)[0].lstrip("/")
    return Path(path)
