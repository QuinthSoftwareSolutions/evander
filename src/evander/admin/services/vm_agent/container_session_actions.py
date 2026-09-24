from __future__ import annotations

from evander.admin.services.vm_agent.common import (
    Any,
    ContainerSessionLifecycleState,
    ContainerWorkerClientError,
    DEFAULT_AGENT_ARTIFACTS_DIR,
    Mapping,
    SessionActionResult,
    SessionSummary,
    _VM_ARTIFACT_TEXT_READ_COMMAND,
    _prune_approvals_for_sessions,
    _revoke_container_git_write_access_for_session,
    _resolve_session_target,
    normalize_artifact_relative_path,
    sanitize_text,
)


class VmAgentContainerSessionActionMixin:
    def list_sessions(
        self,
        *,
        include_expired: bool = False,
        expired_only: bool = False,
        directory_path: str | None = None,
        direct_only: bool = False,
    ) -> list[SessionSummary]:
        summaries = self.operations_admin.list_sessions(
            include_expired=include_expired,
            expired_only=expired_only,
            directory_path=directory_path,
            direct_only=direct_only,
        )
        sessions = [summary for summary in summaries if self._is_container_session(summary)]
        records_by_session = self._container_lifecycle_records_by_session(
            [summary.session_id for summary in sessions]
        )
        return [
            self._session_summary_with_container_lifecycle(
                summary,
                executions=records_by_session.get(summary.session_id, []),
            )
            for summary in summaries
        ]

    def get_session_summary(self, session_id: str) -> SessionSummary:
        summary = self.operations_admin.get_session_summary(session_id)
        if not self._is_container_session(summary):
            return self._session_summary_with_container_lifecycle(summary)
        records = self._container_lifecycle_records_by_session([session_id])
        return self._session_summary_with_container_lifecycle(
            summary,
            executions=records.get(session_id, []),
        )

    def delete_session(self, target: str) -> SessionActionResult:
        sessions = {
            session.session_id: session
            for session in self.runtime.session_repository.list_sessions()
        }
        session_id, manifest = _resolve_session_target(target, sessions)
        associated_agent = self._session_associated_agent_description(session_id, manifest)
        if associated_agent:
            raise ValueError(
                f"Session {session_id!r} is associated with {associated_agent}. "
                "Delete the agent instead so backend cleanup can run."
            )

        git_write_access_revoked = _revoke_container_git_write_access_for_session(manifest)
        worker_session_deleted, removed_mappings = self._delete_detached_worker_session_state(
            session_id
        )
        managed_git = self._deps.managed_git_admin
        if managed_git is not None:
            managed_git.unbind_all("sessions", session_id)
            managed_git.unbind_all("authorized_sessions", session_id)
        result = self.operations_admin.delete_session(session_id)
        removed_approvals = _prune_approvals_for_sessions(self.runtime, session_ids={session_id})
        removed_assistant_mappings = self._delete_chat_assistant_session_mappings({session_id})
        cleanup_parts: list[str] = []
        if worker_session_deleted:
            cleanup_parts.append("worker_session_deleted=1")
        if removed_mappings:
            cleanup_parts.append(f"removed_mappings={removed_mappings}")
        if removed_approvals:
            cleanup_parts.append(f"removed_approvals={removed_approvals}")
        if removed_assistant_mappings:
            cleanup_parts.append(f"removed_assistant_mappings={removed_assistant_mappings}")
        if git_write_access_revoked:
            cleanup_parts.append("git_write_access_revoked=1")
        if cleanup_parts:
            result.message = f"{result.message}; " + " ".join(cleanup_parts)
        return result

    def read_session_artifact_text(
        self,
        *,
        session_id: str,
        path: str,
        vm_user: str = "agent",
    ) -> dict[str, Any]:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            raise ValueError("session_id is required")
        relative_path = normalize_artifact_relative_path(path)
        session = self.runtime.session_repository.get_session(normalized_session_id)
        if session is None:
            raise ValueError(f"Unknown session: {normalized_session_id}")

        metadata = session.metadata if isinstance(session.metadata, Mapping) else {}
        provider = str(
            metadata.get("execution_backend")
            or session.vm_provider
            or metadata.get("vm_provider")
            or ""
        ).strip()
        is_container_session = provider == "container_worker_vm"
        if is_container_session:
            worker_url = self._container_worker_url_for_target(session=session)
            backend = self.container_worker_backend(
                session_id=session.session_id,
                directory_path=session.directory_path,
                worker_url=worker_url,
            )
            content = backend.read_session_artifact_text(
                session_id=normalized_session_id,
                relative_path=relative_path,
            )
            return {
                "session_id": normalized_session_id,
                "path": relative_path,
                "content": content,
                "byte_count": len(content.encode("utf-8")),
                "artifacts_root": DEFAULT_AGENT_ARTIFACTS_DIR,
                "execution_backend": "container_worker_vm",
            }

        result = self.vm_exec(
            normalized_session_id,
            command=_VM_ARTIFACT_TEXT_READ_COMMAND,
            vm_user=(str(vm_user or "").strip() or "agent"),
            environment={"EVANDER_ARTIFACT_RELATIVE_PATH": relative_path},
            timeout_seconds=30.0,
            check=True,
        )
        if not bool(result.get("success")):
            raise RuntimeError(
                str(
                    result.get("stderr") or result.get("stdout") or result.get("message") or ""
                ).strip()
                or "VM artifact read failed"
            )
        content = str(result.get("stdout") or "")
        return {
            "session_id": normalized_session_id,
            "path": relative_path,
            "content": content,
            "byte_count": len(content.encode("utf-8")),
            "artifacts_root": DEFAULT_AGENT_ARTIFACTS_DIR,
            "execution_backend": "libvirt_vm",
            "vm_name": result.get("vm_name") or session.vm_id,
            "vm_provider": session.vm_provider,
        }

    def cleanup_container_session(self, session_id: str) -> SessionActionResult:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            raise ValueError("session_id is required")
        session = self.runtime.session_repository.get_session(normalized_session_id)
        if session is None:
            raise ValueError(f"Unknown session: {normalized_session_id}")
        if not self._is_container_session(session):
            raise ValueError(f"Session {normalized_session_id!r} is not a container worker session")

        lifecycle = self._container_session_lifecycle(session)
        if lifecycle is None or not lifecycle.actions.cleanup.supported:
            reason = (
                lifecycle.actions.cleanup.reason
                if lifecycle is not None and lifecycle.actions.cleanup.reason
                else "Cleanup is not currently supported for this session."
            )
            raise ValueError(reason)

        execution_records = self._container_execution_records_for_session(normalized_session_id)
        worker_backend = self.container_worker_backend(
            session_id=session.session_id,
            directory_path=session.directory_path,
            worker_url=self._container_worker_url_for_target(
                session=session,
                records=execution_records,
            ),
        )
        attempted_at = self._current_utc_now()
        try:
            worker_backend.delete_session(normalized_session_id)
        except ContainerWorkerClientError as exc:
            completed_at = self._current_utc_now()
            sanitized_error = sanitize_text(str(exc))
            updated_session = self._container_session_lifecycle_tombstone(
                session,
                state=ContainerSessionLifecycleState.CLEANUP_FAILED,
                cleanup_status="cleanup_failed",
                attempted_at=attempted_at,
                completed_at=completed_at,
                cleanup_error=sanitized_error,
            )
            self.runtime.session_repository.upsert_session(updated_session)
            return SessionActionResult(
                success=False,
                session_id=normalized_session_id,
                action="cleanup_container_session",
                message=(
                    f"Cleanup failed for container worker session {normalized_session_id}: "
                    f"{sanitized_error}"
                ),
            )

        completed_at = self._current_utc_now()
        updated_session = self._container_session_lifecycle_tombstone(
            session,
            state=ContainerSessionLifecycleState.CLEANED_UP,
            cleanup_status="cleanup_succeeded",
            attempted_at=attempted_at,
            completed_at=completed_at,
            cleanup_error=None,
        )
        self.runtime.session_repository.upsert_session(updated_session)
        return SessionActionResult(
            success=True,
            session_id=normalized_session_id,
            action="cleanup_container_session",
            message=f"Cleaned up container worker session {normalized_session_id}",
        )
