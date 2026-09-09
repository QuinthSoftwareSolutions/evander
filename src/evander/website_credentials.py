"""Encrypted host-only website credentials, shared by Admin Web and the relay."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from uuid import uuid4

from cryptography.fernet import Fernet


def credential_binding(profile) -> str:
    fields = {key: getattr(profile, key) for key in (
        "browser_origin", "login_url", "credential_fields", "static_fields",
    )}
    fields["public_credential_fields"] = sorted(profile.public_credential_fields)
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


class WebsiteCredentialStore:
    def __init__(self, runtime_dir: Path) -> None:
        self.root = Path(runtime_dir) / "website-credentials"
        self.key_path = Path(runtime_dir) / "secrets" / "website-credentials.key"

    def _cipher(self) -> Fernet:
        self.key_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.key_path.exists():
            fd, temporary = tempfile.mkstemp(dir=self.key_path.parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(Fernet.generate_key())
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(temporary, self.key_path)
                except FileExistsError:
                    pass
            finally:
                os.unlink(temporary)
        fd = os.open(self.key_path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            return Fernet(stream.read())

    @contextmanager
    def _database(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.root / "credentials.db"
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        db = sqlite3.connect(path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("""CREATE TABLE IF NOT EXISTS credentials (
                id TEXT PRIMARY KEY, directory_path TEXT NOT NULL, profile_id TEXT NOT NULL,
                binding TEXT NOT NULL, encrypted_values TEXT NOT NULL,
                fields_json TEXT NOT NULL, updated_at REAL NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS login_tests (
                directory_path TEXT NOT NULL, profile_id TEXT NOT NULL, revision TEXT NOT NULL,
                checked_at REAL NOT NULL, status TEXT NOT NULL, expires_at REAL,
                PRIMARY KEY(directory_path, profile_id))""")
            with db:
                yield db
        finally:
            db.close()

    def put(self, scope: str, profile_id: str, profile, values: dict[str, str]) -> str:
        ref = uuid4().hex
        encrypted = self._cipher().encrypt(json.dumps(values).encode()).decode()
        with self._database() as db:
            db.execute("INSERT INTO credentials VALUES (?, ?, ?, ?, ?, ?, ?)", (
                ref, scope, profile_id, credential_binding(profile), encrypted,
                json.dumps(sorted(values)), time.time(),
            ))
        return ref

    def _row(self, ref: str | None, scope: str, profile_id: str, profile):
        with self._database() as db:
            row = db.execute("SELECT * FROM credentials WHERE id=?", (ref,)).fetchone()
        if row is None:
            return None
        owner = row["directory_path"]
        if (scope != owner and not scope.startswith(owner + "/")) or row["profile_id"] != profile_id:
            return None
        if row["binding"] != credential_binding(profile):
            return None
        return row

    def read(self, ref: str | None, scope: str, profile_id: str, profile) -> dict[str, str]:
        row = self._row(ref, scope, profile_id, profile)
        if row is None:
            return {}
        return json.loads(self._cipher().decrypt(row["encrypted_values"].encode()))

    def status(self, scope: str, profile_id: str, profile) -> dict:
        row = self._row(profile.credential_ref, scope, profile_id, profile)
        return {"configured_fields": json.loads(row["fields_json"]) if row else [],
                "updated_at": row["updated_at"] if row else None,
                "owner_directory": row["directory_path"] if row else None}

    def delete(self, ref: str | None, scope: str, profile_id: str) -> None:
        with self._database() as db:
            db.execute("DELETE FROM credentials WHERE id=? AND directory_path=? AND profile_id=?",
                       (ref, scope, profile_id))

    def test_status(self, scope: str, profile_id: str, revision: str) -> dict | None:
        with self._database() as db:
            row = db.execute("SELECT checked_at, status, expires_at FROM login_tests WHERE directory_path=? AND profile_id=? AND revision=?",
                             (scope, profile_id, revision)).fetchone()
        return dict(row) if row else None

    def start_test(self, scope: str, profile_id: str, revision: str) -> bool:
        with self._database() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT checked_at FROM login_tests WHERE directory_path=? AND profile_id=?",
                                  (scope, profile_id)).fetchone()
            if previous and previous["checked_at"] + 60 > time.time():
                return False
            db.execute("INSERT OR REPLACE INTO login_tests VALUES (?, ?, ?, ?, ?, NULL)",
                       (scope, profile_id, revision, time.time(), "testing"))
        return True

    def finish_test(self, scope: str, profile_id: str, revision: str, status: str, expiry=None):
        with self._database() as db:
            db.execute("UPDATE login_tests SET status=?, expires_at=? WHERE directory_path=? AND profile_id=? AND revision=?",
                       (status, expiry, scope, profile_id, revision))
