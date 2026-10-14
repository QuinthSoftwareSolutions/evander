"""Host-only personal ADO credentials and commit-bound publication contexts."""

from __future__ import annotations

import base64
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import tempfile
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken

from evander_azure_devops_addon.oauth import AdoOAuthConfig, AdoOAuthError, SCOPES, exchange_token, has_required_scopes


from evander.admin.services.ado_user_connections import AdoConnectionError


def normalize_organization(value: str) -> str:
    value = value.strip().rstrip("/")
    if value.startswith("https://dev.azure.com/"):
        value = value.removeprefix("https://dev.azure.com/")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,99}", value):
        raise AdoConnectionError(
            "Enter your Azure DevOps organization name, for example contoso from "
            "dev.azure.com/contoso. An email address cannot be used here."
        )
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
            # Additive migration preserves existing PAT rows and their ownership constraints.
            columns = {row[1] for row in db.execute("PRAGMA table_info(connections)")}
            for name in ("encrypted_oauth", "oauth_status"):
                if name not in columns:
                    try:
                        db.execute(f"ALTER TABLE connections ADD COLUMN {name} TEXT")
                    except sqlite3.OperationalError:
                        if name not in {
                            row[1] for row in db.execute("PRAGMA table_info(connections)")
                        }:
                            raise
            db.execute("""CREATE TABLE IF NOT EXISTS oauth_pending (
                state TEXT PRIMARY KEY, login TEXT NOT NULL, session_hash TEXT NOT NULL,
                browser_hash TEXT NOT NULL, organization TEXT NOT NULL, directory_path TEXT NOT NULL,
                encrypted_verifier TEXT NOT NULL, expires_at REAL NOT NULL,
                consumed INTEGER NOT NULL DEFAULT 0)""")
            db.execute("""CREATE TABLE IF NOT EXISTS publications (
                id TEXT PRIMARY KEY, managed_repo_id TEXT NOT NULL, commit_sha TEXT NOT NULL,
                directory_path TEXT NOT NULL, organization TEXT NOT NULL, identity_id TEXT,
                fallback_reason TEXT, source_ref TEXT NOT NULL, created_at TEXT NOT NULL)""")
            db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS publications_source
                ON publications(managed_repo_id, commit_sha, directory_path, source_ref)""")
            db.execute("""CREATE TABLE IF NOT EXISTS publication_work_items (
                context_id TEXT PRIMARY KEY, work_item_ids_json TEXT NOT NULL)""")
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
        return AdoUserConnectionService.validate_identity(organization, auth=("", pat))

    @staticmethod
    def validate_identity(organization: str, **authorization) -> dict:
        organization = normalize_organization(organization)
        try:
            response = httpx.get(
                f"https://dev.azure.com/{organization}/_apis/connectionData",
                params={"api-version": "7.1-preview.1"},
                **authorization,
                timeout=15,
                follow_redirects=False,
            )
            response.raise_for_status()
            identity = response.json().get("authenticatedUser") or {}
            identity_id = str(identity.get("id") or "").strip().lower()
            if not identity_id or identity_id == "00000000-0000-0000-0000-000000000000":
                raise AdoConnectionError("ADO did not identify the connection owner")
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
            raise AdoConnectionError(
                "Could not validate this connection with Azure DevOps"
            ) from None

    def save(self, *, login: str, organization: str, directory_path: str, pat: str) -> dict:
        organization = normalize_organization(organization)
        directory_path = normalize_scope(directory_path)
        identity = self.validate_pat(organization, pat)
        ciphertext = self._cipher().encrypt(pat.encode()).decode()
        try:
            with self._database() as db:
                db.execute("BEGIN IMMEDIATE")
                self._check_identity(
                    db,
                    login,
                    organization,
                    directory_path,
                    identity["identity_id"],
                    "encrypted_oauth",
                )
                db.execute(
                    """INSERT INTO connections
                    (login, organization, directory_path, identity_id, display_name, email, encrypted_pat, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
                    "SELECT organization, directory_path, identity_id, display_name, email, updated_at, "
                    "encrypted_pat != '' AS has_pat, encrypted_oauth IS NOT NULL AS has_oauth, oauth_status "
                    "FROM connections WHERE login=? ORDER BY organization, directory_path",
                    (login.lower(),),
                )
            ]

    def remove(
        self, *, login: str, organization: str, directory_path: str, method: str | None = None
    ) -> None:
        if method not in {None, "pat", "oauth"}:
            raise AdoConnectionError("Unknown connection method")
        key = (login.lower(), normalize_organization(organization), normalize_scope(directory_path))
        with self._database() as db:
            if method in {None, "oauth"}:
                db.execute(
                    "DELETE FROM oauth_pending WHERE login=? AND organization=? AND directory_path=?",
                    key,
                )
            if method is not None:
                update = (
                    "encrypted_pat=''"
                    if method == "pat"
                    else "encrypted_oauth=NULL, oauth_status=NULL"
                )
                db.execute(
                    f"UPDATE connections SET {update} WHERE login=? AND organization=? AND directory_path=?",
                    key,
                )
            db.execute(
                "DELETE FROM connections WHERE login=? AND organization=? AND directory_path=?"
                + (" AND encrypted_pat='' AND encrypted_oauth IS NULL" if method else ""),
                key,
            )

    def remove_user(self, login: str) -> None:
        with self._database() as db:
            db.execute("DELETE FROM connections WHERE login=?", (login.lower(),))
            db.execute("DELETE FROM oauth_pending WHERE login=?", (login.lower(),))

    @staticmethod
    def _check_identity(db, login, organization, directory_path, identity_id, other_column):
        row = db.execute(
            "SELECT * FROM connections WHERE login=? AND organization=? AND directory_path=?",
            (login.lower(), organization, directory_path),
        ).fetchone()
        if row and row[other_column] and row["identity_id"] != identity_id:
            raise AdoConnectionError(
                "PAT and Microsoft connections must identify the same ADO account; remove the existing connection to switch accounts"
            )

    @staticmethod
    def oauth_available() -> bool:
        try:
            AdoOAuthConfig.from_env()
            return True
        except AdoOAuthError:
            return False

    def begin_oauth(self, *, login, organization, directory_path, session_hash):
        config = AdoOAuthConfig.from_env()
        organization, directory_path = (
            normalize_organization(organization),
            normalize_scope(directory_path),
        )
        state, browser, verifier = (secrets.token_urlsafe(48) for _ in range(3))
        encrypted = (
            self._cipher()
            .encrypt(json.dumps({"verifier": verifier, "binding": config.binding}).encode())
            .decode()
        )
        with self._database() as db:
            db.execute(
                "DELETE FROM oauth_pending WHERE expires_at < ? OR login=?",
                (time.time(), login.lower()),
            )
            db.execute(
                "INSERT INTO oauth_pending VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
                (
                    state,
                    login.lower(),
                    session_hash,
                    hashlib.sha256(browser.encode()).hexdigest(),
                    organization,
                    directory_path,
                    encrypted,
                    time.time() + 600,
                ),
            )
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        url = (
            config.authority
            + "/authorize?"
            + urlencode(
                {
                    "client_id": config.client_id,
                    "redirect_uri": config.redirect_uri,
                    "scope": SCOPES,
                    "response_type": "code",
                    "response_mode": "query",
                    "state": state,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "prompt": "select_account",
                }
            )
        )
        return url, browser

    def consume_oauth(self, *, login, session_hash, browser, state):
        with self._database() as db:
            db.execute("BEGIN IMMEDIATE")
            pending = db.execute("SELECT * FROM oauth_pending WHERE state=?", (state,)).fetchone()
            if (
                pending is None
                or pending["consumed"]
                or pending["expires_at"] < time.time()
                or pending["login"] != login.lower()
                or not hmac.compare_digest(pending["session_hash"], session_hash)
                or not browser
                or not hmac.compare_digest(
                    pending["browser_hash"], hashlib.sha256(browser.encode()).hexdigest()
                )
            ):
                raise AdoConnectionError(
                    "Microsoft connection expired or belongs to another sign-in; start again"
                )
            db.execute("UPDATE oauth_pending SET consumed=1 WHERE state=?", (state,))
        return dict(pending)

    def complete_oauth(self, pending, code):
        config = AdoOAuthConfig.from_env()
        try:
            saved = json.loads(self._cipher().decrypt(pending["encrypted_verifier"].encode()))
        except (InvalidToken, ValueError, OSError):
            raise AdoOAuthError() from None
        if saved["binding"] != config.binding:
            raise AdoOAuthError(reconnect=True)
        token = exchange_token(config, code=code, verifier=saved["verifier"])
        identity = self.validate_identity(
            pending["organization"], headers={"Authorization": "Bearer " + token["access_token"]}
        )
        encrypted = self._cipher().encrypt(json.dumps(token).encode()).decode()
        try:
            with self._database() as db:
                db.execute("BEGIN IMMEDIATE")
                active = db.execute(
                    "SELECT 1 FROM oauth_pending WHERE state=? AND consumed=1 AND expires_at>?",
                    (pending["state"], time.time()),
                ).fetchone()
                if active is None:
                    raise AdoConnectionError("Microsoft connection was cancelled; start again")
                self._check_identity(
                    db,
                    pending["login"],
                    pending["organization"],
                    pending["directory_path"],
                    identity["identity_id"],
                    "encrypted_pat",
                )
                db.execute(
                    """INSERT INTO connections
                    (login, organization, directory_path, identity_id, display_name, email, encrypted_pat,
                     updated_at, encrypted_oauth, oauth_status) VALUES (?, ?, ?, ?, ?, ?, '', ?, ?, 'connected')
                    ON CONFLICT(login, organization, directory_path) DO UPDATE SET identity_id=excluded.identity_id,
                    display_name=excluded.display_name, email=excluded.email, updated_at=excluded.updated_at,
                    encrypted_oauth=excluded.encrypted_oauth, oauth_status='connected'""",
                    (
                        pending["login"],
                        pending["organization"],
                        pending["directory_path"],
                        identity["identity_id"],
                        identity["display_name"],
                        identity["email"],
                        datetime.now(UTC).isoformat(),
                        encrypted,
                    ),
                )
                db.execute("DELETE FROM oauth_pending WHERE state=?", (pending["state"],))
        except sqlite3.IntegrityError:
            raise AdoConnectionError(
                "This ADO identity is already connected by another user in this directory"
            ) from None

    def _oauth_authorization(self, candidate):
        key = tuple(candidate[name] for name in ("login", "organization", "directory_path"))
        lock_name = hashlib.sha256(json.dumps(key).encode()).hexdigest() + ".lock"
        fd = os.open(self.root / lock_name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with self._database() as db:
                row = db.execute(
                    "SELECT * FROM connections WHERE login=? AND organization=? AND directory_path=?",
                    key,
                ).fetchone()
            if (
                not row
                or not row["encrypted_oauth"]
                or row["identity_id"] != candidate["identity_id"]
            ):
                raise AdoOAuthError()
            if row["oauth_status"] == "reconnect_required":
                raise AdoOAuthError(reconnect=True)
            config = AdoOAuthConfig.from_env()
            token = json.loads(self._cipher().decrypt(row["encrypted_oauth"].encode()))
            if token["client_binding"] != config.binding or not has_required_scopes(
                token.get("scope")
            ):
                self._mark_oauth_reconnect(row)
                raise AdoOAuthError(reconnect=True)
            if token["expires_at"] <= time.time() + 120:
                try:
                    token = exchange_token(config, previous=token)
                    identity = self.validate_identity(
                        key[1], headers={"Authorization": "Bearer " + token["access_token"]}
                    )
                    if identity["identity_id"] != row["identity_id"]:
                        raise AdoOAuthError(reconnect=True)
                except AdoOAuthError as exc:
                    if exc.reconnect:
                        self._mark_oauth_reconnect(row)
                    raise
                encrypted = self._cipher().encrypt(json.dumps(token).encode()).decode()
                with self._database() as db:
                    updated = db.execute(
                        "UPDATE connections SET encrypted_oauth=?, oauth_status='connected' "
                        "WHERE login=? AND organization=? AND directory_path=? AND encrypted_oauth=?",
                        (encrypted, *key, row["encrypted_oauth"]),
                    ).rowcount
                if not updated:
                    raise AdoOAuthError()
            return "Bearer " + token["access_token"]

    def _mark_oauth_reconnect(self, row):
        with self._database() as db:
            db.execute(
                "UPDATE connections SET oauth_status='reconnect_required' WHERE login=? "
                "AND organization=? AND directory_path=? AND encrypted_oauth=?",
                (row["login"], row["organization"], row["directory_path"], row["encrypted_oauth"]),
            )

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
        work_item_ids: list[str] | None = None,
    ) -> str:
        """Called only by trusted release services, never exposed as a guest operation."""
        context_id = str(uuid4())
        organization = normalize_organization(organization)
        directory_path = normalize_scope(directory_path)
        identity_id = identity_id.lower() if identity_id else None
        item_ids = sorted(set(work_item_ids or []))
        if any(not re.fullmatch(r"[1-9][0-9]*", item_id) for item_id in item_ids):
            raise AdoConnectionError("Publication work item IDs must be positive numeric IDs")
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
                linked_items = db.execute(
                    "SELECT work_item_ids_json FROM publication_work_items WHERE context_id=?",
                    (existing["id"],),
                ).fetchone()
                if linked_items is not None and json.loads(linked_items[0]) != item_ids:
                    raise AdoConnectionError("Published release work items are immutable")
                # Older contexts have no captured IDs. Do not infer or backfill their links.
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
            db.execute(
                "INSERT INTO publication_work_items VALUES (?, ?)",
                (context_id, json.dumps(item_ids)),
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
        excluded_methods: frozenset[str] = frozenset(),
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
            linked_items = db.execute(
                "SELECT work_item_ids_json FROM publication_work_items WHERE context_id=?",
                (context_id,),
            ).fetchone()
            context["work_item_ids"] = json.loads(linked_items[0]) if linked_items else []
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
        if candidate["encrypted_oauth"] and "personal_oauth" not in excluded_methods:
            try:
                auth = self._oauth_authorization(candidate)
                context["credential_source"] = "personal_oauth"
                return context, auth
            except (AdoOAuthError, AdoConnectionError, InvalidToken, ValueError, OSError) as exc:
                context["fallback_reason"] = (
                    "personal_oauth_reconnect_required"
                    if isinstance(exc, AdoOAuthError) and exc.reconnect
                    else "personal_oauth_unavailable"
                )
                # Refresh can overlap a disconnect, PAT replacement, or account switch.
                # Re-read before selecting the fallback instead of using the old ciphertext.
                with self._database() as db:
                    candidate = db.execute(
                        "SELECT * FROM connections WHERE login=? AND organization=? "
                        "AND directory_path=? AND identity_id=?",
                        (
                            candidate["login"],
                            candidate["organization"],
                            candidate["directory_path"],
                            candidate["identity_id"],
                        ),
                    ).fetchone()
                if candidate is None:
                    return context, None
        if not candidate["encrypted_pat"] or "personal_pat" in excluded_methods:
            context["fallback_reason"] = (
                context["fallback_reason"] or "personal_connection_unavailable"
            )
            return context, None
        try:
            pat = self._cipher().decrypt(candidate["encrypted_pat"].encode()).decode()
        except (InvalidToken, ValueError, OSError):
            context["fallback_reason"] = "personal_pat_unavailable"
            return context, None
        context["credential_source"] = "personal_pat"
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
