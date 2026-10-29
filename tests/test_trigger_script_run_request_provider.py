from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from evander.config import GatewayConfig
from evander.events.provider_models import ScriptRunRequestTriggerDefinition
from evander.models import ScriptRunRequestRecord, SessionManifest
from evander.repositories.script_run_requests import ScriptRunRequestStore
from evander.repositories.session_manifests import JsonSessionManifestRepository
from evander.triggers.providers.base import TriggerRuntimeContext
from evander.triggers.providers.script_run_request import ScriptRunRequestTriggerProvider


def _context(runtime_dir: Path) -> TriggerRuntimeContext:
    config = GatewayConfig(
        runtime_dir=runtime_dir,
        session_store_path=runtime_dir / "sessions.json",
    )
    return TriggerRuntimeContext(resolved_config=config, metadata={"directory_path": "root/team-a"})


def test_script_run_request_trigger_emits_once_per_visible_request(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    sessions = JsonSessionManifestRepository(runtime_dir / "sessions.json")
    sessions.upsert_session(
        SessionManifest(
            session_id="agent-a",
            directory_path="root/team-a",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    store = ScriptRunRequestStore(runtime_dir / "script-run-requests-store.json")
    created_at = datetime(2026, 4, 16, 10, 0, tzinfo=UTC)
    store.enqueue(
        session_id="agent-a",
        directory_path="root/team-a",
        script_name="deploy-review",
        parameters=["--pr", "42"],
        request_id="req-1",
    )
    store.enqueue(
        session_id="agent-a",
        directory_path="root/team-b",
        script_name="other-dir",
        parameters=[],
        request_id="req-2",
    )

    provider = ScriptRunRequestTriggerProvider()
    provider.initialize(
        trigger_definition=ScriptRunRequestTriggerDefinition(
            id="script-run-trigger",
            display_name="Script run request",
            type="trigger:script-run-request",
        ),
        runtime_context=_context(runtime_dir),
    )

    events = provider.poll()
    assert len(events) == 1
    assert events[0].event_id == "script-run-trigger:req-1"
    assert events[0].payload == {
        "request_id": "req-1",
        "session_id": "agent-a",
        "script_name": "deploy-review",
        "parameters": ["--pr", "42"],
    }
    cursor_path = runtime_dir / "trigger-cursors-script-run-request.json"
    assert json.loads(cursor_path.read_text(encoding="utf-8")) == {}

    provider.acknowledge_published_event(events[0])
    cursor_payload = json.loads(cursor_path.read_text(encoding="utf-8"))
    assert cursor_payload["script-run-trigger"]["emitted_request_ids"] == ["req-1"]
    assert provider.poll() == []

    raw_payload = store.read_raw()
    raw_payload["requests"]["req-3"] = ScriptRunRequestRecord(
        request_id="req-3",
        session_id="agent-a",
        directory_path="root/team-a/subdir",
        script_name="collect-logs",
        parameters=["session-1"],
        created_at=created_at,
    ).model_dump(mode="json")
    (runtime_dir / "script-run-requests-store.json").write_text(
        json.dumps(raw_payload), encoding="utf-8"
    )

    next_events = provider.poll()
    assert [event.event_id for event in next_events] == ["script-run-trigger:req-3"]
