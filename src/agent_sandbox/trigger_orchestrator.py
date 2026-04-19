from __future__ import annotations

import json
import hashlib
import logging
import os
import random
import time
from uuid import uuid4
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import GatewayConfig, TriggerOrchestratorWorkerGlobalConfig
from .admin_service import AdminService
from .provider_config import ProviderConfigResolver
from .runtime import RuntimeContext, build_runtime
from .events.models import TriggerDefinitionDiscriminated, validate_output_mapping_keys
from .triggers.queue import TriggerEventQueue
from .triggers.registry import TriggerProviderFactory
from .workflow_runtime_config import (
    FixedTriggerOrchestratorWorkerGlobalConfigReloader,
    TriggerOrchestratorWorkerGlobalConfigReloader,
    load_trigger_orchestrator_worker_global_config,
)
from .host_heartbeat_worker import _single_instance_lock

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TriggerOrchestratorCycleResult:
    configured_triggers: int
    events_discovered: int
    events_published: int
    next_sleep_seconds: float


class TriggerOrchestratorWorker:
    def __init__(
        self,
        *,
        config: GatewayConfig,
        worker_config: TriggerOrchestratorWorkerGlobalConfig,
        config_reloader: TriggerOrchestratorWorkerGlobalConfigReloader | None = None,
        runtime: RuntimeContext,
    ) -> None:
        self.config = config
        self.worker_config = worker_config
        self._config_reloader = config_reloader or FixedTriggerOrchestratorWorkerGlobalConfigReloader(worker_config)
        self.runtime = runtime
        self.event_queue = TriggerEventQueue(
            worker_config.event_queue_store_path,
            journal_path=worker_config.workflow_event_journal_path,
        )
        self.provider_factory = TriggerProviderFactory(
            ProviderConfigResolver(config),
            admin_service=AdminService(config.runtime_dir),
            env=dict(os.environ),
        )

    def run_forever(self) -> None:
        with _single_instance_lock(self.worker_config.lock_path):
            logger.info(
                "Trigger orchestrator started poll_interval=%.3f jitter=%.3f lock=%s",
                self.worker_config.poll_interval_seconds,
                self.worker_config.jitter_seconds,
                self.worker_config.lock_path,
            )
            while True:
                try:
                    result = self.run_once()
                except Exception:
                    logger.exception("Trigger orchestrator cycle failed")
                    time.sleep(
                        _compute_backoff_sleep_seconds(
                            poll_interval_seconds=self.worker_config.poll_interval_seconds,
                            jitter_seconds=self.worker_config.jitter_seconds,
                        )
                    )
                    continue
                logger.debug(
                    "Trigger orchestrator cycle complete triggers=%d discovered=%d published=%d sleep_seconds=%.3f",
                    result.configured_triggers,
                    result.events_discovered,
                    result.events_published,
                    result.next_sleep_seconds,
                )
                time.sleep(result.next_sleep_seconds)

    def run_once_with_lock(self) -> TriggerOrchestratorCycleResult:
        with _single_instance_lock(self.worker_config.lock_path):
            return self.run_once()

    def run_once(self) -> TriggerOrchestratorCycleResult:
        latest_config, changed = self._config_reloader.get_latest()
        previous_config = self.worker_config
        self.worker_config = latest_config
        if changed:
            logger.info(
                "Trigger orchestrator config revision changed revision=%s trigger_count=%d",
                _config_revision_hash(latest_config),
                len(latest_config.triggers),
            )
            self.event_queue = TriggerEventQueue(self.worker_config.event_queue_store_path)

        configured_triggers = self._configured_triggers(latest_config)
        providers = self._load_configured_providers(latest_config, configured_triggers)
        events_discovered = 0
        events_published = 0
        cursor_payload = _load_cursor_payload(latest_config.event_queue_cursor_store_path)

        for trigger in configured_triggers:
            provider = providers.get(trigger.id)
            if provider is None:
                continue
            try:
                events = provider.poll()
            except Exception:
                logger.exception(
                    "Trigger poll failed trigger_id=%s trigger_type=%s",
                    trigger.id,
                    trigger.type,
                )
                continue

            discovered_for_trigger = len(events)
            events_discovered += discovered_for_trigger
            for event in events:
                trace_id = _normalize_trace_id(event.trace_id)
                event = event.model_copy(update={"trace_id": trace_id})
                try:
                    validate_output_mapping_keys(
                        event.payload,
                        context=f"{trigger.type} {trigger.id} runtime outputs",
                    )
                except ValueError:
                    logger.exception(
                        "Trigger event rejected trigger_id=%s trigger_type=%s event_id=%s trace_id=%s",
                        trigger.id,
                        trigger.type,
                        event.event_id,
                        trace_id,
                    )
                    continue
                already_queued = self.event_queue.contains(event.event_id)
                record = self.event_queue.publish(event)
                if not already_queued and record.event_id == event.event_id:
                    events_published += 1
                    logger.debug(
                        "Trigger event published trigger_id=%s trigger_type=%s event_id=%s trace_id=%s",
                        trigger.id,
                        trigger.type,
                        event.event_id,
                        trace_id,
                    )

            self._update_cursor_state(
                cursor_payload,
                trigger=trigger,
                discovered=discovered_for_trigger,
                event_ids=[event.event_id for event in events],
            )

        _save_cursor_payload(latest_config.event_queue_cursor_store_path, cursor_payload)
        sleep_config = previous_config if changed else latest_config
        next_sleep_seconds = _compute_next_sleep_seconds(
            poll_interval_seconds=sleep_config.poll_interval_seconds,
            jitter_seconds=sleep_config.jitter_seconds,
        )
        return TriggerOrchestratorCycleResult(
            configured_triggers=len(configured_triggers),
            events_discovered=events_discovered,
            events_published=events_published,
            next_sleep_seconds=next_sleep_seconds,
        )

    def _configured_triggers(
        self, worker_config: TriggerOrchestratorWorkerGlobalConfig
    ) -> list[TriggerDefinitionDiscriminated]:
        return list(worker_config.triggers)

    def _load_configured_providers(
        self,
        worker_config: TriggerOrchestratorWorkerGlobalConfig,
        triggers: list[TriggerDefinitionDiscriminated],
    ) -> dict[str, Any]:
        _ = worker_config
        providers: dict[str, Any] = {}
        for trigger in triggers:
            try:
                providers[trigger.id] = self.provider_factory.build(trigger)
            except Exception:
                logger.exception(
                    "Trigger provider initialization failed trigger_id=%s trigger_type=%s",
                    trigger.id,
                    trigger.type,
                )
        return providers

    def _update_cursor_state(
        self,
        payload: dict[str, Any],
        *,
        trigger: TriggerDefinitionDiscriminated,
        discovered: int,
        event_ids: list[str],
    ) -> None:
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        triggers_payload = payload.setdefault("triggers", {})
        if not isinstance(triggers_payload, dict):
            payload["triggers"] = {}
            triggers_payload = payload["triggers"]
        trigger_payload = {
            "trigger_type": trigger.type,
            "last_polled_at": now,
            "last_discovered_count": discovered,
            "last_event_ids": event_ids[-20:],
        }
        if event_ids:
            trigger_payload["last_event_id"] = event_ids[-1]
        triggers_payload[trigger.id] = trigger_payload


def _normalize_trace_id(value: str | None) -> str:
    if value is None:
        return str(uuid4())
    normalized = value.strip()
    return normalized or str(uuid4())


def _config_revision_hash(worker_config: TriggerOrchestratorWorkerGlobalConfig) -> str:
    payload = worker_config.model_dump(mode="json")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_trigger_orchestrator_worker(
    config: GatewayConfig,
    *,
    project_dir: Path | None = None,
) -> TriggerOrchestratorWorker:
    resolved_project_dir = Path(project_dir) if project_dir is not None else Path.cwd()
    worker_config = load_trigger_orchestrator_worker_global_config(resolved_project_dir, base_config=config)
    config_reloader = TriggerOrchestratorWorkerGlobalConfigReloader(
        project_dir=resolved_project_dir,
        initial_config=worker_config,
        base_config=config,
    )
    effective_config = GatewayConfig.model_validate(
        {
            **config.model_dump(mode="json"),
            "runtime_dir": worker_config.runtime_dir,
        }
    )
    runtime = build_runtime(effective_config)
    return TriggerOrchestratorWorker(
        config=effective_config,
        worker_config=worker_config,
        config_reloader=config_reloader,
        runtime=runtime,
    )


def main() -> None:
    import argparse

    from .bootstrap import load_runtime_gateway_config

    parser = argparse.ArgumentParser(description="Run the trigger orchestrator worker.")
    parser.add_argument("--runtime-dir", default="runtime")
    parser.add_argument("--log-level", default=os.environ.get("TRIGGER_ORCHESTRATOR_LOG_LEVEL", "INFO"))
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    logging_level = getattr(logging, str(args.log_level).upper(), None)
    if not isinstance(logging_level, int):
        raise SystemExit(f"Invalid --log-level value: {args.log_level}")
    logging.basicConfig(level=logging_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    runtime_dir = Path(args.runtime_dir).expanduser().resolve()
    config = load_runtime_gateway_config(runtime_dir=runtime_dir)
    worker = build_trigger_orchestrator_worker(config, project_dir=runtime_dir.parent)
    if args.once:
        result = worker.run_once_with_lock()
        print(
            f"Trigger orchestrator cycle complete triggers={result.configured_triggers} "
            f"discovered={result.events_discovered} published={result.events_published} "
            f"sleep_seconds={result.next_sleep_seconds:.3f}"
        )
        return
    worker.run_forever()


def _compute_next_sleep_seconds(*, poll_interval_seconds: float, jitter_seconds: float) -> float:
    delay = poll_interval_seconds
    if jitter_seconds > 0:
        delay += random.uniform(0.0, jitter_seconds)
    return max(0.0, delay)


def _compute_backoff_sleep_seconds(*, poll_interval_seconds: float, jitter_seconds: float) -> float:
    delay = max(1.0, poll_interval_seconds)
    if jitter_seconds > 0:
        delay += random.uniform(0.0, jitter_seconds)
    return max(1.0, delay)


def _load_cursor_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Unable to read trigger orchestrator cursor state path=%s", path, exc_info=True)
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _save_cursor_payload(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)
