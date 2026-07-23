from __future__ import annotations

from evander.admin.services.vm_agent.common import (
    Any,
    Mapping,
)

_CONTAINER_EXECUTION_LOG_PAGE_FETCH_LIMIT = 5000


class VmAgentLogChunkMixin:
    def _container_execution_log_header(
        self,
        execution: Any,
        *,
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
        return "=== " + " ".join(header_parts) + " ==="

    def _container_execution_log_chunk(
        self,
        worker_backend: Any,
        execution: Any,
        *,
        lines: int,
        mode: str,
    ) -> str:
        header = self._container_execution_log_header(execution, mode=mode)
        try:
            text = worker_backend.get_log_snapshot(
                execution.execution_id,
                lines=lines,
                mode=mode,
            ).rstrip()
        except Exception as exc:
            text = f"[log unavailable: {exc}]"
        return header if not text else header + "\n" + text

    def _container_execution_log_page_chunk(
        self,
        worker_backend: Any,
        execution: Any,
        *,
        body_lines: int,
        mode: str,
    ) -> tuple[str, bool]:
        page_reader = getattr(worker_backend, "get_log_page", None)
        if not callable(page_reader):
            return (
                self._container_execution_log_chunk(
                    worker_backend,
                    execution,
                    lines=max(1, body_lines),
                    mode=mode,
                ),
                False,
            )

        header = self._container_execution_log_header(execution, mode=mode)
        remaining = max(1, int(body_lines))
        page_offset = 0
        body_pages: list[str] = []
        has_more_before = False
        while remaining > 0:
            try:
                page = page_reader(
                    execution.execution_id,
                    limit=min(remaining, _CONTAINER_EXECUTION_LOG_PAGE_FETCH_LIMIT),
                    offset=page_offset,
                    query=None,
                    mode=mode,
                )
            except TypeError:
                if page_offset == 0:
                    return (
                        self._container_execution_log_chunk(
                            worker_backend,
                            execution,
                            lines=max(1, body_lines),
                            mode=mode,
                        ),
                        False,
                    )
                return header + "\n[log unavailable: paged log reader rejected continuation]", False
            except Exception as exc:
                return header + f"\n[log unavailable: {exc}]", False
            if not isinstance(page, Mapping):
                return header + "\n[log unavailable: paged log reader returned invalid data]", False

            text = str(page.get("text") or "").rstrip()
            returned_lines = len(text.splitlines())
            if text:
                body_pages.insert(0, text)
            has_more_before = bool(page.get("has_more_before"))
            if returned_lines <= 0 or not has_more_before:
                break
            page_offset += returned_lines
            remaining -= returned_lines

        body = "\n".join(body_pages)
        return (header if not body else header + "\n" + body), has_more_before

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

    container_execution_log_header = _container_execution_log_header
    container_execution_log_chunk = _container_execution_log_chunk
    container_execution_log_page_chunk = _container_execution_log_page_chunk
    join_container_log_chunks = _join_container_log_chunks
    container_thread_execution_fallback_log = _container_thread_execution_fallback_log
    container_thread_transcript = _container_thread_transcript
    container_execution_thread_probe_text = _container_execution_thread_probe_text
