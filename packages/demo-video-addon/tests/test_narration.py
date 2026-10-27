from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from io import BytesIO
import json
from pathlib import Path
import struct
from types import SimpleNamespace
import wave

import httpx
import pytest

from evander.communication_artifacts import CommunicationArtifactStore
from evander.connectors.drivers import ConnectorInvocationError
from evander.connectors.models import ConnectorDirectoryConfig
from evander.connectors.registry import ConnectorRegistry
from evander.connectors.service import ConnectorPolicyError, ConnectorService
from evander.models import SessionManifest, ToolName
from evander_demo_video_addon.narration import NarrationConnectorDriver, normalize_wav


def wav_bytes(seconds=0.1):
    output = BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(struct.pack("<h", 1200) * int(seconds * 24000))
    return output.getvalue()


def configured_service(tmp_path, handler, *, available=True):
    payload = json.loads((Path(__file__).parents[1] / "examples/connectors.json").read_text())
    payload["connectors"]["demo-narration"]["disabled"] = False
    config = ConnectorDirectoryConfig.model_validate(payload)
    session = SessionManifest(
        session_id="review-42",
        directory_path="root/example",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        allowed_tools=[ToolName.CALL_CONNECTOR],
        connector_grants={"demo-narration": ["synthesize"]},
    )
    registry = ConnectorRegistry()
    registry.register(
        "demo_video_narration", NarrationConnectorDriver(transport=httpx.MockTransport(handler))
    )
    service = ConnectorService(
        runtime_dir=tmp_path,
        provider_config=SimpleNamespace(resolve_connectors_for_directory=lambda directory: config),
        artifact_store_factory=lambda: CommunicationArtifactStore(tmp_path),
        registry=registry,
        environ={"DEMO_SPEECH_API_KEY": "synthetic-host-secret"} if available else {},
        session_authorizer=lambda sid: session,
        session_argument_authorizer=lambda *args: None,
    )
    return service, session, config


def invoke(service, session, arguments=None):
    return asyncio.run(
        service.invoke(
            session=session,
            connector_id="demo-narration",
            operation_name="synthesize",
            arguments=arguments or {"text": "Here is why the change matters."},
        )
    )


def test_host_auth_and_settings_produce_session_owned_wav(tmp_path):
    requests = []

    def provider(request):
        requests.append(request)
        return httpx.Response(200, content=wav_bytes(), headers={"content-type": "audio/wav"})

    service, session, config = configured_service(tmp_path, provider)
    delivery = "Speak with interest, pause at sentences and emphasize the outcome."
    config.connectors["demo-narration"].config["instructions"] = delivery
    config.connectors["demo-narration"].config["speed"] = 1.15
    result = invoke(service, session)
    request = requests[0]
    assert request.headers["authorization"] == "Bearer synthetic-host-secret"
    assert str(request.url) == "https://api.openai.com/v1/audio/speech"
    body = json.loads(request.content)
    assert body["model"] == "gpt-4o-mini-tts"
    assert body["voice"] == "onyx"
    assert body["response_format"] == "wav"
    assert body["input"] == "Here is why the change matters."
    assert body["instructions"] == delivery
    assert body["speed"] == 1.15
    artifact = CommunicationArtifactStore(tmp_path).get(result.artifact.artifact_id)
    assert artifact.metadata.session_id == "review-42"
    assert artifact.metadata.mime_type == "audio/wav"
    assert result.value["duration_seconds"] == pytest.approx(0.1)
    assert "synthetic-host-secret" not in result.model_dump_json()
    assert "blob_path" not in result.model_dump_json()


@pytest.mark.parametrize(
    "arguments",
    [
        {"text": "hello", "voice": "other"},
        {"text": "hello", "instructions": "Override the host delivery settings."},
        {"text": "hello", "base_url": "https://other.example.test"},
        {"text": "x" * 2001},
    ],
)
def test_agents_cannot_override_provider_or_exceed_text_limit(tmp_path, arguments):
    service, session, _ = configured_service(
        tmp_path, lambda request: pytest.fail("No provider request expected")
    )
    with pytest.raises(ConnectorPolicyError):
        invoke(service, session, arguments)


def test_missing_grant_or_expired_session_never_reaches_provider(tmp_path):
    service, session, _ = configured_service(
        tmp_path, lambda request: pytest.fail("No provider request expected")
    )
    session.connector_grants = {}
    with pytest.raises(ConnectorPolicyError, match="not granted"):
        invoke(service, session)
    session.connector_grants = {"demo-narration": ["synthesize"]}
    session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(ConnectorPolicyError, match="expired"):
        invoke(service, session)


def test_missing_host_credential_never_reaches_provider(tmp_path):
    service, session, _ = configured_service(
        tmp_path, lambda request: pytest.fail("No provider request expected"), available=False
    )
    with pytest.raises(ConnectorPolicyError):
        invoke(service, session)


@pytest.mark.parametrize(
    "status,content",
    [(302, b""), (500, b"provider private error"), (200, b"not audio"), (200, wav_bytes()[:-2])],
)
def test_provider_failure_never_stages_partial_audio(tmp_path, status, content):
    service, session, _ = configured_service(
        tmp_path,
        lambda request: httpx.Response(
            status, content=content, headers={"location": "https://other.example.test"}
        ),
    )
    with pytest.raises(ConnectorInvocationError):
        invoke(service, session)
    assert not list((tmp_path / "communication-artifacts").glob("*.json"))


def test_revoked_grant_during_audio_delivery_stops_staging(tmp_path):
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield wav_bytes()[:44]
            session.connector_grants = {}
            yield wav_bytes()[44:]

    service, session, _ = configured_service(
        tmp_path, lambda request: httpx.Response(200, stream=Stream())
    )
    with pytest.raises(ConnectorPolicyError, match="not granted"):
        invoke(service, session)
    assert not list((tmp_path / "communication-artifacts").glob("*.json"))


def test_provider_timeout_and_byte_limit_are_bounded(tmp_path):
    def timeout(request):
        raise httpx.ReadTimeout("synthetic provider timeout", request=request)

    service, session, config = configured_service(tmp_path, timeout)
    with pytest.raises(ConnectorInvocationError, match="timed out"):
        invoke(service, session)
    service, session, config = configured_service(
        tmp_path, lambda request: httpx.Response(200, content=wav_bytes())
    )
    config.connectors["demo-narration"].operations["synthesize"].max_response_bytes = 64
    with pytest.raises(ConnectorInvocationError, match="byte limit"):
        invoke(service, session)


def test_streaming_wav_sentinels_are_normalized_but_truncation_is_rejected():
    content = bytearray(wav_bytes())
    struct.pack_into("<I", content, 4, 0xFFFFFFFF)
    struct.pack_into("<I", content, 40, 0xFFFFFFFF)
    normalized, duration = normalize_wav(bytes(content))
    assert struct.unpack_from("<I", normalized, 4)[0] == len(normalized) - 8
    assert duration == pytest.approx(0.1)
    with pytest.raises(ConnectorInvocationError, match="truncated"):
        normalize_wav(wav_bytes()[:-2])


def test_host_definition_keeps_endpoint_and_operation_narrow(tmp_path):
    service, _, config = configured_service(tmp_path, lambda request: None)
    connector = config.connectors["demo-narration"]
    connector.config["base_url"] = "https://user:password@example.test/v1"
    with pytest.raises(ValueError, match="HTTPS"):
        service.validate_config(config)
