from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from multiprocessing import get_context
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_sandbox.models import SessionManifest
from agent_sandbox.repositories.session_manifests import JsonSessionManifestRepository
from agent_sandbox.storage import JsonObjectStore


def _session_payload(*, session_id: str = "session-1") -> dict[str, object]:
    now = datetime.now(timezone.utc)
    return {
        "session_id": session_id,
        "directory_path": "root",
        "allowed_tools": ["approval_submit"],
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "created_at": now.isoformat(),
    }


def _tool_value(tool: object) -> str:
    return tool.value if hasattr(tool, "value") else str(tool)


def _process_upsert_session(path: str, payload: dict[str, object], barrier, errors) -> None:
    try:
        barrier.wait(timeout=5)
        original_write = JsonObjectStore._write_json_payload

        def delayed_write(self, payload_obj):
            time.sleep(0.05)
            return original_write(self, payload_obj)

        JsonObjectStore._write_json_payload = delayed_write
        repository = JsonSessionManifestRepository(Path(path))
        repository.upsert_session(SessionManifest.model_validate(payload))
    except BaseException as exc:  # pragma: no cover - exercised in subprocesses
        errors.put(repr(exc))
        raise


def test_read_all_returns_valid_manifests(tmp_path) -> None:
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({"session-1": _session_payload()}), encoding="utf-8")

    repository = JsonSessionManifestRepository(path)

    sessions = repository.read_all()

    assert set(sessions) == {"session-1"}
    assert sessions["session-1"].session_id == "session-1"


def test_read_all_raises_on_invalid_entries(tmp_path) -> None:
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

    with pytest.raises(ValidationError):
        repository.read_all()

    sessions = repository.read_all_safe()

    assert set(sessions) == {"session-1"}


def test_read_all_populates_missing_notification_target_ids_as_empty_list(tmp_path) -> None:
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({"session-1": _session_payload()}), encoding="utf-8")
    repository = JsonSessionManifestRepository(path)

    session = repository.get_session("session-1")

    assert session is not None
    assert session.approval_notification_target_ids == []


def test_read_all_populates_missing_allowed_contact_ids_as_none(tmp_path) -> None:
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({"session-1": _session_payload()}), encoding="utf-8")
    repository = JsonSessionManifestRepository(path)

    session = repository.get_session("session-1")

    assert session is not None
    assert session.allowed_contact_ids is None


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


def test_read_all_safe_preserves_dynamic_addon_tool_names(tmp_path) -> None:
    path = tmp_path / "sessions.json"
    payload = {
        **_session_payload(session_id="session-1"),
        "allowed_tools": ["approval_submit", "retired_addon_tool"],
        "approval_required_tools": ["approval_submit", "retired_addon_tool"],
        "approval_tool_settings": {
            "approval_submit": {"max_uses": 1},
            "retired_addon_tool": {"max_uses": 1},
        },
        "tool_argument_policies": {
            "approval_submit": [],
            "retired_addon_tool": [],
        },
    }
    path.write_text(json.dumps({"session-1": payload}), encoding="utf-8")
    repository = JsonSessionManifestRepository(path)

    sessions = repository.read_all_safe()

    assert set(sessions) == {"session-1"}
    session = sessions["session-1"]
    assert [_tool_value(tool) for tool in session.allowed_tools] == [
        "approval_submit",
        "retired_addon_tool",
    ]
    assert [_tool_value(tool) for tool in session.approval_required_tools] == [
        "approval_submit",
        "retired_addon_tool",
    ]
    assert set(session.approval_tool_settings) == {"approval_submit", "retired_addon_tool"}
    assert set(session.tool_argument_policies) == {"approval_submit", "retired_addon_tool"}


def test_session_repository_treats_non_dict_top_level_as_empty(tmp_path) -> None:
    path = tmp_path / "sessions.json"
    path.write_text("[]", encoding="utf-8")
    repository = JsonSessionManifestRepository(path)

    assert repository.read_all() == {}
    assert repository.read_all_safe() == {}


def test_read_all_safe_returns_empty_for_empty_file(tmp_path) -> None:
    path = tmp_path / "sessions.json"
    path.write_text("", encoding="utf-8")
    repository = JsonSessionManifestRepository(path)

    assert repository.read_all_safe() == {}


def test_read_all_raises_on_empty_file(tmp_path) -> None:
    path = tmp_path / "sessions.json"
    path.write_text("", encoding="utf-8")
    repository = JsonSessionManifestRepository(path)

    with pytest.raises(json.JSONDecodeError):
        repository.read_all()


def test_read_all_safe_returns_empty_for_invalid_json(tmp_path) -> None:
    path = tmp_path / "sessions.json"
    path.write_text("{not json", encoding="utf-8")
    repository = JsonSessionManifestRepository(path)

    assert repository.read_all_safe() == {}


def test_read_all_raises_on_invalid_json(tmp_path) -> None:
    path = tmp_path / "sessions.json"
    path.write_text("{not json", encoding="utf-8")
    repository = JsonSessionManifestRepository(path)

    with pytest.raises(json.JSONDecodeError):
        repository.read_all()


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

    assert repository.vm_agent_admin.delete_session("canonical-session") is True
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
    assert repository.vm_agent_admin.delete_session("expired-session") is True
    assert repository.vm_agent_admin.delete_session("missing-session") is False


def test_session_repository_round_trips_notification_target_ids(tmp_path) -> None:
    path = tmp_path / "sessions.json"
    repository = JsonSessionManifestRepository(path)
    manifest = SessionManifest.model_validate(
        {
            **_session_payload(session_id="session-1"),
            "approval_notification_target_ids": [
                " Ops-Primary ",
                "ops-primary",
                "ops-backup",
            ],
        }
    )

    repository.upsert_session(manifest)

    loaded = repository.get_session("session-1")

    assert loaded is not None
    assert loaded.approval_notification_target_ids == ["ops-primary", "ops-backup"]


def test_session_manifest_normalizes_allowed_contact_ids() -> None:
    manifest = SessionManifest.model_validate(
        {
            **_session_payload(session_id="session-1"),
            "allowed_contact_ids": [" ops-main ", "ops-main", "support", " support "],
        }
    )

    assert manifest.allowed_contact_ids == ["ops-main", "support"]


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("ops-main", "allowed_contact_ids must be a JSON array of strings or null"),
        (("ops-main",), "allowed_contact_ids must be a JSON array of strings or null"),
        (["ops-main", ""], "allowed_contact_ids cannot contain empty values"),
        (["ops-main", "  "], "allowed_contact_ids cannot contain empty values"),
    ],
)
def test_session_manifest_rejects_invalid_allowed_contact_ids(value, message) -> None:
    with pytest.raises(ValidationError, match=message):
        SessionManifest.model_validate(
            {
                **_session_payload(session_id="session-1"),
                "allowed_contact_ids": value,
            }
        )


def test_session_repository_round_trips_allowed_contact_ids(tmp_path) -> None:
    path = tmp_path / "sessions.json"
    repository = JsonSessionManifestRepository(path)
    manifest = SessionManifest.model_validate(
        {
            **_session_payload(session_id="session-1"),
            "allowed_contact_ids": [" ops-main ", "ops-backup", "ops-backup"],
        }
    )

    repository.upsert_session(manifest)

    loaded = repository.get_session("session-1")

    assert loaded is not None
    assert loaded.allowed_contact_ids == ["ops-main", "ops-backup"]


def test_session_repository_preserves_concurrent_independent_upserts(tmp_path) -> None:
    path = tmp_path / "sessions.json"
    context = get_context("spawn")
    start = context.Barrier(3)
    errors = context.Queue()
    processes = [
        context.Process(
            target=_process_upsert_session,
            args=(str(path), _session_payload(session_id="session-a"), start, errors),
        ),
        context.Process(
            target=_process_upsert_session,
            args=(str(path), _session_payload(session_id="session-b"), start, errors),
        ),
    ]

    for process in processes:
        process.start()
    start.wait(timeout=5)
    for process in processes:
        process.join(timeout=5)

    for process in processes:
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)
        assert process.exitcode == 0
    assert errors.empty()

    repository = JsonSessionManifestRepository(path)

    assert set(repository.read_all_safe()) == {"session-a", "session-b"}


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
