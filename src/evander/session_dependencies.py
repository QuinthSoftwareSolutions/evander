"""Host-owned dependent resources that must be released before deleting session data.

The registry is provider-neutral and remains usable when an optional add-on is disabled.
Only trusted host integrations register callbacks; agents never provide URLs or credentials.
"""

from contextlib import closing
import os
from pathlib import Path
import sqlite3

import httpx


class SessionDependencies:
    def __init__(self, runtime_dir):
        self.path = Path(runtime_dir) / "session-dependencies.db"

    def register(self, session_id, dependency_id, release_url, token_env):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS dependencies(session_id TEXT, id TEXT PRIMARY KEY, release_url TEXT, token_env TEXT)"
            )
            db.execute(
                "INSERT OR REPLACE INTO dependencies VALUES(?,?,?,?)",
                (session_id, dependency_id, release_url, token_env),
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
            rows = db.execute(
                "SELECT id,release_url,token_env FROM dependencies WHERE session_id=?",
                (session_id,),
            ).fetchall()
        for dependency_id, url, token_env in rows:
            token = os.environ.get(token_env)
            if not token:
                raise ValueError("Dependent resource cleanup credentials are unavailable")
            try:
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
                    "Dependent resource cleanup failed; session data was preserved"
                ) from None
