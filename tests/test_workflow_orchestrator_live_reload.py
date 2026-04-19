from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agent_sandbox.admin_service import AdminService
from agent_sandbox.config import GatewayConfig
from agent_sandbox.effect_orchestrator import build_effect_orchestrator_worker
from agent_sandbox.events.models import TriggerEvent
from agent_sandbox.trigger_orchestrator import build_trigger_orchestrator_worker


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


def _workflow_config_path(project_dir: Path) -> Path:
    return project_dir / "config" / "trigger-orchestrator" / "config.json"


def _save_workflow(
    service: AdminService,
    *,
    runtime_dir: Path,
    trigger_id: str,
    effect_id: str,
) -> None:
    service.save_workflow_editor_document(
        config={
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
        },
        layout={"nodes": {trigger_id: {"x": 0, "y": 0}, effect_id: {"x": 250, "y": 0}}},
    )


def test_workers_reload_saved_workflow_without_restarting_objects(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)

    trigger_worker = build_trigger_orchestrator_worker(config, project_dir=tmp_path)
    effect_worker = build_effect_orchestrator_worker(config, project_dir=tmp_path)

    trigger_events: dict[str, list[TriggerEvent]] = {}
    executed_effect_ids: list[str] = []
    trigger_worker.provider_factory.build = lambda trigger: _QueuedTriggerProvider(trigger.id, trigger_events)  # type: ignore[assignment]
    effect_worker.provider_factory.build = lambda effect: _RecordingEffectProvider(effect.id, executed_effect_ids)  # type: ignore[assignment]

    trigger_worker_id = id(trigger_worker)
    effect_worker_id = id(effect_worker)
    trigger_runtime_id = id(trigger_worker.runtime)
    effect_runtime_id = id(effect_worker.runtime)

    service = AdminService(runtime_dir)

    _save_workflow(service, runtime_dir=runtime_dir, trigger_id="trigger-old", effect_id="effect-old")
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

    assert first_trigger_cycle.configured_triggers == 1
    assert first_effect_cycle.effect_executions == 1
    assert executed_effect_ids == ["effect-old"]

    _save_workflow(service, runtime_dir=runtime_dir, trigger_id="trigger-new", effect_id="effect-new")
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

    assert second_trigger_cycle.configured_triggers == 1
    assert second_effect_cycle.effect_executions >= 1
    assert executed_effect_ids == ["effect-old", "effect-new"]
    assert trigger_worker.worker_config.triggers[0].id == "trigger-new"
    assert effect_worker.worker_config.effects[0].id == "effect-new"

    assert id(trigger_worker) == trigger_worker_id
    assert id(effect_worker) == effect_worker_id
    assert id(trigger_worker.runtime) == trigger_runtime_id
    assert id(effect_worker.runtime) == effect_runtime_id


def test_workers_keep_last_known_good_config_when_reload_payload_is_invalid(tmp_path: Path, caplog) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)

    trigger_worker = build_trigger_orchestrator_worker(config, project_dir=tmp_path)
    effect_worker = build_effect_orchestrator_worker(config, project_dir=tmp_path)

    trigger_events: dict[str, list[TriggerEvent]] = {}
    executed_effect_ids: list[str] = []
    trigger_worker.provider_factory.build = lambda trigger: _QueuedTriggerProvider(trigger.id, trigger_events)  # type: ignore[assignment]
    effect_worker.provider_factory.build = lambda effect: _RecordingEffectProvider(effect.id, executed_effect_ids)  # type: ignore[assignment]

    service = AdminService(runtime_dir)
    _save_workflow(service, runtime_dir=runtime_dir, trigger_id="trigger-stable", effect_id="effect-stable")

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
