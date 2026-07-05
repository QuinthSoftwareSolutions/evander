from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
from mcp.server.fastmcp.exceptions import ToolError
import pytest

from evander.addons import AddonContext
from evander.config import GatewayConfig
from evander.models import ApprovalRequest, SessionManifest
from evander.policy import PolicyEngine
from evander.repositories.session_manifests import JsonSessionManifestRepository
from evander.storage import JsonStore
from evander_bokio_addon.client import BokioApiError, BokioClient, BokioConfigError, settings_from_context
from evander_bokio_addon.mcp import MCP_TOOL_NAMES, register_mcp_tools
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


def test_bokio_upload_staging_separates_session_and_reserved_scopes(tmp_path) -> None:
    admin_scope = upload_staging_dir(tmp_path, scope="admin")
    workflow_scope = upload_staging_dir(tmp_path, scope="workflows")
    admin_session = upload_staging_dir(tmp_path, session_id="admin")
    workflow_session = upload_staging_dir(tmp_path, session_id="workflows")

    assert admin_scope == tmp_path / "addons" / "bokio-addon" / "uploads" / "scopes" / "admin"
    assert workflow_scope == (
        tmp_path / "addons" / "bokio-addon" / "uploads" / "scopes" / "workflows"
    )
    assert admin_session == (
        tmp_path / "addons" / "bokio-addon" / "uploads" / "sessions" / "admin"
    )
    assert workflow_session == (
        tmp_path / "addons" / "bokio-addon" / "uploads" / "sessions" / "workflows"
    )
    assert len({admin_scope, workflow_scope, admin_session, workflow_session}) == 4


def test_bokio_mcp_read_tool_requires_session_tool_permission(
    tmp_path: Path,
    monkeypatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"companyInformation": {"name": "Example AB"}})

    _install_transport(monkeypatch, handler)
    monkeypatch.setenv("BOKIO_COMPANY_ID", "company-1")
    monkeypatch.setenv("BOKIO_INTEGRATION_TOKEN", "secret-token")

    denied_server = _register_bokio_mcp(tmp_path / "denied", allowed_tools=[])
    with pytest.raises(ToolError, match="bokio_get_company"):
        denied_server.tools["bokio_get_company"](session_id="session-1")

    assert requests == []

    allowed_server = _register_bokio_mcp(
        tmp_path / "allowed",
        allowed_tools=["bokio_get_company"],
    )
    result = allowed_server.tools["bokio_get_company"](session_id="session-1")

    assert result == {"result": {"companyInformation": {"name": "Example AB"}}}
    assert len(requests) == 1
    assert requests[0].headers["authorization"] == "Bearer secret-token"


def test_bokio_mcp_prepare_tool_requires_session_tool_permission(tmp_path: Path) -> None:
    denied_root = tmp_path / "denied"
    denied_server = _register_bokio_mcp(denied_root, allowed_tools=[])

    with pytest.raises(ToolError, match="bokio_prepare_journal_entry"):
        denied_server.tools["bokio_prepare_journal_entry"](
            payload={"title": "Receipt", "items": []},
            session_id="session-1",
        )

    assert PreparedActionStore(denied_root).list_actions() == []

    allowed_root = tmp_path / "allowed"
    allowed_server = _register_bokio_mcp(
        allowed_root,
        allowed_tools=["bokio_prepare_journal_entry"],
    )
    result = allowed_server.tools["bokio_prepare_journal_entry"](
        payload={"title": "Receipt", "items": []},
        session_id="session-1",
    )

    assert result["action"]["kind"] == "journal_entry"
    assert result["action"]["source"] == "session-1"
    assert PreparedActionStore(allowed_root).list_actions()[0]["source"] == "session-1"


def test_bokio_mcp_upload_tool_requires_policy_before_client_access(
    tmp_path: Path,
    monkeypatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"uploadId": "upload-1"})

    _install_transport(monkeypatch, handler)
    monkeypatch.setenv("BOKIO_COMPANY_ID", "company-1")
    monkeypatch.setenv("BOKIO_INTEGRATION_TOKEN", "secret-token")

    denied_root = tmp_path / "denied"
    denied_upload_root = upload_staging_dir(denied_root, session_id="session-1")
    denied_upload_root.mkdir(parents=True)
    (denied_upload_root / "receipt.pdf").write_bytes(b"%PDF-1.7\nreceipt")
    denied_server = _register_bokio_mcp(denied_root, allowed_tools=[])

    with pytest.raises(ToolError, match="bokio_upload_document"):
        denied_server.tools["bokio_upload_document"](
            file_path="receipt.pdf",
            session_id="session-1",
        )

    assert requests == []

    allowed_root = tmp_path / "allowed"
    allowed_upload_root = upload_staging_dir(allowed_root, session_id="session-1")
    allowed_upload_root.mkdir(parents=True)
    (allowed_upload_root / "receipt.pdf").write_bytes(b"%PDF-1.7\nreceipt")
    allowed_server = _register_bokio_mcp(
        allowed_root,
        allowed_tools=["bokio_upload_document"],
    )
    result = allowed_server.tools["bokio_upload_document"](
        file_path="receipt.pdf",
        session_id="session-1",
    )

    assert result == {"result": {"uploadId": "upload-1"}}
    assert len(requests) == 1
    assert requests[0].headers["authorization"] == "Bearer secret-token"


def test_bokio_mcp_approved_write_tool_checks_policy_before_client_access(
    tmp_path: Path,
    monkeypatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "journal-entry-1"})

    _install_transport(monkeypatch, handler)
    monkeypatch.setenv("BOKIO_COMPANY_ID", "company-1")
    monkeypatch.setenv("BOKIO_INTEGRATION_TOKEN", "secret-token")
    store = PreparedActionStore(tmp_path)
    action = store.create_action(
        kind="journal_entry",
        payload={"title": "Receipt", "items": []},
        source="session-1",
    )
    store.approve_action(action["action_id"])

    denied_server = _register_bokio_mcp(tmp_path, allowed_tools=[])
    with pytest.raises(ToolError, match="bokio_create_approved_journal_entry"):
        denied_server.tools["bokio_create_approved_journal_entry"](
            prepared_action_id=action["action_id"],
            session_id="session-1",
        )

    assert requests == []
    assert store.get_action(action["action_id"])["status"] == "approved"

    allowed_server = _register_bokio_mcp(
        tmp_path,
        allowed_tools=["bokio_create_approved_journal_entry"],
    )
    result = allowed_server.tools["bokio_create_approved_journal_entry"](
        prepared_action_id=action["action_id"],
        session_id="session-1",
    )

    assert result["result"] == {"id": "journal-entry-1"}
    assert store.get_action(action["action_id"])["status"] == "executed"
    assert len(requests) == 1
    assert requests[0].headers["authorization"] == "Bearer secret-token"


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


class FakeServer:
    def __init__(self) -> None:
        self.tools = {}

    def tool(self, *args, **kwargs):
        def decorator(func):
            self.tools[kwargs.get("name") or func.__name__] = func
            return func

        if args and callable(args[0]):
            return decorator(args[0])
        return decorator


def _register_bokio_mcp(tmp_path: Path, *, allowed_tools: list[str]) -> FakeServer:
    tmp_path.mkdir(parents=True, exist_ok=True)
    config = GatewayConfig(
        runtime_dir=tmp_path,
        audit_log_path=tmp_path / "audit.jsonl",
        session_store_path=tmp_path / "sessions.json",
        approval_store_path=tmp_path / "approvals.json",
        draft_store_path=tmp_path / "drafts.json",
        email_store_path=tmp_path / "emails.json",
        discord_command_store_path=tmp_path / "discord.json",
    )
    sessions = JsonSessionManifestRepository(config.session_store_path)
    approvals = JsonStore(config.approval_store_path, ApprovalRequest)
    policy = PolicyEngine(
        config,
        sessions,
        approvals,
        extra_mcp_tool_names=list(MCP_TOOL_NAMES),
    )
    sessions.upsert_session(
        SessionManifest(
            session_id="session-1",
            allowed_tools=list(allowed_tools),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            directory_path="root",
        )
    )
    runtime = SimpleNamespace(policy=policy)
    server = FakeServer()
    register_mcp_tools(
        server,
        AddonContext(
            addon_id="bokio-addon",
            runtime_dir=tmp_path,
            addon_runtime_dir=tmp_path / "addons" / "bokio-addon",
            config=config,
            service=runtime,
        ),
    )
    return server
