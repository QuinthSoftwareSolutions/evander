from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from starlette.testclient import TestClient

from agent_sandbox.didww_sip_bridge import (
    AsteriskAriClient,
    AsteriskCallHandles,
    DidwwBridgeStore,
    DidwwSipBridgeConfig,
    DidwwSipBridgeService,
    OpenAIRealtimeSession,
    PhoneCallStartRequest,
    RealtimeFunctionCall,
    RtpPacket,
    RtpUdpEndpoint,
    _realtime_audio_delta,
    _realtime_function_call,
    create_app,
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
    assert [request.method for request in requests] == ["POST", "POST", "POST", "POST", "POST"]
    assert requests[0].url.path == "/ari/bridges/bridge-didww-test"
    assert requests[1].url.path == "/ari/channels/call-didww-test"
    assert requests[1].url.params["endpoint"] == "PJSIP/+15551234567@didww"
    assert requests[2].url.path == "/ari/channels/externalMedia"
    assert requests[2].url.params["external_host"] == "127.0.0.1:49000"
    assert requests[2].url.params["format"] == "slin16"
    assert requests[3].url.path == "/ari/bridges/bridge-didww-test/addChannel"
    assert requests[4].url.path == "/ari/bridges/bridge-didww-test/addChannel"


def test_rtp_packet_roundtrip() -> None:
    packet = RtpPacket(
        sequence=42,
        timestamp=320,
        ssrc=1234,
        payload_type=118,
        payload=b"\x01\x02\x03\x04",
        marker=True,
    )

    parsed = RtpPacket.parse(packet.serialize())

    assert parsed.sequence == 42
    assert parsed.timestamp == 320
    assert parsed.ssrc == 1234
    assert parsed.payload_type == 118
    assert parsed.marker is True
    assert parsed.payload == b"\x01\x02\x03\x04"


def test_rtp_endpoint_chunks_output_payloads_by_frame_duration(tmp_path: Path) -> None:
    config = _config(tmp_path)
    endpoint = RtpUdpEndpoint(config)
    endpoint.remote_addr = ("127.0.0.1", 49999)
    sent: list[tuple[bytes, tuple[str, int]]] = []

    class FakeTransport:
        def sendto(self, data: bytes, addr: tuple[str, int]) -> None:
            sent.append((data, addr))

    endpoint.transport = FakeTransport()  # type: ignore[assignment]
    samples_per_frame = int(config.sample_rate_hz * config.frame_duration_ms / 1000)
    payload = b"\x01\x02" * ((samples_per_frame * 2) + 80)

    endpoint.send_payload(payload)

    packets = [RtpPacket.parse(data) for data, _addr in sent]
    assert [len(packet.payload) for packet in packets] == [
        samples_per_frame * 2,
        samples_per_frame * 2,
        80 * 2,
    ]
    assert [packet.sequence for packet in packets] == [1, 2, 3]
    assert [packet.timestamp for packet in packets] == [
        0,
        samples_per_frame,
        samples_per_frame * 2,
    ]
    assert endpoint._timestamp == (samples_per_frame * 2) + 80


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

        async def inject_origin_query_result(self, query: dict[str, Any]) -> None:
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
                "answer": "The origin agent checked the file.",
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
    assert "checking with the originating agent" in str(realtime.responses[0])
    assert realtime.injected[0]["answer"] == "The origin agent checked the file."
    latest = store.get("voice-1")
    assert latest is not None
    assert latest.transcript_events[-1].speaker == "origin_agent"


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
    assert "cannot check the origin agent" in str(realtime.responses[0])


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
        async def recv(self) -> str:
            return json.dumps(
                {
                    "type": "ChannelHangupRequest",
                    "channel": {"id": "call-didww-test"},
                    "cause_txt": "normal clearing",
                }
            )

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
