from __future__ import annotations


class VmAgentAdminLogSnapshotMixin:
    def get_admin_agent_log_snapshot(
        self,
        agent_id: str,
        *,
        backend: str | None = None,
        view: str | None = None,
        lines: int = 50,
        repo_name: str | None = None,
        vm_user: str = "agent",
    ) -> str:
        _ = self._normalize_agent_log_view(view)
        selected_backend = str(backend or "libvirt_vm").strip()
        if selected_backend in {"libvirt", "libvirt_vm"}:
            return self.get_agent_log_snapshot(
                agent_id,
                lines=lines,
                repo_name=repo_name,
                vm_user=vm_user,
            )
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
            if session is None or record is not None:
                try:
                    worker_backend.inspect(agent_id)
                except Exception:
                    pass
                return worker_backend.get_log_snapshot(agent_id, lines=lines, mode="execution")
            if not execution_records:
                return f"No container executions have run for session {session_id}.\n"
            if self._normalize_agent_log_view(view) == "threads":
                return self._join_container_log_chunks(
                    self._container_thread_log_chunks(
                        worker_backend,
                        execution_records,
                        lines=lines,
                    )
                )
            per_execution_lines = lines
            if lines > 0:
                per_execution_lines = max(1, lines // max(1, len(execution_records)))
            chunks: list[str] = []
            for execution in execution_records:
                header_parts = [
                    f"execution={execution.execution_id}",
                    f"status={self._container_execution_status(execution)}",
                    f"worker_job={execution.worker_job_id}",
                    "log=execution",
                ]
                if execution.started_at is not None:
                    header_parts.append(f"started={execution.started_at.isoformat()}")
                if execution.finished_at is not None:
                    header_parts.append(f"finished={execution.finished_at.isoformat()}")
                try:
                    text = worker_backend.get_log_snapshot(
                        execution.execution_id,
                        lines=per_execution_lines,
                        mode="execution",
                    ).rstrip()
                except Exception as exc:
                    text = f"[log unavailable: {exc}]"
                chunks.append("=== " + " ".join(header_parts) + " ===\n" + text)
            return "\n\n".join(chunks).rstrip() + "\n"
        raise ValueError(f"Unknown agent backend: {selected_backend}")
