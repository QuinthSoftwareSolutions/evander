"""Host-owned, leased HTTP destinations shared by optional resource consumers.

References carry no authority on their own: resolve always checks the owner,
directory, placement, readiness and deadline. No optional package is imported.
"""

from contextlib import closing
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import time
from urllib.parse import urlsplit

from pydantic import Field

from .models import Contract, ResourceError


class Endpoint(Contract):
    ref: str = Field(pattern=r"^ep-[a-f0-9]{32}$")
    resource_id: str
    session_id: str
    directory: str
    worker_id: str
    origin: str
    expires_at: float
    ready: bool = False
    # Worker-only routing. Never included in a public receipt.
    network: str = ""
    address: str = ""
    port: int = Field(ge=1, le=65535)
    startup_state_path: str | None = None


class Endpoints:
    def __init__(self, runtime_dir, *, now=time.time):
        self.path = Path(runtime_dir) / "resource-endpoints.db"
        self.now = now

    def db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.execute(
            "CREATE TABLE IF NOT EXISTS endpoints(ref TEXT PRIMARY KEY, resource TEXT, payload TEXT)"
        )
        self.path.chmod(0o600)
        return db

    def put(self, endpoint):
        endpoint = Endpoint.model_validate(endpoint)
        parsed = urlsplit(endpoint.origin)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ResourceError("Expected an HTTP origin")
        with closing(self.db()) as db, db:
            old = db.execute(
                "SELECT payload FROM endpoints WHERE ref=?", (endpoint.ref,)
            ).fetchone()
            if old:
                prior = Endpoint.model_validate_json(old[0])
                if (
                    prior.resource_id,
                    prior.session_id,
                    prior.directory,
                    prior.worker_id,
                    prior.origin,
                ) != (
                    endpoint.resource_id,
                    endpoint.session_id,
                    endpoint.directory,
                    endpoint.worker_id,
                    endpoint.origin,
                ):
                    raise ResourceError("Endpoint identity changed")
            db.execute(
                "INSERT OR REPLACE INTO endpoints VALUES(?,?,?)",
                (endpoint.ref, endpoint.resource_id, endpoint.model_dump_json()),
            )

    def resolve(self, ref, *, session_id, directory, worker_id=None):
        with closing(self.db()) as db:
            row = db.execute("SELECT payload FROM endpoints WHERE ref=?", (ref,)).fetchone()
        if not row:
            raise ResourceError("Unknown resource endpoint")
        value = Endpoint.model_validate_json(row[0])
        if (
            (value.session_id, value.directory) != (session_id, directory)
            or worker_id is not None
            and value.worker_id != worker_id
        ):
            raise ResourceError("Endpoint is outside the current owner or worker")
        if not value.ready or self.now() >= value.expires_at:
            raise ResourceError("Resource endpoint is unavailable")
        return value

    def revoke(self, resource_id):
        with closing(self.db()) as db, db:
            for row in db.execute(
                "SELECT ref,payload FROM endpoints WHERE resource=?", (resource_id,)
            ).fetchall():
                value = Endpoint.model_validate_json(row[1])
                value.ready = False
                db.execute(
                    "UPDATE endpoints SET payload=? WHERE ref=?", (value.model_dump_json(), row[0])
                )


class ProxyGrants:
    """Narrow delegated proxy proof; never disclose the parent's MCP credential."""

    def __init__(self, runtime_dir, *, now=time.time):
        self.path = Path(runtime_dir) / "http-target-proxy-grants.db"
        self.now = now

    def db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.execute(
            "CREATE TABLE IF NOT EXISTS grants(id TEXT PRIMARY KEY, hash TEXT, session TEXT, aliases TEXT, expires REAL)"
        )
        self.path.chmod(0o600)
        return db

    def issue(self, resource_id, session_id, aliases, expires_at):
        token = secrets.token_urlsafe(32)
        with closing(self.db()) as db, db:
            db.execute(
                "INSERT OR REPLACE INTO grants VALUES(?,?,?,?,?)",
                (
                    resource_id,
                    hashlib.sha256(token.encode()).hexdigest(),
                    session_id,
                    json.dumps(aliases),
                    expires_at,
                ),
            )
        return token

    def authenticate(self, resource_id, token):
        with closing(self.db()) as db:
            row = db.execute(
                "SELECT hash,session,aliases,expires FROM grants WHERE id=?", (resource_id,)
            ).fetchone()
        if (
            not row
            or self.now() >= row[3]
            or not secrets.compare_digest(row[0], hashlib.sha256(token.encode()).hexdigest())
        ):
            raise ResourceError("Proxy authentication required")
        return row[1], json.loads(row[2])

    def revoke(self, resource_id):
        with closing(self.db()) as db, db:
            db.execute("DELETE FROM grants WHERE id=?", (resource_id,))
