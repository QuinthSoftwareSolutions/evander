from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from evander.config import GatewayConfig
from evander.directories import DirectoryService
from evander.directory_config import DirectoryConfigStore
from evander.gateway import _MCP_REQUEST_CONTEXT, create_server
from evander.models import EmailMailboxGrant, SessionManifest, ToolName
from evander.runtime import RuntimeContext, build_runtime
from evander.session_auth import ensure_session_mcp_auth_token


@pytest.fixture
def context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runtime = build_runtime(GatewayConfig(runtime_dir=tmp_path))
    runtime.directories.ensure_root()

    def no_provider(*args, **kwargs):
        pytest.fail("Mailbox discovery must not construct or call an email provider")

    monkeypatch.setattr(RuntimeContext, "email_service_for_session", no_provider)
    write_mailboxes(runtime, "root", ["sales", "accounting", "empty"])
    return runtime


def write_mailboxes(context, directory: str, mailbox_ids: list[str], *, domain="example.com"):
    DirectoryConfigStore(context.directories, runtime_dir=context.config.runtime_dir).write(
        directory,
        domain="email",
        payload={
            "email_mailboxes": [
                {
                    "mailbox_id": mailbox_id,
                    "provider": "graph",
                    "account_identifier": f"{mailbox_id}@{domain}",
                    "auth_secret_refs": {"access_token": "UNSET_HOST_ONLY_TOKEN"},
                    "folder_mapping": {"inbox": "private-provider-folder-id"},
                }
                for mailbox_id in mailbox_ids
            ]
        },
    )


def add_session(context, **overrides):
    fields = {
        "session_id": "leo",
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
        "allowed_tools": [ToolName.EMAIL_CREATE_DRAFT],
        "email_mailbox_grants": [
            EmailMailboxGrant(mailbox_id="sales", access="draft", folders=["drafts", "inbox"])
        ],
    }
    fields.update(overrides)
    session = SessionManifest(**fields)
    context.session_repository.upsert_session(session)
    return session


def discover(server, session_id="leo"):
    result = asyncio.run(server.call_tool("email_list_mailboxes", {"session_id": session_id}))
    return result.structured_content


def test_existing_draft_session_discovers_only_granted_public_metadata(context):
    session = add_session(context)
    visibility = context.policy.tool_visibility_for_session(session, ToolName.EMAIL_LIST_MAILBOXES)
    assert visibility.visible
    assert visibility.source == "session.allowed_tools.email"
    assert ToolName.EMAIL_LIST_MAILBOXES not in session.allowed_tools

    result = discover(create_server(context))
    assert result == {
        "mailboxes": [
            {
                "mailbox_id": "sales",
                "provider": "graph",
                "account_identifier": "sales@example.com",
                "access": "draft",
                "folders": ["drafts", "inbox"],
            }
        ]
    }
    serialized = json.dumps(result)
    assert "accounting" not in serialized
    assert "UNSET_HOST_ONLY_TOKEN" not in serialized
    assert "private-provider-folder-id" not in serialized
    audit = json.loads(context.config.audit_log_path.read_text().splitlines()[-1])
    assert audit["tool_name"] == "email_list_mailboxes"
    assert audit["decision"] == "allowed"
    assert audit["result"] == {"count": 1}


def test_read_grant_and_explicit_empty_folders_are_reported_without_defaults(context):
    add_session(
        context,
        email_mailbox_grants=[
            EmailMailboxGrant(mailbox_id="sales", access="read", folders=["inbox"]),
            EmailMailboxGrant(mailbox_id="empty", access="read", folders=[]),
            EmailMailboxGrant(mailbox_id="removed", access="read", folders=["inbox"]),
        ],
    )
    rows = discover(create_server(context))["mailboxes"]
    assert [(row["mailbox_id"], row["access"], row["folders"]) for row in rows] == [
        ("empty", "read", []),
        ("sales", "read", ["inbox"]),
    ]


def test_empty_grants_override_legacy_and_directory_allowlists(context):
    add_session(context, email_mailbox_grants=[], email_mailbox_allowlist=["sales"])
    assert discover(create_server(context)) == {"mailboxes": []}


def test_legacy_discovery_matches_existing_mailbox_folder_and_send_checks(context):
    session = add_session(
        context,
        email_mailbox_grants=None,
        email_mailbox_allowlist=["sales"],
        email_folder_allowlist_by_mailbox={"sales": ["drafts"]},
        email_folder_allowlist=["inbox"],
        email_send_enabled=True,
    )
    row = discover(create_server(context))["mailboxes"][0]
    assert row["mailbox_id"] == "sales"
    assert row["access"] == "send"
    assert row["folders"] == ["drafts", "inbox"]
    for folder in row["folders"]:
        context.policy.require_email_folder_for_mailbox(session, row["mailbox_id"], folder)
    context.policy.require_email_send_enabled(session, row["mailbox_id"])


def test_discovery_uses_session_directory_and_refreshes_revoked_grants(context):
    DirectoryService(context.directories).create(parent="root", name="sales-team")
    write_mailboxes(context, "root/sales-team", ["sales"], domain="team.example.com")
    add_session(context, directory_path="root/sales-team")
    server = create_server(context)
    assert discover(server)["mailboxes"][0]["account_identifier"] == "sales@team.example.com"

    write_mailboxes(context, "root/sales-team", ["replacement"])
    assert discover(server) == {"mailboxes": []}
    write_mailboxes(context, "root/sales-team", ["sales"])
    add_session(context, directory_path="root/sales-team", email_mailbox_grants=[])
    assert discover(server) == {"mailboxes": []}


def test_discovery_requires_tool_access_even_with_mailbox_grant(context):
    add_session(context, allowed_tools=[])
    with pytest.raises(ToolError, match="not allowed"):
        discover(create_server(context))


def test_discovery_can_be_granted_explicitly_without_message_tools(context):
    add_session(context, allowed_tools=[ToolName.EMAIL_LIST_MAILBOXES])
    assert discover(create_server(context))["mailboxes"][0]["mailbox_id"] == "sales"


def test_directory_denial_overrides_inherited_discovery(context):
    session = add_session(context)
    DirectoryConfigStore(context.directories, runtime_dir=context.config.runtime_dir).write(
        "root",
        domain="policy",
        payload={"mcp_tool_policies": {"email_list_mailboxes": {"denied": True}}},
    )
    assert not context.policy.tool_visibility_for_session(
        session, ToolName.EMAIL_LIST_MAILBOXES
    ).visible
    with pytest.raises(ToolError, match="denied by directory"):
        discover(create_server(context))


@pytest.mark.parametrize("session_id", ["unknown", "expired"])
def test_discovery_rejects_unknown_and_expired_sessions(context, session_id):
    add_session(context, session_id="expired", expires_at=datetime.now(UTC) - timedelta(hours=1))
    with pytest.raises(ToolError, match="Unknown session|Expired session"):
        discover(create_server(context), session_id)


def test_discovery_never_inherits_message_listing_permission(context):
    session = add_session(context, allowed_tools=[ToolName.EMAIL_LIST_UNREAD])
    assert context.policy.tool_visibility_for_session(
        session, ToolName.EMAIL_LIST_MAILBOXES
    ).visible
    assert not context.policy.tool_visibility_for_session(
        session, ToolName.EMAIL_LIST_MESSAGES
    ).visible


def test_discovery_honors_directory_approval_requirement(context):
    session = add_session(context)
    DirectoryConfigStore(context.directories, runtime_dir=context.config.runtime_dir).write(
        "root",
        domain="policy",
        payload={"mcp_tool_policies": {"email_list_mailboxes": {"require_approval": True}}},
    )
    assert context.policy.tool_visibility_for_session(
        session, ToolName.EMAIL_LIST_MAILBOXES
    ).requires_approval
    with pytest.raises(ToolError, match="requires approval"):
        discover(create_server(context))


def test_authenticated_tool_inventory_includes_discovery_and_tracks_tool_revocation(context):
    session, proof = ensure_session_mcp_auth_token(add_session(context))
    context.session_repository.upsert_session(session)
    server = create_server(context)
    token = _MCP_REQUEST_CONTEXT.set(
        SimpleNamespace(
            headers={"x-evander-mcp-session-token": proof},
            query_params={"session_id": session.session_id},
        )
    )
    try:
        visible = {tool.name for tool in server._tool_manager.list_tools()}
        assert "email_list_mailboxes" in visible
        assert "email_list_messages" not in visible
        context.session_repository.upsert_session(session.model_copy(update={"allowed_tools": []}))
        assert "email_list_mailboxes" not in {
            tool.name for tool in server._tool_manager.list_tools()
        }
    finally:
        _MCP_REQUEST_CONTEXT.reset(token)


@pytest.mark.parametrize("backend", ["libvirt_vm", "container_worker_vm"])
def test_new_email_sessions_include_discovery_without_widening_read_grant(context, backend):
    from evander.admin.services.session_helpers import build_issue_session_manifest
    from evander.effects.providers.agent_session import build_container_session_manifest

    fields = {
        "session_id": "new-session",
        "directory_path": "root",
        "ttl_hours": 1,
        "discord_channels": [],
        "email_mailboxes": [],
        "email_folders": [],
        "email_mailbox_grants": [
            EmailMailboxGrant(mailbox_id="sales", access="read", folders=["inbox"])
        ],
    }
    if backend == "libvirt_vm":
        session = build_issue_session_manifest(
            **fields,
            policy=context.policy_config.resolve_for_directory("root"),
            vm_id="vm-new",
            issue_number=None,
        )
    else:
        session = build_container_session_manifest(
            **fields,
            config=context.config,
            model=None,
            allowed_tools=None,
            tool_argument_policies=None,
            allowed_script_names=None,
            allowed_contact_ids=None,
            metadata={},
        )
    assert ToolName.EMAIL_LIST_MAILBOXES in session.allowed_tools
    assert ToolName.EMAIL_CREATE_DRAFT not in session.allowed_tools
    assert ToolName.EMAIL_LIST_MESSAGES not in session.allowed_tools
