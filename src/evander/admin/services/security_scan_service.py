"""Durable scan lifecycle, separate from workflow node and scanner implementations."""

from __future__ import annotations

import fcntl
import re
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evander.models import DirectoryPath
from evander.security_scans import (
    SecurityScanRecord,
    SecurityScanRequest,
    SecurityScanResult,
    SecurityScanStore,
    SecurityScanRetryableError,
    TERMINAL_SCAN_STATUSES,
)
from evander.session_expiry import ensure_session_not_expired


class SecurityScanService:
    def __init__(self, *, runtime_dir: Path, providers: dict[str, Any], sessions: Any) -> None:
        self.store = SecurityScanStore(runtime_dir)
        self.providers = providers
        self.sessions = sessions

    @contextmanager
    def _locked(self, scan_id: str):
        if not re.fullmatch(r"[0-9a-f]{32}", scan_id):
            raise ValueError("Invalid security scan identity")
        root = self.store.path.parent / "security-scan-locks"
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (root / f"{scan_id}.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _session(self, request: SecurityScanRequest, directory_path: str):
        session = self.sessions.get_session(request.session_id)
        if session is None:
            raise ValueError(f"Unknown session: {request.session_id}")
        scope = DirectoryPath(segments=directory_path).as_posix()
        target = DirectoryPath(segments=session.directory_path).as_posix()
        if target != scope and not target.startswith(scope + "/"):
            raise ValueError("Security scan session is outside workflow directory scope")
        ensure_session_not_expired(session)
        return session

    def start(
        self,
        request: SecurityScanRequest,
        *,
        directory_path: str,
        execution_id: str,
        idempotency_key: str,
        runtime: Any,
    ) -> SecurityScanRecord:
        self._session(request, directory_path)
        if request.provider not in self.providers:
            raise ValueError(
                f"Security scan provider {request.provider!r} is not installed and enabled"
            )
        scan_id = self.store.identity(directory_path, idempotency_key)
        with self._locked(scan_id):
            record = self.store.create(
                SecurityScanRecord(
                    scan_id=scan_id,
                    directory_path=directory_path,
                    request=request,
                    execution_id=execution_id,
                    owner_idempotency_key=idempotency_key,
                )
            )
            return self._advance(record, runtime)

    def poll(self, scan_id: str, *, directory_path: str, runtime: Any) -> SecurityScanRecord:
        with self._locked(scan_id):
            return self._advance(self.store.get(scan_id, directory_path), runtime)

    def _advance(self, record: SecurityScanRecord, runtime: Any) -> SecurityScanRecord:
        if record.result and record.result.status in TERMINAL_SCAN_STATUSES:
            return record
        provider = self.providers.get(record.request.provider)
        if provider is None:
            raise ValueError(f"Security scan provider {record.request.provider!r} is not enabled")
        try:
            self._session(record.request, record.directory_path)
            elapsed = (datetime.now(UTC) - record.created_at).total_seconds()
            if elapsed >= record.request.timeout_seconds:
                return self._cancel(
                    record, provider, runtime, "timed_out", "Security scan timed out"
                )
        except ValueError as exc:
            return self._cancel(record, provider, runtime, "cancelled", str(exc))
        # A provider start uses the stable scan identity even when the preceding response was lost.
        if record.handle is None:
            try:
                record.handle = provider.start(
                    scan_id=record.scan_id, request=record.request, runtime=runtime
                )
            except SecurityScanRetryableError:
                return record
            self.store.save(record)
        try:
            result = provider.poll(handle=record.handle, runtime=runtime)
        except SecurityScanRetryableError:
            return record
        except ValueError as exc:
            result = SecurityScanResult(status="failed", error=str(exc))
        if result.status == "completed" and result.coverage != "complete":
            result = result.model_copy(
                update={"status": "failed", "error": "Security scan coverage is incomplete"}
            )
        record.result = result
        self.store.save(record)
        return record

    def _cancel(self, record, provider, runtime, status, reason):
        provider.cancel(
            scan_id=record.scan_id, request=record.request, handle=record.handle, runtime=runtime
        )
        if record.handle:
            try:
                result = provider.poll(handle=record.handle, runtime=runtime)
            except (ValueError, RuntimeError):
                # A stopped/expired session may no longer permit artifact collection.
                result = record.result or SecurityScanResult(status=status)
        else:
            result = SecurityScanResult(status=status)
        record.result = result.model_copy(update={"status": status, "error": reason})
        self.store.save(record)
        return record

    def cancel(self, scan_id: str, *, directory_path: str, runtime: Any) -> SecurityScanRecord:
        with self._locked(scan_id):
            record = self.store.get(scan_id, directory_path)
            if record.result and record.result.status in TERMINAL_SCAN_STATUSES:
                return record
            provider = self.providers[record.request.provider]
            return self._cancel(
                record, provider, runtime, "cancelled", "Workflow execution cancelled"
            )
