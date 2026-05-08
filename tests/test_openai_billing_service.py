from __future__ import annotations

from pathlib import Path

import httpx

from agent_sandbox.admin.services.openai_billing_service import OpenAIBillingService
from agent_sandbox.config import GatewayConfig
from agent_sandbox.directories import DirectoryService, DirectoryStore
from agent_sandbox.directory_config import DirectoryConfigStore
from agent_sandbox.provider_config import ProviderConfigResolver


def make_config(tmp_path: Path, **overrides: object) -> GatewayConfig:
    runtime_dir = tmp_path / "runtime"
    payload = {
        "runtime_dir": runtime_dir,
        "audit_log_path": runtime_dir / "audit.jsonl",
        "session_store_path": runtime_dir / "sessions.json",
        "approval_store_path": runtime_dir / "approvals.json",
        "draft_store_path": runtime_dir / "drafts.json",
        "email_store_path": runtime_dir / "emails.json",
        "directories_store_path": runtime_dir / "directories.json",
        **overrides,
    }
    return GatewayConfig.model_validate(payload)


def test_openai_costs_use_resolved_directory_api_key_env(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_config(tmp_path, enable_directory_scoped_config=True)
    directory_store = DirectoryStore(config.directories_store_path)
    directory_store.ensure_root()
    team = DirectoryService(directory_store).create(parent="root", name="team-a")
    DirectoryConfigStore(directory_store, runtime_dir=config.runtime_dir).write(
        team.path,
        domain="providers",
        payload={"openai_api_key_env": "TEAM_A_OPENAI_TOKEN"},
    )
    monkeypatch.delenv("OPENAI_ADMIN_API_KEY", raising=False)
    monkeypatch.setenv("TEAM_A_OPENAI_TOKEN", "team-a-secret")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "object": "page",
                "data": [
                    {
                        "object": "bucket",
                        "results": [
                            {
                                "object": "organization.costs.result",
                                "amount": {"value": 3.25, "currency": "usd"},
                            },
                            {
                                "object": "organization.costs.result",
                                "amount": {"value": 1.0, "currency": "usd"},
                            },
                        ],
                    },
                ],
            },
            request=request,
        )

    service = OpenAIBillingService(
        config,
        provider_config_resolver=ProviderConfigResolver(config, directory_store=directory_store),
        transport=httpx.MockTransport(handler),
    )

    status = service.fetch_status(team.path)

    assert status.status == "ok"
    assert status.cost_usd == 4.25
    assert status.period_days == 30
    assert status.source == "organization_costs"
    assert status.api_key_env == "TEAM_A_OPENAI_TOKEN"
    assert requests[0].url.path == "/v1/organization/costs"
    assert requests[0].url.params["bucket_width"] == "1d"
    assert requests[0].url.params["limit"] == "31"
    assert requests[0].headers["authorization"] == "Bearer team-a-secret"
    assert "team-a-secret" not in status.model_dump_json()


def test_openai_costs_prefer_dedicated_admin_key_env(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_config(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "relay-project-secret")
    monkeypatch.setenv("OPENAI_ADMIN_API_KEY", "costs-admin-secret")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "object": "page",
                "data": [
                    {
                        "object": "bucket",
                        "results": [
                            {
                                "object": "organization.costs.result",
                                "amount": {"value": 2.0, "currency": "usd"},
                            }
                        ],
                    },
                ],
            },
            request=request,
        )

    service = OpenAIBillingService(config, transport=httpx.MockTransport(handler))

    status = service.fetch_status("root")

    assert status.status == "ok"
    assert status.api_key_env == "OPENAI_ADMIN_API_KEY"
    assert requests[0].headers["authorization"] == "Bearer costs-admin-secret"
    assert "relay-project-secret" not in status.model_dump_json()
    assert "costs-admin-secret" not in status.model_dump_json()


def test_openai_costs_accept_string_amount_values(tmp_path: Path, monkeypatch) -> None:
    config = make_config(tmp_path)
    monkeypatch.setenv("OPENAI_ADMIN_API_KEY", "costs-admin-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "object": "page",
                "data": [
                    {
                        "object": "bucket",
                        "results": [
                            {
                                "object": "organization.costs.result",
                                "amount": {"value": "2.25", "currency": "usd"},
                            },
                            {
                                "object": "organization.costs.result",
                                "amount": {"value": "0.75", "currency": "usd"},
                            },
                        ],
                    },
                ],
            },
            request=request,
        )

    service = OpenAIBillingService(config, transport=httpx.MockTransport(handler))

    status = service.fetch_status("root")

    assert status.status == "ok"
    assert status.cost_usd == 3.0
    assert status.currency == "usd"


def test_openai_costs_do_not_call_upstream_when_key_is_missing(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_config(tmp_path)
    monkeypatch.delenv("OPENAI_ADMIN_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected request to {request.url}")

    service = OpenAIBillingService(config, transport=httpx.MockTransport(handler))

    status = service.fetch_status("root")

    assert status.status == "not_configured"
    assert status.cost_usd is None
    assert status.api_key_env == "OPENAI_ADMIN_API_KEY"
    assert "OpenAI Admin API key" in (status.message or "")


def test_openai_costs_report_unsupported_non_openai_base_url(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_config(tmp_path, openai_api_base_url="https://models.example.test/v1")
    monkeypatch.delenv("OPENAI_ADMIN_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")

    service = OpenAIBillingService(config)

    status = service.fetch_status("root")

    assert status.status == "unavailable"
    assert "api.openai.com" in (status.message or "")


def test_openai_costs_report_missing_usage_scope(tmp_path: Path, monkeypatch) -> None:
    config = make_config(tmp_path)
    monkeypatch.delenv("OPENAI_ADMIN_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-test-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "error": (
                    "You have insufficient permissions for this operation. "
                    "Missing scopes: api.usage.read."
                )
            },
            request=request,
        )

    service = OpenAIBillingService(config, transport=httpx.MockTransport(handler))

    status = service.fetch_status("root")

    assert status.status == "unavailable"
    assert "project key" in (status.message or "")
    assert "OPENAI_ADMIN_API_KEY" in (status.message or "")
