import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from agent_sandbox.config import GatewayConfig, TeamsMeetingIngestWorkerGlobalConfig
from agent_sandbox.gateway import create_server
from agent_sandbox.models import SessionManifest, ToolName
from agent_sandbox.runtime import build_runtime
from agent_sandbox.teams_meeting import (
    TeamsMeetingBinding,
    TeamsMeetingBridgeEvent,
    TeamsMeetingStore,
)
from agent_sandbox.messaging.teams_meeting_ingest import (
    TeamsMeetingIngestWorker,
)
from agent_sandbox.triggers.providers.base import TriggerRuntimeContext
from agent_sandbox.triggers.providers.schemas import TeamsMeetingNewTranscriptTriggerDefinition
from agent_sandbox.triggers.providers.teams_meeting_new_transcript import (
    TeamsMeetingNewTranscriptTriggerProvider,
)


class FakeBridgeClient:
    def __init__(self) -> None:
        self.speak_calls: list[dict[str, object]] = []
        self.leave_calls: list[str] = []
        self.events_by_meeting: dict[str, list[TeamsMeetingBridgeEvent]] = {}

    def speak(
        self,
        *,
        meeting_id: str,
        text: str,
        voice: str | None,
        interrupt: bool,
    ) -> dict[str, object]:
        self.speak_calls.append(
            {"meeting_id": meeting_id, "text": text, "voice": voice, "interrupt": interrupt}
        )
        return {"operation_id": "speech-1", "status": "queued"}

    def leave(self, *, meeting_id: str) -> dict[str, object]:
        self.leave_calls.append(meeting_id)
        return {"status": "left", "meeting_id": meeting_id}

    def get_events(self, *, meeting_id: str, after_sequence: int = 0):
        return [
            event
            for event in self.events_by_meeting.get(meeting_id, [])
            if event.sequence > after_sequence
        ]


def make_context(tmp_path: Path, **kwargs: object):
    config = GatewayConfig(
        runtime_dir=tmp_path,
        audit_log_path=tmp_path / "audit.jsonl",
        session_store_path=tmp_path / "sessions.json",
        approval_store_path=tmp_path / "approvals.json",
        draft_store_path=tmp_path / "drafts.json",
        email_store_path=tmp_path / "emails.json",
        discord_command_store_path=tmp_path / "discord.json",
        **kwargs,
    )
    return build_runtime(config)


def call_tool(server, name: str, arguments: dict[str, object]):
    result = asyncio.run(server.call_tool(name, arguments))
    if isinstance(result, tuple) and len(result) == 2:
        structured = result[1]
        if isinstance(structured, dict) and set(structured) == {"result"}:
            return structured["result"]
        return structured
    return result


def read_audit_events(path: Path) -> list[dict[str, object]]:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [json.loads(line) for line in lines]


def test_teams_meeting_config_defaults_follow_runtime_dir_and_normalize_bridge_url() -> None:
    config = GatewayConfig.model_validate(
        {
            "runtime_dir": "/tmp/agent-sandbox-runtime",
            "teams_meeting_media_bridge_url": " https://bridge.example/ ",
        }
    )

    assert config.teams_meeting_media_bridge_url == "https://bridge.example"
    assert config.teams_meeting_default_voice == "en-US-JennyNeural"
    assert str(config.teams_meeting_store_path) == (
        "/tmp/agent-sandbox-runtime/teams-meetings.json"
    )


def test_teams_meeting_ingest_worker_config_defaults_follow_runtime_dir() -> None:
    config = TeamsMeetingIngestWorkerGlobalConfig.model_validate(
        {"runtime_dir": "/tmp/agent-sandbox-runtime"}
    )

    assert str(config.lock_path) == "/tmp/agent-sandbox-runtime/teams-meeting-ingest.lock"
    assert config.directories == []


def test_teams_meeting_store_orders_bridge_events_and_dedupes_by_bridge_sequence(
    tmp_path: Path,
) -> None:
    store = TeamsMeetingStore(tmp_path / "teams-meetings.json")
    store.upsert_binding(
        TeamsMeetingBinding(
            meeting_id="meeting-1",
            session_id="session-1",
            directory_path="root/team-a",
        )
    )

    appended = store.append_bridge_events(
        meeting_id="meeting-1",
        bridge_events=[
            TeamsMeetingBridgeEvent(
                sequence=2,
                event_type="speech_status",
                speech_status="completed",
            ),
            TeamsMeetingBridgeEvent(sequence=1, event_type="transcript", text="hello"),
        ],
    )
    duplicate = store.append_bridge_events(
        meeting_id="meeting-1",
        bridge_events=[TeamsMeetingBridgeEvent(sequence=1, event_type="transcript", text="hello")],
    )

    assert [(event.sequence, event.bridge_sequence, event.event_type) for event in appended] == [
        (1, 1, "transcript"),
        (2, 2, "speech_status"),
    ]
    assert duplicate == []
    assert store.get_binding("meeting-1").last_bridge_sequence == 2
    assert [event.text for event in store.list_events_for_session("session-1")] == [
        "hello",
        None,
    ]


def test_teams_meeting_gateway_speak_and_get_events_use_session_policy(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[
                ToolName.TEAMS_MEETING_GET_EVENTS,
                ToolName.TEAMS_MEETING_SPEAK,
            ],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    context.teams_meeting_store.upsert_binding(
        TeamsMeetingBinding(meeting_id="meeting-1", session_id="session-1")
    )
    fake_bridge = FakeBridgeClient()
    context.teams_meeting_bridge_client = fake_bridge
    server = create_server(context)

    speak_result = call_tool(
        server,
        "teams_meeting_speak",
        {
            "session_id": "session-1",
            "text": "Please pause there.",
            "voice": "en-US-AvaNeural",
            "interrupt": True,
        },
    )
    events_result = call_tool(
        server,
        "teams_meeting_get_events",
        {"session_id": "session-1", "since_sequence": 0},
    )

    assert fake_bridge.speak_calls == [
        {
            "meeting_id": "meeting-1",
            "text": "Please pause there.",
            "voice": "en-US-AvaNeural",
            "interrupt": True,
        }
    ]
    assert speak_result["speech_operation_id"] == "speech-1"
    assert [event["event_type"] for event in events_result["events"]] == ["speech_status"]
    assert events_result["next_sequence"] == 1


def test_teams_meeting_gateway_rejects_speak_without_tool_permission(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.TEAMS_MEETING_GET_EVENTS],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    context.teams_meeting_store.upsert_binding(
        TeamsMeetingBinding(meeting_id="meeting-1", session_id="session-1")
    )
    server = create_server(context)

    with pytest.raises(ToolError, match="teams_meeting_speak is not allowed"):
        call_tool(
            server,
            "teams_meeting_speak",
            {"session_id": "session-1", "text": "hello"},
        )


def test_teams_meeting_gateway_audits_bridge_http_error(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=[ToolName.TEAMS_MEETING_SPEAK],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    context.teams_meeting_store.upsert_binding(
        TeamsMeetingBinding(meeting_id="meeting-1", session_id="session-1")
    )
    request = httpx.Request("POST", "https://bridge.example/meetings/meeting-1/speak")
    response = httpx.Response(503, request=request, text='{"error":"down"}')

    class FailingBridge(FakeBridgeClient):
        def speak(self, **kwargs):
            raise httpx.HTTPStatusError("bridge down", request=request, response=response)

    context.teams_meeting_bridge_client = FailingBridge()
    server = create_server(context)

    with pytest.raises(ToolError):
        call_tool(
            server,
            "teams_meeting_speak",
            {"session_id": "session-1", "text": "hello"},
        )

    events = read_audit_events(context.config.audit_log_path)
    assert events[-1]["tool_name"] == ToolName.TEAMS_MEETING_SPEAK.value
    assert events[-1]["result"]["error_class"] == "adapter_http_error"
    meeting_events = context.teams_meeting_store.list_events_for_session("session-1")
    assert [(event.event_type, event.text) for event in meeting_events] == [
        ("error", "bridge down")
    ]


def test_teams_meeting_ingest_worker_polls_new_bridge_events_once(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.teams_meeting_store.upsert_binding(
        TeamsMeetingBinding(
            meeting_id="meeting-1",
            session_id="session-1",
            directory_path="root/team-a",
        )
    )
    fake_bridge = FakeBridgeClient()
    fake_bridge.events_by_meeting["meeting-1"] = [
        TeamsMeetingBridgeEvent(sequence=1, event_type="transcript", text="hello"),
        TeamsMeetingBridgeEvent(sequence=2, event_type="transcript", text="second"),
    ]
    context.teams_meeting_bridge_client = fake_bridge
    worker = TeamsMeetingIngestWorker(
        config=context.config,
        worker_config=TeamsMeetingIngestWorkerGlobalConfig(
            runtime_dir=tmp_path,
            directories=["root/team-a"],
            jitter_seconds=0,
        ),
        runtime=context,
    )

    first = worker.run_once()
    second = worker.run_once()

    assert first.events_ingested == 2
    assert second.events_ingested == 0
    assert [event.text for event in context.teams_meeting_store.list_events_for_session("session-1")] == [
        "hello",
        "second",
    ]


def test_teams_meeting_transcript_trigger_emits_transcripts_once(tmp_path: Path) -> None:
    config = GatewayConfig(runtime_dir=tmp_path, teams_meeting_store_path=tmp_path / "meetings.json")
    store = TeamsMeetingStore(config.teams_meeting_store_path)
    store.upsert_binding(TeamsMeetingBinding(meeting_id="meeting-1", session_id="session-1"))
    store.append_bridge_events(
        meeting_id="meeting-1",
        bridge_events=[
            TeamsMeetingBridgeEvent(
                sequence=1,
                event_type="transcript",
                text="final sentence",
            )
        ],
    )
    provider = TeamsMeetingNewTranscriptTriggerProvider()
    provider.initialize(
        TeamsMeetingNewTranscriptTriggerDefinition(
            id="meeting-trigger",
            display_name="Meeting transcript",
            type="trigger:teams-meeting:new-transcript",
        ),
        TriggerRuntimeContext(resolved_config=config, metadata={"directory_path": "root"}),
    )

    first = provider.poll()
    second = provider.poll()

    assert [event.event_id for event in first] == ["meeting-trigger:meeting-1:1"]
    assert first[0].payload["session_id"] == "session-1"
    assert first[0].payload["transcript"]["text"] == "final sentence"
    assert second == []


def test_teams_meeting_transcript_trigger_uses_monotonic_sequence_cursor(
    tmp_path: Path,
) -> None:
    config = GatewayConfig(runtime_dir=tmp_path, teams_meeting_store_path=tmp_path / "meetings.json")
    store = TeamsMeetingStore(config.teams_meeting_store_path)
    store.upsert_binding(TeamsMeetingBinding(meeting_id="meeting-1", session_id="session-1"))
    store.append_bridge_events(
        meeting_id="meeting-1",
        bridge_events=[
            TeamsMeetingBridgeEvent(
                sequence=sequence,
                event_type="transcript",
                text=f"sentence {sequence}",
            )
            for sequence in range(1, 601)
        ],
    )
    provider = TeamsMeetingNewTranscriptTriggerProvider()
    provider.initialize(
        TeamsMeetingNewTranscriptTriggerDefinition(
            id="meeting-trigger",
            display_name="Meeting transcript",
            type="trigger:teams-meeting:new-transcript",
        ),
        TriggerRuntimeContext(resolved_config=config, metadata={"directory_path": "root"}),
    )

    first = provider.poll()
    second = provider.poll()

    assert len(first) == 600
    assert first[0].event_id == "meeting-trigger:meeting-1:1"
    assert first[-1].event_id == "meeting-trigger:meeting-1:600"
    assert second == []
    cursor_payload = json.loads(
        (tmp_path / "trigger-cursors-teams-meeting.json").read_text(encoding="utf-8")
    )
    assert cursor_payload["meeting-trigger"] == {
        "last_sequences_by_meeting": {"meeting-1": 600}
    }
