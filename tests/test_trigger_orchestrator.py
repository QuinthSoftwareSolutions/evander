from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_sandbox.config import GatewayConfig, TriggerOrchestratorWorkerGlobalConfig
from agent_sandbox.orchestration.host_polling import HostFileLockManager
from agent_sandbox.daemons.trigger_orchestrator import (
    TriggerOrchestratorCycleResult,
    TriggerOrchestratorWorker,
    build_trigger_orchestrator_worker,
    main,
)
from agent_sandbox.events.models import TriggerEvent


class _StaticProvider:
    def __init__(self, events: list[TriggerEvent]) -> None:
        self._events = events

    def poll(self) -> list[TriggerEvent]:
        return list(self._events)


class _AcknowledgingProvider(_StaticProvider):
    def __init__(self, events: list[TriggerEvent]) -> None:
        super().__init__(events)
        self.acknowledged_event_ids: list[str] = []

    def acknowledge_published_event(self, event: TriggerEvent) -> None:
        self.acknowledged_event_ids.append(event.event_id)


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


def test_build_trigger_orchestrator_worker_accepts_injected_services(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)

    class _InjectedServices:
        def __init__(self) -> None:
            self.called_with: GatewayConfig | None = None

        def build_runtime(self, cfg: GatewayConfig):
            self.called_with = cfg
            return object()

    services = _InjectedServices()
    worker = build_trigger_orchestrator_worker(config, project_dir=tmp_path, services=services)

    assert services.called_with is not None
    assert services.called_with.runtime_dir == runtime_dir
    assert worker.runtime is not None


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
    cursor_payload = json.loads(
        worker_config.event_queue_cursor_store_path.read_text(encoding="utf-8")
    )
    assert cursor_payload["triggers"]["cron-1"]["last_event_id"] == "event-1"


def test_trigger_orchestrator_acknowledges_provider_after_queue_publish(
    tmp_path: Path,
) -> None:
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
    provider = _AcknowledgingProvider([event])
    worker.provider_factory.build = lambda trigger: provider  # type: ignore[assignment]

    worker.run_once()

    assert provider.acknowledged_event_ids == ["event-1"]


def test_trigger_orchestrator_publishes_multiple_events_from_one_provider_poll(
    tmp_path: Path,
) -> None:
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
    worker = TriggerOrchestratorWorker(
        config=config,
        worker_config=worker_config,
        runtime=None,
    )  # type: ignore[arg-type]
    first_event = TriggerEvent(
        event_id="event-1",
        trigger_id="cron-1",
        trigger_type="trigger:cron",
        occurred_at=datetime(2026, 4, 3, 12, 0, tzinfo=UTC),
        payload={"kind": "tick-1"},
    )
    second_event = TriggerEvent(
        event_id="event-2",
        trigger_id="cron-1",
        trigger_type="trigger:cron",
        occurred_at=datetime(2026, 4, 3, 12, 0, tzinfo=UTC),
        payload={"kind": "tick-2"},
    )
    provider = _AcknowledgingProvider([first_event, second_event])
    worker.provider_factory.build = lambda trigger: provider  # type: ignore[assignment]

    result = worker.run_once()

    assert result.configured_triggers == 1
    assert result.events_discovered == 2
    assert result.events_published == 2

    queue_payload = json.loads(
        worker_config.event_queue_store_path.read_text(encoding="utf-8")
    )
    assert list(queue_payload.keys()) == ["event-1", "event-2"]
    cursor_payload = json.loads(
        worker_config.event_queue_cursor_store_path.read_text(encoding="utf-8")
    )
    assert cursor_payload["triggers"]["cron-1"]["last_discovered_count"] == 2
    assert cursor_payload["triggers"]["cron-1"]["last_event_ids"] == ["event-1", "event-2"]
    assert cursor_payload["triggers"]["cron-1"]["last_event_id"] == "event-2"
    assert provider.acknowledged_event_ids == ["event-1", "event-2"]


def test_trigger_orchestrator_main_once_uses_single_cycle(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    class _FakeWorker:
        def run_once_with_lock(self):
            return TriggerOrchestratorCycleResult(
                configured_triggers=2,
                events_discovered=3,
                events_published=2,
                next_sleep_seconds=1.5,
            )

    monkeypatch.setattr(
        "agent_sandbox.bootstrap.load_runtime_gateway_config",
        lambda runtime_dir: GatewayConfig(runtime_dir=runtime_dir),
    )
    monkeypatch.setattr(
        "agent_sandbox.daemons.trigger_orchestrator.build_trigger_orchestrator_worker",
        lambda *_args, **_kwargs: _FakeWorker(),
    )
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


def test_trigger_orchestrator_uses_shared_host_lock_manager(tmp_path: Path) -> None:
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
    worker = TriggerOrchestratorWorker(
        config=config,
        worker_config=worker_config,
        runtime=None,  # type: ignore[arg-type]
    )

    assert isinstance(worker._kernel._lock_manager, HostFileLockManager)
    assert worker._kernel.config.lock_path == worker_config.lock_path

    worker._kernel.run_once_with_lock = lambda: TriggerOrchestratorCycleResult(
        configured_triggers=0,
        events_discovered=0,
        events_published=0,
        next_sleep_seconds=1.0,
    )  # type: ignore[method-assign]

    result = worker.run_once_with_lock()

    assert result.configured_triggers == 0
    assert result.next_sleep_seconds == 1.0


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


def test_trigger_orchestrator_does_not_poll_system_triggers(tmp_path: Path) -> None:
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
                },
                {
                    "id": "system.chat_assistant",
                    "display_name": "System Chat Assistant",
                    "type": "trigger:system:chat-assistant",
                },
            ],
        }
    )
    worker = TriggerOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    built_trigger_ids: list[str] = []

    def _build_provider(trigger):
        built_trigger_ids.append(trigger.id)
        return _StaticProvider([])

    worker.provider_factory.build = _build_provider  # type: ignore[assignment]

    result = worker.run_once()

    assert result.configured_triggers == 1
    assert result.events_discovered == 0
    assert result.events_published == 0
    assert built_trigger_ids == ["cron-1"]


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


def test_trigger_orchestrator_reloads_provider_factory_after_provider_loader_change(
    tmp_path: Path, caplog, monkeypatch: pytest.MonkeyPatch
) -> None:
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
            "provider_loader": {
                "provider_modules": ["custom.reload"],
            },
            "triggers": [
                {
                    "id": "reload-trigger",
                    "display_name": "Reloaded custom trigger",
                    "type": "trigger:custom:reload",
                    "expression": "* * * * *",
                }
            ],
        }
    )
    event = TriggerEvent(
        event_id="reload-event-1",
        trigger_id="reload-trigger",
        trigger_type="trigger:custom:reload",
        occurred_at=datetime(2026, 4, 3, 12, 0, tzinfo=UTC),
        payload={"kind": "reload"},
    )
    build_calls: list[tuple[str, ...]] = []

    def _build_runtime_wiring(
        *,
        config: GatewayConfig,
        worker_config: TriggerOrchestratorWorkerGlobalConfig,
        runtime,
        provider_config_resolver=None,
    ):
        _ = config, runtime, provider_config_resolver
        build_calls.append(tuple(worker_config.provider_modules))
        if worker_config.provider_modules and len(build_calls) == 2:
            raise RuntimeError("temporary provider_loader reload failure")
        provider_factory = SimpleNamespace(
            build=lambda trigger: _StaticProvider([event])
            if worker_config.provider_modules and trigger.id == "reload-trigger"
            else _StaticProvider([])
        )
        return SimpleNamespace(
            provider_config_resolver=object(),
            provider_factory=provider_factory,
        )

    monkeypatch.setattr(
        "agent_sandbox.daemons.trigger_orchestrator.build_trigger_provider_runtime_wiring",
        _build_runtime_wiring,
    )
    monkeypatch.setattr(
        "agent_sandbox.orchestration.wiring.trigger.build_trigger_provider_runtime_wiring",
        _build_runtime_wiring,
    )

    worker = TriggerOrchestratorWorker(
        config=config,
        worker_config=initial_config,
        config_reloader=_SequenceReloader(
            [
                (updated_config, True),
                (updated_config, False),
                (updated_config, False),
            ]
        ),
        runtime=None,  # type: ignore[arg-type]
    )

    caplog.set_level("ERROR")

    first_cycle = worker.run_once()
    assert first_cycle.configured_triggers == 0
    assert worker.worker_config.provider_modules == []

    second_cycle = worker.run_once()

    assert second_cycle.configured_triggers == 1
    assert second_cycle.events_published == 1
    assert build_calls == [(), ("custom.reload",), ("custom.reload",)]
    assert worker.worker_config.provider_modules == ["custom.reload"]
    assert "provider_loader reload failed" in caplog.text
    assert "restart or fix configuration before retrying" in caplog.text

    queue_payload = json.loads(
        updated_config.event_queue_store_path.read_text(encoding="utf-8")
    )
    assert list(queue_payload.keys()) == ["reload-event-1"]


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


def test_trigger_orchestrator_logs_revision_and_tolerates_trigger_set_changes(
    tmp_path: Path, caplog
) -> None:
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
    cursor_payload = json.loads(
        updated_config.event_queue_cursor_store_path.read_text(encoding="utf-8")
    )
    assert cursor_payload["triggers"]["cron-new"]["last_discovered_count"] == 0


def test_event_trigger_matches_internal_event_and_preserves_payload(tmp_path: Path) -> None:
    from agent_sandbox.events.internal import InternalEventBus

    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "event-listener",
                    "display_name": "Plan committed",
                    "type": "trigger:event",
                    "event_type": "issue.plan_committed",
                }
            ],
        }
    )
    bus = InternalEventBus(
        worker_config.internal_event_store_path,
        journal_path=worker_config.workflow_event_journal_path,
    )
    internal = bus.publish(
        event_type="issue.plan_committed",
        payload={"issue_id": "ISSUE-1", "plan_version": 3},
        correlation_key="issue:ISSUE-1",
        idempotency_key="test-event-1",
    )
    worker = TriggerOrchestratorWorker(
        config=config, worker_config=worker_config, runtime=None
    )  # type: ignore[arg-type]

    result = worker.run_once()

    assert result.events_published == 1
    queue_payload = json.loads(worker_config.event_queue_store_path.read_text(encoding="utf-8"))
    record = queue_payload[f"event-listener:{internal.event_id}"]
    assert record["trigger_id"] == "event-listener"
    assert record["trigger_type"] == "trigger:event"
    assert record["payload"]["event_type"] == "issue.plan_committed"
    assert record["payload"]["payload"] == {"issue_id": "ISSUE-1", "plan_version": 3}
    assert record["payload"]["correlation_key"] == "issue:ISSUE-1"


def test_event_trigger_ignores_non_matching_type_and_correlation_key(tmp_path: Path) -> None:
    from agent_sandbox.events.internal import InternalEventBus

    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "event-listener",
                    "display_name": "Plan committed",
                    "type": "trigger:event",
                    "event_type": "issue.plan_committed",
                    "correlation_key": "issue:expected",
                }
            ],
        }
    )
    bus = InternalEventBus(
        worker_config.internal_event_store_path,
        journal_path=worker_config.workflow_event_journal_path,
    )
    bus.publish(event_type="issue.step_completed", payload={}, idempotency_key="wrong-type")
    bus.publish(
        event_type="issue.plan_committed",
        payload={},
        correlation_key="issue:other",
        idempotency_key="wrong-correlation",
    )
    worker = TriggerOrchestratorWorker(
        config=config, worker_config=worker_config, runtime=None
    )  # type: ignore[arg-type]

    result = worker.run_once()

    assert result.events_published == 0
    assert json.loads(worker_config.event_queue_store_path.read_text(encoding="utf-8")) == {}


def test_multiple_event_triggers_can_listen_to_same_internal_event(tmp_path: Path) -> None:
    from agent_sandbox.events.internal import InternalEventBus

    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "listener-a",
                    "display_name": "A",
                    "type": "trigger:event",
                    "event_type": "human.question_answered",
                },
                {
                    "id": "listener-b",
                    "display_name": "B",
                    "type": "trigger:event",
                    "event_type": "human.question_answered",
                },
            ],
        }
    )
    bus = InternalEventBus(
        worker_config.internal_event_store_path,
        journal_path=worker_config.workflow_event_journal_path,
    )
    event = bus.publish(
        event_type="human.question_answered",
        payload={"answer": "yes"},
        idempotency_key="answer-1",
    )
    worker = TriggerOrchestratorWorker(
        config=config, worker_config=worker_config, runtime=None
    )  # type: ignore[arg-type]

    result = worker.run_once()

    assert result.events_published == 2
    queue_payload = json.loads(worker_config.event_queue_store_path.read_text(encoding="utf-8"))
    assert sorted(queue_payload) == [
        f"listener-a:{event.event_id}",
        f"listener-b:{event.event_id}",
    ]
