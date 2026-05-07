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


def test_openai_balance_uses_resolved_directory_api_key_env(
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
    monkeypatch.setenv("TEAM_A_OPENAI_TOKEN", "team-a-secret")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"object": "credit_summary", "total_available": 4.25},
            request=request,
        )

    service = OpenAIBillingService(
        config,
        provider_config_resolver=ProviderConfigResolver(config, directory_store=directory_store),
        transport=httpx.MockTransport(handler),
    )

    status = service.fetch_status(team.path)

    assert status.status == "ok"
    assert status.balance_usd == 4.25
    assert status.low_balance is True
    assert status.api_key_env == "TEAM_A_OPENAI_TOKEN"
    assert requests[0].url == "https://api.openai.com/dashboard/billing/credit_grants"
    assert requests[0].headers["authorization"] == "Bearer team-a-secret"
    assert "team-a-secret" not in status.model_dump_json()


def test_openai_balance_does_not_call_upstream_when_key_is_missing(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_config(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected request to {request.url}")

    service = OpenAIBillingService(config, transport=httpx.MockTransport(handler))

    status = service.fetch_status("root")

    assert status.status == "not_configured"
    assert status.balance_usd is None
    assert status.low_balance is False
    assert status.api_key_env == "OPENAI_API_KEY"


def test_openai_balance_reports_unsupported_non_openai_base_url(
    tmp_path: Path, monkeypatch
) -> None:
    config = make_config(tmp_path, openai_api_base_url="https://models.example.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")

    service = OpenAIBillingService(config)

    status = service.fetch_status("root")

    assert status.status == "unavailable"
    assert "api.openai.com" in (status.message or "")
