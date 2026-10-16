from __future__ import annotations

import base64
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from typing import Any, Iterator
from uuid import uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

DEFAULT_KEY_FILE = Path("/etc/evander/credentials/master.key")
KEY_CREDENTIAL_NAME = "evander-credentials-key"
MAX_BUNDLE_BYTES = 65536
_REF = re.compile(r"cred_[0-9a-f]{32}\Z")


class CredentialError(RuntimeError):
    """A safe diagnostic: never include payloads or underlying exception text."""


class CredentialUnavailable(CredentialError):
    pass


class CredentialConflict(CredentialError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def scope_path(value: str) -> str:
    if (
        not isinstance(value, str)
        or (value != "root" and not value.startswith("root/"))
        or any(ord(char) < 32 for char in value)
    ):
        raise CredentialError("Invalid credential directory")
    if any(not part or part != part.strip() or part in {".", ".."} for part in value.split("/")):
        raise CredentialError("Invalid credential directory")
    return value


def contains_scope(owner: str, directory: str) -> bool:
    return directory == owner or directory.startswith(owner + "/")


def secure_directory(path: Path, *, create: bool = False) -> None:
    """Walk without following symlinks, including intermediate directories."""
    path = path.absolute()
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            if part in {".", ".."}:
                raise CredentialUnavailable("Invalid credential storage path")
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        if os.fstat(fd).st_mode & 0o077:
            raise CredentialUnavailable("Credential directory must have mode 0700")
    except OSError:
        raise CredentialUnavailable("Credential directory is unavailable") from None
    finally:
        os.close(fd)


def protected_read(
    path: Path, *, limit: int = MAX_BUNDLE_BYTES, legacy_permissions: bool = False
) -> bytes:
    # Ancestors are checked without requiring 0700 for the systemd credential directory.
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.absolute().parts[1:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        file_fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(file_fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or (not legacy_permissions and info.st_mode & 0o077):
                raise CredentialUnavailable("Credential file must be a private regular file")
            data = stream.read(limit + 1)
            if len(data) > limit:
                raise CredentialUnavailable("Credential file exceeds its size limit")
            return data
    except OSError:
        raise CredentialUnavailable("Credential file is unavailable") from None
    finally:
        os.close(fd)


def write_private(path: Path, data: bytes) -> None:
    secure_directory(path.parent, create=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    fsync_directory(path.parent)


def fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def private_lock(path: Path, *, exclusive: bool) -> Iterator[None]:
    secure_directory(path.parent)
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise CredentialUnavailable("Credential lock must be a private regular file")
            fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            yield
        finally:
            os.close(fd)
    except OSError:
        raise CredentialUnavailable("Credential lock is unavailable") from None


class CredentialStore:
    """Encrypted records with CAS updates; construction/status never initializes custody.

    This is an in-process domain boundary. All code with access to its key retains
    the authority to decrypt the store; deployments must scope that access.
    """

    def __init__(self, runtime_dir: Path | str, *, key_file: Path | None = None) -> None:
        self.runtime_dir = Path(runtime_dir).absolute()
        self.root = self.runtime_dir / "credentials"
        self.path = self.root / "credentials.db"
        self.key_file = key_file

    def resolved_key_file(self) -> Path:
        if self.key_file is not None:
            return self.key_file.absolute()
        explicit = os.environ.get("EVANDER_CREDENTIALS_KEY_FILE")
        if explicit:
            return Path(explicit).expanduser().absolute()
        directory = os.environ.get("CREDENTIALS_DIRECTORY")
        if directory:
            return Path(directory) / KEY_CREDENTIAL_NAME
        return DEFAULT_KEY_FILE

    def _key(self) -> bytes:
        key_path = self.resolved_key_file()
        if key_path.absolute().is_relative_to(self.runtime_dir):
            raise CredentialUnavailable("Credential key must be outside the runtime directory")
        data = protected_read(key_path, limit=32)
        if len(data) != 32:
            raise CredentialUnavailable("Credential key must contain 32 bytes")
        return data

    def initialize(self) -> dict:
        key_path = self.resolved_key_file()
        if key_path.is_relative_to(self.runtime_dir):
            raise CredentialError("Credential key must be outside the runtime directory")
        secure_directory(self.root, create=True)
        if not key_path.exists():
            if self.path.exists() or self.path.is_symlink():
                raise CredentialUnavailable(
                    "Existing credential database requires its original key"
                )
            write_private(key_path, AESGCM.generate_key(bit_length=256))
        self._key()
        with self._database(create=True) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "CREATE TABLE IF NOT EXISTS credentials (id TEXT PRIMARY KEY, metadata TEXT NOT NULL, ciphertext BLOB NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS migrations (id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
            canary = db.execute("SELECT payload FROM migrations WHERE id='key-check'").fetchone()
            if canary:
                self._check_key(db)
            else:
                if db.execute("SELECT 1 FROM credentials LIMIT 1").fetchone():
                    raise CredentialUnavailable("Existing credential key check is missing")
                db.execute(
                    "INSERT INTO migrations VALUES ('key-check', ?)",
                    (base64.b64encode(self._encrypt({"canary": 1}, {})).decode(),),
                )
        return {"initialized": True}

    @contextmanager
    def _maintenance_lock(self, *, exclusive: bool = False) -> Iterator[None]:
        with private_lock(self.root / "maintenance.lock", exclusive=exclusive):
            yield

    @contextmanager
    def _database(
        self, *, create: bool = False, maintenance: bool = True
    ) -> Iterator[sqlite3.Connection]:
        if maintenance:
            with self._maintenance_lock():
                with self._database(create=create, maintenance=False) as db:
                    yield db
            return
        try:
            secure_directory(self.root)
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | (os.O_CREAT if create else 0)
            fd = os.open(self.path, flags, 0o600)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_mode & 0o077:
                    raise CredentialUnavailable("Credential database must have mode 0600")
            finally:
                os.close(fd)
            for suffix in ("-journal", "-wal", "-shm"):
                sidecar = Path(str(self.path) + suffix)
                if sidecar.is_symlink():
                    raise CredentialUnavailable("Credential database sidecar must not be a symlink")
            db = sqlite3.connect(self.path, timeout=10)
            try:
                db.execute("PRAGMA busy_timeout=10000")
                with db:
                    yield db
            finally:
                db.close()
        except (OSError, sqlite3.Error):
            raise CredentialUnavailable("Credential storage is unavailable") from None

    @contextmanager
    def lock(self, ref: str) -> Iterator[None]:
        self._validate_ref(ref)
        with private_lock(self.root / (ref + ".lock"), exclusive=True):
            yield

    @staticmethod
    def _validate_ref(ref: str) -> None:
        if not isinstance(ref, str) or not _REF.fullmatch(ref):
            raise CredentialError("Invalid credential reference")

    def _encrypt(self, metadata: dict, values: dict) -> bytes:
        payload = canonical(values)
        if len(payload) > MAX_BUNDLE_BYTES:
            raise CredentialError("Credential bundle exceeds its size limit")
        nonce = os.urandom(12)
        return nonce + AESGCM(self._key()).encrypt(nonce, payload, canonical(metadata))

    def _decrypt(self, metadata: dict, ciphertext: bytes) -> dict:
        try:
            payload = AESGCM(self._key()).decrypt(
                ciphertext[:12], ciphertext[12:], canonical(metadata)
            )
            value = json.loads(payload)
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (InvalidTag, ValueError, TypeError):
            raise CredentialUnavailable("Credential authentication failed") from None

    @staticmethod
    def revision(metadata: dict) -> str:
        return f"{metadata['value_revision']}:{metadata['policy_revision']}"

    def _row(self, db: sqlite3.Connection, ref: str) -> tuple[dict, bytes]:
        self._validate_ref(ref)
        row = db.execute(
            "SELECT metadata, ciphertext FROM credentials WHERE id=?", (ref,)
        ).fetchone()
        if row is None:
            raise CredentialUnavailable("Credential is unavailable")
        metadata = self._parse_metadata(row[0])
        if metadata.get("id") != ref:
            raise CredentialUnavailable("Credential identity mismatch")
        return metadata, row[1]

    @staticmethod
    def _parse_metadata(raw: str) -> dict:
        try:
            value = json.loads(raw)
            if (
                not isinstance(value, dict)
                or not _REF.fullmatch(value["id"])
                or value["kind"] not in {"connector", "teams_oauth", "resource"}
                or value["status"] not in {"active", "disabled", "deleted"}
                or not isinstance(value["bindings"], list)
                or not isinstance(value["label"], str)
                or not isinstance(value["value_revision"], int)
                or not isinstance(value["policy_revision"], int)
            ):
                raise ValueError
            scope_path(value["owner_directory"])
            CredentialStore._validate_expiry(value["expires_at"])
            return value
        except (KeyError, TypeError, ValueError, CredentialError):
            raise CredentialUnavailable("Credential metadata is invalid") from None

    @staticmethod
    def _validate_expiry(value: float | None) -> None:
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise CredentialError("Expiry must be a finite timestamp or null")

    def _check_key(self, db: sqlite3.Connection) -> None:
        row = db.execute("SELECT payload FROM migrations WHERE id='key-check'").fetchone()
        if not row:
            raise CredentialUnavailable("Credential key check is missing")
        try:
            self._decrypt({"canary": 1}, base64.b64decode(row[0], validate=True))
        except ValueError:
            raise CredentialUnavailable("Credential key check is invalid") from None

    def metadata(self, ref: str) -> dict:
        with self._database() as db:
            metadata, _ = self._row(db, ref)
        return {**metadata, "revision": self.revision(metadata)}

    def health(self) -> dict:
        if not self.path.exists():
            return {"initialized": False, "available": False}
        try:
            with self._database() as db:
                self._check_key(db)
            return {"initialized": True, "available": True}
        except CredentialError:
            return {"initialized": True, "available": False}

    def list_metadata(self) -> list[dict]:
        if not self.path.exists() and not self.path.is_symlink():
            return []
        with self._database() as db:
            rows = db.execute("SELECT metadata FROM credentials ORDER BY id").fetchall()
        return [
            {**(m := self._parse_metadata(row[0])), "revision": self.revision(m)} for row in rows
        ]

    def create(
        self,
        *,
        owner: str,
        kind: str,
        label: str,
        bindings: list[dict],
        values: dict,
        expires_at: float | None = None,
        ref: str | None = None,
        migration_id: str | None = None,
        source_digest: str | None = None,
    ) -> dict:
        ref = ref or "cred_" + uuid4().hex
        self._validate_ref(ref)
        if kind not in {"connector", "teams_oauth", "resource"}:
            raise CredentialError("Unsupported credential type")
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 128:
            raise CredentialError("Credential label must contain 1 to 128 characters")
        self._validate_expiry(expires_at)
        if migration_id == "key-check":
            raise CredentialError("Reserved migration name")
        metadata = {
            "id": ref,
            "owner_directory": scope_path(owner),
            "kind": kind,
            "label": label.strip(),
            "bindings": bindings,
            "status": "active",
            "expires_at": expires_at,
            "value_revision": 1,
            "policy_revision": 1,
            "updated_at": time.time(),
        }
        with self._database() as db:
            db.execute("BEGIN IMMEDIATE")
            self._check_key(db)
            if db.execute("SELECT 1 FROM credentials WHERE id=?", (ref,)).fetchone():
                raise CredentialConflict("Credential already exists")
            db.execute(
                "INSERT INTO credentials VALUES (?, ?, ?)",
                (ref, canonical(metadata).decode(), self._encrypt(metadata, values)),
            )
            if migration_id:
                db.execute(
                    "INSERT INTO migrations VALUES (?, ?)",
                    (
                        migration_id,
                        canonical(
                            {
                                "credential_ref": ref,
                                "phase": "imported",
                                "source_digest": source_digest,
                            }
                        ).decode(),
                    ),
                )
        return self.metadata(ref)

    def authorize(self, metadata: dict, *, directory: str, binding: dict, kind: str) -> None:
        scope_path(directory)
        if (
            metadata["status"] != "active"
            or metadata["kind"] != kind
            or not contains_scope(metadata["owner_directory"], directory)
            or binding not in metadata["bindings"]
        ):
            raise CredentialUnavailable("Credential use is not authorized")
        if (
            kind != "teams_oauth"
            and metadata["expires_at"] is not None
            and metadata["expires_at"] <= time.time()
        ):
            raise CredentialUnavailable("Credential has expired")

    def resolve(self, ref: str, *, directory: str, binding: dict, kind: str) -> tuple[dict, dict]:
        with self._database() as db:
            metadata, ciphertext = self._row(db, ref)
            self.authorize(metadata, directory=directory, binding=binding, kind=kind)
            values = self._decrypt(metadata, ciphertext)
        return values, {**metadata, "revision": self.revision(metadata)}

    def update(
        self,
        ref: str,
        *,
        expected_revision: str,
        owner: str | None = None,
        values: dict | None = None,
        bindings: list[dict] | None = None,
        status: str | None = None,
        expires_at: float | None = None,
        require_active: bool = False,
    ) -> dict:
        if not expected_revision:
            raise CredentialConflict("expected_revision is required")
        self._validate_expiry(expires_at)
        with self._database() as db:
            db.execute("BEGIN IMMEDIATE")
            metadata, ciphertext = self._row(db, ref)
            if owner is not None and metadata["owner_directory"] != scope_path(owner):
                raise CredentialUnavailable("Credential must be managed in its owning directory")
            if self.revision(metadata) != expected_revision:
                raise CredentialConflict("Credential changed; reload before retrying")
            if require_active and metadata["status"] != "active":
                raise CredentialConflict("Credential was disconnected or disabled")
            if metadata["status"] == "deleted":
                raise CredentialConflict("Deleted credentials cannot be restored")
            old_values = self._decrypt(metadata, ciphertext)
            if values is not None:
                metadata["value_revision"] += 1
                metadata["expires_at"] = expires_at
            if bindings is not None or status is not None:
                metadata["policy_revision"] += 1
            if bindings is not None:
                metadata["bindings"] = bindings
            if status is not None:
                if status not in {"active", "disabled", "deleted"}:
                    raise CredentialError("Invalid credential status")
                metadata["status"] = status
            metadata["updated_at"] = time.time()
            updated = values if values is not None else old_values
            encrypted = b"" if status == "deleted" else self._encrypt(metadata, updated)
            db.execute(
                "UPDATE credentials SET metadata=?, ciphertext=? WHERE id=?",
                (canonical(metadata).decode(), encrypted, ref),
            )
        return self.metadata(ref)

    def migration(self, name: str) -> dict | None:
        if not self.path.exists():
            return None
        with self._database() as db:
            row = db.execute("SELECT payload FROM migrations WHERE id=?", (name,)).fetchone()
        return json.loads(row[0]) if row else None

    def record_migration(self, name: str, payload: dict) -> None:
        if name == "key-check":
            raise CredentialError("Reserved migration name")
        with self._database() as db:
            db.execute(
                "INSERT INTO migrations VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                (name, canonical(payload).decode()),
            )

    def backup(self, label: str, *, maintenance: bool = True) -> Path:
        """Create a new, durable encrypted recovery snapshot for each operation."""
        if not re.fullmatch(r"[a-z-]+", label):
            raise CredentialError("Invalid recovery snapshot label")
        target = self.root / f"{label}-{uuid4().hex}.db"
        temporary = target.with_suffix(".partial")
        try:
            with self._database(maintenance=maintenance) as source:
                self._check_key(source)
                write_private(temporary, b"")
                with sqlite3.connect(temporary) as destination:
                    source.backup(destination)
                fd = os.open(temporary, os.O_RDONLY | os.O_NOFOLLOW)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
                os.rename(temporary, target)
                fsync_directory(self.root)
            return target
        except (OSError, sqlite3.Error):
            raise CredentialUnavailable("Credential recovery snapshot failed") from None
        finally:
            temporary.unlink(missing_ok=True)

    def rotate_key(self, new_key_file: Path) -> dict:
        """Offline rotation. The old key is retained; activation is an operator step.

        New ciphertext is committed atomically and the new key is written/fsynced
        first. A failed commit leaves the old database and original key usable.
        """
        if new_key_file.absolute().is_relative_to(self.runtime_dir):
            raise CredentialError("Credential key must be outside the runtime directory")
        new_key = AESGCM.generate_key(bit_length=256)
        with self._maintenance_lock(exclusive=True):
            recovery = self.backup("key-rotation-recovery", maintenance=False)
            with self._database(maintenance=False) as db:
                db.execute("BEGIN IMMEDIATE")
                self._check_key(db)
                rows = db.execute("SELECT id, metadata, ciphertext FROM credentials").fetchall()
                replacement = []
                for ref, raw, ciphertext in rows:
                    metadata = self._parse_metadata(raw)
                    if metadata["status"] == "deleted":
                        continue
                    values = self._decrypt(metadata, ciphertext)
                    nonce = os.urandom(12)
                    encrypted = nonce + AESGCM(new_key).encrypt(
                        nonce, canonical(values), canonical(metadata)
                    )
                    replacement.append((encrypted, ref))
                write_private(new_key_file.absolute(), new_key)
                db.executemany("UPDATE credentials SET ciphertext=? WHERE id=?", replacement)
                nonce = os.urandom(12)
                canary = nonce + AESGCM(new_key).encrypt(nonce, b"{}", canonical({"canary": 1}))
                db.execute(
                    "UPDATE migrations SET payload=? WHERE id='key-check'",
                    (base64.b64encode(canary).decode(),),
                )
        return {
            "rotated": len(replacement),
            "restart_required": True,
            "recovery_snapshot": str(recovery),
        }


def binding_fingerprint(payload: dict) -> str:
    return hashlib.sha256(canonical(payload)).hexdigest()
