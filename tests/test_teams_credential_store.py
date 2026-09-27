from __future__ import annotations

import stat
from datetime import UTC, datetime
from multiprocessing import get_context
from multiprocessing.synchronize import Event as ProcessEvent
from pathlib import Path
from threading import Event as ThreadEvent
from threading import Thread

import pytest

from evander.messaging import teams_auth
from evander.messaging.teams_auth import (
    JsonTeamsCredentialStore,
    JsonTeamsIdentityCredentialStore,
    TeamsAuthCredentials,
)


def _credentials(token: str) -> TeamsAuthCredentials:
    return TeamsAuthCredentials(
        client_id="client",
        tenant_id="tenant",
        access_token=token,
        refresh_token=f"refresh-{token}",
        expires_at=datetime(2026, 10, 1, tzinfo=UTC),
    )


def _save_second_identity(path: Path, started: ProcessEvent, finished: ProcessEvent) -> None:
    started.set()
    JsonTeamsIdentityCredentialStore(path, "second").save(_credentials("two"))
    finished.set()


@pytest.mark.parametrize("identity_scoped", [False, True])
def test_failed_credential_replacement_preserves_prior_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    identity_scoped: bool,
) -> None:
    path = tmp_path / "credentials.json"
    store = (
        JsonTeamsIdentityCredentialStore(path, "identity")
        if identity_scoped
        else JsonTeamsCredentialStore(path)
    )
    store.save(_credentials("prior"))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    path.chmod(0o640)
    original_bytes = path.read_bytes()

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("simulated replacement failure")

    with monkeypatch.context() as patch:
        patch.setattr(teams_auth.os, "replace", fail_replace)
        with pytest.raises(OSError, match="simulated replacement failure"):
            store.save(_credentials("new"))

    assert path.read_bytes() == original_bytes
    assert store.load().access_token == "prior"
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert list(tmp_path.glob(".credentials.json.*.tmp")) == []

    store.save(_credentials("new"))
    assert store.load().access_token == "new"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_identity_stores_serialize_read_modify_write_across_processes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "credentials.json"
    first = JsonTeamsIdentityCredentialStore(path, "first")
    second = JsonTeamsIdentityCredentialStore(path, "second")
    first_is_writing = ThreadEvent()
    allow_first_write = ThreadEvent()
    process_context = get_context("spawn")
    second_started = process_context.Event()
    second_finished = process_context.Event()
    errors: list[Exception] = []
    write_payload = first._write_object_unlocked

    def paused_write(payload: dict[str, object]) -> None:
        first_is_writing.set()
        if not allow_first_write.wait(5):
            raise TimeoutError("first credential write was not released")
        write_payload(payload)

    def save_first() -> None:
        try:
            first.save(_credentials("one"))
        except Exception as exc:
            errors.append(exc)

    monkeypatch.setattr(first, "_write_object_unlocked", paused_write)
    first_thread = Thread(target=save_first)
    second_process = process_context.Process(
        target=_save_second_identity,
        args=(path, second_started, second_finished),
    )
    first_thread.start()
    try:
        assert first_is_writing.wait(5)
        second_process.start()
        assert second_started.wait(5)
        assert not second_finished.wait(0.2)
    finally:
        allow_first_write.set()
        first_thread.join(timeout=5)
        if second_process.pid is not None:
            second_process.join(timeout=5)
            if second_process.is_alive():
                second_process.terminate()
                second_process.join(timeout=5)

    assert not first_thread.is_alive()
    assert second_process.exitcode == 0
    assert errors == []
    assert {
        identity: credentials.access_token for identity, credentials in first.list_all().items()
    } == {"first": "one", "second": "two"}

    first.clear()
    assert second.load().access_token == "two"
    second.clear()
    assert second.load() is None
    assert first.list_all() == {}
