from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from agent_sandbox.config import GatewayConfig
from evander_bokio_addon.client import BokioApiError, BokioClient, BokioConfigError, settings_from_context
from evander_bokio_addon.providers import BokioCompanyQueryProvider
from evander_bokio_addon.state import PreparedActionStore, upload_staging_dir


def test_bokio_client_uses_private_integration_bearer_token(monkeypatch) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"companyInformation": {"name": "Example AB"}},
        )

    _install_transport(monkeypatch, handler)
    monkeypatch.setenv("BOKIO_COMPANY_ID", "company-1")
    monkeypatch.setenv("BOKIO_INTEGRATION_TOKEN", "secret-token")

    result = BokioClient(settings_from_context()).get_company()

    assert result == {"companyInformation": {"name": "Example AB"}}
    assert str(requests[0].url) == "https://api.bokio.se/v1/companies/company-1/company-information"
    assert requests[0].headers["authorization"] == "Bearer secret-token"


def test_bokio_client_normalizes_api_errors(monkeypatch) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            headers={"Bokio-Plan-Warning": "plan warning"},
            json={
                "error": "price_plan_feature_required",
                "message": "This feature requires Integrations (API) or a Plus plan",
            },
        )

    _install_transport(monkeypatch, handler)
    monkeypatch.setenv("BOKIO_COMPANY_ID", "company-1")
    monkeypatch.setenv("BOKIO_INTEGRATION_TOKEN", "secret-token")

    with pytest.raises(BokioApiError) as exc_info:
        BokioClient(settings_from_context()).list_accounts()

    payload = exc_info.value.to_payload()
    assert payload["status_code"] == 403
    assert payload["plan_warning"] == "plan warning"
    assert payload["payload"]["error"] == "price_plan_feature_required"


def test_bokio_upload_document_requires_staged_pdf_jpeg_or_png(
    tmp_path,
    monkeypatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"uploadId": "upload-1"})

    _install_transport(monkeypatch, handler)
    monkeypatch.setenv("BOKIO_COMPANY_ID", "company-1")
    monkeypatch.setenv("BOKIO_INTEGRATION_TOKEN", "secret-token")
    upload_root = upload_staging_dir(tmp_path, session_id="session-1")
    upload_root.mkdir(parents=True)
    receipt = upload_root / "receipt.pdf"
    receipt.write_bytes(b"%PDF-1.7\nreceipt")
    outside = tmp_path / "secrets.env"
    outside.write_text("SECRET=value", encoding="utf-8")
    text_file = upload_root / "receipt.txt"
    text_file.write_text("not a receipt image", encoding="utf-8")

    result = BokioClient(settings_from_context()).upload_document(
        file_path="receipt.pdf",
        allowed_roots=[upload_root],
    )

    assert result == {"uploadId": "upload-1"}
    assert str(requests[0].url) == "https://api.bokio.se/v1/companies/company-1/uploads"
    assert requests[0].headers["authorization"] == "Bearer secret-token"
    with pytest.raises(PermissionError, match="allowed upload staging"):
        BokioClient(settings_from_context()).upload_document(
            file_path=outside,
            allowed_roots=[upload_root],
        )
    with pytest.raises(ValueError, match="PDF, JPEG, or PNG"):
        BokioClient(settings_from_context()).upload_document(
            file_path="receipt.txt",
            allowed_roots=[upload_root],
        )
    with pytest.raises(BokioConfigError, match="allowed upload directory"):
        BokioClient(settings_from_context()).upload_document(
            file_path="receipt.pdf",
            allowed_roots=None,
        )


def test_bokio_workflow_provider_uses_full_gateway_provider_state(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("BOKIO_CUSTOM_COMPANY", "company-from-custom-env")
    monkeypatch.setenv("BOKIO_CUSTOM_TOKEN", "token-from-custom-env")
    config = GatewayConfig(
        runtime_dir=tmp_path,
        provider_state={
            "bokio": {
                "company_id_env": "BOKIO_CUSTOM_COMPANY",
                "integration_token_env": "BOKIO_CUSTOM_TOKEN",
            }
        },
    )

    class Resolver:
        directory_path: str | None = None

        def resolve_for_directory(self, directory_path: str | None) -> GatewayConfig:
            self.directory_path = directory_path
            return config

    resolver = Resolver()
    provider = BokioCompanyQueryProvider()
    provider.runtime_context = SimpleNamespace(
        resolved_config=SimpleNamespace(runtime_dir=tmp_path),
        provider_config_resolver=resolver,
        metadata={"directory_path": "root/books"},
    )

    settings = settings_from_context(provider._context())

    assert resolver.directory_path == "root/books"
    assert settings.company_id == "company-from-custom-env"
    assert settings.integration_token == "token-from-custom-env"
    assert settings.company_id_env == "BOKIO_CUSTOM_COMPANY"
    assert settings.token_env == "BOKIO_CUSTOM_TOKEN"


def test_prepared_action_store_transitions_without_secrets(tmp_path) -> None:
    store = PreparedActionStore(tmp_path)

    action = store.create_action(
        kind="journal_entry",
        payload={"title": "Receipt", "items": []},
        source="session-1",
        note="needs review",
    )
    approved = store.approve_action(action["action_id"])
    executed = store.mark_executed(action["action_id"], result={"id": "entry-1"})

    assert approved["status"] == "approved"
    assert executed["status"] == "executed"
    assert executed["result"] == {"id": "entry-1"}
    state_text = (
        tmp_path / "addons" / "bokio-addon" / "prepared-actions.json"
    ).read_text(encoding="utf-8")
    assert "secret-token" not in state_text
    assert json.loads(state_text)["actions"][action["action_id"]]["payload"]["title"] == "Receipt"


def _install_transport(monkeypatch, handler) -> None:
    real_client = httpx.Client
    transport = httpx.MockTransport(handler)

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)
