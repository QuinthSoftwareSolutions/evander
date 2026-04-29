from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from agent_sandbox.models import SessionManifest
from agent_sandbox.repositories.session_manifests import JsonSessionManifestRepository


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


def test_repository_canonicalizes_mismatched_session_keys(tmp_path) -> None:
    path = tmp_path / "sessions.json"
    path.write_text(
        json.dumps({"stale-key": _session_payload(session_id="canonical-session")}),
        encoding="utf-8",
    )
    repository = JsonSessionManifestRepository(path)

    sessions = repository.read_all_safe()

    assert set(sessions) == {"canonical-session"}
    assert repository.get_session("canonical-session") is not None


def test_delete_session_removes_mismatched_raw_key(tmp_path) -> None:
    path = tmp_path / "sessions.json"
    path.write_text(
        json.dumps({"stale-key": _session_payload(session_id="canonical-session")}),
        encoding="utf-8",
    )
    repository = JsonSessionManifestRepository(path)

    assert repository.delete_session("canonical-session") is True
    assert json.loads(path.read_text(encoding="utf-8")) == {}


def test_session_repository_supports_lifecycle_queries_and_mutations(tmp_path) -> None:
    path = tmp_path / "sessions.json"
    repository = JsonSessionManifestRepository(path)
    now = datetime.now(timezone.utc)

    active_manifest = SessionManifest.model_validate(_session_payload(session_id="active-session"))
    expired_manifest = SessionManifest.model_validate(
        {
            **_session_payload(session_id="expired-session"),
            "expires_at": (now - timedelta(minutes=1)).isoformat(),
        }
    )

    repository.upsert_session(active_manifest)
    repository.upsert_session(expired_manifest)

    assert repository.get_session("active-session") == active_manifest
    assert [session.session_id for session in repository.list_active_sessions(now=now)] == [
        "active-session"
    ]
    assert repository.delete_session("expired-session") is True
    assert repository.delete_session("missing-session") is False


def test_session_repository_resolves_operational_targets(tmp_path) -> None:
    path = tmp_path / "sessions.json"
    repository = JsonSessionManifestRepository(path)
    now = datetime.now(timezone.utc)
    older = SessionManifest.model_validate(
        {
            **_session_payload(session_id="issue-42-old"),
            "vm_id": "vm-42",
            "created_at": (now - timedelta(hours=2)).isoformat(),
            "expires_at": (now + timedelta(hours=2)).isoformat(),
        }
    )
    newer = SessionManifest.model_validate(
        {
            **_session_payload(session_id="issue-42-new"),
            "vm_id": "vm-42",
            "created_at": (now - timedelta(hours=1)).isoformat(),
            "expires_at": (now + timedelta(hours=3)).isoformat(),
        }
    )
    direct = SessionManifest.model_validate(
        {
            **_session_payload(session_id="session-direct"),
            "vm_id": "vm-direct",
        }
    )
    repository.upsert_session(older)
    repository.upsert_session(newer)
    repository.upsert_session(direct)

    assert [session.session_id for session in repository.list_sessions_for_vm("vm-42")] == [
        "issue-42-old",
        "issue-42-new",
    ]
    assert [session.session_id for session in repository.list_sessions_for_issue("42")] == [
        "issue-42-old",
        "issue-42-new",
    ]
    assert repository.resolve_session_target("session-direct") == direct
    assert repository.resolve_session_target("vm-42") == newer
    assert repository.resolve_session_target("42") == newer
    assert repository.resolve_vm_name_from_target("session-direct") == "vm-direct"
