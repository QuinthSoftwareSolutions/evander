from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evander.models import AuditEvent
from evander.repositories.audit_index import ToolCallAuditIndex


class AuditEventType:
    FEEDBACK_DETECTED = "feedback_detected"
    PR_COMPLETION_DETECTED = "pr_completion_detected"
    MAPPING_RESOLVED = "mapping_resolved"
    BUSY_SKIP = "busy_skip"
    VM_START_ATTEMPTED = "vm_start_attempted"
    VM_START_SUCCEEDED = "vm_start_succeeded"
    VM_START_FAILED = "vm_start_failed"
    RESUME_ATTEMPTED = "resume_attempted"
    RESUME_SUCCEEDED = "resume_succeeded"
    RESUME_FAILED = "resume_failed"


class AuditLogger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()
        self._index = ToolCallAuditIndex.for_audit_log(self.path)

    def log(self, event: AuditEvent) -> None:
        payload = event.model_dump(mode="json")
        try:
            previous_stat = self.path.stat()
            previous_size_bytes = previous_stat.st_size
            previous_mtime_ns = previous_stat.st_mtime_ns
        except OSError:
            previous_size_bytes = None
            previous_mtime_ns = None
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True))
            handle.write("\n")
        try:
            self._index.index_appended_payload(
                payload,
                self.path,
                previous_size_bytes=previous_size_bytes,
                previous_mtime_ns=previous_mtime_ns,
            )
        except Exception:
            pass

    def log_decision(
        self,
        *,
        event_type: str,
        session_id: str | None = None,
        vm_id: str | None = None,
        provider: str | None = None,
        repo: str | None = None,
        pr_id: str | None = None,
        approval_request_id: str | None = None,
        decision: str | None = None,
        reason: str | None = None,
        args: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        self.log(
            AuditEvent(
                event_type=event_type,
                session_id=session_id,
                vm_id=vm_id,
                provider=provider,
                repo=repo,
                pr_id=pr_id,
                approval_request_id=approval_request_id,
                decision=decision,
                reason=reason,
                args=args or {},
                result=result or {},
            )
        )
