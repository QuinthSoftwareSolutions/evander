from __future__ import annotations

import json
import re
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote

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


def admin_frontend_hashed_asset_url(dist_dir: Path, asset_name: str) -> str | None:
    manifest_path = dist_dir / ".vite" / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    direct_asset = _find_manifest_asset_file(payload, dist_dir, asset_name, direct_only=True)
    if direct_asset is not None:
        return f"/{quote(direct_asset.as_posix(), safe='/._-~')}"
    fallback_asset = _find_manifest_asset_file(payload, dist_dir, asset_name, direct_only=False)
    if fallback_asset is None:
        return None
    return f"/{quote(fallback_asset.as_posix(), safe='/._-~')}"


def _normalize_asset_reference(value: str) -> Path:
    path = value.split("?", 1)[0].split("#", 1)[0].lstrip("/")
    return Path(path)


def _find_manifest_asset_file(
    payload: dict[str, Any], dist_dir: Path, asset_name: str, *, direct_only: bool
) -> PurePosixPath | None:
    for key, value in payload.items():
        if not isinstance(value, dict):
            continue
        file_path = _safe_manifest_asset_path(value.get("file"))
        if file_path is None:
            continue
        sources = (str(key), str(value.get("src") or ""))
        if any(
            PurePosixPath(source).name == asset_name for source in sources
        ) and _manifest_file_exists(dist_dir, file_path):
            return file_path

    if direct_only:
        return None

    for value in payload.values():
        if not isinstance(value, dict):
            continue
        for asset in value.get("assets") or ():
            asset_path = _safe_manifest_asset_path(asset)
            if asset_path is None:
                continue
            if _hashed_manifest_asset_matches(asset_path.name, asset_name) and _manifest_file_exists(
                dist_dir, asset_path
            ):
                return asset_path
    return None


def _safe_manifest_asset_path(value: object) -> PurePosixPath | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = PurePosixPath(value.strip())
    if path.is_absolute() or ".." in path.parts:
        return None
    return path


def _manifest_file_exists(dist_dir: Path, path: PurePosixPath) -> bool:
    return (dist_dir / Path(*path.parts)).is_file()


def _hashed_manifest_asset_matches(hashed_name: str, asset_name: str) -> bool:
    source = PurePosixPath(asset_name)
    return hashed_name.startswith(f"{source.stem}-") and hashed_name.endswith(source.suffix)
