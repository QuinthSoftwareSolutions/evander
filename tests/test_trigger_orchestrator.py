from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_sandbox.config import GatewayConfig, TriggerOrchestratorWorkerGlobalConfig
from agent_sandbox.trigger_orchestrator import TriggerOrchestratorCycleResult, TriggerOrchestratorWorker, main
from agent_sandbox.events.models import TriggerEvent


class _StaticProvider:
    def __init__(self, events: list[TriggerEvent]) -> None:
        self._events = events

    def poll(self) -> list[TriggerEvent]:
        return list(self._events)


class _StaticReloader:
    def __init__(self, config: TriggerOrchestratorWorkerGlobalConfig, *, changed: bool) -> None:
        self._config = config
        self._changed = changed

    def get_latest(self) -> tuple[TriggerOrchestratorWorkerGlobalConfig, bool]:
        changed = self._changed
        self._changed = False
        return self._config, changed


class _SequenceReloader:
    def __init__(self, sequence: list[tuple[TriggerOrchestratorWorkerGlobalConfig, bool]]) -> None:
        self._sequence = list(sequence)
        self._last = sequence[-1]

    def get_latest(self) -> tuple[TriggerOrchestratorWorkerGlobalConfig, bool]:
        if self._sequence:
            self._last = self._sequence.pop(0)
        return self._last


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


def test_trigger_orchestrator_rejects_reserved_runtime_output_name(tmp_path: Path) -> None:
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
        payload={"__node_outputs": "forbidden"},
    )
    worker.provider_factory.build = lambda trigger: _StaticProvider([event])  # type: ignore[assignment]

    result = worker.run_once()

    assert result.configured_triggers == 1
    assert result.events_discovered == 1
    assert result.events_published == 0
    queue_payload = json.loads(worker_config.event_queue_store_path.read_text(encoding="utf-8"))
    assert queue_payload == {}


def test_trigger_orchestrator_runs_only_explicitly_configured_triggers(tmp_path: Path) -> None:
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
            "gates": [
                {
                    "id": "await-agent-1",
                    "display_name": "Await agent",
                    "type": "gate:await-agent",
                    "session_id": "session-1",
                    "outputs": {},
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "session_id": "session-1",
                    "prompt": "Continue",
                }
            ],
            "edges": [
                {"source_id": "cron-1", "target_id": "effect-1"},
                {"source_id": "effect-1", "target_id": "await-agent-1"},
            ],
        }
    )
    worker = TriggerOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]

    worker.provider_factory.build = lambda trigger: _StaticProvider([])  # type: ignore[assignment]

    result = worker.run_once()

    assert result.configured_triggers == 1
    assert result.events_discovered == 0
    assert result.events_published == 0


def test_trigger_orchestrator_run_once_uses_reloaded_worker_config(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    initial_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [],
        }
    )
    updated_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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
    worker = TriggerOrchestratorWorker(
        config=config,
        worker_config=initial_config,
        config_reloader=_StaticReloader(updated_config, changed=True),
        runtime=None,  # type: ignore[arg-type]
    )
    worker.provider_factory.build = lambda trigger: _StaticProvider([])  # type: ignore[assignment]

    result = worker.run_once()

    assert result.configured_triggers == 1


def test_trigger_orchestrator_direct_worker_does_not_reload_from_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [],
        }
    )
    project_config_dir = tmp_path / "config" / "trigger-orchestrator"
    project_config_dir.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    worker = TriggerOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    (project_config_dir / "config.json").write_text(
        json.dumps(
            {
                "runtime_dir": str(runtime_dir),
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
        ),
        encoding="utf-8",
    )
    worker.provider_factory.build = lambda trigger: _StaticProvider([])  # type: ignore[assignment]

    result = worker.run_once()

    assert result.configured_triggers == 0


def test_trigger_orchestrator_poll_interval_change_applies_next_cycle(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    initial_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1.0,
            "jitter_seconds": 0.0,
            "triggers": [],
        }
    )
    updated_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 7.0,
            "jitter_seconds": 0.0,
            "triggers": [],
        }
    )
    worker = TriggerOrchestratorWorker(
        config=config,
        worker_config=initial_config,
        config_reloader=_SequenceReloader([(updated_config, True), (updated_config, False)]),
        runtime=None,  # type: ignore[arg-type]
    )

    first = worker.run_once()
    second = worker.run_once()

    assert first.next_sleep_seconds == 1.0
    assert second.next_sleep_seconds == 7.0


def test_trigger_orchestrator_logs_revision_and_tolerates_trigger_set_changes(tmp_path: Path, caplog) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    initial_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1.0,
            "jitter_seconds": 0.0,
            "triggers": [
                {
                    "id": "cron-old",
                    "display_name": "Old",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
        }
    )
    updated_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1.0,
            "jitter_seconds": 0.0,
            "triggers": [
                {
                    "id": "cron-new",
                    "display_name": "New",
                    "type": "trigger:cron",
                    "expression": "*/5 * * * *",
                }
            ],
        }
    )
    worker = TriggerOrchestratorWorker(
        config=config,
        worker_config=initial_config,
        config_reloader=_StaticReloader(updated_config, changed=True),
        runtime=None,  # type: ignore[arg-type]
    )
    worker.provider_factory.build = lambda trigger: _StaticProvider([])  # type: ignore[assignment]

    caplog.set_level("INFO")
    result = worker.run_once()

    assert result.configured_triggers == 1
    assert "Trigger orchestrator config revision changed revision=" in caplog.text
    assert "trigger_count=1" in caplog.text
    cursor_payload = json.loads(updated_config.event_queue_cursor_store_path.read_text(encoding="utf-8"))
    assert cursor_payload["triggers"]["cron-new"]["last_discovered_count"] == 0
