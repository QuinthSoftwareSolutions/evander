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
        policies = getattr(self.providers[request.provider], "coverage_policies", {"provider"})
        if request.coverage_policy not in policies:
            raise ValueError("The selected provider does not support this coverage policy")
        if request.resume_subscription_profile_id is not None and not getattr(
            self.providers[request.provider], "supports_subscription_handoff", False
        ):
            raise ValueError("The selected provider does not support subscription handoff")
        if request.max_batch_text_bytes is not None and not getattr(
            self.providers[request.provider], "supports_batch_text_limit", False
        ):
            raise ValueError("The selected provider does not support a batch text limit")
        if request.max_parallel_reviews is not None and not getattr(
            self.providers[request.provider], "supports_review_parallelism", False
        ):
            raise ValueError("The selected provider does not support review parallelism")
        if (
            request.exclude_paths is not None or request.rebuild_pending_batches
            or request.incomplete_review_policy is not None
        ) and not getattr(self.providers[request.provider], "supports_scan_tail_controls", False):
            raise ValueError("The selected provider does not support scan tail controls")
        if (
            request.incomplete_review_policy == "isolate_blockers"
            or request.retry_blocked_paths is not None
        ) and not getattr(self.providers[request.provider], "supports_file_blocker_isolation", False):
            raise ValueError("The selected provider does not support file blocker isolation")
        if (
            request.exclude_patterns is not None
            or (request.exclude_paths and request.resume_from_scan_id is None)
        ) and not getattr(self.providers[request.provider], "supports_pattern_exclusions", False):
            raise ValueError("The selected provider does not support pattern or initial exclusions")
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

    def read_metadata(self, scan_id: str, *, directory_path: str) -> dict[str, Any]:
        """Read scoped operator metadata without polling, launching, or disclosing logs."""
        if not re.fullmatch(r"[a-f0-9]{32}", scan_id):
            raise ValueError("Invalid security scan identity")
        record = self.store.get(scan_id, directory_path)
        output = record.outputs()
        result = {
            key: output.get(key)
            for key in (
                "scan_id",
                "directory_path",
                "managed_repo_id",
                "mode",
                "status",
                "coverage",
                "coverage_policy",
                "resume_from_scan_id",
                "to_commit",
                "coverage_progress",
                "cleanup_confirmed",
            )
        }
        describe = getattr(self.providers.get(record.request.provider), "describe", None)
        result["resumable"] = False
        if callable(describe):
            extra = describe(record=record, runtime_dir=self.store.path.parent)
            result.update(
                {
                    key: value
                    for key, value in extra.items()
                    if key
                    in {
                        "resumable",
                        "resumable_until",
                        "pinned_ref",
                        "to_commit",
                        "coverage_progress",
                        "cleanup_confirmed",
                        "lineage",
                        "inventory_digest",
                        "execution_configuration",
                    }
                }
            )
        result["coverage_progress"] = {
            key: value
            for key, value in (result.get("coverage_progress") or {}).items()
            if key
            in {
                "phase",
                "files_total",
                "files_reviewed",
                "files_not_applicable",
                "files_blocked",
                "files_isolated",
                "isolated_files",
                "files_excluded",
                "files_pending",
                "active_reviews",
                "completed_reviews",
                "attempts",
                "required_questions_total",
                "required_questions_pending",
                "required_question_aliases",
                "deadline_at",
                "last_progress_at",
                "cleanup_condition",
                "max_batch_text_bytes",
                "max_parallel_reviews",
                "exclude_patterns",
                "incomplete_review_policy",
            }
        }
        if "execution_configuration" in result:
            result["execution_configuration"] = {
                key: value
                for key, value in (result["execution_configuration"] or {}).items()
                if key
                in {
                    "model",
                    "reasoning_effort",
                    "auth_mode",
                    "subscription_profile_id",
                    "cyber_access_program",
                    "model_provider_mode",
                    "image",
                    "image_alias",
                }
            }
        return result

    def _advance(self, record: SecurityScanRecord, runtime: Any) -> SecurityScanRecord:
        if (
            record.result
            and record.result.status in TERMINAL_SCAN_STATUSES
            and record.result.cleanup_confirmed
        ):
            return record
        provider = self.providers.get(record.request.provider)
        if provider is None:
            raise ValueError(f"Security scan provider {record.request.provider!r} is not enabled")
        if record.result and record.result.status in TERMINAL_SCAN_STATUSES:
            try:
                updated = (
                    provider.poll(handle=record.handle, runtime=runtime)
                    if record.handle
                    else provider.cancel(
                        scan_id=record.scan_id, request=record.request, handle=None, runtime=runtime
                    )
                )
            except (ValueError, RuntimeError, OSError):
                return record
            if isinstance(updated, SecurityScanResult):
                record.result = record.result.model_copy(
                    update={
                        "cleanup_confirmed": updated.cleanup_confirmed,
                        "coverage_progress": updated.coverage_progress,
                    }
                )
                self.store.save(record)
            return record
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
            except (ValueError, RuntimeError, LookupError, OSError):
                if record.request.coverage_policy != "all_tracked_files":
                    raise
                return self._cancel(
                    record,
                    provider,
                    runtime,
                    "failed",
                    "Tracked scan could not start; inspect the scanner configuration and private operator diagnostics",
                )
            self.store.save(record)
        try:
            result = provider.poll(handle=record.handle, runtime=runtime)
        except SecurityScanRetryableError:
            return record
        except ValueError as exc:
            if record.request.coverage_policy == "all_tracked_files":
                result = record.result or SecurityScanResult(
                    status="failed", coverage="partial", cleanup_confirmed=False
                )
                try:
                    provider.cancel(
                        scan_id=record.scan_id,
                        request=record.request,
                        handle=record.handle,
                        runtime=runtime,
                    )
                    result = provider.poll(handle=record.handle, runtime=runtime)
                except (ValueError, RuntimeError, OSError):
                    result = result.model_copy(update={"cleanup_confirmed": False})
                result = result.model_copy(
                    update={
                        "status": "failed",
                        "error": "Coverage state could not be verified; inspect operator diagnostics",
                    }
                )
            else:
                result = SecurityScanResult(status="failed", error=str(exc))
        if result.status == "completed" and result.coverage != "complete":
            result = result.model_copy(
                update={"status": "failed", "error": "Security scan coverage is incomplete"}
            )
        record.result = result
        self.store.save(record)
        return record

    def _cancel(self, record, provider, runtime, status, reason):
        result = record.result or SecurityScanResult(status=status, cleanup_confirmed=False)
        try:
            cancelled = provider.cancel(
                scan_id=record.scan_id,
                request=record.request,
                handle=record.handle,
                runtime=runtime,
            )
            if isinstance(cancelled, SecurityScanResult):
                result = cancelled
            elif record.handle:
                result = provider.poll(handle=record.handle, runtime=runtime)
            else:
                result = result.model_copy(update={"cleanup_confirmed": True})
        except (ValueError, RuntimeError, OSError):
            # Preserve the reservation while a lost launch/stop is uncertain.
            result = result.model_copy(update={"cleanup_confirmed": False})
        record.result = result.model_copy(update={"status": status, "error": reason})
        self.store.save(record)
        return record

    def cancel(self, scan_id: str, *, directory_path: str, runtime: Any) -> SecurityScanRecord:
        with self._locked(scan_id):
            record = self.store.get(scan_id, directory_path)
            if record.result and record.result.status in TERMINAL_SCAN_STATUSES:
                return self._advance(record, runtime)
            provider = self.providers[record.request.provider]
            return self._cancel(
                record, provider, runtime, "cancelled", "Workflow execution cancelled"
            )
