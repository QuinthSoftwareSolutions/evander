from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from evander.models import DirectoryPath

from .service import Access, MiroService
from .state import MiroStore


def directory(value) -> str:
    if hasattr(value, "as_posix"):
        return value.as_posix()
    return DirectoryPath(segments=str(value or "root")).as_posix()


def provider_service(context, *, node_id: str = "miro") -> MiroService:
    metadata = getattr(context, "metadata", {}) or {}
    root = None
    for config in (
        getattr(context, "resolved_config", None),
        getattr(context, "provider_config", None),
    ):
        root = (
            config.get("runtime_dir")
            if isinstance(config, Mapping)
            else getattr(config, "runtime_dir", None)
        )
        if root:
            break
    if not root:
        raise RuntimeError("Miro providers require a configured runtime_dir")
    scope = directory(metadata.get("directory_path"))
    return MiroService(
        MiroStore(Path(root)),
        Access(
            directory_path=scope,
            actor=f"workflow:{scope}:{node_id}",
            workflow=True,
        ),
    )
