from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from agent_sandbox.session_manifest_repository import JsonSessionManifestRepository


def _session_payload(*, session_id: str = "session-1") -> dict[str, object]:
    now = datetime.now(timezone.utc)
    return {
        "session_id": session_id,
        "directory_path": "root",
        "allowed_tools": ["approval_submit"],
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "created_at": now.isoformat(),
    }


def test_read_all_returns_valid_manifests(tmp_path) -> None:
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({"session-1": _session_payload()}), encoding="utf-8")

    repository = JsonSessionManifestRepository(path)

    sessions = repository.read_all()

    assert set(sessions) == {"session-1"}
    assert sessions["session-1"].session_id == "session-1"


def test_read_all_safe_ignores_invalid_entries(tmp_path) -> None:
    path = tmp_path / "sessions.json"
    path.write_text(
        json.dumps(
            {
                "session-1": _session_payload(session_id="session-1"),
                "session-2": {"session_id": "session-2"},
            }
        ),
        encoding="utf-8",
    )
    repository = JsonSessionManifestRepository(path)

    sessions = repository.read_all_safe()

    assert set(sessions) == {"session-1"}
