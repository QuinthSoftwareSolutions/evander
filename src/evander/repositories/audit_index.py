from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from evander.models import DirectoryPath
from evander.repositories.orchestration_db import SQLITE_BUSY_TIMEOUT_MS

AUDIT_INDEX_SCHEMA_VERSION = 1
DEFAULT_TAIL_IMPORT_MAX_LINES = 20_000

_SENSITIVE_PAYLOAD_MARKERS: tuple[str, ...] = (
    "token",
    "secret",
    "password",
    "pat",
    "authorization",
    "cookie",
    "private_key",
    "ssh_key",
)

_INSERT_TOOL_CALL_AUDIT_SQL = """
    INSERT OR IGNORE INTO tool_call_audit_entries (
        event_key,
        created_at,
        directory_path,
        session_id,
        vm_id,
        agent_role,
        actor_user_id,
        tool_name,
        status,
        decision,
        request_id,
        correlation_id,
        approval_request_id,
        mcp_tool_approval_lease_id,
        workflow_run_id,
        trace_id,
        duration_ms,
        error_summary,
        search_text,
        payload_json
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


@dataclass(frozen=True)
class ToolCallAuditIndexRecord:
    event_key: str
    created_at: datetime
    created_at_storage: str
    directory_path: str
    session_id: str | None
    vm_id: str | None
    agent_role: str | None
    actor_user_id: str | None
    tool_name: str | None
    status: str
    decision: str | None
    request_id: str | None
    correlation_id: str | None
    approval_request_id: str | None
    mcp_tool_approval_lease_id: str | None
    workflow_run_id: str | None
    trace_id: str | None
    duration_ms: int | None
    error_summary: str | None
    search_text: str
    payload_json: str


@dataclass(frozen=True)
class ToolCallAuditIndexPage:
    payloads: list[dict[str, Any]]
    matched: int
    returned: int
    status_counts: dict[str, int]
    has_more: bool


@dataclass
class AuditIndexImportResult:
    database_path: Path
    source_path: Path
    lines_scanned: int = 0
    imported: int = 0
    existing: int = 0
    skipped_malformed: int = 0
    skipped_non_tool_call: int = 0
    offset_bytes: int = 0
    complete: bool = False
    reached_time_boundary: bool = False
    stopped_by_limit: bool = False
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "database_path": str(self.database_path),
            "source_path": str(self.source_path),
            "lines_scanned": self.lines_scanned,
            "imported": self.imported,
            "existing": self.existing,
            "skipped_malformed": self.skipped_malformed,
            "skipped_non_tool_call": self.skipped_non_tool_call,
            "offset_bytes": self.offset_bytes,
            "complete": self.complete,
            "reached_time_boundary": self.reached_time_boundary,
            "stopped_by_limit": self.stopped_by_limit,
            "errors": list(self.errors),
        }


def audit_index_path_for_log_path(audit_log_path: Path | str) -> Path:
    return Path(audit_log_path).expanduser().resolve().with_name("audit.db")


def connect_audit_index_db(path: Path | str) -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=30.0)
    connection.row_factory = sqlite3.Row
    connection.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    connection.execute("PRAGMA journal_mode=WAL")
    return connection


def ensure_audit_index_schema(path: Path | str) -> None:
    with connect_audit_index_db(path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS audit_index_metadata (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audit_import_sources (
                source_path TEXT PRIMARY KEY,
                size_bytes INTEGER NOT NULL DEFAULT 0,
                mtime_ns INTEGER NOT NULL DEFAULT 0,
                offset_bytes INTEGER NOT NULL DEFAULT 0,
                complete INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tool_call_audit_entries (
                row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                directory_path TEXT NOT NULL,
                session_id TEXT,
                vm_id TEXT,
                agent_role TEXT,
                actor_user_id TEXT,
                tool_name TEXT,
                status TEXT NOT NULL,
                decision TEXT,
                request_id TEXT,
                correlation_id TEXT,
                approval_request_id TEXT,
                mcp_tool_approval_lease_id TEXT,
                workflow_run_id TEXT,
                trace_id TEXT,
                duration_ms INTEGER,
                error_summary TEXT,
                search_text TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_tool_call_audit_created
                ON tool_call_audit_entries(created_at DESC, row_id DESC);
            CREATE INDEX IF NOT EXISTS idx_tool_call_audit_directory
                ON tool_call_audit_entries(directory_path, created_at DESC, row_id DESC);
            CREATE INDEX IF NOT EXISTS idx_tool_call_audit_session
                ON tool_call_audit_entries(session_id, created_at DESC, row_id DESC);
            CREATE INDEX IF NOT EXISTS idx_tool_call_audit_tool
                ON tool_call_audit_entries(tool_name, created_at DESC, row_id DESC);
            CREATE INDEX IF NOT EXISTS idx_tool_call_audit_status
                ON tool_call_audit_entries(status, created_at DESC, row_id DESC);
            CREATE INDEX IF NOT EXISTS idx_tool_call_audit_request
                ON tool_call_audit_entries(request_id, created_at DESC, row_id DESC);
            CREATE INDEX IF NOT EXISTS idx_tool_call_audit_correlation
                ON tool_call_audit_entries(correlation_id, created_at DESC, row_id DESC);
            CREATE INDEX IF NOT EXISTS idx_tool_call_audit_approval
                ON tool_call_audit_entries(approval_request_id, created_at DESC, row_id DESC);
            CREATE INDEX IF NOT EXISTS idx_tool_call_audit_workflow_run
                ON tool_call_audit_entries(workflow_run_id, created_at DESC, row_id DESC);
            CREATE INDEX IF NOT EXISTS idx_tool_call_audit_trace
                ON tool_call_audit_entries(trace_id, created_at DESC, row_id DESC);
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO audit_index_metadata(key, value_json)
            VALUES (?, ?)
            """,
            (
                "schema_version",
                json.dumps(
                    {
                        "version": AUDIT_INDEX_SCHEMA_VERSION,
                        "applied_at": _datetime_to_storage(datetime.now(UTC)),
                    },
                    sort_keys=True,
                ),
            ),
        )


class ToolCallAuditIndex:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    @classmethod
    def for_audit_log(cls, audit_log_path: Path | str) -> ToolCallAuditIndex:
        return cls(audit_index_path_for_log_path(audit_log_path))

    def ensure_schema(self) -> None:
        ensure_audit_index_schema(self.path)

    def index_payload(self, payload: dict[str, Any]) -> bool:
        self.ensure_schema()
        record = tool_call_audit_index_record_from_payload(payload)
        if record is None:
            return False
        return self._insert_record(record)

    def index_appended_payload(
        self,
        payload: dict[str, Any],
        source_path: Path | str,
        *,
        previous_size_bytes: int | None,
        previous_mtime_ns: int | None,
        appended_offset_bytes: int | None,
    ) -> bool:
        self.ensure_schema()
        event_type = str(payload.get("event_type") or "").strip().casefold()
        record = (
            tool_call_audit_index_record_from_payload(payload)
            if event_type == "tool_call"
            else None
        )
        source = Path(source_path)
        source_key = str(source.expanduser().resolve())
        try:
            source_stat = source.stat()
        except OSError:
            source_stat = None

        with connect_audit_index_db(self.path) as connection:
            source_was_complete = self._source_import_complete_for_stat(
                connection,
                source_key=source_key,
                size_bytes=previous_size_bytes,
                mtime_ns=previous_mtime_ns,
                missing_empty_is_complete=True,
            )
            inserted = False
            if record is not None:
                inserted = self._insert_record_with_connection(connection, record)
            if source_was_complete and source_stat is not None:
                progress_offset_bytes = source_stat.st_size
                if appended_offset_bytes is not None:
                    progress_offset_bytes = min(
                        max(0, int(appended_offset_bytes)),
                        source_stat.st_size,
                    )
                self._record_import_progress_with_connection(
                    connection,
                    source,
                    source_stat=source_stat,
                    offset_bytes=progress_offset_bytes,
                    complete=progress_offset_bytes >= source_stat.st_size,
                )
            return inserted

    def import_from_jsonl(
        self,
        source_path: Path | str,
        *,
        max_lines: int | None = None,
        max_seconds: float | None = None,
        reset: bool = False,
    ) -> AuditIndexImportResult:
        self.ensure_schema()
        source = Path(source_path)
        result = AuditIndexImportResult(database_path=self.path, source_path=source)
        if not source.exists():
            result.complete = True
            return result

        source_key = str(source.expanduser().resolve())
        stat = source.stat()
        with connect_audit_index_db(self.path) as connection:
            if reset:
                connection.execute("DELETE FROM tool_call_audit_entries")
                connection.execute(
                    "DELETE FROM audit_import_sources WHERE source_path = ?",
                    (source_key,),
                )
                offset = 0
            else:
                row = connection.execute(
                    """
                    SELECT offset_bytes, size_bytes, mtime_ns
                    FROM audit_import_sources
                    WHERE source_path = ?
                    """,
                    (source_key,),
                ).fetchone()
                offset = int(row["offset_bytes"]) if row is not None else 0
                if offset > stat.st_size:
                    offset = 0

            deadline = time.monotonic() + max_seconds if max_seconds is not None else None
            with source.open("rb") as handle:
                handle.seek(offset)
                while True:
                    if max_lines is not None and result.lines_scanned >= max_lines:
                        result.stopped_by_limit = True
                        break
                    if deadline is not None and time.monotonic() >= deadline:
                        result.stopped_by_limit = True
                        break
                    raw_line = handle.readline()
                    if not raw_line:
                        break
                    offset = handle.tell()
                    self._import_line(raw_line, result, connection)

            final_stat = source.stat()
            result.offset_bytes = offset
            result.complete = offset >= final_stat.st_size
            self._record_import_progress_with_connection(
                connection,
                source,
                source_stat=final_stat,
                offset_bytes=offset,
                complete=result.complete,
            )
        return result

    def import_from_jsonl_tail(
        self,
        source_path: Path | str,
        *,
        created_after: datetime | None = None,
        max_lines: int = DEFAULT_TAIL_IMPORT_MAX_LINES,
    ) -> AuditIndexImportResult:
        self.ensure_schema()
        source = Path(source_path)
        result = AuditIndexImportResult(database_path=self.path, source_path=source)
        if not source.exists():
            result.complete = True
            return result

        created_after_utc = _normalize_datetime(created_after)
        with connect_audit_index_db(self.path) as connection:
            for raw_line in _iter_file_lines_reverse(source):
                if result.lines_scanned >= max_lines:
                    result.stopped_by_limit = True
                    break
                result.lines_scanned += 1
                payload = _json_line_to_object(raw_line)
                if payload is None:
                    result.skipped_malformed += 1
                    continue
                event_type = str(payload.get("event_type") or "").strip().casefold()
                if event_type != "tool_call":
                    result.skipped_non_tool_call += 1
                    continue
                record = tool_call_audit_index_record_from_payload(payload)
                if record is None:
                    result.skipped_malformed += 1
                    continue
                if created_after_utc is not None and record.created_at < created_after_utc:
                    result.reached_time_boundary = True
                    break
                if self._insert_record_with_connection(connection, record):
                    result.imported += 1
                else:
                    result.existing += 1
            else:
                result.complete = True
                result.reached_time_boundary = created_after_utc is not None

            if result.complete:
                final_stat = source.stat()
                result.offset_bytes = final_stat.st_size
                self._record_import_progress_with_connection(
                    connection,
                    source,
                    source_stat=final_stat,
                    offset_bytes=final_stat.st_size,
                    complete=True,
                )
        return result

    def query_tool_calls(
        self,
        *,
        session_id: str | None,
        tool_name: str | None,
        status: str | None,
        directory_path: str | None,
        direct_only: bool,
        created_after: datetime | None,
        created_before: datetime | None,
        q: str | None,
        limit: int,
    ) -> ToolCallAuditIndexPage:
        self.ensure_schema()
        where_sql, values = _build_query_filters(
            session_id=session_id,
            tool_name=tool_name,
            status=status,
            directory_path=directory_path,
            direct_only=direct_only,
            created_after=created_after,
            created_before=created_before,
            q=q,
        )
        where_clause = f"WHERE {' AND '.join(where_sql)}" if where_sql else ""
        with connect_audit_index_db(self.path) as connection:
            matched_row = connection.execute(
                f"SELECT COUNT(*) AS count FROM tool_call_audit_entries {where_clause}",
                values,
            ).fetchone()
            matched = int(matched_row["count"]) if matched_row is not None else 0
            status_counts = {
                str(row["status"] or "success"): int(row["count"])
                for row in connection.execute(
                    f"""
                    SELECT status, COUNT(*) AS count
                    FROM tool_call_audit_entries
                    {where_clause}
                    GROUP BY status
                    """,
                    values,
                ).fetchall()
            }
            rows = connection.execute(
                f"""
                SELECT payload_json
                FROM tool_call_audit_entries
                {where_clause}
                ORDER BY created_at DESC, row_id DESC
                LIMIT ?
                """,
                [*values, limit + 1],
            ).fetchall()
        payloads: list[dict[str, Any]] = []
        for row in rows[:limit]:
            payload = _json_line_to_object(str(row["payload_json"] or ""))
            if payload is not None:
                payloads.append(payload)
        return ToolCallAuditIndexPage(
            payloads=payloads,
            matched=matched,
            returned=len(payloads),
            status_counts=dict(sorted(status_counts.items())),
            has_more=len(rows) > limit or matched > len(payloads),
        )

    def import_is_complete(self, source_path: Path | str) -> bool:
        source = Path(source_path)
        if not source.exists():
            return True
        source_key = str(source.expanduser().resolve())
        stat = source.stat()
        self.ensure_schema()
        with connect_audit_index_db(self.path) as connection:
            row = connection.execute(
                """
                SELECT size_bytes, mtime_ns, offset_bytes, complete
                FROM audit_import_sources
                WHERE source_path = ?
                """,
                (source_key,),
            ).fetchone()
        if row is None:
            return False
        return (
            int(row["complete"]) == 1
            and int(row["size_bytes"]) == stat.st_size
            and int(row["mtime_ns"]) == stat.st_mtime_ns
            and int(row["offset_bytes"]) >= stat.st_size
        )

    def _import_line(
        self,
        raw_line: bytes,
        result: AuditIndexImportResult,
        connection: sqlite3.Connection,
    ) -> None:
        result.lines_scanned += 1
        payload = _json_line_to_object(raw_line.decode("utf-8", errors="replace"))
        if payload is None:
            result.skipped_malformed += 1
            return
        event_type = str(payload.get("event_type") or "").strip().casefold()
        if event_type != "tool_call":
            result.skipped_non_tool_call += 1
            return
        record = tool_call_audit_index_record_from_payload(payload)
        if record is None:
            result.skipped_malformed += 1
            return
        if self._insert_record_with_connection(connection, record):
            result.imported += 1
        else:
            result.existing += 1

    def _insert_record(self, record: ToolCallAuditIndexRecord) -> bool:
        with connect_audit_index_db(self.path) as connection:
            return self._insert_record_with_connection(connection, record)

    def _insert_record_with_connection(
        self,
        connection: sqlite3.Connection,
        record: ToolCallAuditIndexRecord,
    ) -> bool:
        cursor = connection.execute(_INSERT_TOOL_CALL_AUDIT_SQL, _record_values(record))
        return cursor.rowcount > 0

    def _record_import_progress(
        self,
        source_path: Path,
        *,
        offset_bytes: int,
        complete: bool,
    ) -> None:
        stat = source_path.stat()
        with connect_audit_index_db(self.path) as connection:
            self._record_import_progress_with_connection(
                connection,
                source_path,
                source_stat=stat,
                offset_bytes=offset_bytes,
                complete=complete,
            )

    def _record_import_progress_with_connection(
        self,
        connection: sqlite3.Connection,
        source_path: Path,
        *,
        source_stat: os.stat_result,
        offset_bytes: int,
        complete: bool,
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_import_sources (
                source_path,
                size_bytes,
                mtime_ns,
                offset_bytes,
                complete,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_path) DO UPDATE SET
                size_bytes = excluded.size_bytes,
                mtime_ns = excluded.mtime_ns,
                offset_bytes = excluded.offset_bytes,
                complete = excluded.complete,
                updated_at = excluded.updated_at
            """,
            (
                str(source_path.expanduser().resolve()),
                source_stat.st_size,
                source_stat.st_mtime_ns,
                offset_bytes,
                1 if complete else 0,
                _datetime_to_storage(datetime.now(UTC)),
            ),
        )

    def _source_import_complete_for_stat(
        self,
        connection: sqlite3.Connection,
        *,
        source_key: str,
        size_bytes: int | None,
        mtime_ns: int | None,
        missing_empty_is_complete: bool = False,
    ) -> bool:
        if size_bytes is None or mtime_ns is None:
            return False
        row = connection.execute(
            """
            SELECT size_bytes, mtime_ns, offset_bytes, complete
            FROM audit_import_sources
            WHERE source_path = ?
            """,
            (source_key,),
        ).fetchone()
        if row is None:
            return missing_empty_is_complete and size_bytes == 0
        return (
            int(row["complete"]) == 1
            and int(row["size_bytes"]) == size_bytes
            and int(row["mtime_ns"]) == mtime_ns
            and int(row["offset_bytes"]) >= size_bytes
        )


def tool_call_audit_index_record_from_payload(
    payload: dict[str, Any],
) -> ToolCallAuditIndexRecord | None:
    created_at = _parse_tool_call_datetime(
        payload.get("created_at") or payload.get("timestamp") or payload.get("occurred_at")
    )
    if created_at is None:
        return None
    raw_args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
    raw_result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    redacted_args = _redact_sensitive_payload(raw_args)
    redacted_result = _redact_sensitive_payload(raw_result)
    status = _tool_call_failure_status(payload)
    decision = _first_text(payload.get("decision"), payload.get("status"))
    request_id = _first_text(
        payload.get("request_id"),
        raw_args.get("request_id"),
        raw_result.get("request_id"),
    )
    correlation_id = _first_text(
        payload.get("correlation_id"),
        raw_args.get("correlation_id"),
        raw_result.get("correlation_id"),
    )
    approval_request_id = _first_text(
        payload.get("approval_request_id"),
        raw_args.get("approval_request_id"),
        raw_result.get("approval_request_id"),
    )
    mcp_tool_approval_lease_id = _first_text(
        payload.get("mcp_tool_approval_lease_id"),
        payload.get("tool_approval_lease_id"),
        raw_args.get("mcp_tool_approval_lease_id"),
        raw_args.get("tool_approval_lease_id"),
        raw_result.get("mcp_tool_approval_lease_id"),
        raw_result.get("tool_approval_lease_id"),
    )
    tool_name = _first_text(
        payload.get("tool_name"), payload.get("tool"), raw_args.get("tool_name")
    )
    session_id = _first_text(
        payload.get("session_id"),
        raw_args.get("session_id"),
        raw_result.get("session_id"),
        payload.get("target_session_id"),
    )
    vm_id = _first_text(
        payload.get("vm_id"),
        payload.get("vm_name"),
        raw_args.get("vm_id"),
        raw_args.get("vm_name"),
        raw_result.get("vm_id"),
        raw_result.get("vm_name"),
    )
    workflow_run_id = _first_text(
        payload.get("workflow_run_id"),
        raw_args.get("workflow_run_id"),
        raw_result.get("workflow_run_id"),
    )
    trace_id = _first_text(
        payload.get("trace_id"),
        payload.get("workflow_trace_id"),
        raw_args.get("trace_id"),
        raw_args.get("workflow_trace_id"),
        raw_result.get("trace_id"),
        raw_result.get("workflow_trace_id"),
    )
    duration_ms = payload.get("duration_ms")
    try:
        normalized_duration_ms = int(duration_ms) if duration_ms is not None else None
    except (TypeError, ValueError):
        normalized_duration_ms = None

    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    error_summary = _tool_call_error_summary(payload, status, raw_result)
    directory_path = _normalize_directory_path(payload.get("directory_path"))
    search_text = " ".join(
        part
        for part in (
            _safe_search_text(session_id),
            _safe_search_text(vm_id),
            _safe_search_text(directory_path),
            _safe_search_text(_first_text(payload.get("agent_role"), payload.get("role"))),
            _safe_search_text(_first_text(payload.get("actor_user_id"), payload.get("user_id"))),
            _safe_search_text(tool_name),
            _safe_search_text(request_id),
            _safe_search_text(correlation_id),
            _safe_search_text(approval_request_id),
            _safe_search_text(mcp_tool_approval_lease_id),
            _safe_search_text(workflow_run_id),
            _safe_search_text(trace_id),
            _safe_search_text(decision),
            _safe_search_text(status),
            _safe_search_text(error_summary),
            _safe_search_text(redacted_args),
            _safe_search_text(redacted_result),
        )
        if part
    ).casefold()
    return ToolCallAuditIndexRecord(
        event_key=f"payload:{hashlib.sha256(payload_json.encode('utf-8')).hexdigest()}",
        created_at=created_at,
        created_at_storage=_datetime_to_storage(created_at),
        directory_path=directory_path,
        session_id=session_id,
        vm_id=vm_id,
        agent_role=_first_text(payload.get("agent_role"), payload.get("role")),
        actor_user_id=_first_text(payload.get("actor_user_id"), payload.get("user_id")),
        tool_name=tool_name,
        status=status,
        decision=decision,
        request_id=request_id,
        correlation_id=correlation_id,
        approval_request_id=approval_request_id,
        mcp_tool_approval_lease_id=mcp_tool_approval_lease_id,
        workflow_run_id=workflow_run_id,
        trace_id=trace_id,
        duration_ms=normalized_duration_ms,
        error_summary=error_summary,
        search_text=search_text,
        payload_json=payload_json,
    )


def _record_values(record: ToolCallAuditIndexRecord) -> tuple[Any, ...]:
    return (
        record.event_key,
        record.created_at_storage,
        record.directory_path,
        record.session_id,
        record.vm_id,
        record.agent_role,
        record.actor_user_id,
        record.tool_name,
        record.status,
        record.decision,
        record.request_id,
        record.correlation_id,
        record.approval_request_id,
        record.mcp_tool_approval_lease_id,
        record.workflow_run_id,
        record.trace_id,
        record.duration_ms,
        record.error_summary,
        record.search_text,
        record.payload_json,
    )


def _build_query_filters(
    *,
    session_id: str | None,
    tool_name: str | None,
    status: str | None,
    directory_path: str | None,
    direct_only: bool,
    created_after: datetime | None,
    created_before: datetime | None,
    q: str | None,
) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    values: list[Any] = []
    if session_id is not None:
        clauses.append("session_id = ?")
        values.append(str(session_id).strip())
    if tool_name is not None:
        clauses.append("tool_name = ?")
        values.append(str(tool_name).strip())
    if status is not None:
        clauses.append("status = ?")
        values.append(str(status).strip().casefold())
    if directory_path is not None:
        selected_path = _normalize_directory_path(directory_path)
        if direct_only:
            clauses.append("directory_path = ?")
            values.append(selected_path)
        else:
            clauses.append("(directory_path = ? OR directory_path LIKE ? ESCAPE '\\')")
            values.extend([selected_path, f"{_escape_like(selected_path)}/%"])
    after_storage = _datetime_to_storage(_normalize_datetime(created_after))
    if after_storage is not None:
        clauses.append("created_at >= ?")
        values.append(after_storage)
    before_storage = _datetime_to_storage(_normalize_datetime(created_before))
    if before_storage is not None:
        clauses.append("created_at <= ?")
        values.append(before_storage)
    normalized_query = str(q or "").strip().casefold()
    if normalized_query:
        clauses.append("search_text LIKE ? ESCAPE '\\'")
        values.append(f"%{_escape_like(normalized_query)}%")
    return clauses, values


def _json_line_to_object(line: str | bytes) -> dict[str, Any] | None:
    text = line.decode("utf-8", errors="replace") if isinstance(line, bytes) else line
    text = text.strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _iter_file_lines_reverse(path: Path, *, block_size: int = 64 * 1024) -> Iterator[str]:
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        position = handle.tell()
        buffer = b""
        while position > 0:
            read_size = min(block_size, position)
            position -= read_size
            handle.seek(position)
            chunk = handle.read(read_size)
            lines = (chunk + buffer).split(b"\n")
            buffer = lines[0]
            for raw_line in reversed(lines[1:]):
                if raw_line:
                    yield raw_line.decode("utf-8", errors="replace")
        if buffer:
            yield buffer.decode("utf-8", errors="replace")


def _parse_tool_call_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _normalize_datetime(value)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return _normalize_datetime(parsed)


def _normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _datetime_to_storage(value: datetime | None) -> str | None:
    normalized = _normalize_datetime(value)
    if normalized is None:
        return None
    return normalized.isoformat().replace("+00:00", "Z")


def _normalize_directory_path(directory_path: Any) -> str:
    try:
        return DirectoryPath(segments=str(directory_path or "root")).as_posix()
    except ValueError:
        return "root"


def _first_text(*candidates: Any) -> str | None:
    for candidate in candidates:
        text = str(candidate or "").strip()
        if text:
            return text
    return None


def _tool_call_failure_status(event: dict[str, Any]) -> str:
    decision = str(event.get("decision") or event.get("status") or "").strip().casefold()
    result = event.get("result") if isinstance(event.get("result"), dict) else {}
    result_status = str(result.get("status") or "").strip().casefold()
    if decision == "pending_approval":
        return "pending_approval"
    if decision in {"denied", "blocked", "rejected"}:
        return "denied"
    if decision in {"error", "failed", "failure"}:
        return "error"
    if decision in {"allowed", "approved", "success", "ok"}:
        return "success"
    if result_status == "pending_approval":
        return "pending_approval"
    if result_status in {"denied", "blocked", "rejected"}:
        return "denied"
    if result_status in {"error", "failed", "failure"}:
        return "error"
    if result_status in {"allowed", "approved", "success", "ok"}:
        return "success"
    if isinstance(result, dict) and any(
        key in result for key in ("error", "error_class", "exception", "traceback")
    ):
        return "error"
    return "success"


def _tool_call_error_summary(
    entry: dict[str, Any], status: str, result: dict[str, Any]
) -> str | None:
    for candidate in (
        entry.get("reason"),
        result.get("error"),
        result.get("message"),
        result.get("details"),
        result.get("summary"),
    ):
        text = str(candidate or "").strip()
        if text:
            return text
    if status == "denied":
        return "Denied"
    if status == "error":
        return "Tool call failed."
    if status == "pending_approval":
        return "Approval required."
    return None


def _redact_sensitive_payload(value: Any, *, key_name: str | None = None) -> Any:
    normalized_key = (key_name or "").strip().lower()
    if normalized_key and any(marker in normalized_key for marker in _SENSITIVE_PAYLOAD_MARKERS):
        return "<redacted>"
    if isinstance(value, dict):
        return {
            str(key): _redact_sensitive_payload(item, key_name=str(key))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive_payload(item) for item in value]
    return value


def _safe_search_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return str(value)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
