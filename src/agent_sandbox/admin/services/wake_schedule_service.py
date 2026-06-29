from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from agent_sandbox.admin.services import session_helpers
from agent_sandbox.models import WakeScheduledRequest, WakeScheduleDefinition


@dataclass(frozen=True)
class WakeScheduleAdminDependencies:
    runtime: Any
    config: Any


class WakeScheduleAdminService:
    def __init__(self, dependencies: WakeScheduleAdminDependencies) -> None:
        self._deps = dependencies

    def list_wake_schedules(
        self,
        *,
        session_id: str | None = None,
        include_disabled: bool = True,
    ) -> list[dict[str, Any]]:
        requests = self._deps.runtime.wake_store.list_requests(
            session_id=session_id,
            include_disabled=include_disabled,
        )
        latest_dispatch_by_request_id = (
            self._deps.runtime.wake_store.latest_dispatch_by_request_id()
        )

        schedules: list[dict[str, Any]] = []
        for request in requests:
            dispatch = latest_dispatch_by_request_id.get(request.request_id)
            schedules.append(
                {
                    "request_id": request.request_id,
                    "session_id": request.session_id,
                    "resume_at": request.schedule.resume_at,
                    "cron": request.schedule.cron,
                    "prompt": request.resume_prompt.prompt,
                    "status": request.status,
                    "status_reason": request.status_reason,
                    "status_updated_at": request.status_updated_at,
                    "last_dispatch_id": request.last_dispatch_id,
                    "last_dispatch_status": request.last_dispatch_status,
                    "last_error": request.last_error,
                    "last_error_at": request.last_error_at,
                    "completed_at": request.completed_at,
                    "completion_evidence": dict(request.completion_evidence or {}),
                    "resume_mode": request.resume_mode,
                    "enabled": request.enabled,
                    "source": request.source,
                    "created_at": request.created_at,
                    "updated_at": request.updated_at,
                    "last_dispatched_at": request.last_dispatched_at,
                    "next_run_at": request.next_run_at,
                    "cancelled_at": request.cancelled_at,
                    "cancelled_by_session_id": request.cancelled_by_session_id,
                    "cancel_reason": request.cancel_reason,
                    "cancel_metadata": request.cancel_metadata,
                    "last_dispatch": (
                        {
                            "dispatch_id": dispatch.dispatch_id,
                            "status": dispatch.status,
                            "error": dispatch.error,
                            "dispatched_at": dispatch.dispatched_at,
                        }
                        if dispatch is not None
                        else None
                    ),
                }
            )
        schedules.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return schedules

    @staticmethod
    def normalize_wake_ledger_statuses(statuses: list[str] | None) -> list[str] | None:
        if statuses is None:
            return None
        allowed_statuses = {
            "pending",
            "due",
            "dispatching",
            "queued",
            "dispatched",
            "failed",
            "missed_or_late",
            "completed_or_acknowledged",
            "cancelled",
        }
        normalized_statuses: list[str] = []
        seen: set[str] = set()
        for status in statuses:
            normalized = str(status or "").strip().casefold()
            if not normalized:
                continue
            if normalized not in allowed_statuses:
                raise ValueError(f"Invalid wake ledger status: {status}")
            if normalized in seen:
                continue
            seen.add(normalized)
            normalized_statuses.append(normalized)
        return normalized_statuses or None

    def _wake_ledger_entry_for_request(self, request: WakeScheduledRequest) -> dict[str, Any]:
        return self._deps.runtime.wake_store.ledger_entry_for_request(request)

    def list_scheduled_wake_ledger(
        self,
        *,
        statuses: list[str] | None = None,
        session_id: str | None = None,
        limit: int = 100,
        include_completed: bool = True,
    ) -> list[dict[str, Any]]:
        normalized_statuses = self.normalize_wake_ledger_statuses(statuses)
        return self._deps.runtime.wake_store.list_ledger_entries(
            session_id=session_id,
            statuses=normalized_statuses,
            include_completed=include_completed,
            limit=limit,
        )

    def retry_scheduled_wake(
        self,
        request_id: str,
        *,
        resume_at: datetime | None = None,
        run_now: bool = True,
    ) -> dict[str, Any]:
        normalized_request_id = str(request_id or "").strip()
        if not normalized_request_id:
            raise ValueError("request_id cannot be empty")

        request = self._deps.runtime.wake_store.get_request(normalized_request_id)
        if request is None:
            raise ValueError(f"Wake schedule not found: {normalized_request_id}")

        current_entry = self._wake_ledger_entry_for_request(request)
        current_status = str(current_entry.get("status") or "").strip().casefold()
        if current_status not in {"failed", "missed_or_late", "due"}:
            raise ValueError(
                f"Wake schedule {normalized_request_id} is not retryable in status {current_status}."
            )

        if resume_at is not None and run_now:
            raise ValueError("Provide either resume_at or run_now=true, not both.")
        if resume_at is None and not run_now:
            raise ValueError("Either resume_at or run_now=true is required.")

        now = session_helpers.utc_now()
        validated_resume_at = None
        updated_schedule = request.schedule
        if resume_at is not None:
            updated_schedule = WakeScheduleDefinition(resume_at=resume_at)
            validated_resume_at = updated_schedule.resume_at
            next_run_at = validated_resume_at or now
        else:
            next_run_at = now
        self._enforce_wake_schedule_guardrails(
            session_id=request.session_id,
            schedule=updated_schedule,
            enabled=True,
            exclude_request_id=request.request_id,
        )

        metadata = dict(request.resume_prompt.metadata or {})
        existing_history = metadata.get("retry_history")
        retry_history = list(existing_history) if isinstance(existing_history, list) else []
        retry_history.append(
            {
                "retried_at": now,
                "previous_status": current_entry.get("status"),
                "previous_status_reason": current_entry.get("status_reason"),
                "previous_failure_reason": current_entry.get("failure_reason"),
                "previous_last_dispatch_id": current_entry.get("latest_dispatch_id"),
                "previous_last_dispatch_status": current_entry.get("latest_dispatch_status"),
                "previous_auto_dispatch_attempts": request.auto_dispatch_attempts,
                "previous_auto_retry_started_at": request.auto_retry_started_at,
                "previous_auto_retry_last_backoff_seconds": request.auto_retry_last_backoff_seconds,
                "previous_auto_retry_exhausted_at": request.auto_retry_exhausted_at,
                "requested_run_now": bool(run_now and resume_at is None),
                "requested_resume_at": validated_resume_at,
            }
        )
        metadata["retry_history"] = retry_history[-10:]
        request.resume_prompt.metadata = metadata
        request.schedule = updated_schedule
        request.enabled = True
        request.next_run_at = next_run_at
        request.status = "due" if next_run_at <= now else "pending"
        request.status_reason = None
        request.status_updated_at = now
        request.last_error = None
        request.last_error_at = None
        request.auto_dispatch_attempts = 0
        request.auto_retry_started_at = None
        request.auto_retry_last_backoff_seconds = None
        request.auto_retry_exhausted_at = None
        request.updated_at = now
        self._deps.runtime.wake_store.upsert_request(request)
        return self._wake_ledger_entry_for_request(request)

    def create_wake_schedule(
        self,
        *,
        session_id: str,
        prompt: str,
        resume_at: Any | None = None,
        cron: str | None = None,
        resume_mode: str = "same_context",
        enabled: bool = True,
        request_id: str | None = None,
        source: str | None = "admin_cli",
        metadata: dict[str, Any] | None = None,
    ) -> WakeScheduledRequest:
        normalized_prompt = str(prompt or "").strip()
        if not normalized_prompt:
            raise ValueError("prompt cannot be empty")
        normalized_resume_mode = session_helpers.normalize_resume_mode(resume_mode)
        normalized_source = str(source or "admin_cli").strip() or "admin_cli"
        if normalized_source.startswith("mcp") and normalized_resume_mode != "same_context":
            raise ValueError("MCP scheduling only supports resume_mode='same_context'.")
        normalized_cron = None
        if cron is not None and str(cron).strip():
            normalized_cron = session_helpers.require_valid_cron(str(cron))
        schedule = WakeScheduleDefinition(resume_at=resume_at, cron=normalized_cron)
        normalized_session_id = session_id.strip()
        self._enforce_wake_schedule_guardrails(
            session_id=normalized_session_id,
            schedule=schedule,
            enabled=bool(enabled),
        )
        return self._deps.runtime.wake_store.schedule_resume_request(
            session_id=normalized_session_id,
            prompt=normalized_prompt,
            schedule=schedule,
            request_id=request_id.strip()
            if isinstance(request_id, str) and request_id.strip()
            else None,
            source=normalized_source,
            resume_mode=normalized_resume_mode,
            enabled=bool(enabled),
            metadata=metadata or {},
        )

    def edit_wake_schedule(
        self,
        request_id: str,
        *,
        resume_at: Any | None = None,
        cron: str | None = None,
        prompt: str | None = None,
        resume_mode: str | None = None,
        enabled: bool | None = None,
    ) -> WakeScheduledRequest:
        existing = self._deps.runtime.wake_store.get_request(request_id.strip())
        if existing is None:
            raise ValueError(f"Wake schedule not found: {request_id}")
        if existing.status == "cancelled":
            raise ValueError(
                f"Wake schedule {existing.request_id} is cancelled and cannot be edited."
            )
        updated_prompt = existing.resume_prompt.prompt if prompt is None else str(prompt).strip()
        if not updated_prompt:
            raise ValueError("prompt cannot be empty")
        updated_mode = (
            existing.resume_mode
            if resume_mode is None
            else session_helpers.normalize_resume_mode(resume_mode)
        )
        if str(existing.source or "").strip().startswith("mcp") and updated_mode != "same_context":
            raise ValueError("MCP scheduling only supports resume_mode='same_context'.")
        resume_changed = resume_at is not None
        cron_changed = cron is not None
        if resume_changed and cron_changed:
            raise ValueError("Provide only one of resume_at or cron when editing a schedule.")
        if resume_changed:
            updated_schedule = WakeScheduleDefinition(resume_at=resume_at, cron=None)
        elif cron_changed:
            normalized_cron = (
                session_helpers.require_valid_cron(str(cron)) if str(cron).strip() else None
            )
            updated_schedule = WakeScheduleDefinition(resume_at=None, cron=normalized_cron)
        else:
            updated_schedule = existing.schedule

        updated_enabled = existing.enabled if enabled is None else bool(enabled)
        self._enforce_wake_schedule_guardrails(
            session_id=existing.session_id,
            schedule=updated_schedule,
            enabled=updated_enabled,
            exclude_request_id=existing.request_id,
        )
        existing.schedule = updated_schedule
        existing.resume_prompt.prompt = updated_prompt
        existing.resume_mode = updated_mode
        if enabled is not None:
            existing.enabled = bool(enabled)
        existing.updated_at = session_helpers.utc_now()
        if existing.schedule.resume_at is not None:
            existing.next_run_at = existing.schedule.resume_at
        elif cron_changed:
            existing.next_run_at = session_helpers.utc_now()
        self._deps.runtime.wake_store.upsert_request(existing)
        return existing

    def delete_wake_schedule(self, request_id: str) -> bool:
        normalized = request_id.strip()
        if not normalized:
            raise ValueError("request_id cannot be empty")
        return self._deps.runtime.wake_store.delete_request(normalized)

    def _active_wake_requests(
        self, *, exclude_request_id: str | None = None
    ) -> list[WakeScheduledRequest]:
        requests = self._deps.runtime.wake_store.list_requests(include_disabled=False)
        if not exclude_request_id:
            return requests
        return [request for request in requests if request.request_id != exclude_request_id]

    def _enforce_wake_schedule_guardrails(
        self,
        *,
        session_id: str,
        schedule: WakeScheduleDefinition,
        enabled: bool,
        exclude_request_id: str | None = None,
    ) -> None:
        if not enabled:
            return
        active_requests = self._active_wake_requests(exclude_request_id=exclude_request_id)
        max_active_per_session = self._deps.config.wake_schedule_max_active_per_session
        if max_active_per_session is not None:
            active_for_session = sum(
                1 for request in active_requests if request.session_id == session_id
            )
            if active_for_session >= max_active_per_session:
                raise ValueError(
                    "Wake schedule limit reached for session "
                    f"{session_id}: max_active_per_session={max_active_per_session}"
                )
        max_active_total = self._deps.config.wake_schedule_max_active_total
        if max_active_total is not None and len(active_requests) >= max_active_total:
            raise ValueError(f"Wake schedule limit reached: max_active_total={max_active_total}")
        min_interval_seconds = self._deps.config.wake_schedule_min_interval_seconds
        cron = str(schedule.cron or "").strip()
        if min_interval_seconds is not None and cron:
            actual_interval_seconds = session_helpers.cron_min_interval_seconds_for_schedule(cron)
            if actual_interval_seconds is None:
                raise ValueError(f"Unable to determine cron interval for schedule: {cron}")
            if actual_interval_seconds < min_interval_seconds:
                raise ValueError(
                    "Recurring wake schedule interval is too short: "
                    f"minimum={min_interval_seconds:g}s actual={actual_interval_seconds:g}s"
                )
