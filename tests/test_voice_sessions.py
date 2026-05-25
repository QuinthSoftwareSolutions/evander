from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

import httpx

from agent_sandbox.config import GatewayConfig
from agent_sandbox.directory_config import ContactDirectoryConfig
from agent_sandbox.messaging.wake_store import WakeRequestStore
from agent_sandbox.models import SessionManifest, ToolName
from agent_sandbox.voice_sessions import (
    OpenAIRealtimePhoneCallService,
    VoiceMediaBridgeClient,
    VoiceProjectionService,
    VoiceQueryDelegator,
    VoiceSessionReconciler,
    VoiceSessionStore,
    voice_query_payload,
)


class FakeVoiceBridge(VoiceMediaBridgeClient):
    def __init__(self) -> None:
        self.start_calls: list[dict[str, object]] = []

    def start_phone_call(self, **kwargs):
        self.start_calls.append(kwargs)
        return {
            "call_id": "bridge-call-1",
            "status": "active",
            "realtime": {"session_id": "rt-1"},
        }

    def get_voice_session(self, voice_session_id: str) -> dict[str, object]:
        return {
            "voice_session_id": voice_session_id,
            "call_id": "bridge-call-1",
            "status": "ended",
            "transcript": "Jonathan: approve item 123",
            "summary": "Jonathan approved item 123.",
            "decisions": ["Approve item 123"],
            "action_items": ["Tag item 123 as approved"],
        }


def _session(session_id: str = "session-1") -> SessionManifest:
    return SessionManifest(
        session_id=session_id,
        directory_path="root/team-a",
        model="gpt-5.4",
        allowed_tools=[ToolName.MAKE_PHONE_CALL, ToolName.GET_PHONE_CALL_RESULT],
        metadata={"agent_thread_id": "thread-1", "task": "Review approvals"},
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def _contacts() -> ContactDirectoryConfig:
    return ContactDirectoryConfig.model_validate(
        {
            "contacts": {
                "jonathan": {
                    "name": "Jonathan",
                    "phone_numbers": [
                        {"label": "mobile", "number": "+15550000002", "primary": True}
                    ],
                }
            }
        }
    )


def test_voice_projection_contains_originating_agent_context() -> None:
    projection = VoiceProjectionService().build_projection(
        session=_session(),
        prompt="Ask which approval item to release.",
        channel="phone",
        metadata={"reason": "approval"},
    )

    assert projection["origin_session_id"] == "session-1"
    assert projection["directory_path"] == "root/team-a"
    assert projection["model"] == "gpt-5.4"
    assert "Ask which approval item to release." in projection["prompt"]
    assert projection["origin_metadata"]["agent_thread_id"] == "thread-1"
    assert "query_origin_agent" in projection["instructions"]
    assert "Origin metadata" in projection["context"]


def test_voice_query_delegator_schedules_origin_agent_wake_and_completes(
    tmp_path: Path,
) -> None:
    store = VoiceSessionStore(tmp_path / "voice-sessions.json")
    wake_store = WakeRequestStore(tmp_path / "wake-requests.json")
    session_record, _ = store.prepare_session(
        origin_session_id="session-1",
        directory_path="root/team-a",
        channel="phone",
        request_id="call-1",
        prompt_metadata={},
        projection={"task": "call"},
    )

    query = VoiceQueryDelegator(store=store, wake_store=wake_store).create_query_and_wake_origin(
        session_record.voice_session_id,
        question="What does the migration file change?",
        transcript_excerpt="caller: can you check the migration?",
        metadata={"reason": "file access"},
        deadline_seconds=60,
    )
    completed = store.complete_query(
        query.query_id,
        origin_session_id="session-1",
        answer="The migration adds the voice query table.",
        citations=["migrations/voice.sql"],
        confidence="high",
    )

    scheduled = wake_store.list_requests(session_id="session-1")
    assert len(scheduled) == 1
    assert scheduled[0].request_id == f"voice-query:{query.query_id}"
    assert "submit_voice_query_response" in scheduled[0].resume_prompt.prompt
    assert completed.status == "answered"
    assert completed.answer == "The migration adds the voice query table."
    payload = voice_query_payload(completed)
    assert payload["citations"] == ["migrations/voice.sql"]


def test_openai_realtime_phone_backend_starts_bridge_and_reconciles(
    tmp_path: Path,
) -> None:
    store = VoiceSessionStore(tmp_path / "voice-sessions.json")
    wake_store = WakeRequestStore(tmp_path / "wake-requests.json")
    reconciler = VoiceSessionReconciler(store=store, wake_store=wake_store)
    bridge = FakeVoiceBridge()
    service = OpenAIRealtimePhoneCallService(
        config=GatewayConfig(
            runtime_dir=tmp_path,
            phone_call_backend="openai_realtime_didww",
            didww_from_number="+15550000001",
            didww_sip_bridge_url="https://bridge.example",
        ),
        contacts_config=_contacts(),
        store=store,
        bridge_client=bridge,
        reconciler=reconciler,
    )

    started = service.make_phone_call(
        session=_session(),
        contact="jonathan",
        prompt="Ask whether to approve item 123.",
        request_id="approval-call-1",
    )
    result = service.get_phone_call_result(
        session=_session(),
        request_id="approval-call-1",
    )

    assert started == {
        "request_id": "approval-call-1",
        "call_id": "bridge-call-1",
        "contact": "jonathan",
        "status": "active",
    }
    assert bridge.start_calls[0]["to_number"] == "+15550000002"
    assert bridge.start_calls[0]["from_number"] == "+15550000001"
    assert bridge.start_calls[0]["realtime"]["model"] == "gpt-realtime-2"
    assert result["status"] == "ended"
    assert result["transcript"] == "Jonathan: approve item 123"
    assert result["reconciliation_status"] == "scheduled"
    scheduled = wake_store.list_requests(session_id="session-1")
    assert len(scheduled) == 1
    assert scheduled[0].request_id.startswith("voice-session-reconcile:")
    assert "Approve item 123" in scheduled[0].resume_prompt.prompt


def test_voice_media_bridge_client_sends_auth_and_projected_payload(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DIDWW_SIP_BRIDGE_TOKEN", "bridge-token")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json={"call_id": "call-1", "status": "connecting"})

    client = VoiceMediaBridgeClient(
        base_url="https://bridge.example",
        token_env="DIDWW_SIP_BRIDGE_TOKEN",
        timeout_seconds=15,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = client.start_phone_call(
        voice_session_id="voice-1",
        request_id="request-1",
        to_number="+15550000002",
        from_number="+15550000001",
        contact={"id": "jonathan", "name": "Jonathan"},
        projection={"origin_session_id": "session-1"},
        realtime={"model": "gpt-realtime-2"},
    )

    payload = json.loads(requests[0].content.decode("utf-8"))
    assert result["call_id"] == "call-1"
    assert requests[0].method == "POST"
    assert str(requests[0].url) == "https://bridge.example/phone-calls"
    assert requests[0].headers["authorization"] == "Bearer bridge-token"
    assert payload["voice_session_id"] == "voice-1"
    assert payload["to_number"] == "+15550000002"
    assert payload["projection"]["origin_session_id"] == "session-1"
