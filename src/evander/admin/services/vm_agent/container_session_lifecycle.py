from __future__ import annotations

from evander.admin.services.vm_agent.common import (
    Any,
    ContainerSessionLifecycle,
    ContainerSessionLifecycleAction,
    ContainerSessionLifecycleActions,
    ContainerSessionLifecycleState,
    SessionManifest,
    SessionSummary,
    _coerce_optional_datetime,
    _coerce_optional_string,
    datetime,
)


class VmAgentContainerSessionLifecycleMixin:
    @staticmethod
    def _container_lifecycle_action(
        *,
        supported: bool,
        kind: str | None,
        label: str,
        reason: str | None = None,
    ) -> ContainerSessionLifecycleAction:
        return ContainerSessionLifecycleAction(
            supported=supported,
            kind=kind,
            label=label,
            reason=reason,
        )

    @staticmethod
    def _container_session_lifecycle_metadata(session: SessionManifest) -> dict[str, Any]:
        metadata = dict(session.metadata) if isinstance(session.metadata, dict) else {}
        lifecycle = metadata.get("container_lifecycle")
        payload: dict[str, Any] = dict(lifecycle) if isinstance(lifecycle, dict) else {}
        for key in (
            "state",
            "cleanup_status",
            "cleanup_attempted_at",
            "cleanup_completed_at",
            "cleanup_error",
        ):
            if key in metadata and key not in payload:
                payload[key] = metadata[key]
        return payload

    @staticmethod
    def _container_record_lifecycle_metadata(record: Any) -> dict[str, Any]:
        metadata = dict(record.metadata) if isinstance(record.metadata, dict) else {}
        lifecycle = metadata.get("container_lifecycle")
        payload: dict[str, Any] = dict(lifecycle) if isinstance(lifecycle, dict) else {}
        for key in (
            "state",
            "cleanup_status",
            "cleanup_attempted_at",
            "cleanup_completed_at",
            "cleanup_error",
        ):
            if key in metadata and key not in payload:
                payload[key] = metadata[key]
        return payload

    @staticmethod
    def _container_session_lifecycle_tombstone(
        session: SessionManifest,
        *,
        state: ContainerSessionLifecycleState,
        cleanup_status: str,
        attempted_at: datetime,
        completed_at: datetime,
        cleanup_error: str | None,
    ) -> SessionManifest:
        metadata = dict(session.metadata) if isinstance(session.metadata, dict) else {}
        lifecycle = metadata.get("container_lifecycle")
        payload: dict[str, Any] = dict(lifecycle) if isinstance(lifecycle, dict) else {}
        payload.update(
            {
                "state": state.value,
                "cleanup_status": cleanup_status,
                "cleanup_attempted_at": attempted_at,
                "cleanup_completed_at": completed_at,
                "cleanup_error": cleanup_error,
            }
        )
        metadata["container_lifecycle"] = payload
        updated_expires_at = session.expires_at
        if state == ContainerSessionLifecycleState.CLEANED_UP and session.expires_at > completed_at:
            updated_expires_at = completed_at
        return session.model_copy(
            update={
                "metadata": metadata,
                "expires_at": updated_expires_at,
            }
        )

    @staticmethod
    def _container_execution_lifecycle_status(record: Any) -> str:
        normalized = str(getattr(record, "last_status", "") or "").strip().casefold()
        if normalized in {"created", "starting"}:
            return "starting"
        if normalized == "running":
            return "running"
        if normalized in {"stopped", "exited", "removed"}:
            return "stopped"
        if normalized in {"failed", "timeout", "timed_out"}:
            return "failed"
        return "unknown"

    def _session_summary_with_container_lifecycle(self, summary: SessionSummary) -> SessionSummary:
        if not self._is_container_session(summary):
            return summary.model_copy(update={"container_lifecycle": None})
        executions = self._container_execution_records_for_session(summary.session_id)
        lifecycle = self._container_session_lifecycle(summary, executions=executions)
        return summary.model_copy(update={"container_lifecycle": lifecycle})

    def _container_session_lifecycle(
        self,
        session: SessionManifest,
        *,
        executions: list[Any] | None = None,
    ) -> ContainerSessionLifecycle | None:
        if not self._is_container_session(session):
            return None
        execution_records = sorted(
            list(executions or self._container_execution_records_for_session(session.session_id)),
            key=self._container_execution_sort_key,
            reverse=True,
        )
        session_lifecycle_payload = self._container_session_lifecycle_metadata(session)
        record_lifecycle_payloads = [
            self._container_record_lifecycle_metadata(record) for record in execution_records
        ]
        metadata_payloads = [session_lifecycle_payload, *record_lifecycle_payloads]

        def _first_value(payloads: list[dict[str, Any]], *keys: str) -> Any:
            for payload in payloads:
                for key in keys:
                    if key not in payload:
                        continue
                    value = payload.get(key)
                    if value is None:
                        continue
                    if isinstance(value, str):
                        text = value.strip()
                        if not text:
                            continue
                        return text
                    return value
            return None

        def _max_datetime(*keys: str) -> datetime | None:
            candidates: list[datetime] = []
            for payload in metadata_payloads:
                for key in keys:
                    parsed = _coerce_optional_datetime(payload.get(key))
                    if parsed is not None:
                        candidates.append(parsed)
            if not candidates:
                return None
            return max(candidates)

        session_cleanup_status = (
            str(_first_value([session_lifecycle_payload], "cleanup_status", "state") or "")
            .strip()
            .casefold()
        )
        record_cleanup_status = (
            str(_first_value(record_lifecycle_payloads, "cleanup_status", "state") or "")
            .strip()
            .casefold()
        )
        cleanup_attempted_at = _max_datetime("cleanup_attempted_at")
        cleanup_completed_at = _max_datetime("cleanup_completed_at")
        cleanup_error = _coerce_optional_string(_first_value(metadata_payloads, "cleanup_error"))

        active_records = [
            record
            for record in execution_records
            if self._container_execution_lifecycle_status(record) in {"starting", "running"}
        ]
        if active_records:
            state = (
                ContainerSessionLifecycleState.RUNNING
                if any(
                    self._container_execution_lifecycle_status(record) == "running"
                    for record in active_records
                )
                else ContainerSessionLifecycleState.STARTING
            )
        elif session_cleanup_status in {
            "cleaned_up",
            "cleanup_succeeded",
            "succeeded",
            "success",
        }:
            state = ContainerSessionLifecycleState.CLEANED_UP
        elif (
            session_cleanup_status in {"cleanup_failed", "failed", "error"}
            or record_cleanup_status in {"cleanup_failed", "failed", "error"}
            or cleanup_error
        ):
            state = ContainerSessionLifecycleState.CLEANUP_FAILED
        else:
            if execution_records:
                latest_record = execution_records[0]
                latest_status = self._container_execution_lifecycle_status(latest_record)
                if latest_status == "failed":
                    state = ContainerSessionLifecycleState.FAILED
                elif latest_status == "stopped":
                    state = ContainerSessionLifecycleState.STOPPED
                elif session.expires_at <= self._current_utc_now():
                    state = ContainerSessionLifecycleState.ABANDONED
                else:
                    state = ContainerSessionLifecycleState.PREPARED
            elif session.expires_at <= self._current_utc_now():
                state = ContainerSessionLifecycleState.ABANDONED
            else:
                state = ContainerSessionLifecycleState.PREPARED

        last_heartbeat_at = None
        active_heartbeat_candidates: list[datetime] = []
        for record in execution_records:
            if self._container_execution_lifecycle_status(record) not in {"starting", "running"}:
                continue
            updated_at = getattr(record, "updated_at", None)
            if isinstance(updated_at, datetime):
                active_heartbeat_candidates.append(updated_at)
        if active_heartbeat_candidates:
            last_heartbeat_at = max(active_heartbeat_candidates)

        completion_candidates: list[datetime] = []
        for record in execution_records:
            finished_at = getattr(record, "finished_at", None)
            if isinstance(finished_at, datetime):
                completion_candidates.append(finished_at)
        last_completion_at = max(completion_candidates) if completion_candidates else None
        last_signal_at = max(
            [
                timestamp
                for timestamp in (last_heartbeat_at, last_completion_at)
                if timestamp is not None
            ],
            default=None,
        )

        cleanup_supported = state in {
            ContainerSessionLifecycleState.STOPPED,
            ContainerSessionLifecycleState.FAILED,
            ContainerSessionLifecycleState.ABANDONED,
            ContainerSessionLifecycleState.CLEANUP_FAILED,
        }
        if cleanup_supported:
            cleanup_reason = None
        elif state == ContainerSessionLifecycleState.CLEANED_UP:
            cleanup_reason = "Session has already been cleaned up."
        elif state in {
            ContainerSessionLifecycleState.RUNNING,
            ContainerSessionLifecycleState.STARTING,
        }:
            cleanup_reason = "Session still has active executions."
        else:
            cleanup_reason = "Cleanup is not currently supported for this session."

        recovery_supported = (
            state
            not in {
                ContainerSessionLifecycleState.RUNNING,
                ContainerSessionLifecycleState.STARTING,
                ContainerSessionLifecycleState.CLEANED_UP,
            }
            and session.expires_at <= self._current_utc_now()
        )
        if recovery_supported:
            recovery_reason = None
        elif state == ContainerSessionLifecycleState.CLEANED_UP:
            recovery_reason = "Session has already been cleaned up."
        elif state in {
            ContainerSessionLifecycleState.RUNNING,
            ContainerSessionLifecycleState.STARTING,
        }:
            recovery_reason = "Session still has active executions."
        else:
            recovery_reason = "Session is not expired."

        return ContainerSessionLifecycle(
            state=state,
            last_heartbeat_at=last_heartbeat_at,
            last_completion_at=last_completion_at,
            last_signal_at=last_signal_at,
            cleanup_attempted_at=cleanup_attempted_at,
            cleanup_completed_at=cleanup_completed_at,
            cleanup_error=cleanup_error,
            actions=ContainerSessionLifecycleActions(
                cleanup=self._container_lifecycle_action(
                    supported=cleanup_supported,
                    kind="cleanup_container_session",
                    label="Cleanup session",
                    reason=cleanup_reason,
                ),
                recovery=self._container_lifecycle_action(
                    supported=recovery_supported,
                    kind="renew_session",
                    label="Renew session",
                    reason=recovery_reason,
                ),
            ),
        )

    container_lifecycle_action = _container_lifecycle_action
    container_session_lifecycle_metadata = _container_session_lifecycle_metadata
    container_record_lifecycle_metadata = _container_record_lifecycle_metadata
    container_session_lifecycle_tombstone = _container_session_lifecycle_tombstone
    container_execution_lifecycle_status = _container_execution_lifecycle_status
    session_summary_with_container_lifecycle = _session_summary_with_container_lifecycle
    container_session_lifecycle = _container_session_lifecycle
