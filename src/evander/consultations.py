from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
import hmac
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterator, Mapping
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


CONSULTATION_REQUESTED_EVENT_TYPE = "agent.consultation.requested"
CONSULTATION_TERMINATE_EVENT_TYPE = "agent.consultation.terminate"
CONSULTATION_RESULT_TOOL = "submit_codebase_consultation_result"
CONSULTATION_RESULT_TOKEN_PATH = "/home/agent/.agent/code-consultation-result-token"

ACTIVE_CONSULTATION_STATUSES = ("provisioning", "running")
TERMINAL_CONSULTATION_STATUSES = ("completed", "failed", "timed_out", "cancelled")
_CONSULTATION_ID_PATTERN = re.compile(r"^consult-[A-Za-z0-9][A-Za-z0-9_-]{0,119}$")


def utc_now() -> datetime:
    return datetime.now(UTC)


def consultation_store_path(runtime_dir: Path) -> Path:
    return Path(runtime_dir) / "agent-consultations.db"


class CodeConsultationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=1000)
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    explanation: str = Field(min_length=1, max_length=4000)

    @field_validator("path", "explanation", mode="before")
    @classmethod
    def normalize_text(cls, value: object) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("value must be a non-empty string")
        return text

    @field_validator("line_end")
    @classmethod
    def validate_line_range(cls, value: int, info) -> int:
        line_start = info.data.get("line_start")
        if isinstance(line_start, int) and value < line_start:
            raise ValueError("line_end must be greater than or equal to line_start")
        return value


class CodeConsultationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1, max_length=50_000)
    evidence: list[CodeConsultationEvidence] = Field(default_factory=list, max_length=100)
    confidence: float = Field(ge=0.0, le=1.0)
    limitations: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("answer", mode="before")
    @classmethod
    def normalize_answer(cls, value: object) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("answer must be a non-empty string")
        return text

    @field_validator("limitations", mode="before")
    @classmethod
    def normalize_limitations(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            raise ValueError("limitations must be a list")
        normalized: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text and text not in normalized:
                normalized.append(text[:4000])
        return normalized


class CodeConsultationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    consultation_id: str
    source_session_id: str
    directory_path: str
    managed_repo_id: str
    source_ref: str | None = None
    commit_sha: str
    profile_name: str
    profile: dict[str, Any]
    query: str
    request_hash: str
    idempotency_key: str | None = None
    status: str
    session_id: str
    workspace_path: str | None = None
    result: CodeConsultationResult | None = None
    error: str | None = None
    cleanup_status: str = "pending"
    cleanup_error: str | None = None
    max_concurrency: int = 1
    token_hash: str
    token_used_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def public_payload(self) -> dict[str, Any]:
        payload = self.model_dump(
            mode="json",
            exclude={"token_hash", "profile", "request_hash"},
        )
        payload["profile"] = self.profile_name
        payload["provenance"] = {
            "managed_repo_id": self.managed_repo_id,
            "source_ref": self.source_ref,
            "commit_sha": self.commit_sha,
        }
        return payload


class CodeConsultationStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS code_consultations (
                    consultation_id TEXT PRIMARY KEY,
                    source_session_id TEXT NOT NULL,
                    directory_path TEXT NOT NULL,
                    managed_repo_id TEXT NOT NULL,
                    source_ref TEXT,
                    commit_sha TEXT NOT NULL,
                    profile_name TEXT NOT NULL,
                    profile_json TEXT NOT NULL,
                    query_text TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    idempotency_key TEXT,
                    status TEXT NOT NULL,
                    session_id TEXT NOT NULL UNIQUE,
                    workspace_path TEXT,
                    result_json TEXT,
                    error TEXT,
                    cleanup_status TEXT NOT NULL,
                    cleanup_error TEXT,
                    max_concurrency INTEGER NOT NULL,
                    token_hash TEXT NOT NULL,
                    token_used_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_code_consultations_idempotency
                ON code_consultations(source_session_id, idempotency_key)
                WHERE idempotency_key IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_code_consultations_capacity
                ON code_consultations(directory_path, profile_name, status, created_at);
                CREATE INDEX IF NOT EXISTS idx_code_consultations_session
                ON code_consultations(session_id);
                """
            )
            connection.commit()

    def create(
        self,
        *,
        consultation_id: str | None = None,
        session_id: str | None = None,
        source_session_id: str,
        directory_path: str,
        managed_repo_id: str,
        source_ref: str | None,
        commit_sha: str,
        profile_name: str,
        profile: Mapping[str, Any],
        query: str,
        idempotency_key: str | None,
        max_concurrency: int,
        expires_at: datetime,
        token_hash: str,
        request_hash: str | None = None,
    ) -> tuple[CodeConsultationRecord, bool]:
        normalized_idempotency_key = str(idempotency_key or "").strip() or None
        if normalized_idempotency_key is not None and len(normalized_idempotency_key) > 256:
            raise ValueError("idempotency_key exceeds the 256 character limit")
        normalized_request_hash = str(request_hash or "").strip() or hash_consultation_request(
            managed_repo_id=managed_repo_id,
            source_ref=source_ref,
            commit_sha=commit_sha,
            profile_name=profile_name,
            query=query,
        )
        now = utc_now()
        consultation_id = str(consultation_id or f"consult-{uuid4().hex}").strip()
        session_id = str(session_id or f"consult-{uuid4().hex[:24]}").strip()
        consultation_snapshot_ref(consultation_id)
        if not session_id.startswith("consult-") or not session_id[8:]:
            raise ValueError("consultation worker session_id must start with consult-")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if normalized_idempotency_key is not None:
                existing = connection.execute(
                    """
                    SELECT * FROM code_consultations
                    WHERE source_session_id = ? AND idempotency_key = ?
                    """,
                    (source_session_id, normalized_idempotency_key),
                ).fetchone()
                if existing is not None:
                    record = self._record(existing)
                    if record.request_hash != normalized_request_hash:
                        raise ValueError(
                            "idempotency_key is already bound to a different consultation request"
                        )
                    connection.rollback()
                    return record, False
            connection.execute(
                """
                INSERT INTO code_consultations (
                    consultation_id, source_session_id, directory_path, managed_repo_id,
                    source_ref, commit_sha, profile_name, profile_json, query_text,
                    request_hash, idempotency_key, status, session_id, result_json,
                    cleanup_status, max_concurrency, token_hash, created_at, updated_at,
                    expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, NULL,
                          'pending', ?, ?, ?, ?, ?)
                """,
                (
                    consultation_id,
                    source_session_id,
                    directory_path,
                    managed_repo_id,
                    source_ref,
                    commit_sha,
                    profile_name,
                    json.dumps(dict(profile), sort_keys=True),
                    query,
                    normalized_request_hash,
                    normalized_idempotency_key,
                    session_id,
                    max(1, int(max_concurrency)),
                    token_hash,
                    _dt(now),
                    _dt(now),
                    _dt(expires_at),
                ),
            )
            connection.commit()
        record = self.get(consultation_id)
        assert record is not None
        return record, True

    def get(self, consultation_id: str) -> CodeConsultationRecord | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM code_consultations WHERE consultation_id = ?",
                (str(consultation_id).strip(),),
            ).fetchone()
        return self._record(row) if row is not None else None

    def get_by_session(self, session_id: str) -> CodeConsultationRecord | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM code_consultations WHERE session_id = ?",
                (str(session_id).strip(),),
            ).fetchone()
        return self._record(row) if row is not None else None

    def get_by_idempotency_key(
        self,
        *,
        source_session_id: str,
        idempotency_key: str | None,
    ) -> CodeConsultationRecord | None:
        normalized_key = str(idempotency_key or "").strip()
        if not normalized_key:
            return None
        if len(normalized_key) > 256:
            raise ValueError("idempotency_key exceeds the 256 character limit")
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM code_consultations
                WHERE source_session_id = ? AND idempotency_key = ?
                """,
                (str(source_session_id).strip(), normalized_key),
            ).fetchone()
        return self._record(row) if row is not None else None

    def claim_available(
        self,
        *,
        directory_path: str,
        profile_name: str,
    ) -> list[CodeConsultationRecord]:
        claimed_ids: list[str] = []
        now = utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            first = connection.execute(
                """
                SELECT max_concurrency FROM code_consultations
                WHERE directory_path = ? AND profile_name = ? AND status = 'queued'
                ORDER BY created_at, consultation_id LIMIT 1
                """,
                (directory_path, profile_name),
            ).fetchone()
            if first is None:
                connection.rollback()
                return []
            active = int(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count FROM code_consultations
                    WHERE directory_path = ? AND profile_name = ?
                      AND (
                        status IN ('provisioning', 'running')
                        OR (
                          status IN ('completed', 'failed', 'timed_out', 'cancelled')
                          AND cleanup_status != 'completed'
                          AND started_at IS NOT NULL
                        )
                      )
                    """,
                    (directory_path, profile_name),
                ).fetchone()["count"]
            )
            capacity = max(0, int(first["max_concurrency"]) - active)
            if capacity:
                rows = connection.execute(
                    """
                    SELECT consultation_id FROM code_consultations
                    WHERE directory_path = ? AND profile_name = ? AND status = 'queued'
                    ORDER BY created_at, consultation_id LIMIT ?
                    """,
                    (directory_path, profile_name, capacity),
                ).fetchall()
                claimed_ids = [str(row["consultation_id"]) for row in rows]
                for claimed_id in claimed_ids:
                    connection.execute(
                        """
                        UPDATE code_consultations
                        SET status = 'provisioning', updated_at = ?, started_at = COALESCE(started_at, ?)
                        WHERE consultation_id = ? AND status = 'queued'
                        """,
                        (_dt(now), _dt(now), claimed_id),
                    )
            connection.commit()
        return [record for claimed_id in claimed_ids if (record := self.get(claimed_id))]

    def mark_running(self, consultation_id: str, *, workspace_path: str) -> CodeConsultationRecord:
        return self._transition(
            consultation_id,
            allowed={"provisioning"},
            status="running",
            fields={"workspace_path": workspace_path},
        )

    def submit_result(
        self,
        consultation_id: str,
        *,
        session_id: str,
        token_hash: str,
        result: CodeConsultationResult,
    ) -> CodeConsultationRecord:
        now = utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM code_consultations WHERE consultation_id = ?",
                (consultation_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Unknown consultation_id: {consultation_id}")
            record = self._record(row)
            if record.session_id != session_id:
                raise ValueError("consultation result session does not match the worker session")
            if record.status != "running":
                raise ValueError(f"consultation {consultation_id} is {record.status}, not running")
            if record.token_used_at is not None or not hmac.compare_digest(
                record.token_hash, token_hash
            ):
                raise ValueError("consultation result token is invalid or already consumed")
            cursor = connection.execute(
                """
                UPDATE code_consultations
                SET status = 'completed', result_json = ?, token_used_at = ?,
                    completed_at = ?, updated_at = ?
                WHERE consultation_id = ? AND status = 'running' AND token_used_at IS NULL
                """,
                (
                    result.model_dump_json(),
                    _dt(now),
                    _dt(now),
                    _dt(now),
                    consultation_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("consultation result was not accepted")
            connection.commit()
        updated = self.get(consultation_id)
        assert updated is not None
        return updated

    def fail(self, consultation_id: str, *, error: str) -> CodeConsultationRecord:
        return self._terminal_transition(
            consultation_id,
            status="failed",
            error=error,
            preserve_completed=True,
        )

    def terminate(
        self,
        consultation_id: str,
        *,
        status: str,
        error: str,
    ) -> CodeConsultationRecord:
        if status not in {"timed_out", "cancelled"}:
            raise ValueError("termination status must be timed_out or cancelled")
        return self._terminal_transition(
            consultation_id,
            status=status,
            error=error,
            preserve_completed=True,
        )

    def mark_cleanup(
        self,
        consultation_id: str,
        *,
        success: bool,
        error: str | None = None,
    ) -> CodeConsultationRecord:
        now = utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT cleanup_status FROM code_consultations WHERE consultation_id = ?",
                (consultation_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise ValueError(f"Unknown consultation_id: {consultation_id}")
            if row["cleanup_status"] != "completed":
                connection.execute(
                    """
                    UPDATE code_consultations
                    SET cleanup_status = ?, cleanup_error = ?, updated_at = ?
                    WHERE consultation_id = ?
                    """,
                    (
                        "completed" if success else "failed",
                        None if success else str(error or "cleanup failed")[:8000],
                        _dt(now),
                        consultation_id,
                    ),
                )
            connection.commit()
        record = self.get(consultation_id)
        assert record is not None
        return record

    def compact(self, *, before: datetime) -> int:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                DELETE FROM code_consultations
                WHERE status IN ('completed', 'failed', 'timed_out', 'cancelled')
                  AND cleanup_status = 'completed'
                  AND updated_at < ?
                """,
                (_dt(before),),
            )
            connection.commit()
            return max(0, int(cursor.rowcount))

    def _terminal_transition(
        self,
        consultation_id: str,
        *,
        status: str,
        error: str,
        preserve_completed: bool,
    ) -> CodeConsultationRecord:
        now = utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM code_consultations WHERE consultation_id = ?",
                (consultation_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Unknown consultation_id: {consultation_id}")
            current = str(row["status"])
            if current == "completed" and preserve_completed:
                connection.rollback()
                record = self.get(consultation_id)
                assert record is not None
                return record
            if current not in TERMINAL_CONSULTATION_STATUSES:
                connection.execute(
                    """
                    UPDATE code_consultations
                    SET status = ?, error = ?, completed_at = ?, updated_at = ?
                    WHERE consultation_id = ?
                    """,
                    (status, str(error)[:8000], _dt(now), _dt(now), consultation_id),
                )
            connection.commit()
        record = self.get(consultation_id)
        assert record is not None
        return record

    def _transition(
        self,
        consultation_id: str,
        *,
        allowed: set[str],
        status: str,
        fields: Mapping[str, Any],
    ) -> CodeConsultationRecord:
        assignments = ["status = ?", "updated_at = ?"]
        values: list[Any] = [status, _dt(utc_now())]
        for name, value in fields.items():
            if name not in {"workspace_path"}:
                raise ValueError(f"unsupported consultation transition field: {name}")
            assignments.append(f"{name} = ?")
            values.append(value)
        allowed_values = sorted(allowed)
        values.extend([consultation_id, *allowed_values])
        placeholders = ", ".join("?" for _ in allowed_values)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                f"""
                UPDATE code_consultations
                SET {", ".join(assignments)}
                WHERE consultation_id = ? AND status IN ({placeholders})
                """,
                values,
            )
            if cursor.rowcount != 1:
                row = connection.execute(
                    "SELECT status FROM code_consultations WHERE consultation_id = ?",
                    (consultation_id,),
                ).fetchone()
                connection.rollback()
                if row is None:
                    raise ValueError(f"Unknown consultation_id: {consultation_id}")
                raise ValueError(
                    f"consultation {consultation_id} is {row['status']}; "
                    f"expected one of {allowed_values}"
                )
            connection.commit()
        updated = self.get(consultation_id)
        assert updated is not None
        return updated

    @staticmethod
    def _record(row: sqlite3.Row) -> CodeConsultationRecord:
        result_payload = json.loads(row["result_json"]) if row["result_json"] else None
        return CodeConsultationRecord.model_validate(
            {
                "consultation_id": row["consultation_id"],
                "source_session_id": row["source_session_id"],
                "directory_path": row["directory_path"],
                "managed_repo_id": row["managed_repo_id"],
                "source_ref": row["source_ref"],
                "commit_sha": row["commit_sha"],
                "profile_name": row["profile_name"],
                "profile": json.loads(row["profile_json"]),
                "query": row["query_text"],
                "request_hash": row["request_hash"],
                "idempotency_key": row["idempotency_key"],
                "status": row["status"],
                "session_id": row["session_id"],
                "workspace_path": row["workspace_path"],
                "result": result_payload,
                "error": row["error"],
                "cleanup_status": row["cleanup_status"],
                "cleanup_error": row["cleanup_error"],
                "max_concurrency": row["max_concurrency"],
                "token_hash": row["token_hash"],
                "token_used_at": row["token_used_at"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "expires_at": row["expires_at"],
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
            }
        )


def build_consultation_prompt(
    record: CodeConsultationRecord,
    *,
    result_token_path: str = CONSULTATION_RESULT_TOKEN_PATH,
) -> str:
    return (
        "You are a disposable, read-only codebase consultation worker.\n"
        "Answer the caller's question by inspecting the prepared repository snapshot. "
        "Do not modify files, create commits, push branches, or perform unrelated work.\n\n"
        f"Consultation ID: {record.consultation_id}\n"
        f"Repository path: {record.workspace_path or '<provided after preparation>'}\n"
        f"Managed repository: {record.managed_repo_id}\n"
        f"Pinned commit: {record.commit_sha}\n"
        f"Source ref: {record.source_ref or '(commit-only snapshot)'}\n\n"
        "Question:\n"
        f"{record.query}\n\n"
        f"Return the result by calling {CONSULTATION_RESULT_TOOL}. Read the one-time result "
        f"token from {result_token_path} without printing it, then pass that value with the "
        "consultation_id. Provide a concise natural-language answer, "
        "repository-relative evidence paths with valid one-based line ranges, a confidence "
        "value from 0 to 1, and any limitations. Do not merely print the answer to stdout."
    )


def issue_consultation_token(
    *,
    secret: str,
    consultation_id: str,
    session_id: str,
    expires_at: datetime,
) -> str:
    claims = {
        "v": 1,
        "consultation_id": consultation_id,
        "session_id": session_id,
        "expires_at": int(expires_at.timestamp()),
    }
    payload = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    signature = _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def verify_consultation_token(
    token: str,
    *,
    secret: str,
    consultation_id: str,
    session_id: str,
) -> dict[str, Any]:
    payload, separator, signature = str(token or "").partition(".")
    if not separator or not payload or not signature:
        raise ValueError("consultation result token is malformed")
    expected_signature = _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected_signature):
        raise ValueError("consultation result token signature is invalid")
    try:
        claims = json.loads(_unb64(payload))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("consultation result token payload is malformed") from exc
    if not isinstance(claims, dict) or claims.get("v") != 1:
        raise ValueError("consultation result token version is unsupported")
    if claims.get("consultation_id") != consultation_id:
        raise ValueError("consultation result token is bound to another consultation")
    if claims.get("session_id") != session_id:
        raise ValueError("consultation result token is bound to another worker session")
    if int(claims.get("expires_at") or 0) <= int(utc_now().timestamp()):
        raise ValueError("consultation result token has expired")
    return claims


def hash_consultation_token(token: str) -> str:
    return hashlib.sha256(str(token or "").encode()).hexdigest()


def hash_consultation_request(
    *,
    managed_repo_id: str,
    source_ref: str | None,
    commit_sha: str | None,
    profile_name: str,
    query: str,
) -> str:
    payload = json.dumps(
        {
            "managed_repo_id": str(managed_repo_id or "").strip(),
            "source_ref": str(source_ref or "").strip(),
            "commit_sha": str(commit_sha or "").strip().lower(),
            "profile_name": str(profile_name or "").strip(),
            "query": str(query or "").strip(),
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def consultation_snapshot_ref(consultation_id: str) -> str:
    normalized = str(consultation_id or "").strip()
    if not _CONSULTATION_ID_PATTERN.fullmatch(normalized):
        raise ValueError(
            "consultation_id must start with consult- and contain only letters, numbers, "
            "underscores, or hyphens"
        )
    return f"refs/evander/consultations/{normalized}"


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _unb64(value: str) -> str:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}").decode()


def _dt(value: datetime) -> str:
    normalized = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return normalized.astimezone(UTC).isoformat().replace("+00:00", "Z")


__all__ = [
    "ACTIVE_CONSULTATION_STATUSES",
    "CONSULTATION_REQUESTED_EVENT_TYPE",
    "CONSULTATION_RESULT_TOOL",
    "CONSULTATION_RESULT_TOKEN_PATH",
    "CONSULTATION_TERMINATE_EVENT_TYPE",
    "CodeConsultationEvidence",
    "CodeConsultationRecord",
    "CodeConsultationResult",
    "CodeConsultationStore",
    "TERMINAL_CONSULTATION_STATUSES",
    "build_consultation_prompt",
    "consultation_store_path",
    "consultation_snapshot_ref",
    "hash_consultation_request",
    "hash_consultation_token",
    "issue_consultation_token",
    "utc_now",
    "verify_consultation_token",
]
