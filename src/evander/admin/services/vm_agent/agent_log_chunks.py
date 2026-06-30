from __future__ import annotations

from evander.admin.services.vm_agent.common import (
    Any,
)


class VmAgentLogChunkMixin:
    def _container_execution_log_chunk(
        self,
        worker_backend: Any,
        execution: Any,
        *,
        lines: int,
        mode: str,
    ) -> str:
        header_parts = [
            f"execution={execution.execution_id}",
            f"status={self._container_execution_status(execution)}",
            f"worker_job={execution.worker_job_id}",
            f"log={mode}",
        ]
        if execution.started_at is not None:
            header_parts.append(f"started={execution.started_at.isoformat()}")
        if execution.finished_at is not None:
            header_parts.append(f"finished={execution.finished_at.isoformat()}")
        try:
            text = worker_backend.get_log_snapshot(
                execution.execution_id,
                lines=lines,
                mode=mode,
            ).rstrip()
        except Exception as exc:
            text = f"[log unavailable: {exc}]"
        header = "=== " + " ".join(header_parts) + " ==="
        return header if not text else header + "\n" + text

    @staticmethod
    def _join_container_log_chunks(chunks: list[str]) -> str:
        return "\n\n".join(chunk for chunk in chunks if chunk).rstrip() + "\n"

    def _container_thread_execution_fallback_log(
        self,
        worker_backend: Any,
        records: list[Any],
        *,
        lines: int,
    ) -> str:
        ordered_records = sorted(records, key=self._container_execution_sort_key)
        per_execution_lines = lines
        if lines > 0 and len(ordered_records) > 1:
            per_execution_lines = max(1, lines // len(ordered_records))
        chunks: list[str] = []
        for execution in ordered_records:
            try:
                text = worker_backend.get_log_snapshot(
                    execution.execution_id,
                    lines=per_execution_lines,
                    mode="execution",
                )
            except Exception as exc:
                text = f"[log unavailable: {exc}]"
            body = self._container_log_without_session_id_lines(text).rstrip()
            if body:
                chunks.append(body)
        return self._join_container_log_chunks(chunks).rstrip()

    def _container_thread_transcript(
        self,
        worker_backend: Any,
        records: list[Any],
        *,
        lines: int,
    ) -> str:
        first_execution = records[0]
        session_error: Exception | None = None
        try:
            transcript = worker_backend.get_log_snapshot(
                first_execution.execution_id,
                lines=lines,
                mode="session",
            ).rstrip()
        except Exception as exc:
            session_error = exc
            transcript = ""
        if transcript:
            return transcript
        fallback = self._container_thread_execution_fallback_log(
            worker_backend,
            records,
            lines=lines,
        ).rstrip()
        if fallback:
            return fallback
        if session_error is not None:
            return f"[log unavailable: {session_error}]"
        return ""

    def _container_execution_thread_probe_text(self, worker_backend: Any, execution: Any) -> str:
        page_reader = getattr(worker_backend, "get_log_page", None)
        if callable(page_reader):
            try:
                page = page_reader(
                    execution.execution_id,
                    limit=1,
                    offset=0,
                    query="session id:",
                    mode="raw",
                )
                text = str(page.get("text") or "")
                if text:
                    return text
            except Exception:
                pass
        try:
            return str(
                worker_backend.get_log_snapshot(
                    execution.execution_id,
                    lines=0,
                    mode="raw",
                )
            )
        except Exception as exc:
            return f"[log unavailable: {exc}]"

    container_execution_log_chunk = _container_execution_log_chunk
    join_container_log_chunks = _join_container_log_chunks
    container_thread_execution_fallback_log = _container_thread_execution_fallback_log
    container_thread_transcript = _container_thread_transcript
    container_execution_thread_probe_text = _container_execution_thread_probe_text
