from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_sandbox.config import GatewayConfig, TriggerOrchestratorWorkerGlobalConfig
from agent_sandbox.events.internal import InternalEventBus
from agent_sandbox.events.scheduled import ScheduledInternalEventStore
from agent_sandbox.orchestration.host_polling import HostFileLockManager
from agent_sandbox.daemons.trigger_orchestrator import (
    TriggerOrchestratorCycleResult,
    TriggerOrchestratorWorker,
    build_trigger_orchestrator_worker,
    main,
)
from agent_sandbox.events.models import TriggerEvent
from agent_sandbox.events.provider_models import (
    AgentPromptNewTriggerDefinition,
    EventTriggerDefinition,
    TeamsNewMessageTriggerDefinition,
    WebhookTriggerDefinition,
)
from agent_sandbox.messaging.agent_prompt_store import AgentPromptRecord, AgentPromptStore
from agent_sandbox.messaging.teams_inbox_store import TeamsInboxMessage, TeamsInboxStore
from agent_sandbox.models import SessionManifest
from agent_sandbox.repositories.session_manifests import JsonSessionManifestRepository
from agent_sandbox.triggers.providers.agent_prompt_new import AgentPromptNewTriggerProvider
from agent_sandbox.triggers.providers.base import TriggerRuntimeContext
from agent_sandbox.triggers.providers.event import EventTriggerProvider
from agent_sandbox.triggers.providers.webhook import WebhookTriggerProvider
from agent_sandbox.triggers.providers.teams_new_message import TeamsNewMessageTriggerProvider
from agent_sandbox.triggers.queue import SqliteTriggerEventQueue


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


class _FailingEventQueue:
    def __init__(self) -> None:
        self.published_event_ids: list[str] = []

    def contains(self, event_id: str) -> bool:
        _ = event_id
        return False

    def publish(self, event: TriggerEvent):
        _ = event
        raise RuntimeError("queue publish failed")


class _FailingSecondEventQueue:
    def __init__(self) -> None:
        self.published_event_ids: list[str] = []
        self.calls = 0

    def contains(self, event_id: str) -> bool:
        _ = event_id
        return False

    def publish(self, event: TriggerEvent):
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("queue publish failed on second event")
        self.published_event_ids.append(event.event_id)
        return SimpleNamespace(event_id=event.event_id)


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


def test_build_trigger_orchestrator_worker_uses_sqlite_default_from_loader(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker = build_trigger_orchestrator_worker(config, project_dir=tmp_path)
    event = TriggerEvent(
        event_id="event-1",
        trigger_id="cron-1",
        trigger_type="trigger:cron",
        occurred_at=datetime(2026, 4, 3, 12, 0, tzinfo=UTC),
        payload={"kind": "tick"},
    )

    worker.event_queue.publish(event)

    queue = SqliteTriggerEventQueue(runtime_dir / "orchestration.db")
    assert queue.contains("event-1") is True
    assert not (runtime_dir / "trigger-orchestrator-events.json").exists()


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
    wake_calls: list[str] = []
    worker._effect_wake_notifier = SimpleNamespace(  # type: ignore[attr-defined]
        notify=lambda: wake_calls.append("effect") or True
    )

    result = worker.run_once()

    assert result.configured_triggers == 1
    assert result.events_discovered == 1
    assert result.events_published == 1
    assert result.next_sleep_seconds == 0.0
    assert wake_calls == ["effect"]

    queue_payload = json.loads(worker_config.event_queue_store_path.read_text(encoding="utf-8"))
    assert list(queue_payload.keys()) == ["event-1"]
    assert queue_payload["event-1"]["trace_id"]
    cursor_payload = json.loads(
        worker_config.event_queue_cursor_store_path.read_text(encoding="utf-8")
    )
    assert cursor_payload["triggers"]["cron-1"]["last_event_id"] == "event-1"


def test_trigger_orchestrator_skips_disabled_triggers(tmp_path: Path) -> None:
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
                    "enabled": False,
                }
            ],
        }
    )
    worker = TriggerOrchestratorWorker(
        config=config,
        worker_config=worker_config,
        runtime=None,  # type: ignore[arg-type]
    )
    build_calls: list[str] = []

    def build_provider(trigger):
        build_calls.append(trigger.id)
        return _StaticProvider([])

    worker.provider_factory.build = build_provider  # type: ignore[assignment]
    wake_calls: list[str] = []
    worker._effect_wake_notifier = SimpleNamespace(  # type: ignore[attr-defined]
        notify=lambda: wake_calls.append("effect") or True
    )

    result = worker.run_once()

    assert result.configured_triggers == 0
    assert result.events_discovered == 0
    assert result.events_published == 0
    assert build_calls == []
    assert worker.event_queue.list_pending() == []
    assert wake_calls == []


def test_trigger_orchestrator_does_not_wake_effect_worker_for_duplicate_event(
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
    worker.event_queue.publish(event)
    worker.provider_factory.build = lambda trigger: _StaticProvider([event])  # type: ignore[assignment]
    wake_calls: list[str] = []
    worker._effect_wake_notifier = SimpleNamespace(  # type: ignore[attr-defined]
        notify=lambda: wake_calls.append("effect") or True
    )

    result = worker.run_once()

    assert result.events_published == 0
    assert result.next_sleep_seconds == 1
    assert wake_calls == []


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


def test_trigger_orchestrator_does_not_checkpoint_when_publish_fails(
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
    failing_queue = _FailingEventQueue()
    worker.event_queue = failing_queue  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="queue publish failed"):
        worker.run_once()

    assert provider.acknowledged_event_ids == []
    assert failing_queue.published_event_ids == []
    assert not worker_config.event_queue_cursor_store_path.exists()


def test_trigger_orchestrator_retries_same_timestamp_teams_event_after_publish_failure(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    created_at = datetime(2026, 4, 9, 9, 30, tzinfo=UTC)
    inbox_store = TeamsInboxStore(runtime_dir / "teams-inbox-store.json")
    inbox_store.add_message_if_new(
        TeamsInboxMessage(
            cursor="team-a/channel-1/msg-1",
            message_id="msg-1",
            author_id="user-a",
            author_name="User A",
            team_id="team-a",
            channel_id="channel-1",
            thread_id="msg-1",
            content="hello",
            created_at=created_at,
            metadata={"source": "teams_graph_api"},
        )
    )
    inbox_store.add_message_if_new(
        TeamsInboxMessage(
            cursor="team-a/channel-1/msg-2",
            message_id="msg-2",
            author_id="user-a",
            author_name="User A",
            team_id="team-a",
            channel_id="channel-1",
            thread_id="msg-1",
            content="same timestamp reply",
            created_at=created_at,
            metadata={"source": "teams_graph_api"},
        )
    )

    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "teams-trigger",
                    "display_name": "Teams new message",
                    "type": "trigger:teams:new-message",
                    "targets": [{"target": "team-a/channel-1"}],
                    "user_ids": ["user-a"],
                }
            ],
        }
    )
    provider = TeamsNewMessageTriggerProvider()
    provider.initialize(
        TeamsNewMessageTriggerDefinition.model_validate(
            {
                "id": "teams-trigger",
                "display_name": "Teams new message",
                "type": "trigger:teams:new-message",
                "targets": [{"target": "team-a/channel-1"}],
                "user_ids": ["user-a"],
            }
        ),
        TriggerRuntimeContext(resolved_config=config, env={}),
    )
    worker = TriggerOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory.build = lambda trigger: provider  # type: ignore[assignment]
    worker.event_queue = _FailingSecondEventQueue()  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="queue publish failed on second event"):
        worker.run_once()

    cursor_payload = json.loads(
        (runtime_dir / "trigger-cursors-teams.json").read_text(encoding="utf-8")
    )
    assert cursor_payload["teams-trigger"]["last_created_at"] == "2026-04-09T09:30:00Z"
    assert cursor_payload["teams-trigger"]["recent_cursors"] == ["team-a/channel-1/msg-1"]
    assert [event.event_id for event in provider.poll()] == ["teams-trigger:team-a/channel-1/msg-2"]


def test_trigger_orchestrator_publish_failure_with_real_provider_keeps_cursor_unadvanced(
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
                    "id": "agent-prompt-trigger",
                    "display_name": "Agent prompt",
                    "type": "trigger:agent:prompt:new",
                }
            ],
        }
    )
    worker = TriggerOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]

    sessions = JsonSessionManifestRepository(runtime_dir / "sessions.json")
    sessions.upsert_session(
        SessionManifest(
            session_id="target-a",
            directory_path="root/team-a",
            allowed_tools=[],
            expires_at=datetime(2026, 4, 3, 13, 0, tzinfo=UTC),
        )
    )
    prompt_store = AgentPromptStore(runtime_dir / "agent-prompt-store.json")
    prompt_store.create_prompt_message(
        AgentPromptRecord(
            message_id="msg-1",
            sender_session_id="sender",
            target_session_id="target-a",
            prompt_payload={"prompt": "Fetch the prompt inbox."},
            issued_token_hash="token-hash-1",
            issued_token_claims={"nonce": "n-1"},
            created_at=datetime(2026, 4, 3, 12, 0, tzinfo=UTC),
            updated_at=datetime(2026, 4, 3, 12, 0, tzinfo=UTC),
        )
    )

    provider = AgentPromptNewTriggerProvider()
    provider.initialize(
        AgentPromptNewTriggerDefinition(
            id="agent-prompt-trigger",
            display_name="Agent prompt",
            type="trigger:agent:prompt:new",
        ),
        TriggerRuntimeContext(
            resolved_config=GatewayConfig(
                runtime_dir=runtime_dir,
                session_store_path=runtime_dir / "sessions.json",
            ),
            metadata={"directory_path": "root/team-a"},
        ),
    )
    worker.provider_factory.build = lambda trigger: provider  # type: ignore[assignment]
    failing_queue = _FailingEventQueue()
    worker.event_queue = failing_queue  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="queue publish failed"):
        worker.run_once()

    assert [event.event_id for event in provider.poll()] == ["agent-prompt-trigger:msg-1"]
    assert failing_queue.published_event_ids == []
    assert len(prompt_store.list_unread_prompts(target_session_id="target-a")) == 1
    cursor_payload = json.loads(
        (runtime_dir / "trigger-cursors-agent-prompt-new.json").read_text(encoding="utf-8")
    )
    assert cursor_payload == {}
    assert not worker_config.event_queue_cursor_store_path.exists()


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


def test_due_scheduled_internal_event_publishes_before_event_trigger_poll(
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
                    "id": "event-listener",
                    "display_name": "Plan committed",
                    "type": "trigger:event",
                    "event_type": "issue.plan_committed",
                }
            ],
        }
    )
    scheduled_store = ScheduledInternalEventStore(
        worker_config.scheduled_internal_event_store_path
    )
    scheduled_event, created = scheduled_store.schedule_event(
        event_type="issue.plan_committed",
        payload={"issue_id": "ISSUE-1", "plan_version": 3},
        correlation_key="issue:ISSUE-1",
        run_at=datetime.now(UTC) - timedelta(seconds=1),
        idempotency_key="schedule-1",
        source={
            "source_type": "workflow_effect",
            "workflow_run_id": "run-1",
            "workflow_event_id": "event-1",
            "workflow_trace_id": "trace-1",
            "node_id": "node-1",
            "node_type": "effect:event:schedule",
        },
    )
    worker = TriggerOrchestratorWorker(
        config=config, worker_config=worker_config, runtime=None
    )  # type: ignore[arg-type]

    result = worker.run_once()

    assert created is True
    assert result.events_published == 1

    internal_events = InternalEventBus(
        worker_config.internal_event_store_path,
        journal_path=worker_config.workflow_event_journal_path,
    ).list_events()
    assert len(internal_events) == 1
    published_event = internal_events[0]
    assert published_event.event_type == "issue.plan_committed"
    assert published_event.payload == {"issue_id": "ISSUE-1", "plan_version": 3}
    assert published_event.correlation_key == "issue:ISSUE-1"
    assert published_event.idempotency_key == f"scheduled-event:{scheduled_event.schedule_id}"
    assert published_event.source is not None
    assert published_event.source.source_type == "scheduled_internal_event"
    assert published_event.source.workflow_run_id == "run-1"
    assert published_event.source.workflow_event_id == "event-1"
    assert published_event.source.workflow_trace_id == "trace-1"
    assert published_event.source.node_id == "node-1"
    assert published_event.source.node_type == "effect:event:schedule"

    queue_payload = json.loads(worker_config.event_queue_store_path.read_text(encoding="utf-8"))
    assert list(queue_payload.keys()) == [f"event-listener:{published_event.event_id}"]
    record = queue_payload[f"event-listener:{published_event.event_id}"]
    assert record["payload"]["event_type"] == "issue.plan_committed"
    assert record["payload"]["payload"] == {"issue_id": "ISSUE-1", "plan_version": 3}
    assert record["payload"]["correlation_key"] == "issue:ISSUE-1"
    assert record["payload"]["source"]["source_type"] == "scheduled_internal_event"
    assert record["payload"]["source"]["workflow_run_id"] == "run-1"

    schedule_payload = json.loads(
        worker_config.scheduled_internal_event_store_path.read_text(encoding="utf-8")
    )
    scheduled_record = schedule_payload[scheduled_event.schedule_id]
    assert scheduled_record["status"] == "published"
    assert scheduled_record["published_event_id"] == published_event.event_id


def test_future_scheduled_internal_event_stays_pending_until_due(
    tmp_path: Path,
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
    scheduled_store = ScheduledInternalEventStore(
        worker_config.scheduled_internal_event_store_path
    )
    scheduled_event, created = scheduled_store.schedule_event(
        event_type="issue.plan_committed",
        payload={"issue_id": "ISSUE-2"},
        run_at=datetime.now(UTC) + timedelta(minutes=5),
        idempotency_key="schedule-future",
        source={
            "source_type": "workflow_effect",
            "workflow_run_id": "run-2",
            "workflow_event_id": "event-2",
            "workflow_trace_id": "trace-2",
            "node_id": "node-2",
            "node_type": "effect:event:schedule",
        },
    )
    worker = TriggerOrchestratorWorker(
        config=config, worker_config=worker_config, runtime=None
    )  # type: ignore[arg-type]

    result = worker.run_once()

    assert created is True
    assert result.events_published == 0
    assert not worker_config.internal_event_store_path.exists()

    schedule_payload = json.loads(
        worker_config.scheduled_internal_event_store_path.read_text(encoding="utf-8")
    )
    scheduled_record = schedule_payload[scheduled_event.schedule_id]
    assert scheduled_record["status"] == "pending"
    assert scheduled_record["published_event_id"] is None
    assert scheduled_record["published_at"] is None


def test_due_scheduled_internal_event_is_published_once_across_cycles(
    tmp_path: Path,
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
    scheduled_store = ScheduledInternalEventStore(
        worker_config.scheduled_internal_event_store_path
    )
    scheduled_event, created = scheduled_store.schedule_event(
        event_type="issue.plan_committed",
        payload={"issue_id": "ISSUE-3"},
        run_at=datetime.now(UTC) - timedelta(seconds=1),
        idempotency_key="schedule-once",
        source={
            "source_type": "workflow_effect",
            "workflow_run_id": "run-3",
            "workflow_event_id": "event-3",
            "workflow_trace_id": "trace-3",
            "node_id": "node-3",
            "node_type": "effect:event:schedule",
        },
    )
    worker = TriggerOrchestratorWorker(
        config=config, worker_config=worker_config, runtime=None
    )  # type: ignore[arg-type]

    first_cycle = worker.run_once()
    second_cycle = worker.run_once()

    assert created is True
    assert first_cycle.events_published == 0
    assert second_cycle.events_published == 0

    internal_events = InternalEventBus(
        worker_config.internal_event_store_path,
        journal_path=worker_config.workflow_event_journal_path,
    ).list_events()
    assert len(internal_events) == 1
    assert internal_events[0].idempotency_key == f"scheduled-event:{scheduled_event.schedule_id}"

    schedule_payload = json.loads(
        worker_config.scheduled_internal_event_store_path.read_text(encoding="utf-8")
    )
    scheduled_record = schedule_payload[scheduled_event.schedule_id]
    assert scheduled_record["status"] == "published"
    assert scheduled_record["published_event_id"] == internal_events[0].event_id


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


def test_event_trigger_acknowledges_internal_event_after_publish(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    event_bus = InternalEventBus(
        runtime_dir / "internal-events.json",
        journal_path=runtime_dir / "workflow-events.jsonl",
    )
    internal = event_bus.publish(
        event_type="issue.plan_committed",
        payload={"issue_id": "ISSUE-1", "plan_version": 3},
        correlation_key="issue:ISSUE-1",
        idempotency_key="internal-event-1",
    )

    provider = EventTriggerProvider()
    provider.initialize(
        EventTriggerDefinition.model_validate(
            {
                "id": "event-listener",
                "display_name": "Plan committed",
                "type": "trigger:event",
                "event_type": "issue.plan_committed",
            }
        ),
        TriggerRuntimeContext(resolved_config=config, env={}),
    )

    first = provider.poll()
    cursor_path = runtime_dir / "trigger-cursors-event.json"
    cursor_payload = json.loads(cursor_path.read_text(encoding="utf-8"))

    assert len(first) == 1
    assert first[0].payload["event_id"] == internal.event_id
    assert cursor_payload == {}

    provider.acknowledge_published_event(first[0])

    second = provider.poll()
    cursor_payload = json.loads(cursor_path.read_text(encoding="utf-8"))

    assert second == []
    assert cursor_payload["event-listener"]["emitted_internal_event_ids"] == [internal.event_id]


def _webhook_trigger_definition(
    *,
    trigger_id: str,
    slug: str,
    event_type: str | None = None,
) -> WebhookTriggerDefinition:
    payload: dict[str, object] = {
        "id": trigger_id,
        "display_name": "Webhook listener",
        "type": "trigger:http:webhook",
        "slug": slug,
        "secret_env": "WEBHOOK_SECRET",
    }
    if event_type is not None:
        payload["event_type"] = event_type
    return WebhookTriggerDefinition.model_validate(payload)


def _webhook_provider(runtime_dir: Path, *, trigger_id: str, slug: str) -> WebhookTriggerProvider:
    provider = WebhookTriggerProvider()
    provider.initialize(
        _webhook_trigger_definition(trigger_id=trigger_id, slug=slug),
        TriggerRuntimeContext(
            resolved_config=GatewayConfig(runtime_dir=runtime_dir),
            env={},
        ),
    )
    return provider


def _webhook_event_bus(runtime_dir: Path) -> InternalEventBus:
    return InternalEventBus(
        runtime_dir / "internal-events.json",
        journal_path=runtime_dir / "workflow-events.jsonl",
    )


def test_webhook_trigger_emits_only_matching_signed_webhook_events(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    bus = _webhook_event_bus(runtime_dir)
    matched = bus.publish(
        event_type="external.webhook.build-notify",
        payload={
            "slug": "build-notify",
            "headers": {"x-test": "one"},
            "query": {"source": "ci"},
            "body": {"target": "demo"},
            "received_at": "2026-04-03T12:00:00Z",
            "remote_addr": "198.51.100.10",
            "signature_header": "sha256=1234",
        },
        source={
            "source_type": "webhook",
            "source_name": "build-notify",
            "node_id": "webhook-1",
            "node_type": "trigger:http:webhook",
            "workflow_trace_id": "trace-123",
        },
        idempotency_key="sha256=body-1",
        created_at=datetime(2026, 4, 3, 12, 0, tzinfo=UTC),
    )
    bus.publish(
        event_type="issue.plan_committed",
        payload={"issue_id": "ISSUE-1"},
        source={"source_type": "event", "source_name": "issue"},
    )
    bus.publish(
        event_type="external.webhook.other-slug",
        payload={"slug": "other-slug"},
        source={
            "source_type": "webhook",
            "source_name": "other-slug",
            "node_id": "webhook-2",
            "node_type": "trigger:http:webhook",
        },
    )

    provider = _webhook_provider(runtime_dir, trigger_id="webhook-1", slug="build-notify")

    first = provider.poll()
    assert len(first) == 1
    assert first[0].event_id == f"webhook-1:{matched.event_id}"
    assert first[0].trigger_type == "trigger:http:webhook"
    assert first[0].occurred_at == matched.created_at
    assert first[0].trace_id == "trace-123"
    assert first[0].payload == dict(matched.payload)

    cursor_path = runtime_dir / "trigger-cursors-http-webhook.json"
    assert json.loads(cursor_path.read_text(encoding="utf-8")) == {}

    provider.acknowledge_published_event(first[0])
    second = provider.poll()

    cursor_payload = json.loads(cursor_path.read_text(encoding="utf-8"))
    assert second == []
    assert cursor_payload["webhook-1"]["emitted_internal_event_ids"] == [matched.event_id]


def test_webhook_trigger_does_not_cross_deliver_between_slugs(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    bus = _webhook_event_bus(runtime_dir)
    first_event = bus.publish(
        event_type="external.webhook.build-notify",
        payload={"slug": "build-notify", "body": {"target": "build"}},
        source={
            "source_type": "webhook",
            "source_name": "build-notify",
            "node_id": "webhook-build",
            "node_type": "trigger:http:webhook",
        },
    )
    second_event = bus.publish(
        event_type="external.webhook.deploy-notify",
        payload={"slug": "deploy-notify", "body": {"target": "deploy"}},
        source={
            "source_type": "webhook",
            "source_name": "deploy-notify",
            "node_id": "webhook-deploy",
            "node_type": "trigger:http:webhook",
        },
    )

    build_provider = _webhook_provider(
        runtime_dir, trigger_id="webhook-build", slug="build-notify"
    )
    deploy_provider = _webhook_provider(
        runtime_dir, trigger_id="webhook-deploy", slug="deploy-notify"
    )

    build_events = build_provider.poll()
    deploy_events = deploy_provider.poll()

    assert [event.event_id for event in build_events] == [
        f"webhook-build:{first_event.event_id}"
    ]
    assert [event.event_id for event in deploy_events] == [
        f"webhook-deploy:{second_event.event_id}"
    ]
    assert build_events[0].payload["slug"] == "build-notify"
    assert deploy_events[0].payload["slug"] == "deploy-notify"


def test_trigger_orchestrator_publishes_webhook_events_and_keeps_cursor_state(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    bus = _webhook_event_bus(runtime_dir)
    internal = bus.publish(
        event_type="external.webhook.build-notify",
        payload={
            "slug": "build-notify",
            "headers": {"content-type": "application/json"},
            "query": {},
            "body": {"target": "demo"},
            "received_at": "2026-04-03T12:00:00Z",
            "remote_addr": "198.51.100.10",
            "signature_header": "sha256=body-hash",
        },
        source={
            "source_type": "webhook",
            "source_name": "build-notify",
            "node_id": "webhook-1",
            "node_type": "trigger:http:webhook",
        },
        idempotency_key="sha256=body-hash",
        created_at=datetime(2026, 4, 3, 12, 0, tzinfo=UTC),
    )
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "webhook-1",
                    "display_name": "Build notifications",
                    "type": "trigger:http:webhook",
                    "slug": "build-notify",
                    "secret_env": "WEBHOOK_SECRET",
                }
            ],
        }
    )
    worker = TriggerOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    provider = _webhook_provider(runtime_dir, trigger_id="webhook-1", slug="build-notify")
    worker.provider_factory.build = lambda trigger: provider  # type: ignore[assignment]

    first = worker.run_once()
    second = worker.run_once()

    assert first.configured_triggers == 1
    assert first.events_discovered == 1
    assert first.events_published == 1
    assert second.events_discovered == 0
    assert second.events_published == 0

    queue_payload = json.loads(worker_config.event_queue_store_path.read_text(encoding="utf-8"))
    assert list(queue_payload) == [f"webhook-1:{internal.event_id}"]
    assert queue_payload[f"webhook-1:{internal.event_id}"]["trace_id"]

    provider_cursor = json.loads(
        (runtime_dir / "trigger-cursors-http-webhook.json").read_text(encoding="utf-8")
    )
    assert provider_cursor["webhook-1"]["emitted_internal_event_ids"] == [internal.event_id]
