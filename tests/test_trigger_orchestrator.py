from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from agent_sandbox.config import GatewayConfig, TriggerOrchestratorWorkerGlobalConfig
from agent_sandbox.trigger_orchestrator import TriggerOrchestratorCycleResult, TriggerOrchestratorWorker, main
from agent_sandbox.events.models import TriggerEvent


class _StaticProvider:
    def __init__(self, events: list[TriggerEvent]) -> None:
        self._events = events

    def poll(self) -> list[TriggerEvent]:
        return list(self._events)


def test_trigger_orchestrator_run_once_publishes_and_updates_cursor(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "cron-1",
                    "display_name": "Every minute",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
        }
    )
    worker = TriggerOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    event = TriggerEvent(
        event_id="event-1",
        trigger_id="cron-1",
        trigger_type="trigger:cron",
        occurred_at=datetime(2026, 4, 3, 12, 0, tzinfo=UTC),
        payload={"kind": "tick"},
    )
    worker.provider_factory.build = lambda trigger: _StaticProvider([event])  # type: ignore[assignment]

    result = worker.run_once()

    assert result.configured_triggers == 1
    assert result.events_discovered == 1
    assert result.events_published == 1

    queue_payload = json.loads(worker_config.event_queue_store_path.read_text(encoding="utf-8"))
    assert list(queue_payload.keys()) == ["event-1"]
    assert queue_payload["event-1"]["trace_id"]
    cursor_payload = json.loads(worker_config.event_queue_cursor_store_path.read_text(encoding="utf-8"))
    assert cursor_payload["triggers"]["cron-1"]["last_event_id"] == "event-1"


def test_trigger_orchestrator_main_once_uses_single_cycle(monkeypatch, tmp_path: Path, capsys) -> None:
    class _FakeWorker:
        def run_once_with_lock(self):
            return TriggerOrchestratorCycleResult(
                configured_triggers=2,
                events_discovered=3,
                events_published=2,
                next_sleep_seconds=1.5,
            )

    monkeypatch.setattr("agent_sandbox.bootstrap.load_runtime_gateway_config", lambda runtime_dir: GatewayConfig(runtime_dir=runtime_dir))
    monkeypatch.setattr("agent_sandbox.trigger_orchestrator.build_trigger_orchestrator_worker", lambda *_args, **_kwargs: _FakeWorker())
    monkeypatch.setattr(
        "sys.argv",
        [
            "agent-sandbox-trigger-orchestrator",
            "--runtime-dir",
            str(tmp_path / "runtime"),
            "--once",
        ],
    )

    main()

    output = capsys.readouterr().out.strip()
    assert "Trigger orchestrator cycle complete triggers=2 discovered=3 published=2" in output
