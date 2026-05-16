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
from agent_sandbox.messaging.wake_dispatch import (
    HostWakeDispatcher,
    SessionWakeBinding,
    compute_next_sleep_seconds,
    directory_is_within_scope,
    is_session_in_cooldown,
    probe_agent_dispatch_state,
    set_session_cooldown,
)

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
    ) -> None:
        self.config = config
        self.worker_config = worker_config
        self.runtime = runtime
        self.dispatcher = HostWakeDispatcher(
            config=config,
            runtime=runtime,
            agent_control_store=AgentControlStore(config.host_agent_control_state_path),
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
        if not self.worker_config.directories:
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
        wake_store = getattr(self.runtime, "wake_store", None)
        if wake_store is None:
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
        now_utc = datetime.now(UTC)
        requests_due = 0
        wakes_dispatched = 0
        for request in _load_due_wake_requests(wake_store=wake_store, now_utc=now_utc):
            session = self.runtime.session_repository.get_session(request.session_id)
            if session is None:
                continue
            if not any(
                directory_is_within_scope(session.directory_path, scope)
                for scope in self.worker_config.directories
            ):
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

    def _dispatch_request(
        self, *, request: WakeScheduledRequest, session: Any, now_utc: datetime
    ) -> bool:
        wake_store = self.runtime.wake_store
        source = "scheduled_cron" if (request.schedule.cron or "").strip() else "scheduled_once"
        if session.expires_at <= now_utc:
            return False
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
            return False
        if is_session_in_cooldown(
            path=scoped_config.wake_session_cooldown_path,
            session_id=binding.session_id,
        ):
            return False
        if not binding.vm_id:
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
            return False
        dispatch = self.dispatcher.resume_binding(
            binding,
            worker_config=scoped_config,
            prompt=_build_scheduled_resume_prompt(scoped_config, request.resume_prompt.prompt),
            resume_mode=request.resume_mode,
            dispatch_source=source,
            request_id=request.request_id,
            scheduled_for=request.next_run_at or request.schedule.resume_at,
            wake_metadata={"schedule_cron": request.schedule.cron},
        )
        dispatch_succeeded = bool(dispatch and dispatch.status == "dispatched")
        if dispatch_succeeded:
            set_session_cooldown(
                path=scoped_config.wake_session_cooldown_path,
                session_id=binding.session_id,
                cooldown_seconds=scoped_config.wake_cooldown_seconds,
            )
        if (request.schedule.cron or "").strip() and dispatch_succeeded:
            _advance_cron_wake_request(
                wake_store=wake_store,
                request=request,
                now_utc=now_utc,
                mark_consumed=False,
                was_dispatched=True,
            )
        elif dispatch_succeeded:
            _advance_cron_wake_request(
                wake_store=wake_store,
                request=request,
                now_utc=now_utc,
                mark_consumed=True,
                was_dispatched=True,
            )
        return dispatch_succeeded


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
            "lock_path": source.runtime_dir / "scheduled-wake.lock",
            "session_cooldown_path": source.wake_session_cooldown_path,
            "busy_check_script_path": source.host_busy_check_script_path,
            "resume_script_path": source.host_resume_script_path,
            "wake_state_path": source.runtime_dir / "scheduled-wake-state.json",
            "vm_user": source.wake_vm_user,
            "resume_timeout_seconds": source.wake_resume_timeout_seconds,
            "scheduled_wake_prefix_text": source.wake_scheduled_prefix_text,
        }
    ).model_dump(mode="json")
    path = _worker_global_config_path(project_dir)
    if not path.exists():
        return ScheduledWakeWorkerGlobalConfig.model_validate(base_payload)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"worker config file {path} must contain a JSON object")
    return ScheduledWakeWorkerGlobalConfig.model_validate({**base_payload, **raw})


def build_scheduled_wake_worker(
    config: GatewayConfig,
    *,
    project_dir: Path | None = None,
) -> ScheduledWakeWorker:
    resolved_project_dir = Path(project_dir) if project_dir is not None else Path.cwd()
    worker_config = _load_scheduled_wake_worker_global_config(
        resolved_project_dir, base_config=config
    )
    effective_config = GatewayConfig.model_validate(
        {
            **config.model_dump(mode="json"),
            "runtime_dir": worker_config.runtime_dir,
            "wake_poll_interval_seconds": worker_config.poll_interval_seconds,
            "wake_jitter_seconds": worker_config.jitter_seconds,
            "wake_cooldown_seconds": worker_config.cooldown_seconds,
            "wake_session_cooldown_path": worker_config.session_cooldown_path,
            "host_busy_check_script_path": worker_config.busy_check_script_path,
            "host_resume_script_path": worker_config.resume_script_path,
            "wake_dispatch_state_path": worker_config.wake_state_path,
            "wake_vm_user": worker_config.vm_user,
            "wake_resume_timeout_seconds": worker_config.resume_timeout_seconds,
            "wake_scheduled_prefix_text": worker_config.scheduled_wake_prefix_text,
        }
    )
    runtime = build_runtime(effective_config)
    return ScheduledWakeWorker(
        config=effective_config, worker_config=worker_config, runtime=runtime
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
    preamble = str(worker_config.wake_scheduled_prefix_text).strip()
    cleaned_requested = requested_prompt.strip()
    if preamble and cleaned_requested:
        return f"{preamble} {cleaned_requested}"
    if preamble:
        return preamble
    return cleaned_requested


def _load_due_wake_requests(*, wake_store: Any, now_utc: datetime) -> list[WakeScheduledRequest]:
    payload = wake_store.read_raw()
    raw_requests = payload.get("requests", {})
    if not isinstance(raw_requests, dict):
        return []
    due: list[WakeScheduledRequest] = []
    for raw_value in raw_requests.values():
        if not isinstance(raw_value, dict):
            continue
        request = _parse_wake_request(raw_value)
        if request is None or not request.enabled:
            continue
        next_run_at = request.next_run_at
        if next_run_at is None:
            cron = (request.schedule.cron or "").strip()
            if cron:
                next_run_at = next_cron_time(cron, now_utc - timedelta(minutes=1))
                if next_run_at is not None:
                    request.next_run_at = next_run_at
                    request.updated_at = now_utc
                    wake_store.upsert_request(request)
        if next_run_at is None or next_run_at > now_utc:
            continue
        due.append(request)
    due.sort(key=lambda request: request.next_run_at or request.created_at)
    return due


def _advance_cron_wake_request(
    *,
    wake_store: Any,
    request: WakeScheduledRequest,
    now_utc: datetime,
    mark_consumed: bool,
    was_dispatched: bool,
) -> None:
    request.last_dispatched_at = now_utc if was_dispatched else request.last_dispatched_at
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


def _parse_wake_request(raw_value: dict[str, Any]) -> WakeScheduledRequest | None:
    try:
        return WakeScheduledRequest.model_validate(raw_value)
    except Exception:
        logger.warning("Skipping invalid wake request entry=%r", raw_value, exc_info=True)
        return None
