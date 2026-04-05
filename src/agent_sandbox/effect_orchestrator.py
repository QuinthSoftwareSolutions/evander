from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any

from pydantic import BaseModel, Field

from .admin_service import AdminService
from .config import GatewayConfig, TriggerOrchestratorWorkerGlobalConfig
from .effects.binding import BindingResolutionError, resolve_effect_config_bindings
from .effects.registry import EffectProviderFactory
from .events.models import EffectDefinitionDiscriminated, TriggerDefinitionDiscriminated, TriggerEvent
from .host_heartbeat_worker import _single_instance_lock
from .provider_config import ProviderConfigResolver
from .runtime import RuntimeContext, build_runtime
from .triggers.queue import TriggerEventQueue, TriggerQueueRecord

logger = logging.getLogger(__name__)

_MAX_GRAPH_DEPTH = 16
_MAX_EVENTS_PER_CYCLE = 200


class EffectExecutionRecord(BaseModel):
    execution_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    source_node_id: str = Field(min_length=1)
    source_trigger_type: str = Field(min_length=1)
    effect_id: str = Field(min_length=1)
    effect_type: str = Field(min_length=1)
    status: str = Field(min_length=1)
    started_at: datetime
    finished_at: datetime
    resolved_config_hash: str = Field(min_length=1)
    resolved_config: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    published_event_id: str | None = None
    error: str | None = None
    completion_event_enqueued_at: datetime | None = None


@dataclass(frozen=True)
class EffectOrchestratorCycleResult:
    pending_events_scanned: int
    events_processed: int
    effect_executions: int
    completion_events_published: int
    next_sleep_seconds: float


class EffectOrchestratorWorker:
    def __init__(
        self,
        *,
        config: GatewayConfig,
        worker_config: TriggerOrchestratorWorkerGlobalConfig,
        runtime: RuntimeContext,
    ) -> None:
        self.config = config
        self.worker_config = worker_config
        self.runtime = runtime
        self.event_queue = TriggerEventQueue(worker_config.event_queue_store_path)
        self.execution_store_path = worker_config.effect_execution_store_path
        self.execution_store = JsonExecutionStore(self.execution_store_path)
        self.cursor_path = worker_config.effect_cursor_store_path
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, min(32, max(1, (os.cpu_count() or 1) * 4))),
            thread_name_prefix="effect-orchestrator",
        )
        self._inflight: dict[str, Future[dict[str, Any]]] = {}
        self._inflight_lock = RLock()
        self.provider_factory = EffectProviderFactory(
            ProviderConfigResolver(config),
            admin_service=AdminService(config.runtime_dir),
            env=dict(os.environ),
        )

    def run_forever(self) -> None:
        with _single_instance_lock(self.worker_config.effect_lock_path):
            logger.info(
                "Effect orchestrator started poll_interval=%.3f jitter=%.3f lock=%s",
                self.worker_config.poll_interval_seconds,
                self.worker_config.jitter_seconds,
                self.worker_config.effect_lock_path,
            )
            while True:
                try:
                    result = self.run_once()
                except Exception:
                    logger.exception("Effect orchestrator cycle failed")
                    time.sleep(
                        _compute_backoff_sleep_seconds(
                            poll_interval_seconds=self.worker_config.poll_interval_seconds,
                            jitter_seconds=self.worker_config.jitter_seconds,
                        )
                    )
                    continue
                logger.debug(
                    "Effect orchestrator cycle complete pending=%d processed=%d executed=%d published=%d sleep_seconds=%.3f",
                    result.pending_events_scanned,
                    result.events_processed,
                    result.effect_executions,
                    result.completion_events_published,
                    result.next_sleep_seconds,
                )
                time.sleep(result.next_sleep_seconds)

    def run_once_with_lock(self) -> EffectOrchestratorCycleResult:
        with _single_instance_lock(self.worker_config.effect_lock_path):
            return self.run_once()

    def run_once(self) -> EffectOrchestratorCycleResult:
        cursor_payload = _load_cursor_payload(self.cursor_path)
        pending_events_scanned = 0
        events_processed = 0
        effect_executions_started = 0
        effect_executions_completed = 0
        completion_events_published = 0

        indexed_effects = _index_effects_by_upstream(self.worker_config.effects)
        node_outputs = _index_declared_outputs(self.worker_config.triggers, self.worker_config.effects)

        completed_count, published_count = self._reap_completed_futures()
        effect_executions_completed += completed_count
        completion_events_published += published_count

        while events_processed < _MAX_EVENTS_PER_CYCLE:
            pending = self.event_queue.list_pending(limit=1)
            if not pending:
                break
            pending_events_scanned += 1
            queue_record = pending[0]
            execution_count = self._process_event_record(
                queue_record,
                indexed_effects=indexed_effects,
                node_outputs=node_outputs,
            )
            effect_executions_started += execution_count
            events_processed += 1
            self.event_queue.mark_delivered(queue_record.event_id)
            _update_cursor_payload(cursor_payload, queue_record)

        _save_cursor_payload(self.cursor_path, cursor_payload)
        next_sleep_seconds = _compute_next_sleep_seconds(
            poll_interval_seconds=self.worker_config.poll_interval_seconds,
            jitter_seconds=self.worker_config.jitter_seconds,
        )

        return EffectOrchestratorCycleResult(
            pending_events_scanned=pending_events_scanned,
            events_processed=events_processed,
            effect_executions=effect_executions_started + effect_executions_completed,
            completion_events_published=completion_events_published,
            next_sleep_seconds=next_sleep_seconds,
        )

    def _process_event_record(
        self,
        queue_record: TriggerQueueRecord,
        *,
        indexed_effects: dict[str, list[EffectDefinitionDiscriminated]],
        node_outputs: dict[str, set[str]],
    ) -> int:
        source_node_id = queue_record.trigger_id
        downstream_effects = indexed_effects.get(source_node_id, [])
        if not downstream_effects:
            return 0

        execution_count = 0
        for effect in downstream_effects:
            if _would_form_cycle(source_node_id=source_node_id, effect=effect):
                self._write_skipped_record(
                    queue_record=queue_record,
                    effect=effect,
                    reason="cycle_detected",
                )
                continue

            depth = _compute_depth(effect, indexed_effects)
            if depth > _MAX_GRAPH_DEPTH:
                self._write_skipped_record(
                    queue_record=queue_record,
                    effect=effect,
                    reason=f"max_depth_exceeded:{depth}",
                )
                continue

            if self._dispatch_effect(queue_record, effect=effect, node_outputs=node_outputs):
                execution_count += 1

        return execution_count

    def _dispatch_effect(
        self,
        queue_record: TriggerQueueRecord,
        *,
        effect: EffectDefinitionDiscriminated,
        node_outputs: dict[str, set[str]],
    ) -> bool:
        idempotency_key = _execution_idempotency_key(queue_record.event_id, effect.id)
        existing = self.execution_store.find_by_idempotency_key(idempotency_key)
        if existing is not None:
            return False
        with self._inflight_lock:
            if idempotency_key in self._inflight:
                return False

        started_at = datetime.now(UTC)
        upstream_output_names = node_outputs.get(effect.upstream_id, set()) or set(queue_record.payload.keys())
        raw_effect_config = _extract_effect_config(effect)
        try:
            resolved = resolve_effect_config_bindings(
                raw_effect_config,
                upstream_payload=queue_record.payload,
                declared_upstream_output_names=upstream_output_names,
                strict_validation=True,
            )
        except BindingResolutionError as exc:
            self._write_failed_record(
                queue_record=queue_record,
                effect=effect,
                started_at=started_at,
                resolved_config={},
                error=str(exc),
            )
            return False
        resolved_config = resolved.resolved_config if isinstance(resolved.resolved_config, dict) else {}
        resolved_config_hash = _json_sha256(resolved_config)
        record = EffectExecutionRecord(
            execution_id=f"exec-{hashlib.sha1(idempotency_key.encode('utf-8')).hexdigest()[:16]}",
            idempotency_key=idempotency_key,
            event_id=queue_record.event_id,
            source_node_id=queue_record.trigger_id,
            source_trigger_type=queue_record.trigger_type,
            effect_id=effect.id,
            effect_type=effect.type,
            status="running",
            started_at=started_at,
            finished_at=started_at,
            resolved_config_hash=resolved_config_hash,
            resolved_config=resolved_config,
            outputs={},
        )
        self.execution_store.put(record)
        future = self._executor.submit(self._execute_provider, effect=effect, resolved_config=resolved_config)
        with self._inflight_lock:
            self._inflight[idempotency_key] = future
        return True

    def _execute_provider(self, *, effect: EffectDefinitionDiscriminated, resolved_config: dict[str, Any]) -> dict[str, Any]:
        provider = self.provider_factory.build(effect)
        return dict(provider.execute(resolved_config=resolved_config) or {})

    def _reap_completed_futures(self) -> tuple[int, int]:
        completed_count = 0
        published_count = 0
        to_finalize: list[tuple[str, Future[dict[str, Any]]]] = []
        with self._inflight_lock:
            for idempotency_key, future in list(self._inflight.items()):
                if future.done():
                    to_finalize.append((idempotency_key, future))
                    self._inflight.pop(idempotency_key, None)

        for idempotency_key, future in to_finalize:
            completed_count += 1
            record = self.execution_store.find_by_idempotency_key(idempotency_key)
            if record is None:
                continue
            finished_at = datetime.now(UTC)
            try:
                outputs = dict(future.result() or {})
                published_event_id = self._publish_completion_event_from_record(record=record, outputs=outputs)
                record.status = "succeeded"
                record.outputs = outputs
                record.published_event_id = published_event_id
                record.completion_event_enqueued_at = finished_at
                published_count += 1
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "Effect execution failed event_id=%s effect_id=%s effect_type=%s",
                    record.event_id,
                    record.effect_id,
                    record.effect_type,
                )
                record.status = "failed"
                record.error = str(exc)
            record.finished_at = finished_at
            self.execution_store.put(record)

        return completed_count, published_count

    def _publish_completion_event_from_record(
        self,
        *,
        record: EffectExecutionRecord,
        outputs: dict[str, Any],
    ) -> str:
        completion_event_id = _completion_event_id(parent_event_id=record.event_id, effect_id=record.effect_id)
        completion_event = TriggerEvent(
            event_id=completion_event_id,
            trigger_id=record.effect_id,
            trigger_type=record.source_trigger_type,
            occurred_at=datetime.now(UTC),
            payload=outputs,
        )
        self.event_queue.publish(completion_event, idempotency_key=record.idempotency_key)
        return completion_event_id

    def _write_skipped_record(self, *, queue_record: TriggerQueueRecord, effect: EffectDefinitionDiscriminated, reason: str) -> None:
        self._write_terminal_record(
            queue_record=queue_record,
            effect=effect,
            status="skipped",
            started_at=datetime.now(UTC),
            resolved_config={},
            error=reason,
        )

    def _write_failed_record(
        self,
        *,
        queue_record: TriggerQueueRecord,
        effect: EffectDefinitionDiscriminated,
        started_at: datetime,
        resolved_config: dict[str, Any],
        error: str,
    ) -> None:
        self._write_terminal_record(
            queue_record=queue_record,
            effect=effect,
            status="failed",
            started_at=started_at,
            resolved_config=resolved_config,
            error=error,
        )

    def _write_terminal_record(
        self,
        *,
        queue_record: TriggerQueueRecord,
        effect: EffectDefinitionDiscriminated,
        status: str,
        started_at: datetime,
        resolved_config: dict[str, Any],
        error: str,
    ) -> None:
        idempotency_key = _execution_idempotency_key(queue_record.event_id, effect.id)
        if self.execution_store.find_by_idempotency_key(idempotency_key) is not None:
            return
        finished_at = datetime.now(UTC)
        record = EffectExecutionRecord(
            execution_id=f"exec-{hashlib.sha1(idempotency_key.encode('utf-8')).hexdigest()[:16]}",
            idempotency_key=idempotency_key,
            event_id=queue_record.event_id,
            source_node_id=queue_record.trigger_id,
            source_trigger_type=queue_record.trigger_type,
            effect_id=effect.id,
            effect_type=effect.type,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            resolved_config_hash=_json_sha256(resolved_config),
            resolved_config=resolved_config,
            outputs={},
            error=error,
        )
        self.execution_store.put(record)


class JsonExecutionStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("{}", encoding="utf-8")

    def read_all(self) -> dict[str, EffectExecutionRecord]:
        with self._lock:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return {}
            return {key: EffectExecutionRecord.model_validate(value) for key, value in payload.items()}

    def put(self, record: EffectExecutionRecord) -> None:
        with self._lock:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                payload = {}
            payload[record.execution_id] = record.model_dump(mode="json")
            self.path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def find_by_idempotency_key(self, idempotency_key: str) -> EffectExecutionRecord | None:
        with self._lock:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return None
            for value in payload.values():
                record = EffectExecutionRecord.model_validate(value)
                if record.idempotency_key == idempotency_key:
                    return record
        return None


def build_effect_orchestrator_worker(
    config: GatewayConfig,
    *,
    project_dir: Path | None = None,
) -> EffectOrchestratorWorker:
    resolved_project_dir = Path(project_dir) if project_dir is not None else Path.cwd()
    worker_config = _load_trigger_orchestrator_worker_global_config(resolved_project_dir, base_config=config)
    effective_config = GatewayConfig.model_validate(
        {
            **config.model_dump(mode="json"),
            "runtime_dir": worker_config.runtime_dir,
        }
    )
    runtime = build_runtime(effective_config)
    return EffectOrchestratorWorker(
        config=effective_config,
        worker_config=worker_config,
        runtime=runtime,
    )


def main() -> None:
    import argparse

    from .bootstrap import load_runtime_gateway_config

    parser = argparse.ArgumentParser(description="Run the effect orchestrator worker.")
    parser.add_argument("--runtime-dir", default="runtime")
    parser.add_argument("--log-level", default=os.environ.get("EFFECT_ORCHESTRATOR_LOG_LEVEL", "INFO"))
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    logging_level = getattr(logging, str(args.log_level).upper(), None)
    if not isinstance(logging_level, int):
        raise SystemExit(f"Invalid --log-level value: {args.log_level}")
    logging.basicConfig(level=logging_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    runtime_dir = Path(args.runtime_dir).expanduser().resolve()
    config = load_runtime_gateway_config(runtime_dir=runtime_dir)
    worker = build_effect_orchestrator_worker(config, project_dir=runtime_dir.parent)
    if args.once:
        result = worker.run_once_with_lock()
        print(
            f"Effect orchestrator cycle complete pending={result.pending_events_scanned} "
            f"processed={result.events_processed} executed={result.effect_executions} "
            f"published={result.completion_events_published} sleep_seconds={result.next_sleep_seconds:.3f}"
        )
        return
    worker.run_forever()


def _index_effects_by_upstream(effects: list[EffectDefinitionDiscriminated]) -> dict[str, list[EffectDefinitionDiscriminated]]:
    indexed: dict[str, list[EffectDefinitionDiscriminated]] = {}
    for effect in effects:
        indexed.setdefault(effect.upstream_id, []).append(effect)
    for nodes in indexed.values():
        nodes.sort(key=lambda effect: effect.id)
    return indexed


def _index_declared_outputs(
    triggers: list[TriggerDefinitionDiscriminated],
    effects: list[EffectDefinitionDiscriminated],
) -> dict[str, set[str]]:
    outputs: dict[str, set[str]] = {}
    for node in [*triggers, *effects]:
        declared = set((node.outputs or {}).keys())
        outputs[node.id] = declared
    return outputs


def _extract_effect_config(effect: EffectDefinitionDiscriminated) -> dict[str, Any]:
    payload = effect.model_dump(mode="json")
    for field_name in ("id", "display_name", "type", "upstream_id", "outputs"):
        payload.pop(field_name, None)
    return payload


def _execution_idempotency_key(event_id: str, effect_id: str) -> str:
    return f"{event_id}::{effect_id}"


def _completion_event_id(*, parent_event_id: str, effect_id: str) -> str:
    return f"effect:{effect_id}:{hashlib.sha1(parent_event_id.encode('utf-8')).hexdigest()[:12]}"


def _json_sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _compute_depth(
    effect: EffectDefinitionDiscriminated,
    indexed_effects: dict[str, list[EffectDefinitionDiscriminated]],
    *,
    _seen: set[str] | None = None,
) -> int:
    seen = set(_seen or set())
    if effect.id in seen:
        return _MAX_GRAPH_DEPTH + 1
    seen.add(effect.id)
    downstream = indexed_effects.get(effect.id, [])
    if not downstream:
        return 1
    return 1 + max(_compute_depth(node, indexed_effects, _seen=seen) for node in downstream)


def _would_form_cycle(*, source_node_id: str, effect: EffectDefinitionDiscriminated) -> bool:
    return source_node_id == effect.id or effect.upstream_id == effect.id


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


def _load_trigger_orchestrator_worker_global_config(
    project_dir: Path,
    *,
    base_config: GatewayConfig | None = None,
) -> TriggerOrchestratorWorkerGlobalConfig:
    base_payload = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": (base_config.runtime_dir if base_config is not None else Path("runtime")),
        }
    ).model_dump(mode="json")
    path = project_dir / "config" / "trigger-orchestrator" / "config.json"
    if not path.exists():
        return TriggerOrchestratorWorkerGlobalConfig.model_validate(base_payload)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"worker config file {path} must contain a JSON object")
    return TriggerOrchestratorWorkerGlobalConfig.model_validate({**base_payload, **raw})


def _load_cursor_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Unable to read effect orchestrator cursor state path=%s", path, exc_info=True)
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _save_cursor_payload(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)


def _update_cursor_payload(payload: dict[str, Any], queue_record: TriggerQueueRecord) -> None:
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    payload["last_processed_event_id"] = queue_record.event_id
    payload["last_processed_source_node_id"] = queue_record.trigger_id
    payload["last_processed_at"] = now
