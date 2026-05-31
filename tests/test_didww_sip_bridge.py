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
    payload = b"\x55" * ((samples_per_frame * 2) + 80)

    asyncio.run(endpoint.send_payload(payload))

    packets = [RtpPacket.parse(data) for data, _addr in sent]
    assert [len(packet.payload) for packet in packets] == [
        samples_per_frame,
        samples_per_frame,
        80,
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
    session = captured["session_update"]["session"]
    assert session["audio"]["input"]["format"] == {"type": "audio/pcma"}
    assert session["audio"]["output"]["format"] == {"type": "audio/pcma"}


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


def test_realtime_function_calls_wait_for_response_done_and_dedupe_queries(
    tmp_path: Path,
) -> None:
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
    assert realtime.responses == [
        "Tell the caller you are checking with the originating agent, then continue naturally."
    ]
    latest = store.get("voice-1")
    assert latest is not None
    assert len(latest.transcript_events) == 1


def test_realtime_active_response_error_requeues_response_request(
    tmp_path: Path,
) -> None:
    store = DidwwBridgeStore(tmp_path / "bridge.json")
    record, _ = store.create_call(PhoneCallStartRequest.model_validate(_phone_payload()))
    stop_event = asyncio.Event()
    instruction = "Tell the caller you are checking with the originating agent, then continue naturally."

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
                {"type": "response.done"},
                {
                    "type": "error",
                    "error": {
                        "code": "conversation_already_has_active_response",
                        "message": "Conversation already has an active response in progress",
                    },
                },
                {"type": "response.done"},
            ]
            self.responses: list[str | None] = []

        async def recv_json(self) -> dict[str, Any]:
            if self.events:
                return self.events.pop(0)
            await asyncio.sleep(0.01)
            return {}

        async def send_tool_output(self, *, call_id: str, output: dict[str, Any]) -> None:
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
            await asyncio.sleep(0.5)
            return {"query_id": query_id, "status": "pending"}

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

    assert realtime.responses == [instruction, instruction]


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
