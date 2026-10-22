"""Trusted workload bindings, independent of an agent job's process lifetime."""

from contextlib import closing
from pathlib import Path
import sqlite3
import time
from pydantic import Field
from .models import Contract, Owner, ResourceError


class Workload(Contract):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    owner: Owner
    worker_id: str
    container_id: str
    expires_at: float
    active: bool = True


class Workloads:
    def __init__(self, path, *, now=time.time):
        self.path, self.now = Path(path), now

    def db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("CREATE TABLE IF NOT EXISTS workloads(id TEXT PRIMARY KEY, payload TEXT)")
        self.path.chmod(0o600)
        return db

    def put(self, value):
        value = Workload.model_validate(value)
        if value.owner.reference.get("workload_id") != value.id:
            raise ResourceError("Workload reference differs from its trusted owner binding")
        with closing(self.db()) as db, db:
            row = db.execute("SELECT payload FROM workloads WHERE id=?", (value.id,)).fetchone()
            if (
                row
                and Workload.model_validate_json(row[0]).owner.identity() != value.owner.identity()
            ):
                raise ResourceError("Workload owner changed")
            db.execute(
                "INSERT OR REPLACE INTO workloads VALUES(?,?)", (value.id, value.model_dump_json())
            )

    def current(self, owner):
        with closing(self.db()) as db:
            row = db.execute(
                "SELECT payload FROM workloads WHERE id=?", (owner.reference.get("workload_id"),)
            ).fetchone()
        if not row:
            return None
        value = Workload.model_validate_json(row[0])
        if value.owner.identity() != owner.identity():
            raise ResourceError("Workload is outside the resource owner")
        return value if value.active and self.now() < value.expires_at else None

    def revoke(self, owner):
        value = self.current(owner)
        if value:
            value.active = False
            self.put(value)
