from __future__ import annotations

import json

import httpx
import pytest

from evander_bokio_addon.client import BokioApiError, BokioClient, settings_from_context
from evander_bokio_addon.state import PreparedActionStore


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
