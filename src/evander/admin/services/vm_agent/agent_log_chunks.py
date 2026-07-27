from __future__ import annotations

import json

from evander.admin.services.vm_agent.common import (
    Any,
    Mapping,
)

_CONTAINER_EXECUTION_LOG_PAGE_FETCH_LIMIT = 5000
_ARTIFACT_EFFECT_PAYLOAD_BEGIN = "__EVANDER_ARTIFACT_EFFECT_JSON_BEGIN__"


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

    def _container_execution_log_page_payload(
        self,
        worker_backend: Any,
        execution: Any,
        *,
        limit: int,
        offset: int,
        query: str | None,
        mode: str,
    ) -> Mapping[str, object]:
        page_reader = getattr(worker_backend, "get_log_page", None)
        if callable(page_reader):
            try:
                page = page_reader(
                    execution.execution_id,
                    limit=max(1, min(int(limit), _CONTAINER_EXECUTION_LOG_PAGE_FETCH_LIMIT)),
                    offset=max(0, int(offset)),
                    query=query,
                    mode=mode,
                )
                if isinstance(page, Mapping):
                    return page
            except TypeError:
                pass
            except Exception as exc:
                return {
                    "text": f"[log unavailable: {exc}]\n",
                    "returned_lines": 1,
                    "has_more_before": False,
                    "log_unavailable": True,
                }
        log_unavailable = False
        try:
            snapshot_line_limit = max(1, int(limit)) + max(0, int(offset))
            text = worker_backend.get_log_snapshot(
                execution.execution_id,
                lines=snapshot_line_limit,
                mode=mode,
            )
        except Exception as exc:
            text = f"[log unavailable: {exc}]\n"
            log_unavailable = True
        lines = str(text or "").splitlines()
        normalized_query = str(query or "").strip()
        if normalized_query:
            needle = normalized_query.casefold()
            lines = [line for line in lines if needle in line.casefold()]
        end_index = max(0, len(lines) - max(0, int(offset)))
        start_index = max(0, end_index - max(1, int(limit)))
        selected = lines[start_index:end_index]
        return {
            "text": "\n".join(selected) + ("\n" if selected else ""),
            "returned_lines": len(selected),
            "total_lines": len(lines),
            "start_line": start_index + 1 if selected else 0,
            "end_line": end_index if selected else 0,
            "has_more_before": start_index > 0
            or (not normalized_query and len(lines) >= snapshot_line_limit and bool(selected)),
            "log_unavailable": log_unavailable,
        }

    def _container_legacy_artifact_metadata(
        self,
        worker_backend: Any,
        execution: Any,
    ) -> Mapping[str, Any] | None:
        marker_page = self._container_execution_log_page_payload(
            worker_backend,
            execution,
            limit=1,
            offset=0,
            query=_ARTIFACT_EFFECT_PAYLOAD_BEGIN,
            mode="execution",
        )
        if _ARTIFACT_EFFECT_PAYLOAD_BEGIN not in str(marker_page.get("text") or ""):
            return None
        payload_page = self._container_execution_log_page_payload(
            worker_backend,
            execution,
            limit=1,
            offset=0,
            query='"destination_path"',
            mode="execution",
        )
        for line in str(payload_page.get("text") or "").splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, Mapping):
                continue
            destination_path = str(payload.get("destination_path") or "").strip()
            if destination_path:
                return {
                    "operation_kind": "artifact_import",
                    "operation_label": "Import artifact",
                    "artifact_destination_path": destination_path,
                }
        return {
            "operation_kind": "artifact_operation",
            "operation_label": "Artifact operation",
        }

    def _container_artifact_event_line(
        self,
        execution: Any,
        metadata: Mapping[str, Any],
    ) -> str:
        operation_kind = str(metadata.get("operation_kind") or "artifact_operation").strip()
        default_labels = {
            "artifact_import": "Imported artifact",
            "artifact_publish": "Published artifact",
            "artifact_stage": "Staged artifact",
        }
        label = (
            str(metadata.get("operation_label") or "").strip()
            or default_labels.get(operation_kind)
            or "Artifact operation"
        )
        details: list[str] = []
        artifact_key = str(metadata.get("artifact_key") or "").strip()
        if artifact_key:
            details.append(f"key={artifact_key}")
        destination_path = str(metadata.get("artifact_destination_path") or "").strip()
        if destination_path:
            normalized_destination = destination_path.lstrip("/")
            details.append(f"destination=/artifacts/{normalized_destination}")
        source_path = str(metadata.get("artifact_source_path") or "").strip()
        if source_path:
            details.append(f"source={source_path}")
        status = self._container_execution_status(execution)
        if status == "destroyed":
            status = "completed"
        details.append(f"status={status}")
        started_at = getattr(execution, "started_at", None)
        if started_at is not None:
            details.append(f"at={started_at.isoformat()}")
        return "--- " + label + " · " + " · ".join(details) + " ---"

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
    container_execution_log_page_payload = _container_execution_log_page_payload
    container_legacy_artifact_metadata = _container_legacy_artifact_metadata
    container_artifact_event_line = _container_artifact_event_line
    join_container_log_chunks = _join_container_log_chunks
    container_thread_execution_fallback_log = _container_thread_execution_fallback_log
    container_thread_transcript = _container_thread_transcript
    container_execution_thread_probe_text = _container_execution_thread_probe_text
