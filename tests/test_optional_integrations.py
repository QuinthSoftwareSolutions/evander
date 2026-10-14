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
