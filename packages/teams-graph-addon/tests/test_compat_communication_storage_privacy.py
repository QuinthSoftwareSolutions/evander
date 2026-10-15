"""Local storage safety only; these tests do not establish cross-store erasure."""

from __future__ import annotations


from datetime import UTC, datetime, timedelta


import json


from multiprocessing import get_context


from pathlib import Path


import stat


import time


from cryptography.fernet import Fernet


import pytest


from evander.didww_sip_bridge import DidwwBridgeStore, PhoneCallStartRequest


from evander.messaging.teams_auth import (
    JsonTeamsCredentialStore,
    JsonTeamsIdentityCredentialStore,
    TeamsAuthCredentials,
    TeamsAuthProtocolError,
)


from evander.teams_meeting import TeamsMeetingBinding, TeamsMeetingStore


from evander.voice_sessions import VoiceSessionStore


def _credentials(label: str = "example") -> TeamsAuthCredentials:
    return TeamsAuthCredentials(
        client_id="client",
        tenant_id="tenant",
        access_token=f"access-secret-{label}",
        refresh_token=f"refresh-secret-{label}",
        account={"id_token": f"identity-secret-{label}"},
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def _prepare_store(kind: str, path: Path):
    if kind == "voice":
        store = VoiceSessionStore(path)
        record, _ = store.prepare_session(
            origin_session_id="origin",
            directory_path="root/test",
            channel="phone",
            request_id="request",
            prompt_metadata={},
            projection={},
        )
        return store, record.voice_session_id
    if kind == "meeting":
        store = TeamsMeetingStore(path)
        store.upsert_binding(TeamsMeetingBinding(meeting_id="meeting", session_id="origin"))
        return store, "meeting"
    if kind == "bridge":
        store = DidwwBridgeStore(path)
        store.create_call(
            PhoneCallStartRequest(
                voice_session_id="voice",
                request_id="request",
                to_number="+15555550100",
            )
        )
        return store, "voice"
    return JsonTeamsIdentityCredentialStore(path, "owner"), "identities"


def _append_in_process(kind: str, path: str, record_id: str, index: int, start) -> None:
    start.wait(timeout=20)
    if kind == "identities":
        store = JsonTeamsIdentityCredentialStore(Path(path), f"identity-{index}")
        read = store._read_object_unlocked

        def delayed_read():
            payload = read()
            time.sleep(0.03)
            return payload

        store._read_object_unlocked = delayed_read
        store.save(_credentials(str(index)))
        return
    store_type = {
        "voice": VoiceSessionStore,
        "meeting": TeamsMeetingStore,
        "bridge": DidwwBridgeStore,
    }[kind]
    store = store_type(Path(path))
    read = store._read_raw

    def delayed_read():
        payload = read()
        time.sleep(0.03)
        return payload

    store._read_raw = delayed_read
    for offset in range(3):
        text = f"message-{index}-{offset}"
        if kind == "meeting":
            store.append_local_event(meeting_id=record_id, event_type="transcript", text=text)
        else:
            store.append_transcript_event(record_id, text=text, speaker="caller")


@pytest.mark.parametrize("kind", ["voice", "meeting", "bridge", "identities"])
def test_communication_transactions_preserve_cross_process_writes(tmp_path: Path, kind: str):
    path = tmp_path / "store.json"
    store, record_id = _prepare_store(kind, path)
    context = get_context("spawn")
    start = context.Barrier(4)
    processes = [
        context.Process(target=_append_in_process, args=(kind, str(path), record_id, index, start))
        for index in range(3)
    ]
    try:
        for process in processes:
            process.start()
        start.wait(timeout=20)
        for process in processes:
            process.join(timeout=20)
        assert [process.exitcode for process in processes] == [0, 0, 0]
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
    if kind == "identities":
        assert set(store.list_all()) == {f"identity-{index}" for index in range(3)}
    else:
        raw = json.loads(path.read_text())
        if kind == "voice":
            events = raw["sessions"][record_id]["transcript_events"]
        elif kind == "bridge":
            events = raw["calls"][record_id]["transcript_events"]
        else:
            events = raw["events"][record_id]
        assert [event["sequence"] for event in events] == list(range(1, 10))
        assert len({event["text"] for event in events}) == 9


@pytest.mark.parametrize("kind", ["voice", "meeting", "bridge"])
@pytest.mark.parametrize("invalid", ["{", "[]", "invalid-section"])
def test_communication_corrupt_state_fails_without_replacing_data(tmp_path, kind, invalid):
    path = tmp_path / "store.json"
    store, record_id = _prepare_store(kind, path)
    if invalid == "invalid-section":
        section = {"voice": "sessions", "meeting": "events", "bridge": "calls"}[kind]
        payload = json.loads(path.read_text())
        payload[section] = []
        invalid = json.dumps(payload)
    path.write_text(invalid)
    with pytest.raises(ValueError):
        if kind == "meeting":
            store.append_local_event(meeting_id=record_id, event_type="error", text="test")
        else:
            store.append_transcript_event(record_id, text="test", speaker="caller")
    assert path.read_text() == invalid


@pytest.mark.parametrize("kind", ["voice", "meeting", "bridge"])
def test_communication_reads_do_not_rewrite_state(tmp_path, kind, monkeypatch):
    path = tmp_path / "store.json"
    store, record_id = _prepare_store(kind, path)

    def unexpected_write(*args):
        pytest.fail("read attempted a write")

    monkeypatch.setattr(store, "_write_object_unlocked", unexpected_write)
    if kind == "voice":
        assert store.get_by_voice_session_id(record_id)
    elif kind == "meeting":
        assert store.get_binding(record_id)
    else:
        assert store.get(record_id)


@pytest.mark.parametrize("identities", [False, True])
def test_teams_credentials_migrate_all_secrets_atomically(tmp_path, identities):
    path = tmp_path / "credentials.json"
    credentials = _credentials()
    legacy = credentials.model_dump(mode="json")
    if identities:
        legacy = {"one": legacy, "two": _credentials("two").model_dump(mode="json")}
    path.write_text(json.dumps(legacy))
    store = (
        JsonTeamsIdentityCredentialStore(path, "one")
        if identities
        else JsonTeamsCredentialStore(path)
    )
    assert store.load() == credentials
    serialized = path.read_text()
    assert json.loads(serialized)["format"] == "evander.teams-credentials.fernet.v1"
    for secret in ("access-secret", "refresh-secret", "identity-secret"):
        assert secret not in serialized
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.key_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.key_path.parent.stat().st_mode) == 0o700
    assert list(tmp_path.glob("*.tmp")) == []
    if identities:
        assert set(store.list_all()) == {"one", "two"}
        store.clear()
        assert set(store.list_all()) == {"two"}
    else:
        store.clear()
        assert store.load() is None


@pytest.mark.parametrize("action", ["load", "save", "clear"])
@pytest.mark.parametrize("damage", ["missing", "wrong", "invalid"])
def test_teams_credentials_missing_or_wrong_key_fail_closed(tmp_path, action, damage):
    store = JsonTeamsCredentialStore(tmp_path / "credentials.json")
    store.save(_credentials())
    original = store.path.read_bytes()
    if damage == "missing":
        store.key_path.unlink()
    else:
        store.key_path.write_bytes(Fernet.generate_key() if damage == "wrong" else b"invalid")
    with pytest.raises(TeamsAuthProtocolError):
        if action == "save":
            store.save(_credentials("replacement"))
        else:
            getattr(store, action)()
    assert store.path.read_bytes() == original
    if damage == "missing":
        assert not store.key_path.exists()


def test_teams_invalid_plaintext_migration_is_redacted_and_preserves_original(tmp_path):
    path = tmp_path / "credentials.json"
    raw = json.dumps({"one": {"access_token": "secret-must-not-leak"}})
    path.write_text(raw)
    store = JsonTeamsIdentityCredentialStore(path, "one")
    with pytest.raises(TeamsAuthProtocolError) as error:
        store.load()
    assert "secret-must-not-leak" not in str(error.value)
    assert error.value.__suppress_context__
    assert path.read_text() == raw
    assert not store.key_path.exists()


def test_teams_migration_write_failure_preserves_plaintext_for_recovery(tmp_path, monkeypatch):
    path = tmp_path / "credentials.json"
    raw = _credentials().model_dump_json()
    path.write_text(raw)
    store = JsonTeamsCredentialStore(path)

    def fail_write(*args):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(store, "_write_json_payload", fail_write)
    with pytest.raises(OSError, match="simulated disk failure"):
        store.load()
    assert path.read_text() == raw


def test_teams_encrypted_read_is_stable_and_unknown_format_is_rejected(tmp_path):
    store = JsonTeamsCredentialStore(tmp_path / "credentials.json")
    store.save(_credentials())
    original = store.path.read_bytes()
    store.load()
    assert store.path.read_bytes() == original
    envelope = json.loads(original)
    envelope["format"] = "future-version"
    store.path.write_text(json.dumps(envelope))
    damaged = store.path.read_bytes()
    with pytest.raises(TeamsAuthProtocolError, match="Unsupported"):
        store.save(_credentials("replacement"))
    assert store.path.read_bytes() == damaged
