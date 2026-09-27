from __future__ import annotations

import asyncio
import json
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from starlette.applications import Starlette
from starlette.testclient import TestClient

from evander.addons import AddonContext, AddonLoaderConfig, addon_runtime_dir, load_addons
from evander.config import GatewayConfig
from evander.effects.providers.base import EffectRuntimeContext
from evander.gateway import create_server
from evander.models import ApprovalStatus, SessionManifest
from evander.runtime import build_runtime
from evander.workflow.plugins import load_provider_plugins
from evander_skatteverket_addon.api import admin_api_routes
from evander_skatteverket_addon.catalog import ADDON_ID, catalog_payload
from evander_skatteverket_addon.mcp import (
    CONNECTION_STATUS_TOOL,
    CREATE_DRAFT_TOOL,
    LIST_CATALOG_TOOL,
    MCP_TOOL_NAMES,
    REQUEST_SUBMISSION_APPROVAL_TOOL,
    SUBMIT_APPROVED_DRAFT_TOOL,
    VALIDATE_DRAFT_TOOL,
)
from evander_skatteverket_addon.provider_schemas import (
    SkatteverketRequestSubmissionApprovalEffectDefinition,
    SkatteverketSubmitApprovedDraftEffectDefinition,
)
from evander_skatteverket_addon.providers import (
    SkatteverketRequestSubmissionApprovalEffectProvider,
    SkatteverketSubmitApprovedDraftEffectProvider,
)
from evander_skatteverket_addon.repository import SkatteverketRepository

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _runtime(tmp_path: Path, *, allowed_tools: list[str]):
    runtime_dir = tmp_path / "runtime"
    context = build_runtime(
        GatewayConfig(
            runtime_dir=runtime_dir,
            audit_log_path=runtime_dir / "audit.jsonl",
            session_store_path=runtime_dir / "sessions.json",
            approval_store_path=runtime_dir / "approvals.json",
            draft_store_path=runtime_dir / "drafts.json",
            email_store_path=runtime_dir / "emails.json",
            discord_command_store_path=runtime_dir / "discord.json",
            addon_loader=AddonLoaderConfig(
                modules=["evander_skatteverket_addon"],
                enabled_addon_ids=[ADDON_ID],
            ),
        )
    )
    context.session_repository.upsert_session(
        SessionManifest(
            session_id="session-skv",
            allowed_tools=allowed_tools,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            directory_path="root",
        )
    )
    return context


def _call_tool(server, name: str, arguments: dict[str, object]):
    result = asyncio.run(server._tool_manager.call_tool(name, arguments))
    if isinstance(result, tuple) and len(result) == 2:
        structured = result[1]
        if isinstance(structured, dict) and set(structured) == {"result"}:
            return structured["result"]
        return structured
    return result


def _insert_session(context, session_id: str, *, allowed_tools: list[str]) -> None:
    context.session_repository.upsert_session(
        SessionManifest(
            session_id=session_id,
            allowed_tools=allowed_tools,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            directory_path="root",
        )
    )


def _effect_context(context) -> EffectRuntimeContext:
    return EffectRuntimeContext(
        resolved_config=context.config,
        admin_service=SimpleNamespace(runtime=context),
    )


def test_bundled_skatteverket_addon_packaging_contract_is_declared() -> None:
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    addon_entry_points = pyproject["project"]["entry-points"]["evander.addons"]
    package_data = pyproject["tool"]["setuptools"]["package-data"]

    assert addon_entry_points["skatteverket"] == "evander_skatteverket_addon:register_addon"
    assert package_data["evander_skatteverket_addon"] == ["addon.json", "ui_static/*"]
    assert json.loads(
        (PROJECT_ROOT / "src/evander_skatteverket_addon/addon.json").read_text(encoding="utf-8")
    ) == {"addon_id": "skatteverket-addon", "module": "evander_skatteverket_addon"}
    assert (PROJECT_ROOT / "src/evander_skatteverket_addon/ui_static/index.html").is_file()


def test_skatteverket_admin_api_upserts_connection(tmp_path: Path) -> None:
    context = AddonContext(
        addon_id=ADDON_ID,
        runtime_dir=tmp_path,
        addon_runtime_dir=addon_runtime_dir(tmp_path, ADDON_ID),
    )
    client = TestClient(Starlette(routes=admin_api_routes(context)))

    response = client.post(
        "/connections",
        json={
            "connection_id": "default",
            "environment": "komplett_testtjanst",
            "display_name": "Skatteverket test",
            "base_url": "https://api.example.test",
            "token_url": "https://login.example.test/token",
            "client_id_env": "SKV_CLIENT_ID",
            "client_secret_env": "SKV_CLIENT_SECRET",
            "apigw_client_id_env": "SKV_APIGW_CLIENT_ID",
            "apigw_client_secret_env": "SKV_APIGW_CLIENT_SECRET",
            "api_key_env": "SKV_API_KEY",
            "certificate_subject": "CN=test",
            "redirect_uri": "https://admin.example.test/skv/callback",
            "submission_endpoint": "https://api.example.test/submit",
            "enabled_api_names": ["momsdeklaration", "skattekonto"],
            "enabled": True,
        },
    )

    assert response.status_code == 201
    connection = response.json()["connection"]
    assert connection["connection_id"] == "default"
    assert connection["enabled_api_names"] == ["momsdeklaration", "skattekonto"]

    status = client.get("/connection-status").json()
    assert status["configured"] is True
    assert status["ready"] is False
    assert status["missing_secret_refs"] == [
        "SKV_CLIENT_ID",
        "SKV_CLIENT_SECRET",
        "SKV_APIGW_CLIENT_ID",
        "SKV_APIGW_CLIENT_SECRET",
        "SKV_API_KEY",
    ]


def test_skatteverket_addon_loads_admin_and_workflow_surfaces() -> None:
    catalog = load_addons(
        AddonLoaderConfig(
            modules=["evander_skatteverket_addon"],
            enabled_addon_ids=[ADDON_ID],
        )
    )

    assert catalog.registrations[0].addon_id == ADDON_ID
    assert catalog.registrations[0].mcp_tools is not None

    providers = load_provider_plugins(
        module_names=[],
        include_entry_points=False,
        include_builtin=False,
        addon_loader=AddonLoaderConfig(
            modules=["evander_skatteverket_addon"],
            enabled_addon_ids=[ADDON_ID],
        ),
    )
    assert "addon:skatteverket-addon" in providers.loaded_plugins
    assert "query:skatteverket:catalog" in providers.query_providers
    assert "effect:skatteverket:submit-approved-draft" in providers.effect_providers
    assert "effect:skatteverket:submit-approved-draft" in providers.node_definition_resolvers
    assert any(
        entry.type == "effect:skatteverket:create-draft"
        for entry in providers.workflow_editor_catalog_entries
    )


def test_skatteverket_repository_tracks_draft_validation_and_submission(tmp_path: Path) -> None:
    repo = SkatteverketRepository(tmp_path)

    draft = repo.create_draft(
        api_name="momsdeklaration",
        period="2026-05",
        subject_id="165560000000",
        payload={"momsAttBetala": 123},
        source_session_id="session-skv",
    )
    validated = repo.validate_draft(draft["draft_id"])
    snapshot = repo.get_draft(draft["draft_id"])

    assert validated["status"] == "validated"
    assert snapshot.approval_payload()["draft_revision"] == validated["revision"]

    submission = repo.record_submission(
        draft_id=draft["draft_id"],
        approval_request_id="apr-1",
        response={"status": "submitted", "reference": "ref-1"},
        idempotency_key="idem-1",
    )

    assert submission["status"] == "submitted"
    assert repo.get_draft_payload(draft["draft_id"])["status"] == "submitted"


def test_gateway_registers_and_filters_skatteverket_mcp_tools(tmp_path: Path) -> None:
    context = _runtime(tmp_path, allowed_tools=[LIST_CATALOG_TOOL])
    server = create_server(context)

    registered = {tool.name for tool in server._tool_manager.list_tools()}
    session = context.policy.get_session("session-skv")
    visible = {item.tool for item in context.policy.visible_tools_for_session(session)}

    assert set(MCP_TOOL_NAMES).issubset(registered)
    assert LIST_CATALOG_TOOL in visible
    assert CREATE_DRAFT_TOOL not in visible

    result = _call_tool(
        server,
        LIST_CATALOG_TOOL,
        {"session_id": "session-skv"},
    )
    assert result["addon_id"] == ADDON_ID
    assert result["submission_api_names"] == catalog_payload()["submission_api_names"]


def test_skatteverket_submit_tool_requires_exact_draft_approval(tmp_path: Path) -> None:
    context = _runtime(
        tmp_path,
        allowed_tools=[
            CONNECTION_STATUS_TOOL,
            CREATE_DRAFT_TOOL,
            VALIDATE_DRAFT_TOOL,
            REQUEST_SUBMISSION_APPROVAL_TOOL,
            SUBMIT_APPROVED_DRAFT_TOOL,
        ],
    )
    server = create_server(context)

    draft = _call_tool(
        server,
        CREATE_DRAFT_TOOL,
        {
            "session_id": "session-skv",
            "api_name": "momsdeklaration",
            "period": "2026-05",
            "subject_id": "165560000000",
            "payload": {"momsAttBetala": 123},
        },
    )
    validated = _call_tool(
        server,
        VALIDATE_DRAFT_TOOL,
        {"session_id": "session-skv", "draft_id": draft["draft_id"]},
    )
    approval_result = _call_tool(
        server,
        REQUEST_SUBMISSION_APPROVAL_TOOL,
        {"session_id": "session-skv", "draft_id": validated["draft_id"]},
    )
    approval = context.approvals.get(approval_result["approval_request_id"])
    assert approval is not None
    approval.status = ApprovalStatus.APPROVED
    context.approvals.put(approval.request_id, approval)

    submitted = _call_tool(
        server,
        SUBMIT_APPROVED_DRAFT_TOOL,
        {
            "session_id": "session-skv",
            "draft_id": validated["draft_id"],
            "submission_approval_request_id": approval.request_id,
        },
    )

    assert submitted["status"] == "blocked"
    assert submitted["submission"]["approval_request_id"] == approval.request_id


def test_skatteverket_submit_tool_rejects_other_session_approval(tmp_path: Path) -> None:
    context = _runtime(
        tmp_path,
        allowed_tools=[
            CREATE_DRAFT_TOOL,
            VALIDATE_DRAFT_TOOL,
            REQUEST_SUBMISSION_APPROVAL_TOOL,
            SUBMIT_APPROVED_DRAFT_TOOL,
        ],
    )
    _insert_session(context, "session-other", allowed_tools=[SUBMIT_APPROVED_DRAFT_TOOL])
    server = create_server(context)

    draft = _call_tool(
        server,
        CREATE_DRAFT_TOOL,
        {
            "session_id": "session-skv",
            "api_name": "momsdeklaration",
            "period": "2026-05",
            "subject_id": "165560000000",
            "payload": {"momsAttBetala": 123},
        },
    )
    validated = _call_tool(
        server,
        VALIDATE_DRAFT_TOOL,
        {"session_id": "session-skv", "draft_id": draft["draft_id"]},
    )
    approval_result = _call_tool(
        server,
        REQUEST_SUBMISSION_APPROVAL_TOOL,
        {"session_id": "session-skv", "draft_id": validated["draft_id"]},
    )
    approval = context.approvals.get(approval_result["approval_request_id"])
    assert approval is not None
    approval.status = ApprovalStatus.APPROVED
    context.approvals.put(approval.request_id, approval)

    with pytest.raises(ToolError, match="belongs to session session-skv, not session-other"):
        _call_tool(
            server,
            SUBMIT_APPROVED_DRAFT_TOOL,
            {
                "session_id": "session-other",
                "draft_id": validated["draft_id"],
                "submission_approval_request_id": approval.request_id,
            },
        )

    assert context.approvals.get(approval.request_id).status == ApprovalStatus.APPROVED


def test_skatteverket_workflow_approval_nodes_use_admin_runtime_policy(
    tmp_path: Path,
) -> None:
    context = _runtime(tmp_path, allowed_tools=[])
    repo = SkatteverketRepository(context.config.runtime_dir)
    draft = repo.create_draft(
        api_name="momsdeklaration",
        period="2026-05",
        subject_id="165560000000",
        payload={"momsAttBetala": 123},
        source_session_id="session-skv",
    )
    validated = repo.validate_draft(draft["draft_id"])

    request_provider = SkatteverketRequestSubmissionApprovalEffectProvider()
    request_provider.initialize(
        SkatteverketRequestSubmissionApprovalEffectDefinition(
            draft_id=validated["draft_id"],
            session_id="session-skv",
        ),
        _effect_context(context),
    )

    approval_result = request_provider.execute()

    assert approval_result["status"] == "pending_approval"
    approval = context.approvals.get(approval_result["approval_request_id"])
    assert approval is not None
    assert approval.session_id == "session-skv"
    approval.status = ApprovalStatus.APPROVED
    context.approvals.put(approval.request_id, approval)

    submit_provider = SkatteverketSubmitApprovedDraftEffectProvider()
    submit_provider.initialize(
        SkatteverketSubmitApprovedDraftEffectDefinition(
            draft_id=validated["draft_id"],
            approval_request_id=approval.request_id,
        ),
        _effect_context(context),
    )

    submitted = submit_provider.execute()

    assert submitted["status"] == "blocked"
    assert submitted["submission"]["approval_request_id"] == approval.request_id
