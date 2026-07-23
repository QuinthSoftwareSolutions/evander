from __future__ import annotations

from evander.admin.services.vm_agent.common import (
    Any,
    _CONTAINER_THREAD_FULL_GROUP_RECORD_LIMIT,
    _log_page_from_end,
    _log_page_from_start,
    datetime,
)


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

    def _container_thread_window_chunks(
        self,
        worker_backend: Any,
        execution_records: list[Any],
        *,
        target_lines: int,
    ) -> tuple[list[str], bool]:
        sorted_records = sorted(execution_records, key=self._container_execution_sort_key)
        chunks: list[str] = []
        grouped_records: dict[str, list[Any]] = {}
        thread_chunk_indexes: dict[str, int] = {}
        dirty_thread_ids: set[str] = set()
        line_count = 0
        truncated_after = False
        for index, execution in enumerate(sorted_records):
            raw_text = None
            thread_id = self._container_execution_stored_thread_id(execution)
            if thread_id is None:
                raw_text = self._container_execution_thread_probe_text(worker_backend, execution)
                thread_id = self._container_log_thread_id(raw_text)
            if thread_id is not None:
                records = grouped_records.setdefault(thread_id, [])
                records.append(execution)
                chunk_index = thread_chunk_indexes.get(thread_id)
                if chunk_index is not None:
                    dirty_thread_ids.add(thread_id)
                    continue
                thread_chunk_indexes[thread_id] = len(chunks)
            else:
                records = [execution]
            chunk = self._container_thread_group_chunk(
                worker_backend,
                thread_id=thread_id,
                records=records,
                raw_text=raw_text,
            )
            chunks.append(chunk)
            line_count += len(chunk.splitlines()) + (1 if len(chunks) > 1 else 0)
            if line_count >= target_lines:
                truncated_after = index < len(sorted_records) - 1
                break
        for thread_id in dirty_thread_ids:
            chunk_index = thread_chunk_indexes.get(thread_id)
            if chunk_index is None:
                continue
            chunks[chunk_index] = self._container_thread_group_chunk(
                worker_backend,
                thread_id=thread_id,
                records=grouped_records[thread_id],
                raw_text=None,
            )
        return chunks, truncated_after

    def _container_thread_group_chunk(
        self,
        worker_backend: Any,
        *,
        thread_id: str | None,
        records: list[Any],
        raw_text: str | None,
    ) -> str:
        first_execution = records[0]
        header_parts = [
            f"thread={thread_id or 'unthreaded'}",
            f"first_execution={first_execution.execution_id}",
            f"executions={len(records)}",
        ]
        if first_execution.started_at is not None:
            header_parts.append(f"started={first_execution.started_at.isoformat()}")
        header = "=== " + " ".join(header_parts) + " ==="
        if thread_id is None:
            body = str(raw_text or "").rstrip()
            return header if not body else header + "\n" + body
        transcript = self._container_thread_transcript(
            worker_backend,
            records,
            lines=0,
        )
        return header if not transcript else header + "\n" + transcript

    def _container_thread_log_page(
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
            return _log_page_from_start(
                f"No container executions have run for session {session_id}.\n",
                limit=limit,
                offset=offset,
                query=query,
            )
        normalized_query = str(query or "").strip()
        chunks: list[str] = []
        target_lines = max(1, int(limit)) + max(0, int(offset)) + 1
        line_count = 0
        truncated_after = False
        if (
            not normalized_query
            and len(execution_records) > _CONTAINER_THREAD_FULL_GROUP_RECORD_LIMIT
        ):
            chunks, truncated_after = self._container_thread_window_chunks(
                worker_backend,
                execution_records,
                target_lines=target_lines,
            )
        else:
            groups = self._container_thread_groups(worker_backend, execution_records)
            for index, (_sort_key, thread_id, records, raw_text) in enumerate(groups):
                chunk = self._container_thread_group_chunk(
                    worker_backend,
                    thread_id=thread_id,
                    records=records,
                    raw_text=raw_text,
                )
                chunks.append(chunk)
                if not normalized_query:
                    line_count += len(chunk.splitlines()) + (1 if len(chunks) > 1 else 0)
                    if line_count >= target_lines:
                        truncated_after = index < len(groups) - 1
                        break
        page = _log_page_from_start(
            self._join_container_log_chunks(chunks),
            limit=limit,
            offset=offset,
            query=normalized_query or None,
        )
        if truncated_after:
            page["has_more_before"] = True
            page["total_lines"] = None
            page["end_line"] = None
        return page

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
        sorted_records = sorted(execution_records, key=self._container_execution_sort_key)

        chunks: list[str] = []
        truncated_before = False
        # A short auxiliary execution, such as an artifact import, must not hide the
        # preceding agent transcript. Read backward until the requested window is full.
        target_lines = max(1, limit) + max(0, offset) + 1
        line_count = 0
        if normalized_query:
            for execution in sorted_records:
                chunk = self._container_execution_log_chunk(
                    worker_backend,
                    execution,
                    lines=0,
                    mode="execution",
                )
                chunks.append(chunk)
        else:
            for _original_index, execution in reversed(list(enumerate(sorted_records))):
                separator_lines = 1 if chunks else 0
                body_lines = max(1, target_lines - line_count - separator_lines - 1)
                chunk, execution_has_more = self._container_execution_log_page_chunk(
                    worker_backend,
                    execution,
                    body_lines=body_lines,
                    mode="execution",
                )
                chunks.insert(0, chunk)
                line_count += len(chunk.splitlines()) + separator_lines
                if execution_has_more or line_count >= target_lines:
                    truncated_before = execution_has_more or _original_index > 0
                    break

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
    container_thread_window_chunks = _container_thread_window_chunks
    container_thread_group_chunk = _container_thread_group_chunk
    container_thread_log_page = _container_thread_log_page
    container_execution_log_page = _container_execution_log_page
