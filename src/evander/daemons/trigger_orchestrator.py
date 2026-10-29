from __future__ import annotations

import json
import hashlib
import logging
import os
import random
import time
from datetime import UTC, datetime
from uuid import uuid4
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evander.config import (
    GatewayConfig,
    OrchestrationCoreConfig,
    TriggerOrchestratorWorkerGlobalConfig,
)
from evander.provider_config import ProviderConfigResolver
from evander.runtime import RuntimeContext
from evander.app_bootstrap import build_runtime
from evander.platform_integrations import PlatformServiceBundle, RuntimeAssemblyInterface
from evander.events.models import (
    TriggerDefinitionDiscriminated,
    TriggerEvent,
    validate_output_mapping_keys,
)
# Retain historical daemon imports for callers using the adapter API.
from evander.orchestration.adapters import (
    TriggerCompletionPublisherAdapter as TriggerCompletionPublisherAdapter,
    TriggerDependencyResolverAdapter as TriggerDependencyResolverAdapter,
    TriggerEventFetcherAdapter as TriggerEventFetcherAdapter,
    TriggerExecutionRecorderAdapter as TriggerExecutionRecorderAdapter,
)
from evander.workflow.engine import OrchestrationLoop as OrchestrationLoop
from evander.triggers.queue import SqliteTriggerEventQueue
from evander.workflow.runtime_config import (
    provider_loader_signature as _provider_loader_signature,
    FixedTriggerOrchestratorWorkerGlobalConfigReloader,
    TriggerOrchestratorWorkerGlobalConfigReloader,
    load_trigger_orchestrator_worker_global_config,
)
from evander.workflow.graph_metadata import (
    ResolvedWorkflowGraphMetadata,
    resolve_workflow_graph_metadata,
)
from evander.workflow.node_state import workflow_node_is_enabled
from evander.orchestration.host_polling import (
    HostFileLockManager,
    HostWakeNotifier,
    HostWakeSleeper,
    wake_socket_path_for_lock,
)
from evander.orchestration.kernels import PollingKernelConfig, PollingOrchestratorKernel
from evander.orchestration.completion_scheduling import CompletionRetry
from evander.orchestration.wal_maintenance import (
    OrchestrationWalCheckpointMaintenance,
)
from evander.orchestration.internal_events import (
    build_internal_event_bus,
    build_scheduled_internal_event_store,
)
from evander.orchestration.wiring import TriggerWorkerWiring, build_trigger_worker_wiring
from evander.orchestration.wiring.trigger import build_trigger_provider_runtime_wiring
from evander.repositories.orchestration import GateCursorRepository, TriggerPollCheckpoint
from evander.repositories.sqlite_runtime import foreground_storage_wait_budget, is_storage_busy

logger = logging.getLogger(__name__)
_SCHEDULED_PUBLICATION_BATCH_SIZE = 200


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
        project_dir: Path | None = None,
    ) -> None:
        self.config = config
        self.project_dir = (
            Path(project_dir) if project_dir is not None else config.runtime_dir.parent
        )
        selected_config = core_config or worker_config
        if selected_config is None:
            raise ValueError(
                "TriggerOrchestratorWorker requires either core_config or worker_config"
            )
        self.worker_config = (
            selected_config
            if isinstance(selected_config, TriggerOrchestratorWorkerGlobalConfig)
            else TriggerOrchestratorWorkerGlobalConfig.model_validate(
                selected_config.model_dump(mode="json")
            )
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
            addon_base_dir=self.project_dir,
        )
        self.provider_config_resolver = resolved_wiring.provider_config_resolver
        self.event_queue = resolved_wiring.event_queue
        self.provider_factory = resolved_wiring.provider_factory
        self.managed_git_maintenance = resolved_wiring.managed_git_maintenance
        self.cursor_repository: GateCursorRepository = resolved_wiring.gate_cursor_repository
        self._wal_checkpoint_maintenance = OrchestrationWalCheckpointMaintenance()
        self._cycle_storage_retry = CompletionRetry()
        self._wake_sleeper = HostWakeSleeper(
            wake_socket_path_for_lock(self.worker_config.lock_path),
        )
        self._effect_wake_notifier = HostWakeNotifier(
            wake_socket_path_for_lock(self.worker_config.effect_lock_path)
        )
        self._kernel = PollingOrchestratorKernel(
            config=PollingKernelConfig(
                poll_interval_seconds=self.worker_config.poll_interval_seconds,
                jitter_seconds=self.worker_config.jitter_seconds,
                lock_path=self.worker_config.lock_path,
            ),
            lock_manager=HostFileLockManager(),
            sleeper=self._wake_sleeper,
            run_cycle=self.run_once,
            get_sleep_seconds=lambda result: result.next_sleep_seconds,
            on_start=self._wake_sleeper.start,
            on_cycle_exception=self._on_cycle_storage_exception,
            get_exception_sleep_seconds=lambda _error: max(
                0.0, self._cycle_storage_retry.retry_at - time.monotonic()
            ),
        )

    def run_forever(self) -> None:
        self._kernel.run_forever()

    def run_once_with_lock(self) -> TriggerOrchestratorCycleResult:
        return self._kernel.run_once_with_lock()

    def run_once(self) -> TriggerOrchestratorCycleResult:
        remaining = self._cycle_storage_retry.retry_at - time.monotonic()
        if remaining > 0:
            return TriggerOrchestratorCycleResult(len(self.worker_config.triggers), 0, 0, remaining)
        try:
            with foreground_storage_wait_budget():
                result = self._run_once()
        except Exception as error:
            self._on_cycle_storage_exception(error)
            return TriggerOrchestratorCycleResult(
                len(self.worker_config.triggers), 0, 0,
                max(0.0, self._cycle_storage_retry.retry_at - time.monotonic()),
            )
        self._cycle_storage_retry = CompletionRetry()
        return result

    def _on_cycle_storage_exception(self, error: Exception) -> None:
        if not is_storage_busy(error):
            raise error
        delay = self._cycle_storage_retry.defer()
        logger.warning("Trigger orchestrator storage busy; retry_seconds=%.3f", delay)

    def _run_once(self) -> TriggerOrchestratorCycleResult:
        latest_config, _ = self._config_reloader.get_latest()
        previous_config = self.worker_config
        config_changed = latest_config != previous_config
        active_config = previous_config
        if config_changed:
            if _provider_loader_signature(latest_config) != _provider_loader_signature(
                previous_config
            ):
                try:
                    runtime_wiring = build_trigger_provider_runtime_wiring(
                        config=self.config,
                        worker_config=latest_config,
                        runtime=self.runtime,
                        provider_config_resolver=self.provider_config_resolver,
                        addon_base_dir=self.project_dir,
                    )
                except Exception:
                    logger.exception(
                        "Trigger orchestrator provider_loader reload failed revision=%s; "
                        "restart or fix configuration before retrying",
                        _config_revision_hash(latest_config),
                    )
                    latest_config = previous_config
                else:
                    self.provider_config_resolver = runtime_wiring.provider_config_resolver
                    self.provider_factory = runtime_wiring.provider_factory
                    self.managed_git_maintenance = runtime_wiring.managed_git_maintenance
            if latest_config != previous_config:
                # A contended replacement must leave the old queue/config pair
                # intact so the next cycle retries the rebind.
                event_queue = _build_trigger_event_queue(latest_config)
                self.worker_config = latest_config
                active_config = latest_config
                self._wake_sleeper.set_paths(
                    wake_socket_path_for_lock(self.worker_config.lock_path),
                )
                logger.info(
                    "Trigger orchestrator config revision changed revision=%s trigger_count=%d",
                    _config_revision_hash(latest_config),
                    len(latest_config.triggers),
                )
                self.event_queue = event_queue
                self._effect_wake_notifier = HostWakeNotifier(
                    wake_socket_path_for_lock(self.worker_config.effect_lock_path)
                )

        configured_triggers = self._configured_triggers(active_config)
        providers = self._load_configured_providers(active_config, configured_triggers)
        node_graph_metadata_by_id = _resolve_node_graph_metadata(active_config)
        config_revision = _config_revision_hash(active_config)
        self._reconcile_internal_event_subscriptions(
            active_config,
            configured_triggers,
            providers,
            config_revision=config_revision,
        )
        self._publish_due_scheduled_internal_events(active_config)
        events_discovered = 0
        events_published = 0
        published_event_ids_by_trigger_id: dict[str, list[str]] = {}

        def publish_trigger_event(
            trigger: TriggerDefinitionDiscriminated, event: TriggerEvent
        ) -> bool:
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
                return False
            publish_result = self.event_queue.publish_result(
                event,
                config_revision=config_revision,
                directory_path=_effective_trigger_directory_path(
                    trigger,
                    node_graph_metadata_by_id,
                ),
            )
            record = publish_result.record
            if record.event_id == event.event_id:
                _acknowledge_provider_event_published(providers.get(trigger.id), event)
                published_event_ids = published_event_ids_by_trigger_id.setdefault(trigger.id, [])
                published_event_ids.append(event.event_id)
                self._update_cursor_state(
                    trigger=trigger,
                    discovered=len(published_event_ids),
                    event_ids=published_event_ids,
                )
            if publish_result.created and record.event_id == event.event_id:
                logger.debug(
                    "Trigger event published trigger_id=%s trigger_type=%s event_id=%s trace_id=%s",
                    trigger.id,
                    trigger.type,
                    event.event_id,
                    trace_id,
                )
                self._effect_wake_notifier.notify()
                return True
            return False

        def _poll_triggers(
            triggers: list[TriggerDefinitionDiscriminated],
        ) -> tuple[int, int]:
            empty_polls: list[TriggerPollCheckpoint] = []

            def flush_empty_polls():
                # Only empty-poll telemetry is deferred. Published events keep
                # their immediate durable checkpoint before the next provider.
                record_batch = getattr(self.cursor_repository, "record_trigger_polls", None)
                if record_batch is not None:
                    record_batch(empty_polls)
                else:
                    for poll in empty_polls:
                        self.cursor_repository.record_trigger_poll(
                            trigger_id=poll.trigger_id,
                            trigger_type=poll.trigger_type,
                            discovered=poll.discovered,
                            event_ids=poll.event_ids,
                            polled_at=poll.polled_at,
                        )
            try:
                discovered = 0
                published = 0
                for trigger in triggers:
                    provider = providers.get(trigger.id)
                    if provider is None:
                        continue
                    try:
                        events = provider.poll()
                    except Exception as error:
                        if is_storage_busy(error):
                            raise
                        logger.exception(
                            "Trigger poll failed trigger_id=%s trigger_type=%s",
                            trigger.id,
                            trigger.type,
                        )
                        continue
                    if not events:
                        empty_polls.append(TriggerPollCheckpoint(
                            trigger_id=trigger.id,
                            trigger_type=trigger.type,
                            discovered=0,
                            event_ids=[],
                            polled_at=datetime.now(UTC),
                        ))
                    events = list(events)
                    discovered += len(events)
                    for event in events:
                        if publish_trigger_event(trigger, event):
                            published += 1
            except Exception:
                try:
                    flush_empty_polls()
                except Exception:
                    # Preserve the original failure, including its storage-busy
                    # retry classification, when deferred telemetry also fails.
                    logger.warning("Failed to flush empty trigger polls after cycle failure", exc_info=True)
                raise
            flush_empty_polls()
            return discovered, published

        events_discovered, events_published = _poll_triggers(configured_triggers)
        scheduled_events_published_after_poll = self._publish_due_scheduled_internal_events(
            active_config
        )
        if scheduled_events_published_after_poll:
            internal_event_triggers = [
                trigger for trigger in configured_triggers if trigger.type == "trigger:event"
            ]
            discovered, published = _poll_triggers(internal_event_triggers)
            events_discovered += discovered
            events_published += published

        sleep_config = previous_config if config_changed else latest_config
        next_sleep_seconds = _compute_trigger_cycle_sleep_seconds(
            events_published=events_published + scheduled_events_published_after_poll,
            poll_interval_seconds=sleep_config.poll_interval_seconds,
            jitter_seconds=sleep_config.jitter_seconds,
            next_scheduled_run_at=build_scheduled_internal_event_store(
                active_config
            ).next_pending_run_at(),
        )
        self._run_orchestration_wal_checkpoint_maintenance()
        return TriggerOrchestratorCycleResult(
            configured_triggers=len(configured_triggers),
            events_discovered=events_discovered,
            events_published=events_published,
            next_sleep_seconds=next_sleep_seconds,
        )

    def _run_orchestration_wal_checkpoint_maintenance(self) -> None:
        result = self._wal_checkpoint_maintenance.run_if_due(self.worker_config)
        if result is not None and result.error:
            logger.warning(
                "Trigger orchestrator WAL checkpoint maintenance reported an error "
                "skipped_reason=%s wal_size_bytes=%d error=%s",
                result.skipped_reason,
                result.wal_size_bytes,
                result.error,
            )

    def _reconcile_internal_event_subscriptions(
        self,
        worker_config: TriggerOrchestratorWorkerGlobalConfig,
        configured_triggers: list[TriggerDefinitionDiscriminated],
        providers: dict[str, Any],
        *,
        config_revision: str,
    ) -> None:
        bus = build_internal_event_bus(worker_config)
        # A configured trigger is missing from providers only when its factory build failed.
        # Keep any existing subscription active until a later cycle can initialize it again.
        active_trigger_ids = {
            trigger.id for trigger in configured_triggers if trigger.id not in providers
        }
        for provider in providers.values():
            subscription_spec = getattr(provider, "subscription_spec", None)
            if not callable(subscription_spec):
                continue
            spec = subscription_spec()
            bus.reconcile_subscription(spec, config_revision=config_revision)
            active_trigger_ids.add(spec.trigger_id)
        bus.deactivate_missing_subscriptions(
            active_trigger_ids,
            config_revision=config_revision,
        )

    def _publish_due_scheduled_internal_events(
        self, worker_config: TriggerOrchestratorWorkerGlobalConfig
    ) -> int:
        scheduled_event_store = build_scheduled_internal_event_store(worker_config)
        # Keep overdue backlogs from monopolizing a cycle. Remaining due events
        # already force the next cycle's sleep to zero through next_pending_run_at.
        due_ids = scheduled_event_store.list_due_ids(
            datetime.now(UTC), limit=_SCHEDULED_PUBLICATION_BATCH_SIZE
        )
        if not due_ids:
            return 0

        internal_event_bus = build_internal_event_bus(worker_config)
        published_count = 0
        for schedule_id in due_ids:
            _published_event, created = internal_event_bus.publish_scheduled(
                schedule_id
            )
            published_count += int(created)
        return published_count

    def _configured_triggers(
        self, worker_config: TriggerOrchestratorWorkerGlobalConfig
    ) -> list[TriggerDefinitionDiscriminated]:
        return [
            trigger
            for trigger in worker_config.triggers
            if not _is_system_trigger(trigger) and workflow_node_is_enabled(trigger)
        ]

    def _load_configured_providers(
        self,
        worker_config: TriggerOrchestratorWorkerGlobalConfig,
        triggers: list[TriggerDefinitionDiscriminated],
    ) -> dict[str, Any]:
        node_graph_metadata_by_id = _resolve_node_graph_metadata(worker_config)
        providers: dict[str, Any] = {}
        for trigger in triggers:
            try:
                providers[trigger.id] = self.provider_factory.build(
                    trigger,
                    directory_path=_effective_trigger_directory_path(
                        trigger, node_graph_metadata_by_id
                    ),
                )
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


def _acknowledge_provider_event_published(provider: Any | None, event: TriggerEvent) -> None:
    acknowledge = getattr(provider, "acknowledge_published_event", None)
    if not callable(acknowledge):
        return
    try:
        acknowledge(event)
    except Exception:
        logger.exception(
            "Trigger provider delivery acknowledgement failed trigger_id=%s trigger_type=%s event_id=%s",
            event.trigger_id,
            event.trigger_type,
            event.event_id,
        )


def _is_system_trigger(trigger: TriggerDefinitionDiscriminated) -> bool:
    return str(trigger.type or "").startswith("trigger:system:")


def _resolve_node_graph_metadata(
    worker_config: TriggerOrchestratorWorkerGlobalConfig,
) -> dict[str, ResolvedWorkflowGraphMetadata]:
    nodes_by_id = {
        node.id: node
        for node in (
            *worker_config.triggers,
            *worker_config.gates,
            *worker_config.effects,
            *worker_config.queries,
        )
    }
    return resolve_workflow_graph_metadata(
        nodes_by_id=nodes_by_id,
        edges=worker_config.edges,
        workflow_graphs=worker_config.workflow_graphs,
    )


def _effective_trigger_directory_path(
    trigger: TriggerDefinitionDiscriminated,
    node_graph_metadata_by_id: dict[str, ResolvedWorkflowGraphMetadata],
) -> str | None:
    explicit_directory_path = str(getattr(trigger, "directory_path", "") or "").strip()
    if explicit_directory_path:
        return explicit_directory_path
    graph_metadata = node_graph_metadata_by_id.get(trigger.id)
    if graph_metadata is not None:
        return graph_metadata.directory_path
    return None


def _build_trigger_event_queue(
    worker_config: TriggerOrchestratorWorkerGlobalConfig,
) -> SqliteTriggerEventQueue:
    return SqliteTriggerEventQueue(
        worker_config.orchestration_store_path,
        retention_days=worker_config.orchestration_retention_days,
        compact_history_retention_days=(worker_config.orchestration_compact_history_retention_days),
    )


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
        project_dir=resolved_project_dir,
    )


def main() -> None:
    import argparse

    from evander.bootstrap import load_runtime_gateway_config

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


def _compute_trigger_cycle_sleep_seconds(
    *,
    events_published: int,
    poll_interval_seconds: float,
    jitter_seconds: float,
    next_scheduled_run_at: datetime | None = None,
    now: datetime | None = None,
) -> float:
    if events_published > 0:
        return 0.0
    poll_sleep_seconds = _compute_next_sleep_seconds(
        poll_interval_seconds=poll_interval_seconds,
        jitter_seconds=jitter_seconds,
    )
    if next_scheduled_run_at is None:
        return poll_sleep_seconds
    normalized_run_at = (
        next_scheduled_run_at.replace(tzinfo=UTC)
        if next_scheduled_run_at.tzinfo is None
        else next_scheduled_run_at.astimezone(UTC)
    )
    effective_now = now or datetime.now(UTC)
    normalized_now = (
        effective_now.replace(tzinfo=UTC)
        if effective_now.tzinfo is None
        else effective_now.astimezone(UTC)
    )
    scheduled_sleep_seconds = max(0.0, (normalized_run_at - normalized_now).total_seconds())
    return min(poll_sleep_seconds, scheduled_sleep_seconds)


def _compute_backoff_sleep_seconds(*, poll_interval_seconds: float, jitter_seconds: float) -> float:
    delay = max(1.0, poll_interval_seconds)
    if jitter_seconds > 0:
        delay += random.uniform(0.0, jitter_seconds)
    return max(1.0, delay)
