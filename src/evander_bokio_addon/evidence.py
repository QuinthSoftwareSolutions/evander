from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from typing import Any
from uuid import uuid4

from evander.addons import addon_storage_path

from .durable_operations import SQLiteDurableOperationLedger
from .state import ADDON_ID


_EVIDENCE_ID_RE = re.compile(r"^bokio_evidence_[0-9a-f]{32}$")
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_SUPPORTED_CONTENT_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
}
_CONTENT_TYPES_BY_SUFFIX = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}
_SUFFIX_BY_CONTENT_TYPE = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
}


class BokioEvidenceError(RuntimeError):
    """Raised when evidence storage or association state is invalid."""


@dataclass(frozen=True)
class BokioEvidenceFile:
    record: dict[str, Any]
    blob_path: Path


class BokioEvidenceStore:
    """Durable, add-on-owned receipt and invoice evidence store.

    Evidence files are keyed by content hash, while individual communication and
    connector-artifact sources are retained as immutable provenance.
    The store intentionally tracks a pending Bokio association rather than blindly
    retrying it: Bokio upload responses do not offer an idempotency key.
    """

    def __init__(self, runtime_dir: str | Path) -> None:
        self.root = addon_storage_path(runtime_dir, ADDON_ID, "evidence")
        self.blob_root = self.root / "blobs"
        self.database_path = self.root / "evidence.db"
        self.root.mkdir(parents=True, exist_ok=True)
        self.blob_root.mkdir(parents=True, exist_ok=True)
        self._operation_ledger = SQLiteDurableOperationLedger(
            self.database_path,
            table="journal_entry_operations",
            identity_columns=("operation_key",),
            foreign_keys=True,
        )
        self._initialize()

    def ingest_bytes(
        self,
        *,
        content: bytes,
        filename: str,
        source_type: str,
        source_key: str,
        source_metadata: Mapping[str, Any] | None = None,
        mime_type: str | None = None,
        created_at: datetime | None = None,
    ) -> tuple[dict[str, Any], bool]:
        if not isinstance(content, bytes) or not content:
            raise BokioEvidenceError("Bokio evidence content must be non-empty bytes")
        normalized_source_type = _required_text(source_type, "source_type")
        normalized_source_key = _required_text(source_key, "source_key")
        safe_filename = normalize_evidence_filename(filename)
        content_type = _validated_content_type(content, safe_filename, mime_type)
        digest = sha256(content).hexdigest()
        now = _as_utc(created_at or datetime.now(UTC))
        source_payload = _safe_json_object(source_metadata)

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            source_row = connection.execute(
                "SELECT evidence_id FROM evidence_sources WHERE source_type = ? AND source_key = ?",
                (normalized_source_type, normalized_source_key),
            ).fetchone()
            if source_row is not None:
                return self._get_with_connection(connection, str(source_row["evidence_id"])), False

            evidence_row = connection.execute(
                "SELECT evidence_id FROM evidence WHERE sha256 = ?", (digest,)
            ).fetchone()
            created = evidence_row is None
            if evidence_row is None:
                evidence_id = f"bokio_evidence_{uuid4().hex}"
                blob_path = self._blob_path(digest, content_type)
                if not blob_path.exists():
                    temporary_path = blob_path.with_suffix(".tmp")
                    temporary_path.write_bytes(content)
                    temporary_path.replace(blob_path)
                connection.execute(
                    """
                    INSERT INTO evidence (
                        evidence_id, sha256, filename, mime_type, byte_count, blob_filename,
                        created_at, ingested_at, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        evidence_id,
                        digest,
                        safe_filename,
                        content_type,
                        len(content),
                        blob_path.name,
                        _storage_datetime(now),
                        _storage_datetime(now),
                        "unmatched",
                    ),
                )
            else:
                evidence_id = str(evidence_row["evidence_id"])

            connection.execute(
                """
                INSERT INTO evidence_sources (
                    evidence_id, source_type, source_key, source_metadata_json, received_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    evidence_id,
                    normalized_source_type,
                    normalized_source_key,
                    json.dumps(source_payload, sort_keys=True),
                    _storage_datetime(now),
                ),
            )
            return self._get_with_connection(connection, evidence_id), created

    def get(self, evidence_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            return self._get_with_connection(connection, evidence_id)

    def get_file(self, evidence_id: str) -> BokioEvidenceFile:
        record = self.get(evidence_id)
        blob_path = self.blob_root / str(record["blob_filename"])
        if not blob_path.is_file():
            raise FileNotFoundError(f"Bokio evidence blob is missing: {record['evidence_id']}")
        return BokioEvidenceFile(record=record, blob_path=blob_path)

    def list(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        normalized_status = str(status or "").strip() or None
        bounded_limit = min(500, max(1, int(limit or 100)))
        with self._connect() as connection:
            if normalized_status is None:
                rows = connection.execute(
                    "SELECT evidence_id FROM evidence ORDER BY ingested_at DESC LIMIT ?",
                    (bounded_limit,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT evidence_id FROM evidence WHERE status = ? ORDER BY ingested_at DESC LIMIT ?",
                    (normalized_status, bounded_limit),
                ).fetchall()
            return [self._get_with_connection(connection, str(row["evidence_id"])) for row in rows]

    def begin_association(
        self,
        *,
        evidence_id: str,
        journal_entry_id: str,
        retry_if_failed: bool = False,
    ) -> dict[str, Any]:
        evidence_id = validate_evidence_id(evidence_id)
        journal_entry_id = _required_text(journal_entry_id, "journal_entry_id")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._get_with_connection(connection, evidence_id)
            row = connection.execute(
                """
                SELECT * FROM evidence_associations
                WHERE evidence_id = ? AND journal_entry_id = ?
                """,
                (evidence_id, journal_entry_id),
            ).fetchone()
            if row is not None:
                association = _association_from_row(row)
                if association["status"] == "attached":
                    return association
                if association["status"] == "pending":
                    raise BokioEvidenceError(
                        "Bokio evidence association is already pending; inspect Bokio before retrying"
                    )
                if not retry_if_failed:
                    raise BokioEvidenceError(
                        "Bokio evidence association previously failed; set retry_if_failed after "
                        "checking whether Bokio received the upload"
                    )
                claimed = connection.execute(
                    """
                    UPDATE evidence_associations
                    SET status = ?, error = NULL, updated_at = ?
                    WHERE evidence_id = ? AND journal_entry_id = ? AND status = ?
                    """,
                    (
                        "pending",
                        _storage_datetime(datetime.now(UTC)),
                        evidence_id,
                        journal_entry_id,
                        "failed",
                    ),
                ).rowcount
                if claimed != 1:
                    current = self._association_with_connection(
                        connection, evidence_id, journal_entry_id
                    )
                    if current["status"] == "attached":
                        return current
                    raise BokioEvidenceError(
                        "Bokio evidence association is already pending; inspect Bokio before retrying"
                    )
                self._refresh_evidence_status(connection, evidence_id)
                return self._association_with_connection(connection, evidence_id, journal_entry_id)
            now = _storage_datetime(datetime.now(UTC))
            connection.execute(
                """
                INSERT INTO evidence_associations (
                    evidence_id, journal_entry_id, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (evidence_id, journal_entry_id, "pending", now, now),
            )
            return self._association_with_connection(connection, evidence_id, journal_entry_id)

    def mark_association_attached(
        self,
        *,
        evidence_id: str,
        journal_entry_id: str,
        bokio_result: Any,
    ) -> dict[str, Any]:
        evidence_id = validate_evidence_id(evidence_id)
        journal_entry_id = _required_text(journal_entry_id, "journal_entry_id")
        with self._connect() as connection:
            result_payload = _safe_json_value(bokio_result)
            connection.execute(
                """
                UPDATE evidence_associations
                SET status = ?, bokio_result_json = ?, error = NULL, updated_at = ?
                WHERE evidence_id = ? AND journal_entry_id = ?
                """,
                (
                    "attached",
                    json.dumps(result_payload, sort_keys=True),
                    _storage_datetime(datetime.now(UTC)),
                    evidence_id,
                    journal_entry_id,
                ),
            )
            self._refresh_evidence_status(connection, evidence_id)
            return self._association_with_connection(connection, evidence_id, journal_entry_id)

    def mark_association_failed(
        self,
        *,
        evidence_id: str,
        journal_entry_id: str,
        error: Exception | str,
    ) -> dict[str, Any]:
        evidence_id = validate_evidence_id(evidence_id)
        journal_entry_id = _required_text(journal_entry_id, "journal_entry_id")
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE evidence_associations
                SET status = ?, error = ?, updated_at = ?
                WHERE evidence_id = ? AND journal_entry_id = ?
                """,
                (
                    "failed",
                    str(error)[:2000],
                    _storage_datetime(datetime.now(UTC)),
                    evidence_id,
                    journal_entry_id,
                ),
            )
            self._refresh_evidence_status(connection, evidence_id)
            return self._association_with_connection(connection, evidence_id, journal_entry_id)

    def begin_journal_entry_operation(
        self,
        *,
        operation_key: str,
        payload: Mapping[str, Any],
        evidence_ids: Iterable[str],
    ) -> dict[str, Any]:
        normalized_key = _required_text(operation_key, "operation_key")
        normalized_evidence_ids = [validate_evidence_id(value) for value in evidence_ids]
        if not normalized_evidence_ids:
            raise BokioEvidenceError("evidence_ids must contain at least one evidence ID")
        normalized_payload = _safe_json_object(payload)
        with self._connect() as connection:
            self._operation_ledger.begin_immediate(connection)
            row = self._operation_ledger.fetch_row(connection, (normalized_key,))
            if row is not None:
                operation = _operation_from_row(row)
                if (
                    operation["payload"] != normalized_payload
                    or operation["evidence_ids"] != normalized_evidence_ids
                ):
                    raise BokioEvidenceError(
                        "operation_key is already associated with a different journal-entry request"
                    )
                return operation
            for evidence_id in normalized_evidence_ids:
                self._get_with_connection(connection, evidence_id)
            now = _storage_datetime(datetime.now(UTC))
            self._operation_ledger.insert(
                connection,
                {
                    "operation_key": normalized_key,
                    "payload_json": json.dumps(normalized_payload, sort_keys=True),
                    "evidence_ids_json": json.dumps(normalized_evidence_ids),
                    "status": "pending_create",
                    "created_at": now,
                    "updated_at": now,
                },
            )
            return self._operation_with_connection(connection, normalized_key)

    def claim_journal_entry_creation(self, *, operation_key: str) -> tuple[dict[str, Any], bool]:
        """Atomically claim the one allowed attempt to create a journal entry."""

        normalized_key = _required_text(operation_key, "operation_key")
        with self._connect() as connection:
            self._operation_ledger.begin_immediate(connection)
            claimed = self._operation_ledger.transition(
                connection,
                (normalized_key,),
                expected_status="pending_create",
                new_status="creating",
                values={"updated_at": _storage_datetime(datetime.now(UTC))},
            )
            operation = self._operation_with_connection(connection, normalized_key)
            return operation, claimed == 1

    def mark_journal_entry_created(
        self, *, operation_key: str, journal_entry_id: str, result: Any
    ) -> dict[str, Any]:
        normalized_key = _required_text(operation_key, "operation_key")
        with self._connect() as connection:
            updated = self._operation_ledger.transition(
                connection,
                (normalized_key,),
                expected_status="creating",
                new_status="pending_evidence",
                values={
                    "journal_entry_id": _required_text(journal_entry_id, "journal_entry_id"),
                    "result_json": json.dumps(_safe_json_value(result), sort_keys=True),
                    "updated_at": _storage_datetime(datetime.now(UTC)),
                },
            )
            if updated != 1:
                raise BokioEvidenceError(
                    "Bokio journal-entry creation was not claimed by this operation"
                )
            return self._operation_with_connection(connection, normalized_key)

    def mark_journal_entry_operation_completed(self, *, operation_key: str) -> dict[str, Any]:
        normalized_key = _required_text(operation_key, "operation_key")
        with self._connect() as connection:
            self._operation_ledger.update(
                connection,
                (normalized_key,),
                values={
                    "status": "completed",
                    "updated_at": _storage_datetime(datetime.now(UTC)),
                },
            )
            return self._operation_with_connection(connection, normalized_key)

    def mark_journal_entry_operation_needs_attention(
        self,
        *,
        operation_key: str,
        error: Exception | str,
        expected_status: str,
    ) -> dict[str, Any]:
        normalized_key = _required_text(operation_key, "operation_key")
        normalized_expected_status = _required_text(expected_status, "expected_status")
        with self._connect() as connection:
            self._operation_ledger.transition(
                connection,
                (normalized_key,),
                expected_status=normalized_expected_status,
                new_status="needs_attention",
                values={
                    "error": str(error)[:2000],
                    "updated_at": _storage_datetime(datetime.now(UTC)),
                },
            )
            return self._operation_with_connection(connection, normalized_key)

    def resume_journal_entry_operation(
        self, *, operation_key: str, retry_if_failed: bool
    ) -> dict[str, Any]:
        normalized_key = _required_text(operation_key, "operation_key")
        with self._connect() as connection:
            operation = self._operation_with_connection(connection, normalized_key)
            if operation["status"] != "needs_attention":
                return operation
            if not retry_if_failed:
                raise BokioEvidenceError(
                    "Bokio journal-entry operation needs attention; set retry_if_failed only after "
                    "checking the existing journal entry"
                )
            if not str(operation.get("journal_entry_id") or "").strip():
                raise BokioEvidenceError(
                    "Bokio journal-entry creation outcome is unknown; do not retry creation automatically"
                )
            self._operation_ledger.update(
                connection,
                (normalized_key,),
                values={
                    "status": "pending_evidence",
                    "error": None,
                    "updated_at": _storage_datetime(datetime.now(UTC)),
                },
            )
            return self._operation_with_connection(connection, normalized_key)

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS evidence (
                    evidence_id TEXT PRIMARY KEY,
                    sha256 TEXT NOT NULL UNIQUE,
                    filename TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    byte_count INTEGER NOT NULL,
                    blob_filename TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    ingested_at TEXT NOT NULL,
                    status TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evidence_sources (
                    source_type TEXT NOT NULL,
                    source_key TEXT NOT NULL,
                    evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id),
                    source_metadata_json TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    PRIMARY KEY (source_type, source_key)
                );
                CREATE TABLE IF NOT EXISTS evidence_associations (
                    evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id),
                    journal_entry_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    bokio_result_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (evidence_id, journal_entry_id)
                );
                CREATE TABLE IF NOT EXISTS journal_entry_operations (
                    operation_key TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    journal_entry_id TEXT,
                    result_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return self._operation_ledger.connect()

    def _get_with_connection(
        self, connection: sqlite3.Connection, evidence_id: str
    ) -> dict[str, Any]:
        normalized_id = validate_evidence_id(evidence_id)
        row = connection.execute(
            "SELECT * FROM evidence WHERE evidence_id = ?", (normalized_id,)
        ).fetchone()
        if row is None:
            raise FileNotFoundError(f"Unknown Bokio evidence: {normalized_id}")
        sources = connection.execute(
            """
            SELECT source_type, source_key, source_metadata_json, received_at
            FROM evidence_sources WHERE evidence_id = ? ORDER BY received_at ASC
            """,
            (normalized_id,),
        ).fetchall()
        associations = connection.execute(
            """
            SELECT * FROM evidence_associations WHERE evidence_id = ? ORDER BY created_at ASC
            """,
            (normalized_id,),
        ).fetchall()
        return {
            "evidence_id": str(row["evidence_id"]),
            "sha256": str(row["sha256"]),
            "filename": str(row["filename"]),
            "mime_type": str(row["mime_type"]),
            "byte_count": int(row["byte_count"]),
            "blob_filename": str(row["blob_filename"]),
            "created_at": str(row["created_at"]),
            "ingested_at": str(row["ingested_at"]),
            "status": str(row["status"]),
            "sources": [
                {
                    "source_type": str(source["source_type"]),
                    "source_key": str(source["source_key"]),
                    "metadata": _load_json_object(source["source_metadata_json"]),
                    "received_at": str(source["received_at"]),
                }
                for source in sources
            ],
            "associations": [_association_from_row(association) for association in associations],
        }

    def _association_with_connection(
        self, connection: sqlite3.Connection, evidence_id: str, journal_entry_id: str
    ) -> dict[str, Any]:
        row = connection.execute(
            """
            SELECT * FROM evidence_associations
            WHERE evidence_id = ? AND journal_entry_id = ?
            """,
            (evidence_id, journal_entry_id),
        ).fetchone()
        if row is None:
            raise BokioEvidenceError("Bokio evidence association was not recorded")
        return _association_from_row(row)

    def _operation_with_connection(
        self, connection: sqlite3.Connection, operation_key: str
    ) -> dict[str, Any]:
        row = self._operation_ledger.fetch_row(connection, (operation_key,))
        if row is None:
            raise BokioEvidenceError("Bokio journal-entry operation was not recorded")
        return _operation_from_row(row)

    @staticmethod
    def _refresh_evidence_status(connection: sqlite3.Connection, evidence_id: str) -> None:
        statuses = {
            str(row["status"])
            for row in connection.execute(
                "SELECT status FROM evidence_associations WHERE evidence_id = ?",
                (evidence_id,),
            ).fetchall()
        }
        if "failed" in statuses:
            status = "needs_attention"
        elif "attached" in statuses:
            status = "attached"
        else:
            status = "unmatched"
        connection.execute(
            "UPDATE evidence SET status = ? WHERE evidence_id = ?",
            (status, evidence_id),
        )

    def _blob_path(self, digest: str, content_type: str) -> Path:
        suffix = _SUFFIX_BY_CONTENT_TYPE[content_type]
        return self.blob_root / f"{digest}{suffix}"


def validate_evidence_id(value: object) -> str:
    normalized = str(value or "").strip()
    if not _EVIDENCE_ID_RE.fullmatch(normalized):
        raise BokioEvidenceError("evidence_id must be an opaque Bokio evidence ID")
    return normalized


def normalize_evidence_filename(value: object) -> str:
    candidate = Path(str(value or "evidence").replace("\\", "/")).name.replace("\x00", "")
    normalized = _SAFE_FILENAME_RE.sub("-", candidate).strip(".-")
    if not normalized or normalized in {".", ".."}:
        normalized = "evidence"
    return normalized[:255]


def supported_evidence_content_type(filename: str, mime_type: str | None = None) -> str | None:
    normalized = str(mime_type or "").split(";", 1)[0].strip().lower()
    if normalized in _SUPPORTED_CONTENT_TYPES:
        return normalized
    return _CONTENT_TYPES_BY_SUFFIX.get(Path(filename).suffix.lower())


def _validated_content_type(content: bytes, filename: str, mime_type: str | None) -> str:
    content_type = supported_evidence_content_type(filename, mime_type)
    if content_type not in _SUPPORTED_CONTENT_TYPES:
        raise BokioEvidenceError("Bokio evidence supports only PDF, JPEG, or PNG files")
    if content_type == "application/pdf" and not content.startswith(b"%PDF-"):
        raise BokioEvidenceError("Bokio evidence PDF content does not match the file type")
    if content_type == "image/jpeg" and not content.startswith(b"\xff\xd8\xff"):
        raise BokioEvidenceError("Bokio evidence JPEG content does not match the file type")
    if content_type == "image/png" and not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise BokioEvidenceError("Bokio evidence PNG content does not match the file type")
    return content_type


def _association_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "evidence_id": str(row["evidence_id"]),
        "journal_entry_id": str(row["journal_entry_id"]),
        "status": str(row["status"]),
        "bokio_result": _load_json_value(row["bokio_result_json"]),
        "error": row["error"],
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _operation_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "operation_key": str(row["operation_key"]),
        "payload": _load_json_object(row["payload_json"]),
        "evidence_ids": _load_json_list(row["evidence_ids_json"]),
        "status": str(row["status"]),
        "journal_entry_id": row["journal_entry_id"],
        "result": _load_json_value(row["result_json"]),
        "error": row["error"],
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _required_text(value: object, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise BokioEvidenceError(f"{field_name} is required")
    return normalized


def _safe_json_object(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise BokioEvidenceError("evidence metadata must be a JSON object")
    return json.loads(json.dumps(dict(value), default=str))


def _safe_json_value(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _load_json_object(value: object) -> dict[str, Any]:
    try:
        payload = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_json_list(value: object) -> list[str]:
    try:
        payload = json.loads(str(value or "[]"))
    except json.JSONDecodeError:
        return []
    return [str(item) for item in payload] if isinstance(payload, list) else []


def _load_json_value(value: object) -> Any:
    if value is None:
        return None
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return None


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _storage_datetime(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")
