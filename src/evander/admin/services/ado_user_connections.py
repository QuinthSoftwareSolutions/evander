"""Host-only personal ADO credentials and commit-bound publication contexts."""

from __future__ import annotations

import base64
import os
import re
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from cryptography.fernet import Fernet, InvalidToken


class AdoConnectionError(ValueError):
    pass


def normalize_organization(value: str) -> str:
    value = value.strip().rstrip("/")
    if value.startswith("https://dev.azure.com/"):
        value = value.removeprefix("https://dev.azure.com/")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,99}", value):
        raise AdoConnectionError("Select an Azure DevOps organization")
    return value.lower()


def normalize_scope(value: str) -> str:
    value = value.strip().strip("/")
    if (
        not value
        or value.split("/")[0] != "root"
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise AdoConnectionError("Invalid directory path")
    return value


class AdoUserConnectionService:
    def __init__(self, runtime_dir: Path) -> None:
        self.root = Path(runtime_dir) / "ado-user-connections"
        self.key_path = Path(runtime_dir) / "secrets" / "ado-user-connections.key"

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
        path = self.root / "connections.db"
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        db = sqlite3.connect(path, timeout=30)
        try:
            db.row_factory = sqlite3.Row
            db.execute("""CREATE TABLE IF NOT EXISTS connections (
                login TEXT NOT NULL, organization TEXT NOT NULL, directory_path TEXT NOT NULL,
                identity_id TEXT NOT NULL, display_name TEXT NOT NULL, email TEXT,
                encrypted_pat TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY(login, organization, directory_path),
                UNIQUE(organization, directory_path, identity_id))""")
            db.execute("""CREATE TABLE IF NOT EXISTS publications (
                id TEXT PRIMARY KEY, managed_repo_id TEXT NOT NULL, commit_sha TEXT NOT NULL,
                directory_path TEXT NOT NULL, organization TEXT NOT NULL, identity_id TEXT,
                fallback_reason TEXT, source_ref TEXT NOT NULL, created_at TEXT NOT NULL)""")
            db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS publications_source
                ON publications(managed_repo_id, commit_sha, directory_path, source_ref)""")
            db.execute("""CREATE TABLE IF NOT EXISTS pr_attempts (
                attempt_key TEXT PRIMARY KEY, attempted_at TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS publication_results (
                id INTEGER PRIMARY KEY, context_id TEXT, pr_id TEXT, intended_creator_id TEXT,
                actual_creator_id TEXT, credential_source TEXT, fallback_reason TEXT,
                recorded_at TEXT NOT NULL)""")
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def validate_pat(organization: str, pat: str) -> dict[str, str | None]:
        organization = normalize_organization(organization)
        if not pat or len(pat) > 4096 or any(char.isspace() for char in pat):
            raise AdoConnectionError("Enter a valid personal access token")
        try:
            response = httpx.get(
                f"https://dev.azure.com/{organization}/_apis/connectionData",
                params={"api-version": "7.1-preview.1"},
                auth=("", pat),
                timeout=15,
                follow_redirects=False,
            )
            response.raise_for_status()
            identity = response.json().get("authenticatedUser") or {}
            identity_id = str(identity.get("id") or "").strip().lower()
            if not identity_id or identity_id == "00000000-0000-0000-0000-000000000000":
                raise AdoConnectionError("ADO did not identify the PAT owner")
            properties = identity.get("properties") or {}
            account = properties.get("Account") or {}
            email = account.get("$value") if isinstance(account, dict) else None
            return {
                "identity_id": identity_id,
                "display_name": str(
                    identity.get("providerDisplayName")
                    or identity.get("customDisplayName")
                    or identity_id
                ),
                "email": str(email) if email else None,
            }
        except (httpx.HTTPError, ValueError, AttributeError):
            # Provider responses and exceptions must never expose credentials.
            raise AdoConnectionError("Could not validate this PAT with Azure DevOps") from None

    def save(self, *, login: str, organization: str, directory_path: str, pat: str) -> dict:
        organization = normalize_organization(organization)
        directory_path = normalize_scope(directory_path)
        identity = self.validate_pat(organization, pat)
        ciphertext = self._cipher().encrypt(pat.encode()).decode()
        try:
            with self._database() as db:
                db.execute(
                    """INSERT INTO connections VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(login, organization, directory_path) DO UPDATE SET
                    identity_id=excluded.identity_id, display_name=excluded.display_name,
                    email=excluded.email, encrypted_pat=excluded.encrypted_pat,
                    updated_at=excluded.updated_at""",
                    (
                        login.lower(),
                        organization,
                        directory_path,
                        identity["identity_id"],
                        identity["display_name"],
                        identity["email"],
                        ciphertext,
                        datetime.now(UTC).isoformat(),
                    ),
                )
        except sqlite3.IntegrityError:
            raise AdoConnectionError(
                "This ADO identity is already connected by another user in this directory"
            ) from None
        return {"organization": organization, "directory_path": directory_path, **identity}

    def list_for_user(self, login: str) -> list[dict]:
        with self._database() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT organization, directory_path, identity_id, display_name, email, updated_at "
                    "FROM connections WHERE login=? ORDER BY organization, directory_path",
                    (login.lower(),),
                )
            ]

    def remove(self, *, login: str, organization: str, directory_path: str) -> None:
        with self._database() as db:
            db.execute(
                "DELETE FROM connections WHERE login=? AND organization=? AND directory_path=?",
                (
                    login.lower(),
                    normalize_organization(organization),
                    normalize_scope(directory_path),
                ),
            )

    def remove_user(self, login: str) -> None:
        with self._database() as db:
            db.execute("DELETE FROM connections WHERE login=?", (login.lower(),))

    def issue_publication_context(
        self,
        *,
        managed_repo_id: str,
        commit_sha: str,
        directory_path: str,
        organization: str,
        identity_id: str | None,
        source_ref: str,
        fallback_reason: str | None = None,
    ) -> str:
        """Called only by trusted release services, never exposed as a guest operation."""
        context_id = str(uuid4())
        organization = normalize_organization(organization)
        directory_path = normalize_scope(directory_path)
        identity_id = identity_id.lower() if identity_id else None
        with self._database() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM publications WHERE managed_repo_id=? AND commit_sha=? "
                "AND directory_path=? AND source_ref=?",
                (managed_repo_id, commit_sha.lower(), directory_path, source_ref),
            ).fetchone()
            if existing is not None:
                if (
                    existing["organization"],
                    existing["identity_id"],
                    existing["fallback_reason"],
                ) != (
                    organization,
                    identity_id,
                    fallback_reason,
                ):
                    raise AdoConnectionError("Published release attribution is immutable")
                return existing["id"]
            db.execute(
                "INSERT INTO publications VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    context_id,
                    managed_repo_id,
                    commit_sha.lower(),
                    directory_path,
                    organization,
                    identity_id,
                    fallback_reason,
                    source_ref,
                    datetime.now(UTC).isoformat(),
                ),
            )
        return context_id

    def resolve_publication(
        self,
        context_id: str,
        *,
        managed_repo_id: str,
        commit_sha: str,
        directory_path: str,
        organization: str,
    ) -> tuple[dict, str | None]:
        directory_path = normalize_scope(directory_path)
        with self._database() as db:
            row = db.execute("SELECT * FROM publications WHERE id=?", (context_id,)).fetchone()
            if row is None or any(
                (
                    row["managed_repo_id"] != managed_repo_id,
                    row["commit_sha"] != commit_sha.lower(),
                    row["directory_path"] != normalize_scope(directory_path),
                    row["organization"] != normalize_organization(organization),
                )
            ):
                raise AdoConnectionError(
                    "Publication attribution does not match this repository and commit"
                )
            context = dict(row)
            candidates = db.execute(
                "SELECT * FROM connections WHERE organization=? AND identity_id=?",
                (row["organization"], row["identity_id"]),
            ).fetchall()
        candidates = [
            item
            for item in candidates
            if directory_path == item["directory_path"]
            or directory_path.startswith(item["directory_path"] + "/")
        ]
        if not candidates:
            context["fallback_reason"] = context["fallback_reason"] or "personal_pat_missing"
            return context, None
        candidate = max(candidates, key=lambda item: len(item["directory_path"]))
        try:
            pat = self._cipher().decrypt(candidate["encrypted_pat"].encode()).decode()
        except (InvalidToken, ValueError, OSError):
            context["fallback_reason"] = "personal_pat_unavailable"
            return context, None
        return context, "Basic " + base64.b64encode(f":{pat}".encode()).decode()

    def has_unconfirmed_attempt(self, attempt_key: str) -> bool:
        with self._database() as db:
            return (
                db.execute(
                    "SELECT 1 FROM pr_attempts WHERE attempt_key=?", (attempt_key,)
                ).fetchone()
                is not None
            )

    def mark_attempt(self, attempt_key: str, *, confirmed: bool = False) -> None:
        with self._database() as db:
            if confirmed:
                db.execute("DELETE FROM pr_attempts WHERE attempt_key=?", (attempt_key,))
            else:
                db.execute(
                    "INSERT OR IGNORE INTO pr_attempts VALUES (?, ?)",
                    (attempt_key, datetime.now(UTC).isoformat()),
                )

    def record_publication(self, context_id: str | None, result: dict) -> None:
        actual = result.get("actual_creator") or {}
        with self._database() as db:
            db.execute(
                "INSERT INTO publication_results VALUES (NULL, ?, ?, ?, ?, ?, ?, ?)",
                (
                    context_id,
                    result.get("pr_id"),
                    result.get("intended_creator_id"),
                    actual.get("id"),
                    result.get("credential_source"),
                    result.get("fallback_reason"),
                    datetime.now(UTC).isoformat(),
                ),
            )
