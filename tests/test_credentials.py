from __future__ import annotations

import json
import os
import sqlite3

import pytest

from evander.credentials import (
    CredentialConflict,
    CredentialError,
    CredentialStore,
    CredentialUnavailable,
)


@pytest.fixture
def store(tmp_path):
    result = CredentialStore(tmp_path / "runtime", key_file=tmp_path / "keys" / "master.key")
    result.initialize()
    return result


def create(store, **kwargs):
    return store.create(
        owner="root/a",
        kind="connector",
        label="Documents",
        values={"token": "synthetic-secret-42"},
        bindings=[{"consumer": "test"}],
        **kwargs,
    )


def resolve(store, record, **kwargs):
    return store.resolve(
        record["id"],
        directory=kwargs.get("directory", "root/a"),
        binding=kwargs.get("binding", {"consumer": "test"}),
        kind="connector",
    )


def test_metadata_does_not_initialize_store_or_read_key(tmp_path):
    store = CredentialStore(tmp_path / "runtime")
    assert store.list_metadata() == []
    assert store.health() == {"initialized": False, "available": False}
    assert not store.root.exists()


def test_encryption_permissions_and_scope(store):
    record = create(store)
    values, metadata = resolve(store, record)
    assert values == {"token": "synthetic-secret-42"}
    assert "synthetic-secret-42" not in json.dumps(metadata)
    assert b"synthetic-secret-42" not in store.path.read_bytes()
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert store.root.stat().st_mode & 0o777 == 0o700
    assert resolve(store, record, directory="root/a/child")[0] == values
    for context in ({"directory": "root/ab"}, {"binding": {"consumer": "other"}}):
        with pytest.raises(CredentialUnavailable):
            resolve(store, record, **context)


@pytest.mark.parametrize("change", ["owner_directory", "bindings", "status", "ciphertext"])
def test_tampering_is_rejected(store, change):
    record = create(store)
    with sqlite3.connect(store.path) as db:
        raw, ciphertext = db.execute("SELECT metadata,ciphertext FROM credentials").fetchone()
        metadata = json.loads(raw)
        if change == "owner_directory":
            metadata[change] = "root"
        elif change == "bindings":
            metadata[change].append({"consumer": "other"})
        elif change == "status":
            metadata[change] = "disabled"
        else:
            ciphertext = ciphertext[:-1] + bytes([ciphertext[-1] ^ 1])
        db.execute(
            "UPDATE credentials SET metadata=?,ciphertext=?", (json.dumps(metadata), ciphertext)
        )
    with pytest.raises(CredentialUnavailable):
        resolve(store, record)


def test_disable_and_delete_prevent_stale_refresh(store):
    record = create(store)
    disabled = store.update(
        record["id"], owner="root/a", expected_revision=record["revision"], status="disabled"
    )
    with pytest.raises(CredentialConflict):
        store.update(
            record["id"],
            expected_revision=record["revision"],
            values={"token": "late"},
            require_active=True,
        )
    with pytest.raises(CredentialUnavailable):
        resolve(store, record)
    deleted = store.update(
        record["id"], owner="root/a", expected_revision=disabled["revision"], status="deleted"
    )
    with pytest.raises(CredentialConflict):
        store.update(record["id"], expected_revision=deleted["revision"], status="active")


def test_key_loss_preserves_metadata_and_never_reinitializes(store):
    record = create(store)
    store.key_file.unlink()
    assert store.metadata(record["id"])["label"] == "Documents"
    assert store.health() == {"initialized": True, "available": False}
    with pytest.raises(CredentialUnavailable):
        store.initialize()
    assert not store.key_file.exists()


def test_key_rotation_and_restore(store, tmp_path):
    record = create(store)
    old_db = store.path.read_bytes()
    target = tmp_path / "next-key" / "master.key"
    store.rotate_key(target)
    with pytest.raises(CredentialUnavailable):
        resolve(store, record)
    rotated = CredentialStore(store.runtime_dir, key_file=target)
    assert resolve(rotated, record)[0]["token"] == "synthetic-secret-42"
    assert rotated.health()["available"]
    # Test a matched historical encrypted snapshot and key, not application rollback.
    restore = CredentialStore(tmp_path / "restored", key_file=store.key_file)
    restore.root.mkdir(parents=True, mode=0o700)
    restore.path.write_bytes(old_db)
    restore.path.chmod(0o600)
    assert resolve(restore, record)[0]["token"] == "synthetic-secret-42"


def test_symlink_and_in_runtime_keys_are_rejected(tmp_path, store):
    root_link = tmp_path / "linked"
    root_link.symlink_to(store.root, target_is_directory=True)
    unsafe = CredentialStore(tmp_path)
    unsafe.root = root_link
    unsafe.path = root_link / "credentials.db"
    with pytest.raises(CredentialUnavailable):
        unsafe.list_metadata()
    with pytest.raises(CredentialError):
        CredentialStore(tmp_path / "bad", key_file=tmp_path / "bad" / "key").initialize()
    store.path.unlink()
    store.path.symlink_to(tmp_path / "elsewhere")
    with pytest.raises(CredentialUnavailable):
        create(store)


def test_failed_rotation_keeps_database_and_key_usable(store, tmp_path):
    record = create(store)
    occupied = tmp_path / "occupied"
    occupied.write_text("do not replace")
    os.chmod(tmp_path, 0o700)
    with pytest.raises(CredentialUnavailable):
        store.rotate_key(occupied)
    assert resolve(store, record)[0]["token"] == "synthetic-secret-42"


def test_wrong_key_cannot_write_into_existing_empty_store(store, tmp_path):
    wrong = tmp_path / "keys" / "wrong.key"
    wrong.write_bytes(os.urandom(32))
    wrong.chmod(0o600)
    other = CredentialStore(store.runtime_dir, key_file=wrong)
    with pytest.raises(CredentialUnavailable):
        create(other)
    assert store.list_metadata() == []
    assert store.health()["available"]


def test_restart_uses_systemd_delivered_key_without_recreating_it(store, monkeypatch, tmp_path):
    record = create(store)
    delivery = tmp_path / "delivered"
    delivery.mkdir(mode=0o700)
    key = delivery / "evander-credentials-key"
    key.write_bytes(store.key_file.read_bytes())
    key.chmod(0o400)
    monkeypatch.delenv("EVANDER_CREDENTIALS_KEY_FILE", raising=False)
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(delivery))
    restarted = CredentialStore(store.runtime_dir)
    assert resolve(restarted, record)[0]["token"] == "synthetic-secret-42"
    key.unlink()
    assert not restarted.health()["available"]
    assert restarted.metadata(record["id"])["revision"] == record["revision"]
    assert not key.exists()


def test_metadata_corruption_is_a_value_free_error(store):
    record = create(store)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE credentials SET metadata=?", ('{"broken":"secret-canary"}',))
    with pytest.raises(CredentialUnavailable, match="Credential metadata is invalid"):
        store.metadata(record["id"])


def test_expiry_and_unsafe_lock_files_fail_closed(store):
    record = create(store, expires_at=1)
    with pytest.raises(CredentialUnavailable, match="expired"):
        resolve(store, record)
    lock = store.root / (record["id"] + ".lock")
    lock.symlink_to(store.path)
    with pytest.raises(CredentialUnavailable):
        with store.lock(record["id"]):
            pytest.fail("symlink lock accepted")


def test_credentials_preserve_directory_names_with_spaces_and_unicode(store):
    record = store.create(
        owner="root/Årsbokslut team",
        kind="connector",
        label="Books",
        values={"token": "synthetic-token"},
        bindings=[{"consumer": "test"}],
    )
    values, _ = store.resolve(
        record["id"],
        directory="root/Årsbokslut team/Child",
        binding={"consumer": "test"},
        kind="connector",
    )
    assert values["token"] == "synthetic-token"


def test_initialization_never_generates_key_for_a_dangling_database_symlink(tmp_path):
    store = CredentialStore(tmp_path / "runtime", key_file=tmp_path / "keys" / "master.key")
    store.root.mkdir(parents=True, mode=0o700)
    store.path.symlink_to(tmp_path / "lost.db")
    with pytest.raises(CredentialUnavailable):
        store.initialize()
    assert not store.key_file.exists()
