from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agent_sandbox.agent_control import AgentControlStore
from agent_sandbox.bootstrap import load_runtime_gateway_config
from agent_sandbox.config import GatewayConfig, ScheduledWakeWorkerGlobalConfig
from agent_sandbox.cron import next_cron_time
from agent_sandbox.models import WakeScheduledRequest
from agent_sandbox.orchestration.host_polling import HostFileLockManager, HostSleeper
from agent_sandbox.orchestration.kernels import PollingKernelConfig, PollingOrchestratorKernel
from agent_sandbox.runtime import RuntimeContext, build_runtime
from agent_sandbox.workflow.runtime_config import _file_fingerprint
from agent_sandbox.messaging.wake_dispatch import (
    HostWakeDispatcher,
    SessionWakeBinding,
    compute_next_sleep_seconds,
    directory_is_within_scope,
    is_session_in_cooldown,
    probe_agent_dispatch_state,
    set_session_cooldown,
)
from agent_sandbox.session_backends import session_is_container_worker_vm

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScheduledWakeCycleResult:
    requests_due: int
    wakes_dispatched: int
    next_sleep_seconds: float


class ScheduledWakeWorker:
    def __init__(
        self,
        *,
        config: GatewayConfig,
        worker_config: ScheduledWakeWorkerGlobalConfig,
        runtime: RuntimeContext,
        base_config: GatewayConfig | None = None,
        config_reloader: ScheduledWakeWorkerGlobalConfigReloader | None = None,
    ) -> None:
        self.config = config
        self.worker_config = worker_config
        self._base_config = base_config or config
        self._config_reloader = (
            config_reloader
            or FixedScheduledWakeWorkerGlobalConfigReloader(self.worker_config)
        )
        self.runtime = runtime
        self.dispatcher = HostWakeDispatcher(
            config=self.config,
            runtime=runtime,
            agent_control_store=AgentControlStore(self.config.host_agent_control_state_path),
        )
        self._kernel = PollingOrchestratorKernel(
            config=PollingKernelConfig(
                poll_interval_seconds=self.worker_config.poll_interval_seconds,
                jitter_seconds=self.worker_config.jitter_seconds,
                lock_path=self.worker_config.lock_path,
            ),
            lock_manager=HostFileLockManager(
                busy_error_text="Scheduled wake worker already running"
            ),
            sleeper=HostSleeper(),
            run_cycle=self.run_once,
            get_sleep_seconds=lambda result: result.next_sleep_seconds,
            on_start=self._log_start,
            on_cycle_success=self._log_cycle_success,
            on_cycle_exception=self._log_cycle_exception,
            get_exception_sleep_seconds=lambda _exc: self.worker_config.backoff_seconds,
        )

    def run_forever(self) -> None:
        self._kernel.run_forever()

    def run_once_with_lock(self) -> ScheduledWakeCycleResult:
        return self._kernel.run_once_with_lock()

    def run_once(self) -> ScheduledWakeCycleResult:
        self._refresh_config_from_reloader()
        now_utc = datetime.now(UTC)
        wake_store = getattr(self.runtime, "wake_store", None)
        if wake_store is not None:
            refresh_retry_state = getattr(wake_store, "refresh_one_time_retry_state", None)
            if callable(refresh_retry_state):
                refresh_retry_state(
                    now_utc=now_utc,
                    retry_max_attempts=self.config.wake_completion_confirmation_retry_max_attempts,
                    retry_window_seconds=self.config.wake_completion_confirmation_retry_window_seconds,
                    retry_base_backoff_seconds=self.config.wake_completion_confirmation_retry_base_backoff_seconds,
                    retry_max_backoff_seconds=self.config.wake_completion_confirmation_retry_max_backoff_seconds,
                )
            wake_store.mark_request_due_or_missed(
                now_utc=now_utc,
                late_after=timedelta(
                    seconds=max(0.0, self.config.wake_schedule_late_threshold_seconds)
                ),
            )
        if not self.worker_config.directories or wake_store is None:
            return ScheduledWakeCycleResult(
                requests_due=0,
                wakes_dispatched=0,
                next_sleep_seconds=compute_next_sleep_seconds(
                    poll_interval_seconds=self.worker_config.poll_interval_seconds,
                    jitter_seconds=self.worker_config.jitter_seconds,
                    cooldown_seconds=self.worker_config.cooldown_seconds,
                    activity_detected=0,
                ),
            )
        requests_due = 0
        wakes_dispatched = 0
        for request in wake_store.find_due_requests(now_utc=now_utc):
            session = self.runtime.session_repository.get_session(request.session_id)
            if session is None:
                self._mark_request_missed_or_late_if_needed(
                    wake_store=wake_store,
                    request=request,
                    now_utc=now_utc,
                    reason=(
                        f"Scheduled wake missed because session {request.session_id} is unavailable."
                    ),
                )
                continue
            if not any(
                directory_is_within_scope(session.directory_path, scope)
                for scope in self.worker_config.directories
            ):
                self._mark_request_missed_or_late_if_needed(
                    wake_store=wake_store,
                    request=request,
                    now_utc=now_utc,
                    reason=(
                        "Scheduled wake missed because session "
                        f"{request.session_id} is outside the worker directory scope."
                    ),
                )
                continue
            requests_due += 1
            if self._dispatch_request(request=request, session=session, now_utc=now_utc):
                wakes_dispatched += 1
        return ScheduledWakeCycleResult(
            requests_due=requests_due,
            wakes_dispatched=wakes_dispatched,
            next_sleep_seconds=compute_next_sleep_seconds(
                poll_interval_seconds=self.worker_config.poll_interval_seconds,
                jitter_seconds=self.worker_config.jitter_seconds,
                cooldown_seconds=self.worker_config.cooldown_seconds,
                activity_detected=requests_due,
            ),
        )

    def _log_start(self) -> None:
        logger.info(
            (
                "Scheduled wake worker started poll_interval=%.3f jitter=%.3f "
                "backoff=%.3f cooldown=%.3f lock=%s"
            ),
            self.worker_config.poll_interval_seconds,
            self.worker_config.jitter_seconds,
            self.worker_config.backoff_seconds,
            self.worker_config.cooldown_seconds,
            self.worker_config.lock_path,
        )

    def _log_cycle_success(self, result: ScheduledWakeCycleResult) -> None:
        logger.debug(
            "Scheduled wake cycle complete due=%d dispatched=%d sleep_seconds=%.3f",
            result.requests_due,
            result.wakes_dispatched,
            result.next_sleep_seconds,
        )

    def _log_cycle_exception(self, _exc: Exception) -> None:
        logger.exception("Scheduled wake worker cycle failed")

    def _refresh_config_from_reloader(self) -> None:
        latest_config, changed = self._config_reloader.get_latest()
        if not changed:
            return

        self.worker_config = latest_config
        self.config = _effective_gateway_config_for_scheduled_wake(
            self._base_config,
            self.worker_config,
        )
        self.dispatcher = HostWakeDispatcher(
            config=self.config,
            runtime=self.runtime,
            agent_control_store=AgentControlStore(self.config.host_agent_control_state_path),
        )
        logger.info(
            "Scheduled wake worker config reloaded prefix_text=%r directories=%d",
            self.worker_config.scheduled_wake_prefix_text,
            len(self.worker_config.directories),
        )

    def _dispatch_request(
        self, *, request: WakeScheduledRequest, session: Any, now_utc: datetime
    ) -> bool:
        wake_store = self.runtime.wake_store
        source = "scheduled_cron" if (request.schedule.cron or "").strip() else "scheduled_once"
        wake_metadata: dict[str, Any] = {"schedule_cron": request.schedule.cron}
        late_seconds = self._request_late_seconds(request, now_utc=now_utc)
        if late_seconds is not None:
            wake_metadata["was_late"] = True
            wake_metadata["late_seconds"] = late_seconds
        binding = SessionWakeBinding(
            session_id=session.session_id,
            vm_id=session.vm_id,
            source=source,
            directory_path=session.directory_path,
        )
        scoped_config = self.dispatcher.resolve_scoped_config(
            session_id=binding.session_id,
            directory_path=binding.directory_path,
            source=f"scheduled_wake:{source}",
        )
        if self.dispatcher.is_host_disabled(binding):
            self._mark_request_missed_or_late_if_needed(
                wake_store=wake_store,
                request=request,
                now_utc=now_utc,
                reason=(
                    "Scheduled wake missed because host wake dispatch is disabled "
                    f"for {binding.vm_id or binding.session_id}."
                ),
            )
            return False
        if is_session_in_cooldown(
            path=scoped_config.wake_session_cooldown_path,
            session_id=binding.session_id,
        ):
            self._mark_request_missed_or_late_if_needed(
                wake_store=wake_store,
                request=request,
                now_utc=now_utc,
                reason=(
                    f"Scheduled wake missed because session {binding.session_id} is still cooling down."
                ),
            )
            return False
        dispatch_reason = "dispatch attempt started"
        if late_seconds is not None:
            dispatch_reason = f"{dispatch_reason} ({late_seconds:.3f}s late)"
        wake_store.mark_request_dispatching(
            request.request_id,
            now_utc=now_utc,
            reason=dispatch_reason,
        )
        if session_is_container_worker_vm(session):
            dispatch = self.dispatcher.resume_container_binding(
                session,
                worker_config=scoped_config,
                prompt=_build_scheduled_resume_prompt(scoped_config, request.resume_prompt.prompt),
                resume_mode=request.resume_mode,
                dispatch_source=source,
                request_id=request.request_id,
                scheduled_for=request.next_run_at or request.schedule.resume_at,
                wake_metadata=wake_metadata,
            )
        else:
            if not binding.vm_id:
                self._mark_request_missed_or_late_if_needed(
                    wake_store=wake_store,
                    request=request,
                    now_utc=now_utc,
                    reason=(
                        f"Scheduled wake missed because session {binding.session_id} has no usable VM binding."
                    ),
                )
                return False
            agent_state = probe_agent_dispatch_state(
                session_id=binding.session_id,
                vm_id=binding.vm_id,
                script_path=scoped_config.host_busy_check_script_path,
            )
            if agent_state != "idle":
                if agent_state == "busy":
                    set_session_cooldown(
                        path=scoped_config.wake_session_cooldown_path,
                        session_id=binding.session_id,
                        cooldown_seconds=scoped_config.wake_cooldown_seconds,
                    )
                self._mark_request_missed_or_late_if_needed(
                    wake_store=wake_store,
                    request=request,
                    now_utc=now_utc,
                    reason=(
                        f"Scheduled wake missed because session {binding.session_id} is busy or unavailable."
                    ),
                )
                return False
            dispatch = self.dispatcher.resume_binding(
                binding,
                worker_config=scoped_config,
                prompt=_build_scheduled_resume_prompt(scoped_config, request.resume_prompt.prompt),
                resume_mode=request.resume_mode,
                dispatch_source=source,
                request_id=request.request_id,
                scheduled_for=request.next_run_at or request.schedule.resume_at,
                wake_metadata=wake_metadata,
            )
        dispatch_handled = _wake_dispatch_counts_as_handled(dispatch)
        dispatch_completed_at = datetime.now(UTC)
        if request.schedule.resume_at is not None and not dispatch_handled:
            refresh_retry_state = getattr(wake_store, "refresh_one_time_retry_state", None)
            if callable(refresh_retry_state):
                refresh_retry_state(
                    now_utc=dispatch_completed_at,
                    retry_max_attempts=self.config.wake_completion_confirmation_retry_max_attempts,
                    retry_window_seconds=self.config.wake_completion_confirmation_retry_window_seconds,
                    retry_base_backoff_seconds=self.config.wake_completion_confirmation_retry_base_backoff_seconds,
                    retry_max_backoff_seconds=self.config.wake_completion_confirmation_retry_max_backoff_seconds,
                )
        if dispatch_handled:
            set_session_cooldown(
                path=scoped_config.wake_session_cooldown_path,
                session_id=binding.session_id,
                cooldown_seconds=scoped_config.wake_cooldown_seconds,
            )
        if (request.schedule.cron or "").strip() and dispatch_handled:
            _advance_cron_wake_request(
                wake_store=wake_store,
                request=request,
                now_utc=now_utc,
                mark_consumed=False,
                was_handled=True,
            )
        elif dispatch_handled:
            _defer_one_time_wake_confirmation(
                wake_store=wake_store,
                request=request,
                now_utc=getattr(dispatch, "dispatched_at", None) or dispatch_completed_at,
                confirmation_timeout_seconds=(
                    scoped_config.wake_completion_confirmation_timeout_seconds
                ),
            )
        return dispatch_handled

    def _request_late_seconds(
        self, request: WakeScheduledRequest, *, now_utc: datetime
    ) -> float | None:
        scheduled_for = request.next_run_at or request.schedule.resume_at
        if scheduled_for is None:
            return None
        late_seconds = (now_utc - scheduled_for).total_seconds()
        if late_seconds <= 0:
            return None
        return round(late_seconds, 3)

    def _mark_request_missed_or_late_if_needed(
        self,
        *,
        wake_store: Any,
        request: WakeScheduledRequest,
        now_utc: datetime,
        reason: str,
    ) -> None:
        late_after_seconds = max(0.0, self.config.wake_schedule_late_threshold_seconds)
        late_seconds = self._request_late_seconds(request, now_utc=now_utc)
        if late_seconds is None or late_seconds <= late_after_seconds:
            return
        request.status = "missed_or_late"
        request.status_reason = reason
        request.status_updated_at = now_utc
        request.last_error = reason
        request.last_error_at = now_utc
        request.updated_at = now_utc
        wake_store.upsert_request(request)


def _worker_global_config_path(project_dir: Path) -> Path:
    return project_dir / "config" / "scheduled-wake" / "config.json"


def _load_scheduled_wake_worker_global_config(
    project_dir: Path,
    *,
    base_config: GatewayConfig | None = None,
) -> ScheduledWakeWorkerGlobalConfig:
    source = base_config or GatewayConfig()
    base_payload = ScheduledWakeWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": source.runtime_dir,
            "directories": [],
            "poll_interval_seconds": source.wake_poll_interval_seconds,
            "jitter_seconds": source.wake_jitter_seconds,
            "cooldown_seconds": source.wake_cooldown_seconds,
            "late_threshold_seconds": source.wake_schedule_late_threshold_seconds,
            "completion_confirmation_retry_max_attempts": (
                source.wake_completion_confirmation_retry_max_attempts
            ),
            "completion_confirmation_retry_window_seconds": (
                source.wake_completion_confirmation_retry_window_seconds
            ),
            "completion_confirmation_retry_base_backoff_seconds": (
                source.wake_completion_confirmation_retry_base_backoff_seconds
            ),
            "completion_confirmation_retry_max_backoff_seconds": (
                source.wake_completion_confirmation_retry_max_backoff_seconds
            ),
            "lock_path": source.runtime_dir / "scheduled-wake.lock",
            "session_cooldown_path": source.wake_session_cooldown_path,
            "busy_check_script_path": source.host_busy_check_script_path,
            "resume_script_path": source.host_resume_script_path,
            "wake_state_path": source.runtime_dir / "scheduled-wake-state.json",
            "vm_user": source.wake_vm_user,
            "resume_timeout_seconds": source.wake_resume_timeout_seconds,
            "scheduled_wake_prefix_text": source.scheduled_wake_prefix_text,
        }
    ).model_dump(mode="json")
    path = _worker_global_config_path(project_dir)
    if not path.exists():
        return ScheduledWakeWorkerGlobalConfig.model_validate(base_payload)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"worker config file {path} must contain a JSON object")
    return ScheduledWakeWorkerGlobalConfig.model_validate({**base_payload, **raw})


def _effective_gateway_config_for_scheduled_wake(
    base_config: GatewayConfig,
    worker_config: ScheduledWakeWorkerGlobalConfig,
) -> GatewayConfig:
    return GatewayConfig.model_validate(
        {
            **base_config.model_dump(mode="json"),
            "runtime_dir": worker_config.runtime_dir,
            "wake_poll_interval_seconds": worker_config.poll_interval_seconds,
            "wake_jitter_seconds": worker_config.jitter_seconds,
            "wake_cooldown_seconds": worker_config.cooldown_seconds,
            "wake_schedule_late_threshold_seconds": worker_config.late_threshold_seconds,
            "wake_completion_confirmation_timeout_seconds": (
                worker_config.completion_confirmation_timeout_seconds
            ),
            "wake_completion_confirmation_retry_max_attempts": (
                worker_config.completion_confirmation_retry_max_attempts
            ),
            "wake_completion_confirmation_retry_window_seconds": (
                worker_config.completion_confirmation_retry_window_seconds
            ),
            "wake_completion_confirmation_retry_base_backoff_seconds": (
                worker_config.completion_confirmation_retry_base_backoff_seconds
            ),
            "wake_completion_confirmation_retry_max_backoff_seconds": (
                worker_config.completion_confirmation_retry_max_backoff_seconds
            ),
            "wake_session_cooldown_path": worker_config.session_cooldown_path,
            "host_busy_check_script_path": worker_config.busy_check_script_path,
            "host_resume_script_path": worker_config.resume_script_path,
            "wake_dispatch_state_path": worker_config.wake_state_path,
            "wake_vm_user": worker_config.vm_user,
            "wake_resume_timeout_seconds": worker_config.resume_timeout_seconds,
            "scheduled_wake_prefix_text": worker_config.scheduled_wake_prefix_text,
        }
    )


class ScheduledWakeWorkerGlobalConfigReloader:
    def __init__(
        self,
        *,
        project_dir: Path,
        initial_config: ScheduledWakeWorkerGlobalConfig,
        base_config: GatewayConfig | None = None,
    ) -> None:
        self._project_dir = Path(project_dir)
        self._path = _worker_global_config_path(self._project_dir)
        self._base_config = base_config
        self._current_config = initial_config
        self._last_fingerprint = self._compute_fingerprint()

    def get_latest(self) -> tuple[ScheduledWakeWorkerGlobalConfig, bool]:
        return self._reload(force=False)

    def reload_now(self) -> tuple[ScheduledWakeWorkerGlobalConfig, bool]:
        return self._reload(force=True)

    def _reload(self, *, force: bool) -> tuple[ScheduledWakeWorkerGlobalConfig, bool]:
        fingerprint = self._compute_fingerprint()
        if not force and fingerprint == self._last_fingerprint:
            return self._current_config, False

        try:
            next_config = _load_scheduled_wake_worker_global_config(
                self._project_dir, base_config=self._base_config
            )
        except Exception:
            self._last_fingerprint = fingerprint
            logger.error(
                "Failed to reload scheduled wake worker config path=%s; keeping last-known-good",
                self._path,
                exc_info=True,
            )
            return self._current_config, False

        changed = next_config != self._current_config
        self._current_config = next_config
        self._last_fingerprint = fingerprint
        return self._current_config, changed

    def _compute_fingerprint(self) -> tuple[int, int, int, str]:
        fingerprint = _file_fingerprint(self._path)
        if fingerprint is None:
            return (0, 0, 0, f"missing:{self._path}")
        return fingerprint


class FixedScheduledWakeWorkerGlobalConfigReloader:
    def __init__(self, config: ScheduledWakeWorkerGlobalConfig) -> None:
        self._config = config

    def get_latest(self) -> tuple[ScheduledWakeWorkerGlobalConfig, bool]:
        return self._config, False

    def reload_now(self) -> tuple[ScheduledWakeWorkerGlobalConfig, bool]:
        return self._config, False


def build_scheduled_wake_worker(
    config: GatewayConfig,
    *,
    project_dir: Path | None = None,
) -> ScheduledWakeWorker:
    resolved_project_dir = Path(project_dir) if project_dir is not None else Path.cwd()
    worker_config = _load_scheduled_wake_worker_global_config(
        resolved_project_dir, base_config=config
    )
    effective_config = _effective_gateway_config_for_scheduled_wake(config, worker_config)
    runtime = build_runtime(effective_config)
    config_reloader = ScheduledWakeWorkerGlobalConfigReloader(
        project_dir=resolved_project_dir,
        initial_config=worker_config,
        base_config=config,
    )
    return ScheduledWakeWorker(
        config=effective_config,
        worker_config=worker_config,
        runtime=runtime,
        base_config=config,
        config_reloader=config_reloader,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the scheduled wake worker.")
    parser.add_argument("--runtime-dir", default="runtime")
    parser.add_argument("--log-level", default=os.environ.get("SCHEDULED_WAKE_LOG_LEVEL", "INFO"))
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    logging_level = getattr(logging, str(args.log_level).upper(), None)
    if not isinstance(logging_level, int):
        raise SystemExit(f"Invalid --log-level value: {args.log_level}")
    logging.basicConfig(
        level=logging_level, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )

    runtime_dir = Path(args.runtime_dir).expanduser().resolve()
    config = load_runtime_gateway_config(runtime_dir=runtime_dir)
    worker = build_scheduled_wake_worker(config, project_dir=runtime_dir.parent)
    if args.once:
        result = worker.run_once_with_lock()
        print(
            f"Scheduled wake worker cycle complete due={result.requests_due} "
            f"dispatched={result.wakes_dispatched} sleep_seconds={result.next_sleep_seconds:.3f}"
        )
        return
    worker.run_forever()


def _build_scheduled_resume_prompt(worker_config: GatewayConfig, requested_prompt: str) -> str:
    preamble = str(worker_config.scheduled_wake_prefix_text).strip()
    cleaned_requested = requested_prompt.strip()
    if preamble and cleaned_requested:
        return f"{preamble} {cleaned_requested}"
    if preamble:
        return preamble
    return cleaned_requested


def _wake_dispatch_counts_as_handled(dispatch: Any | None) -> bool:
    status = str(getattr(dispatch, "status", "") or "").strip()
    return status in {"dispatched", "queued"}


def _advance_cron_wake_request(
    *,
    wake_store: Any,
    request: WakeScheduledRequest,
    now_utc: datetime,
    mark_consumed: bool,
    was_handled: bool,
) -> None:
    get_request = getattr(wake_store, "get_request", None)
    if callable(get_request):
        refreshed = get_request(request.request_id)
        if refreshed is not None:
            request = refreshed

    if was_handled and request.last_dispatched_at is None:
        request.last_dispatched_at = now_utc
    request.updated_at = now_utc
    if mark_consumed:
        request.enabled = False
        request.next_run_at = None
        wake_store.upsert_request(request)
        return
    cron = (request.schedule.cron or "").strip()
    if not cron:
        wake_store.upsert_request(request)
        return
    next_base = request.next_run_at or now_utc
    request.next_run_at = next_cron_time(cron, next_base)
    wake_store.upsert_request(request)


def _defer_one_time_wake_confirmation(
    *,
    wake_store: Any,
    request: WakeScheduledRequest,
    now_utc: datetime,
    confirmation_timeout_seconds: float,
) -> None:
    get_request = getattr(wake_store, "get_request", None)
    if callable(get_request):
        refreshed = get_request(request.request_id)
        if refreshed is not None:
            request = refreshed

    if request.last_dispatched_at is None:
        request.last_dispatched_at = now_utc
    request.enabled = True
    request.auto_dispatch_attempts = max(0, request.auto_dispatch_attempts) + 1
    if request.auto_retry_started_at is None:
        request.auto_retry_started_at = request.last_dispatched_at or now_utc
    request.updated_at = now_utc
    request.next_run_at = now_utc + timedelta(
        seconds=max(0.0, float(confirmation_timeout_seconds))
    )
    request.auto_retry_exhausted_at = None
    wake_store.upsert_request(request)
