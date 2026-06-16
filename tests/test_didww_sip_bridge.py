from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError
from starlette.testclient import TestClient

import agent_sandbox.didww_sip_bridge as didww_sip_bridge
from agent_sandbox.didww_sip_bridge import (
    AsteriskAriClient,
    AsteriskCallHandles,
    DidwwAudioQualityEvent,
    DidwwAudioQualityMetrics,
    DidwwAudioQualityWindow,
    DidwwBridgeCallRecord,
    DidwwBridgeStore,
    DidwwSipBridgeConfig,
    DidwwSipBridgeService,
    DidwwAudioQualityTracker,
    InboundPhoneCallStartRequest,
    OpenAIRealtimeSession,
    PhoneCallStartRequest,
    RealtimeFunctionCall,
    RtpPacket,
    RtpUdpEndpoint,
    VoiceMediaControllerClient,
    _realtime_audio_delta,
    _realtime_function_call,
    create_app,
    initial_inbound_call_instructions,
    initial_outbound_call_instructions,
    load_config,
    projection_to_instructions,
)


def _config(tmp_path: Path) -> DidwwSipBridgeConfig:
    return DidwwSipBridgeConfig(
        runtime_dir=tmp_path,
        asterisk_ari_url="http://asterisk.local:8088/ari",
        rtp_port_min=49000,
        rtp_port_max=49010,
        voice_query_poll_interval_seconds=0.01,
        voice_query_timeout_seconds=1,
    )


def _phone_payload() -> dict[str, object]:
    return {
        "voice_session_id": "voice-1",
        "request_id": "request-1",
        "to_number": "+15551234567",
        "from_number": "+15550000001",
        "contact": {"id": "jonathan", "name": "Jonathan"},
        "projection": {
            "prompt": "Call Jonathan and discuss the deployment.",
            "origin": {"session_id": "session-1"},
        },
        "realtime": {"model": "gpt-realtime-2", "api_key_env": "OPENAI_API_KEY"},
        "metadata": {"origin_session_id": "session-1"},
    }


def _inbound_start_payload() -> dict[str, object]:
    return {
        "voice_session_id": "voice-inbound-1",
        "request_id": "inbound-phone:ops-main:SIP/abc-0001",
        "gateway_voice_session_id": "voice-inbound-1",
        "route_id": "ops-main",
        "asterisk_channel_id": "SIP/abc-0001",
        "bridge_call_id": "bridge-1",
        "did_redacted": "***0100",
        "caller_redacted": "***0200",
        "projection": {
            "prompt": "Answer inbound calls for ops.",
            "caller_metadata": {"direction": "inbound"},
        },
        "realtime": {
            "model": "gpt-realtime-2",
            "media_controller_url": "http://gateway.local",
            "media_controller_token_env": "VOICE_MEDIA_CONTROLLER_TOKEN",
        },
        "route": {
            "route_id": "ops-main",
            "directory_path": "root/ops",
            "session_id": "origin-session",
            "did": {"redacted": "***0100", "sha256": "a" * 64},
            "caller": {"redacted": "***0200", "sha256": "b" * 64},
            "asterisk_channel_id": "SIP/abc-0001",
            "bridge_call_id": "bridge-1",
            "sip_headers": {"X-Bridge-Token": "<redacted>"},
        },
        "inbound_instructions": {
            "route_prompt": "Answer inbound calls for ops.",
            "startup_text": "Thanks for calling ops.",
        },
        "metadata": {
            "route": {
                "route_id": "ops-main",
                "directory_path": "root/ops",
            },
        },
    }


def test_bridge_api_starts_and_returns_projected_call_without_leaking_numbers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("DIDWW_SIP_BRIDGE_TOKEN", "bridge-token")
    app = create_app(
        _config(tmp_path),
        store=DidwwBridgeStore(tmp_path / "bridge.json"),
        start_background_tasks=False,
    )
    client = TestClient(app)

    response = client.post(
        "/phone-calls",
        json=_phone_payload(),
        headers={"Authorization": "Bearer bridge-token"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["voice_session_id"] == "voice-1"
    assert body["status"] == "connecting"
    assert body["call_id"].startswith("didww-")

    result = client.get(
        "/voice-sessions/voice-1",
        headers={"Authorization": "Bearer bridge-token"},
    )

    assert result.status_code == 200
    payload = result.json()
    assert payload["metadata"]["to_number"] == "***4567"
    assert payload["metadata"]["from_number"] == "***0001"
    assert "+15551234567" not in json.dumps(payload)


def test_bridge_api_is_idempotent_by_request_id(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DIDWW_SIP_BRIDGE_TOKEN", "bridge-token")
    app = create_app(
        _config(tmp_path),
        store=DidwwBridgeStore(tmp_path / "bridge.json"),
        start_background_tasks=False,
    )
    client = TestClient(app)

    first = client.post(
        "/phone-calls",
        json=_phone_payload(),
        headers={"Authorization": "Bearer bridge-token"},
    )
    second = client.post(
        "/phone-calls",
        json=_phone_payload(),
        headers={"Authorization": "Bearer bridge-token"},
    )

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["call_id"] == first.json()["call_id"]


def test_projection_to_instructions_sets_natural_phone_behavior() -> None:
    instructions = projection_to_instructions(
        {
            "prompt": "Call Jonathan and discuss the deployment.",
            "origin": {"session_id": "session-1"},
            "context": "Allowed tools: query_origin_agent",
        }
    )

    lowered = instructions.lower()
    assert "one or two short spoken paragraphs" in lowered
    assert "outbound startup contract" in lowered
    assert "authoritative first-turn task state" in lowered
    assert "do not ask internally for the same script" in lowered
    assert "backchannels" in lowered
    assert "do not repeat the prior answer" in lowered
    assert "pause naturally after a long answer" in lowered
    assert "do not fill the call with generic startup scaffolding" in lowered
    assert "give the answer directly" in lowered
    assert "do not narrate internal bookkeeping" in lowered
    assert "after a natural goodbye or closing" in lowered
    assert "call end_call to end the phone call" in lowered
    assert "for reconciliation" not in lowered


def test_initial_outbound_call_uses_complete_objective_without_startup_scaffolding() -> None:
    objective = (
        "Open with: Hi Jonathan, this is Truman calling about the auto-dev approval queue. "
        "Briefly summarize the candidate work items and ask which IDs should be approved."
    )
    instructions = initial_outbound_call_instructions(
        {
            "task_prompt": objective,
            "startup_contract": {"complete_objective": True},
        }
    )
    lowered = instructions.lower()

    assert "authoritative first-turn task state" in lowered
    assert f"caller-facing opening/objective:\n{objective}".lower() in lowered
    assert "do not ask for the script" in lowered
    assert "checking context" not in lowered
    assert "missing task details" not in lowered
    assert "what would you like to do" not in lowered


def test_initial_outbound_call_with_incomplete_prompt_asks_caller_clarification() -> None:
    instructions = initial_outbound_call_instructions(
        {
            "task_prompt": "Call Jonathan.",
            "startup_contract": {"complete_objective": False},
        }
    )
    lowered = instructions.lower()

    assert "ask exactly one concise caller-facing clarification question" in lowered
    assert "about the call objective" in lowered
    assert "supplied task:\ncall jonathan." in lowered
    assert "what would you like to do" not in lowered
    assert "checking context" not in lowered


def test_projection_to_instructions_uses_inbound_startup_contract_for_inbound_calls() -> None:
    instructions = projection_to_instructions(
        {
            "prompt": "Answer inbound calls for ops.",
            "caller_metadata": {"direction": "inbound"},
            "context": "Allowed tools: query_origin_agent",
        }
    )

    lowered = instructions.lower()
    assert "inbound startup contract" in lowered
    assert "outbound startup contract" not in lowered
    assert "do not use outbound startup wording" in lowered
    assert "caller metadata" in lowered
    assert "after a natural goodbye or closing" in lowered
    assert "call end_call to end the phone call" in lowered


def test_initial_inbound_call_instructions_use_route_specific_opening() -> None:
    instructions = initial_inbound_call_instructions(
        {
            "route_prompt": "Answer inbound calls for ops.",
            "startup_text": "Thanks for calling ops.",
        },
        {
            "prompt": "Answer inbound calls for ops.",
            "caller_metadata": {"direction": "inbound"},
        },
    )

    lowered = instructions.lower()
    assert "start the inbound phone call now" in lowered
    assert "thanks for calling ops." in lowered
    assert "route prompt" in lowered
    assert "outbound startup wording" in lowered


def test_inbound_bridge_store_creates_idempotent_redacted_records(tmp_path: Path) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    payload = InboundPhoneCallStartRequest.model_validate(_inbound_start_payload())

    record, created = store.create_inbound_call(payload)
    duplicate, duplicate_created = store.create_inbound_call(payload)

    assert created is True
    assert duplicate_created is False
    assert duplicate.voice_session_id == record.voice_session_id
    assert record.direction == "inbound"
    assert record.route_id == "ops-main"
    assert record.gateway_voice_session_id == "voice-inbound-1"
    assert record.did_redacted == "***0100"
    assert record.caller_redacted == "***0200"
    assert record.contact["id"] == "ops-main"
    payload_json = json.dumps(record.response_payload(), sort_keys=True)
    assert "+15550000100" not in payload_json
    assert "+15550000200" not in payload_json


def test_bridge_store_persists_audio_quality_and_indexes_by_call_id(
    tmp_path: Path,
) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    payload = PhoneCallStartRequest.model_validate(_phone_payload())

    record, created = store.create_call(payload)

    assert created is True
    metrics = DidwwAudioQualityMetrics(
        voice_session_id=record.voice_session_id,
        call_id=record.call_id,
        direction=record.direction,
        media_format="alaw",
        rtp_payload_type=8,
        sample_rate_hz=8000,
        frame_duration_ms=20,
        rtp_port=49100,
        route_id="route-1",
        asterisk_pjsip_endpoint="didww",
        carrier="+15551234567 Carrier",
        region="us-east-1",
        inbound_packets=2,
        inbound_bytes=160,
        inbound_samples=160,
        outbound_packets=3,
        outbound_bytes=240,
        outbound_samples=240,
        lost_packets=1,
        late_packets=1,
        out_of_order_packets=1,
        duplicate_packets=1,
        malformed_packets=1,
        decode_errors=1,
        jitter_ms=12.5,
        queue_depth_max=4,
        windows=[
            DidwwAudioQualityWindow(
                inbound_packets=index,
                outbound_packets=index + 1,
                queue_depth_max=index,
            )
            for index in range(20)
        ],
        anomaly_events=[
            DidwwAudioQualityEvent(
                event_type=f"audio.{index}",
                message=f"carrier +15551234567 {index}",
                metadata={"ordinal": index},
            )
            for index in range(70)
        ],
    )

    updated = store.update_audio_quality(record.voice_session_id, metrics)
    assert updated.audio_quality is not None
    assert updated.audio_quality.voice_session_id == record.voice_session_id
    assert len(updated.audio_quality.windows) <= 16
    assert len(updated.audio_quality.anomaly_events) <= 64

    reloaded_store = DidwwBridgeStore(tmp_path / "bridge.json")
    reloaded = reloaded_store.get_by_call_id(record.call_id)

    assert reloaded is not None
    assert reloaded.call_id == record.call_id
    assert reloaded.audio_quality is not None
    assert reloaded.audio_quality.model_dump(mode="json") == updated.audio_quality.model_dump(
        mode="json"
    )
    assert reloaded.response_payload()["audio_quality"] == updated.audio_quality.model_dump(
        mode="json"
    )
    response_json = json.dumps(reloaded.response_payload(), sort_keys=True)
    assert "+15551234567" not in response_json


def test_bridge_store_reads_legacy_call_records_without_audio_quality(
    tmp_path: Path,
) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))

    raw = json.loads((tmp_path / "bridge.json").read_text(encoding="utf-8"))
    raw["calls"][record.voice_session_id].pop("audio_quality", None)
    (tmp_path / "bridge.json").write_text(
        json.dumps(raw, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    legacy_store = DidwwBridgeStore(tmp_path / "bridge.json")
    loaded = legacy_store.get(record.voice_session_id)

    assert loaded is not None
    assert loaded.audio_quality is None
    assert loaded.response_payload()["audio_quality"] is None
    assert legacy_store.get_by_call_id(record.call_id) is not None


def test_debug_events_are_exposed_without_changing_transcript_events(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("DIDWW_SIP_BRIDGE_TOKEN", "bridge-token")
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    store.append_transcript_event(
        "voice-1",
        speaker="caller",
        text="We should deploy tonight.",
        metadata={"event_type": "conversation.item.input_audio_transcription.completed"},
    )
    stored = store.get("voice-1")
    assert stored is not None
    before = [
        event.model_dump(mode="json")
        for event in stored.transcript_events
    ]

    store.append_debug_event(
        "voice-1",
        "test.debug",
        metadata={
            "snippet": f"Call me at +15551234567. {'x' * 300}",
            "api_key": "should-not-persist",
        },
    )

    latest = store.get("voice-1")
    assert latest is not None
    assert [event.model_dump(mode="json") for event in latest.transcript_events] == before
    assert latest.debug_events[0].sequence == 1
    assert latest.debug_events[0].event_type == "test.debug"
    assert latest.debug_events[0].metadata["api_key"] == "<redacted>"
    assert len(latest.debug_events[0].metadata["snippet"]) <= 200
    debug_json = json.dumps(
        [event.model_dump(mode="json") for event in latest.debug_events],
        sort_keys=True,
    )
    assert "+15551234567" not in debug_json
    assert "should-not-persist" not in debug_json

    app = create_app(_config(tmp_path), store=store, start_background_tasks=False)
    client = TestClient(app)
    response = client.get(
        "/voice-sessions/voice-1",
        headers={"Authorization": "Bearer bridge-token"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["debug_events"][0]["event_type"] == "test.debug"
    assert payload["transcript_events"] == before


def test_bridge_api_requires_bearer_token(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DIDWW_SIP_BRIDGE_TOKEN", "bridge-token")
    app = create_app(_config(tmp_path), start_background_tasks=False)
    client = TestClient(app)

    response = client.post("/phone-calls", json=_phone_payload())

    assert response.status_code == 401


def test_asterisk_ari_client_origination_contract(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ASTERISK_ARI_PASSWORD", "ari-secret")
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/ari/channels/externalMedia":
            return httpx.Response(
                200,
                json={
                    "id": "media-didww-test",
                    "channelvars": {
                        "UNICASTRTP_LOCAL_ADDRESS": "127.0.0.1",
                        "UNICASTRTP_LOCAL_PORT": "27086",
                    },
                },
            )
        return httpx.Response(200, json={"id": request.url.path.rsplit("/", 1)[-1]})

    async def run() -> AsteriskCallHandles:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            ari = AsteriskAriClient(_config(tmp_path), http_client=client)
            return await ari.start_external_media_call(
                call_id="didww-test",
                to_number="+15551234567",
                from_number="+15550000001",
                external_host="127.0.0.1:49000",
            )

    handles = asyncio.run(run())

    assert handles.call_channel_id == "call-didww-test"
    assert handles.external_media_local_addr == ("127.0.0.1", 27086)
    assert [request.method for request in requests] == ["POST", "POST", "POST", "POST", "POST"]
    assert requests[0].url.path == "/ari/bridges/bridge-didww-test"
    assert requests[1].url.path == "/ari/channels/call-didww-test"
    assert requests[1].url.params["endpoint"] == "PJSIP/+15551234567@didww"
    assert requests[2].url.path == "/ari/channels/externalMedia"
    assert requests[2].url.params["external_host"] == "127.0.0.1:49000"
    assert requests[2].url.params["format"] == "alaw"
    assert requests[3].url.path == "/ari/bridges/bridge-didww-test/addChannel"
    assert requests[4].url.path == "/ari/bridges/bridge-didww-test/addChannel"


def test_voice_media_controller_client_provisions_and_syncs_inbound_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VOICE_MEDIA_CONTROLLER_TOKEN", "controller-token")
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/voice-sessions/inbound-phone-calls":
            return httpx.Response(
                201,
                json={
                    "voice_session_id": "voice-inbound-1",
                    "request_id": "inbound-phone:ops-main:SIP/abc-0001",
                },
            )
        if request.url.path == "/voice-sessions/voice-inbound-1/bridge-result":
            return httpx.Response(
                200,
                json={
                    "voice_session_id": "voice-inbound-1",
                    "status": "ended",
                    "reconciliation_status": "not_requested",
                },
            )
        return httpx.Response(404, json={"error": "unexpected path"})

    async def run() -> tuple[dict[str, Any], dict[str, Any]]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            controller = VoiceMediaControllerClient(
                base_url="http://gateway.local/",
                token_env="VOICE_MEDIA_CONTROLLER_TOKEN",
                timeout_seconds=1,
                http_client=client,
            )
            provisioned = await controller.create_inbound_phone_call(
                {
                    "did": "+15550000100",
                    "caller_number": "+15550000200",
                    "asterisk_channel_id": "SIP/abc-0001",
                    "bridge_call_id": "bridge-1",
                }
            )
            synced = await controller.sync_voice_session(
                "voice-inbound-1",
                {"status": "ended", "transcript": "caller: hello"},
            )
            return provisioned, synced

    provisioned, synced = asyncio.run(run())

    assert provisioned["voice_session_id"] == "voice-inbound-1"
    assert synced["reconciliation_status"] == "not_requested"
    assert [request.url.path for request in requests] == [
        "/voice-sessions/inbound-phone-calls",
        "/voice-sessions/voice-inbound-1/bridge-result",
    ]
    assert all(request.headers["authorization"] == "Bearer controller-token" for request in requests)


def test_asterisk_ari_client_waits_for_stasis_before_bridge_add(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ASTERISK_ARI_PASSWORD", "ari-secret")
    events_seen: list[str] = []

    class FakeWebSocket:
        def __init__(self) -> None:
            self.events = [
                {
                    "type": "StasisStart",
                    "channel": {"id": "media-didww-test"},
                },
                {
                    "type": "StasisStart",
                    "channel": {"id": "call-didww-test"},
                },
            ]

        async def recv(self) -> str:
            event = self.events.pop(0)
            events_seen.append(str(event["channel"]["id"]))
            return json.dumps(event)

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/addChannel"):
            assert events_seen == ["media-didww-test", "call-didww-test"]
        return httpx.Response(200, json={"id": request.url.path.rsplit("/", 1)[-1]})

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            ari = AsteriskAriClient(_config(tmp_path), http_client=client)
            await ari.start_external_media_call(
                call_id="didww-test",
                to_number="+15551234567",
                from_number="+15550000001",
                external_host="127.0.0.1:49000",
                event_websocket=FakeWebSocket(),
            )

    asyncio.run(run())


def test_asterisk_ari_client_reports_hangup_before_stasis(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ASTERISK_ARI_PASSWORD", "ari-secret")

    class FakeWebSocket:
        async def recv(self) -> str:
            return json.dumps(
                {
                    "type": "ChannelDestroyed",
                    "channel": {"id": "call-didww-test"},
                    "cause_txt": "busy",
                }
            )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": request.url.path.rsplit("/", 1)[-1]})

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            ari = AsteriskAriClient(_config(tmp_path), http_client=client)
            await ari.start_external_media_call(
                call_id="didww-test",
                to_number="+15551234567",
                from_number="+15550000001",
                external_host="127.0.0.1:49000",
                event_websocket=FakeWebSocket(),
            )

    try:
        asyncio.run(run())
    except RuntimeError as exc:
        assert "ended before entering Stasis: busy" in str(exc)
    else:  # pragma: no cover - defensive assertion clarity
        raise AssertionError("expected RuntimeError")


def test_asterisk_ari_client_starts_external_media_for_inbound_calls(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ASTERISK_ARI_PASSWORD", "ari-secret")
    requests: list[httpx.Request] = []

    class FakeWebSocket:
        def __init__(self) -> None:
            self.events = [
                {
                    "type": "StasisStart",
                    "channel": {"id": "media-inbound-test"},
                }
            ]

        async def recv(self) -> str:
            return json.dumps(self.events.pop(0))

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/ari/channels/externalMedia":
            return httpx.Response(
                200,
                json={
                    "id": "media-inbound-test",
                    "channelvars": {
                        "UNICASTRTP_LOCAL_ADDRESS": "127.0.0.1",
                        "UNICASTRTP_LOCAL_PORT": "27086",
                    },
                },
            )
        return httpx.Response(200, json={"id": request.url.path.rsplit("/", 1)[-1]})

    async def run() -> AsteriskCallHandles:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            ari = AsteriskAriClient(_config(tmp_path), http_client=client)
            return await ari.start_external_media_for_inbound(
                call_id="inbound-test",
                inbound_channel_id="SIP/abc-0001",
                external_host="127.0.0.1:49000",
                event_websocket=FakeWebSocket(),
            )

    handles = asyncio.run(run())

    assert handles.call_channel_id == "SIP/abc-0001"
    assert handles.external_media_local_addr == ("127.0.0.1", 27086)
    assert [request.method for request in requests] == ["POST", "POST", "POST", "POST", "POST"]
    assert requests[0].url.path == "/ari/bridges/bridge-inbound-test"
    assert requests[1].url.path == "/ari/channels/SIP/abc-0001/answer"
    assert requests[2].url.path == "/ari/channels/externalMedia"
    assert requests[3].url.path == "/ari/bridges/bridge-inbound-test/addChannel"
    assert requests[4].url.path == "/ari/bridges/bridge-inbound-test/addChannel"


def test_rtp_packet_roundtrip() -> None:
    packet = RtpPacket(
        sequence=42,
        timestamp=320,
        ssrc=1234,
        payload_type=8,
        payload=b"\x01\x02\x03\x04",
        marker=True,
    )

    parsed = RtpPacket.parse(packet.serialize())

    assert parsed.sequence == 42
    assert parsed.timestamp == 320
    assert parsed.ssrc == 1234
    assert parsed.payload_type == 8
    assert parsed.marker is True
    assert parsed.payload == b"\x01\x02\x03\x04"


def test_audio_quality_tracker_handles_sequence_wrap_loss_late_and_duplicates() -> None:
    tracker = DidwwAudioQualityTracker(
        voice_session_id="voice-1",
        call_id="call-1",
        media_format="alaw",
        rtp_payload_type=8,
        sample_rate_hz=8000,
        frame_duration_ms=20,
    )
    payload = b"\x55" * 160

    tracker.record_inbound_packet(RtpPacket(65534, 1000, 1, 8, payload), received_at_monotonic=0.0)
    tracker.record_inbound_packet(RtpPacket(65535, 1160, 1, 8, payload), received_at_monotonic=0.02)
    tracker.record_inbound_packet(RtpPacket(0, 1320, 1, 8, payload), received_at_monotonic=0.04)
    tracker.record_inbound_packet(RtpPacket(2, 1480, 1, 8, payload), received_at_monotonic=0.06)
    tracker.record_inbound_packet(RtpPacket(1, 1640, 1, 8, payload), received_at_monotonic=0.08)
    tracker.record_inbound_packet(RtpPacket(2, 1800, 1, 8, payload), received_at_monotonic=0.10)

    snapshot = tracker.snapshot()

    assert snapshot.inbound_packets == 6
    assert snapshot.lost_packets == 1
    assert snapshot.late_packets == 1
    assert snapshot.duplicate_packets == 1
    assert snapshot.out_of_order_packets == 0
    assert snapshot.queue_depth_max == 0
    assert snapshot.windows[0].inbound_packets == 6
    assert snapshot.anomaly_events


def test_audio_quality_tracker_marks_out_of_order_frame_gaps_and_malformed_packets() -> None:
    tracker = DidwwAudioQualityTracker(
        voice_session_id="voice-2",
        call_id="call-2",
        media_format="alaw",
        rtp_payload_type=8,
        sample_rate_hz=8000,
        frame_duration_ms=20,
    )
    payload = b"\x55" * 160

    tracker.record_inbound_packet(RtpPacket(5, 1000, 1, 8, payload), received_at_monotonic=0.0)
    tracker.record_inbound_packet(RtpPacket(3, 1160, 1, 8, payload), received_at_monotonic=0.01)
    tracker.record_malformed_packet(reason="RTP packet is too short", payload_length=8)
    tracker.record_inbound_packet(RtpPacket(6, 1320, 1, 8, payload), received_at_monotonic=0.08)
    tracker.record_queue_depth(9)

    snapshot = tracker.snapshot()

    assert snapshot.out_of_order_packets == 1
    assert snapshot.malformed_packets == 1
    assert snapshot.queue_depth_max == 9
    assert snapshot.jitter_ms > 0
    event_types = [event.event_type for event in snapshot.anomaly_events]
    assert "rtp.packet.out_of_order" in event_types
    assert "rtp.packet.malformed" in event_types
    assert "rtp.frame_gap" in event_types


def test_rtp_datagram_protocol_tracks_malformed_and_payload_type_mismatch_without_dropping_payload(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    tracker = DidwwAudioQualityTracker(
        voice_session_id="voice-3",
        call_id="call-3",
        media_format="alaw",
        rtp_payload_type=config.rtp_payload_type,
        sample_rate_hz=config.sample_rate_hz,
        frame_duration_ms=config.frame_duration_ms,
    )
    endpoint = RtpUdpEndpoint(config, tracker=tracker)
    protocol = didww_sip_bridge.RtpDatagramProtocol(endpoint)
    received: list[bytes] = []

    class FakeTransport:
        def sendto(self, data: bytes, addr: tuple[str, int]) -> None:
            received.append(data)

    endpoint.transport = FakeTransport()  # type: ignore[assignment]
    endpoint.remote_addr = ("127.0.0.1", 49999)

    malformed = b"\x80\x08\x00"
    protocol.datagram_received(malformed, ("127.0.0.1", 60000))

    packet = RtpPacket(sequence=1, timestamp=0, ssrc=1, payload_type=0, payload=b"\x55" * 160)
    protocol.datagram_received(packet.serialize(), ("127.0.0.1", 60000))

    assert len(received) == 0
    assert asyncio.run(endpoint.recv_payload()) == b"\x55" * 160
    snapshot = tracker.snapshot()
    assert snapshot.malformed_packets == 1
    assert snapshot.inbound_packets == 1
    event_types = [event.event_type for event in snapshot.anomaly_events]
    assert "rtp.packet.malformed" in event_types
    assert "rtp.packet.payload_type_mismatch" in event_types


def test_rtp_endpoint_chunks_output_payloads_by_frame_duration(tmp_path: Path) -> None:
    config = _config(tmp_path)
    tracker = DidwwAudioQualityTracker(
        voice_session_id="voice-4",
        call_id="call-4",
        media_format=config.media_format,
        rtp_payload_type=config.rtp_payload_type,
        sample_rate_hz=config.sample_rate_hz,
        frame_duration_ms=config.frame_duration_ms,
    )
    endpoint = RtpUdpEndpoint(config, tracker=tracker)
    endpoint.remote_addr = ("127.0.0.1", 49999)
    sent: list[tuple[bytes, tuple[str, int]]] = []

    class FakeTransport:
        def sendto(self, data: bytes, addr: tuple[str, int]) -> None:
            sent.append((data, addr))

    endpoint.transport = FakeTransport()  # type: ignore[assignment]
    samples_per_frame = int(config.sample_rate_hz * config.frame_duration_ms / 1000)
    payload = b"\x55" * ((samples_per_frame * 2) + 80)

    reported_samples: list[int] = []
    sent_samples = asyncio.run(
        endpoint.send_payload(payload, on_samples_sent=reported_samples.append)
    )

    packets = [RtpPacket.parse(data) for data, _addr in sent]
    assert [len(packet.payload) for packet in packets] == [
        samples_per_frame,
        samples_per_frame,
        80,
    ]
    assert reported_samples == [samples_per_frame, samples_per_frame, 80]
    assert sent_samples == (samples_per_frame * 2) + 80
    assert [packet.sequence for packet in packets] == [1, 2, 3]
    assert [packet.timestamp for packet in packets] == [
        0,
        samples_per_frame,
        samples_per_frame * 2,
    ]
    assert endpoint._timestamp == (samples_per_frame * 2) + 80
    assert tracker.outbound_packets == 3
    assert tracker.outbound_samples == (samples_per_frame * 2) + 80


def test_rtp_endpoint_reports_zero_samples_without_remote_address(tmp_path: Path) -> None:
    config = _config(tmp_path)
    endpoint = RtpUdpEndpoint(config)
    sent: list[tuple[bytes, tuple[str, int]]] = []
    reported_samples: list[int] = []

    class FakeTransport:
        def sendto(self, data: bytes, addr: tuple[str, int]) -> None:
            sent.append((data, addr))

    endpoint.transport = FakeTransport()  # type: ignore[assignment]

    sent_samples = asyncio.run(
        endpoint.send_payload(b"\x55" * 160, on_samples_sent=reported_samples.append)
    )

    assert sent_samples == 0
    assert reported_samples == []
    assert sent == []


def test_realtime_audio_delta_accepts_current_and_legacy_event_names() -> None:
    assert _realtime_audio_delta({"type": "response.output_audio.delta", "delta": "abc"}) == "abc"
    assert _realtime_audio_delta({"type": "response.audio.delta", "delta": "def"}) == "def"
    assert _realtime_audio_delta({"type": "response.output_text.delta", "delta": "nope"}) is None


def test_realtime_function_call_parser_accepts_output_item_done() -> None:
    parsed = _realtime_function_call(
        {
            "type": "response.output_item.done",
            "item": {
                "type": "function_call",
                "call_id": "call-tool-1",
                "name": "query_origin_agent",
                "arguments": "{\"question\":\"What changed?\"}",
            },
        }
    )

    assert parsed == RealtimeFunctionCall(
        call_id="call-tool-1",
        name="query_origin_agent",
        arguments={"question": "What changed?"},
    )


def test_realtime_session_uses_bridge_local_openai_secret_and_base_url(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "host-openai-key")
    monkeypatch.setenv("ATTACKER_SECRET", "do-not-send")
    captured: dict[str, Any] = {}

    class FakeWebSocket:
        async def send(self, text: str) -> None:
            captured["session_update"] = json.loads(text)

        async def close(self) -> None:
            captured["closed"] = True

    async def fake_connect(url: str, **kwargs: Any) -> FakeWebSocket:
        captured["url"] = url
        captured["headers"] = kwargs["additional_headers"]
        return FakeWebSocket()

    import websockets

    monkeypatch.setattr(websockets, "connect", fake_connect)
    config = _config(tmp_path).model_copy(
        update={
            "openai_api_key_env": "OPENAI_API_KEY",
            "openai_realtime_api_base_url": "https://api.openai.test/v1",
        }
    )

    async def run() -> None:
        async with OpenAIRealtimeSession(
            config=config,
            projection={"prompt": "Call Jonathan."},
            realtime={
                "api_key_env": "ATTACKER_SECRET",
                "api_base_url": "https://attacker.example/v1",
                "model": "gpt-realtime-2",
            },
        ):
            return None

    asyncio.run(run())

    assert captured["url"] == "wss://api.openai.test/v1/realtime?model=gpt-realtime-2"
    assert captured["headers"] == {"Authorization": "Bearer host-openai-key"}
    assert captured["session_update"]["type"] == "session.update"
    session = captured["session_update"]["session"]
    assert session["audio"]["input"]["format"] == {"type": "audio/pcma"}
    assert session["audio"]["output"]["format"] == {"type": "audio/pcma"}
    assert session["audio"]["input"]["noise_reduction"] == {"type": "near_field"}
    turn_detection = session["audio"]["input"]["turn_detection"]
    assert turn_detection["type"] == "server_vad"
    assert turn_detection["create_response"] is False
    assert turn_detection["interrupt_response"] is False
    assert turn_detection["threshold"] == 0.7
    assert turn_detection["prefix_padding_ms"] == 300
    assert turn_detection["silence_duration_ms"] == 800
    assert "idle_timeout_ms" not in turn_detection
    assert session["tool_choice"] == "auto"
    assert [tool["name"] for tool in session["tools"]] == ["end_call"]
    assert session["tools"][0]["parameters"]["properties"]["reason"]["type"] == "string"


def test_realtime_session_with_media_controller_includes_query_origin_agent_tool(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "host-openai-key")
    captured: dict[str, Any] = {}

    class FakeWebSocket:
        async def send(self, text: str) -> None:
            captured["session_update"] = json.loads(text)

        async def close(self) -> None:
            captured["closed"] = True

    async def fake_connect(url: str, **kwargs: Any) -> FakeWebSocket:
        captured["url"] = url
        captured["headers"] = kwargs["additional_headers"]
        return FakeWebSocket()

    import websockets

    monkeypatch.setattr(websockets, "connect", fake_connect)
    config = _config(tmp_path).model_copy(
        update={
            "openai_api_key_env": "OPENAI_API_KEY",
            "openai_realtime_api_base_url": "https://api.openai.test/v1",
        }
    )

    async def run() -> None:
        async with OpenAIRealtimeSession(
            config=config,
            projection={"prompt": "Call Jonathan."},
            realtime={
                "api_key_env": "OPENAI_API_KEY",
                "api_base_url": "https://api.openai.test/v1",
                "media_controller_url": "http://controller.local",
                "model": "gpt-realtime-2",
            },
        ):
            return None

    asyncio.run(run())

    assert captured["url"] == "wss://api.openai.test/v1/realtime?model=gpt-realtime-2"
    assert captured["headers"] == {"Authorization": "Bearer host-openai-key"}
    session = captured["session_update"]["session"]
    assert session["tool_choice"] == "auto"
    assert [tool["name"] for tool in session["tools"]] == ["end_call", "query_origin_agent"]
    assert session["tools"][1]["parameters"]["required"] == ["question"]


def test_barge_in_config_defaults_and_env_overrides(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = DidwwSipBridgeConfig(runtime_dir=tmp_path)

    assert config.barge_in_delay_ms == 700
    assert config.goodbye_idle_hangup_seconds == 0.0
    assert config.backchannel_max_words == 3
    assert config.backchannel_phrases == [
        "ok",
        "okay",
        "uh-huh",
        "mhm",
        "yeah",
        "yes",
        "good",
        "great",
        "thanks",
        "thank you",
    ]

    monkeypatch.setenv("DIDWW_SIP_BRIDGE_BARGE_IN_DELAY_MS", "250")
    monkeypatch.setenv("DIDWW_SIP_BRIDGE_GOODBYE_IDLE_HANGUP_SECONDS", "0.25")
    monkeypatch.setenv("DIDWW_SIP_BRIDGE_BACKCHANNEL_MAX_WORDS", "2")
    monkeypatch.setenv("DIDWW_SIP_BRIDGE_BACKCHANNEL_PHRASES", "yep, right")

    loaded = load_config(tmp_path)

    assert loaded.barge_in_delay_ms == 250
    assert loaded.goodbye_idle_hangup_seconds == 0.25
    assert loaded.backchannel_max_words == 2
    assert loaded.backchannel_phrases == ["yep", "right"]


def test_realtime_config_defaults_and_env_overrides(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = DidwwSipBridgeConfig(runtime_dir=tmp_path)

    assert config.openai_realtime_input_noise_reduction_type == "near_field"
    assert config.openai_realtime_turn_detection_type == "server_vad"
    assert config.openai_realtime_vad_threshold == 0.7
    assert config.openai_realtime_vad_prefix_padding_ms == 300
    assert config.openai_realtime_vad_silence_duration_ms == 800

    monkeypatch.setenv("OPENAI_REALTIME_INPUT_NOISE_REDUCTION_TYPE", "far_field")
    monkeypatch.setenv("OPENAI_REALTIME_TURN_DETECTION_TYPE", "semantic_vad")
    monkeypatch.setenv("OPENAI_REALTIME_VAD_THRESHOLD", "0.82")
    monkeypatch.setenv("OPENAI_REALTIME_VAD_PREFIX_PADDING_MS", "125")
    monkeypatch.setenv("OPENAI_REALTIME_VAD_SILENCE_DURATION_MS", "950")

    loaded = load_config(tmp_path)

    assert loaded.openai_realtime_input_noise_reduction_type == "far_field"
    assert loaded.openai_realtime_turn_detection_type == "semantic_vad"
    assert loaded.openai_realtime_vad_threshold == 0.82
    assert loaded.openai_realtime_vad_prefix_padding_ms == 125
    assert loaded.openai_realtime_vad_silence_duration_ms == 950

    monkeypatch.setenv("OPENAI_REALTIME_INPUT_NOISE_REDUCTION_TYPE", "disabled")
    assert load_config(tmp_path).openai_realtime_input_noise_reduction_type == "disabled"


def test_voice_query_config_defaults_and_env_overrides(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = DidwwSipBridgeConfig(runtime_dir=tmp_path)

    assert config.voice_query_poll_interval_seconds == 1.0
    assert config.voice_query_caller_patience_seconds == 18.0
    assert config.voice_query_timeout_seconds == 90.0

    monkeypatch.setenv("DIDWW_SIP_BRIDGE_VOICE_QUERY_POLL_INTERVAL_SECONDS", "0.25")
    monkeypatch.setenv("DIDWW_SIP_BRIDGE_VOICE_QUERY_CALLER_PATIENCE_SECONDS", "12.5")
    monkeypatch.setenv("DIDWW_SIP_BRIDGE_VOICE_QUERY_TIMEOUT_SECONDS", "31")

    loaded = load_config(tmp_path)

    assert loaded.voice_query_poll_interval_seconds == 0.25
    assert loaded.voice_query_caller_patience_seconds == 12.5
    assert loaded.voice_query_timeout_seconds == 31.0


def test_realtime_config_rejects_invalid_modes_and_ranges(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        DidwwSipBridgeConfig(
            runtime_dir=tmp_path,
            openai_realtime_input_noise_reduction_type="room_mode",
        )

    with pytest.raises(ValidationError):
        DidwwSipBridgeConfig(runtime_dir=tmp_path, openai_realtime_turn_detection_type="vad")

    with pytest.raises(ValidationError):
        DidwwSipBridgeConfig(runtime_dir=tmp_path, openai_realtime_vad_threshold=1.1)

    with pytest.raises(ValidationError):
        DidwwSipBridgeConfig(runtime_dir=tmp_path, openai_realtime_vad_prefix_padding_ms=-1)

    with pytest.raises(ValidationError):
        DidwwSipBridgeConfig(runtime_dir=tmp_path, openai_realtime_vad_silence_duration_ms=5001)


def test_realtime_session_config_error_retries_once_for_unsupported_optional_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "host-openai-key")
    captured: dict[str, Any] = {"session_updates": []}
    stop_event = asyncio.Event()

    class FakeWebSocket:
        def __init__(self) -> None:
            self.events = [
                json.dumps(
                    {
                        "type": "error",
                        "error": {
                            "code": "unsupported_value",
                            "type": "invalid_request_error",
                            "param": "session.audio.input.noise_reduction.type",
                            "message": (
                                "noise_reduction type far_field is not supported; "
                                "semantic_vad is also not supported"
                            ),
                        },
                    }
                )
            ]

        async def send(self, text: str) -> None:
            captured["session_updates"].append(json.loads(text))

        async def recv(self) -> str:
            if self.events:
                return self.events.pop(0)
            stop_event.set()
            await asyncio.sleep(0)
            return json.dumps({})

        async def close(self) -> None:
            captured["closed"] = True

    async def fake_connect(url: str, **kwargs: Any) -> FakeWebSocket:
        captured["url"] = url
        captured["headers"] = kwargs["additional_headers"]
        return FakeWebSocket()

    import websockets

    monkeypatch.setattr(websockets, "connect", fake_connect)
    config = _config(tmp_path).model_copy(
        update={
            "openai_realtime_input_noise_reduction_type": "far_field",
            "openai_realtime_turn_detection_type": "semantic_vad",
        }
    )
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    service = DidwwSipBridgeService(config, store=store, start_background_tasks=False)

    class FakeController:
        configured = False

    class FakeRtp:
        pass

    async def run() -> None:
        async with OpenAIRealtimeSession(
            config=config,
            projection={"prompt": "Call Jonathan."},
            realtime={"model": "gpt-realtime-2"},
        ) as realtime:
            await asyncio.wait_for(
                service._realtime_to_rtp(  # type: ignore[arg-type]
                    record.voice_session_id,
                    FakeRtp(),  # type: ignore[arg-type]
                    realtime,
                    FakeController(),  # type: ignore[arg-type]
                    stop_event,
                ),
                timeout=1,
            )

    asyncio.run(run())

    assert captured["url"] == "wss://api.openai.com/v1/realtime?model=gpt-realtime-2"
    assert captured["headers"] == {"Authorization": "Bearer host-openai-key"}
    assert len(captured["session_updates"]) == 2
    initial_session = captured["session_updates"][0]["session"]
    fallback_session = captured["session_updates"][1]["session"]
    assert initial_session["audio"]["input"]["noise_reduction"] == {"type": "far_field"}
    assert initial_session["audio"]["input"]["turn_detection"]["type"] == "semantic_vad"
    assert "threshold" not in initial_session["audio"]["input"]["turn_detection"]
    assert "noise_reduction" not in fallback_session["audio"]["input"]
    fallback_turn_detection = fallback_session["audio"]["input"]["turn_detection"]
    assert fallback_turn_detection["type"] == "server_vad"
    assert fallback_turn_detection["create_response"] is False
    assert fallback_turn_detection["interrupt_response"] is False
    assert fallback_turn_detection["threshold"] == 0.7
    assert fallback_turn_detection["prefix_padding_ms"] == 300
    assert fallback_turn_detection["silence_duration_ms"] == 800
    latest = store.get("voice-1")
    assert latest is not None
    event_types = [event.event_type for event in latest.debug_events]
    assert "error" in event_types
    assert "realtime.session_config_fallback" in event_types
    fallback_event = next(
        event for event in latest.debug_events if event.event_type == "realtime.session_config_fallback"
    )
    assert fallback_event.metadata["removed_features"] == [
        "audio.input.noise_reduction",
        "audio.input.turn_detection.type",
    ]
    assert fallback_event.metadata["fallback_turn_detection_type"] == "server_vad"
    assert fallback_event.metadata["noise_reduction_omitted"] is True


def test_realtime_generic_session_error_remains_fatal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "host-openai-key")
    captured: dict[str, Any] = {"session_updates": []}
    stop_event = asyncio.Event()

    class FakeWebSocket:
        def __init__(self) -> None:
            self.events = [
                json.dumps(
                    {
                        "type": "error",
                        "error": {
                            "code": "server_error",
                            "type": "server_error",
                            "message": "internal failure",
                        },
                    }
                )
            ]

        async def send(self, text: str) -> None:
            captured["session_updates"].append(json.loads(text))

        async def recv(self) -> str:
            if self.events:
                return self.events.pop(0)
            stop_event.set()
            await asyncio.sleep(0)
            return json.dumps({})

        async def close(self) -> None:
            captured["closed"] = True

    async def fake_connect(url: str, **kwargs: Any) -> FakeWebSocket:
        captured["url"] = url
        captured["headers"] = kwargs["additional_headers"]
        return FakeWebSocket()

    import websockets

    monkeypatch.setattr(websockets, "connect", fake_connect)
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    service = DidwwSipBridgeService(_config(tmp_path), store=store, start_background_tasks=False)

    class FakeController:
        configured = False

    class FakeRtp:
        pass

    async def run() -> None:
        async with OpenAIRealtimeSession(
            config=_config(tmp_path),
            projection={"prompt": "Call Jonathan."},
            realtime={"model": "gpt-realtime-2"},
        ) as realtime:
            await asyncio.wait_for(
                service._realtime_to_rtp(  # type: ignore[arg-type]
                    record.voice_session_id,
                    FakeRtp(),  # type: ignore[arg-type]
                    realtime,
                    FakeController(),  # type: ignore[arg-type]
                    stop_event,
                ),
                timeout=1,
            )

    with pytest.raises(RuntimeError, match="internal failure"):
        asyncio.run(run())

    assert captured["url"] == "wss://api.openai.com/v1/realtime?model=gpt-realtime-2"
    assert len(captured["session_updates"]) == 1
    assert "realtime.session_config_fallback" not in {
        event.event_type for event in store.get("voice-1").debug_events
    }


def test_realtime_g711_bridge_rejects_dynamic_rtp_media_format(tmp_path: Path) -> None:
    try:
        DidwwSipBridgeConfig(
            runtime_dir=tmp_path,
            media_format="slin24",
            rtp_payload_type=118,
            sample_rate_hz=24000,
        )
    except ValueError as exc:
        assert "static G.711" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("dynamic RTP media formats should be rejected")


def test_realtime_lifecycle_audio_turn_and_transcript_debug_events(tmp_path: Path) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    stop_event = asyncio.Event()
    audio_delta = base64.b64encode(b"\x01" * 160).decode("ascii")

    class FakeRealtime:
        def __init__(self) -> None:
            self.responses: list[str | None] = []
            self.events = [
                {"type": "input_audio_buffer.speech_started", "audio_start_ms": 120},
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "item_id": "caller-item-1",
                    "transcript": "My phone is +15551234567 and I need status.",
                },
                {"type": "response.created", "response": {"id": "resp-1", "status": "in_progress"}},
                {
                    "type": "response.output_audio.delta",
                    "response_id": "resp-1",
                    "item_id": "assistant-item-1",
                    "delta": audio_delta,
                },
                {
                    "type": "response.output_audio.done",
                    "response_id": "resp-1",
                    "item_id": "assistant-item-1",
                },
                {
                    "type": "response.output_audio_transcript.done",
                    "response_id": "resp-1",
                    "transcript": "I can help with that.",
                },
                {"type": "response.done", "response": {"id": "resp-1", "status": "completed"}},
            ]

        async def recv_json(self) -> dict[str, Any]:
            if self.events:
                return self.events.pop(0)
            stop_event.set()
            await asyncio.sleep(0)
            return {}

        async def request_response(self, instructions: str | None = None) -> None:
            self.responses.append(instructions)

    class FakeRtp:
        def __init__(self) -> None:
            self.sent: list[bytes] = []

        async def send_payload(self, payload: bytes) -> None:
            self.sent.append(payload)

    class FakeController:
        configured = False

    async def run() -> FakeRtp:
        service = DidwwSipBridgeService(
            _config(tmp_path),
            store=store,
            start_background_tasks=False,
        )
        rtp = FakeRtp()
        await asyncio.wait_for(
            service._realtime_to_rtp(  # type: ignore[arg-type]
                record.voice_session_id,
                rtp,  # type: ignore[arg-type]
                FakeRealtime(),  # type: ignore[arg-type]
                FakeController(),  # type: ignore[arg-type]
                stop_event,
            ),
            timeout=1,
        )
        return rtp

    rtp = asyncio.run(run())

    assert rtp.sent == [b"\x01" * 160]
    latest = store.get("voice-1")
    assert latest is not None
    assert [event.speaker for event in latest.transcript_events] == ["caller", "assistant"]
    event_types = [event.event_type for event in latest.debug_events]
    assert "realtime.vad" in event_types
    assert "caller_transcript.completed" in event_types
    assert "assistant_transcript.completed" in event_types
    assert "response.created" in event_types
    assert "response.done" in event_types
    assert "response.audio_delta.started" in event_types
    assert "response.audio_delta.finished" in event_types
    debug_json = json.dumps(
        [event.model_dump(mode="json") for event in latest.debug_events],
        sort_keys=True,
    )
    assert "+15551234567" not in debug_json
    assert "<redacted-phone>" in debug_json


def test_inbound_listener_ignores_outbound_and_external_media_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ASTERISK_ARI_PASSWORD", "ari-secret")
    service = DidwwSipBridgeService(
        _config(tmp_path).model_copy(
            update={
                "enable_inbound_phone_calls": True,
                "inbound_gateway_url": "http://gateway.local",
            }
        ),
        store=DidwwBridgeStore(tmp_path / "bridge.json"),
        start_background_tasks=False,
    )
    handled: list[dict[str, Any]] = []

    class FakeWebSocket:
        def __init__(self) -> None:
            self.events = [
                {
                    "type": "StasisStart",
                    "channel": {"id": "call-didww-test"},
                    "args": ["didww-test"],
                },
                {
                    "type": "StasisStart",
                    "channel": {"id": "media-didww-test"},
                    "args": ["didww-test"],
                },
                {
                    "type": "StasisStart",
                    "channel": {"id": "SIP/abc-0001"},
                    "args": ["inbound:+1 (555) 000-0100"],
                    "channelvars": {
                        "INBOUND_PHONE_CALL": "1",
                        "CALLER_NUMBER": "+1 (555) 000-0200",
                        "BRIDGE_CALL_ID": "bridge-1",
                    },
                },
            ]

        async def recv(self) -> str:
            if not self.events:
                raise RuntimeError("listener stopped")
            return json.dumps(self.events.pop(0))

    class FakeAriClient:
        @asynccontextmanager
        async def event_stream(self) -> Any:
            yield FakeWebSocket()

    service.ari_client = FakeAriClient()  # type: ignore[assignment]

    async def fake_handle(context: dict[str, Any], gateway_client: Any) -> None:
        handled.append(context)

    async def run() -> None:
        monkeypatch.setattr(service, "_handle_inbound_stasis_start", fake_handle)
        await service._run_inbound_phone_listener()

    asyncio.run(run())

    assert len(handled) == 1
    assert handled[0]["asterisk_channel_id"] == "SIP/abc-0001"
    assert handled[0]["did"] == "+15550000100"
    assert handled[0]["caller_number"] == "+15550000200"
    assert handled[0]["bridge_call_id"] == "bridge-1"


def test_inbound_phone_call_provisioning_and_bridge_use_route_specific_greeting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ASTERISK_ARI_PASSWORD", "ari-secret")
    monkeypatch.setenv("VOICE_MEDIA_CONTROLLER_TOKEN", "controller-token")
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    service = DidwwSipBridgeService(
        _config(tmp_path).model_copy(
            update={
                "enable_inbound_phone_calls": True,
                "inbound_gateway_url": "http://gateway.local",
            }
        ),
        store=store,
        realtime_factory=lambda _projection, _realtime: FakeRealtime(),  # type: ignore[arg-type]
        rtp_endpoint_factory=lambda: FakeRtpEndpoint(),  # type: ignore[arg-type]
        start_background_tasks=False,
    )
    events: list[str] = []
    synced_results: list[dict[str, Any]] = []

    async def fake_sync_voice_session(
        self: VoiceMediaControllerClient,
        voice_session_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        synced_results.append(
            {
                "base_url": self.base_url,
                "voice_session_id": voice_session_id,
                "payload": payload,
            }
        )
        return {
            "voice_session_id": voice_session_id,
            "status": payload.get("status"),
            "reconciliation_status": "not_requested",
        }

    monkeypatch.setattr(
        VoiceMediaControllerClient,
        "sync_voice_session",
        fake_sync_voice_session,
    )

    class FakeWebSocket:
        def __init__(self) -> None:
            self.events = [
                {
                    "type": "StasisStart",
                    "channel": {"id": "media-inbound-1"},
                }
            ]

        async def recv(self) -> str:
            return json.dumps(self.events.pop(0))

    class FakeRealtime:
        def __init__(self) -> None:
            self.responses: list[str | None] = []

        async def __aenter__(self) -> "FakeRealtime":
            events.append("realtime_open")
            return self

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            events.append("realtime_close")

        async def request_response(self, instructions: str | None = None) -> None:
            self.responses.append(instructions)
            events.append("initial_response")

    class FakeRtpEndpoint:
        port: int | None = None
        remote_addr: tuple[str, int] | None = None

        async def start(self) -> None:
            events.append("rtp_start")
            self.port = 49000

        def close(self) -> None:
            events.append("rtp_close")

    class FakeAriClient:
        @asynccontextmanager
        async def event_stream(self) -> Any:
            events.append("event_stream_open")
            try:
                yield FakeWebSocket()
            finally:
                events.append("event_stream_close")

        async def start_external_media_for_inbound(
            self,
            *,
            call_id: str,
            inbound_channel_id: str,
            external_host: str,
            event_websocket: Any | None = None,
        ) -> AsteriskCallHandles:
            events.append("start_external_media_for_inbound")
            assert call_id == "inbound-bridge-1"
            assert inbound_channel_id == "SIP/abc-0001"
            assert external_host == "127.0.0.1:49000"
            assert event_websocket is not None
            assert json.loads(await event_websocket.recv())["channel"]["id"] == "media-inbound-1"
            return AsteriskCallHandles(
                call_channel_id="SIP/abc-0001",
                bridge_id="bridge-inbound-1",
                external_media_channel_id="media-inbound-1",
                external_media_local_address="127.0.0.1",
                external_media_local_port=27086,
            )

        async def hangup_channel(self, channel_id: str | None) -> None:
            events.append(f"hangup:{channel_id}")

        async def destroy_bridge(self, bridge_id: str | None) -> None:
            events.append(f"destroy:{bridge_id}")

    service.ari_client = FakeAriClient()  # type: ignore[assignment]

    class FakeGatewayClient:
        def __init__(self) -> None:
            self.requests: list[dict[str, Any]] = []

        async def create_inbound_phone_call(self, payload: dict[str, Any]) -> dict[str, Any]:
            self.requests.append(payload)
            return {
                "voice_session_id": "voice-inbound-1",
                "request_id": "inbound-phone:ops-main:SIP/abc-0001",
                "projection": {
                    "prompt": "Answer inbound calls for ops.",
                    "caller_metadata": {"direction": "inbound"},
                },
                "realtime_session_config": {
                    "model": "gpt-realtime-2",
                    "media_controller_url": "http://gateway.local",
                    "media_controller_token_env": "VOICE_MEDIA_CONTROLLER_TOKEN",
                },
                "route": {
                    "route_id": "ops-main",
                    "did": {"redacted": "***0100", "sha256": "a" * 64},
                    "caller": {"redacted": "***0200", "sha256": "b" * 64},
                    "asterisk_channel_id": "SIP/abc-0001",
                    "bridge_call_id": "bridge-1",
                    "sip_headers": {"X-Bridge-Token": "<redacted>"},
                },
                "inbound_instructions": {
                    "route_prompt": "Answer inbound calls for ops.",
                    "startup_text": "Thanks for calling ops.",
                },
            }

    async def fake_bridge_media_until_done(*args: Any, **kwargs: Any) -> None:
        events.append("bridge_media")

    async def run() -> tuple[list[str], DidwwBridgeCallRecord, list[str | None], dict[str, Any]]:
        gateway_client = FakeGatewayClient()
        monkeypatch.setattr(service, "_bridge_media_until_done", fake_bridge_media_until_done)
        monkeypatch.setattr(service, "_cleanup_asterisk", lambda _record: asyncio.sleep(0))
        await service._handle_inbound_stasis_start(
            {
                "asterisk_channel_id": "SIP/abc-0001",
                "did": "+15550000100",
                "route_id": "ops-main",
                "caller_number": "+15550000200",
                "bridge_call_id": "bridge-1",
                "sip_headers": {"X-Bridge-Token": "secret"},
            },
            gateway_client,  # type: ignore[arg-type]
        )
        assert service._background_tasks
        await asyncio.wait_for(
            asyncio.gather(*list(service._background_tasks)),
            timeout=1,
        )
        record = store.get("voice-inbound-1")
        assert record is not None
        return events, record, gateway_client.requests, service.store.get("voice-inbound-1").response_payload()

    events, record, gateway_requests, response_payload = asyncio.run(run())

    assert gateway_requests[0]["did"] == "+15550000100"
    assert gateway_requests[0]["caller_number"] == "+15550000200"
    assert gateway_requests[0]["route_id"] == "ops-main"
    assert record.direction == "inbound"
    assert record.route_id == "ops-main"
    assert record.did_redacted == "***0100"
    assert record.caller_redacted == "***0200"
    assert record.ari_channel_id == "SIP/abc-0001"
    assert record.ari_external_media_channel_id == "media-inbound-1"
    assert synced_results[0]["base_url"] == "http://gateway.local"
    assert synced_results[0]["voice_session_id"] == "voice-inbound-1"
    synced_payload = synced_results[0]["payload"]
    assert synced_payload["voice_session_id"] == "voice-inbound-1"
    assert synced_payload["direction"] == "inbound"
    assert synced_payload["metadata"]["ari_channel_id"] == "SIP/abc-0001"
    assert response_payload["direction"] == "inbound"
    assert response_payload["metadata"]["to_number"] == "***0100"
    assert response_payload["metadata"]["from_number"] == "***0200"
    assert events == [
        "rtp_start",
        "realtime_open",
        "event_stream_open",
        "start_external_media_for_inbound",
        "initial_response",
        "bridge_media",
        "event_stream_close",
        "realtime_close",
        "rtp_close",
    ]
    assert record.response_payload()["metadata"]["to_number"] == "***0100"
    assert "thanks for calling ops." in initial_inbound_call_instructions(
        {"route_prompt": "Answer inbound calls for ops.", "startup_text": "Thanks for calling ops."},
        {"prompt": "Answer inbound calls for ops.", "caller_metadata": {"direction": "inbound"}},
    ).lower()


def test_inbound_phone_call_provisioning_failure_hangs_up_inbound_channel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ASTERISK_ARI_PASSWORD", "ari-secret")
    service = DidwwSipBridgeService(
        _config(tmp_path).model_copy(
            update={
                "enable_inbound_phone_calls": True,
                "inbound_gateway_url": "http://gateway.local",
            }
        ),
        store=DidwwBridgeStore(tmp_path / "bridge.json"),
        start_background_tasks=False,
    )
    hung_up: list[str | None] = []

    class FailingGatewayClient:
        async def create_inbound_phone_call(self, payload: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("No inbound phone routes are configured")

    async def hangup_channel(channel_id: str | None) -> None:
        hung_up.append(channel_id)

    service.ari_client.hangup_channel = hangup_channel  # type: ignore[method-assign]

    async def run() -> None:
        await service._handle_inbound_stasis_start(
            {
                "asterisk_channel_id": "SIP/abc-0001",
                "did": "+15550000100",
                "caller_number": "+15550000200",
                "bridge_call_id": "bridge-1",
                "route_id": "missing-route",
            },
            FailingGatewayClient(),  # type: ignore[arg-type]
        )

    asyncio.run(run())

    assert hung_up == ["SIP/abc-0001"]
    assert service.store.get("voice-inbound-1") is None


def test_realtime_idle_prompt_waits_for_rtp_playback_before_countdown(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(didww_sip_bridge, "_BRIDGE_IDLE_PROMPT_DELAY_SECONDS", 0.01)
    monkeypatch.setattr(didww_sip_bridge, "_ASSISTANT_PLAYBACK_DRAIN_SECONDS", 0.01)
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    stop_event = asyncio.Event()
    audio_delta = base64.b64encode(b"\x02" * 160).decode("ascii")
    order: list[str] = []

    class FakeRealtime:
        def __init__(self) -> None:
            self.responses: list[str | None] = []
            self.events = [
                {"type": "response.created", "response": {"id": "resp-1"}},
                {
                    "type": "response.output_audio.delta",
                    "response_id": "resp-1",
                    "delta": audio_delta,
                },
                {"type": "response.output_audio.done", "response_id": "resp-1"},
                {"type": "response.done", "response": {"id": "resp-1"}},
            ]

        async def recv_json(self) -> dict[str, Any]:
            if self.events:
                return self.events.pop(0)
            await asyncio.sleep(0.005)
            return {}

        async def request_response(self, instructions: str | None = None) -> None:
            self.responses.append(instructions)
            order.append("idle_response")
            stop_event.set()

        async def cancel_response(self, response_id: str | None = None) -> None:
            return None

    class FakeRtp:
        async def send_payload(self, payload: bytes) -> None:
            order.append("send_start")
            await asyncio.sleep(0.03)
            order.append("send_done")

    class FakeController:
        configured = False

    async def run() -> FakeRealtime:
        service = DidwwSipBridgeService(
            _config(tmp_path),
            store=store,
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        await asyncio.wait_for(
            service._realtime_to_rtp(  # type: ignore[arg-type]
                record.voice_session_id,
                FakeRtp(),  # type: ignore[arg-type]
                realtime,  # type: ignore[arg-type]
                FakeController(),  # type: ignore[arg-type]
                stop_event,
            ),
            timeout=1,
        )
        return realtime

    realtime = asyncio.run(run())

    assert realtime.responses == [didww_sip_bridge._IDLE_PROMPT_INSTRUCTIONS]
    assert order.index("send_done") < order.index("idle_response")


def test_realtime_idle_prompt_is_cancelled_when_caller_speaks_during_countdown(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(didww_sip_bridge, "_BRIDGE_IDLE_PROMPT_DELAY_SECONDS", 0.05)
    monkeypatch.setattr(didww_sip_bridge, "_ASSISTANT_PLAYBACK_DRAIN_SECONDS", 0.005)
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    stop_event = asyncio.Event()
    audio_delta = base64.b64encode(b"\x03" * 160).decode("ascii")

    class FakeRealtime:
        def __init__(self) -> None:
            self.responses: list[str | None] = []
            self.speech_sent = False
            self.events = [
                {"type": "response.created", "response": {"id": "resp-1"}},
                {
                    "type": "response.output_audio.delta",
                    "response_id": "resp-1",
                    "delta": audio_delta,
                },
                {"type": "response.output_audio.done", "response_id": "resp-1"},
                {"type": "response.done", "response": {"id": "resp-1"}},
            ]

        async def recv_json(self) -> dict[str, Any]:
            if self.events:
                return self.events.pop(0)
            if not self.speech_sent:
                self.speech_sent = True
                await asyncio.sleep(0.015)
                return {"type": "input_audio_buffer.speech_started", "audio_start_ms": 500}
            await asyncio.sleep(0.06)
            stop_event.set()
            return {}

        async def request_response(self, instructions: str | None = None) -> None:
            self.responses.append(instructions)
            stop_event.set()

        async def cancel_response(self, response_id: str | None = None) -> None:
            return None

    class FakeRtp:
        async def send_payload(self, payload: bytes) -> None:
            return None

    class FakeController:
        configured = False

    async def run() -> FakeRealtime:
        service = DidwwSipBridgeService(
            _config(tmp_path),
            store=store,
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        await asyncio.wait_for(
            service._realtime_to_rtp(  # type: ignore[arg-type]
                record.voice_session_id,
                FakeRtp(),  # type: ignore[arg-type]
                realtime,  # type: ignore[arg-type]
                FakeController(),  # type: ignore[arg-type]
                stop_event,
            ),
            timeout=1,
        )
        return realtime

    realtime = asyncio.run(run())

    assert realtime.responses == []
    latest = store.get("voice-1")
    assert latest is not None
    assert "bridge.idle_prompt.cancelled" in [
        event.event_type for event in latest.debug_events
    ]


def test_realtime_tool_response_cancels_pending_idle_prompt(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(didww_sip_bridge, "_BRIDGE_IDLE_PROMPT_DELAY_SECONDS", 0.05)
    monkeypatch.setattr(didww_sip_bridge, "_ASSISTANT_PLAYBACK_DRAIN_SECONDS", 0.005)
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    stop_event = asyncio.Event()
    audio_delta = base64.b64encode(b"\x04" * 160).decode("ascii")

    class FakeRealtime:
        def __init__(self) -> None:
            self.responses: list[str | None] = []
            self.tool_outputs: list[dict[str, object]] = []
            self.injected: list[dict[str, Any]] = []
            self.tool_sent = False
            self.events = [
                {"type": "response.created", "response": {"id": "resp-1"}},
                {
                    "type": "response.output_audio.delta",
                    "response_id": "resp-1",
                    "delta": audio_delta,
                },
                {"type": "response.output_audio.done", "response_id": "resp-1"},
                {"type": "response.done", "response": {"id": "resp-1"}},
            ]

        async def recv_json(self) -> dict[str, Any]:
            if self.events:
                return self.events.pop(0)
            if not self.tool_sent:
                self.tool_sent = True
                await asyncio.sleep(0.015)
                return {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "call_id": "tool-call-1",
                        "name": "query_origin_agent",
                        "arguments": json.dumps({"question": "What changed?"}),
                    },
                }
            await asyncio.sleep(0.02)
            return {}

        async def send_tool_output(self, *, call_id: str, output: dict[str, Any]) -> None:
            self.tool_outputs.append({"call_id": call_id, "output": output})

        async def request_response(self, instructions: str | None = None) -> None:
            self.responses.append(instructions)
            stop_event.set()

        async def inject_origin_query_result(
            self,
            query: dict[str, Any],
            *,
            request_response: bool = True,
        ) -> None:
            self.injected.append(query)

        async def cancel_response(self, response_id: str | None = None) -> None:
            return None

    class FakeController:
        configured = True

        async def create_query(self, voice_session_id: str, **kwargs: Any) -> dict[str, Any]:
            return {
                "query_id": "voice-query-1",
                "status": "answered",
                "answer": "The deployment is ready.",
            }

        async def get_query(self, voice_session_id: str, query_id: str) -> dict[str, Any]:
            await asyncio.sleep(0.5)
            return {"query_id": query_id, "status": "pending"}

    class FakeRtp:
        async def send_payload(self, payload: bytes) -> None:
            return None

    async def run() -> FakeRealtime:
        service = DidwwSipBridgeService(
            _config(tmp_path),
            store=store,
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        await asyncio.wait_for(
            service._realtime_to_rtp(  # type: ignore[arg-type]
                record.voice_session_id,
                FakeRtp(),  # type: ignore[arg-type]
                realtime,  # type: ignore[arg-type]
                FakeController(),  # type: ignore[arg-type]
                stop_event,
            ),
            timeout=1,
        )
        return realtime

    realtime = asyncio.run(run())

    assert realtime.responses == [
        didww_sip_bridge._ORIGIN_QUERY_ANSWER_RESPONSE_INSTRUCTIONS
    ]
    answer_text = didww_sip_bridge._ORIGIN_QUERY_ANSWER_RESPONSE_INSTRUCTIONS.lower()
    assert "earlier question" in answer_text
    assert (
        "quick callback to your earlier question" in answer_text
        or "heard back on the earlier question" in answer_text
    )
    assert "origin agent" not in answer_text
    assert "originating agent" not in answer_text
    assert "internal handoff" not in answer_text
    assert didww_sip_bridge._IDLE_PROMPT_INSTRUCTIONS not in realtime.responses


def test_realtime_pending_origin_query_uses_waiting_idle_prompt(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(didww_sip_bridge, "_ORIGIN_QUERY_ACK_GRACE_SECONDS", 0.005)
    monkeypatch.setattr(didww_sip_bridge, "_BRIDGE_IDLE_PROMPT_DELAY_SECONDS", 0.01)
    monkeypatch.setattr(didww_sip_bridge, "_ASSISTANT_PLAYBACK_DRAIN_SECONDS", 0.005)
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    stop_event = asyncio.Event()

    class FakeRealtime:
        def __init__(self) -> None:
            self.responses: list[str | None] = []
            self.events: list[dict[str, Any]] = [
                {"type": "response.created", "response": {"id": "resp-1"}},
                {
                    "type": "response.output_audio.delta",
                    "response_id": "resp-1",
                    "delta": base64.b64encode(b"\x01" * 160).decode("ascii"),
                },
                {"type": "response.output_audio.done", "response_id": "resp-1"},
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "call_id": "tool-call-1",
                        "name": "query_origin_agent",
                        "arguments": json.dumps({"question": "What changed?"}),
                    },
                },
                {"type": "response.done", "response": {"id": "resp-1"}},
            ]

        async def recv_json(self) -> dict[str, Any]:
            if self.events:
                return self.events.pop(0)
            await asyncio.sleep(0.001)
            return {}

        async def request_response(self, instructions: str | None = None) -> None:
            self.responses.append(instructions)
            if instructions == didww_sip_bridge._ORIGIN_QUERY_HOLD_RESPONSE_INSTRUCTIONS:
                self.events.extend(
                    [
                        {"type": "response.created", "response": {"id": "hold-1"}},
                        {
                            "type": "response.output_audio.delta",
                            "response_id": "hold-1",
                            "delta": base64.b64encode(b"\x02" * 160).decode("ascii"),
                        },
                        {
                            "type": "response.output_audio.done",
                            "response_id": "hold-1",
                        },
                        {"type": "response.done", "response": {"id": "hold-1"}},
                    ]
                )
            elif instructions == didww_sip_bridge._ORIGIN_QUERY_IDLE_PROMPT_INSTRUCTIONS:
                stop_event.set()

        async def send_tool_output(self, *, call_id: str, output: dict[str, Any]) -> None:
            return None

        async def cancel_response(self, response_id: str | None = None) -> None:
            return None

        async def inject_origin_query_result(
            self,
            query: dict[str, Any],
            *,
            request_response: bool = True,
        ) -> None:
            return None

    class FakeController:
        configured = True

        async def create_query(self, voice_session_id: str, **kwargs: Any) -> dict[str, Any]:
            return {
                "query_id": "voice-query-1",
                "status": "pending",
            }

        async def get_query(self, voice_session_id: str, query_id: str) -> dict[str, Any]:
            await asyncio.sleep(0.01)
            return {"query_id": query_id, "status": "pending"}

    class FakeRtp:
        async def send_payload(self, payload: bytes) -> None:
            return None

    async def run() -> FakeRealtime:
        service = DidwwSipBridgeService(
            _config(tmp_path),
            store=store,
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        try:
            await asyncio.wait_for(
                service._realtime_to_rtp(  # type: ignore[arg-type]
                    record.voice_session_id,
                    FakeRtp(),  # type: ignore[arg-type]
                    realtime,  # type: ignore[arg-type]
                    FakeController(),  # type: ignore[arg-type]
                    stop_event,
                ),
                timeout=1,
            )
        except TimeoutError:
            pass
        return realtime

    realtime = asyncio.run(run())

    prompt_responses = [response for response in realtime.responses if response is not None]
    assert prompt_responses == [
        didww_sip_bridge._ORIGIN_QUERY_HOLD_RESPONSE_INSTRUCTIONS,
        didww_sip_bridge._ORIGIN_QUERY_IDLE_PROMPT_INSTRUCTIONS,
    ]


def test_realtime_idle_prompt_does_not_repeat_without_new_caller_speech(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(didww_sip_bridge, "_BRIDGE_IDLE_PROMPT_DELAY_SECONDS", 0.01)
    monkeypatch.setattr(didww_sip_bridge, "_ASSISTANT_PLAYBACK_DRAIN_SECONDS", 0.005)
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    stop_event = asyncio.Event()
    initial_audio = base64.b64encode(b"\x05" * 160).decode("ascii")
    idle_audio = base64.b64encode(b"\x06" * 160).decode("ascii")

    class FakeRealtime:
        def __init__(self) -> None:
            self.responses: list[str | None] = []
            self.stop_task: asyncio.Task[None] | None = None
            self.events = [
                {"type": "response.created", "response": {"id": "resp-1"}},
                {
                    "type": "response.output_audio.delta",
                    "response_id": "resp-1",
                    "delta": initial_audio,
                },
                {"type": "response.output_audio.done", "response_id": "resp-1"},
                {"type": "response.done", "response": {"id": "resp-1"}},
            ]

        async def recv_json(self) -> dict[str, Any]:
            if self.events:
                return self.events.pop(0)
            await asyncio.sleep(0.005)
            return {}

        async def request_response(self, instructions: str | None = None) -> None:
            self.responses.append(instructions)
            if instructions == didww_sip_bridge._IDLE_PROMPT_INSTRUCTIONS:
                self.events.extend(
                    [
                        {"type": "response.created", "response": {"id": "idle-1"}},
                        {
                            "type": "response.output_audio.delta",
                            "response_id": "idle-1",
                            "delta": idle_audio,
                        },
                        {"type": "response.output_audio.done", "response_id": "idle-1"},
                        {"type": "response.done", "response": {"id": "idle-1"}},
                    ]
                )
                if self.stop_task is None:
                    self.stop_task = asyncio.create_task(self.stop_after_repeat_window())

        async def stop_after_repeat_window(self) -> None:
            await asyncio.sleep(0.06)
            stop_event.set()

        async def cancel_response(self, response_id: str | None = None) -> None:
            return None

    class FakeRtp:
        async def send_payload(self, payload: bytes) -> None:
            return None

    class FakeController:
        configured = False

    async def run() -> FakeRealtime:
        service = DidwwSipBridgeService(
            _config(tmp_path),
            store=store,
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        await asyncio.wait_for(
            service._realtime_to_rtp(  # type: ignore[arg-type]
                record.voice_session_id,
                FakeRtp(),  # type: ignore[arg-type]
                realtime,  # type: ignore[arg-type]
                FakeController(),  # type: ignore[arg-type]
                stop_event,
            ),
            timeout=1,
        )
        return realtime

    realtime = asyncio.run(run())

    assert realtime.responses == [didww_sip_bridge._IDLE_PROMPT_INSTRUCTIONS]


def test_realtime_idle_prompt_is_bounded_across_speech_resets(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(didww_sip_bridge, "_BRIDGE_IDLE_PROMPT_DELAY_SECONDS", 0.01)
    monkeypatch.setattr(didww_sip_bridge, "_ASSISTANT_PLAYBACK_DRAIN_SECONDS", 0.005)
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    stop_event = asyncio.Event()

    class FakeRealtime:
        def __init__(self) -> None:
            self.responses: list[str | None] = []
            self.events: list[dict[str, Any]] = [
                {"type": "response.created", "response": {"id": "seed-1"}},
                {
                    "type": "response.output_audio.delta",
                    "response_id": "seed-1",
                    "delta": base64.b64encode(b"\x03" * 160).decode("ascii"),
                },
                {"type": "response.output_audio.done", "response_id": "seed-1"},
                {"type": "response.done", "response": {"id": "seed-1"}},
            ]

        async def recv_json(self) -> dict[str, Any]:
            if self.events:
                return self.events.pop(0)
            await asyncio.sleep(0.001)
            return {}

        async def request_response(self, instructions: str | None = None) -> None:
            self.responses.append(instructions)
            if instructions != didww_sip_bridge._IDLE_PROMPT_INSTRUCTIONS:
                return
            cycle_index = sum(
                1
                for prompt in self.responses
                if prompt == didww_sip_bridge._IDLE_PROMPT_INSTRUCTIONS
            )
            self.events.extend(
                [
                    {"type": "response.created", "response": {"id": f"idle-{cycle_index}"}},
                    {
                        "type": "response.output_audio.delta",
                        "response_id": f"idle-{cycle_index}",
                        "delta": base64.b64encode(b"\x04" * 160).decode("ascii"),
                    },
                    {
                        "type": "response.output_audio.done",
                        "response_id": f"idle-{cycle_index}",
                    },
                    {"type": "response.done", "response": {"id": f"idle-{cycle_index}"}},
                    {"type": "input_audio_buffer.speech_started", "audio_start_ms": 100},
                    {"type": "input_audio_buffer.speech_stopped", "audio_end_ms": 180},
                    {
                        "type": "conversation.item.input_audio_transcription.completed",
                        "transcript": f"reset {cycle_index}",
                    },
                    {
                        "type": "response.done",
                        "response": {"id": f"cycle-{cycle_index}"},
                    },
                ]
            )
            if cycle_index == 2:
                stop_event.set()

        async def cancel_response(self, response_id: str | None = None) -> None:
            return None

    class FakeRtp:
        async def send_payload(self, payload: bytes) -> None:
            return None

    class FakeController:
        configured = False

    async def run() -> FakeRealtime:
        service = DidwwSipBridgeService(
            _config(tmp_path),
            store=store,
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        await asyncio.wait_for(
            service._realtime_to_rtp(  # type: ignore[arg-type]
                record.voice_session_id,
                FakeRtp(),  # type: ignore[arg-type]
                realtime,  # type: ignore[arg-type]
                FakeController(),  # type: ignore[arg-type]
                stop_event,
            ),
            timeout=1,
        )
        return realtime

    realtime = asyncio.run(run())

    assert realtime.responses.count(didww_sip_bridge._IDLE_PROMPT_INSTRUCTIONS) == 2


def test_realtime_end_call_waits_for_rtp_drain_before_hangup(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(didww_sip_bridge, "_ASSISTANT_PLAYBACK_DRAIN_SECONDS", 0.01)
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    store.attach_asterisk(
        record.voice_session_id,
        ari_channel_id="call-didww-test",
        ari_bridge_id="bridge-didww-test",
        ari_external_media_channel_id="media-didww-test",
        rtp_port=49000,
    )
    stop_event = asyncio.Event()
    audio_delta = base64.b64encode(b"\x07" * 160).decode("ascii")
    order: list[str] = []

    class FakeRealtime:
        def __init__(self) -> None:
            self.events = [
                {"type": "response.created", "response": {"id": "resp-1"}},
                {
                    "type": "response.output_audio.delta",
                    "response_id": "resp-1",
                    "item_id": "assistant-item-1",
                    "delta": audio_delta,
                },
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "call_id": "tool-call-1",
                        "name": "end_call",
                        "arguments": json.dumps({"reason": "caller said goodbye"}),
                    },
                },
                {"type": "response.output_audio.done", "response_id": "resp-1"},
                {"type": "response.done", "response": {"id": "resp-1"}},
            ]
            self.responses: list[str | None] = []
            self.tool_outputs: list[dict[str, Any]] = []

        async def recv_json(self) -> dict[str, Any]:
            if self.events:
                return self.events.pop(0)
            await asyncio.sleep(0.005)
            return {}

        async def send_tool_output(self, *, call_id: str, output: dict[str, Any]) -> None:
            self.tool_outputs.append({"call_id": call_id, "output": output})
            order.append(f"tool_output:{call_id}")

        async def request_response(self, instructions: str | None = None) -> None:
            self.responses.append(instructions)

    class FakeRtp:
        async def send_payload(
            self,
            payload: bytes,
            *,
            on_samples_sent: Callable[[int], None] | None = None,
        ) -> int:
            order.append("send_start")
            if on_samples_sent is not None:
                on_samples_sent(160)
            await asyncio.sleep(0.03)
            order.append("send_done")
            return len(payload)

    class FakeAriClient:
        async def hangup_channel(self, channel_id: str | None) -> None:
            order.append(f"hangup:{channel_id}")

        async def destroy_bridge(self, bridge_id: str | None) -> None:
            order.append(f"destroy:{bridge_id}")

    class FakeController:
        configured = False

    async def run() -> FakeRealtime:
        service = DidwwSipBridgeService(
            _config(tmp_path),
            store=store,
            ari_client=FakeAriClient(),  # type: ignore[arg-type]
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        await asyncio.wait_for(
            service._realtime_to_rtp(  # type: ignore[arg-type]
                record.voice_session_id,
                FakeRtp(),  # type: ignore[arg-type]
                realtime,  # type: ignore[arg-type]
                FakeController(),  # type: ignore[arg-type]
                stop_event,
            ),
            timeout=1,
        )
        return realtime

    realtime = asyncio.run(run())

    assert realtime.tool_outputs == [
        {"call_id": "tool-call-1", "output": {"status": "ended"}}
    ]
    assert order.index("send_done") < order.index("hangup:media-didww-test")
    assert order.index("hangup:media-didww-test") < order.index("tool_output:tool-call-1")
    latest = store.get(record.voice_session_id)
    assert latest is not None
    assert latest.status == "ended"
    assert latest.summary == "Call ended by voice agent."
    event_types = [event.event_type for event in latest.debug_events]
    assert "bridge.end_call.requested" in event_types
    assert "bridge.end_call.executing" in event_types
    assert "bridge.end_call.completed" in event_types


def test_realtime_end_call_waits_for_caller_turn_completion_before_hangup(
    tmp_path: Path,
) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    store.attach_asterisk(
        record.voice_session_id,
        ari_channel_id="call-didww-test",
        ari_bridge_id="bridge-didww-test",
        ari_external_media_channel_id="media-didww-test",
        rtp_port=49000,
    )
    stop_event = asyncio.Event()
    order: list[str] = []

    class FakeRealtime:
        def __init__(self) -> None:
            self.events = [
                {"type": "response.created", "response": {"id": "resp-1"}},
                {"type": "input_audio_buffer.speech_started", "audio_start_ms": 100},
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "call_id": "tool-call-1",
                        "name": "end_call",
                        "arguments": json.dumps({"reason": "caller is done"}),
                    },
                },
                {"type": "__sleep__", "seconds": 0.02},
                {"type": "input_audio_buffer.speech_stopped", "audio_end_ms": 240},
                {"type": "__sleep__", "seconds": 0.02},
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "Bye for now.",
                },
                {"type": "__sleep__", "seconds": 0.02},
                {"type": "response.done", "response": {"id": "resp-1"}},
            ]
            self.responses: list[str | None] = []
            self.tool_outputs: list[dict[str, Any]] = []

        async def recv_json(self) -> dict[str, Any]:
            while self.events:
                event = self.events.pop(0)
                if event["type"] == "__sleep__":
                    await asyncio.sleep(float(event["seconds"]))
                    continue
                order.append(str(event["type"]))
                return event
            await asyncio.sleep(0.005)
            return {}

        async def send_tool_output(self, *, call_id: str, output: dict[str, Any]) -> None:
            self.tool_outputs.append({"call_id": call_id, "output": output})
            order.append(f"tool_output:{call_id}")

        async def request_response(self, instructions: str | None = None) -> None:
            self.responses.append(instructions)
            self.events.append({"type": "response.done", "response": {"id": "resp-2"}})

    class FakeRtp:
        async def send_payload(self, payload: bytes) -> None:
            del payload
            return None

    class FakeAriClient:
        async def hangup_channel(self, channel_id: str | None) -> None:
            order.append(f"hangup:{channel_id}")

        async def destroy_bridge(self, bridge_id: str | None) -> None:
            order.append(f"destroy:{bridge_id}")

    class FakeController:
        configured = False

    async def run() -> FakeRealtime:
        service = DidwwSipBridgeService(
            _config(tmp_path),
            store=store,
            ari_client=FakeAriClient(),  # type: ignore[arg-type]
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        await asyncio.wait_for(
            service._realtime_to_rtp(  # type: ignore[arg-type]
                record.voice_session_id,
                FakeRtp(),  # type: ignore[arg-type]
                realtime,  # type: ignore[arg-type]
                FakeController(),  # type: ignore[arg-type]
                stop_event,
            ),
            timeout=1,
        )
        return realtime

    realtime = asyncio.run(run())

    assert realtime.tool_outputs == [
        {"call_id": "tool-call-1", "output": {"status": "ended"}}
    ]
    assert order.index("conversation.item.input_audio_transcription.completed") < order.index(
        "hangup:media-didww-test"
    )
    assert order.index("response.done") < order.index("hangup:media-didww-test")
    latest = store.get(record.voice_session_id)
    assert latest is not None
    assert latest.status == "ended"
    assert latest.summary == "Call ended by voice agent."


def test_realtime_end_call_waits_for_response_done_before_hangup(
    tmp_path: Path,
) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    store.attach_asterisk(
        record.voice_session_id,
        ari_channel_id="call-didww-test",
        ari_bridge_id="bridge-didww-test",
        ari_external_media_channel_id="media-didww-test",
        rtp_port=49000,
    )
    stop_event = asyncio.Event()
    order: list[str] = []

    class FakeRealtime:
        def __init__(self) -> None:
            self.events = [
                {"type": "response.created", "response": {"id": "resp-1"}},
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "call_id": "tool-call-1",
                        "name": "end_call",
                        "arguments": json.dumps({"reason": "no further request"}),
                    },
                },
                {"type": "__sleep__", "seconds": 0.02},
                {"type": "response.done", "response": {"id": "resp-1"}},
            ]
            self.tool_outputs: list[dict[str, Any]] = []

        async def recv_json(self) -> dict[str, Any]:
            while self.events:
                event = self.events.pop(0)
                if event["type"] == "__sleep__":
                    await asyncio.sleep(float(event["seconds"]))
                    continue
                order.append(str(event["type"]))
                return event
            await asyncio.sleep(0.005)
            return {}

        async def send_tool_output(self, *, call_id: str, output: dict[str, Any]) -> None:
            self.tool_outputs.append({"call_id": call_id, "output": output})
            order.append(f"tool_output:{call_id}")

        async def request_response(self, instructions: str | None = None) -> None:
            del instructions
            return None

    class FakeRtp:
        async def send_payload(self, payload: bytes) -> None:
            del payload
            return None

    class FakeAriClient:
        async def hangup_channel(self, channel_id: str | None) -> None:
            order.append(f"hangup:{channel_id}")

        async def destroy_bridge(self, bridge_id: str | None) -> None:
            order.append(f"destroy:{bridge_id}")

    class FakeController:
        configured = False

    async def run() -> FakeRealtime:
        service = DidwwSipBridgeService(
            _config(tmp_path),
            store=store,
            ari_client=FakeAriClient(),  # type: ignore[arg-type]
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        await asyncio.wait_for(
            service._realtime_to_rtp(  # type: ignore[arg-type]
                record.voice_session_id,
                FakeRtp(),  # type: ignore[arg-type]
                realtime,  # type: ignore[arg-type]
                FakeController(),  # type: ignore[arg-type]
                stop_event,
            ),
            timeout=1,
        )
        return realtime

    realtime = asyncio.run(run())

    assert realtime.tool_outputs == [
        {"call_id": "tool-call-1", "output": {"status": "ended"}}
    ]
    assert order.index("response.done") < order.index("hangup:media-didww-test")
    assert order.index("hangup:media-didww-test") < order.index("tool_output:tool-call-1")
    latest = store.get(record.voice_session_id)
    assert latest is not None
    assert latest.status == "ended"
    assert latest.summary == "Call ended by voice agent."
    event_types = [event.event_type for event in latest.debug_events]
    assert "bridge.end_call.requested" in event_types
    assert "bridge.end_call.executing" in event_types
    assert "bridge.end_call.completed" in event_types


def test_realtime_end_call_duplicates_do_not_duplicate_cleanup(
    tmp_path: Path,
) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    store.attach_asterisk(
        record.voice_session_id,
        ari_channel_id="call-didww-test",
        ari_bridge_id="bridge-didww-test",
        ari_external_media_channel_id="media-didww-test",
        rtp_port=49000,
    )
    stop_event = asyncio.Event()
    order: list[str] = []
    audio_delta = base64.b64encode(b"\x08" * 160).decode("ascii")

    class FakeRealtime:
        def __init__(self) -> None:
            self.events = [
                {"type": "response.created", "response": {"id": "resp-1"}},
                {
                    "type": "response.output_audio.delta",
                    "response_id": "resp-1",
                    "item_id": "assistant-item-1",
                    "delta": audio_delta,
                },
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "call_id": "tool-call-1",
                        "name": "end_call",
                        "arguments": json.dumps({"reason": "caller said goodbye"}),
                    },
                },
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "call_id": "tool-call-2",
                        "name": "end_call",
                        "arguments": json.dumps({"reason": "still goodbye"}),
                    },
                },
                {"type": "response.output_audio.done", "response_id": "resp-1"},
                {"type": "response.done", "response": {"id": "resp-1"}},
            ]
            self.tool_outputs: list[dict[str, Any]] = []

        async def recv_json(self) -> dict[str, Any]:
            if self.events:
                event = self.events.pop(0)
                order.append(str(event["type"]))
                return event
            await asyncio.sleep(0.005)
            return {}

        async def send_tool_output(self, *, call_id: str, output: dict[str, Any]) -> None:
            self.tool_outputs.append({"call_id": call_id, "output": output})
            order.append(f"tool_output:{call_id}")

        async def request_response(self, instructions: str | None = None) -> None:
            raise AssertionError("end_call should not request a follow-up response")

    class FakeRtp:
        async def send_payload(
            self,
            payload: bytes,
            *,
            on_samples_sent: Callable[[int], None] | None = None,
        ) -> int:
            order.append("send_start")
            if on_samples_sent is not None:
                on_samples_sent(160)
            await asyncio.sleep(0.03)
            order.append("send_done")
            return len(payload)

    class FakeAriClient:
        async def hangup_channel(self, channel_id: str | None) -> None:
            order.append(f"hangup:{channel_id}")

        async def destroy_bridge(self, bridge_id: str | None) -> None:
            order.append(f"destroy:{bridge_id}")

    class FakeController:
        configured = False

    async def run() -> FakeRealtime:
        service = DidwwSipBridgeService(
            _config(tmp_path),
            store=store,
            ari_client=FakeAriClient(),  # type: ignore[arg-type]
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        await asyncio.wait_for(
            service._realtime_to_rtp(  # type: ignore[arg-type]
                record.voice_session_id,
                FakeRtp(),  # type: ignore[arg-type]
                realtime,  # type: ignore[arg-type]
                FakeController(),  # type: ignore[arg-type]
                stop_event,
            ),
            timeout=1,
        )
        return realtime

    realtime = asyncio.run(run())

    assert realtime.tool_outputs == [
        {"call_id": "tool-call-1", "output": {"status": "ended"}}
    ]
    assert order.count("hangup:media-didww-test") == 1
    assert order.count("hangup:call-didww-test") == 1
    assert order.count("destroy:bridge-didww-test") == 1
    latest = store.get(record.voice_session_id)
    assert latest is not None
    assert latest.status == "ended"


def test_goodbye_transcript_does_not_treat_inbound_greetings_as_closings() -> None:
    assert not didww_sip_bridge._is_goodbye_transcript(
        "Thanks for calling, how can I help?"
    )
    assert not didww_sip_bridge._is_goodbye_transcript(
        "Thank you for calling Truman support. How may I direct your call?"
    )
    assert not didww_sip_bridge._is_goodbye_transcript("Thanks for calling ops.")

    assert didww_sip_bridge._is_goodbye_transcript("Thank you for calling.")
    assert didww_sip_bridge._is_goodbye_transcript("Thanks for calling. Goodbye.")


def test_realtime_goodbye_idle_hangup_waits_for_silence_before_cleanup(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(didww_sip_bridge, "_ASSISTANT_PLAYBACK_DRAIN_SECONDS", 0.01)
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    store.attach_asterisk(
        record.voice_session_id,
        ari_channel_id="call-didww-test",
        ari_bridge_id="bridge-didww-test",
        ari_external_media_channel_id="media-didww-test",
        rtp_port=49000,
    )
    stop_event = asyncio.Event()
    order: list[str] = []
    audio_delta = base64.b64encode(b"\x09" * 160).decode("ascii")

    class FakeRealtime:
        def __init__(self) -> None:
            self.events = [
                {"type": "response.created", "response": {"id": "resp-1"}},
                {
                    "type": "response.output_audio.delta",
                    "response_id": "resp-1",
                    "item_id": "assistant-item-1",
                    "delta": audio_delta,
                },
                {
                    "type": "response.output_audio_transcript.done",
                    "response_id": "resp-1",
                    "item_id": "assistant-item-1",
                    "transcript": "Thanks for calling. Goodbye.",
                },
                {"type": "response.output_audio.done", "response_id": "resp-1"},
                {"type": "response.done", "response": {"id": "resp-1"}},
            ]
            self.tool_outputs: list[dict[str, Any]] = []

        async def recv_json(self) -> dict[str, Any]:
            if self.events:
                event = self.events.pop(0)
                order.append(str(event["type"]))
                return event
            await asyncio.sleep(0.005)
            return {}

        async def send_tool_output(self, *, call_id: str, output: dict[str, Any]) -> None:
            self.tool_outputs.append({"call_id": call_id, "output": output})
            order.append(f"tool_output:{call_id}")

        async def request_response(self, instructions: str | None = None) -> None:
            raise AssertionError("idle prompt should not be requested after goodbye")

    class FakeRtp:
        async def send_payload(
            self,
            payload: bytes,
            *,
            on_samples_sent: Callable[[int], None] | None = None,
        ) -> int:
            order.append("send_start")
            if on_samples_sent is not None:
                on_samples_sent(160)
            await asyncio.sleep(0.03)
            order.append("send_done")
            return len(payload)

    class FakeAriClient:
        async def hangup_channel(self, channel_id: str | None) -> None:
            order.append(f"hangup:{channel_id}")

        async def destroy_bridge(self, bridge_id: str | None) -> None:
            order.append(f"destroy:{bridge_id}")

    class FakeController:
        configured = False

    async def run() -> FakeRealtime:
        service = DidwwSipBridgeService(
            _config(tmp_path).model_copy(update={"goodbye_idle_hangup_seconds": 0.01}),
            store=store,
            ari_client=FakeAriClient(),  # type: ignore[arg-type]
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        await asyncio.wait_for(
            service._realtime_to_rtp(  # type: ignore[arg-type]
                record.voice_session_id,
                FakeRtp(),  # type: ignore[arg-type]
                realtime,  # type: ignore[arg-type]
                FakeController(),  # type: ignore[arg-type]
                stop_event,
            ),
            timeout=2,
        )
        return realtime

    realtime = asyncio.run(run())

    assert realtime.tool_outputs == []
    assert order.index("send_done") < order.index("hangup:media-didww-test")
    assert order.index("hangup:media-didww-test") < order.index("hangup:call-didww-test")
    assert order.index("hangup:call-didww-test") < order.index("destroy:bridge-didww-test")
    latest = store.get(record.voice_session_id)
    assert latest is not None
    assert latest.status == "ended"
    assert latest.summary == "Call ended by voice agent."
    event_types = [event.event_type for event in latest.debug_events]
    assert "bridge.goodbye_idle.scheduled" in event_types
    assert "bridge.goodbye_idle.executing" in event_types
    assert "bridge.goodbye_idle.completed" in event_types


def test_realtime_goodbye_idle_hangup_is_cancelled_by_caller_speech(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(didww_sip_bridge, "_ASSISTANT_PLAYBACK_DRAIN_SECONDS", 0.01)
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    store.attach_asterisk(
        record.voice_session_id,
        ari_channel_id="call-didww-test",
        ari_bridge_id="bridge-didww-test",
        ari_external_media_channel_id="media-didww-test",
        rtp_port=49000,
    )
    stop_event = asyncio.Event()
    order: list[str] = []
    audio_delta = base64.b64encode(b"\x0a" * 160).decode("ascii")

    class FakeRealtime:
        def __init__(self) -> None:
            self.events = [
                {"type": "response.created", "response": {"id": "resp-1"}},
                {
                    "type": "response.output_audio.delta",
                    "response_id": "resp-1",
                    "item_id": "assistant-item-1",
                    "delta": audio_delta,
                },
                {
                    "type": "response.output_audio_transcript.done",
                    "response_id": "resp-1",
                    "item_id": "assistant-item-1",
                    "transcript": "Thanks for calling. Goodbye.",
                },
                {"type": "response.output_audio.done", "response_id": "resp-1"},
                {"type": "response.done", "response": {"id": "resp-1"}},
                {"type": "__sleep__", "seconds": 0.01},
                {"type": "input_audio_buffer.speech_started", "audio_start_ms": 2500},
                {"type": "__sleep__", "seconds": 0.02},
                {"type": "input_audio_buffer.speech_stopped", "audio_end_ms": 2900},
            ]
            self.tool_outputs: list[dict[str, Any]] = []

        async def recv_json(self) -> dict[str, Any]:
            while self.events:
                event = self.events.pop(0)
                if event["type"] == "__sleep__":
                    await asyncio.sleep(float(event["seconds"]))
                    continue
                order.append(str(event["type"]))
                return event
            stop_event.set()
            await asyncio.sleep(0)
            return {}

        async def send_tool_output(self, *, call_id: str, output: dict[str, Any]) -> None:
            self.tool_outputs.append({"call_id": call_id, "output": output})
            order.append(f"tool_output:{call_id}")

        async def request_response(self, instructions: str | None = None) -> None:
            del instructions
            return None

    class FakeRtp:
        async def send_payload(
            self,
            payload: bytes,
            *,
            on_samples_sent: Callable[[int], None] | None = None,
        ) -> int:
            if on_samples_sent is not None:
                on_samples_sent(160)
            await asyncio.sleep(0.03)
            return len(payload)

    class FakeAriClient:
        async def hangup_channel(self, channel_id: str | None) -> None:
            order.append(f"hangup:{channel_id}")

        async def destroy_bridge(self, bridge_id: str | None) -> None:
            order.append(f"destroy:{bridge_id}")

    class FakeController:
        configured = False

    async def run() -> FakeRealtime:
        service = DidwwSipBridgeService(
            _config(tmp_path).model_copy(update={"goodbye_idle_hangup_seconds": 0.05}),
            store=store,
            ari_client=FakeAriClient(),  # type: ignore[arg-type]
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        await asyncio.wait_for(
            service._realtime_to_rtp(  # type: ignore[arg-type]
                record.voice_session_id,
                FakeRtp(),  # type: ignore[arg-type]
                realtime,  # type: ignore[arg-type]
                FakeController(),  # type: ignore[arg-type]
                stop_event,
            ),
            timeout=2,
        )
        return realtime

    realtime = asyncio.run(run())

    assert realtime.tool_outputs == []
    assert not any(item.startswith("hangup:") for item in order)
    assert not any(item.startswith("destroy:") for item in order)
    latest = store.get(record.voice_session_id)
    assert latest is not None
    assert latest.status != "ended"
    event_types = [event.event_type for event in latest.debug_events]
    assert "bridge.goodbye_idle.scheduled" in event_types
    assert "bridge.goodbye_idle.cancelled" in event_types


def test_short_backchannel_during_assistant_audio_does_not_cancel_or_request_response(
    tmp_path: Path,
) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    stop_event = asyncio.Event()
    audio_delta = base64.b64encode(b"\x11" * 160).decode("ascii")

    class FakeRealtime:
        def __init__(self) -> None:
            self.events = [
                {"type": "response.created", "response": {"id": "resp-1"}},
                {
                    "type": "response.output_audio.delta",
                    "response_id": "resp-1",
                    "item_id": "assistant-item-1",
                    "delta": audio_delta,
                },
                {"type": "input_audio_buffer.speech_started", "audio_start_ms": 100},
                {"type": "input_audio_buffer.speech_stopped", "audio_end_ms": 240},
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "Uh-huh.",
                },
                {"type": "response.done", "response": {"id": "resp-1"}},
            ]
            self.cancels: list[str | None] = []
            self.responses: list[str | None] = []
            self.truncates: list[dict[str, Any]] = []

        async def recv_json(self) -> dict[str, Any]:
            if self.events:
                return self.events.pop(0)
            stop_event.set()
            await asyncio.sleep(0)
            return {}

        async def cancel_response(self, response_id: str | None = None) -> None:
            self.cancels.append(response_id)

        async def truncate_assistant_audio(
            self,
            *,
            item_id: str,
            audio_end_ms: int,
            content_index: int = 0,
        ) -> None:
            self.truncates.append(
                {
                    "item_id": item_id,
                    "audio_end_ms": audio_end_ms,
                    "content_index": content_index,
                }
            )

        async def request_response(self, instructions: str | None = None) -> None:
            self.responses.append(instructions)

    class FakeRtp:
        def __init__(self) -> None:
            self.sent: list[bytes] = []

        async def send_payload(self, payload: bytes) -> None:
            self.sent.append(payload)

    class FakeController:
        configured = False

    async def run() -> tuple[FakeRealtime, FakeRtp]:
        service = DidwwSipBridgeService(
            _config(tmp_path).model_copy(update={"barge_in_delay_ms": 50}),
            store=store,
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        rtp = FakeRtp()
        await asyncio.wait_for(
            service._realtime_to_rtp(  # type: ignore[arg-type]
                record.voice_session_id,
                rtp,  # type: ignore[arg-type]
                realtime,  # type: ignore[arg-type]
                FakeController(),  # type: ignore[arg-type]
                stop_event,
            ),
            timeout=1,
        )
        return realtime, rtp

    realtime, rtp = asyncio.run(run())

    assert rtp.sent == [b"\x11" * 160]
    assert realtime.cancels == []
    assert realtime.truncates == []
    assert realtime.responses == []
    latest = store.get("voice-1")
    assert latest is not None
    assert latest.transcript_events[-1].speaker == "caller"
    assert latest.transcript_events[-1].text == "Uh-huh."
    event_types = [event.event_type for event in latest.debug_events]
    assert "barge_in_delay_started" in event_types
    assert "barge_in_transcript_classified" in event_types
    assert "backchannel_ignored" in event_types


def test_standalone_backchannel_does_not_request_response(tmp_path: Path) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    stop_event = asyncio.Event()

    class FakeRealtime:
        def __init__(self) -> None:
            self.events = [
                {"type": "response.done", "response": {"id": "resp-1", "status": "completed"}},
                {"type": "input_audio_buffer.speech_started", "audio_start_ms": 100},
                {"type": "input_audio_buffer.speech_stopped", "audio_end_ms": 360},
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "Okay, great.",
                },
            ]
            self.responses: list[str | None] = []

        async def recv_json(self) -> dict[str, Any]:
            if self.events:
                return self.events.pop(0)
            stop_event.set()
            await asyncio.sleep(0)
            return {}

        async def cancel_response(self, response_id: str | None = None) -> None:
            raise AssertionError("standalone backchannels should not cancel responses")

        async def truncate_assistant_audio(
            self,
            *,
            item_id: str,
            audio_end_ms: int,
            content_index: int = 0,
        ) -> None:
            raise AssertionError("standalone backchannels should not truncate audio")

        async def request_response(self, instructions: str | None = None) -> None:
            self.responses.append(instructions)

    class FakeRtp:
        async def send_payload(self, payload: bytes) -> None:
            return None

    class FakeController:
        configured = False

    async def run() -> FakeRealtime:
        service = DidwwSipBridgeService(
            _config(tmp_path),
            store=store,
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        await asyncio.wait_for(
            service._realtime_to_rtp(  # type: ignore[arg-type]
                record.voice_session_id,
                FakeRtp(),
                realtime,  # type: ignore[arg-type]
                FakeController(),  # type: ignore[arg-type]
                stop_event,
            ),
            timeout=1,
        )
        return realtime

    realtime = asyncio.run(run())

    assert realtime.responses == []
    latest = store.get("voice-1")
    assert latest is not None
    backchannel_events = [
        event for event in latest.debug_events if event.event_type == "backchannel_ignored"
    ]
    assert len(backchannel_events) == 1
    assert backchannel_events[0].metadata["reason"] == "standalone_backchannel"


def test_substantive_speech_during_pre_audio_response_cancels_generation(
    tmp_path: Path,
) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    stop_event = asyncio.Event()

    class FakeRealtime:
        def __init__(self) -> None:
            self.events: list[dict[str, Any]] = [
                {"type": "response.created", "response": {"id": "resp-1"}},
                {"type": "input_audio_buffer.speech_started", "audio_start_ms": 100},
                {"type": "__sleep__", "seconds": 0.05},
                {"type": "input_audio_buffer.speech_stopped", "audio_end_ms": 900},
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "Stop, I need to ask about the outage.",
                },
                {"type": "response.cancelled", "response": {"id": "resp-1", "status": "cancelled"}},
            ]
            self.cancels: list[str | None] = []
            self.responses: list[str | None] = []
            self.truncates: list[dict[str, Any]] = []

        async def recv_json(self) -> dict[str, Any]:
            while self.events:
                event = self.events.pop(0)
                if event["type"] == "__sleep__":
                    await asyncio.sleep(float(event["seconds"]))
                    continue
                return event
            stop_event.set()
            await asyncio.sleep(0)
            return {}

        async def cancel_response(self, response_id: str | None = None) -> None:
            self.cancels.append(response_id)

        async def truncate_assistant_audio(
            self,
            *,
            item_id: str,
            audio_end_ms: int,
            content_index: int = 0,
        ) -> None:
            self.truncates.append(
                {
                    "item_id": item_id,
                    "audio_end_ms": audio_end_ms,
                    "content_index": content_index,
                }
            )

        async def request_response(self, instructions: str | None = None) -> None:
            self.responses.append(instructions)
            stop_event.set()

    class FakeRtp:
        async def send_payload(self, payload: bytes) -> None:
            raise AssertionError("pre-audio cancellation should not play assistant audio")

    class FakeController:
        configured = False

    async def run() -> FakeRealtime:
        service = DidwwSipBridgeService(
            _config(tmp_path).model_copy(update={"barge_in_delay_ms": 10}),
            store=store,
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        await asyncio.wait_for(
            service._realtime_to_rtp(  # type: ignore[arg-type]
                record.voice_session_id,
                FakeRtp(),
                realtime,  # type: ignore[arg-type]
                FakeController(),  # type: ignore[arg-type]
                stop_event,
            ),
            timeout=1,
        )
        return realtime

    realtime = asyncio.run(run())

    assert realtime.cancels == ["resp-1"]
    assert realtime.truncates == []
    assert realtime.responses == [None]
    latest = store.get("voice-1")
    assert latest is not None
    delay_events = [
        event for event in latest.debug_events if event.event_type == "barge_in_delay_started"
    ]
    assert len(delay_events) == 1
    assert delay_events[0].metadata["assistant_audio_active"] is False
    assert delay_events[0].metadata["response_active"] is True
    cancel_events = [
        event for event in latest.debug_events if event.event_type == "barge_in_cancel_sent"
    ]
    assert len(cancel_events) == 1
    assert cancel_events[0].metadata["cancel_response_id"] == "resp-1"


def test_substantive_speech_during_assistant_audio_sends_one_cancel(
    tmp_path: Path,
) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    stop_event = asyncio.Event()
    audio_delta = base64.b64encode(b"\x22" * 160).decode("ascii")

    class FakeRealtime:
        def __init__(self) -> None:
            self.events: list[dict[str, Any]] = [
                {"type": "response.created", "response": {"id": "resp-1"}},
                {"type": "response.output_audio.delta", "response_id": "resp-1", "delta": audio_delta},
                {"type": "input_audio_buffer.speech_started", "audio_start_ms": 100},
                {"type": "__sleep__", "seconds": 0.05},
                {"type": "input_audio_buffer.speech_stopped", "audio_end_ms": 1200},
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "Stop, I need to ask about the outage.",
                },
                {"type": "response.cancelled", "response": {"id": "resp-1", "status": "cancelled"}},
            ]
            self.cancels: list[str | None] = []
            self.responses: list[str | None] = []
            self.truncates: list[dict[str, Any]] = []

        async def recv_json(self) -> dict[str, Any]:
            while self.events:
                event = self.events.pop(0)
                if event["type"] == "__sleep__":
                    await asyncio.sleep(float(event["seconds"]))
                    continue
                return event
            stop_event.set()
            await asyncio.sleep(0)
            return {}

        async def cancel_response(self, response_id: str | None = None) -> None:
            self.cancels.append(response_id)

        async def truncate_assistant_audio(
            self,
            *,
            item_id: str,
            audio_end_ms: int,
            content_index: int = 0,
        ) -> None:
            self.truncates.append(
                {
                    "item_id": item_id,
                    "audio_end_ms": audio_end_ms,
                    "content_index": content_index,
                }
            )

        async def request_response(self, instructions: str | None = None) -> None:
            self.responses.append(instructions)
            stop_event.set()

    class FakeRtp:
        async def send_payload(self, payload: bytes) -> None:
            return None

    class FakeController:
        configured = False

    async def run() -> FakeRealtime:
        service = DidwwSipBridgeService(
            _config(tmp_path).model_copy(update={"barge_in_delay_ms": 10}),
            store=store,
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        await asyncio.wait_for(
            service._realtime_to_rtp(  # type: ignore[arg-type]
                record.voice_session_id,
                FakeRtp(),
                realtime,  # type: ignore[arg-type]
                FakeController(),  # type: ignore[arg-type]
                stop_event,
            ),
            timeout=1,
        )
        return realtime

    realtime = asyncio.run(run())

    assert realtime.cancels == ["resp-1"]
    assert realtime.truncates == []
    assert realtime.responses == [None]
    latest = store.get("voice-1")
    assert latest is not None
    cancel_events = [
        event for event in latest.debug_events if event.event_type == "barge_in_cancel_sent"
    ]
    assert len(cancel_events) == 1
    assert cancel_events[0].metadata["reason"] == "speech_delay_elapsed"


def test_substantive_speech_truncates_assistant_audio_to_played_rtp_position(
    tmp_path: Path,
) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    stop_event = asyncio.Event()
    audio_delta = base64.b64encode(b"\x24" * 160).decode("ascii")

    class FakeRealtime:
        def __init__(self) -> None:
            self.events: list[dict[str, Any]] = [
                {"type": "response.created", "response": {"id": "resp-1"}},
                {
                    "type": "response.output_audio.delta",
                    "response_id": "resp-1",
                    "item_id": "assistant-item-1",
                    "content_index": 0,
                    "delta": audio_delta,
                },
                {"type": "input_audio_buffer.speech_started", "audio_start_ms": 100},
                {"type": "__sleep__", "seconds": 0.05},
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "Stop, I need to ask about the outage.",
                },
                {"type": "response.cancelled", "response": {"id": "resp-1", "status": "cancelled"}},
            ]
            self.cancels: list[str | None] = []
            self.responses: list[str | None] = []
            self.truncates: list[dict[str, Any]] = []
            self.actions: list[str] = []

        async def recv_json(self) -> dict[str, Any]:
            while self.events:
                event = self.events.pop(0)
                if event["type"] == "__sleep__":
                    await asyncio.sleep(float(event["seconds"]))
                    continue
                return event
            stop_event.set()
            await asyncio.sleep(0)
            return {}

        async def cancel_response(self, response_id: str | None = None) -> None:
            self.actions.append("cancel")
            self.cancels.append(response_id)

        async def truncate_assistant_audio(
            self,
            *,
            item_id: str,
            audio_end_ms: int,
            content_index: int = 0,
        ) -> None:
            self.actions.append("truncate")
            self.truncates.append(
                {
                    "item_id": item_id,
                    "audio_end_ms": audio_end_ms,
                    "content_index": content_index,
                }
            )

        async def request_response(self, instructions: str | None = None) -> None:
            self.actions.append("response")
            self.responses.append(instructions)
            stop_event.set()

    class FakeRtp:
        async def send_payload(self, payload: bytes) -> None:
            return None

    class FakeController:
        configured = False

    async def run() -> FakeRealtime:
        service = DidwwSipBridgeService(
            _config(tmp_path).model_copy(update={"barge_in_delay_ms": 10}),
            store=store,
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        await asyncio.wait_for(
            service._realtime_to_rtp(  # type: ignore[arg-type]
                record.voice_session_id,
                FakeRtp(),
                realtime,  # type: ignore[arg-type]
                FakeController(),  # type: ignore[arg-type]
                stop_event,
            ),
            timeout=1,
        )
        return realtime

    realtime = asyncio.run(run())

    assert realtime.cancels == ["resp-1"]
    assert realtime.truncates == [
        {"item_id": "assistant-item-1", "audio_end_ms": 20, "content_index": 0}
    ]
    assert realtime.responses == [None]
    assert realtime.actions == ["cancel", "truncate", "response"]
    latest = store.get("voice-1")
    assert latest is not None
    truncate_events = [
        event for event in latest.debug_events if event.event_type == "assistant_audio_truncated"
    ]
    assert len(truncate_events) == 1
    assert truncate_events[0].metadata["item_id"] == "assistant-item-1"
    assert truncate_events[0].metadata["audio_end_ms"] == 20


def test_substantive_speech_truncates_only_confirmed_rtp_samples_when_send_cancelled(
    tmp_path: Path,
) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    stop_event = asyncio.Event()
    audio_delta = base64.b64encode(b"\x25" * 320).decode("ascii")

    class FakeRealtime:
        def __init__(self) -> None:
            self.events: list[dict[str, Any]] = [
                {"type": "response.created", "response": {"id": "resp-1"}},
                {
                    "type": "response.output_audio.delta",
                    "response_id": "resp-1",
                    "item_id": "assistant-item-1",
                    "content_index": 0,
                    "delta": audio_delta,
                },
                {"type": "input_audio_buffer.speech_started", "audio_start_ms": 100},
                {"type": "__sleep__", "seconds": 0.05},
                {"type": "response.cancelled", "response": {"id": "resp-1", "status": "cancelled"}},
            ]
            self.cancels: list[str | None] = []
            self.truncates: list[dict[str, Any]] = []

        async def recv_json(self) -> dict[str, Any]:
            while self.events:
                event = self.events.pop(0)
                if event["type"] == "__sleep__":
                    await asyncio.sleep(float(event["seconds"]))
                    continue
                return event
            stop_event.set()
            await asyncio.sleep(0)
            return {}

        async def cancel_response(self, response_id: str | None = None) -> None:
            self.cancels.append(response_id)

        async def truncate_assistant_audio(
            self,
            *,
            item_id: str,
            audio_end_ms: int,
            content_index: int = 0,
        ) -> None:
            self.truncates.append(
                {
                    "item_id": item_id,
                    "audio_end_ms": audio_end_ms,
                    "content_index": content_index,
                }
            )

        async def request_response(self, instructions: str | None = None) -> None:
            raise AssertionError("no bridge response should be requested")

    class FakeRtp:
        async def send_payload(
            self,
            payload: bytes,
            *,
            on_samples_sent: Callable[[int], None] | None = None,
        ) -> int:
            if on_samples_sent is not None:
                on_samples_sent(160)
            await asyncio.sleep(1)
            return len(payload)

    class FakeController:
        configured = False

    async def run() -> FakeRealtime:
        service = DidwwSipBridgeService(
            _config(tmp_path).model_copy(update={"barge_in_delay_ms": 10}),
            store=store,
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        await asyncio.wait_for(
            service._realtime_to_rtp(  # type: ignore[arg-type]
                record.voice_session_id,
                FakeRtp(),  # type: ignore[arg-type]
                realtime,  # type: ignore[arg-type]
                FakeController(),  # type: ignore[arg-type]
                stop_event,
            ),
            timeout=1,
        )
        return realtime

    realtime = asyncio.run(run())

    assert realtime.cancels == ["resp-1"]
    assert realtime.truncates == [
        {"item_id": "assistant-item-1", "audio_end_ms": 20, "content_index": 0}
    ]


def test_barge_in_after_response_done_skips_cancel_but_truncates_playback(
    tmp_path: Path,
) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    stop_event = asyncio.Event()
    audio_delta = base64.b64encode(b"\x27" * 160).decode("ascii")

    class FakeRealtime:
        def __init__(self) -> None:
            self.events: list[dict[str, Any]] = [
                {"type": "response.created", "response": {"id": "resp-1"}},
                {
                    "type": "response.output_audio.delta",
                    "response_id": "resp-1",
                    "item_id": "assistant-item-1",
                    "content_index": 0,
                    "delta": audio_delta,
                },
                {"type": "response.output_audio.done", "response_id": "resp-1"},
                {"type": "response.done", "response": {"id": "resp-1", "status": "completed"}},
                {"type": "input_audio_buffer.speech_started", "audio_start_ms": 100},
                {"type": "__sleep__", "seconds": 0.05},
            ]
            self.cancels: list[str | None] = []
            self.truncates: list[dict[str, Any]] = []

        async def recv_json(self) -> dict[str, Any]:
            while self.events:
                event = self.events.pop(0)
                if event["type"] == "__sleep__":
                    await asyncio.sleep(float(event["seconds"]))
                    continue
                return event
            stop_event.set()
            await asyncio.sleep(0)
            return {}

        async def cancel_response(self, response_id: str | None = None) -> None:
            self.cancels.append(response_id)

        async def truncate_assistant_audio(
            self,
            *,
            item_id: str,
            audio_end_ms: int,
            content_index: int = 0,
        ) -> None:
            self.truncates.append(
                {
                    "item_id": item_id,
                    "audio_end_ms": audio_end_ms,
                    "content_index": content_index,
                }
            )

        async def request_response(self, instructions: str | None = None) -> None:
            raise AssertionError("no bridge response should be requested")

    class FakeRtp:
        async def send_payload(
            self,
            payload: bytes,
            *,
            on_samples_sent: Callable[[int], None] | None = None,
        ) -> int:
            if on_samples_sent is not None:
                on_samples_sent(len(payload))
            await asyncio.sleep(1)
            return len(payload)

    class FakeController:
        configured = False

    async def run() -> FakeRealtime:
        service = DidwwSipBridgeService(
            _config(tmp_path).model_copy(update={"barge_in_delay_ms": 10}),
            store=store,
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        await asyncio.wait_for(
            service._realtime_to_rtp(  # type: ignore[arg-type]
                record.voice_session_id,
                FakeRtp(),  # type: ignore[arg-type]
                realtime,  # type: ignore[arg-type]
                FakeController(),  # type: ignore[arg-type]
                stop_event,
            ),
            timeout=1,
        )
        return realtime

    realtime = asyncio.run(run())

    assert realtime.cancels == []
    assert realtime.truncates == [
        {"item_id": "assistant-item-1", "audio_end_ms": 20, "content_index": 0}
    ]
    latest = store.get("voice-1")
    assert latest is not None
    event_types = [event.event_type for event in latest.debug_events]
    assert "barge_in_cancel_response_skipped" in event_types


def test_response_cancel_not_active_error_is_non_fatal(
    tmp_path: Path,
) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    stop_event = asyncio.Event()

    class FakeRealtime:
        def __init__(self) -> None:
            self.events: list[dict[str, Any]] = [
                {
                    "type": "error",
                    "error": {
                        "code": "response_cancel_not_active",
                        "type": "invalid_request_error",
                        "message": "Cancellation failed: no active response found",
                    },
                }
            ]

        async def recv_json(self) -> dict[str, Any]:
            if self.events:
                return self.events.pop(0)
            stop_event.set()
            await asyncio.sleep(0)
            return {}

    class FakeRtp:
        async def send_payload(self, payload: bytes) -> None:
            raise AssertionError("audio should not be sent")

    class FakeController:
        configured = False

    async def run() -> None:
        service = DidwwSipBridgeService(
            _config(tmp_path),
            store=store,
            start_background_tasks=False,
        )
        await asyncio.wait_for(
            service._realtime_to_rtp(  # type: ignore[arg-type]
                record.voice_session_id,
                FakeRtp(),  # type: ignore[arg-type]
                FakeRealtime(),  # type: ignore[arg-type]
                FakeController(),  # type: ignore[arg-type]
                stop_event,
            ),
            timeout=1,
        )

    asyncio.run(run())

    latest = store.get("voice-1")
    assert latest is not None
    event_types = [event.event_type for event in latest.debug_events]
    assert "error" in event_types
    assert "response_cancel_not_active_ignored" in event_types


def test_substantive_speech_does_not_truncate_unsent_rtp_audio(
    tmp_path: Path,
) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    stop_event = asyncio.Event()
    audio_delta = base64.b64encode(b"\x26" * 160).decode("ascii")

    class FakeRealtime:
        def __init__(self) -> None:
            self.events: list[dict[str, Any]] = [
                {"type": "response.created", "response": {"id": "resp-1"}},
                {
                    "type": "response.output_audio.delta",
                    "response_id": "resp-1",
                    "item_id": "assistant-item-1",
                    "content_index": 0,
                    "delta": audio_delta,
                },
                {"type": "input_audio_buffer.speech_started", "audio_start_ms": 100},
                {"type": "__sleep__", "seconds": 0.05},
                {"type": "response.cancelled", "response": {"id": "resp-1", "status": "cancelled"}},
            ]
            self.cancels: list[str | None] = []
            self.truncates: list[dict[str, Any]] = []

        async def recv_json(self) -> dict[str, Any]:
            while self.events:
                event = self.events.pop(0)
                if event["type"] == "__sleep__":
                    await asyncio.sleep(float(event["seconds"]))
                    continue
                return event
            stop_event.set()
            await asyncio.sleep(0)
            return {}

        async def cancel_response(self, response_id: str | None = None) -> None:
            self.cancels.append(response_id)

        async def truncate_assistant_audio(
            self,
            *,
            item_id: str,
            audio_end_ms: int,
            content_index: int = 0,
        ) -> None:
            self.truncates.append(
                {
                    "item_id": item_id,
                    "audio_end_ms": audio_end_ms,
                    "content_index": content_index,
                }
            )

        async def request_response(self, instructions: str | None = None) -> None:
            raise AssertionError("no bridge response should be requested")

    class FakeRtp:
        async def send_payload(
            self,
            payload: bytes,
            *,
            on_samples_sent: Callable[[int], None] | None = None,
        ) -> int:
            del payload, on_samples_sent
            return 0

    class FakeController:
        configured = False

    async def run() -> FakeRealtime:
        service = DidwwSipBridgeService(
            _config(tmp_path).model_copy(update={"barge_in_delay_ms": 10}),
            store=store,
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        await asyncio.wait_for(
            service._realtime_to_rtp(  # type: ignore[arg-type]
                record.voice_session_id,
                FakeRtp(),  # type: ignore[arg-type]
                realtime,  # type: ignore[arg-type]
                FakeController(),  # type: ignore[arg-type]
                stop_event,
            ),
            timeout=1,
        )
        return realtime

    realtime = asyncio.run(run())

    assert realtime.cancels == ["resp-1"]
    assert realtime.truncates == []
    latest = store.get("voice-1")
    assert latest is not None
    cancel_events = [
        event for event in latest.debug_events if event.event_type == "barge_in_cancel_sent"
    ]
    assert len(cancel_events) == 1
    assert cancel_events[0].metadata["truncate_item_id"] is None
    assert cancel_events[0].metadata["truncate_audio_end_ms"] == 0


def test_barge_in_cancel_clears_pending_rtp_audio_before_later_response(
    tmp_path: Path,
) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    stop_event = asyncio.Event()
    first = b"\x31" * 160
    pending = b"\x32" * 160
    later = b"\x33" * 160

    class FakeRealtime:
        def __init__(self) -> None:
            self.events: list[dict[str, Any]] = [
                {"type": "response.created", "response": {"id": "resp-1"}},
                {
                    "type": "response.output_audio.delta",
                    "response_id": "resp-1",
                    "item_id": "assistant-item-1",
                    "delta": base64.b64encode(first).decode("ascii"),
                },
                {
                    "type": "response.output_audio.delta",
                    "response_id": "resp-1",
                    "item_id": "assistant-item-1",
                    "delta": base64.b64encode(pending).decode("ascii"),
                },
                {"type": "input_audio_buffer.speech_started", "audio_start_ms": 100},
                {"type": "__sleep__", "seconds": 0.05},
                {"type": "response.cancelled", "response": {"id": "resp-1", "status": "cancelled"}},
                {"type": "response.created", "response": {"id": "resp-2"}},
                {
                    "type": "response.output_audio.delta",
                    "response_id": "resp-2",
                    "item_id": "assistant-item-2",
                    "delta": base64.b64encode(later).decode("ascii"),
                },
            ]
            self.cancels: list[str | None] = []
            self.truncates: list[dict[str, Any]] = []

        async def recv_json(self) -> dict[str, Any]:
            while self.events:
                event = self.events.pop(0)
                if event["type"] == "__sleep__":
                    await asyncio.sleep(float(event["seconds"]))
                    continue
                return event
            await asyncio.sleep(0.01)
            return {}

        async def cancel_response(self, response_id: str | None = None) -> None:
            self.cancels.append(response_id)

        async def truncate_assistant_audio(
            self,
            *,
            item_id: str,
            audio_end_ms: int,
            content_index: int = 0,
        ) -> None:
            self.truncates.append(
                {
                    "item_id": item_id,
                    "audio_end_ms": audio_end_ms,
                    "content_index": content_index,
                }
            )

        async def request_response(self, instructions: str | None = None) -> None:
            raise AssertionError("no bridge response should be requested")

    class FakeRtp:
        def __init__(self) -> None:
            self.sent: list[bytes] = []

        async def send_payload(
            self,
            payload: bytes,
            *,
            on_samples_sent: Callable[[int], None] | None = None,
        ) -> int:
            self.sent.append(payload)
            if on_samples_sent is not None:
                on_samples_sent(len(payload))
            if payload == first:
                await asyncio.sleep(1)
            if payload == later:
                stop_event.set()
            return len(payload)

    class FakeController:
        configured = False

    async def run() -> tuple[FakeRealtime, FakeRtp]:
        service = DidwwSipBridgeService(
            _config(tmp_path).model_copy(update={"barge_in_delay_ms": 10}),
            store=store,
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        rtp = FakeRtp()
        await asyncio.wait_for(
            service._realtime_to_rtp(  # type: ignore[arg-type]
                record.voice_session_id,
                rtp,  # type: ignore[arg-type]
                realtime,  # type: ignore[arg-type]
                FakeController(),  # type: ignore[arg-type]
                stop_event,
            ),
            timeout=1,
        )
        return realtime, rtp

    realtime, rtp = asyncio.run(run())

    assert realtime.cancels == ["resp-1"]
    assert realtime.truncates == [
        {"item_id": "assistant-item-1", "audio_end_ms": 20, "content_index": 0}
    ]
    assert rtp.sent == [first, later]


def test_truncated_assistant_audio_transcript_is_not_recorded_as_spoken(
    tmp_path: Path,
) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    stop_event = asyncio.Event()
    audio_delta = base64.b64encode(b"\x41" * 160).decode("ascii")

    class FakeRealtime:
        def __init__(self) -> None:
            self.events: list[dict[str, Any]] = [
                {"type": "response.created", "response": {"id": "resp-1"}},
                {
                    "type": "response.output_audio.delta",
                    "response_id": "resp-1",
                    "item_id": "assistant-item-1",
                    "delta": audio_delta,
                },
                {"type": "input_audio_buffer.speech_started", "audio_start_ms": 100},
                {"type": "__sleep__", "seconds": 0.05},
                {"type": "response.cancelled", "response": {"id": "resp-1", "status": "cancelled"}},
                {
                    "type": "response.output_audio_transcript.done",
                    "response_id": "resp-1",
                    "item_id": "assistant-item-1",
                    "transcript": "This sentence was generated after the caller interrupted.",
                },
            ]
            self.cancels: list[str | None] = []
            self.truncates: list[dict[str, Any]] = []

        async def recv_json(self) -> dict[str, Any]:
            while self.events:
                event = self.events.pop(0)
                if event["type"] == "__sleep__":
                    await asyncio.sleep(float(event["seconds"]))
                    continue
                return event
            stop_event.set()
            await asyncio.sleep(0)
            return {}

        async def cancel_response(self, response_id: str | None = None) -> None:
            self.cancels.append(response_id)

        async def truncate_assistant_audio(
            self,
            *,
            item_id: str,
            audio_end_ms: int,
            content_index: int = 0,
        ) -> None:
            self.truncates.append(
                {
                    "item_id": item_id,
                    "audio_end_ms": audio_end_ms,
                    "content_index": content_index,
                }
            )

        async def request_response(self, instructions: str | None = None) -> None:
            raise AssertionError("no bridge response should be requested")

    class FakeRtp:
        async def send_payload(self, payload: bytes) -> None:
            return None

    class FakeController:
        configured = False

    async def run() -> FakeRealtime:
        service = DidwwSipBridgeService(
            _config(tmp_path).model_copy(update={"barge_in_delay_ms": 10}),
            store=store,
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        await asyncio.wait_for(
            service._realtime_to_rtp(  # type: ignore[arg-type]
                record.voice_session_id,
                FakeRtp(),
                realtime,  # type: ignore[arg-type]
                FakeController(),  # type: ignore[arg-type]
                stop_event,
            ),
            timeout=1,
        )
        return realtime

    realtime = asyncio.run(run())

    assert realtime.cancels == ["resp-1"]
    assert realtime.truncates == [
        {"item_id": "assistant-item-1", "audio_end_ms": 20, "content_index": 0}
    ]
    latest = store.get("voice-1")
    assert latest is not None
    assert latest.transcript_events == []
    assert "assistant_transcript.truncated_ignored" in [
        event.event_type for event in latest.debug_events
    ]


def test_realtime_caller_transcript_requests_bridge_response_after_greeting_done(
    tmp_path: Path,
) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    stop_event = asyncio.Event()
    order: list[str] = []

    class FakeRealtime:
        def __init__(self) -> None:
            self.events = [
                {"type": "response.done"},
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "What changed?",
                },
            ]
            self.responses: list[str | None] = []

        async def recv_json(self) -> dict[str, Any]:
            if self.events:
                event = self.events.pop(0)
                order.append(str(event["type"]))
                return event
            stop_event.set()
            await asyncio.sleep(0)
            return {}

        async def request_response(self, instructions: str | None = None) -> None:
            self.responses.append(instructions)
            order.append("response.create")
            stop_event.set()

    class FakeRtp:
        async def send_payload(self, payload: bytes) -> None:
            raise AssertionError("audio should not be sent")

    class FakeController:
        configured = False

    async def run() -> FakeRealtime:
        service = DidwwSipBridgeService(
            _config(tmp_path),
            store=store,
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        await asyncio.wait_for(
            service._realtime_to_rtp(  # type: ignore[arg-type]
                record.voice_session_id,
                FakeRtp(),
                realtime,  # type: ignore[arg-type]
                FakeController(),  # type: ignore[arg-type]
                stop_event,
            ),
            timeout=1,
        )
        return realtime

    realtime = asyncio.run(run())

    assert realtime.responses == [None]
    assert order == [
        "response.done",
        "conversation.item.input_audio_transcription.completed",
        "response.create",
    ]
    latest = store.get("voice-1")
    assert latest is not None
    assert latest.transcript_events[-1].speaker == "caller"
    assert latest.transcript_events[-1].text == "What changed?"


def test_realtime_caller_transcript_during_active_response_is_queued_until_done(
    tmp_path: Path,
) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    stop_event = asyncio.Event()
    order: list[str] = []

    class FakeRealtime:
        def __init__(self) -> None:
            self.events = [
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "Can you check the deployment?",
                },
                {"type": "response.done"},
            ]
            self.responses: list[str | None] = []

        async def recv_json(self) -> dict[str, Any]:
            if self.events:
                event = self.events.pop(0)
                order.append(str(event["type"]))
                return event
            stop_event.set()
            await asyncio.sleep(0)
            return {}

        async def request_response(self, instructions: str | None = None) -> None:
            self.responses.append(instructions)
            order.append("response.create")
            stop_event.set()

    class FakeRtp:
        async def send_payload(self, payload: bytes) -> None:
            raise AssertionError("audio should not be sent")

    class FakeController:
        configured = False

    async def run() -> FakeRealtime:
        service = DidwwSipBridgeService(
            _config(tmp_path),
            store=store,
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        await asyncio.wait_for(
            service._realtime_to_rtp(  # type: ignore[arg-type]
                record.voice_session_id,
                FakeRtp(),
                realtime,  # type: ignore[arg-type]
                FakeController(),  # type: ignore[arg-type]
                stop_event,
            ),
            timeout=1,
        )
        return realtime

    realtime = asyncio.run(run())

    assert realtime.responses == [None]
    assert order == [
        "conversation.item.input_audio_transcription.completed",
        "response.done",
        "response.create",
    ]


def test_realtime_caller_transcripts_create_serialized_bridge_responses(
    tmp_path: Path,
) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    stop_event = asyncio.Event()
    order: list[str] = []

    class FakeRealtime:
        def __init__(self) -> None:
            self.events = [
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "First question?",
                },
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "Second question?",
                },
                {"type": "response.done"},
                {"type": "response.done"},
            ]
            self.responses: list[str | None] = []

        async def recv_json(self) -> dict[str, Any]:
            if self.events:
                event = self.events.pop(0)
                order.append(str(event["type"]))
                return event
            stop_event.set()
            await asyncio.sleep(0)
            return {}

        async def request_response(self, instructions: str | None = None) -> None:
            self.responses.append(instructions)
            order.append(f"response.create.{len(self.responses)}")
            if len(self.responses) == 2:
                stop_event.set()

    class FakeRtp:
        async def send_payload(self, payload: bytes) -> None:
            raise AssertionError("audio should not be sent")

    class FakeController:
        configured = False

    async def run() -> FakeRealtime:
        service = DidwwSipBridgeService(
            _config(tmp_path),
            store=store,
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        await asyncio.wait_for(
            service._realtime_to_rtp(  # type: ignore[arg-type]
                record.voice_session_id,
                FakeRtp(),
                realtime,  # type: ignore[arg-type]
                FakeController(),  # type: ignore[arg-type]
                stop_event,
            ),
            timeout=1,
        )
        return realtime

    realtime = asyncio.run(run())

    assert realtime.responses == [None, None]
    assert order == [
        "conversation.item.input_audio_transcription.completed",
        "conversation.item.input_audio_transcription.completed",
        "response.done",
        "response.create.1",
        "response.done",
        "response.create.2",
    ]


def test_realtime_function_call_creates_pending_origin_query_and_injects_answer(
    tmp_path: Path,
) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))

    class FakeRealtime:
        def __init__(self) -> None:
            self.tool_outputs: list[dict[str, object]] = []
            self.responses: list[str | None] = []
            self.injected: list[dict[str, object]] = []

        async def send_tool_output(self, *, call_id: str, output: dict[str, Any]) -> None:
            self.tool_outputs.append({"call_id": call_id, "output": output})

        async def request_response(self, instructions: str | None = None) -> None:
            self.responses.append(instructions)

        async def inject_origin_query_result(
            self,
            query: dict[str, Any],
            *,
            request_response: bool = True,
        ) -> None:
            self.injected.append(query)

    class FakeController:
        configured = True

        async def create_query(self, voice_session_id: str, **kwargs: Any) -> dict[str, Any]:
            assert voice_session_id == "voice-1"
            assert kwargs["question"] == "What changed?"
            return {"query_id": "voice-query-1", "status": "pending"}

        async def get_query(self, voice_session_id: str, query_id: str) -> dict[str, Any]:
            assert voice_session_id == "voice-1"
            assert query_id == "voice-query-1"
            return {
                "query_id": query_id,
                "status": "answered",
                "answer": "The file was checked.",
            }

        async def mark_query_timeout(self, voice_session_id: str, query_id: str) -> dict[str, Any]:
            raise AssertionError("query should not time out")

    async def run() -> FakeRealtime:
        service = DidwwSipBridgeService(
            _config(tmp_path),
            store=store,
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        query_tasks: set[asyncio.Task[None]] = set()
        await service._handle_realtime_function_call(  # type: ignore[attr-defined]
            record.voice_session_id,
            RealtimeFunctionCall(
                call_id="tool-call-1",
                name="query_origin_agent",
                arguments={"question": "What changed?"},
            ),
            realtime,  # type: ignore[arg-type]
            FakeController(),  # type: ignore[arg-type]
            query_tasks,
            asyncio.Event(),
        )
        await asyncio.wait_for(asyncio.gather(*query_tasks), timeout=1)
        return realtime

    realtime = asyncio.run(run())

    assert realtime.tool_outputs == [
        {"call_id": "tool-call-1", "output": {"status": "pending", "query_id": "voice-query-1"}}
    ]
    assert len(realtime.responses) == 1
    assert realtime.responses[0] == didww_sip_bridge._ORIGIN_QUERY_ANSWER_RESPONSE_INSTRUCTIONS
    assert "origin agent" not in str(realtime.responses[0]).lower()
    assert "originating agent" not in str(realtime.responses[0]).lower()
    assert realtime.injected[0]["answer"] == "The file was checked."
    latest = store.get("voice-1")
    assert latest is not None
    assert latest.transcript_events[-1].speaker == "origin_agent"
    event_types = [event.event_type for event in latest.debug_events]
    assert "origin_query.created" in event_types
    assert "origin_query.answered" in event_types
    assert "origin_query.result_injected" in event_types
    assert "origin_query.response_requested" in event_types
    created = next(event for event in latest.debug_events if event.event_type == "origin_query.created")
    assert created.metadata["query_id"] == "voice-query-1"
    assert created.metadata["question_snippet"] == "What changed?"
    requested = next(
        event for event in latest.debug_events if event.event_type == "origin_query.response_requested"
    )
    assert requested.metadata["reason"] == "query_result"
    assert len(requested.metadata["instruction_sha256"]) == 64


def test_realtime_origin_query_answer_waits_for_caller_transcript_completion(
    tmp_path: Path,
) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    stop_event = asyncio.Event()
    order: list[str] = []

    class FakeRealtime:
        def __init__(self) -> None:
            self.events = [
                {"type": "response.done", "response": {"id": "resp-1"}},
                {"type": "input_audio_buffer.speech_started", "audio_start_ms": 120},
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "call_id": "tool-call-1",
                        "name": "query_origin_agent",
                        "arguments": json.dumps({"question": "What changed?"}),
                    },
                },
                {"type": "__sleep__", "seconds": 0.05},
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "What changed?",
                },
                {"type": "response.done", "response": {"id": "resp-2"}},
            ]
            self.responses: list[str | None] = []
            self.injected: list[dict[str, object]] = []

        async def recv_json(self) -> dict[str, Any]:
            while self.events:
                event = self.events.pop(0)
                if event["type"] == "__sleep__":
                    await asyncio.sleep(float(event["seconds"]))
                    continue
                order.append(str(event["type"]))
                return event
            stop_event.set()
            await asyncio.sleep(0)
            return {}

        async def send_tool_output(self, *, call_id: str, output: dict[str, Any]) -> None:
            return None

        async def request_response(self, instructions: str | None = None) -> None:
            self.responses.append(instructions)
            order.append("response.create")

        async def inject_origin_query_result(
            self,
            query: dict[str, Any],
            *,
            request_response: bool = True,
        ) -> None:
            self.injected.append(query)

    class FakeController:
        configured = True

        async def create_query(self, voice_session_id: str, **kwargs: Any) -> dict[str, Any]:
            assert voice_session_id == "voice-1"
            assert kwargs["question"] == "What changed?"
            return {"query_id": "voice-query-1", "status": "pending"}

        async def get_query(self, voice_session_id: str, query_id: str) -> dict[str, Any]:
            assert voice_session_id == "voice-1"
            assert query_id == "voice-query-1"
            return {
                "query_id": query_id,
                "status": "answered",
                "answer": "The file was checked.",
            }

        async def mark_query_timeout(self, voice_session_id: str, query_id: str) -> dict[str, Any]:
            raise AssertionError("query should not time out")

    class FakeRtp:
        async def send_payload(self, payload: bytes) -> None:
            raise AssertionError("audio should not be sent")

    async def run() -> FakeRealtime:
        service = DidwwSipBridgeService(
            _config(tmp_path),
            store=store,
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        await asyncio.wait_for(
            service._realtime_to_rtp(  # type: ignore[arg-type]
                record.voice_session_id,
                FakeRtp(),
                realtime,  # type: ignore[arg-type]
                FakeController(),  # type: ignore[arg-type]
                stop_event,
            ),
            timeout=1,
        )
        return realtime

    realtime = asyncio.run(run())

    assert realtime.injected[0]["answer"] == "The file was checked."
    assert realtime.responses[0] == didww_sip_bridge._CALLER_TURN_COMPLETION_INSTRUCTIONS
    assert len(realtime.responses) == 2
    assert realtime.responses[1] == didww_sip_bridge._ORIGIN_QUERY_ANSWER_RESPONSE_INSTRUCTIONS
    assert order.index("conversation.item.input_audio_transcription.completed") < order.index(
        "response.create"
    )
    latest = store.get("voice-1")
    assert latest is not None
    queued_reasons = [
        event.metadata["reason"]
        for event in latest.debug_events
        if event.event_type == "bridge.response.queued"
    ]
    assert "caller_turn_incomplete" in queued_reasons


def test_realtime_idle_origin_query_answer_requests_response_immediately(
    tmp_path: Path,
) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))

    class FakeRealtime:
        def __init__(self) -> None:
            self.responses: list[str | None] = []
            self.injected: list[dict[str, object]] = []

        async def send_tool_output(self, *, call_id: str, output: dict[str, Any]) -> None:
            return None

        async def request_response(self, instructions: str | None = None) -> None:
            self.responses.append(instructions)

        async def inject_origin_query_result(
            self,
            query: dict[str, Any],
            *,
            request_response: bool = True,
        ) -> None:
            self.injected.append(query)

    class FakeController:
        configured = True

        async def create_query(self, voice_session_id: str, **kwargs: Any) -> dict[str, Any]:
            assert voice_session_id == "voice-1"
            assert kwargs["question"] == "What changed?"
            return {"query_id": "voice-query-1", "status": "pending"}

        async def get_query(self, voice_session_id: str, query_id: str) -> dict[str, Any]:
            assert voice_session_id == "voice-1"
            assert query_id == "voice-query-1"
            return {
                "query_id": query_id,
                "status": "answered",
                "answer": "The file was checked.",
            }

        async def mark_query_timeout(self, voice_session_id: str, query_id: str) -> dict[str, Any]:
            raise AssertionError("query should not time out")

    class FakeRtp:
        async def send_payload(self, payload: bytes) -> None:
            raise AssertionError("audio should not be sent")

    async def run() -> FakeRealtime:
        service = DidwwSipBridgeService(
            _config(tmp_path),
            store=store,
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        query_tasks: set[asyncio.Task[None]] = set()
        await service._handle_realtime_function_call(  # type: ignore[attr-defined]
            record.voice_session_id,
            RealtimeFunctionCall(
                call_id="tool-call-1",
                name="query_origin_agent",
                arguments={"question": "What changed?"},
            ),
            realtime,  # type: ignore[arg-type]
            FakeController(),  # type: ignore[arg-type]
            query_tasks,
            asyncio.Event(),
        )
        await asyncio.wait_for(asyncio.gather(*query_tasks), timeout=1)
        return realtime

    realtime = asyncio.run(run())

    assert len(realtime.responses) == 1
    assert realtime.responses[0] == didww_sip_bridge._ORIGIN_QUERY_ANSWER_RESPONSE_INSTRUCTIONS
    assert realtime.injected[0]["answer"] == "The file was checked."


def test_realtime_queued_origin_results_are_flushed_fifo_after_caller_turn_completion(
    tmp_path: Path,
) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    stop_event = asyncio.Event()
    order: list[str] = []

    class FakeRealtime:
        def __init__(self) -> None:
            self.events = [
                {"type": "response.done", "response": {"id": "resp-1"}},
                {"type": "input_audio_buffer.speech_started", "audio_start_ms": 120},
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "call_id": "tool-call-1",
                        "name": "query_origin_agent",
                        "arguments": json.dumps({"question": "What changed?"}),
                    },
                },
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "call_id": "tool-call-2",
                        "name": "query_origin_agent",
                        "arguments": json.dumps({"question": "What else changed?"}),
                    },
                },
                {"type": "__sleep__", "seconds": 0.05},
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "What changed and what else changed?",
                },
                {"type": "response.done", "response": {"id": "resp-2"}},
                {"type": "response.done", "response": {"id": "resp-3"}},
            ]
            self.responses: list[str | None] = []
            self.injected: list[dict[str, object]] = []

        async def recv_json(self) -> dict[str, Any]:
            while self.events:
                event = self.events.pop(0)
                if event["type"] == "__sleep__":
                    await asyncio.sleep(float(event["seconds"]))
                    continue
                order.append(str(event["type"]))
                return event
            stop_event.set()
            await asyncio.sleep(0)
            return {}

        async def send_tool_output(self, *, call_id: str, output: dict[str, Any]) -> None:
            return None

        async def request_response(self, instructions: str | None = None) -> None:
            self.responses.append(instructions)
            order.append(f"response.create.{len(self.responses)}")
            if len(self.responses) == 3:
                stop_event.set()

        async def inject_origin_query_result(
            self,
            query: dict[str, Any],
            *,
            request_response: bool = True,
        ) -> None:
            self.injected.append(query)

    class FakeController:
        configured = True

        def __init__(self) -> None:
            self.query_count = 0

        async def create_query(self, voice_session_id: str, **kwargs: Any) -> dict[str, Any]:
            self.query_count += 1
            return {
                "query_id": f"voice-query-{self.query_count}",
                "status": "pending",
            }

        async def get_query(self, voice_session_id: str, query_id: str) -> dict[str, Any]:
            if query_id == "voice-query-1":
                return {
                    "query_id": query_id,
                    "status": "answered",
                    "answer": "The file was checked.",
                }
            return {
                "query_id": query_id,
                "status": "failed",
                "error": "The lookup could not be completed.",
            }

        async def mark_query_timeout(self, voice_session_id: str, query_id: str) -> dict[str, Any]:
            raise AssertionError("query should not time out")

    class FakeRtp:
        async def send_payload(self, payload: bytes) -> None:
            raise AssertionError("audio should not be sent")

    async def run() -> FakeRealtime:
        service = DidwwSipBridgeService(
            _config(tmp_path),
            store=store,
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        await asyncio.wait_for(
            service._realtime_to_rtp(  # type: ignore[arg-type]
                record.voice_session_id,
                FakeRtp(),
                realtime,  # type: ignore[arg-type]
                FakeController(),  # type: ignore[arg-type]
                stop_event,
            ),
            timeout=1,
        )
        return realtime

    realtime = asyncio.run(run())

    assert realtime.responses[0] == didww_sip_bridge._CALLER_TURN_COMPLETION_INSTRUCTIONS
    assert len(realtime.responses) == 3
    assert realtime.responses[1] == didww_sip_bridge._ORIGIN_QUERY_ANSWER_RESPONSE_INSTRUCTIONS
    assert "could not complete the lookup right now" in str(realtime.responses[2]).lower()
    assert order.index("conversation.item.input_audio_transcription.completed") < order.index(
        "response.create.1"
    )
    latest = store.get("voice-1")
    assert latest is not None
    requested_query_ids = [
        event.metadata["query_id"]
        for event in latest.debug_events
        if event.event_type == "origin_query.response_requested"
    ]
    assert requested_query_ids == ["voice-query-1", "voice-query-2"]


def test_realtime_origin_query_result_is_not_delivered_after_stop_before_turn_completion(
    tmp_path: Path,
) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    stop_event = asyncio.Event()
    order: list[str] = []

    class FakeRealtime:
        def __init__(self) -> None:
            self.events = [
                {"type": "response.done", "response": {"id": "resp-1"}},
                {"type": "input_audio_buffer.speech_started", "audio_start_ms": 120},
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "call_id": "tool-call-1",
                        "name": "query_origin_agent",
                        "arguments": json.dumps({"question": "What changed?"}),
                    },
                },
                {"type": "__sleep__", "seconds": 0.05},
            ]
            self.responses: list[str | None] = []

        async def recv_json(self) -> dict[str, Any]:
            while self.events:
                event = self.events.pop(0)
                if event["type"] == "__sleep__":
                    await asyncio.sleep(float(event["seconds"]))
                    continue
                order.append(str(event["type"]))
                return event
            stop_event.set()
            await asyncio.sleep(0)
            return {}

        async def send_tool_output(self, *, call_id: str, output: dict[str, Any]) -> None:
            return None

        async def request_response(self, instructions: str | None = None) -> None:
            self.responses.append(instructions)
            order.append("response.create")

        async def inject_origin_query_result(
            self,
            query: dict[str, Any],
            *,
            request_response: bool = True,
        ) -> None:
            return None

    class FakeController:
        configured = True

        async def create_query(self, voice_session_id: str, **kwargs: Any) -> dict[str, Any]:
            return {"query_id": "voice-query-1", "status": "pending"}

        async def get_query(self, voice_session_id: str, query_id: str) -> dict[str, Any]:
            return {
                "query_id": query_id,
                "status": "answered",
                "answer": "The file was checked.",
            }

        async def mark_query_timeout(self, voice_session_id: str, query_id: str) -> dict[str, Any]:
            raise AssertionError("query should not time out")

    class FakeRtp:
        async def send_payload(self, payload: bytes) -> None:
            raise AssertionError("audio should not be sent")

    async def run() -> FakeRealtime:
        service = DidwwSipBridgeService(
            _config(tmp_path),
            store=store,
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        await asyncio.wait_for(
            service._realtime_to_rtp(  # type: ignore[arg-type]
                record.voice_session_id,
                FakeRtp(),
                realtime,  # type: ignore[arg-type]
                FakeController(),  # type: ignore[arg-type]
                stop_event,
            ),
            timeout=1,
        )
        return realtime

    realtime = asyncio.run(run())

    assert realtime.responses == []
    assert "response.create" not in order


def test_slow_origin_query_answer_supersedes_pending_hold_offer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(didww_sip_bridge, "_ORIGIN_QUERY_ACK_GRACE_SECONDS", 0.01)
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    stop_event = asyncio.Event()

    class FakeRealtime:
        def __init__(self) -> None:
            self.events: list[dict[str, Any]] = [
                {"type": "response.created", "response": {"id": "resp-1"}},
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "call_id": "tool-call-1",
                        "name": "query_origin_agent",
                        "arguments": json.dumps({"question": "What changed?"}),
                    },
                },
                {"type": "__sleep__", "seconds": 0.05},
                {"type": "response.done", "response": {"id": "resp-1"}},
            ]
            self.tool_outputs: list[dict[str, object]] = []
            self.responses: list[str | None] = []
            self.injected: list[dict[str, object]] = []

        async def recv_json(self) -> dict[str, Any]:
            while self.events:
                event = self.events.pop(0)
                if event["type"] == "__sleep__":
                    await asyncio.sleep(float(event["seconds"]))
                    continue
                return event
            await asyncio.sleep(0.01)
            return {}

        async def send_tool_output(self, *, call_id: str, output: dict[str, Any]) -> None:
            self.tool_outputs.append({"call_id": call_id, "output": output})

        async def request_response(self, instructions: str | None = None) -> None:
            self.responses.append(instructions)
            stop_event.set()

        async def inject_origin_query_result(
            self,
            query: dict[str, Any],
            *,
            request_response: bool = True,
        ) -> None:
            self.injected.append(query)

    class FakeController:
        configured = True

        def __init__(self) -> None:
            self.create_count = 0
            self.poll_count = 0

        async def create_query(self, voice_session_id: str, **kwargs: Any) -> dict[str, Any]:
            self.create_count += 1
            return {"query_id": "voice-query-1", "status": "pending"}

        async def get_query(self, voice_session_id: str, query_id: str) -> dict[str, Any]:
            self.poll_count += 1
            if self.poll_count < 3:
                return {"query_id": query_id, "status": "pending"}
            return {
                "query_id": query_id,
                "status": "answered",
                "answer": "The deployment summary is available.",
            }

    class FakeRtp:
        async def send_payload(self, payload: bytes) -> None:
            raise AssertionError("audio should not be sent")

    async def run() -> tuple[FakeRealtime, FakeController]:
        service = DidwwSipBridgeService(
            _config(tmp_path),
            store=store,
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        controller = FakeController()
        await asyncio.wait_for(
            service._realtime_to_rtp(  # type: ignore[arg-type]
                record.voice_session_id,
                FakeRtp(),
                realtime,  # type: ignore[arg-type]
                controller,  # type: ignore[arg-type]
                stop_event,
            ),
            timeout=1,
        )
        return realtime, controller

    realtime, controller = asyncio.run(run())

    assert controller.create_count == 1
    assert realtime.tool_outputs == [
        {"call_id": "tool-call-1", "output": {"status": "pending", "query_id": "voice-query-1"}}
    ]
    assert realtime.injected[0]["answer"] == "The deployment summary is available."
    assert len(realtime.responses) == 1
    assert realtime.responses[0] == didww_sip_bridge._ORIGIN_QUERY_ANSWER_RESPONSE_INSTRUCTIONS
    assert "origin agent" not in str(realtime.responses[0]).lower()
    assert "originating agent" not in str(realtime.responses[0]).lower()
    latest = store.get("voice-1")
    assert latest is not None
    requested_reasons = [
        event.metadata["reason"]
        for event in latest.debug_events
        if event.event_type == "origin_query.response_requested"
    ]
    event_types = [event.event_type for event in latest.debug_events]
    assert "origin_query.hold_offer_requested" in event_types
    assert requested_reasons == ["query_result"]


def test_pending_origin_query_suppresses_projected_task_fallback_and_offers_hold(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(didww_sip_bridge, "_ORIGIN_QUERY_ACK_GRACE_SECONDS", 0.01)
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    payload = _phone_payload()
    payload["projection"] = {
        "task_prompt": (
            "Open with: Hi Jonathan, it's Truman. Then share a concise interesting fact."
        ),
        "startup_contract": {"complete_objective": True},
    }
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(payload))
    stop_event = asyncio.Event()

    class FakeRealtime:
        def __init__(self) -> None:
            self.events: list[dict[str, Any]] = [
                {"type": "response.created", "response": {"id": "resp-1"}},
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "call_id": "tool-call-empty",
                        "name": "query_origin_agent",
                        "arguments": "{}",
                    },
                },
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "call_id": "tool-call-real",
                        "name": "query_origin_agent",
                        "arguments": json.dumps(
                            {"question": "Give me one Truman-specific interesting fact."}
                        ),
                    },
                },
                {"type": "__sleep__", "seconds": 0.03},
                {"type": "response.done", "response": {"id": "resp-1", "status": "completed"}},
            ]
            self.tool_outputs: list[dict[str, object]] = []
            self.responses: list[str | None] = []

        async def recv_json(self) -> dict[str, Any]:
            while self.events:
                event = self.events.pop(0)
                if event["type"] == "__sleep__":
                    await asyncio.sleep(float(event["seconds"]))
                    continue
                return event
            await asyncio.sleep(0.01)
            return {}

        async def send_tool_output(self, *, call_id: str, output: dict[str, Any]) -> None:
            self.tool_outputs.append({"call_id": call_id, "output": output})

        async def request_response(self, instructions: str | None = None) -> None:
            self.responses.append(instructions)
            stop_event.set()

    class FakeController:
        configured = True

        def __init__(self) -> None:
            self.create_count = 0

        async def create_query(self, voice_session_id: str, **kwargs: Any) -> dict[str, Any]:
            self.create_count += 1
            return {"query_id": "voice-query-1", "status": "pending"}

        async def get_query(self, voice_session_id: str, query_id: str) -> dict[str, Any]:
            await asyncio.sleep(0.5)
            return {"query_id": query_id, "status": "pending"}

    class FakeRtp:
        async def send_payload(self, payload: bytes) -> None:
            raise AssertionError("test should not play assistant audio")

    async def run() -> tuple[FakeRealtime, FakeController]:
        service = DidwwSipBridgeService(
            _config(tmp_path),
            store=store,
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        controller = FakeController()
        await asyncio.wait_for(
            service._realtime_to_rtp(  # type: ignore[arg-type]
                record.voice_session_id,
                FakeRtp(),
                realtime,  # type: ignore[arg-type]
                controller,  # type: ignore[arg-type]
                stop_event,
            ),
            timeout=1,
        )
        return realtime, controller

    realtime, controller = asyncio.run(run())

    assert controller.create_count == 1
    assert realtime.tool_outputs[0]["call_id"] == "tool-call-empty"
    assert realtime.tool_outputs[0]["output"]["status"] == "answered"
    assert realtime.tool_outputs[1] == {
        "call_id": "tool-call-real",
        "output": {"status": "pending", "query_id": "voice-query-1"},
    }
    assert realtime.responses == [didww_sip_bridge._ORIGIN_QUERY_HOLD_RESPONSE_INSTRUCTIONS]
    response_text = str(realtime.responses[0]).lower()
    assert "look that up for you" in response_text
    assert "one moment" in response_text
    assert "projected calling task" not in response_text
    assert "interesting fact" not in response_text
    assert "discuss" not in response_text
    assert "something relevant" not in response_text
    assert "while you wait" not in response_text
    assert "unrelated filler" not in response_text
    assert "origin agent" not in response_text
    assert "originating agent" not in response_text
    latest = store.get("voice-1")
    assert latest is not None
    event_types = [event.event_type for event in latest.debug_events]
    assert "bridge.response.discarded" in event_types
    assert "origin_query.hold_offer_requested" in event_types
    discarded = next(
        event for event in latest.debug_events if event.event_type == "bridge.response.discarded"
    )
    assert discarded.metadata["reason"] == "origin_query_created"
    assert didww_sip_bridge._PROJECTED_TASK_RESPONSE_KEY in discarded.metadata["request_keys"]


def test_origin_query_hold_ack_does_not_queue_extra_response(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(didww_sip_bridge, "_ORIGIN_QUERY_ACK_GRACE_SECONDS", 0.01)
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    stop_event = asyncio.Event()

    class FakeRealtime:
        def __init__(self) -> None:
            self.events: list[dict[str, Any]] = [
                {"type": "response.created", "response": {"id": "resp-1"}},
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "call_id": "tool-call-1",
                        "name": "query_origin_agent",
                        "arguments": json.dumps({"question": "What changed?"}),
                    },
                },
                {"type": "response.done", "response": {"id": "resp-1", "status": "completed"}},
                {"type": "__sleep__", "seconds": 0.03},
                {"type": "response.created", "response": {"id": "resp-hold"}},
                {"type": "input_audio_buffer.speech_started", "audio_start_ms": 100},
                {"type": "input_audio_buffer.speech_stopped", "audio_end_ms": 500},
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "Yes, please.",
                },
            ]
            self.tool_outputs: list[dict[str, object]] = []
            self.responses: list[str | None] = []

        async def recv_json(self) -> dict[str, Any]:
            while self.events:
                event = self.events.pop(0)
                if event["type"] == "__sleep__":
                    await asyncio.sleep(float(event["seconds"]))
                    continue
                return event
            stop_event.set()
            await asyncio.sleep(0)
            return {}

        async def send_tool_output(self, *, call_id: str, output: dict[str, Any]) -> None:
            self.tool_outputs.append({"call_id": call_id, "output": output})

        async def request_response(self, instructions: str | None = None) -> None:
            self.responses.append(instructions)

        async def cancel_response(self, response_id: str | None = None) -> None:
            return None

    class FakeController:
        configured = True

        async def create_query(self, voice_session_id: str, **kwargs: Any) -> dict[str, Any]:
            return {"query_id": "voice-query-1", "status": "pending"}

        async def get_query(self, voice_session_id: str, query_id: str) -> dict[str, Any]:
            await asyncio.sleep(0.5)
            return {"query_id": query_id, "status": "pending"}

    class FakeRtp:
        async def send_payload(self, payload: bytes) -> None:
            return None

    async def run() -> FakeRealtime:
        service = DidwwSipBridgeService(
            _config(tmp_path),
            store=store,
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        await asyncio.wait_for(
            service._realtime_to_rtp(  # type: ignore[arg-type]
                record.voice_session_id,
                FakeRtp(),
                realtime,  # type: ignore[arg-type]
                FakeController(),  # type: ignore[arg-type]
                stop_event,
            ),
            timeout=1,
        )
        return realtime

    realtime = asyncio.run(run())

    assert realtime.responses == [didww_sip_bridge._ORIGIN_QUERY_HOLD_RESPONSE_INSTRUCTIONS]
    response_text = str(realtime.responses[0]).lower()
    assert "look that up for you" in response_text
    assert "one moment" in response_text
    assert "discuss" not in response_text
    assert "something relevant" not in response_text
    assert "while you wait" not in response_text
    assert "unrelated filler" not in response_text
    assert "origin agent" not in response_text
    assert "originating agent" not in response_text
    assert didww_sip_bridge._is_origin_query_hold_ack("No, I'll wait.")
    assert didww_sip_bridge._is_origin_query_hold_ack("Yes, please.")
    assert not didww_sip_bridge._is_origin_query_hold_ack("Please continue with the topic.")
    latest = store.get("voice-1")
    assert latest is not None
    hold_ack = [
        event
        for event in latest.debug_events
        if event.event_type == "backchannel_ignored"
        and event.metadata.get("reason") == "origin_query_hold_ack"
    ]
    assert len(hold_ack) == 1


def test_realtime_origin_query_hold_ack_flushes_queued_result_without_prelude(
    tmp_path: Path,
) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    stop_event = asyncio.Event()
    order: list[str] = []

    class FakeRealtime:
        def __init__(self) -> None:
            self.events = [
                {"type": "response.done", "response": {"id": "resp-1"}},
                {"type": "input_audio_buffer.speech_started", "audio_start_ms": 120},
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "call_id": "tool-call-1",
                        "name": "query_origin_agent",
                        "arguments": json.dumps({"question": "What changed?"}),
                    },
                },
                {"type": "__sleep__", "seconds": 0.05},
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "No, I'll wait.",
                },
                {"type": "response.done", "response": {"id": "resp-2"}},
            ]
            self.responses: list[str | None] = []
            self.injected: list[dict[str, object]] = []

        async def recv_json(self) -> dict[str, Any]:
            while self.events:
                event = self.events.pop(0)
                if event["type"] == "__sleep__":
                    await asyncio.sleep(float(event["seconds"]))
                    continue
                order.append(str(event["type"]))
                return event
            stop_event.set()
            await asyncio.sleep(0)
            return {}

        async def send_tool_output(self, *, call_id: str, output: dict[str, Any]) -> None:
            return None

        async def request_response(self, instructions: str | None = None) -> None:
            self.responses.append(instructions)
            order.append("response.create")

        async def inject_origin_query_result(
            self,
            query: dict[str, Any],
            *,
            request_response: bool = True,
        ) -> None:
            self.injected.append(query)

    class FakeController:
        configured = True

        async def create_query(self, voice_session_id: str, **kwargs: Any) -> dict[str, Any]:
            assert voice_session_id == "voice-1"
            assert kwargs["question"] == "What changed?"
            return {"query_id": "voice-query-1", "status": "pending"}

        async def get_query(self, voice_session_id: str, query_id: str) -> dict[str, Any]:
            assert voice_session_id == "voice-1"
            assert query_id == "voice-query-1"
            return {
                "query_id": query_id,
                "status": "answered",
                "answer": "The file was checked.",
            }

        async def mark_query_timeout(self, voice_session_id: str, query_id: str) -> dict[str, Any]:
            raise AssertionError("query should not time out")

    class FakeRtp:
        async def send_payload(self, payload: bytes) -> None:
            raise AssertionError("audio should not be sent")

    async def run() -> FakeRealtime:
        service = DidwwSipBridgeService(
            _config(tmp_path),
            store=store,
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        await asyncio.wait_for(
            service._realtime_to_rtp(  # type: ignore[arg-type]
                record.voice_session_id,
                FakeRtp(),
                realtime,  # type: ignore[arg-type]
                FakeController(),  # type: ignore[arg-type]
                stop_event,
            ),
            timeout=1,
        )
        return realtime

    realtime = asyncio.run(run())

    assert realtime.injected[0]["answer"] == "The file was checked."
    assert realtime.responses == [didww_sip_bridge._ORIGIN_QUERY_ANSWER_RESPONSE_INSTRUCTIONS]
    assert order.index("conversation.item.input_audio_transcription.completed") < order.index(
        "response.create"
    )
    latest = store.get("voice-1")
    assert latest is not None
    event_types = [event.event_type for event in latest.debug_events]
    assert "backchannel_ignored" in event_types
    assert "origin_query.response_requested" in event_types


def test_duplicate_answered_origin_query_reuses_answer_without_new_controller_query(
    tmp_path: Path,
) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))

    class FakeRealtime:
        def __init__(self) -> None:
            self.tool_outputs: list[dict[str, object]] = []
            self.responses: list[str | None] = []
            self.injected: list[dict[str, object]] = []

        async def send_tool_output(self, *, call_id: str, output: dict[str, Any]) -> None:
            self.tool_outputs.append({"call_id": call_id, "output": output})

        async def request_response(self, instructions: str | None = None) -> None:
            self.responses.append(instructions)

        async def inject_origin_query_result(
            self,
            query: dict[str, Any],
            *,
            request_response: bool = True,
        ) -> None:
            self.injected.append(query)

    class FakeController:
        configured = True

        def __init__(self) -> None:
            self.create_count = 0

        async def create_query(self, voice_session_id: str, **kwargs: Any) -> dict[str, Any]:
            self.create_count += 1
            return {"query_id": "voice-query-1", "status": "pending"}

        async def get_query(self, voice_session_id: str, query_id: str) -> dict[str, Any]:
            return {
                "query_id": query_id,
                "status": "answered",
                "answer": "The answer is already available.",
            }

    async def run() -> tuple[FakeRealtime, FakeController]:
        service = DidwwSipBridgeService(
            _config(tmp_path),
            store=store,
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        controller = FakeController()
        query_tasks: set[asyncio.Task[None]] = set()
        origin_queries_by_question: dict[str, didww_sip_bridge.OriginQueryState] = {}
        origin_queries_by_id: dict[str, didww_sip_bridge.OriginQueryState] = {}
        await service._handle_realtime_function_call(  # type: ignore[attr-defined]
            record.voice_session_id,
            RealtimeFunctionCall(
                call_id="tool-call-1",
                name="query_origin_agent",
                arguments={"question": "What changed?"},
            ),
            realtime,  # type: ignore[arg-type]
            controller,  # type: ignore[arg-type]
            query_tasks,
            asyncio.Event(),
            origin_queries_by_question=origin_queries_by_question,
            origin_queries_by_id=origin_queries_by_id,
        )
        await asyncio.wait_for(asyncio.gather(*query_tasks), timeout=1)
        await service._handle_realtime_function_call(  # type: ignore[attr-defined]
            record.voice_session_id,
            RealtimeFunctionCall(
                call_id="tool-call-2",
                name="query_origin_agent",
                arguments={"question": "  What   changed? "},
            ),
            realtime,  # type: ignore[arg-type]
            controller,  # type: ignore[arg-type]
            query_tasks,
            asyncio.Event(),
            origin_queries_by_question=origin_queries_by_question,
            origin_queries_by_id=origin_queries_by_id,
        )
        return realtime, controller

    realtime, controller = asyncio.run(run())

    assert controller.create_count == 1
    assert realtime.tool_outputs == [
        {"call_id": "tool-call-1", "output": {"status": "pending", "query_id": "voice-query-1"}},
        {
            "call_id": "tool-call-2",
            "output": {
                "status": "answered",
                "query_id": "voice-query-1",
                "answer": "The answer is already available.",
                "deduplicated": True,
            },
        },
    ]
    assert len(realtime.responses) == 1
    assert realtime.responses[0] == didww_sip_bridge._ORIGIN_QUERY_ANSWER_RESPONSE_INSTRUCTIONS
    assert "origin agent" not in str(realtime.responses[0]).lower()


def test_terminal_origin_query_retry_clears_legacy_delegated_query_cache(
    tmp_path: Path,
) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))

    class FakeRealtime:
        def __init__(self) -> None:
            self.tool_outputs: list[dict[str, object]] = []
            self.responses: list[str | None] = []
            self.injected: list[dict[str, object]] = []

        async def send_tool_output(self, *, call_id: str, output: dict[str, Any]) -> None:
            self.tool_outputs.append({"call_id": call_id, "output": output})

        async def request_response(self, instructions: str | None = None) -> None:
            self.responses.append(instructions)

        async def inject_origin_query_result(
            self,
            query: dict[str, Any],
            *,
            request_response: bool = True,
        ) -> None:
            self.injected.append(query)

    class FakeController:
        configured = True

        def __init__(self) -> None:
            self.create_count = 0

        async def create_query(self, voice_session_id: str, **kwargs: Any) -> dict[str, Any]:
            self.create_count += 1
            if self.create_count == 1:
                return {
                    "query_id": "voice-query-1",
                    "status": "failed",
                    "error": "first failed",
                }
            return {
                "query_id": "voice-query-2",
                "status": "failed",
                "error": "second failed",
            }

    async def run() -> tuple[FakeRealtime, FakeController, dict[str, str]]:
        service = DidwwSipBridgeService(
            _config(tmp_path),
            store=store,
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        controller = FakeController()
        query_tasks: set[asyncio.Task[None]] = set()
        delegated_queries: dict[str, str] = {}
        origin_queries_by_question: dict[str, didww_sip_bridge.OriginQueryState] = {}
        origin_queries_by_id: dict[str, didww_sip_bridge.OriginQueryState] = {}

        async def ask(call_id: str) -> None:
            await service._handle_realtime_function_call(  # type: ignore[attr-defined]
                record.voice_session_id,
                RealtimeFunctionCall(
                    call_id=call_id,
                    name="query_origin_agent",
                    arguments={"question": "What changed?"},
                ),
                realtime,  # type: ignore[arg-type]
                controller,  # type: ignore[arg-type]
                query_tasks,
                asyncio.Event(),
                delegated_queries=delegated_queries,
                origin_queries_by_question=origin_queries_by_question,
                origin_queries_by_id=origin_queries_by_id,
            )

        await ask("tool-call-1")
        await ask("tool-call-2")
        await ask("tool-call-3")
        return realtime, controller, delegated_queries

    realtime, controller, delegated_queries = asyncio.run(run())

    assert controller.create_count == 2
    assert realtime.tool_outputs == [
        {
            "call_id": "tool-call-1",
            "output": {
                "status": "failed",
                "query_id": "voice-query-1",
                "error": "first failed",
            },
        },
        {
            "call_id": "tool-call-2",
            "output": {
                "status": "failed",
                "query_id": "voice-query-1",
                "error": "first failed",
                "deduplicated": True,
            },
        },
        {
            "call_id": "tool-call-3",
            "output": {
                "status": "failed",
                "query_id": "voice-query-2",
                "error": "second failed",
            },
        },
    ]
    assert list(delegated_queries.values()) == ["voice-query-2"]
    assert [query["query_id"] for query in realtime.injected] == [
        "voice-query-1",
        "voice-query-2",
    ]


def test_calling_task_question_answers_from_projection_without_origin_query(
    tmp_path: Path,
) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))

    class FakeRealtime:
        def __init__(self) -> None:
            self.tool_outputs: list[dict[str, object]] = []
            self.responses: list[str | None] = []

        async def send_tool_output(self, *, call_id: str, output: dict[str, Any]) -> None:
            self.tool_outputs.append({"call_id": call_id, "output": output})

        async def request_response(self, instructions: str | None = None) -> None:
            self.responses.append(instructions)

    class FakeController:
        configured = True

        def __init__(self) -> None:
            self.create_count = 0

        async def create_query(self, voice_session_id: str, **kwargs: Any) -> dict[str, Any]:
            self.create_count += 1
            raise AssertionError("projection should answer calling-task questions")

    async def run() -> tuple[FakeRealtime, FakeController, set[asyncio.Task[None]]]:
        service = DidwwSipBridgeService(
            _config(tmp_path),
            store=store,
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        controller = FakeController()
        query_tasks: set[asyncio.Task[None]] = set()
        await service._handle_realtime_function_call(  # type: ignore[attr-defined]
            record.voice_session_id,
            RealtimeFunctionCall(
                call_id="tool-call-1",
                name="query_origin_agent",
                arguments={"question": "What is the calling task?"},
            ),
            realtime,  # type: ignore[arg-type]
            controller,  # type: ignore[arg-type]
            query_tasks,
            asyncio.Event(),
        )
        return realtime, controller, query_tasks

    realtime, controller, query_tasks = asyncio.run(run())

    assert controller.create_count == 0
    assert query_tasks == set()
    assert realtime.tool_outputs == [
        {
            "call_id": "tool-call-1",
            "output": {
                "status": "answered",
                "source": "projection",
                "answer": (
                    "The projected calling task is already authoritative: "
                    "Call Jonathan and discuss the deployment."
                ),
            },
        }
    ]
    assert "projected calling task" in str(realtime.responses[0])


def test_script_instruction_question_answers_from_projection_without_origin_query(
    tmp_path: Path,
) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    payload = _phone_payload()
    payload["projection"] = {
        "task_prompt": (
            "Open with: Hi Jonathan, this is Truman calling about turn-taking. "
            "Ask whether the greeting sounded natural."
        ),
        "startup_contract": {"complete_objective": True},
    }
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(payload))

    class FakeRealtime:
        def __init__(self) -> None:
            self.tool_outputs: list[dict[str, object]] = []
            self.responses: list[str | None] = []

        async def send_tool_output(self, *, call_id: str, output: dict[str, Any]) -> None:
            self.tool_outputs.append({"call_id": call_id, "output": output})

        async def request_response(self, instructions: str | None = None) -> None:
            self.responses.append(instructions)

    class FakeController:
        configured = True

        def __init__(self) -> None:
            self.create_count = 0

        async def create_query(self, voice_session_id: str, **kwargs: Any) -> dict[str, Any]:
            self.create_count += 1
            raise AssertionError("projection should answer startup script questions")

    async def run() -> tuple[FakeRealtime, FakeController]:
        service = DidwwSipBridgeService(
            _config(tmp_path),
            store=store,
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        controller = FakeController()
        await service._handle_realtime_function_call(  # type: ignore[attr-defined]
            record.voice_session_id,
            RealtimeFunctionCall(
                call_id="tool-call-1",
                name="query_origin_agent",
                arguments={"question": "What exact instructions should I follow?"},
            ),
            realtime,  # type: ignore[arg-type]
            controller,  # type: ignore[arg-type]
            set(),
            asyncio.Event(),
        )
        return realtime, controller

    realtime, controller = asyncio.run(run())

    assert controller.create_count == 0
    assert realtime.tool_outputs[0]["output"] == {
        "status": "answered",
        "source": "projection",
        "answer": (
            "The projected calling task is already authoritative: Open with: Hi Jonathan, "
            "this is Truman calling about turn-taking. Ask whether the greeting sounded natural."
        ),
    }
    assert "projected calling task" in str(realtime.responses[0])


def test_empty_origin_query_during_complete_startup_uses_projection_not_clarification(
    tmp_path: Path,
) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    payload = _phone_payload()
    payload["projection"] = {
        "task_prompt": (
            "Open with: Hi Jonathan, this is Truman calling about turn-taking. "
            "Ask whether the greeting sounded natural."
        ),
        "startup_contract": {"complete_objective": True},
    }
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(payload))

    class FakeRealtime:
        def __init__(self) -> None:
            self.tool_outputs: list[dict[str, object]] = []
            self.responses: list[str | None] = []

        async def send_tool_output(self, *, call_id: str, output: dict[str, Any]) -> None:
            self.tool_outputs.append({"call_id": call_id, "output": output})

        async def request_response(self, instructions: str | None = None) -> None:
            self.responses.append(instructions)

    class FakeController:
        configured = True

        async def create_query(self, voice_session_id: str, **kwargs: Any) -> dict[str, Any]:
            raise AssertionError("empty startup tool call should not wake origin")

    async def run() -> FakeRealtime:
        service = DidwwSipBridgeService(
            _config(tmp_path),
            store=store,
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        await service._handle_realtime_function_call(  # type: ignore[attr-defined]
            record.voice_session_id,
            RealtimeFunctionCall(
                call_id="tool-call-1",
                name="query_origin_agent",
                arguments={},
            ),
            realtime,  # type: ignore[arg-type]
            FakeController(),  # type: ignore[arg-type]
            set(),
            asyncio.Event(),
        )
        return realtime

    realtime = asyncio.run(run())

    assert realtime.tool_outputs[0]["output"] == {
        "status": "answered",
        "source": "projection",
        "answer": (
            "The projected calling task is already authoritative: Open with: Hi Jonathan, "
            "this is Truman calling about turn-taking. Ask whether the greeting sounded natural."
        ),
    }
    assert len(realtime.responses) == 1
    assert "projected calling task" in str(realtime.responses[0])
    assert "clarify the question" not in str(realtime.responses[0]).lower()
    latest = store.get("voice-1")
    assert latest is not None
    assert "origin_query.empty_question_answered_from_projection" in [
        event.event_type for event in latest.debug_events
    ]


def test_empty_origin_query_with_incomplete_startup_requests_keyed_call_objective_clarification(
    tmp_path: Path,
) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    payload = _phone_payload()
    payload["projection"] = {
        "task_prompt": "Call Jonathan.",
        "startup_contract": {"complete_objective": False},
    }
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(payload))

    class FakeRealtime:
        def __init__(self) -> None:
            self.tool_outputs: list[dict[str, object]] = []
            self.responses: list[tuple[str | None, dict[str, object]]] = []

        async def send_tool_output(self, *, call_id: str, output: dict[str, Any]) -> None:
            self.tool_outputs.append({"call_id": call_id, "output": output})

    async def request_response(
        instructions: str | None = None,
        **kwargs: object,
    ) -> None:
        realtime.responses.append((instructions, kwargs))

    async def run() -> FakeRealtime:
        service = DidwwSipBridgeService(
            _config(tmp_path),
            store=store,
            start_background_tasks=False,
        )
        query_tasks: set[asyncio.Task[None]] = set()
        await service._handle_realtime_function_call(  # type: ignore[attr-defined]
            record.voice_session_id,
            RealtimeFunctionCall(
                call_id="tool-call-1",
                name="query_origin_agent",
                arguments={},
            ),
            realtime,  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            query_tasks,
            asyncio.Event(),
            request_response=request_response,
        )
        await service._handle_realtime_function_call(  # type: ignore[attr-defined]
            record.voice_session_id,
            RealtimeFunctionCall(
                call_id="tool-call-2",
                name="query_origin_agent",
                arguments={},
            ),
            realtime,  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            query_tasks,
            asyncio.Event(),
            request_response=request_response,
        )
        return realtime

    realtime = FakeRealtime()
    realtime = asyncio.run(run())

    assert realtime.tool_outputs == [
        {
            "call_id": "tool-call-1",
            "output": {"status": "failed", "error": "question is required"},
        },
        {
            "call_id": "tool-call-2",
            "output": {"status": "failed", "error": "question is required"},
        },
    ]
    assert realtime.responses == [
        (
            "Ask exactly one concise caller-facing clarification question about the call objective.",
            {
                "reason": "missing_task_clarification",
                "key": "projection:missing_task_clarification",
            },
        ),
        (
            "Ask exactly one concise caller-facing clarification question about the call objective.",
            {
                "reason": "missing_task_clarification",
                "key": "projection:missing_task_clarification",
            },
        ),
    ]


def test_origin_query_caller_patience_keeps_query_pending_for_reuse(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(didww_sip_bridge, "_ORIGIN_QUERY_ACK_GRACE_SECONDS", 0.05)
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))

    class FakeRealtime:
        def __init__(self) -> None:
            self.injected: list[dict[str, object]] = []
            self.responses: list[str | None] = []
            self.tool_outputs: list[dict[str, object]] = []

        async def inject_origin_query_result(
            self,
            query: dict[str, Any],
            *,
            request_response: bool = True,
        ) -> None:
            self.injected.append(query)

        async def request_response(self, instructions: str | None = None) -> None:
            self.responses.append(instructions)

        async def send_tool_output(self, *, call_id: str, output: dict[str, Any]) -> None:
            self.tool_outputs.append({"call_id": call_id, "output": output})

    class FakeController:
        configured = True

        def __init__(self) -> None:
            self.create_query_calls: list[dict[str, Any]] = []
            self.get_query_calls = 0
            self.mark_timeout_calls = 0

        async def create_query(self, voice_session_id: str, **kwargs: Any) -> dict[str, Any]:
            self.create_query_calls.append(kwargs)
            return {"query_id": "voice-query-1", "status": "pending"}

        async def get_query(self, voice_session_id: str, query_id: str) -> dict[str, Any]:
            self.get_query_calls += 1
            return {"query_id": query_id, "status": "pending"}

        async def mark_query_timeout(self, voice_session_id: str, query_id: str) -> dict[str, Any]:
            self.mark_timeout_calls += 1
            return {
                "query_id": query_id,
                "status": "timeout",
                "error": "Timed out while calling +15551234567 for help.",
            }

    async def run() -> tuple[FakeRealtime, FakeController, float, dict[str, didww_sip_bridge.OriginQueryState], dict[str, didww_sip_bridge.OriginQueryState]]:
        service = DidwwSipBridgeService(
            _config(tmp_path).model_copy(
                update={
                    "voice_query_poll_interval_seconds": 0.01,
                    "voice_query_caller_patience_seconds": 0.03,
                    "voice_query_timeout_seconds": 1.0,
                }
            ),
            store=store,
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        controller = FakeController()
        query_tasks: set[asyncio.Task[None]] = set()
        origin_queries_by_question: dict[str, didww_sip_bridge.OriginQueryState] = {}
        origin_queries_by_id: dict[str, didww_sip_bridge.OriginQueryState] = {}
        loop = asyncio.get_running_loop()
        started_at = loop.time()
        await service._handle_realtime_function_call(  # type: ignore[attr-defined]
            record.voice_session_id,
            RealtimeFunctionCall(
                call_id="tool-call-1",
                name="query_origin_agent",
                arguments={"question": "What changed?"},
            ),
            realtime,  # type: ignore[arg-type]
            controller,  # type: ignore[arg-type]
            query_tasks,
            asyncio.Event(),
            origin_queries_by_question=origin_queries_by_question,
            origin_queries_by_id=origin_queries_by_id,
        )
        while not realtime.responses and loop.time() - started_at < 0.5:
            await asyncio.sleep(0.005)
        elapsed = loop.time() - started_at
        await service._handle_realtime_function_call(  # type: ignore[attr-defined]
            record.voice_session_id,
            RealtimeFunctionCall(
                call_id="tool-call-2",
                name="query_origin_agent",
                arguments={"question": "What changed?"},
            ),
            realtime,  # type: ignore[arg-type]
            controller,  # type: ignore[arg-type]
            query_tasks,
            asyncio.Event(),
            origin_queries_by_question=origin_queries_by_question,
            origin_queries_by_id=origin_queries_by_id,
        )
        while loop.time() - started_at < 0.12:
            await asyncio.sleep(0.005)
        for task in query_tasks:
            task.cancel()
        await asyncio.gather(*query_tasks, return_exceptions=True)
        return realtime, controller, elapsed, origin_queries_by_question, origin_queries_by_id

    realtime, controller, elapsed, origin_queries_by_question, origin_queries_by_id = asyncio.run(
        run()
    )

    assert elapsed < 1.0
    assert controller.create_query_calls[0]["deadline_seconds"] == 1
    assert controller.mark_timeout_calls == 0
    assert realtime.tool_outputs == [
        {
            "call_id": "tool-call-1",
            "output": {"status": "pending", "query_id": "voice-query-1"},
        },
        {
            "call_id": "tool-call-2",
            "output": {
                "status": "pending",
                "query_id": "voice-query-1",
                "deduplicated": True,
            },
        },
    ]
    assert realtime.injected == []
    assert realtime.responses == [didww_sip_bridge._ORIGIN_QUERY_TIMEOUT_RESPONSE_INSTRUCTIONS]
    assert "handoff" not in str(realtime.responses[0]).lower()
    assert len(origin_queries_by_question) == 1
    assert len(origin_queries_by_id) == 1


def test_origin_query_answer_after_caller_patience_is_injected_before_backend_timeout(
    tmp_path: Path,
) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))

    class FakeRealtime:
        def __init__(self) -> None:
            self.injected: list[dict[str, object]] = []
            self.responses: list[str | None] = []

        async def inject_origin_query_result(
            self,
            query: dict[str, Any],
            *,
            request_response: bool = True,
        ) -> None:
            self.injected.append(query)

        async def request_response(self, instructions: str | None = None) -> None:
            self.responses.append(instructions)

    class FakeController:
        def __init__(self) -> None:
            self.started_at = 0.0
            self.get_query_calls = 0
            self.mark_timeout_calls = 0

        async def get_query(self, voice_session_id: str, query_id: str) -> dict[str, Any]:
            self.get_query_calls += 1
            if asyncio.get_running_loop().time() - self.started_at >= 0.05:
                return {
                    "query_id": query_id,
                    "status": "answered",
                    "answer": "The package is ready.",
                }
            return {"query_id": query_id, "status": "pending"}

        async def mark_query_timeout(self, voice_session_id: str, query_id: str) -> dict[str, Any]:
            self.mark_timeout_calls += 1
            return {"query_id": query_id, "status": "timeout"}

    async def run() -> tuple[FakeRealtime, FakeController]:
        service = DidwwSipBridgeService(
            _config(tmp_path).model_copy(
                update={
                    "voice_query_poll_interval_seconds": 0.01,
                    "voice_query_caller_patience_seconds": 0.02,
                    "voice_query_timeout_seconds": 0.2,
                }
            ),
            store=store,
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        controller = FakeController()
        controller.started_at = asyncio.get_running_loop().time()
        await asyncio.wait_for(
            service._poll_origin_query_result(  # type: ignore[arg-type]
                record.voice_session_id,
                "voice-query-late-answer",
                controller,  # type: ignore[arg-type]
                realtime,  # type: ignore[arg-type]
                asyncio.Event(),
            ),
            timeout=1,
        )
        return realtime, controller

    realtime, controller = asyncio.run(run())

    assert controller.mark_timeout_calls == 0
    assert controller.get_query_calls >= 2
    assert realtime.injected == [
        {
            "query_id": "voice-query-late-answer",
            "status": "answered",
            "answer": "The package is ready.",
        }
    ]
    assert realtime.responses == [
        didww_sip_bridge._ORIGIN_QUERY_TIMEOUT_RESPONSE_INSTRUCTIONS,
        didww_sip_bridge._ORIGIN_QUERY_ANSWER_RESPONSE_INSTRUCTIONS,
    ]
    latest = store.get("voice-1")
    assert latest is not None
    event_types = [event.event_type for event in latest.debug_events]
    assert "origin_query.caller_patience_expired" in event_types
    assert "origin_query.answered" in event_types
    requested_reasons = [
        event.metadata["reason"]
        for event in latest.debug_events
        if event.event_type == "origin_query.response_requested"
    ]
    assert requested_reasons == ["caller_patience_expired", "query_result"]


def test_origin_query_timeout_persists_debug_events(tmp_path: Path) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))

    class FakeRealtime:
        def __init__(self) -> None:
            self.injected: list[dict[str, object]] = []
            self.responses: list[str | None] = []

        async def inject_origin_query_result(
            self,
            query: dict[str, Any],
            *,
            request_response: bool = True,
        ) -> None:
            self.injected.append(query)

        async def request_response(self, instructions: str | None = None) -> None:
            self.responses.append(instructions)

    class FakeController:
        async def get_query(self, voice_session_id: str, query_id: str) -> dict[str, Any]:
            return {"query_id": query_id, "status": "pending"}

        async def mark_query_timeout(self, voice_session_id: str, query_id: str) -> dict[str, Any]:
            return {
                "query_id": query_id,
                "status": "timeout",
                "error": "Timed out while calling +15551234567 for help.",
            }

    async def run() -> FakeRealtime:
        service = DidwwSipBridgeService(
            _config(tmp_path).model_copy(
                update={
                    "voice_query_poll_interval_seconds": 0.01,
                    "voice_query_timeout_seconds": 0.03,
                }
            ),
            store=store,
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        await asyncio.wait_for(
            service._poll_origin_query_result(  # type: ignore[arg-type]
                record.voice_session_id,
                "voice-query-timeout",
                FakeController(),  # type: ignore[arg-type]
                realtime,  # type: ignore[arg-type]
                asyncio.Event(),
            ),
            timeout=1,
        )
        return realtime

    realtime = asyncio.run(run())

    assert realtime.injected[0]["status"] == "timeout"
    assert "cannot pull that up right now" in str(realtime.responses[0])
    assert "take a message" in str(realtime.responses[0])
    assert "handoff" not in str(realtime.responses[0]).lower()
    latest = store.get("voice-1")
    assert latest is not None
    event_types = [event.event_type for event in latest.debug_events]
    assert "origin_query.poll_pending" in event_types
    assert "origin_query.timeout" in event_types
    assert "origin_query.result_injected" in event_types
    assert "origin_query.response_requested" in event_types
    timeout_event = next(event for event in latest.debug_events if event.event_type == "origin_query.timeout")
    assert timeout_event.metadata["query_id"] == "voice-query-timeout"
    assert timeout_event.metadata["reason"] == "timeout"
    requested = next(
        event for event in latest.debug_events if event.event_type == "origin_query.response_requested"
    )
    assert requested.metadata["reason"] == "query_timeout"
    debug_json = json.dumps(
        [event.model_dump(mode="json") for event in latest.debug_events],
        sort_keys=True,
    )
    assert "+15551234567" not in debug_json


def test_realtime_function_calls_wait_for_response_done_and_dedupe_queries(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(didww_sip_bridge, "_ORIGIN_QUERY_ACK_GRACE_SECONDS", 0.01)
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    stop_event = asyncio.Event()

    class FakeRealtime:
        def __init__(self) -> None:
            self.events = [
                {"type": "response.created"},
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "call_id": "tool-call-1",
                        "name": "query_origin_agent",
                        "arguments": json.dumps({"question": "What changed?"}),
                    },
                },
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "call_id": "tool-call-2",
                        "name": "query_origin_agent",
                        "arguments": json.dumps({"question": "  What   changed? "}),
                    },
                },
                {"type": "response.done"},
            ]
            self.tool_outputs: list[dict[str, object]] = []
            self.responses: list[str | None] = []

        async def recv_json(self) -> dict[str, Any]:
            if self.events:
                return self.events.pop(0)
            await asyncio.sleep(0.05)
            stop_event.set()
            return {}

        async def send_tool_output(self, *, call_id: str, output: dict[str, Any]) -> None:
            self.tool_outputs.append({"call_id": call_id, "output": output})

        async def request_response(self, instructions: str | None = None) -> None:
            self.responses.append(instructions)
            stop_event.set()

    class FakeController:
        configured = True

        def __init__(self) -> None:
            self.create_count = 0

        async def create_query(self, voice_session_id: str, **kwargs: Any) -> dict[str, Any]:
            self.create_count += 1
            return {"query_id": "voice-query-1", "status": "pending"}

        async def get_query(self, voice_session_id: str, query_id: str) -> dict[str, Any]:
            await asyncio.sleep(0.5)
            return {"query_id": query_id, "status": "pending"}

    class FakeRtp:
        async def recv_payload(self) -> bytes:
            await asyncio.sleep(0.5)
            return b""

    async def run() -> tuple[FakeRealtime, FakeController]:
        service = DidwwSipBridgeService(
            _config(tmp_path),
            store=store,
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        controller = FakeController()
        await asyncio.wait_for(
            service._realtime_to_rtp(  # type: ignore[arg-type]
                record.voice_session_id,
                FakeRtp(),
                realtime,  # type: ignore[arg-type]
                controller,  # type: ignore[arg-type]
                stop_event,
            ),
            timeout=1,
        )
        return realtime, controller

    realtime, controller = asyncio.run(run())

    assert controller.create_count == 1
    assert realtime.tool_outputs == [
        {"call_id": "tool-call-1", "output": {"status": "pending", "query_id": "voice-query-1"}},
        {
            "call_id": "tool-call-2",
            "output": {"status": "pending", "query_id": "voice-query-1", "deduplicated": True},
        },
    ]
    assert realtime.responses == [didww_sip_bridge._ORIGIN_QUERY_HOLD_RESPONSE_INSTRUCTIONS]
    response_text = str(realtime.responses[0]).lower()
    assert "look that up for you" in response_text
    assert "one moment" in response_text
    assert "discuss" not in response_text
    assert "something relevant" not in response_text
    assert "while you wait" not in response_text
    assert "unrelated filler" not in response_text
    assert "origin agent" not in response_text
    assert "originating agent" not in response_text
    latest = store.get("voice-1")
    assert latest is not None
    assert len(latest.transcript_events) == 1
    event_types = [event.event_type for event in latest.debug_events]
    assert "origin_query.hold_offer_requested" in event_types


def test_realtime_active_response_error_requeues_response_request(
    tmp_path: Path,
    caplog: Any,
) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    stop_event = asyncio.Event()
    caplog.set_level(logging.INFO, logger="agent_sandbox.didww_sip_bridge")

    class FakeRealtime:
        def __init__(self) -> None:
            self.events = [
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "call_id": "tool-call-1",
                        "name": "query_origin_agent",
                        "arguments": json.dumps({"question": "What changed?"}),
                    },
                },
                {"type": "__sleep__", "seconds": 0.03},
                {"type": "response.done"},
                {
                    "type": "error",
                    "error": {
                        "code": "conversation_already_has_active_response",
                        "message": "Conversation already has an active response in progress",
                        "debug": {
                            "phone_number": "+15551234567",
                            "raw_payload": "x" * 500,
                        },
                    },
                },
                {"type": "response.done"},
            ]
            self.responses: list[str | None] = []

        async def recv_json(self) -> dict[str, Any]:
            while self.events:
                event = self.events.pop(0)
                if event["type"] == "__sleep__":
                    await asyncio.sleep(float(event["seconds"]))
                    continue
                return event
            await asyncio.sleep(0.01)
            return {}

        async def send_tool_output(self, *, call_id: str, output: dict[str, Any]) -> None:
            return None

        async def inject_origin_query_result(
            self,
            query: dict[str, Any],
            *,
            request_response: bool = True,
        ) -> None:
            return None

        async def request_response(self, instructions: str | None = None) -> None:
            self.responses.append(instructions)
            if len(self.responses) == 2:
                stop_event.set()

    class FakeController:
        configured = True

        async def create_query(self, voice_session_id: str, **kwargs: Any) -> dict[str, Any]:
            return {"query_id": "voice-query-1", "status": "pending"}

        async def get_query(self, voice_session_id: str, query_id: str) -> dict[str, Any]:
            await asyncio.sleep(0.01)
            return {
                "query_id": query_id,
                "status": "answered",
                "answer": "The deployment note was checked.",
            }

    class FakeRtp:
        async def recv_payload(self) -> bytes:
            await asyncio.sleep(0.5)
            return b""

    async def run() -> FakeRealtime:
        service = DidwwSipBridgeService(
            _config(tmp_path),
            store=store,
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        await asyncio.wait_for(
            service._realtime_to_rtp(  # type: ignore[arg-type]
                record.voice_session_id,
                FakeRtp(),
                realtime,  # type: ignore[arg-type]
                FakeController(),  # type: ignore[arg-type]
                stop_event,
            ),
            timeout=1,
        )
        return realtime

    realtime = asyncio.run(run())

    assert len(realtime.responses) == 2
    assert all(
        item == didww_sip_bridge._ORIGIN_QUERY_ANSWER_RESPONSE_INSTRUCTIONS
        for item in realtime.responses
    )
    assert all("origin agent" not in str(item).lower() for item in realtime.responses)
    latest = store.get("voice-1")
    assert latest is not None
    event_types = [event.event_type for event in latest.debug_events]
    assert "bridge.response.queued" in event_types
    assert "bridge.response.flushed" in event_types
    assert "bridge.response.requeued" in event_types
    assert "error" in event_types
    queued = next(
        event for event in latest.debug_events if event.event_type == "bridge.response.queued"
    )
    assert queued.metadata["request_reason"] == "query_result"
    assert len(queued.metadata["instruction_sha256"]) == 64
    assert "heard back on the earlier question" in queued.metadata["instruction_snippet"]
    assert "origin agent" not in queued.metadata["instruction_snippet"].lower()
    requeued = next(event for event in latest.debug_events if event.event_type == "bridge.response.requeued")
    assert requeued.metadata["reason"] == "conversation_already_has_active_response"
    error_event = next(event for event in latest.debug_events if event.event_type == "error")
    assert error_event.metadata["reason"] == "conversation_already_has_active_response"
    debug_json = json.dumps(
        [event.model_dump(mode="json") for event in latest.debug_events],
        sort_keys=True,
    )
    assert "+15551234567" not in debug_json
    assert "raw_payload" not in debug_json
    assert "didww_bridge_debug_event" in caplog.text
    assert "event_type=error" in caplog.text
    assert "reason=conversation_already_has_active_response" in caplog.text


def test_realtime_function_call_reports_controller_failure_without_raising(
    tmp_path: Path,
) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))

    class FakeRealtime:
        def __init__(self) -> None:
            self.tool_outputs: list[dict[str, object]] = []
            self.responses: list[str | None] = []

        async def send_tool_output(self, *, call_id: str, output: dict[str, Any]) -> None:
            self.tool_outputs.append({"call_id": call_id, "output": output})

        async def request_response(self, instructions: str | None = None) -> None:
            self.responses.append(instructions)

    class FailingController:
        configured = True

        async def create_query(self, voice_session_id: str, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("controller unavailable")

    async def run() -> tuple[FakeRealtime, set[asyncio.Task[None]]]:
        service = DidwwSipBridgeService(
            _config(tmp_path),
            store=store,
            start_background_tasks=False,
        )
        realtime = FakeRealtime()
        query_tasks: set[asyncio.Task[None]] = set()
        await service._handle_realtime_function_call(  # type: ignore[attr-defined]
            record.voice_session_id,
            RealtimeFunctionCall(
                call_id="tool-call-1",
                name="query_origin_agent",
                arguments={"question": "What changed?"},
            ),
            realtime,  # type: ignore[arg-type]
            FailingController(),  # type: ignore[arg-type]
            query_tasks,
            asyncio.Event(),
        )
        return realtime, query_tasks

    realtime, query_tasks = asyncio.run(run())

    assert query_tasks == set()
    assert realtime.tool_outputs == [
        {
            "call_id": "tool-call-1",
            "output": {"status": "failed", "error": "controller unavailable"},
        }
    ]
    assert "cannot complete that lookup" in str(realtime.responses[0])
    assert "origin agent" not in str(realtime.responses[0]).lower()


def test_run_call_registers_ari_app_before_originating_channels(tmp_path: Path) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    order: list[str] = []
    app_registered = False

    class FakeRealtime:
        def __init__(self) -> None:
            self.responses: list[str | None] = []

        async def __aenter__(self) -> "FakeRealtime":
            return self

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        async def request_response(self, instructions: str | None = None) -> None:
            self.responses.append(instructions)
            order.append("initial_response")

    class FakeRtpEndpoint:
        port: int | None = None
        remote_addr: tuple[str, int] | None = None

        async def start(self) -> None:
            self.port = 49000

        def close(self) -> None:
            order.append("rtp_close")

    class FakeAriClient:
        @asynccontextmanager
        async def event_stream(self) -> Any:
            nonlocal app_registered
            order.append("event_stream_open")
            app_registered = True
            try:
                yield object()
            finally:
                app_registered = False
                order.append("event_stream_close")

        async def start_external_media_call(self, **_: Any) -> AsteriskCallHandles:
            assert app_registered
            assert _.get("event_websocket") is not None
            order.append("start_external_media_call")
            return AsteriskCallHandles(
                call_channel_id="call-didww-test",
                bridge_id="bridge-didww-test",
                external_media_channel_id="media-didww-test",
                external_media_local_address="127.0.0.1",
                external_media_local_port=27086,
            )

        async def hangup_channel(self, channel_id: str | None) -> None:
            return None

        async def destroy_bridge(self, bridge_id: str | None) -> None:
            return None

    async def run() -> None:
        service = DidwwSipBridgeService(
            _config(tmp_path),
            store=store,
            ari_client=FakeAriClient(),  # type: ignore[arg-type]
            realtime_factory=lambda _projection, _realtime: FakeRealtime(),  # type: ignore[arg-type]
            rtp_endpoint_factory=FakeRtpEndpoint,  # type: ignore[arg-type]
            start_background_tasks=False,
        )

        async def bridge_media_until_done(*args: Any, **kwargs: Any) -> None:
            rtp = args[2]
            assert rtp.remote_addr == ("127.0.0.1", 27086)
            assert kwargs["ari_events"] is not None
            order.append("bridge_media")

        service._bridge_media_until_done = bridge_media_until_done  # type: ignore[method-assign]
        await service.run_call(record, PhoneCallStartRequest.model_validate(_phone_payload()))

    asyncio.run(run())

    assert order == [
        "event_stream_open",
        "start_external_media_call",
        "initial_response",
        "bridge_media",
        "event_stream_close",
        "rtp_close",
    ]
    latest = store.get(record.voice_session_id)
    assert latest is not None
    assert latest.ari_channel_id == "call-didww-test"
    initial_event = next(
        event
        for event in latest.debug_events
        if event.event_type == "bridge.response.initial_greeting_requested"
    )
    assert initial_event.metadata["reason"] == "call_started"
    assert len(initial_event.metadata["instruction_sha256"]) == 64


def test_attach_asterisk_keeps_call_connecting_until_answer(tmp_path: Path) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))

    latest = store.attach_asterisk(
        record.voice_session_id,
        ari_channel_id="call-didww-test",
        ari_bridge_id="bridge-didww-test",
        ari_external_media_channel_id="media-didww-test",
        rtp_port=49000,
    )

    assert latest.status == "connecting"


def test_asterisk_hangup_event_completes_call(tmp_path: Path) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    store.attach_asterisk(
        record.voice_session_id,
        ari_channel_id="call-didww-test",
        ari_bridge_id="bridge-didww-test",
        ari_external_media_channel_id="media-didww-test",
        rtp_port=49000,
    )

    class FakeWebSocket:
        def __init__(self) -> None:
            self.events = [
                {
                    "type": "ChannelStateChange",
                    "channel": {"id": "call-didww-test", "state": "Up"},
                },
                {
                    "type": "ChannelHangupRequest",
                    "channel": {"id": "call-didww-test"},
                    "cause_txt": "normal clearing",
                },
            ]

        async def recv(self) -> str:
            return json.dumps(self.events.pop(0))

    class FakeAriClient:
        @asynccontextmanager
        async def event_stream(self) -> Any:
            yield FakeWebSocket()

        async def hangup_channel(self, channel_id: str | None) -> None:
            return None

        async def destroy_bridge(self, bridge_id: str | None) -> None:
            return None

    service = DidwwSipBridgeService(
        _config(tmp_path),
        store=store,
        ari_client=FakeAriClient(),  # type: ignore[arg-type]
        start_background_tasks=False,
    )

    asyncio.run(
        service._wait_for_asterisk_end(  # type: ignore[attr-defined]
            "voice-1",
            AsteriskCallHandles(
                call_channel_id="call-didww-test",
                bridge_id="bridge-didww-test",
                external_media_channel_id="media-didww-test",
            ),
            asyncio.Event(),
        )
    )

    latest = store.get("voice-1")
    assert latest is not None
    assert latest.status == "ended"
    assert latest.summary == "Call ended: normal clearing"
    event_types = [event.event_type for event in latest.debug_events]
    assert "asterisk.channel_up" in event_types
    assert "asterisk.hangup" in event_types


def test_asterisk_hangup_failure_causes_mark_call_failed(tmp_path: Path) -> None:
    cases: list[dict[str, object]] = [
        {"cause_txt": "No Answer"},
        {"cause_txt": "User busy"},
        {"cause": 17},
        {"cause": "19"},
    ]

    for index, cause_fields in enumerate(cases):
        store = DidwwBridgeStore(tmp_path / f"bridge-{index}.json")
        payload = _phone_payload()
        payload["voice_session_id"] = f"voice-{index}"
        payload["request_id"] = f"request-{index}"
        record, _ = store.create_call(PhoneCallStartRequest.model_validate(payload))
        store.attach_asterisk(
            record.voice_session_id,
            ari_channel_id=f"call-didww-test-{index}",
            ari_bridge_id=f"bridge-didww-test-{index}",
            ari_external_media_channel_id=f"media-didww-test-{index}",
            rtp_port=49000 + index,
        )

        class FakeWebSocket:
            async def recv(self) -> str:
                return json.dumps(
                    {
                        "type": "ChannelHangupRequest",
                        "channel": {"id": f"call-didww-test-{index}"},
                        **cause_fields,
                    }
                )

        class FakeAriClient:
            @asynccontextmanager
            async def event_stream(self) -> Any:
                yield FakeWebSocket()

        service = DidwwSipBridgeService(
            _config(tmp_path),
            store=store,
            ari_client=FakeAriClient(),  # type: ignore[arg-type]
            start_background_tasks=False,
        )

        asyncio.run(
            service._wait_for_asterisk_end(  # type: ignore[attr-defined]
                record.voice_session_id,
                AsteriskCallHandles(
                    call_channel_id=f"call-didww-test-{index}",
                    bridge_id=f"bridge-didww-test-{index}",
                    external_media_channel_id=f"media-didww-test-{index}",
                ),
                asyncio.Event(),
            )
        )

        latest = store.get(record.voice_session_id)
        assert latest is not None
        assert latest.status == "failed"


def test_bridge_completion_sanitizes_terminal_summary_and_error(tmp_path: Path) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))

    completed = store.complete(
        record.voice_session_id,
        status="failed",
        summary="Call failed for +1 (555) 000-0100",
        error="Caller +1 (555) 000-0200 triggered a bridge failure",
    )

    serialized = store.path.read_text(encoding="utf-8")
    assert "+15550000100" not in serialized
    assert "+15550000200" not in serialized
    assert "+15550000100" not in (completed.summary or "")
    assert "+15550000200" not in (completed.error or "")
    assert completed.status == "failed"
