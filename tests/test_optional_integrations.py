"""The core distribution can operate while a configured integration is unavailable."""

from datetime import UTC, datetime, timedelta
from importlib import util
from types import SimpleNamespace

import pytest

from evander.config import GatewayConfig, MailboxConfig
from evander.models import SessionManifest
from evander.optional_integrations import OptionalIntegrationUnavailable
from evander.runtime import build_runtime


def hide_teams_graph(monkeypatch):
    find_spec = util.find_spec
    monkeypatch.setattr(
        util,
        "find_spec",
        lambda name, *args: None if name == "evander_teams_graph_addon" else find_spec(name, *args),
    )


def test_missing_integration_keeps_runtime_and_persisted_sessions_available(tmp_path, monkeypatch):
    hide_teams_graph(monkeypatch)
    config = GatewayConfig(
        runtime_dir=tmp_path,
        teams_backend="graph_api",
        email_mailboxes=[
            MailboxConfig(mailbox_id="graph", provider="graph"),
            MailboxConfig(mailbox_id="local", provider="json_store"),
        ],
    )
    runtime = build_runtime(config)
    session = SessionManifest(
        session_id="existing-session",
        allowed_tools=[],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    runtime.session_repository.upsert_session(session)
    restored = build_runtime(config)
    assert restored.session_repository.get_session("existing-session") == session
    assert restored.email_service.provider.list_unread("local", "inbox", 10) == []
    with pytest.raises(OptionalIntegrationUnavailable, match="evander-teams-graph-addon"):
        restored.teams_service.get_messages()
    with pytest.raises(OptionalIntegrationUnavailable, match="evander-teams-graph-addon"):
        restored.email_service.provider.list_unread("graph", "inbox", 10)


def test_explicit_disable_suppresses_legacy_tools_even_when_package_is_discoverable(monkeypatch):
    from evander.gateway.communication_teams_tools import create_teams_tools

    monkeypatch.setattr(util, "find_spec", lambda name: SimpleNamespace())
    config = GatewayConfig(addon_loader={"enabled_addon_ids": []})
    assert create_teams_tools(SimpleNamespace(config=config), None) == {}


def test_missing_teams_auth_route_returns_unavailable_after_authentication(tmp_path, monkeypatch):
    from starlette.applications import Starlette
    from starlette.testclient import TestClient
    from evander.admin.web.teams_auth import teams_auth_routes

    hide_teams_graph(monkeypatch)
    authenticated = []
    auth = SimpleNamespace(require=lambda request: authenticated.append(request.url.path))
    service = SimpleNamespace(config=GatewayConfig(runtime_dir=tmp_path))
    with TestClient(Starlette(routes=teams_auth_routes(service, auth))) as client:
        response = client.post("/api/teams-auth/login")
    assert response.status_code == 503
    assert "evander-teams-graph-addon" in response.text
    assert authenticated == ["/api/teams-auth/login"]


@pytest.mark.parametrize("mode", ["missing", "disabled"])
def test_ado_optional_boundary_keeps_core_usable_and_rejects_only_ado_operations(tmp_path, monkeypatch, mode):
    from evander.issues.service import IssueService
    from evander.provider_config import ProviderConfigResolver
    from evander.gateway.ado_tools import register_ado_tools
    from evander.runtime import _build_pr_publish_service
    from evander.events.provider_models import PullRequestFeedbackQueryDefinition
    from evander.queries.providers.base import QueryRuntimeContext
    from evander.queries.providers.pull_request_feedback import PullRequestFeedbackQueryProvider

    original = util.find_spec
    if mode == "missing":
        monkeypatch.setattr(util, "find_spec", lambda name, *args: None if name == "evander_azure_devops_addon" else original(name, *args))
    config = GatewayConfig(runtime_dir=tmp_path, addon_loader={"enabled_addon_ids": []} if mode == "disabled" else {})
    monkeypatch.setenv(config.azure_devops_pr_pat_env, "legacy-test-token")
    runtime = build_runtime(config)
    assert runtime.config.runtime_dir == tmp_path
    assert "azure_devops" not in _build_pr_publish_service(config).providers
    # A valid old environment token must not bypass the add-on's missing/disabled state.
    with pytest.raises(OptionalIntegrationUnavailable, match="evander-azure-devops-addon"):
        IssueService(ProviderConfigResolver(config)).read_remote_issue(provider="azure_devops", remote_issue_id="42", directory_path="root")
    feedback = PullRequestFeedbackQueryProvider()
    feedback.initialize(
        PullRequestFeedbackQueryDefinition(
            id="feedback", display_name="Feedback", type="query:pull-request:feedback",
            provider="ado", repo="repository", pr_id="17",
        ),
        QueryRuntimeContext(provider_config_resolver=ProviderConfigResolver(config)),
    )
    with pytest.raises(OptionalIntegrationUnavailable, match="evander-azure-devops-addon"):
        feedback.execute()
    register_ado_tools(object(), SimpleNamespace(config=config), None)


def test_missing_ado_connection_route_is_authenticated_and_actionable(tmp_path, monkeypatch):
    from starlette.applications import Starlette
    from starlette.testclient import TestClient
    from evander.admin.web.ado_connections import ado_connection_routes

    original = util.find_spec
    monkeypatch.setattr(util, "find_spec", lambda name, *args: None if name == "evander_azure_devops_addon" else original(name, *args))
    authenticated = []
    auth = SimpleNamespace(require=lambda request: authenticated.append(request.url.path))
    with TestClient(Starlette(routes=ado_connection_routes(SimpleNamespace(config=GatewayConfig(runtime_dir=tmp_path)), auth, None))) as client:
        result = client.get("/api/admin/me/ado-connections")
    assert result.status_code == 503
    assert "evander-azure-devops-addon" in result.text
    assert authenticated == ["/api/admin/me/ado-connections"]
