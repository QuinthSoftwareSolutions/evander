"""Host-owned dependent resources that must be released before deleting session data.

The registry is provider-neutral and remains usable when an optional add-on is disabled.
Only trusted host integrations register callbacks; agents never provide URLs or credentials.
"""

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit

import httpx

from evander.credentials import CredentialStore
from evander.credentials.bearer import resolve_bearer


def requires_browser_credential_migration(owner, dependency_id, release_url):
    """Distinguish directory-selected callbacks from trusted worker registrations."""
    if not dependency_id.startswith("browser-"):
        return False
    session_id, _, kind = dependency_id.partition(":")
    path = urlsplit(release_url).path
    if kind == "worker" and path == f"/v1/sessions/{session_id}/close":
        return False
    if (
        kind.startswith("target:")
        and kind.removeprefix("target:")
        and owner.startswith("resource:")
        and owner.removeprefix("resource:")
        and path == f"/v1/sessions/{session_id}/detach-{owner.removeprefix('resource:')}"
    ):
        return False
    return True


class SessionDependencies:
    def __init__(self, runtime_dir, *, credential_store=None):
        self.path = Path(runtime_dir) / "session-dependencies.db"
        self.credentials = credential_store or CredentialStore(runtime_dir)

    def register(self, session_id, dependency_id, release_url, token_env):
        self._register(session_id, dependency_id, release_url, token_env, None)

    def register_managed(self, session_id, dependency_id, release_url, auth):
        self._register(session_id, dependency_id, release_url, None, json.dumps(auth))

    def _register(self, session_id, dependency_id, release_url, token_env, managed_auth):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "CREATE TABLE IF NOT EXISTS dependencies(session_id TEXT, id TEXT PRIMARY KEY, release_url TEXT, token_env TEXT)"
            )
            if "managed_auth" not in {
                row[1] for row in db.execute("PRAGMA table_info(dependencies)")
            }:
                db.execute("ALTER TABLE dependencies ADD COLUMN managed_auth TEXT")
            db.execute(
                "INSERT OR REPLACE INTO dependencies(session_id,id,release_url,token_env,managed_auth) VALUES(?,?,?,?,?)",
                (session_id, dependency_id, release_url, token_env, managed_auth),
            )
        self.path.chmod(0o600)

    def remove(self, dependency_id):
        if not self.path.exists():
            return
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute("DELETE FROM dependencies WHERE id=?", (dependency_id,))

    def release(self, session_id, *, transport=None):
        if not self.path.exists():
            return
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            managed = (
                "managed_auth"
                if "managed_auth"
                in {row[1] for row in db.execute("PRAGMA table_info(dependencies)")}
                else "NULL"
            )
            rows = db.execute(
                f"SELECT id,release_url,token_env,{managed} FROM dependencies WHERE session_id=?",
                (session_id,),
            ).fetchall()
        for dependency_id, url, token_env, managed_auth in rows:
            try:
                if managed_auth:
                    auth = json.loads(managed_auth)
                    token = resolve_bearer(
                        self.credentials,
                        auth["credential_ref"],
                        directory=auth["directory"],
                        binding=auth["binding"],
                        url=url,
                    )
                else:
                    # Browser control-plane callbacks used directory-editable
                    # selectors. Worker-local callbacks remain trusted config.
                    if requires_browser_credential_migration(session_id, dependency_id, url):
                        raise ValueError("Legacy browser cleanup requires credential migration")
                    token = os.environ.get(token_env or "")
                    if not token:
                        raise ValueError("Dependent resource cleanup credentials are unavailable")
                with httpx.Client(
                    timeout=30, trust_env=False, follow_redirects=False, transport=transport
                ) as client:
                    response = client.post(url, headers={"Authorization": "Bearer " + token})
                    response.raise_for_status()
                    if response.json().get("released") is not True:
                        raise ValueError("Resource release was not confirmed")
                self.remove(dependency_id)
            except Exception:
                raise ValueError(
                    "Dependent resource cleanup failed; check credentials or migration; session data was preserved"
                ) from None
