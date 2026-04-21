from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .agent_control import AgentControlStore
from .bootstrap import load_runtime_gateway_config
from .config import GatewayConfig, ScheduledWakeWorkerGlobalConfig
from .models import WakeScheduledRequest
from .runtime import RuntimeContext, build_runtime
from .wake_dispatch import (
    HostWakeDispatcher,
    SessionWakeBinding,
    compute_next_sleep_seconds,
    directory_is_within_scope,
    is_session_in_cooldown,
    probe_agent_dispatch_state,
    set_session_cooldown,
    single_instance_lock,
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

    def run_forever(self) -> None:
        with single_instance_lock(self.worker_config.lock_path):
            while True:
                result = self.run_once()
                logger.debug(
                    "Scheduled wake cycle complete due=%d dispatched=%d sleep_seconds=%.3f",
                    result.requests_due,
                    result.wakes_dispatched,
                    result.next_sleep_seconds,
                )
                import time

                time.sleep(result.next_sleep_seconds)

    def run_once_with_lock(self) -> ScheduledWakeCycleResult:
        with single_instance_lock(self.worker_config.lock_path):
            return self.run_once()

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
        sessions_by_id = self.runtime.sessions.read_all()
        for request in _load_due_wake_requests(wake_store=wake_store, now_utc=now_utc):
            session = sessions_by_id.get(request.session_id)
            if session is None:
                continue
            if not any(directory_is_within_scope(session.directory_path, scope) for scope in self.worker_config.directories):
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

    def _dispatch_request(self, *, request: WakeScheduledRequest, session: Any, now_utc: datetime) -> bool:
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
            path=scoped_config.host_heartbeat_session_cooldown_path,
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
                    path=scoped_config.host_heartbeat_session_cooldown_path,
                    session_id=binding.session_id,
                    cooldown_seconds=scoped_config.host_heartbeat_cooldown_seconds,
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
        set_session_cooldown(
            path=scoped_config.host_heartbeat_session_cooldown_path,
            session_id=binding.session_id,
            cooldown_seconds=scoped_config.host_heartbeat_cooldown_seconds,
        )
        if (request.schedule.cron or "").strip():
            _advance_cron_wake_request(
                wake_store=wake_store,
                request=request,
                now_utc=now_utc,
                mark_consumed=False,
                was_dispatched=bool(dispatch and dispatch.status == "dispatched"),
            )
        elif dispatch is not None and dispatch.status == "dispatched":
            _advance_cron_wake_request(
                wake_store=wake_store,
                request=request,
                now_utc=now_utc,
                mark_consumed=True,
                was_dispatched=True,
            )
        return bool(dispatch and dispatch.status == "dispatched")


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
            "poll_interval_seconds": source.host_heartbeat_poll_interval_seconds,
            "jitter_seconds": source.host_heartbeat_jitter_seconds,
            "cooldown_seconds": source.host_heartbeat_cooldown_seconds,
            "lock_path": source.runtime_dir / "scheduled-wake.lock",
            "session_cooldown_path": source.host_heartbeat_session_cooldown_path,
            "busy_check_script_path": source.host_busy_check_script_path,
            "resume_script_path": source.host_resume_script_path,
            "wake_state_path": source.runtime_dir / "scheduled-wake-state.json",
            "vm_user": source.host_heartbeat_vm_user,
            "resume_timeout_seconds": source.host_heartbeat_resume_timeout_seconds,
            "scheduled_wake_prefix_text": source.host_heartbeat_scheduled_wake_prefix_text,
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
    worker_config = _load_scheduled_wake_worker_global_config(resolved_project_dir, base_config=config)
    effective_config = GatewayConfig.model_validate(
        {
            **config.model_dump(mode="json"),
            "runtime_dir": worker_config.runtime_dir,
            "host_heartbeat_directory_paths": worker_config.directories,
            "host_heartbeat_poll_interval_seconds": worker_config.poll_interval_seconds,
            "host_heartbeat_jitter_seconds": worker_config.jitter_seconds,
            "host_heartbeat_cooldown_seconds": worker_config.cooldown_seconds,
            "host_heartbeat_lock_path": worker_config.lock_path,
            "host_heartbeat_session_cooldown_path": worker_config.session_cooldown_path,
            "host_busy_check_script_path": worker_config.busy_check_script_path,
            "host_resume_script_path": worker_config.resume_script_path,
            "host_heartbeat_wake_state_path": worker_config.wake_state_path,
            "host_heartbeat_vm_user": worker_config.vm_user,
            "host_heartbeat_resume_timeout_seconds": worker_config.resume_timeout_seconds,
            "host_heartbeat_scheduled_wake_prefix_text": worker_config.scheduled_wake_prefix_text,
        }
    )
    runtime = build_runtime(effective_config)
    return ScheduledWakeWorker(config=effective_config, worker_config=worker_config, runtime=runtime)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the scheduled wake worker.")
    parser.add_argument("--runtime-dir", default="runtime")
    parser.add_argument("--log-level", default=os.environ.get("SCHEDULED_WAKE_LOG_LEVEL", "INFO"))
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    logging_level = getattr(logging, str(args.log_level).upper(), None)
    if not isinstance(logging_level, int):
        raise SystemExit(f"Invalid --log-level value: {args.log_level}")
    logging.basicConfig(level=logging_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")

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
    preamble = str(worker_config.host_heartbeat_scheduled_wake_prefix_text).strip()
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
                next_run_at = _next_cron_time(cron, now_utc - timedelta(minutes=1))
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
    request.next_run_at = _next_cron_time(cron, next_base)
    wake_store.upsert_request(request)


def _parse_wake_request(raw_value: dict[str, Any]) -> WakeScheduledRequest | None:
    try:
        return WakeScheduledRequest.model_validate(raw_value)
    except Exception:
        logger.warning("Skipping invalid wake request entry=%r", raw_value, exc_info=True)
        return None


def _next_cron_time(expression: str, base: datetime) -> datetime | None:
    fields = expression.split()
    if len(fields) != 5:
        return None
    minute_values = _parse_cron_field(fields[0], 0, 59)
    hour_values = _parse_cron_field(fields[1], 0, 23)
    day_values = _parse_cron_field(fields[2], 1, 31)
    month_values = _parse_cron_field(fields[3], 1, 12)
    weekday_values = _parse_cron_field(fields[4], 0, 6)
    if any(value is None for value in (minute_values, hour_values, day_values, month_values, weekday_values)):
        return None
    assert minute_values is not None
    assert hour_values is not None
    assert day_values is not None
    assert month_values is not None
    assert weekday_values is not None
    candidate = base.astimezone(UTC).replace(second=0, microsecond=0) + timedelta(minutes=1)
    deadline = candidate + timedelta(days=366)
    while candidate <= deadline:
        cron_weekday = (candidate.weekday() + 1) % 7
        if (
            candidate.minute in minute_values
            and candidate.hour in hour_values
            and candidate.day in day_values
            and candidate.month in month_values
            and cron_weekday in weekday_values
        ):
            return candidate
        candidate += timedelta(minutes=1)
    return None


def _parse_cron_field(token: str, lower: int, upper: int) -> set[int] | None:
    normalized = token.strip()
    if not normalized:
        return None
    if normalized == "*":
        return set(range(lower, upper + 1))
    values: set[int] = set()
    for chunk in normalized.split(","):
        part = chunk.strip()
        if not part:
            return None
        if part.startswith("*/"):
            try:
                step = int(part[2:])
            except ValueError:
                return None
            if step <= 0:
                return None
            values.update(range(lower, upper + 1, step))
            continue
        try:
            value = int(part)
        except ValueError:
            return None
        if value < lower or value > upper:
            return None
        values.add(value)
    return values
