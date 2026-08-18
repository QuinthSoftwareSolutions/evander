from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from evander.models import DirectoryPath


def resolve_ephemeral_parent_directory_path(
    session_lookup: Any | None,
    parent_session_id: str,
) -> str:
    """Resolve the directory owned by a persistent parent agent's core session."""
    normalized_session_id = str(parent_session_id or "").strip()
    if not normalized_session_id:
        raise ValueError("parent agent must have a backing session")
    if session_lookup is None:
        raise ValueError(
            "ephemeral worker directory cannot be derived because the parent session lookup "
            "is unavailable"
        )

    get_session = getattr(session_lookup, "get_session", None)
    if callable(get_session):
        session = get_session(normalized_session_id)
    elif callable(session_lookup):
        session = session_lookup(normalized_session_id)
    else:
        raise ValueError(
            "ephemeral worker directory cannot be derived because the parent session lookup "
            "does not support get_session"
        )
    if session is None:
        raise ValueError(
            f"parent agent backing session {normalized_session_id!r} was not found"
        )

    if isinstance(session, Mapping):
        directory_path = session.get("directory_path")
    else:
        directory_path = getattr(session, "directory_path", None)
    normalized_directory_path = str(directory_path or "root").strip() or "root"
    return DirectoryPath(segments=normalized_directory_path).as_posix()
