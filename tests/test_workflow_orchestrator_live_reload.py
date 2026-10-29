from __future__ import annotations

from config_helpers import canonical_worker_config, canonical_workflow_payload

import importlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from textwrap import dedent

from evander.admin.composition import AdminRuntimeComponents, build_admin_runtime
from evander.config import GatewayConfig, TriggerOrchestratorWorkerGlobalConfig
from evander.daemons.effect_orchestrator import (
    EffectOrchestratorWorker,
    build_effect_orchestrator_worker,
)
from evander.daemons.trigger_orchestrator import build_trigger_orchestrator_worker
from evander.events.models import TriggerEvent
from evander.workflow.system_nodes import is_system_managed_workflow_node_id


class _QueuedTriggerProvider:
    def __init__(self, trigger_id: str, event_queue: dict[str, list[TriggerEvent]]) -> None:
        self._trigger_id = trigger_id
        self._event_queue = event_queue

    def poll(self) -> list[TriggerEvent]:
        queued = self._event_queue.get(self._trigger_id, [])
        self._event_queue[self._trigger_id] = []
        return list(queued)


class _RecordingEffectProvider:
    def __init__(self, effect_id: str, calls: list[str]) -> None:
        self._effect_id = effect_id
        self._calls = calls

    def execute(self, *, resolved_config=None):
        self._calls.append(self._effect_id)
        return {"success": True, "effect_id": self._effect_id}


class _BlockingRecordingEffectProvider(_RecordingEffectProvider):
    def __init__(self, effect_id: str, calls: list[str], *, wait_on: Event | None = None) -> None:
        super().__init__(effect_id, calls)
        self._wait_on = wait_on

    def execute(self, *, resolved_config=None):
        if self._wait_on is not None:
            self._wait_on.wait(timeout=2)
        return super().execute(resolved_config=resolved_config)


class _SequenceConfigReloader:
    def __init__(self, *configs: TriggerOrchestratorWorkerGlobalConfig) -> None:
        self._configs = list(configs)
        self._index = 0

    def get_latest(self) -> tuple[TriggerOrchestratorWorkerGlobalConfig, bool]:
        if self._index < len(self._configs):
            config = self._configs[self._index]
            self._index += 1
        else:
            config = self._configs[-1]
        return config, self._index > 1


def _reload_worker_config(
    runtime_dir: Path,
    *,
    orchestration_store_path: Path | None = None,
    effect_cursor_store_path: Path | None = None,
    effect_lock_path: Path | None = None,
    provider_modules: list[str] | None = None,
    triggers: list[dict[str, object]] | None = None,
    gates: list[dict[str, object]] | None = None,
    effects: list[dict[str, object]] | None = None,
    queries: list[dict[str, object]] | None = None,
    edges: list[dict[str, object]] | None = None,
) -> TriggerOrchestratorWorkerGlobalConfig:
    return canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            **(
                {"orchestration_store_path": orchestration_store_path}
                if orchestration_store_path is not None
                else {}
            ),
            **(
                {"effect_cursor_store_path": effect_cursor_store_path}
                if effect_cursor_store_path is not None
                else {}
            ),
            **({"effect_lock_path": effect_lock_path} if effect_lock_path is not None else {}),
            "provider_loader": {
                "provider_entry_points_enabled": False,
                "provider_modules": provider_modules or [],
            },
            "triggers": triggers or [],
            "gates": gates or [],
            "effects": effects or [],
            "queries": queries or [],
            "edges": edges or [],
        }
    )


def _save_worker_config(
    project_dir: Path, worker_config: TriggerOrchestratorWorkerGlobalConfig
) -> None:
    _workflow_config_path(project_dir).parent.mkdir(parents=True, exist_ok=True)
    _workflow_config_path(project_dir).write_text(
        json.dumps(worker_config.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _write_reload_provider_module(
    module_root: Path, module_name: str = "workflow_reload_plugin"
) -> str:
    module_path = module_root / f"{module_name}.py"
    module_path.write_text(
        dedent(
            """
            from __future__ import annotations

            from evander.events.models import TriggerEvent
            from evander.effects.providers.base import EffectProviderBase
            from evander.gates.providers.base import GateProviderBase
            from evander.queries.providers.base import QueryProviderBase
            from evander.triggers.providers.base import TriggerProviderBase
            from evander.workflow.plugins import ProviderPluginRegistration

            TRIGGER_EVENT_QUEUE: dict[str, list[TriggerEvent]] = {}
            TRIGGER_POLLS: list[str] = []
            EFFECT_CALLS: list[str] = []
            QUERY_CALLS: list[str] = []
            GATE_CALLS: list[str] = []

            class CustomTriggerProvider(TriggerProviderBase):
                def poll(self) -> list[TriggerEvent]:
                    trigger_id = self.trigger_definition.id
                    TRIGGER_POLLS.append(trigger_id)
                    queued = TRIGGER_EVENT_QUEUE.get(trigger_id, [])
                    TRIGGER_EVENT_QUEUE[trigger_id] = []
                    return list(queued)

            class CustomEffectProvider(EffectProviderBase):
                def execute(self, *, resolved_config=None):
                    del resolved_config
                    EFFECT_CALLS.append(self.effect_definition.id)
                    return {"success": True, "effect_id": self.effect_definition.id}

            class CustomQueryProvider(QueryProviderBase):
                def execute(self, *, resolved_config=None):
                    del resolved_config
                    QUERY_CALLS.append(self.query_definition.id)
                    return {"success": True, "query_id": self.query_definition.id}

            class CustomGateProvider(GateProviderBase):
                def execute(self, **kwargs):
                    worker = kwargs["worker"]
                    queue_record = kwargs["queue_record"]
                    gate = kwargs["gate"]
                    started_at = kwargs["started_at"]
                    config_revision = kwargs["config_revision"]
                    inputs_by_source = kwargs["inputs_by_source"]
                    input_event_ids_by_source = kwargs["input_event_ids_by_source"]
                    input_occurred_at_by_source = kwargs["input_occurred_at_by_source"]

                    GATE_CALLS.append(gate.id)
                    worker._write_gate_record(
                        queue_record=queue_record,
                        gate=gate,
                        status="succeeded",
                        started_at=started_at,
                        resolved_config={"gate_id": gate.id},
                        outputs={"gate_id": gate.id},
                        inputs_by_source=inputs_by_source,
                        input_event_ids_by_source=input_event_ids_by_source,
                        input_occurred_at_by_source=input_occurred_at_by_source,
                        config_revision=config_revision,
                        publish_completion_event=True,
                    )
                    return True

            def register_providers():
                return ProviderPluginRegistration(
                    trigger_providers={"trigger:custom:reload": CustomTriggerProvider},
                    gate_providers={"gate:custom:reload": CustomGateProvider},
                    effect_providers={"effect:custom:reload": CustomEffectProvider},
                    query_providers={"query:custom:reload": CustomQueryProvider},
                )
            """
        ),
        encoding="utf-8",
    )
    return module_name


def _workflow_config_path(project_dir: Path) -> Path:
    return project_dir / "config" / "trigger-orchestrator" / "config.json"


def _operator_trigger_ids(worker) -> list[str]:
    return [
        trigger.id
        for trigger in worker.worker_config.triggers
        if not is_system_managed_workflow_node_id(trigger.id)
    ]


def _save_workflow(
    service: AdminRuntimeComponents,
    *,
    runtime_dir: Path,
    trigger_id: str,
    effect_id: str,
) -> None:
    service.workflow_config_admin.save_workflow_editor_document(
        config=canonical_workflow_payload(
            {
                "runtime_dir": str(runtime_dir),
                "poll_interval_seconds": 1,
                "jitter_seconds": 0,
                "triggers": [
                    {
                        "id": trigger_id,
                        "display_name": f"Trigger {trigger_id}",
                        "type": "trigger:cron",
                        "expression": "* * * * *",
                    }
                ],
                "effects": [
                    {
                        "id": effect_id,
                        "display_name": f"Effect {effect_id}",
                        "type": "effect:agent:prompt",
                        "prompt": "run",
                    }
                ],
                "edges": [{"source_id": trigger_id, "target_id": effect_id}],
            }
        ),
        layout={"nodes": {trigger_id: {"x": 0, "y": 0}, effect_id: {"x": 250, "y": 0}}},
    )


def test_workers_reload_saved_workflow_without_restarting_objects(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)

    trigger_worker = build_trigger_orchestrator_worker(config, project_dir=tmp_path)
    effect_worker = build_effect_orchestrator_worker(config, project_dir=tmp_path)

    trigger_events: dict[str, list[TriggerEvent]] = {}
    executed_effect_ids: list[str] = []
    trigger_worker.provider_factory.build = lambda trigger, **_: _QueuedTriggerProvider(
        trigger.id, trigger_events
    )  # type: ignore[assignment]
    effect_worker.provider_factory.build = lambda effect: _RecordingEffectProvider(
        effect.id, executed_effect_ids
    )  # type: ignore[assignment]

    trigger_worker_id = id(trigger_worker)
    effect_worker_id = id(effect_worker)
    trigger_runtime_id = id(trigger_worker.runtime)
    effect_runtime_id = id(effect_worker.runtime)

    service = build_admin_runtime(runtime_dir)

    _save_workflow(
        service, runtime_dir=runtime_dir, trigger_id="trigger-old", effect_id="effect-old"
    )
    trigger_events["trigger-old"] = [
        TriggerEvent(
            event_id="event-old-1",
            trigger_id="trigger-old",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc),
            payload={"version": "old"},
        )
    ]

    first_trigger_cycle = trigger_worker.run_once()
    first_effect_cycle = effect_worker.run_once()

    assert _operator_trigger_ids(trigger_worker) == ["trigger-old"]
    assert first_trigger_cycle.events_published == 1
    assert first_effect_cycle.effect_executions == 1
    assert executed_effect_ids == ["effect-old"]

    _save_workflow(
        service, runtime_dir=runtime_dir, trigger_id="trigger-new", effect_id="effect-new"
    )
    trigger_events["trigger-old"] = [
        TriggerEvent(
            event_id="event-old-2",
            trigger_id="trigger-old",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 19, 12, 1, tzinfo=timezone.utc),
            payload={"version": "old"},
        )
    ]
    trigger_events["trigger-new"] = [
        TriggerEvent(
            event_id="event-new-1",
            trigger_id="trigger-new",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 19, 12, 2, tzinfo=timezone.utc),
            payload={"version": "new"},
        )
    ]

    second_trigger_cycle = trigger_worker.run_once()
    second_effect_cycle = effect_worker.run_once()

    assert _operator_trigger_ids(trigger_worker) == ["trigger-new"]
    assert second_trigger_cycle.events_published == 1
    assert second_effect_cycle.effect_executions >= 1
    assert executed_effect_ids == ["effect-old", "effect-new"]
    assert trigger_worker.worker_config.triggers[0].id == "trigger-new"
    assert effect_worker.worker_config.effects[0].id == "effect-new"

    assert id(trigger_worker) == trigger_worker_id
    assert id(effect_worker) == effect_worker_id
    assert id(trigger_worker.runtime) == trigger_runtime_id
    assert id(effect_worker.runtime) == effect_runtime_id


def test_trigger_worker_reloads_custom_provider_module(tmp_path: Path, monkeypatch) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    initial_config = _reload_worker_config(runtime_dir)
    _save_worker_config(tmp_path, initial_config)

    module_name = _write_reload_provider_module(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    sys.modules.pop(module_name, None)

    trigger_worker = build_trigger_orchestrator_worker(config, project_dir=tmp_path)
    initial_provider_factory = trigger_worker.provider_factory

    first_cycle = trigger_worker.run_once()
    assert _operator_trigger_ids(trigger_worker) == []
    assert first_cycle.events_published == 0

    updated_config = _reload_worker_config(
        runtime_dir,
        provider_modules=[module_name],
        triggers=[
            {
                "id": "reload-trigger",
                "display_name": "Reloaded custom trigger",
                "type": "trigger:custom:reload",
                "expression": "* * * * *",
            }
        ],
    )
    _save_worker_config(tmp_path, updated_config)
    module = importlib.import_module(module_name)
    module.TRIGGER_EVENT_QUEUE["reload-trigger"] = [
        TriggerEvent(
            event_id="reload-event-1",
            trigger_id="reload-trigger",
            trigger_type="trigger:custom:reload",
            occurred_at=datetime(2026, 4, 19, 15, 0, tzinfo=timezone.utc),
            payload={"kind": "reload"},
        )
    ]

    second_cycle = trigger_worker.run_once()

    assert _operator_trigger_ids(trigger_worker) == ["reload-trigger"]
    assert second_cycle.events_published == 1
    assert trigger_worker.provider_factory is not initial_provider_factory
    assert trigger_worker.worker_config.provider_modules == [module_name]
    assert trigger_worker.worker_config.triggers[0].type == "trigger:custom:reload"
    assert module.TRIGGER_POLLS == ["reload-trigger"]
    pending_events = trigger_worker.event_queue.list_pending()
    assert [event.event_id for event in pending_events] == ["reload-event-1"]
    assert pending_events[0].trigger_type == "trigger:custom:reload"


def test_effect_worker_reloads_custom_provider_module(tmp_path: Path, monkeypatch) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    initial_config = _reload_worker_config(runtime_dir)
    _save_worker_config(tmp_path, initial_config)

    module_name = _write_reload_provider_module(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    sys.modules.pop(module_name, None)

    effect_worker = build_effect_orchestrator_worker(config, project_dir=tmp_path)
    initial_provider_factory = effect_worker.provider_factory
    initial_query_provider_factory = effect_worker.query_provider_factory
    initial_gate_provider_factory = effect_worker.gate_provider_factory

    first_cycle = effect_worker.run_once()
    assert first_cycle.events_processed == 0

    updated_config = _reload_worker_config(
        runtime_dir,
        provider_modules=[module_name],
        triggers=[
            {
                "id": "source-trigger",
                "display_name": "Source trigger",
                "type": "trigger:custom:reload",
                "expression": "* * * * *",
            }
        ],
        gates=[
            {
                "id": "custom-gate",
                "display_name": "Custom gate",
                "type": "gate:custom:reload",
            }
        ],
        effects=[
            {
                "id": "custom-effect",
                "display_name": "Custom effect",
                "type": "effect:custom:reload",
            }
        ],
        queries=[
            {
                "id": "custom-query",
                "display_name": "Custom query",
                "type": "query:custom:reload",
            }
        ],
        edges=[
            {"source_id": "source-trigger", "target_id": "custom-gate"},
            {"source_id": "custom-gate", "target_id": "custom-effect"},
            {"source_id": "custom-gate", "target_id": "custom-query"},
        ],
    )
    _save_worker_config(tmp_path, updated_config)
    module = importlib.import_module(module_name)
    effect_worker.event_queue.publish(
        TriggerEvent(
            event_id="source-event-1",
            trigger_id="source-trigger",
            trigger_type="trigger:custom:reload",
            occurred_at=datetime(2026, 4, 19, 16, 0, tzinfo=timezone.utc),
            payload={"kind": "source"},
        )
    )

    second_cycle = effect_worker.run_once()
    effect_worker._wait_for_inflight_completion(timeout_seconds=2)
    effect_worker.run_once()

    assert second_cycle.events_processed >= 1
    assert effect_worker.provider_factory is not initial_provider_factory
    assert effect_worker.query_provider_factory is not initial_query_provider_factory
    assert effect_worker.gate_provider_factory is not initial_gate_provider_factory
    assert effect_worker.worker_config.provider_modules == [module_name]
    assert module.GATE_CALLS == ["custom-gate"]
    assert module.EFFECT_CALLS == ["custom-effect"]
    assert module.QUERY_CALLS == ["custom-query"]

    gate_records = effect_worker.gate_execution_store.list_executions()
    effect_records = effect_worker.execution_store.list_executions()
    assert any(
        record.gate_type == "gate:custom:reload" and record.status == "succeeded"
        for record in gate_records
    )
    assert any(
        record.effect_type == "effect:custom:reload" and record.status == "succeeded"
        for record in effect_records
    )
    assert any(
        record.effect_type == "query:custom:reload" and record.status == "succeeded"
        for record in effect_records
    )
    assert all("no_gate_provider_registered" not in (record.error or "") for record in gate_records)
    assert all(
        "no_effect_executor_registered" not in (record.error or "") for record in effect_records
    )


def test_effect_worker_reloads_to_new_orchestration_store_without_restart(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    initial_store_path = runtime_dir / "initial-orchestration.db"
    initial_cursor_path = runtime_dir / "initial-effect-cursors.json"
    initial_lock_path = runtime_dir / "initial-effect-orchestrator.lock"
    reloaded_store_path = runtime_dir / "reloaded-orchestration.db"
    reloaded_cursor_path = runtime_dir / "reloaded-effect-cursors.json"

    initial_config = _reload_worker_config(
        runtime_dir,
        orchestration_store_path=initial_store_path,
        effect_cursor_store_path=initial_cursor_path,
        effect_lock_path=initial_lock_path,
    )
    _save_worker_config(tmp_path, initial_config)

    module_name = _write_reload_provider_module(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    sys.modules.pop(module_name, None)

    trigger_worker = build_trigger_orchestrator_worker(config, project_dir=tmp_path)
    effect_worker = build_effect_orchestrator_worker(config, project_dir=tmp_path)

    module = importlib.import_module(module_name)
    initial_trigger_cycle = trigger_worker.run_once()
    initial_effect_cycle = effect_worker.run_once()
    assert initial_trigger_cycle.events_published == 0
    assert initial_effect_cycle.events_processed == 0
    assert trigger_worker.event_queue.path == initial_store_path
    assert effect_worker.event_queue.path == initial_store_path

    updated_config = _reload_worker_config(
        runtime_dir,
        orchestration_store_path=reloaded_store_path,
        effect_cursor_store_path=reloaded_cursor_path,
        provider_modules=[module_name],
        triggers=[
            {
                "id": "reload-trigger",
                "display_name": "Reload trigger",
                "type": "trigger:custom:reload",
                "expression": "* * * * *",
            }
        ],
        effects=[
            {
                "id": "reload-effect",
                "display_name": "Reload effect",
                "type": "effect:custom:reload",
            }
        ],
        edges=[{"source_id": "reload-trigger", "target_id": "reload-effect"}],
    )
    _save_worker_config(tmp_path, updated_config)
    module.TRIGGER_EVENT_QUEUE["reload-trigger"] = [
        TriggerEvent(
            event_id="reload-event-1",
            trigger_id="reload-trigger",
            trigger_type="trigger:custom:reload",
            occurred_at=datetime(2026, 4, 19, 17, 0, tzinfo=timezone.utc),
            payload={"kind": "reload"},
        )
    ]

    trigger_cycle = trigger_worker.run_once()
    effect_cycle = effect_worker.run_once()
    effect_worker._wait_for_inflight_completion(timeout_seconds=2)
    effect_worker.run_once()

    assert trigger_cycle.events_published == 1
    assert effect_cycle.events_processed >= 1
    assert module.TRIGGER_POLLS == ["reload-trigger"]
    assert module.EFFECT_CALLS == ["reload-effect"]
    assert trigger_worker.event_queue.path == reloaded_store_path
    assert effect_worker.event_queue.path == reloaded_store_path
    assert effect_worker.cursor_path == reloaded_cursor_path
    assert trigger_worker.worker_config.orchestration_store_path == reloaded_store_path
    assert effect_worker.worker_config.orchestration_store_path == reloaded_store_path


def test_effect_worker_reloads_wake_sleeper_and_notifier_with_lock_path(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    initial_lock_path = runtime_dir / "initial-effect-orchestrator.lock"
    reloaded_lock_path = runtime_dir / "reloaded-effect-orchestrator.lock"
    initial_config = _reload_worker_config(
        runtime_dir,
        effect_lock_path=initial_lock_path,
    )
    updated_config = _reload_worker_config(
        runtime_dir,
        effect_lock_path=reloaded_lock_path,
    )
    effect_worker = EffectOrchestratorWorker(
        config=GatewayConfig(runtime_dir=runtime_dir),
        worker_config=initial_config,
        config_reloader=_SequenceConfigReloader(initial_config, updated_config),
        runtime=None,  # type: ignore[arg-type]
    )

    effect_worker.run_once()
    assert effect_worker._wake_sleeper.path == initial_lock_path.with_suffix(".wake.sock")
    assert effect_worker._wake_notifier.path == initial_lock_path.with_suffix(".wake.sock")

    effect_worker.run_once()

    expected_wake_path = reloaded_lock_path.with_suffix(".wake.sock")
    assert effect_worker.lock_path == reloaded_lock_path
    assert effect_worker._wake_sleeper.path == expected_wake_path
    assert effect_worker._wake_notifier.path == expected_wake_path
    assert effect_worker._kernel.config.lock_path == reloaded_lock_path


def test_effect_worker_drains_completed_future_before_store_rebind(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    initial_store_path = runtime_dir / "initial-orchestration.db"
    initial_cursor_path = runtime_dir / "initial-effect-cursors.json"
    initial_lock_path = runtime_dir / "initial-effect-orchestrator.lock"
    reloaded_store_path = runtime_dir / "reloaded-orchestration.db"
    reloaded_cursor_path = runtime_dir / "reloaded-effect-cursors.json"

    workflow_nodes = {
        "triggers": [
            {
                "id": "source-trigger",
                "display_name": "Source",
                "type": "trigger:cron",
                "expression": "* * * * *",
            }
        ],
        "effects": [
            {
                "id": "slow-effect",
                "display_name": "Slow effect",
                "type": "effect:agent:prompt",
                "prompt": "slow",
            },
            {
                "id": "downstream-effect",
                "display_name": "Downstream effect",
                "type": "effect:agent:prompt",
                "prompt": "downstream",
            },
        ],
        "edges": [
            {"source_id": "source-trigger", "target_id": "slow-effect"},
            {"source_id": "slow-effect", "target_id": "downstream-effect"},
        ],
    }
    initial_config = _reload_worker_config(
        runtime_dir,
        orchestration_store_path=initial_store_path,
        effect_cursor_store_path=initial_cursor_path,
        effect_lock_path=initial_lock_path,
        **workflow_nodes,
    )
    updated_config = _reload_worker_config(
        runtime_dir,
        orchestration_store_path=reloaded_store_path,
        effect_cursor_store_path=reloaded_cursor_path,
        **workflow_nodes,
    )

    release_slow_effect = Event()
    effect_calls: list[str] = []
    effect_worker = EffectOrchestratorWorker(
        config=config,
        worker_config=initial_config,
        config_reloader=_SequenceConfigReloader(initial_config, updated_config),
        runtime=None,  # type: ignore[arg-type]
    )

    def _build_provider(effect):
        return _BlockingRecordingEffectProvider(
            effect.id,
            effect_calls,
            wait_on=release_slow_effect if effect.id == "slow-effect" else None,
        )

    effect_worker.provider_factory.build = _build_provider  # type: ignore[assignment]
    effect_worker.event_queue.publish(
        TriggerEvent(
            event_id="source-event-1",
            trigger_id="source-trigger",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 19, 18, 0, tzinfo=timezone.utc),
            payload={"kind": "source"},
        )
    )

    first_cycle = effect_worker.run_once()
    assert first_cycle.effect_executions == 1
    assert effect_calls == []

    release_slow_effect.set()
    effect_worker._wait_for_inflight_completion(timeout_seconds=2)
    second_cycle = effect_worker.run_once()

    assert second_cycle.completion_events_published >= 1
    assert effect_worker.event_queue.path == initial_store_path
    initial_queue = effect_worker.event_queue.read_all()
    slow_completion_records = [
        record for record in initial_queue.values() if record.trigger_id == "slow-effect"
    ]
    assert len(slow_completion_records) == 1
    assert slow_completion_records[0].delivery_state == "delivered"

    for _ in range(5):
        initial_queue = effect_worker.event_queue.read_all()
        if "downstream-effect" in effect_calls and all(
            record.delivery_state == "delivered" for record in initial_queue.values()
        ):
            break
        effect_worker._wait_for_inflight_completion(timeout_seconds=2)
        effect_worker.run_once()

    assert effect_calls == ["slow-effect", "downstream-effect"]
    initial_queue = effect_worker.event_queue.read_all()
    assert all(record.delivery_state == "delivered" for record in initial_queue.values())

    for _ in range(5):
        if effect_worker.event_queue.path == reloaded_store_path:
            break
        effect_worker.run_once()

    assert effect_worker.event_queue.path == reloaded_store_path
    assert effect_worker.event_queue.list_pending() == []


def test_workers_keep_last_known_good_config_when_reload_payload_is_invalid(
    tmp_path: Path, caplog
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)

    trigger_worker = build_trigger_orchestrator_worker(config, project_dir=tmp_path)
    effect_worker = build_effect_orchestrator_worker(config, project_dir=tmp_path)

    trigger_events: dict[str, list[TriggerEvent]] = {}
    executed_effect_ids: list[str] = []
    trigger_worker.provider_factory.build = lambda trigger, **_: _QueuedTriggerProvider(
        trigger.id, trigger_events
    )  # type: ignore[assignment]
    effect_worker.provider_factory.build = lambda effect: _RecordingEffectProvider(
        effect.id, executed_effect_ids
    )  # type: ignore[assignment]

    service = build_admin_runtime(runtime_dir)
    _save_workflow(
        service, runtime_dir=runtime_dir, trigger_id="trigger-stable", effect_id="effect-stable"
    )

    trigger_events["trigger-stable"] = [
        TriggerEvent(
            event_id="event-stable-1",
            trigger_id="trigger-stable",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 19, 13, 0, tzinfo=timezone.utc),
            payload={"batch": 1},
        )
    ]
    trigger_worker.run_once()
    effect_worker.run_once()

    assert executed_effect_ids == ["effect-stable"]

    caplog.set_level("ERROR")
    _workflow_config_path(tmp_path).write_text("{not-json", encoding="utf-8")

    trigger_events["trigger-stable"] = [
        TriggerEvent(
            event_id="event-stable-2",
            trigger_id="trigger-stable",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 19, 13, 1, tzinfo=timezone.utc),
            payload={"batch": 2},
        )
    ]
    trigger_worker.run_once()
    effect_worker.run_once()

    assert executed_effect_ids == ["effect-stable", "effect-stable"]
    assert trigger_worker.worker_config.triggers[0].id == "trigger-stable"
    assert effect_worker.worker_config.effects[0].id == "effect-stable"
    assert "keeping last-known-good" in caplog.text


def test_workers_reload_when_referenced_workflow_pack_changes(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)

    packs_dir = tmp_path / "config" / "workflow-packs"
    packs_dir.mkdir(parents=True, exist_ok=True)
    pack_path = packs_dir / "reloadable.v1.json"
    pack_path.write_text(
        json.dumps(
            {
                "pack_id": "reloadable",
                "pack_version": "v1",
                "ir": {
                    "version": "v1",
                    "nodes": [
                        {
                            "id": "trigger-pack-old",
                            "display_name": "Trigger old",
                            "type": "trigger:cron",
                            "config": {"expression": "* * * * *"},
                        },
                        {
                            "id": "effect-pack-old",
                            "display_name": "Effect old",
                            "type": "effect:agent:prompt",
                            "config": {"prompt": "run"},
                        },
                    ],
                    "edges": [{"source_id": "trigger-pack-old", "target_id": "effect-pack-old"}],
                },
            }
        ),
        encoding="utf-8",
    )
    _workflow_config_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    _workflow_config_path(tmp_path).write_text(
        json.dumps(
            {
                "runtime_dir": str(runtime_dir),
                "poll_interval_seconds": 1,
                "jitter_seconds": 0,
                "workflow_packs": ["reloadable.v1.json"],
            }
        ),
        encoding="utf-8",
    )

    trigger_worker = build_trigger_orchestrator_worker(config, project_dir=tmp_path)
    effect_worker = build_effect_orchestrator_worker(config, project_dir=tmp_path)

    trigger_events: dict[str, list[TriggerEvent]] = {}
    executed_effect_ids: list[str] = []
    trigger_worker.provider_factory.build = lambda trigger, **_: _QueuedTriggerProvider(
        trigger.id, trigger_events
    )  # type: ignore[assignment]
    effect_worker.provider_factory.build = lambda effect: _RecordingEffectProvider(
        effect.id, executed_effect_ids
    )  # type: ignore[assignment]

    trigger_events["trigger-pack-old"] = [
        TriggerEvent(
            event_id="event-pack-old-1",
            trigger_id="trigger-pack-old",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 19, 14, 0, tzinfo=timezone.utc),
            payload={"version": "old"},
        )
    ]

    trigger_worker.run_once()
    effect_worker.run_once()

    assert executed_effect_ids == ["effect-pack-old"]

    pack_path.write_text(
        json.dumps(
            {
                "pack_id": "reloadable",
                "pack_version": "v1",
                "ir": {
                    "version": "v1",
                    "nodes": [
                        {
                            "id": "trigger-pack-new",
                            "display_name": "Trigger new",
                            "type": "trigger:cron",
                            "config": {"expression": "*/5 * * * *"},
                        },
                        {
                            "id": "effect-pack-new",
                            "display_name": "Effect new",
                            "type": "effect:agent:prompt",
                            "config": {"prompt": "run"},
                        },
                    ],
                    "edges": [{"source_id": "trigger-pack-new", "target_id": "effect-pack-new"}],
                },
            }
        ),
        encoding="utf-8",
    )
    trigger_events["trigger-pack-new"] = [
        TriggerEvent(
            event_id="event-pack-new-1",
            trigger_id="trigger-pack-new",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 19, 14, 1, tzinfo=timezone.utc),
            payload={"version": "new"},
        )
    ]

    trigger_worker.run_once()
    effect_worker.run_once()

    assert executed_effect_ids == ["effect-pack-old", "effect-pack-new"]
    assert trigger_worker.worker_config.triggers[0].id == "trigger-pack-new"
    assert effect_worker.worker_config.effects[0].id == "effect-pack-new"
