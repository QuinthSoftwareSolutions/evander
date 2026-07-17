from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from evander.config import GatewayConfig
from evander.directory_config import ContactDirectoryConfig
from evander.messaging.wake_store import WakeRequestStore
from evander.models import (
    SessionManifest,
    ToolName,
    WakeDispatchRecord,
)
from evander.runtime import build_runtime
from evander.voice_sessions import (
    OpenAIRealtimeClientSecretService,
    OpenAIRealtimePhoneCallService,
    VoiceMediaBridgeClient,
    VoiceProjectionService,
    VoiceQueryDelegator,
    VoiceSessionReconciler,
    VoiceSessionStore,
    disable_pending_voice_query_wake,
    realtime_browser_session_config,
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
            "audio_quality": {
                "voice_session_id": voice_session_id,
                "call_id": "bridge-call-1",
                "direction": "outbound",
                "started_at": "2026-06-16T12:00:00Z",
                "updated_at": "2026-06-16T12:00:05Z",
                "media_format": "alaw",
                "rtp_payload_type": 8,
                "sample_rate_hz": 8000,
                "frame_duration_ms": 20,
                "rtp_port": 49100,
                "route_id": "route-1",
                "asterisk_pjsip_endpoint": "didww",
                "carrier": "carrier-one",
                "region": "us-east-1",
                "inbound_packets": 12,
                "outbound_packets": 8,
                "lost_packets": 1,
                "queue_depth_max": 3,
                "metadata": {
                    "api_key": "bridge-secret",
                    "phone_number": "+15551234567",
                },
            },
        }


def _session(
    session_id: str = "session-1",
    *,
    allowed_contact_ids: list[str] | None = None,
) -> SessionManifest:
    return SessionManifest(
        session_id=session_id,
        directory_path="root/team-a",
        model="gpt-5.4",
        allowed_tools=[ToolName.MAKE_PHONE_CALL, ToolName.GET_PHONE_CALL_RESULT],
        allowed_contact_ids=allowed_contact_ids,
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
                },
                "support": {
                    "name": "Support",
                    "phone_numbers": [{"label": "main", "number": "+15550000003", "primary": True}],
                },
            }
        }
    )


def test_realtime_browser_session_config_includes_origin_query_tool() -> None:
    config = SimpleNamespace(
        openai_realtime_model="gpt-realtime-2",
        openai_realtime_voice="marin",
        openai_realtime_transcription_model="gpt-4o-transcribe",
    )

    payload = realtime_browser_session_config(config, instructions="Answer as Backend.")

    assert payload["type"] == "realtime"
    assert payload["model"] == "gpt-realtime-2"
    assert payload["instructions"] == "Answer as Backend."
    assert payload["audio"]["output"]["voice"] == "marin"
    assert payload["audio"]["input"]["transcription"]["model"] == "gpt-4o-transcribe"
    assert payload["tools"][0]["name"] == "query_origin_agent"
    assert payload["tool_choice"] == "auto"

    custom_payload = realtime_browser_session_config(
        config,
        instructions="Answer as Backend.",
        voice={"id": "voice_custom"},
    )

    assert custom_payload["audio"]["output"]["voice"] == {"id": "voice_custom"}


def test_openai_realtime_client_secret_service_posts_browser_session_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content)
        assert body["session"]["instructions"] == "Answer as Backend."
        assert body["session"]["tools"][0]["name"] == "query_origin_agent"
        assert body["session"]["audio"]["output"]["voice"] == "cedar"
        return httpx.Response(200, json={"value": "ek_test", "expires_at": 123})

    monkeypatch.setenv("REALTIME_KEY", "sk-test")
    config = SimpleNamespace(
        openai_realtime_model="gpt-realtime-2",
        openai_realtime_voice="marin",
        openai_realtime_transcription_model="gpt-4o-transcribe",
        openai_realtime_api_key_env="REALTIME_KEY",
        openai_realtime_api_base_url="https://realtime.example.test/v1/",
        voice_media_controller_request_timeout_seconds=1.0,
    )
    with httpx.Client(transport=httpx.MockTransport(_handler)) as client:
        payload = OpenAIRealtimeClientSecretService(
            config=config,
            http_client=client,
        ).create_client_secret(
            instructions="Answer as Backend.",
            safety_identifier="team-addon:backend",
            voice="cedar",
        )

    assert payload == {"value": "ek_test", "expires_at": 123}
    assert len(requests) == 1
    assert str(requests[0].url) == "https://realtime.example.test/v1/realtime/client_secrets"
    assert requests[0].headers["authorization"] == "Bearer sk-test"
    assert requests[0].headers["openai-safety-identifier"] == "team-addon:backend"


def test_voice_session_store_supports_browser_agent_call(tmp_path: Path) -> None:
    store = VoiceSessionStore(tmp_path / "voice-sessions.json")

    record, created = store.prepare_session(
        origin_session_id="session-backend",
        directory_path="root/team-a",
        channel="browser_agent_call",
        request_id="browser-call-1",
        prompt_metadata={"agent_id": "backend"},
        projection={"prompt": "Answer as Backend."},
        caller_metadata={"call_surface": "team_people_tab"},
        contact_id="backend",
        contact_display_name="Backend",
    )
    assert created is True
    assert record.channel == "browser_agent_call"

    store.append_transcript_event(
        record.voice_session_id,
        text="What is blocked?",
        speaker="operator",
    )
    ended = store.complete_session(record.voice_session_id, status="ended")

    assert ended.status == "ended"
    assert ended.transcript == "operator: What is blocked?"
    assert ended.contact_id == "backend"


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


def _projected_tool_summary(session_id: str) -> dict[str, object]:
    return {
        "session_id": session_id,
        "available_tools": [
            {
                "tool_name": ToolName.MAKE_PHONE_CALL.value,
                "source": "session.allowed_tools",
                "requires_approval": False,
            },
            {
                "tool_name": ToolName.GET_PHONE_CALL_RESULT.value,
                "source": "session.allowed_tools",
                "requires_approval": True,
            },
        ],
        "approval_required_tools": [ToolName.GET_PHONE_CALL_RESULT.value],
        "unavailable_tools": [
            {
                "tool_name": ToolName.RUN_SCRIPT.value,
                "source": "session.allowed_tools",
                "reason": f"Tool run_script is not allowed for session {session_id}",
            }
        ],
        "evaluated": True,
    }


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
    assert projection["tool_summary"]["session_id"] == "session-1"
    assert projection["tool_summary"]["available_tools"] == [
        {
            "tool_name": ToolName.MAKE_PHONE_CALL.value,
            "source": "session.allowed_tools",
            "requires_approval": False,
        },
        {
            "tool_name": ToolName.GET_PHONE_CALL_RESULT.value,
            "source": "session.allowed_tools",
            "requires_approval": False,
        },
    ]
    assert "Available MCP tools: make_phone_call, get_phone_call_result" in projection["context"]
    assert "Unavailable MCP tools: <not evaluated>" in projection["context"]
    assert projection["startup_contract"] == {
        "authoritative_first_turn_state": True,
        "complete_objective": True,
        "policy": "start_from_objective",
        "forbid_origin_query_for": [
            "same script",
            "same instructions",
            "same objective",
            "expected behavior",
            "call context already present in the prompt",
        ],
        "pending_origin_query_fallback": "silent_until_result",
    }
    assert "Origin metadata" in projection["context"]


def test_voice_projection_renders_policy_tool_summary_details() -> None:
    tool_summary = _projected_tool_summary("session-1")
    projection = VoiceProjectionService().build_projection(
        session=_session(),
        prompt="Ask which approval item to release.",
        channel="phone",
        metadata={"reason": "approval"},
        tool_summary=tool_summary,
    )

    assert projection["tool_summary"] == tool_summary
    assert (
        "Available MCP tools: make_phone_call, get_phone_call_result (approval required)"
        in projection["context"]
    )
    assert (
        "Unavailable MCP tools: run_script (Tool run_script is not allowed for session session-1)"
        in projection["context"]
    )


def test_voice_projection_instructions_set_natural_phone_behavior() -> None:
    projection = VoiceProjectionService().build_projection(
        session=_session(),
        prompt="Ask which approval item to release.",
        channel="phone",
        metadata={"reason": "approval"},
    )

    instructions = projection["instructions"].lower()
    prompt = projection["prompt"].lower()
    assert "backchannels" in instructions
    assert "do not repeat the prior answer" in instructions
    assert "one or two short spoken paragraphs" in instructions
    assert "pause naturally after a long answer" in instructions
    assert (
        "do not fill pending time with generic startup or context-check scaffolding" in instructions
    )
    assert "give the answer directly" in instructions
    assert "do not narrate internal bookkeeping" in instructions
    assert "authoritative first-turn task state" in instructions
    assert "ask one concise caller-facing clarification question" in instructions
    assert "for reconciliation" not in instructions
    assert "for reconciliation" not in prompt


def test_voice_projection_marks_incomplete_outbound_prompt_for_caller_clarification() -> None:
    projection = VoiceProjectionService().build_projection(
        session=_session(),
        prompt="Call Jonathan.",
        channel="phone",
        metadata={"reason": "approval"},
    )

    assert projection["startup_contract"]["complete_objective"] is False
    assert projection["startup_contract"]["policy"] == "ask_caller_clarification"


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
    assert scheduled[0].enabled is True
    assert "submit_voice_query_response" in scheduled[0].resume_prompt.prompt
    assert completed.status == "answered"
    assert completed.answer == "The migration adds the voice query table."
    assert disable_pending_voice_query_wake(query=completed, wake_store=wake_store) is True
    disabled_request = wake_store.get_request(f"voice-query:{query.query_id}")
    assert disabled_request is not None
    assert disabled_request.enabled is False
    assert disabled_request.next_run_at is None
    payload = voice_query_payload(completed)
    assert payload["citations"] == ["migrations/voice.sql"]

    failed_query = VoiceQueryDelegator(
        store=store,
        wake_store=wake_store,
    ).create_query_and_wake_origin(
        session_record.voice_session_id,
        question="Can you inspect the deployment log?",
    )
    failed = store.complete_query(
        failed_query.query_id,
        origin_session_id="session-1",
        answer="The deployment log is unavailable.",
        status="failed",
    )

    assert failed.status == "failed"
    assert disable_pending_voice_query_wake(query=failed, wake_store=wake_store) is True
    failed_request = wake_store.get_request(f"voice-query:{failed_query.query_id}")
    assert failed_request is not None
    assert failed_request.enabled is False
    assert failed_request.next_run_at is None

    retried_query = VoiceQueryDelegator(
        store=store,
        wake_store=wake_store,
    ).create_query_and_wake_origin(
        session_record.voice_session_id,
        question="Can you retry after a failed wake dispatch?",
    )
    retried_request = wake_store.get_request(f"voice-query:{retried_query.query_id}")
    assert retried_request is not None
    wake_store.record_dispatch(
        WakeDispatchRecord(
            dispatch_id=f"dispatch:{retried_query.query_id}",
            request_id=retried_request.request_id,
            session_id="session-1",
            status="failed",
            resume_prompt=retried_request.resume_prompt,
            resume_mode=retried_request.resume_mode,
            scheduled_for=retried_request.next_run_at,
            source=retried_request.source,
            error="temporary enqueue failure",
        )
    )
    retried_after_failure = wake_store.get_request(retried_request.request_id)
    assert retried_after_failure is not None
    assert retried_after_failure.enabled is True
    completed_after_failure = store.complete_query(
        retried_query.query_id,
        origin_session_id="session-1",
        answer="The retry is no longer needed.",
    )

    assert (
        disable_pending_voice_query_wake(
            query=completed_after_failure,
            wake_store=wake_store,
        )
        is True
    )
    disabled_after_failure = wake_store.get_request(retried_request.request_id)
    assert disabled_after_failure is not None
    assert disabled_after_failure.enabled is False
    assert disabled_after_failure.next_run_at is None


def test_voice_session_retry_clears_terminal_failure_fields(tmp_path: Path) -> None:
    store = VoiceSessionStore(tmp_path / "voice-sessions.json")
    record, created = store.prepare_session(
        origin_session_id="session-1",
        directory_path="root/team-a",
        channel="phone",
        request_id="call-1",
        prompt_metadata={"sha256": "same-prompt"},
        projection={"task": "call"},
    )
    assert created is True
    failed = store.complete_session(
        record.voice_session_id,
        status="failed",
        error="bridge unavailable",
    )
    assert failed.error == "bridge unavailable"
    assert failed.ended_at is not None

    retry_record, retry_created = store.prepare_session(
        origin_session_id="session-1",
        directory_path="root/team-a",
        channel="phone",
        request_id="call-1",
        prompt_metadata={"sha256": "same-prompt"},
        projection={"task": "call"},
    )
    assert retry_created is False

    retried = store.attach_bridge_session(
        retry_record.voice_session_id,
        bridge_session_id="bridge-call-2",
        status="active",
        bridge_metadata={"call_id": "bridge-call-2", "status": "active"},
    )

    assert retried.status == "active"
    assert retried.error is None
    assert retried.ended_at is None


def test_openai_realtime_phone_backend_starts_bridge_without_auto_reconciliation(
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
        tool_summary_provider=lambda session: _projected_tool_summary(session.session_id),
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
    assert result["reconciliation_status"] == "not_requested"
    assert result["audio_quality"]["call_id"] == "bridge-call-1"
    assert result["audio_quality"]["metadata"]["api_key"] == "<redacted>"
    assert result["audio_quality"]["metadata"]["phone_number"] == "<redacted>"
    assert result["metadata"]["bridge"]["audio_quality"] == result["audio_quality"]
    assert wake_store.list_requests(session_id="session-1") == []
    assert bridge.start_calls[0]["projection"]["tool_summary"] == _projected_tool_summary(
        "session-1"
    )
    assert (
        "Available MCP tools: make_phone_call, get_phone_call_result (approval required)"
        in bridge.start_calls[0]["projection"]["context"]
    )
    assert (
        "Unavailable MCP tools: run_script (Tool run_script is not allowed for session session-1)"
        in bridge.start_calls[0]["projection"]["context"]
    )


def test_openai_realtime_phone_backend_enforces_contact_allowlist_before_bridge_request(
    tmp_path: Path,
) -> None:
    store = VoiceSessionStore(tmp_path / "voice-sessions.json")
    wake_store = WakeRequestStore(tmp_path / "wake-requests.json")
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
        reconciler=VoiceSessionReconciler(store=store, wake_store=wake_store),
        tool_summary_provider=lambda session: _projected_tool_summary(session.session_id),
    )
    allowed_session = _session(allowed_contact_ids=["jonathan"])

    started = service.make_phone_call(
        session=allowed_session,
        contact="jonathan",
        prompt="Ask whether to approve item 123.",
        request_id="approval-call-1",
    )
    assert started["call_id"] == "bridge-call-1"
    assert bridge.start_calls[0]["to_number"] == "+15550000002"

    with pytest.raises(
        ValueError,
        match=r"Contact 'support' is not allowed for session 'session-1'",
    ):
        service.make_phone_call(
            session=allowed_session,
            contact="support",
            prompt="Ask whether to approve item 123.",
            request_id="approval-call-2",
        )

    invalid_allowlist_session = _session(
        "session-2",
        allowed_contact_ids=["jonathan", "missing"],
    )
    with pytest.raises(
        ValueError,
        match=(
            r"contacts allowlist for session 'session-2' and directory 'root/team-a' "
            r"references unknown contact ids: missing"
        ),
    ):
        service.make_phone_call(
            session=invalid_allowlist_session,
            contact="jonathan",
            prompt="Ask whether to approve item 123.",
            request_id="approval-call-3",
        )

    assert len(bridge.start_calls) == 1
    assert store.get_by_request_id("approval-call-2") is None
    assert store.get_by_request_id("approval-call-3") is None


def test_runtime_phone_call_service_wires_tool_summary_provider(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root",
            model="gpt-5.4",
            allowed_tools=[ToolName.MAKE_PHONE_CALL, ToolName.GET_PHONE_CALL_RESULT],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    context.policy.allowed_tool_summary_for_session = lambda session: _projected_tool_summary(
        session.session_id
    )

    router = context.phone_call_service_for_session("session-1")

    assert router.realtime_service.tool_summary_provider is not None
    session = context.session_repository.get_session("session-1")
    assert session is not None
    assert router.realtime_service.tool_summary_provider(session) == _projected_tool_summary(
        "session-1"
    )


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
