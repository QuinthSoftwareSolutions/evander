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

from agent_sandbox.config import (
    GatewayConfig,
    OrchestrationCoreConfig,
    TriggerOrchestratorWorkerGlobalConfig,
)
from agent_sandbox.provider_config import ProviderConfigResolver
from agent_sandbox.runtime import RuntimeContext
from agent_sandbox.app_bootstrap import build_runtime
from agent_sandbox.platform_integrations import PlatformServiceBundle, RuntimeAssemblyInterface
from agent_sandbox.events.models import TriggerDefinitionDiscriminated, validate_output_mapping_keys
from agent_sandbox.orchestration.adapters import (
    TriggerCompletionPublisherAdapter,
    TriggerDependencyResolverAdapter,
    TriggerEventFetcherAdapter,
    TriggerExecutionRecorderAdapter,
)
from agent_sandbox.workflow.engine import OrchestrationLoop
from agent_sandbox.triggers.queue import TriggerEventQueue
from agent_sandbox.workflow.runtime_config import (
    FixedTriggerOrchestratorWorkerGlobalConfigReloader,
    TriggerOrchestratorWorkerGlobalConfigReloader,
    load_trigger_orchestrator_worker_global_config,
)
from agent_sandbox.messaging.wake_dispatch import single_instance_lock
from agent_sandbox.orchestration.kernels import PollingKernelConfig, PollingOrchestratorKernel
from agent_sandbox.orchestration.wiring import TriggerWorkerWiring, build_trigger_worker_wiring
from agent_sandbox.repositories.orchestration import GateCursorRepository

logger = logging.getLogger(__name__)


class _LockManager:
    def lock(self, path: Path):
        return single_instance_lock(path)


class _Sleeper:
    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


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
        core_config: OrchestrationCoreConfig | None = None,
        worker_config: TriggerOrchestratorWorkerGlobalConfig | None = None,
        config_reloader: TriggerOrchestratorWorkerGlobalConfigReloader | None = None,
        runtime: RuntimeContext,
        provider_config_resolver: ProviderConfigResolver | None = None,
        wiring: TriggerWorkerWiring | None = None,
    ) -> None:
        self.config = config
        selected_config = core_config or worker_config
        if selected_config is None:
            raise ValueError(
                "TriggerOrchestratorWorker requires either core_config or worker_config"
            )
        self.worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
            selected_config.model_dump(mode="json")
        )
        self._config_reloader = (
            config_reloader
            or FixedTriggerOrchestratorWorkerGlobalConfigReloader(self.worker_config)
        )
        self.runtime = runtime
        resolved_wiring = wiring or build_trigger_worker_wiring(
            config=config,
            worker_config=self.worker_config,
            runtime=runtime,
            provider_config_resolver=provider_config_resolver,
        )
        self.provider_config_resolver = resolved_wiring.provider_config_resolver
        self.event_queue = resolved_wiring.event_queue
        self.provider_factory = resolved_wiring.provider_factory
        self.cursor_repository: GateCursorRepository = resolved_wiring.gate_cursor_repository
        self._kernel = PollingOrchestratorKernel(
            config=PollingKernelConfig(
                poll_interval_seconds=self.worker_config.poll_interval_seconds,
                jitter_seconds=self.worker_config.jitter_seconds,
                lock_path=self.worker_config.lock_path,
            ),
            lock_manager=_LockManager(),
            sleeper=_Sleeper(),
            run_cycle=self.run_once,
            get_sleep_seconds=lambda result: result.next_sleep_seconds,
        )

    def run_forever(self) -> None:
        self._kernel.run_forever()

    def run_once_with_lock(self) -> TriggerOrchestratorCycleResult:
        return self._kernel.run_once_with_lock()

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
        loop = OrchestrationLoop[
            TriggerDefinitionDiscriminated,
            Any,
            Any,
        ](
            event_fetcher=TriggerEventFetcherAdapter(triggers=configured_triggers),
            dependency_resolver=TriggerDependencyResolverAdapter(
                providers_by_id=providers,
                update_cursor_state=lambda trigger, discovered, event_ids: (
                    self._update_cursor_state(
                        trigger=trigger,
                        discovered=discovered,
                        event_ids=event_ids,
                    )
                ),
                on_poll_error=lambda trigger: logger.exception(
                    "Trigger poll failed trigger_id=%s trigger_type=%s",
                    trigger.id,
                    trigger.type,
                ),
            ),
            execution_recorder=TriggerExecutionRecorderAdapter(),
            completion_publisher=TriggerCompletionPublisherAdapter(),
        )

        def _resolve_and_execute(trigger: TriggerDefinitionDiscriminated, event: Any) -> Any | None:
            trace_id = _normalize_trace_id(getattr(event, "trace_id", None))
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
                return None
            already_queued = self.event_queue.contains(event.event_id)
            record = self.event_queue.publish(event)
            if not already_queued and record.event_id == event.event_id:
                logger.debug(
                    "Trigger event published trigger_id=%s trigger_type=%s event_id=%s trace_id=%s",
                    trigger.id,
                    trigger.type,
                    event.event_id,
                    trace_id,
                )
                return {"published": True}
            return None

        loop_result = loop.run_once(
            max_events=len(configured_triggers), resolve_and_execute=_resolve_and_execute
        )
        events_discovered = loop_result.dependencies_resolved
        events_published = loop_result.executions_recorded

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
        *,
        trigger: TriggerDefinitionDiscriminated,
        discovered: int,
        event_ids: list[str],
    ) -> None:
        self.cursor_repository.record_trigger_poll(
            trigger_id=trigger.id,
            trigger_type=trigger.type,
            discovered=discovered,
            event_ids=event_ids,
        )


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
    services: RuntimeAssemblyInterface | None = None,
) -> TriggerOrchestratorWorker:
    resolved_project_dir = Path(project_dir) if project_dir is not None else Path.cwd()
    worker_config = load_trigger_orchestrator_worker_global_config(
        resolved_project_dir, base_config=config
    )
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
    service_bundle = services or PlatformServiceBundle(runtime_builder=build_runtime)
    runtime = service_bundle.build_runtime(effective_config)
    return TriggerOrchestratorWorker(
        config=effective_config,
        worker_config=worker_config,
        config_reloader=config_reloader,
        runtime=runtime,
    )


def main() -> None:
    import argparse

    from agent_sandbox.bootstrap import load_runtime_gateway_config

    parser = argparse.ArgumentParser(description="Run the trigger orchestrator worker.")
    parser.add_argument("--runtime-dir", default="runtime")
    parser.add_argument(
        "--log-level", default=os.environ.get("TRIGGER_ORCHESTRATOR_LOG_LEVEL", "INFO")
    )
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
