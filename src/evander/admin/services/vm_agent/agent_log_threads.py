from __future__ import annotations

import base64
import binascii
import json

from evander.admin.services.vm_agent.common import (
    Any,
    Mapping,
    _log_page_from_end,
    _log_page_from_start,
    datetime,
)

_CONTAINER_THREAD_GROUP_WINDOW = 10
_CONTAINER_THREAD_FALLBACK_EXECUTION_WINDOW = 25
_CONTAINER_THREAD_RECORD_SCAN_WINDOW = 10


class VmAgentThreadLogMixin:
    def _container_thread_log_chunks(
        self,
        worker_backend: Any,
        execution_records: list[Any],
        *,
        lines: int,
    ) -> list[str]:
        sorted_records = sorted(execution_records, key=self._container_execution_sort_key)
        grouped_records: dict[str, list[Any]] = {}
        unthreaded_chunks: list[tuple[tuple[datetime, str], str]] = []
        for execution in sorted_records:
            raw_text = ""
            thread_id = self._container_execution_stored_thread_id(execution)
            if thread_id is None:
                try:
                    raw_text = self._container_execution_thread_probe_text(
                        worker_backend, execution
                    )
                except Exception as exc:
                    raw_text = f"[log unavailable: {exc}]"
                thread_id = self._container_log_thread_id(raw_text)
            if thread_id is None:
                header_parts = [
                    "thread=unthreaded",
                    f"first_execution={execution.execution_id}",
                    "executions=1",
                ]
                if execution.started_at is not None:
                    header_parts.append(f"started={execution.started_at.isoformat()}")
                header = "=== " + " ".join(header_parts) + " ==="
                body = raw_text.rstrip()
                unthreaded_chunks.append(
                    (
                        self._container_execution_sort_key(execution),
                        header if not body else header + "\n" + body,
                    )
                )
                continue
            grouped_records.setdefault(thread_id, []).append(execution)

        threaded_chunks: list[tuple[tuple[datetime, str], str]] = []
        for thread_id, records in grouped_records.items():
            ordered_records = sorted(records, key=self._container_execution_sort_key)
            first_execution = ordered_records[0]
            header_parts = [
                f"thread={thread_id}",
                f"first_execution={first_execution.execution_id}",
                f"executions={len(ordered_records)}",
            ]
            if first_execution.started_at is not None:
                header_parts.append(f"started={first_execution.started_at.isoformat()}")
            header = "=== " + " ".join(header_parts) + " ==="
            transcript = self._container_thread_transcript(
                worker_backend,
                ordered_records,
                lines=lines,
            )
            threaded_chunks.append(
                (
                    self._container_execution_sort_key(first_execution),
                    header if not transcript else header + "\n" + transcript,
                )
            )

        return [
            chunk
            for _, chunk in sorted(
                [*threaded_chunks, *unthreaded_chunks],
                key=lambda item: item[0],
            )
        ]

    def _container_thread_groups(
        self,
        worker_backend: Any,
        execution_records: list[Any],
    ) -> list[tuple[tuple[datetime, str], str | None, list[Any], str | None]]:
        sorted_records = sorted(execution_records, key=self._container_execution_sort_key)
        grouped_records: dict[str, list[Any]] = {}
        unthreaded_groups: list[tuple[tuple[datetime, str], str | None, list[Any], str | None]] = []
        for execution in sorted_records:
            raw_text = None
            thread_id = self._container_execution_stored_thread_id(execution)
            if thread_id is None:
                raw_text = self._container_execution_thread_probe_text(worker_backend, execution)
                thread_id = self._container_log_thread_id(raw_text)
            if thread_id is None:
                if not self._container_execution_is_agent_run(execution):
                    continue
                unthreaded_groups.append(
                    (
                        self._container_execution_sort_key(execution),
                        None,
                        [execution],
                        raw_text,
                    )
                )
                continue
            grouped_records.setdefault(thread_id, []).append(execution)

        threaded_groups: list[tuple[tuple[datetime, str], str | None, list[Any], str | None]] = []
        for thread_id, records in grouped_records.items():
            ordered_records = sorted(records, key=self._container_execution_sort_key)
            threaded_groups.append(
                (
                    self._container_execution_sort_key(ordered_records[0]),
                    thread_id,
                    ordered_records,
                    None,
                )
            )
        return sorted([*threaded_groups, *unthreaded_groups], key=lambda item: item[0])

    @staticmethod
    def _container_thread_cursor_state(cursor: str | None) -> tuple[int, int, int, str | None]:
        normalized = str(cursor or "").strip()
        if not normalized:
            return 0, 0, 0, None
        try:
            padding = "=" * (-len(normalized) % 4)
            payload = json.loads(
                base64.urlsafe_b64decode((normalized + padding).encode("ascii")).decode("utf-8")
            )
            if not isinstance(payload, dict) or int(payload.get("v") or 0) != 1:
                raise ValueError
            return (
                max(0, int(payload.get("record") or 0)),
                max(0, int(payload.get("group") or 0)),
                max(0, int(payload.get("line") or 0)),
                str(payload.get("skip_thread") or "").strip() or None,
            )
        except (
            binascii.Error,
            UnicodeDecodeError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
        ) as exc:
            raise ValueError("Invalid agent thread log cursor") from exc

    @staticmethod
    def _container_thread_cursor(
        record_offset: int,
        group_offset: int,
        line_offset: int,
        *,
        skip_thread_id: str | None = None,
    ) -> str:
        payload = json.dumps(
            {
                "v": 1,
                "record": max(0, record_offset),
                "group": max(0, group_offset),
                "line": max(0, line_offset),
                **({"skip_thread": skip_thread_id} if skip_thread_id else {}),
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    def _container_recent_thread_groups(
        self,
        worker_backend: Any,
        execution_records: list[Any],
        *,
        record_offset: int,
        max_groups: int,
        skip_thread_id: str | None,
    ) -> tuple[list[tuple[str | None, list[Any], str | None]], int, bool]:
        sorted_records = sorted(
            execution_records,
            key=self._container_execution_sort_key,
            reverse=True,
        )
        record_window = sorted_records[
            max(0, record_offset) : max(0, record_offset) + _CONTAINER_THREAD_RECORD_SCAN_WINDOW
        ]
        groups: list[tuple[str | None, list[Any], str | None]] = []
        grouped_records: dict[str, list[Any]] = {}
        group_indexes: dict[str, int] = {}
        leading_thread = skip_thread_id
        for index, execution in enumerate(record_window):
            is_agent_run = self._container_execution_is_agent_run(execution)
            if self._container_execution_artifact_metadata(execution) is not None:
                continue
            metadata = getattr(execution, "metadata", None)
            if (
                not is_agent_run
                and isinstance(metadata, Mapping)
                and str(metadata.get("effect_type") or "").strip()
            ):
                continue
            raw_text = None
            thread_id = self._container_execution_stored_thread_id(execution)
            if thread_id is None:
                raw_text = self._container_execution_thread_probe_text(worker_backend, execution)
                thread_id = self._container_log_thread_id(raw_text)
            if thread_id is None and not is_agent_run:
                continue
            if leading_thread is not None and thread_id == leading_thread and not groups:
                continue
            leading_thread = None
            group_key = thread_id or f"unthreaded:{execution.execution_id}"
            group_index = group_indexes.get(group_key)
            if group_index is not None:
                grouped_records[group_key].append(execution)
                continue
            if len(groups) >= max(1, max_groups):
                return groups, index, True
            records = [execution]
            grouped_records[group_key] = records
            group_indexes[group_key] = len(groups)
            groups.append((thread_id, records, raw_text))
        records_consumed = len(record_window)
        has_more_records = max(0, record_offset) + records_consumed < len(sorted_records)
        if groups and has_more_records:
            for execution in sorted_records[max(0, record_offset) + records_consumed :]:
                stored_thread_id = self._container_execution_stored_thread_id(execution)
                if stored_thread_id is None:
                    continue
                group_index = group_indexes.get(stored_thread_id)
                if group_index is not None:
                    grouped_records[stored_thread_id].append(execution)
        return groups, records_consumed, has_more_records

    def _container_thread_group_chunk(
        self,
        worker_backend: Any,
        *,
        thread_id: str | None,
        records: list[Any],
        raw_text: str | None,
    ) -> str:
        ordered_records = sorted(records, key=self._container_execution_sort_key)
        first_execution = ordered_records[0]
        header_parts = [
            f"thread={thread_id or 'unthreaded'}",
            f"first_execution={first_execution.execution_id}",
            f"executions={len(ordered_records)}",
        ]
        if first_execution.started_at is not None:
            header_parts.append(f"started={first_execution.started_at.isoformat()}")
        header = "=== " + " ".join(header_parts) + " ==="
        if thread_id is None:
            body = str(raw_text or "").rstrip()
            return header if not body else header + "\n" + body
        transcript = self._container_thread_transcript(
            worker_backend,
            ordered_records,
            lines=0,
        )
        return header if not transcript else header + "\n" + transcript

    def _container_thread_page_chunk(
        self,
        worker_backend: Any,
        *,
        thread_id: str | None,
        records: list[Any],
        raw_text: str | None,
        limit: int,
        offset: int,
    ) -> tuple[str, int, bool]:
        ordered_records = sorted(records, key=self._container_execution_sort_key)
        first_execution = ordered_records[0]
        header_parts = [
            f"thread={thread_id or 'unthreaded'}",
            f"first_execution={first_execution.execution_id}",
            f"executions={len(ordered_records)}",
        ]
        if first_execution.started_at is not None:
            header_parts.append(f"started={first_execution.started_at.isoformat()}")
        if offset > 0:
            header_parts.append("continuation=older")
        header = "=== " + " ".join(header_parts) + " ==="
        if thread_id is None:
            page = self._container_execution_log_page_payload(
                worker_backend,
                first_execution,
                limit=max(1, limit),
                offset=offset,
                query=None,
                mode="execution",
            )
        else:
            page = self._container_execution_log_page_payload(
                worker_backend,
                first_execution,
                limit=max(1, limit),
                offset=offset,
                query=None,
                mode="session",
            )
            if not str(page.get("text") or "").strip():
                page = self._container_thread_fallback_page(
                    worker_backend,
                    ordered_records,
                    limit=limit,
                    offset=offset,
                )
        body = self._container_log_without_session_id_lines(
            str(page.get("text") or raw_text or "")
        ).rstrip()
        consumed_lines = int(page.get("returned_lines") or len(body.splitlines()))
        return (
            header if not body else header + "\n" + body,
            consumed_lines,
            bool(page.get("has_more_before")),
        )

    def _container_thread_fallback_page(
        self,
        worker_backend: Any,
        records: list[Any],
        *,
        limit: int,
        offset: int,
    ) -> Mapping[str, object]:
        target_lines = max(1, limit) + max(0, offset)
        chunks: list[str] = []
        used_lines = 0
        truncated_before = False
        ordered_records = sorted(records, key=self._container_execution_sort_key)
        recent_records = ordered_records[-_CONTAINER_THREAD_FALLBACK_EXECUTION_WINDOW:]
        if len(recent_records) < len(ordered_records):
            truncated_before = True
        for index, execution in reversed(list(enumerate(recent_records))):
            remaining = max(1, target_lines - used_lines)
            execution_page = self._container_execution_log_page_payload(
                worker_backend,
                execution,
                limit=remaining,
                offset=0,
                query=None,
                mode="execution",
            )
            body = self._container_log_without_session_id_lines(
                str(execution_page.get("text") or "")
            ).rstrip()
            if body:
                chunks.insert(0, body)
                used_lines += len(body.splitlines()) + (1 if len(chunks) > 1 else 0)
            if bool(execution_page.get("has_more_before")) or used_lines >= target_lines:
                truncated_before = bool(execution_page.get("has_more_before")) or index > 0
                break
        page = _log_page_from_end(
            self._join_container_log_chunks(chunks),
            limit=limit,
            offset=offset,
        )
        if truncated_before:
            page["has_more_before"] = True
            page["total_lines"] = None
            page["start_line"] = None
            page["end_line"] = None
        return page

    def _container_lazy_thread_log_page(
        self,
        *,
        session_id: str,
        execution_records: list[Any],
        worker_backend: Any,
        limit: int,
        cursor: str | None,
    ) -> dict[str, object]:
        if not execution_records:
            page = _log_page_from_end(
                f"No agent executions have run for session {session_id}.\n",
                limit=limit,
                offset=0,
            )
            page["next_cursor"] = None
            return page
        record_offset, group_offset, line_offset, skip_thread_id = (
            self._container_thread_cursor_state(cursor)
        )
        groups, records_consumed, records_truncated = self._container_recent_thread_groups(
            worker_backend,
            execution_records,
            record_offset=record_offset,
            max_groups=_CONTAINER_THREAD_GROUP_WINDOW,
            skip_thread_id=skip_thread_id,
        )
        if group_offset >= len(groups):
            next_cursor = (
                self._container_thread_cursor(
                    record_offset + records_consumed,
                    0,
                    0,
                    skip_thread_id=skip_thread_id,
                )
                if records_truncated and records_consumed > 0
                else None
            )
            return {
                "text": "",
                "limit": limit,
                "offset": 0,
                "returned_lines": 0,
                "total_lines": None,
                "start_line": None,
                "end_line": None,
                "has_more_before": next_cursor is not None,
                "query": None,
                "next_cursor": next_cursor,
            }

        chunks_newest_first: list[str] = []
        used_lines = 0
        next_group_offset = group_offset
        next_line_offset = line_offset
        next_cursor: str | None = None
        while next_group_offset < len(groups) and used_lines < max(1, limit):
            thread_id, records, raw_text = groups[next_group_offset]
            separator_lines = 1 if chunks_newest_first else 0
            body_limit = max(1, max(1, limit) - used_lines - separator_lines - 1)
            chunk, consumed_lines, has_more_in_thread = self._container_thread_page_chunk(
                worker_backend,
                thread_id=thread_id,
                records=records,
                raw_text=raw_text,
                limit=body_limit,
                offset=next_line_offset,
            )
            chunks_newest_first.append(chunk)
            used_lines += len(chunk.splitlines()) + separator_lines
            if has_more_in_thread and consumed_lines > 0:
                next_cursor = self._container_thread_cursor(
                    record_offset,
                    next_group_offset,
                    next_line_offset + consumed_lines,
                )
                break
            next_group_offset += 1
            next_line_offset = 0
            if next_group_offset < len(groups):
                next_cursor = self._container_thread_cursor(
                    record_offset,
                    next_group_offset,
                    0,
                )
            elif records_truncated:
                next_cursor = self._container_thread_cursor(
                    record_offset + records_consumed,
                    0,
                    0,
                    skip_thread_id=thread_id,
                )
            else:
                next_cursor = None

        text = self._join_container_log_chunks(list(reversed(chunks_newest_first)))
        returned_lines = len(text.splitlines())
        return {
            "text": text,
            "limit": max(1, limit),
            "offset": 0,
            "returned_lines": returned_lines,
            "total_lines": None,
            "start_line": None,
            "end_line": None,
            "has_more_before": next_cursor is not None,
            "query": None,
            "next_cursor": next_cursor,
        }

    def _container_thread_log_page(
        self,
        *,
        session_id: str,
        execution_records: list[Any],
        worker_backend: Any,
        limit: int,
        offset: int,
        query: str | None,
        cursor: str | None = None,
    ) -> dict[str, object]:
        if not execution_records:
            return _log_page_from_start(
                f"No container executions have run for session {session_id}.\n",
                limit=limit,
                offset=offset,
                query=query,
            )
        normalized_query = str(query or "").strip()
        if not normalized_query:
            return self._container_lazy_thread_log_page(
                session_id=session_id,
                execution_records=execution_records,
                worker_backend=worker_backend,
                limit=limit,
                cursor=cursor,
            )
        chunks: list[str] = []
        groups = self._container_thread_groups(worker_backend, execution_records)
        for _sort_key, thread_id, records, raw_text in groups:
            chunk = self._container_thread_group_chunk(
                worker_backend,
                thread_id=thread_id,
                records=records,
                raw_text=raw_text,
            )
            chunks.append(chunk)
        page = _log_page_from_end(
            self._join_container_log_chunks(chunks),
            limit=limit,
            offset=offset,
            query=normalized_query or None,
        )
        page["next_cursor"] = None
        return page

    def _container_latest_agent_run(
        self,
        worker_backend: Any,
        execution_records: list[Any],
    ) -> tuple[Any | None, list[tuple[Any, Mapping[str, Any]]]]:
        sorted_records = sorted(execution_records, key=self._container_execution_sort_key)
        agent_execution = next(
            (
                execution
                for execution in reversed(sorted_records)
                if self._container_execution_is_agent_run(execution)
            ),
            None,
        )
        artifact_metadata_by_execution: dict[str, Mapping[str, Any]] = {}
        fallback_execution = None
        if agent_execution is None:
            for execution in reversed(sorted_records):
                metadata = self._container_execution_artifact_metadata(execution)
                if metadata is None:
                    metadata = self._container_legacy_artifact_metadata(
                        worker_backend,
                        execution,
                    )
                if metadata is not None:
                    artifact_metadata_by_execution[execution.execution_id] = metadata
                    continue
                thread_id = self._container_execution_stored_thread_id(execution)
                if thread_id is None:
                    raw_text = self._container_execution_thread_probe_text(
                        worker_backend, execution
                    )
                    thread_id = self._container_log_thread_id(raw_text)
                if thread_id is not None:
                    agent_execution = execution
                    break
                if fallback_execution is None:
                    fallback_execution = execution
            agent_execution = agent_execution or fallback_execution
        if agent_execution is None:
            return None, []

        artifact_events: list[tuple[Any, Mapping[str, Any]]] = []
        agent_sort_key = self._container_execution_sort_key(agent_execution)
        previous_agent_sort_key = next(
            (
                self._container_execution_sort_key(execution)
                for execution in reversed(sorted_records)
                if execution is not agent_execution
                and self._container_execution_is_agent_run(execution)
                and self._container_execution_sort_key(execution) < agent_sort_key
            ),
            None,
        )
        for execution in sorted_records:
            if execution is agent_execution:
                continue
            execution_sort_key = self._container_execution_sort_key(execution)
            if (
                previous_agent_sort_key is not None
                and execution_sort_key <= previous_agent_sort_key
            ):
                continue
            metadata = artifact_metadata_by_execution.get(execution.execution_id)
            if metadata is None:
                metadata = self._container_execution_artifact_metadata(execution)
            if metadata is None and not self._container_execution_is_agent_run(execution):
                metadata = self._container_legacy_artifact_metadata(
                    worker_backend,
                    execution,
                )
            if (
                metadata is not None
                and execution_sort_key < agent_sort_key
                and str(metadata.get("operation_kind") or "") != "artifact_import"
            ):
                continue
            if metadata is not None:
                artifact_events.append((execution, metadata))
        return agent_execution, artifact_events

    def _container_execution_log_page(
        self,
        *,
        session_id: str,
        execution_records: list[Any],
        worker_backend: Any,
        limit: int,
        offset: int,
        query: str | None,
    ) -> dict[str, object]:
        if not execution_records:
            return _log_page_from_end(
                f"No container executions have run for session {session_id}.\n",
                limit=limit,
                offset=offset,
                query=query,
            )

        normalized_query = str(query or "").strip()
        agent_execution, artifact_events = self._container_latest_agent_run(
            worker_backend,
            execution_records,
        )
        if agent_execution is None:
            return _log_page_from_end(
                f"No agent executions have run for session {session_id}.\n",
                limit=limit,
                offset=offset,
                query=query,
            )
        agent_sort_key = self._container_execution_sort_key(agent_execution)
        before_event_lines = [
            self._container_artifact_event_line(execution, metadata)
            for execution, metadata in artifact_events
            if self._container_execution_sort_key(execution) < agent_sort_key
        ]
        after_event_lines = [
            self._container_artifact_event_line(execution, metadata)
            for execution, metadata in artifact_events
            if self._container_execution_sort_key(execution) >= agent_sort_key
        ]
        target_lines = max(1, limit) + max(0, offset) + 1
        if normalized_query:
            payload = self._container_execution_log_page_payload(
                worker_backend,
                agent_execution,
                limit=target_lines,
                offset=0,
                query=normalized_query,
                mode="execution",
            )
            header = self._container_execution_log_header(
                agent_execution,
                mode="execution",
            )
            body = str(payload.get("text") or "").rstrip()
            run_lines = [
                header,
                *before_event_lines,
                *([body] if body else []),
                *after_event_lines,
            ]
            chunks = ["\n".join(run_lines)]
            truncated_before = bool(payload.get("has_more_before"))
        else:
            body_lines = max(1, target_lines - len(after_event_lines) - 1)
            chunk, truncated_before = self._container_execution_log_page_chunk(
                worker_backend,
                agent_execution,
                body_lines=body_lines,
                mode="execution",
            )
            chunk_lines = chunk.splitlines()
            visible_before_event_lines = [] if truncated_before else before_event_lines
            chunks = [
                "\n".join(
                    [
                        chunk_lines[0],
                        *visible_before_event_lines,
                        *chunk_lines[1:],
                        *after_event_lines,
                    ]
                )
            ]

        page = _log_page_from_end(
            self._join_container_log_chunks(chunks),
            limit=limit,
            offset=offset,
            query=normalized_query or None,
        )
        if truncated_before:
            page["has_more_before"] = True
            page["total_lines"] = None
            page["start_line"] = None
            page["end_line"] = None
        return page

    def get_admin_agent_log_page(
        self,
        agent_id: str,
        *,
        backend: str | None = None,
        view: str | None = None,
        limit: int = 200,
        offset: int = 0,
        query: str | None = None,
        cursor: str | None = None,
        repo_name: str | None = None,
        vm_user: str = "agent",
    ) -> dict[str, object]:
        normalized_limit = max(1, min(int(limit), 5000))
        normalized_offset = max(0, int(offset))
        normalized_query = str(query or "").strip()
        normalized_view = self._normalize_agent_log_view(view)
        selected_backend = str(backend or "libvirt_vm").strip()
        if selected_backend in {"libvirt", "libvirt_vm"}:
            window_lines = normalized_limit + normalized_offset
            if normalized_query:
                window_lines = max(window_lines, 5000)
            text = self.get_agent_log_snapshot(
                agent_id,
                lines=window_lines,
                repo_name=repo_name,
                vm_user=vm_user,
            )
            page = _log_page_from_end(
                text,
                limit=normalized_limit,
                offset=normalized_offset,
                query=normalized_query or None,
            )
            if not normalized_query and page["returned_lines"] >= normalized_limit:
                page["has_more_before"] = True
                page["total_lines"] = None
                page["start_line"] = None
                page["end_line"] = None
            page["view"] = normalized_view
            return page
        if selected_backend == "container_worker_vm":
            session, record = self._resolve_container_agent_session(agent_id, require_session=False)
            session_id = (
                str(session.session_id).strip()
                if session is not None
                else str(getattr(record, "session_id", "") or "").strip()
            )
            execution_records = (
                self._container_execution_records_for_session(session_id) if session_id else []
            )
            worker_backend = self.container_worker_backend(
                directory_path=(
                    session.directory_path
                    if session is not None
                    else getattr(record, "directory_path", None)
                ),
                worker_url=self._container_worker_url_for_target(
                    session=session,
                    record=record,
                    records=execution_records,
                ),
            )
            target = agent_id
            if session is not None and record is None:
                if normalized_view == "threads":
                    page = self._container_thread_log_page(
                        session_id=session_id,
                        execution_records=execution_records,
                        worker_backend=worker_backend,
                        limit=normalized_limit,
                        offset=normalized_offset,
                        query=normalized_query or None,
                        cursor=cursor,
                    )
                else:
                    page = self._container_execution_log_page(
                        session_id=session_id,
                        execution_records=execution_records,
                        worker_backend=worker_backend,
                        limit=normalized_limit,
                        offset=normalized_offset,
                        query=normalized_query or None,
                    )
                page["view"] = normalized_view
                return page
            page_reader = getattr(worker_backend, "get_log_page", None)
            if callable(page_reader):
                page = page_reader(
                    target,
                    limit=normalized_limit,
                    offset=normalized_offset,
                    query=normalized_query or None,
                    mode="execution",
                )
                page["view"] = normalized_view
                return page
            text = worker_backend.get_log_snapshot(
                target, lines=normalized_limit + normalized_offset, mode="execution"
            )
            page = _log_page_from_end(
                text,
                limit=normalized_limit,
                offset=normalized_offset,
                query=normalized_query or None,
            )
            page["view"] = normalized_view
            return page
        raise ValueError(f"Unknown agent backend: {selected_backend}")

    container_thread_log_chunks = _container_thread_log_chunks
    container_thread_groups = _container_thread_groups
    container_recent_thread_groups = _container_recent_thread_groups
    container_thread_group_chunk = _container_thread_group_chunk
    container_lazy_thread_log_page = _container_lazy_thread_log_page
    container_thread_log_page = _container_thread_log_page
    container_execution_log_page = _container_execution_log_page
