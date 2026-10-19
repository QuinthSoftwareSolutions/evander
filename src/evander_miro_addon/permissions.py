"""Durable board grants bound to a particular session instance."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from .models import MiroError, identifier


def key_for(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def board_reference(value: str) -> str:
    value = str(value or "").strip()
    if "://" not in value:
        return identifier(value)
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError:
        raise MiroError(
            "invalid_input", "Use an exact https://miro.com/app/board/<id>/ URL"
        ) from None
    parts = url.path.split("/")
    if (
        url.scheme != "https"
        or url.hostname != "miro.com"
        or port not in (None, 443)
        or url.username
        or url.password
        or len(parts) not in (4, 5)
        or parts[1:3] != ["app", "board"]
        or (len(parts) == 5 and parts[4])
    ):
        raise MiroError("invalid_input", "Use an exact https://miro.com/app/board/<id>/ URL")
    return identifier(unquote(parts[3]))


def session_instance(session) -> str:
    return session.created_at.astimezone(UTC).isoformat()


class BoardGrantStore:
    """Mixin for MiroStore; mutations serialize with configuration and other grants."""

    def migrate_board_grants(self, db) -> None:
        marker = "session-board-grants-v1"
        if db.execute("SELECT 1 FROM records WHERE kind='schema' AND key=?", (marker,)).fetchone():
            return
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM records WHERE kind='schema' AND key=?", (marker,)).fetchone():
            return
        for row in db.execute("SELECT key,value FROM records WHERE kind='connection'").fetchall():
            record = json.loads(row[1])
            legacy = record["config"].get("boards", [])
            if legacy or "session_ids" in record["config"]:
                db.execute(
                    "INSERT OR IGNORE INTO records VALUES ('invalidated-board-approvals',?,?)",
                    (row[0], json.dumps({"boards": legacy, "invalidated_at": time.time()})),
                )
                record["config"]["boards"] = []
                record["config"].pop("session_ids", None)
                record["revision"] += 1
                db.execute(
                    "UPDATE records SET value=? WHERE kind='connection' AND key=?",
                    (json.dumps(record), row[0]),
                )
        db.execute(
            "INSERT INTO records VALUES ('schema',?,?)", (marker, json.dumps({"at": time.time()}))
        )

    @staticmethod
    def grant_key(record, session, board_id):
        return key_for([record["key"], session.session_id, session_instance(session), board_id])

    def session_grants(self, record, session):
        return sorted(
            (
                g
                for g in self.list("board-grant")
                if g["connection_key"] == record["key"]
                and g["session_id"] == session.session_id
                and g["session_created_at"] == session_instance(session)
                and g["active"]
            ),
            key=lambda g: g["board_id"],
        )

    def board_grant(self, record, session, board_id):
        grant = self.get("board-grant", self.grant_key(record, session, board_id))
        return grant if grant and grant["active"] else None

    def save_board_grant(
        self,
        record,
        session,
        board_id,
        access,
        *,
        actor,
        source=None,
        expected_revision=None,
        operation_key=None,
        merge=False,
        name="",
    ):
        board_id = board_reference(board_id)
        if access not in {"read", "edit"}:
            raise MiroError("invalid_input", "Board access must be read or edit")
        key = self.grant_key(record, session, board_id)
        fingerprint = key_for([key, access, actor, source])
        receipt_key = key_for([record["key"], operation_key]) if operation_key else None
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                "SELECT value FROM records WHERE kind='connection' AND key=?", (record["key"],)
            ).fetchone()
            if not current or json.loads(current[0])["revision"] != record["revision"]:
                raise MiroError("conflict", "Connection changed; reload before granting access")
            if receipt_key:
                receipt = db.execute(
                    "SELECT value FROM records WHERE kind='board-grant-operation' AND key=?",
                    (receipt_key,),
                ).fetchone()
                if receipt:
                    receipt = json.loads(receipt[0])
                    if receipt["fingerprint"] != fingerprint:
                        raise MiroError(
                            "conflict",
                            "Grant operation key was already used with different arguments",
                        )
                    return receipt["grant"]
            row = db.execute(
                "SELECT value FROM records WHERE kind='board-grant' AND key=?", (key,)
            ).fetchone()
            previous = json.loads(row[0]) if row else None
            revision = previous["revision"] if previous else 0
            if expected_revision is not None and expected_revision != revision:
                raise MiroError("conflict", "Board grant changed; reload before saving")
            if merge and previous and previous["active"] and previous["access"] == "edit":
                access = "edit"
            grant = {
                "grant_id": key,
                "connection_key": record["key"],
                "connection_id": record["config"]["id"],
                "session_id": session.session_id,
                "session_created_at": session_instance(session),
                "directory_path": session.directory_path,
                "board_id": board_id,
                "access": access,
                "name": name or (previous or {}).get("name", ""),
                "active": True,
                "revision": revision + 1,
                "actor": actor,
                "source": source,
                "updated_at": datetime.now(UTC).isoformat(),
            }
            db.execute(
                "INSERT OR REPLACE INTO records VALUES ('board-grant',?,?)",
                (key, json.dumps(grant)),
            )
            db.execute(
                "INSERT INTO records VALUES ('board-grant-audit',?,?)",
                (uuid4().hex, json.dumps(grant)),
            )
            if receipt_key:
                db.execute(
                    "INSERT INTO records VALUES ('board-grant-operation',?,?)",
                    (receipt_key, json.dumps({"fingerprint": fingerprint, "grant": grant})),
                )
            return grant

    def revoke_board_grant(self, grant_id, *, expected_revision, actor):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT value FROM records WHERE kind='board-grant' AND key=?", (grant_id,)
            ).fetchone()
            if not row:
                raise MiroError("forbidden", "Board grant is unavailable")
            grant = json.loads(row[0])
            if grant["revision"] != expected_revision:
                raise MiroError("conflict", "Board grant changed; reload before revoking")
            grant.update(
                active=False,
                revision=grant["revision"] + 1,
                actor=actor,
                updated_at=datetime.now(UTC).isoformat(),
            )
            db.execute(
                "UPDATE records SET value=? WHERE kind='board-grant' AND key=?",
                (json.dumps(grant), grant_id),
            )
            db.execute(
                "INSERT INTO records VALUES ('board-grant-audit',?,?)",
                (uuid4().hex, json.dumps(grant)),
            )
            return grant
