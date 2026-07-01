from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from evander.config import GatewayConfig
from evander.email.http_utils import ProviderRequestError
from evander.directory_config import ContactDirectoryConfig
from evander.gateway import create_app
from evander.models import SessionManifest, ToolName
from evander.retell_phone import (
    RetellPhoneCallStore,
    RetellPhoneClient,
    RetellPhoneCallService,
    RetellWebhookSignatureError,
    verify_retell_webhook_signature,
)
from evander.runtime import build_runtime


def _config(tmp_path: Path, **kwargs: object) -> GatewayConfig:
    return GatewayConfig(
        runtime_dir=tmp_path,
        audit_log_path=tmp_path / "audit.jsonl",
        session_store_path=tmp_path / "sessions.json",
        approval_store_path=tmp_path / "approvals.json",
        draft_store_path=tmp_path / "drafts.json",
        email_store_path=tmp_path / "emails.json",
        discord_command_store_path=tmp_path / "discord.json",
        retell_api_base_url="https://retell.test",
        retell_from_number="+15550000001",
        retell_default_agent_id="agent-default",
        **kwargs,
    )


def _session(
    session_id: str,
    *,
    directory_path: str = "root",
    allowed_contact_ids: list[str] | None = None,
) -> SessionManifest:
    return SessionManifest(
        session_id=session_id,
        directory_path=directory_path,
        allowed_tools=[ToolName.MAKE_PHONE_CALL],
        allowed_contact_ids=allowed_contact_ids,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def _contacts() -> ContactDirectoryConfig:
    return ContactDirectoryConfig.model_validate(
        {
            "contacts": {
                "ops": {
                    "name": "Ops Duty",
                    "phone_numbers": [
                        {"label": "main", "number": "+15550000002", "primary": True}
                    ],
                },
                "support": {
                    "name": "Support",
                    "phone_numbers": [
                        {"label": "main", "number": "+15550000003", "primary": True}
                    ],
                },
            }
        }
    )


def _signature(raw_body: bytes, api_key: str, timestamp_ms: int | None = None) -> str:
    timestamp_ms = timestamp_ms or int(datetime.now(UTC).timestamp() * 1000)
    digest = hmac.new(
        api_key.encode("utf-8"),
        raw_body + str(timestamp_ms).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"v={timestamp_ms},d={digest}"


def test_retell_client_create_phone_call_payload_and_auth() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            201,
            json={"call_id": "call-1", "call_status": "registered"},
        )

    client = RetellPhoneClient(
        api_base_url="https://retell.test",
        api_key="secret-key",
        timeout_seconds=12.5,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = client.create_phone_call(
        from_number="+15550000001",
        to_number="+15550000002",
        override_agent_id="agent-1",
        metadata={"request_id": "phone-1"},
        agent_question="Ask whether the shipment arrived.",
    )

    assert result["call_id"] == "call-1"
    assert requests[0].method == "POST"
    assert str(requests[0].url) == "https://retell.test/v2/create-phone-call"
    assert requests[0].headers["authorization"] == "Bearer secret-key"
    payload = json.loads(requests[0].content.decode("utf-8"))
    assert payload["from_number"] == "+15550000001"
    assert payload["to_number"] == "+15550000002"
    assert payload["override_agent_id"] == "agent-1"
    assert payload["metadata"] == {"request_id": "phone-1"}
    assert payload["retell_llm_dynamic_variables"] == {
        "agent_question": "Ask whether the shipment arrived."
    }


def test_retell_client_surfaces_provider_errors() -> None:
    client = RetellPhoneClient(
        api_base_url="https://retell.test",
        api_key="secret-key",
        timeout_seconds=12.5,
        client=httpx.Client(
            transport=httpx.MockTransport(lambda _request: httpx.Response(500, json={}))
        ),
    )

    with pytest.raises(ProviderRequestError, match="retell.create_phone_call failed"):
        client.create_phone_call(
            from_number="+15550000001",
            to_number="+15550000002",
            override_agent_id="agent-1",
            metadata={},
            agent_question="Question",
        )


def test_retell_store_idempotency_and_sanitized_payload(tmp_path: Path) -> None:
    store = RetellPhoneCallStore(tmp_path / "retell-phone-calls-store.json")

    first, created = store.prepare_call(
        session_id="session-1",
        directory_path="root/team-a",
        contact_alias="ops",
        contact_display_name="Ops Duty",
        prompt_metadata={"sha256": "prompt-sha", "length": 8},
        metadata={"safe": "visible", "phone": "+15550009999"},
        request_id="phone-1",
    )
    second, duplicate_created = store.prepare_call(
        session_id="session-1",
        directory_path="root/team-a",
        contact_alias="ops",
        contact_display_name="Ops Duty",
        prompt_metadata={"sha256": "prompt-sha", "length": 8},
        metadata={"safe": "visible", "phone": "+15550009999"},
        request_id="phone-1",
    )
    updated = store.attach_registered_call(
        "phone-1",
        {
            "call_id": "call-1",
            "call_status": "registered",
            "from_number": "+15550000001",
            "to_number": "+15550000002",
            "metadata": {"customer_phone": "+15550000003", "safe": "ok"},
        },
    )

    assert created is True
    assert duplicate_created is False
    assert second.request_id == first.request_id
    assert updated.call_id == "call-1"
    raw_text = (tmp_path / "retell-phone-calls-store.json").read_text(encoding="utf-8")
    assert "+15550000001" not in raw_text
    assert "+15550000002" not in raw_text
    assert "+15550000003" not in raw_text
    assert "+15550009999" not in raw_text


def test_retell_store_rejects_request_id_reuse_with_different_inputs(tmp_path: Path) -> None:
    store = RetellPhoneCallStore(tmp_path / "retell-phone-calls-store.json")
    store.prepare_call(
        session_id="session-1",
        directory_path="root",
        contact_alias="ops",
        contact_display_name="Ops Duty",
        prompt_metadata={"sha256": "prompt-sha", "length": 8},
        request_id="phone-1",
    )

    with pytest.raises(ValueError, match="different parameters"):
        store.prepare_call(
            session_id="session-1",
            directory_path="root",
            contact_alias="sales",
            contact_display_name="Sales",
            prompt_metadata={"sha256": "prompt-sha", "length": 8},
            request_id="phone-1",
        )


def test_retell_phone_call_service_enforces_contact_allowlist_before_provider_request(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json={"call_id": "call-1", "call_status": "registered"})

    client = RetellPhoneClient(
        api_base_url="https://retell.test",
        api_key="secret-key",
        timeout_seconds=12.5,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    service = RetellPhoneCallService(
        config=_config(tmp_path),
        contacts_config=_contacts(),
        store=RetellPhoneCallStore(tmp_path / "retell-phone-calls-store.json"),
        client=client,
    )
    allowed_session = _session("session-1", allowed_contact_ids=["ops"])

    record = service.make_phone_call(
        session=allowed_session,
        contact="ops",
        prompt="Ask whether the shipment arrived.",
        request_id="phone-allowed",
    )
    assert record.call_id == "call-1"
    assert len(requests) == 1
    assert json.loads(requests[0].content.decode("utf-8"))["to_number"] == "+15550000002"

    with pytest.raises(
        ValueError,
        match=r"Contact 'support' is not allowed for session 'session-1'",
    ):
        service.make_phone_call(
            session=allowed_session,
            contact="support",
            prompt="Ask whether the shipment arrived.",
            request_id="phone-blocked",
        )

    invalid_allowlist_session = _session(
        "session-2",
        allowed_contact_ids=["ops", "missing"],
    )
    with pytest.raises(
        ValueError,
        match=(
            r"contacts allowlist for session 'session-2' and directory 'root' "
            r"references unknown contact ids: missing"
        ),
    ):
        service.make_phone_call(
            session=invalid_allowlist_session,
            contact="ops",
            prompt="Ask whether the shipment arrived.",
            request_id="phone-invalid",
        )

    assert len(requests) == 1
    assert service.store.get_by_request_id("phone-blocked") is None
    assert service.store.get_by_request_id("phone-invalid") is None


def test_retell_webhook_signature_validates_raw_body() -> None:
    raw_body = b'{"event":"call_ended","call":{"call_id":"call-1"}}'
    header = _signature(raw_body, "secret-key", timestamp_ms=1_777_000_000_000)

    verify_retell_webhook_signature(
        raw_body=raw_body,
        signature_header=header,
        api_key="secret-key",
        now_ms=1_777_000_000_000,
    )

    with pytest.raises(RetellWebhookSignatureError, match="digest"):
        verify_retell_webhook_signature(
            raw_body=b'{"call":{"call_id":"call-1"},"event":"call_ended"}',
            signature_header=header,
            api_key="secret-key",
            now_ms=1_777_000_000_000,
        )

    with pytest.raises(RetellWebhookSignatureError, match="stale"):
        verify_retell_webhook_signature(
            raw_body=raw_body,
            signature_header=header,
            api_key="secret-key",
            now_ms=1_777_000_600_001,
        )


def test_retell_webhook_endpoint_updates_existing_call(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("RETELL_API_KEY", "secret-key")
    context = build_runtime(_config(tmp_path))
    context.session_repository.upsert_session(_session("session-1"))
    record, _created = context.retell_phone_call_store.prepare_call(
        session_id="session-1",
        directory_path="root",
        contact_alias="ops",
        contact_display_name="Ops Duty",
        prompt_metadata={"sha256": "prompt-sha", "length": 8},
        request_id="phone-1",
    )
    context.retell_phone_call_store.attach_registered_call(
        record.request_id,
        {"call_id": "call-1", "call_status": "registered"},
    )
    client = TestClient(create_app(context))
    payload = {
        "event": "call_ended",
        "call": {
            "call_id": "call-1",
            "call_status": "ended",
            "to_number": "+15550000002",
            "transcript": "Agent: done",
            "disconnection_reason": "agent_hangup",
        },
    }
    raw_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    response = client.post(
        "/retell/webhook",
        content=raw_body,
        headers={"X-Retell-Signature": _signature(raw_body, "secret-key")},
    )

    assert response.status_code == 204
    updated = context.retell_phone_call_store.get_by_call_id("call-1")
    assert updated is not None
    assert updated.status == "ended"
    assert updated.transcript == "Agent: done"
    assert "+15550000002" not in json.dumps(updated.model_dump(mode="json"))


def test_retell_webhook_endpoint_rejects_invalid_signature(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("RETELL_API_KEY", "secret-key")
    context = build_runtime(_config(tmp_path))
    client = TestClient(create_app(context))
    raw_body = b'{"event":"call_ended","call":{"call_id":"call-1"}}'

    response = client.post(
        "/retell/webhook",
        content=raw_body,
        headers={"X-Retell-Signature": "v=1777000000000,d=bad"},
    )

    assert response.status_code == 401
