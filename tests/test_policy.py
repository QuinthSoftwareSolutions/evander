from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from evander.config import GatewayConfig
from evander.approval_diagnostics import build_payload_diagnostic
from evander.effects.providers.base import EffectRuntimeContext
from evander.effects.providers.session_revoke_mcp_tool_approval_lease import (
    SessionRevokeMcpToolApprovalLeaseEffectProvider,
)
from evander.directory_config import DirectoryConfigStore
from evander.directories import DirectoryStore
from evander.events.provider_models import (
    SessionRevokeMcpToolApprovalLeaseEffectDefinition,
)
from evander.messaging.targets import (
    SqliteCommunicationTargetsRepository,
    communication_targets_db_path,
)
from evander.messaging.discord_inbox_store import DiscordInboxStore
from evander.models import (
    ApprovalGrantSettings,
    ApprovalNotificationStatus,
    ApprovalPayloadMode,
    ApprovalRequest,
    ApprovalStatus,
    CancelScheduledWakeRequest,
    Directory,
    DiscordCommandEnvelope,
    EmailMailboxAccess,
    EmailMailboxGrant,
    SessionManifest,
    TeamsMessageEnvelope,
    ToolName,
    digest_payload,
)
from evander.policy import (
    ALWAYS_APPROVAL_REQUIRED_TOOLS,
    PolicyEngine,
    PolicyError,
    ToolArgumentPolicyError,
    reset_tool_approval_context,
    set_tool_approval_context,
)
from evander.repositories.session_manifests import JsonSessionManifestRepository
from evander.repositories.session_tool_approval_leases import (
    JsonSessionToolApprovalLeaseRepository,
)
from evander.runtime import allowed_tools_for_capability_profile
from evander.services import (
    effective_teams_channel_targets,
    normalize_teams_chat_targets,
)
from evander.storage import JsonStore


def make_engine(tmp_path: Path) -> PolicyEngine:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        audit_log_path=tmp_path / "audit.jsonl",
        session_store_path=tmp_path / "sessions.json",
        approval_store_path=tmp_path / "approvals.json",
        draft_store_path=tmp_path / "drafts.json",
        email_store_path=tmp_path / "emails.json",
        discord_command_store_path=tmp_path / "discord.json",
    )
    return make_engine_from_config(config)


def make_engine_from_config(config: GatewayConfig) -> PolicyEngine:
    sessions = JsonSessionManifestRepository(config.session_store_path)
    approvals = JsonStore(config.approval_store_path, ApprovalRequest)
    return PolicyEngine(config, sessions, approvals)


def write_root_policy_overlay(config: GatewayConfig, payload: dict[str, object]) -> None:
    directory_store = DirectoryStore(config.directories_store_path)
    directory_store.ensure_root()
    DirectoryConfigStore(directory_store, runtime_dir=config.runtime_dir).write(
        "root",
        domain="policy",
        payload=payload,
    )


def write_root_communication_targets(
    config: GatewayConfig, targets: list[dict[str, object]]
) -> None:
    directory_store = DirectoryStore(config.directories_store_path)
    directory_store.ensure_root()
    repository = SqliteCommunicationTargetsRepository(
        communication_targets_db_path(config.runtime_dir)
    )
    repository.write_overlay("root", {"targets": targets})


def make_ado_tool_policy_engine(tmp_path: Path) -> tuple[PolicyEngine, SessionManifest]:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        audit_log_path=tmp_path / "audit.jsonl",
        session_store_path=tmp_path / "sessions.json",
        approval_store_path=tmp_path / "approvals.json",
        draft_store_path=tmp_path / "drafts.json",
        email_store_path=tmp_path / "emails.json",
        discord_command_store_path=tmp_path / "discord.json",
    )
    write_root_policy_overlay(
        config,
        {
            "tool_argument_policies": {
                "create_ado_backlog_item": [
                    {
                        "id": "project-lock",
                        "name": "Lock project",
                        "constraints": [
                            {
                                "id": "project",
                                "path": "project",
                                "operator": "fixed",
                                "value": "Truman",
                                "case_sensitive": False,
                            }
                        ],
                    },
                    {
                        "id": "type-allowlist",
                        "name": "Allow backlog item types",
                        "constraints": [
                            {
                                "id": "type",
                                "path": "type",
                                "operator": "allowlist",
                                "values": ["Task", "Product Backlog Item"],
                                "case_sensitive": False,
                            }
                        ],
                    },
                    {
                        "id": "title-required",
                        "name": "Require title",
                        "constraints": [
                            {
                                "id": "title",
                                "path": "title",
                                "operator": "required",
                            }
                        ],
                    },
                    {
                        "id": "title-pattern",
                        "name": "Title pattern",
                        "constraints": [
                            {
                                "id": "title-pattern",
                                "path": "title",
                                "operator": "pattern",
                                "pattern": r"^Implement [A-Za-z ]+$",
                                "case_sensitive": False,
                            }
                        ],
                    },
                    {
                        "id": "state-enum",
                        "name": "Allow state values",
                        "constraints": [
                            {
                                "id": "state",
                                "path": "state",
                                "operator": "enum",
                                "values": ["Ready", "Active"],
                                "case_sensitive": False,
                            }
                        ],
                    },
                ]
            }
        },
    )
    engine = make_engine_from_config(config)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.CREATE_ADO_BACKLOG_ITEM],
        tool_argument_policies={
            "create_ado_backlog_item": [
                {
                    "id": "iteration-required",
                    "name": "Require iteration",
                    "constraints": [
                        {
                            "id": "iteration",
                            "path": "iteration",
                            "operator": "required",
                        }
                    ],
                },
                {
                    "id": "tags-deny",
                    "name": "Block forbidden tags",
                    "constraints": [
                        {
                            "id": "tags",
                            "path": "tags[]",
                            "operator": "denylist",
                            "values": ["forbidden-tag"],
                            "case_sensitive": False,
                        }
                    ],
                },
            ]
        },
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    engine.session_store.upsert_session(session)
    return engine, session


def _revoke_lease_with_provider(
    tmp_path: Path,
    *,
    lease_id: str,
    session_id: str,
    workflow_run_id: str,
) -> None:
    config = GatewayConfig(runtime_dir=tmp_path)
    repository = JsonSessionToolApprovalLeaseRepository.from_runtime_dir(tmp_path)
    provider = SessionRevokeMcpToolApprovalLeaseEffectProvider()
    provider.initialize(
        effect_definition=SessionRevokeMcpToolApprovalLeaseEffectDefinition(
            id="revoke-lease",
            display_name="Revoke lease",
            type="effect:session:revoke-mcp-tool-approval-lease",
            mcp_tool_approval_lease_id=lease_id,
            session_id=session_id,
            workflow_run_id=workflow_run_id,
        ),
        runtime_context=EffectRuntimeContext(
            resolved_config=config,
            admin_service=SimpleNamespace(
                runtime=SimpleNamespace(
                    tool_approval_leases=repository,
                    config=config,
                )
            ),
            env={},
        ),
    )
    provider.execute()


def test_require_tool_rejects_unknown_tool(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    manifest = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.EMAIL_LIST_UNREAD],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    engine.session_store.upsert_session(manifest)

    try:
        engine.require_tool("s1", ToolName.DISCORD_POST_STATUS)
    except PolicyError as exc:
        assert "not allowed" in str(exc)
    else:
        raise AssertionError("Expected PolicyError")


def test_require_tool_accepts_matching_run_scoped_tool_approval_lease(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    engine.tool_approval_leases = JsonSessionToolApprovalLeaseRepository.from_runtime_dir(tmp_path)
    engine.session_store.upsert_session(
        SessionManifest(
            session_id="s1",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    lease = engine.tool_approval_leases.upsert_lease(
        session_id="s1",
        workflow_run_id="run-1",
        tools=[ToolName.RUN_SCRIPT],
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )

    session = engine.require_tool(
        "s1",
        ToolName.RUN_SCRIPT,
        workflow_run_id="run-1",
        mcp_tool_approval_lease_id=lease.lease_id,
    )

    assert session.session_id == "s1"
    with pytest.raises(PolicyError, match="not allowed"):
        engine.require_tool(
            "s1",
            ToolName.RUN_SCRIPT,
            workflow_run_id="run-2",
            mcp_tool_approval_lease_id=lease.lease_id,
        )


def test_require_tool_reads_and_resets_run_scoped_tool_approval_context(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    engine.tool_approval_leases = JsonSessionToolApprovalLeaseRepository.from_runtime_dir(tmp_path)
    engine.session_store.upsert_session(
        SessionManifest(
            session_id="s1",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    lease = engine.tool_approval_leases.upsert_lease(
        session_id="s1",
        workflow_run_id="run-1",
        tools=[ToolName.RUN_SCRIPT],
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )

    token = set_tool_approval_context(
        session_id="s1",
        workflow_run_id="run-1",
        lease_id=lease.lease_id,
    )
    try:
        session = engine.require_tool("s1", ToolName.RUN_SCRIPT)
    finally:
        reset_tool_approval_context(token)

    assert session.session_id == "s1"
    with pytest.raises(PolicyError, match="not allowed"):
        engine.require_tool("s1", ToolName.RUN_SCRIPT)


def test_run_scoped_tool_approval_lease_can_be_limited_by_use_count(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    engine.tool_approval_leases = JsonSessionToolApprovalLeaseRepository.from_runtime_dir(tmp_path)
    engine.session_store.upsert_session(
        SessionManifest(
            session_id="s1",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    lease = engine.tool_approval_leases.upsert_lease(
        session_id="s1",
        workflow_run_id="run-1",
        tools=[ToolName.RUN_SCRIPT],
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
        approval_settings={
            ToolName.RUN_SCRIPT.value: ApprovalGrantSettings(max_uses=1, payload_mode="any")
        },
    )

    engine.require_tool(
        "s1",
        ToolName.RUN_SCRIPT,
        workflow_run_id="run-1",
        mcp_tool_approval_lease_id=lease.lease_id,
    )
    engine.consume_tool_approval_lease_use(
        session_id="s1",
        tool_name=ToolName.RUN_SCRIPT,
        workflow_run_id="run-1",
        mcp_tool_approval_lease_id=lease.lease_id,
    )

    assert (
        engine.tool_approval_leases.get_lease(lease.lease_id).used_counts[ToolName.RUN_SCRIPT.value]
        == 1
    )
    with pytest.raises(PolicyError, match="not usable"):
        engine.consume_tool_approval_lease_use(
            session_id="s1",
            tool_name=ToolName.RUN_SCRIPT,
            workflow_run_id="run-1",
            mcp_tool_approval_lease_id=lease.lease_id,
        )


def test_require_tool_rejects_revoked_run_scoped_tool_approval_lease(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    engine.tool_approval_leases = JsonSessionToolApprovalLeaseRepository.from_runtime_dir(tmp_path)
    engine.session_store.upsert_session(
        SessionManifest(
            session_id="s1",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    lease = engine.tool_approval_leases.upsert_lease(
        session_id="s1",
        workflow_run_id="run-1",
        tools=[ToolName.RUN_SCRIPT],
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    _revoke_lease_with_provider(
        tmp_path,
        lease_id=lease.lease_id,
        session_id="s1",
        workflow_run_id="run-1",
    )

    with pytest.raises(PolicyError, match="not allowed"):
        engine.require_tool(
            "s1",
            ToolName.RUN_SCRIPT,
            workflow_run_id="run-1",
            mcp_tool_approval_lease_id=lease.lease_id,
        )


def test_consume_tool_approval_lease_use_rejects_revoked_limited_use_lease(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    engine.tool_approval_leases = JsonSessionToolApprovalLeaseRepository.from_runtime_dir(tmp_path)
    engine.session_store.upsert_session(
        SessionManifest(
            session_id="s1",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    lease = engine.tool_approval_leases.upsert_lease(
        session_id="s1",
        workflow_run_id="run-1",
        tools=[ToolName.RUN_SCRIPT],
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
        approval_settings={
            ToolName.RUN_SCRIPT.value: ApprovalGrantSettings(max_uses=1, payload_mode="any")
        },
    )
    _revoke_lease_with_provider(
        tmp_path,
        lease_id=lease.lease_id,
        session_id="s1",
        workflow_run_id="run-1",
    )

    with pytest.raises(PolicyError, match="not usable"):
        engine.consume_tool_approval_lease_use(
            session_id="s1",
            tool_name=ToolName.RUN_SCRIPT,
            workflow_run_id="run-1",
            mcp_tool_approval_lease_id=lease.lease_id,
        )


def test_directory_mcp_tool_deny_overrides_session_and_descendant_policy(
    tmp_path: Path,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        audit_log_path=tmp_path / "audit.jsonl",
        session_store_path=tmp_path / "sessions.json",
        approval_store_path=tmp_path / "approvals.json",
        draft_store_path=tmp_path / "drafts.json",
        email_store_path=tmp_path / "emails.json",
        discord_command_store_path=tmp_path / "discord.json",
    )
    directory_store = DirectoryStore(config.directories_store_path)
    directory_store.ensure_root()
    directory_store.put(Directory(id="child", name="child", parent_id="root"))
    overlays = DirectoryConfigStore(directory_store, runtime_dir=config.runtime_dir)
    overlays.write(
        "root",
        domain="policy",
        payload={"mcp_tool_policies": {ToolName.RUN_SCRIPT.value: {"denied": True}}},
    )
    overlays.write(
        "root/child",
        domain="policy",
        payload={"mcp_tool_policies": {ToolName.RUN_SCRIPT.value: {"denied": False}}},
    )
    engine = make_engine_from_config(config)
    session = SessionManifest(
        session_id="s1",
        directory_path="root/child",
        allowed_tools=[ToolName.RUN_SCRIPT],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    engine.session_store.upsert_session(session)

    resolved = engine.effective_policy_for_session(session)
    assert resolved.mcp_tool_policies[ToolName.RUN_SCRIPT.value].denied is True
    with pytest.raises(PolicyError, match="denied by directory policy"):
        engine.require_tool("s1", ToolName.RUN_SCRIPT)


def test_tool_visibility_reports_approval_required_tools_as_available(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.RUN_SCRIPT],
        approval_required_tools=[ToolName.RUN_SCRIPT],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    engine.session_store.upsert_session(session)

    visibility = engine.tool_visibility_for_session(session, ToolName.RUN_SCRIPT)
    summary = engine.allowed_tool_summary_for_session(session)

    assert visibility.available is True
    assert visibility.visible is True
    assert visibility.requires_approval is True
    assert visibility.approval_locked is False
    assert visibility.approval_settings is not None
    assert visibility.source == "session.allowed_tools"
    assert any(
        item["tool_name"] == ToolName.RUN_SCRIPT.value
        and item["requires_approval"] is True
        and item["approval_locked"] is False
        for item in summary["available_tools"]
    )
    assert ToolName.RUN_SCRIPT.value in summary["approval_required_tools"]
    assert not any(
        item["tool_name"] == ToolName.RUN_SCRIPT.value for item in summary["unavailable_tools"]
    )


def test_tool_visibility_supports_registered_addon_tool_names(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.set_extra_mcp_tool_names(["bokio_get_company"])
    session = SessionManifest(
        session_id="s1",
        allowed_tools=["bokio_get_company"],
        approval_required_tools=["bokio_get_company"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    engine.session_store.upsert_session(session)

    visibility = engine.tool_visibility_for_session(session, "bokio_get_company")
    summary = engine.allowed_tool_summary_for_session(session)

    assert visibility.available is True
    assert visibility.visible is True
    assert visibility.requires_approval is True
    assert "bokio_get_company" in {item["tool_name"] for item in summary["available_tools"]}
    assert "bokio_get_company" in summary["approval_required_tools"]


def test_delete_ado_backlog_item_always_requires_approval_when_allowed(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.DELETE_ADO_BACKLOG_ITEM],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    engine.session_store.upsert_session(session)

    visibility = engine.tool_visibility_for_session(session, ToolName.DELETE_ADO_BACKLOG_ITEM)
    summary = engine.allowed_tool_summary_for_session(session)

    assert visibility.available is True
    assert visibility.visible is True
    assert visibility.requires_approval is True
    assert visibility.approval_locked is True
    assert visibility.approval_settings is not None
    assert engine.approval_required_for_tool(session, ToolName.DELETE_ADO_BACKLOG_ITEM) is True
    assert ToolName.DELETE_ADO_BACKLOG_ITEM.value in summary["approval_required_tools"]
    assert any(
        item["tool_name"] == ToolName.DELETE_ADO_BACKLOG_ITEM.value
        and item["requires_approval"] is True
        for item in summary["available_tools"]
    )


def test_delete_ado_backlog_item_approval_settings_force_exact_single_use(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.DELETE_ADO_BACKLOG_ITEM],
        approval_tool_settings={
            ToolName.DELETE_ADO_BACKLOG_ITEM.value: ApprovalGrantSettings(
                ttl_seconds=900,
                max_uses=None,
                payload_mode=ApprovalPayloadMode.ANY,
            )
        },
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    engine.session_store.upsert_session(session)

    settings = engine.approval_grant_settings_for_tool(session, ToolName.DELETE_ADO_BACKLOG_ITEM)

    assert settings.ttl_seconds == 900
    assert settings.max_uses == 1
    assert settings.payload_mode is ApprovalPayloadMode.EXACT


def test_always_approval_required_tools_approval_settings_force_exact_single_use(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)

    for index, tool_name in enumerate(
        sorted(ALWAYS_APPROVAL_REQUIRED_TOOLS, key=lambda tool: tool.value), start=1
    ):
        session = SessionManifest(
            session_id=f"s{index}",
            allowed_tools=[tool_name],
            approval_tool_settings={
                tool_name.value: ApprovalGrantSettings(
                    ttl_seconds=900,
                    max_uses=None,
                    payload_mode=ApprovalPayloadMode.ANY,
                )
            },
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        engine.session_store.upsert_session(session)

        settings = engine.approval_grant_settings_for_tool(session, tool_name)

        assert settings.ttl_seconds == 900
        assert settings.max_uses == 1
        assert settings.payload_mode is ApprovalPayloadMode.EXACT


def test_tool_visibility_reports_directory_denials_as_unavailable(
    tmp_path: Path,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        audit_log_path=tmp_path / "audit.jsonl",
        session_store_path=tmp_path / "sessions.json",
        approval_store_path=tmp_path / "approvals.json",
        draft_store_path=tmp_path / "drafts.json",
        email_store_path=tmp_path / "emails.json",
        discord_command_store_path=tmp_path / "discord.json",
    )
    write_root_policy_overlay(
        config,
        {"mcp_tool_policies": {ToolName.RUN_SCRIPT.value: {"denied": True}}},
    )
    engine = make_engine_from_config(config)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.RUN_SCRIPT],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    engine.session_store.upsert_session(session)

    visibility = engine.tool_visibility_for_session(session, ToolName.RUN_SCRIPT)
    summary = engine.allowed_tool_summary_for_session(session)

    assert visibility.available is False
    assert visibility.visible is False
    assert visibility.source == "directory.mcp_tool_policies"
    assert visibility.reason is not None
    assert "denied by directory policy" in visibility.reason
    assert any(
        item["tool_name"] == ToolName.RUN_SCRIPT.value
        and "denied by directory policy" in item["reason"]
        for item in summary["unavailable_tools"]
    )


def test_semantic_delete_points_always_requires_approval_without_config(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.SEMANTIC_DELETE_POINTS],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    engine.session_store.upsert_session(session)

    visibility = engine.tool_visibility_for_session(session, ToolName.SEMANTIC_DELETE_POINTS)
    summary = engine.allowed_tool_summary_for_session(session)

    assert engine.approval_required_for_tool(session, ToolName.SEMANTIC_DELETE_POINTS) is True
    assert visibility.available is True
    assert visibility.visible is True
    assert visibility.requires_approval is True
    assert visibility.approval_settings is not None
    assert visibility.source == "session.allowed_tools"
    assert any(
        item["tool_name"] == ToolName.SEMANTIC_DELETE_POINTS.value
        and item["requires_approval"] is True
        for item in summary["available_tools"]
    )
    assert ToolName.SEMANTIC_DELETE_POINTS.value in summary["approval_required_tools"]
    assert not any(
        item["tool_name"] == ToolName.SEMANTIC_DELETE_POINTS.value
        for item in summary["unavailable_tools"]
    )


def test_semantic_delete_points_cannot_be_disabled_by_policy_config(
    tmp_path: Path,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        audit_log_path=tmp_path / "audit.jsonl",
        session_store_path=tmp_path / "sessions.json",
        approval_store_path=tmp_path / "approvals.json",
        draft_store_path=tmp_path / "drafts.json",
        email_store_path=tmp_path / "emails.json",
        discord_command_store_path=tmp_path / "discord.json",
    )
    write_root_policy_overlay(
        config,
        {
            "approval_required_tools": [],
            "mcp_tool_policies": {
                ToolName.SEMANTIC_DELETE_POINTS.value: {"require_approval": False}
            },
        },
    )
    engine = make_engine_from_config(config)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.SEMANTIC_DELETE_POINTS],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    engine.session_store.upsert_session(session)

    visibility = engine.tool_visibility_for_session(session, ToolName.SEMANTIC_DELETE_POINTS)
    summary = engine.allowed_tool_summary_for_session(session)

    assert engine.approval_required_for_tool(session, ToolName.SEMANTIC_DELETE_POINTS) is True
    assert visibility.available is True
    assert visibility.visible is True
    assert visibility.requires_approval is True
    assert visibility.approval_settings is not None
    assert any(
        item["tool_name"] == ToolName.SEMANTIC_DELETE_POINTS.value
        and item["requires_approval"] is True
        for item in summary["available_tools"]
    )
    assert ToolName.SEMANTIC_DELETE_POINTS.value in summary["approval_required_tools"]


def test_tool_visibility_reports_session_disallowed_tools_with_reason(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.EMAIL_LIST_UNREAD],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    engine.session_store.upsert_session(session)

    visibility = engine.tool_visibility_for_session(session, ToolName.DISCORD_POST_STATUS)
    summary = engine.allowed_tool_summary_for_session(session)

    assert visibility.available is False
    assert visibility.visible is False
    assert visibility.source == "session.allowed_tools"
    assert visibility.reason == "Tool discord_post_status is not allowed for session s1"
    assert any(
        item["tool_name"] == ToolName.DISCORD_POST_STATUS.value
        and item["reason"] == "Tool discord_post_status is not allowed for session s1"
        for item in summary["unavailable_tools"]
    )


def test_tool_visibility_keeps_pending_prompts_visible_when_respond_to_agent_is_allowed(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.RESPOND_TO_AGENT],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    engine.session_store.upsert_session(session)

    visibility = engine.tool_visibility_for_session(session, ToolName.LIST_PENDING_AGENT_PROMPTS)

    assert visibility.available is True
    assert visibility.visible is True
    assert visibility.source == "session.allowed_tools.respond_to_agent"


def test_tool_visibility_reports_run_scoped_lease_access_without_session_allowlist(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    engine.tool_approval_leases = JsonSessionToolApprovalLeaseRepository.from_runtime_dir(tmp_path)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    engine.session_store.upsert_session(session)
    lease = engine.tool_approval_leases.upsert_lease(
        session_id="s1",
        workflow_run_id="run-1",
        tools=[ToolName.RUN_SCRIPT],
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )

    visibility = engine.tool_visibility_for_session(
        session,
        ToolName.RUN_SCRIPT,
        workflow_run_id="run-1",
        mcp_tool_approval_lease_id=lease.lease_id,
    )
    summary = engine.allowed_tool_summary_for_session(
        session,
        workflow_run_id="run-1",
        mcp_tool_approval_lease_id=lease.lease_id,
    )

    assert visibility.available is True
    assert visibility.visible is True
    assert visibility.source == "session_tool_approval_lease"
    assert visibility.requires_approval is False
    assert any(
        item["tool_name"] == ToolName.RUN_SCRIPT.value
        and item["source"] == "session_tool_approval_lease"
        for item in summary["available_tools"]
    )
    assert not any(
        item["tool_name"] == ToolName.RUN_SCRIPT.value for item in summary["unavailable_tools"]
    )


def test_approval_grant_settings_apply_directory_defaults_session_overrides_and_clamps(
    tmp_path: Path,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        audit_log_path=tmp_path / "audit.jsonl",
        session_store_path=tmp_path / "sessions.json",
        approval_store_path=tmp_path / "approvals.json",
        draft_store_path=tmp_path / "drafts.json",
        email_store_path=tmp_path / "emails.json",
        discord_command_store_path=tmp_path / "discord.json",
    )
    write_root_policy_overlay(
        config,
        {
            "mcp_tool_policies": {
                ToolName.RUN_SCRIPT.value: {
                    "default_settings": {
                        "ttl_seconds": 7200,
                        "max_uses": None,
                        "payload_mode": "any",
                    },
                    "max_ttl_seconds": 300,
                    "max_uses": 5,
                    "require_exact_payload": True,
                }
            }
        },
    )
    engine = make_engine_from_config(config)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.RUN_SCRIPT],
        approval_tool_settings={
            ToolName.RUN_SCRIPT.value: {
                "ttl_seconds": 600,
                "max_uses": 10,
                "payload_mode": "any",
            }
        },
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    engine.session_store.upsert_session(session)

    settings = engine.approval_grant_settings_for_tool(session, ToolName.RUN_SCRIPT)

    assert settings.ttl_seconds == 300
    assert settings.max_uses == 5
    assert settings.payload_mode == ApprovalPayloadMode.EXACT


def test_mcp_tool_approval_can_allow_payload_variance_until_use_count_exhausted(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.RUN_SCRIPT],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    engine.session_store.upsert_session(session)
    approval = engine.create_approval(
        "s1",
        "mcp_tool_invocation",
        {
            "tool_name": ToolName.RUN_SCRIPT.value,
            "session_id": "s1",
            "arguments": {"script_name": "one"},
        },
        "Run script",
        tool_name=ToolName.RUN_SCRIPT.value,
        approval_settings=ApprovalGrantSettings(
            ttl_seconds=3600,
            max_uses=2,
            payload_mode=ApprovalPayloadMode.ANY,
        ),
    )
    approval.status = ApprovalStatus.APPROVED
    engine.approval_store.put(approval.request_id, approval)

    engine.require_approved(
        approval.request_id,
        {
            "tool_name": ToolName.RUN_SCRIPT.value,
            "session_id": "s1",
            "arguments": {"script_name": "two"},
        },
        consume=True,
    )
    request = engine.require_approved(
        approval.request_id,
        {
            "tool_name": ToolName.RUN_SCRIPT.value,
            "session_id": "s1",
            "arguments": {"script_name": "three"},
        },
        consume=True,
    )

    assert request.used_count == 2
    assert request.status == ApprovalStatus.EXHAUSTED
    with pytest.raises(PolicyError, match="not approved") as excinfo:
        engine.require_approved(
            approval.request_id,
            {
                "tool_name": ToolName.RUN_SCRIPT.value,
                "session_id": "s1",
                "arguments": {"script_name": "four"},
            },
            consume=True,
        )
    exc = excinfo.value
    assert getattr(exc, "rejection_kind", None) == "exhausted"
    assert getattr(exc, "approval_request", None) is not None
    assert exc.approval_request.status == ApprovalStatus.EXHAUSTED
    assert exc.approval_diagnostic["approval_request_id"] == approval.request_id
    assert exc.approval_diagnostic["rejection_kind"] == "exhausted"
    assert exc.approval_diagnostic["status"] == ApprovalStatus.EXHAUSTED.value


def test_mcp_tool_any_payload_approval_validates_owner_before_consuming(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    engine.session_store.upsert_session(
        SessionManifest(
            session_id="s1",
            allowed_tools=[ToolName.RUN_SCRIPT],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    engine.session_store.upsert_session(
        SessionManifest(
            session_id="s2",
            allowed_tools=[ToolName.RUN_SCRIPT],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    approval = engine.create_approval(
        "s1",
        "mcp_tool_invocation",
        {
            "tool_name": ToolName.RUN_SCRIPT.value,
            "session_id": "s1",
            "arguments": {"script_name": "one"},
        },
        "Run script",
        tool_name=ToolName.RUN_SCRIPT.value,
        approval_settings=ApprovalGrantSettings(
            ttl_seconds=3600,
            max_uses=1,
            payload_mode=ApprovalPayloadMode.ANY,
        ),
    )
    approval.status = ApprovalStatus.APPROVED
    engine.approval_store.put(approval.request_id, approval)

    with pytest.raises(PolicyError, match="does not belong to session s2") as excinfo:
        engine.require_approved(
            approval.request_id,
            {
                "tool_name": ToolName.RUN_SCRIPT.value,
                "session_id": "s2",
                "arguments": {"script_name": "two"},
            },
            consume=True,
        )
    exc = excinfo.value
    assert getattr(exc, "rejection_kind", None) == "wrong_session"
    assert getattr(exc, "approval_request", None) is not None
    assert exc.approval_request.request_id == approval.request_id
    assert exc.approval_diagnostic["approval_request_id"] == approval.request_id
    assert exc.approval_diagnostic["rejection_kind"] == "wrong_session"
    stored = engine.approval_store.get(approval.request_id)
    assert stored is not None
    assert stored.status == ApprovalStatus.APPROVED
    assert stored.used_count == 0

    with pytest.raises(PolicyError, match="is not for tool complete_issue") as excinfo:
        engine.require_approved(
            approval.request_id,
            {
                "tool_name": ToolName.COMPLETE_ISSUE.value,
                "session_id": "s1",
                "arguments": {"issue_number": "1"},
            },
            consume=True,
        )
    exc = excinfo.value
    assert getattr(exc, "rejection_kind", None) == "wrong_tool"
    assert getattr(exc, "approval_request", None) is not None
    assert exc.approval_request.request_id == approval.request_id
    assert exc.approval_diagnostic["approval_request_id"] == approval.request_id
    assert exc.approval_diagnostic["rejection_kind"] == "wrong_tool"
    stored = engine.approval_store.get(approval.request_id)
    assert stored is not None
    assert stored.status == ApprovalStatus.APPROVED
    assert stored.used_count == 0

    consumed = engine.require_approved(
        approval.request_id,
        {
            "tool_name": ToolName.RUN_SCRIPT.value,
            "session_id": "s1",
            "arguments": {"script_name": "three"},
        },
        consume=True,
    )
    assert consumed.used_count == 1
    assert consumed.status == ApprovalStatus.EXHAUSTED


def test_require_approved_attaches_payload_mismatch_diagnostic(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.session_store.upsert_session(
        SessionManifest(
            session_id="s1",
            allowed_tools=[ToolName.RUN_SCRIPT],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    approval = engine.create_approval(
        "s1",
        "mcp_tool_invocation",
        {
            "tool_name": ToolName.RUN_SCRIPT.value,
            "session_id": "s1",
            "arguments": {"script_name": "one", "token": "approved-secret"},
        },
        "Run script",
        tool_name=ToolName.RUN_SCRIPT.value,
    )
    approval.status = ApprovalStatus.APPROVED
    engine.approval_store.put(approval.request_id, approval)

    attempted = {
        "tool_name": ToolName.RUN_SCRIPT.value,
        "session_id": "s1",
        "arguments": {"script_name": "two", "token": "attempted-secret"},
    }

    with pytest.raises(PolicyError, match="Approval payload mismatch") as excinfo:
        engine.require_approved(approval.request_id, attempted, consume=True)

    exc = excinfo.value
    assert getattr(exc, "rejection_kind", None) == "payload_mismatch"
    assert getattr(exc, "approval_request", None) is not None
    assert exc.approval_request.request_id == approval.request_id
    diagnostic = exc.approval_diagnostic
    assert diagnostic["approval_request_id"] == approval.request_id
    assert diagnostic["rejection_kind"] == "payload_mismatch"
    assert diagnostic["payload_match"] is False
    assert diagnostic["approved_payload_digest"] == approval.payload_digest
    assert diagnostic["attempted_payload_digest"] == digest_payload(attempted)
    assert diagnostic["approved_payload_summary"]["arguments"]["token"] == "<redacted>"
    assert diagnostic["attempted_payload_summary"]["arguments"]["token"] == "<redacted>"
    changed = {entry["path"]: entry for entry in diagnostic["differences"]["changed"]["entries"]}
    assert changed["arguments.script_name"] == {
        "path": "arguments.script_name",
        "approved_value": "one",
        "attempted_value": "two",
    }
    assert changed["arguments.token"] == {
        "path": "arguments.token",
        "approved_value": "<redacted>",
        "attempted_value": "<redacted>",
    }


def test_require_approved_attaches_missing_approval_diagnostic(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)

    with pytest.raises(PolicyError, match="Unknown approval request") as excinfo:
        engine.require_approved("missing-request", {"tool_name": ToolName.RUN_SCRIPT.value})

    exc = excinfo.value
    assert getattr(exc, "rejection_kind", None) == "missing"
    assert getattr(exc, "approval_request", None) is None
    assert exc.approval_diagnostic == {
        "approval_request_id": "missing-request",
        "rejection_kind": "missing",
        "status": "missing",
        "reason": "Unknown approval request: missing-request",
    }


def test_require_approved_attaches_denied_and_expired_diagnostics(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.session_store.upsert_session(
        SessionManifest(
            session_id="s1",
            allowed_tools=[ToolName.RUN_SCRIPT],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )

    denied_payload = {
        "tool_name": ToolName.RUN_SCRIPT.value,
        "session_id": "s1",
        "arguments": {"script_name": "one"},
    }
    denied = ApprovalRequest(
        request_id="denied-1",
        session_id="s1",
        action_type="mcp_tool_invocation",
        payload_digest=digest_payload(denied_payload),
        human_summary="denied",
        payload=denied_payload,
        tool_name=ToolName.RUN_SCRIPT.value,
        status=ApprovalStatus.DENIED,
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    engine.approval_store.put(denied.request_id, denied)

    with pytest.raises(PolicyError, match="is not approved") as excinfo:
        engine.require_approved(denied.request_id, denied_payload)
    exc = excinfo.value
    assert getattr(exc, "rejection_kind", None) == "denied"
    assert exc.approval_request is not None
    assert exc.approval_request.status == ApprovalStatus.DENIED
    assert exc.approval_diagnostic["payload_match"] is True
    assert exc.approval_diagnostic["rejection_kind"] == "denied"
    stored_denied = engine.approval_store.get(denied.request_id)
    assert stored_denied is not None
    assert stored_denied.status == ApprovalStatus.DENIED

    expired_payload = {
        "tool_name": ToolName.RUN_SCRIPT.value,
        "session_id": "s1",
        "arguments": {"script_name": "two"},
    }
    expired = ApprovalRequest(
        request_id="expired-1",
        session_id="s1",
        action_type="mcp_tool_invocation",
        payload_digest=digest_payload(expired_payload),
        human_summary="expired",
        payload=expired_payload,
        tool_name=ToolName.RUN_SCRIPT.value,
        status=ApprovalStatus.APPROVED,
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    engine.approval_store.put(expired.request_id, expired)

    with pytest.raises(PolicyError, match="has expired") as excinfo:
        engine.require_approved(expired.request_id, expired_payload)
    exc = excinfo.value
    assert getattr(exc, "rejection_kind", None) == "expired"
    assert exc.approval_request is not None
    assert exc.approval_request.status == ApprovalStatus.EXPIRED
    assert exc.approval_diagnostic["approval_request_id"] == expired.request_id
    assert exc.approval_diagnostic["rejection_kind"] == "expired"


def test_require_approved_ignores_redacted_stored_fields_when_digest_matches(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    engine.session_store.upsert_session(
        SessionManifest(
            session_id="s1",
            allowed_tools=[ToolName.RUN_SCRIPT],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    approved_payload = {
        "tool_name": ToolName.RUN_SCRIPT.value,
        "session_id": "s1",
        "arguments": {"script_name": "one", "token": "approved-secret"},
    }
    approval = engine.create_approval(
        "s1",
        "mcp_tool_invocation",
        approved_payload,
        "Run script",
        tool_name=ToolName.RUN_SCRIPT.value,
    )
    assert approval.payload["arguments"]["token"] == "<redacted>"
    approval.status = ApprovalStatus.DENIED
    engine.approval_store.put(approval.request_id, approval)

    with pytest.raises(PolicyError, match="is not approved") as excinfo:
        engine.require_approved(approval.request_id, approved_payload)

    diagnostic = excinfo.value.approval_diagnostic
    assert diagnostic["rejection_kind"] == "denied"
    assert diagnostic["approved_payload_digest"] == approval.payload_digest
    assert diagnostic["attempted_payload_digest"] == approval.payload_digest
    assert diagnostic["payload_match"] is True
    assert diagnostic["approved_payload_summary"]["arguments"]["token"] == "<redacted>"
    assert diagnostic["attempted_payload_summary"]["arguments"]["token"] == "<redacted>"
    assert diagnostic["differences"] == {
        "changed": {"entries": [], "truncated": False},
        "missing": {"entries": [], "truncated": False},
        "extra": {"entries": [], "truncated": False},
        "truncated": False,
    }


def test_require_tool_schedule_resume_requires_vm_binding(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.session_store.upsert_session(
        SessionManifest(
            session_id="s1",
            allowed_tools=[ToolName.SCHEDULE_RESUME],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    with pytest.raises(PolicyError, match="requires a session with a VM binding"):
        engine.require_tool("s1", ToolName.SCHEDULE_RESUME)


def test_require_tool_schedule_resume_allows_container_worker_vm_without_vm_binding(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    engine.session_store.upsert_session(
        SessionManifest(
            session_id="s1",
            vm_provider="container_worker_vm",
            allowed_tools=[ToolName.SCHEDULE_RESUME],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    session = engine.require_tool("s1", ToolName.SCHEDULE_RESUME)

    assert session.session_id == "s1"


def test_require_tool_schedule_resume_allows_container_backend_metadata_without_vm_binding(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    engine.session_store.upsert_session(
        SessionManifest(
            session_id="s1",
            allowed_tools=[ToolName.SCHEDULE_RESUME],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={"execution_backend": "container_worker_vm"},
        ),
    )

    session = engine.require_tool("s1", ToolName.SCHEDULE_RESUME)

    assert session.session_id == "s1"


def test_require_tool_schedule_resume_rejects_non_container_session_without_vm_binding(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    engine.session_store.upsert_session(
        SessionManifest(
            session_id="s1",
            vm_provider="libvirt_vm",
            allowed_tools=[ToolName.SCHEDULE_RESUME],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    with pytest.raises(PolicyError, match="requires a session with a VM binding"):
        engine.require_tool("s1", ToolName.SCHEDULE_RESUME)


def test_require_tool_cancel_scheduled_wake_does_not_require_vm_binding(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    engine.session_store.upsert_session(
        SessionManifest(
            session_id="s1",
            allowed_tools=[ToolName.CANCEL_SCHEDULED_WAKE],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    session = engine.require_tool("s1", ToolName.CANCEL_SCHEDULED_WAKE)

    assert session.session_id == "s1"


def test_require_tool_cancel_scheduled_wake_rejects_session_without_tool_permission(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    engine.session_store.upsert_session(
        SessionManifest(
            session_id="s1",
            allowed_tools=[ToolName.SCHEDULE_RESUME],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    with pytest.raises(PolicyError, match="is not allowed for session s1"):
        engine.require_tool("s1", ToolName.CANCEL_SCHEDULED_WAKE)


def test_require_tool_cancel_scheduled_wake_respects_directory_denials(
    tmp_path: Path,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        audit_log_path=tmp_path / "audit.jsonl",
        session_store_path=tmp_path / "sessions.json",
        approval_store_path=tmp_path / "approvals.json",
        draft_store_path=tmp_path / "drafts.json",
        email_store_path=tmp_path / "emails.json",
        discord_command_store_path=tmp_path / "discord.json",
    )
    write_root_policy_overlay(
        config,
        {"mcp_tool_policies": {ToolName.CANCEL_SCHEDULED_WAKE.value: {"denied": True}}},
    )
    engine = make_engine_from_config(config)
    engine.session_store.upsert_session(
        SessionManifest(
            session_id="s1",
            allowed_tools=[ToolName.CANCEL_SCHEDULED_WAKE],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    with pytest.raises(PolicyError, match="denied by directory policy"):
        engine.require_tool("s1", ToolName.CANCEL_SCHEDULED_WAKE)


def test_cancel_scheduled_wake_request_normalizes_identifiers_reason_and_metadata() -> None:
    request = CancelScheduledWakeRequest(
        session_id="  s1  ",
        request_id="  req-1  ",
        reason="  " + ("x" * 1100) + "  ",
        metadata=None,
    )

    assert request.session_id == "s1"
    assert request.request_id == "req-1"
    assert len(request.reason or "") == 1024
    assert request.reason == "x" * 1024
    assert request.metadata == {}


def test_assistant_mutation_tools_require_explicit_mutation_policy(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.session_store.upsert_session(
        SessionManifest(
            session_id="s1",
            allowed_tools=[ToolName.ASSISTANT_UPSERT_CONFIG],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={"capability_profile": "operator-limited"},
        ),
    )
    with pytest.raises(PolicyError, match="assistant_mutation_policy"):
        engine.require_tool("s1", ToolName.ASSISTANT_UPSERT_CONFIG)


def test_assistant_read_config_does_not_require_mutation_policy(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.session_store.upsert_session(
        SessionManifest(
            session_id="s1",
            allowed_tools=[ToolName.ASSISTANT_READ_CONFIG],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={"capability_profile": "read-only"},
        ),
    )

    session = engine.require_tool("s1", ToolName.ASSISTANT_READ_CONFIG)

    assert session.session_id == "s1"


def test_assistant_profiles_include_read_config_tool() -> None:
    assert ToolName.ASSISTANT_READ_CONFIG in allowed_tools_for_capability_profile("read-only")
    assert ToolName.ASSISTANT_READ_CONFIG in allowed_tools_for_capability_profile(
        "operator-limited"
    )
    assert ToolName.ASSISTANT_READ_CONFIG in allowed_tools_for_capability_profile("full-admin")


def test_assistant_profiles_include_documentation_tools() -> None:
    documentation_tools = {
        ToolName.DOCUMENTATION_LIST,
        ToolName.DOCUMENTATION_SEARCH,
        ToolName.DOCUMENTATION_READ,
    }
    for profile in ("read-only", "operator-limited", "full-admin"):
        assert documentation_tools <= set(allowed_tools_for_capability_profile(profile))


def test_documentation_tool_is_grantable_without_assistant_metadata(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.session_store.upsert_session(
        SessionManifest(
            session_id="ordinary-agent",
            allowed_tools=[ToolName.DOCUMENTATION_READ],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )

    session = engine.require_tool("ordinary-agent", ToolName.DOCUMENTATION_READ)

    assert session.session_id == "ordinary-agent"


def test_assistant_destructive_operation_can_require_approval_gate(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.session_store.upsert_session(
        SessionManifest(
            session_id="s1",
            allowed_tools=[ToolName.ASSISTANT_RUN_SAFE_OPERATION],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "capability_profile": "full-admin",
                "assistant_mutation_policy": {
                    "writable_scopes": ["config/workflow", "config/root"],
                    "require_destructive_approval_gate": True,
                },
            },
        ),
    )
    with pytest.raises(PolicyError, match="destructive approval gate"):
        engine.require_tool("s1", ToolName.ASSISTANT_RUN_SAFE_OPERATION)


def test_tool_argument_policy_for_session_combines_directory_and_session_rules(
    tmp_path: Path,
) -> None:
    engine, session = make_ado_tool_policy_engine(tmp_path)

    rules = engine.tool_argument_policy_for_session(session, ToolName.CREATE_ADO_BACKLOG_ITEM)

    assert [rule.id for rule in rules] == [
        "project-lock",
        "type-allowlist",
        "title-required",
        "title-pattern",
        "state-enum",
        "iteration-required",
        "tags-deny",
    ]


def test_require_tool_arguments_allows_matching_payload(tmp_path: Path) -> None:
    engine, session = make_ado_tool_policy_engine(tmp_path)

    engine.require_tool_arguments(
        session,
        ToolName.CREATE_ADO_BACKLOG_ITEM,
        {
            "project": "truman",
            "type": "task",
            "title": "IMPLEMENT POLICY ENGINE",
            "state": "ready",
            "iteration": "Sprint 1",
            "tags": ["alpha", "beta"],
        },
    )


@pytest.mark.parametrize(
    ("payload", "rule_id", "constraint_id", "path", "message_fragment"),
    [
        (
            {
                "project": "truman",
                "type": "task",
                "title": "",
                "state": "ready",
                "iteration": "Sprint 1",
                "tags": ["alpha", "beta"],
            },
            "title-required",
            "title",
            "title",
            "must be present",
        ),
        (
            {
                "project": "truman",
                "type": "bug",
                "title": "IMPLEMENT POLICY ENGINE",
                "state": "ready",
                "iteration": "Sprint 1",
                "tags": ["alpha", "beta"],
            },
            "type-allowlist",
            "type",
            "type",
            "allowed set",
        ),
        (
            {
                "project": "truman",
                "type": "task",
                "title": "IMPLEMENT POLICY ENGINE",
                "state": "ready",
                "iteration": "Sprint 1",
                "tags": ["alpha", "forbidden-tag"],
            },
            "tags-deny",
            "tags",
            "tags[]",
            "denied value",
        ),
        (
            {
                "project": "Other Project",
                "type": "task",
                "title": "IMPLEMENT POLICY ENGINE",
                "state": "ready",
                "iteration": "Sprint 1",
                "tags": ["alpha", "beta"],
            },
            "project-lock",
            "project",
            "project",
            "must equal",
        ),
        (
            {
                "project": "truman",
                "type": "task",
                "title": "Ship policy engine",
                "state": "ready",
                "iteration": "Sprint 1",
                "tags": ["alpha", "beta"],
            },
            "title-pattern",
            "title-pattern",
            "title",
            "required pattern",
        ),
        (
            {
                "project": "truman",
                "type": "task",
                "title": "IMPLEMENT POLICY ENGINE",
                "state": "blocked",
                "iteration": "Sprint 1",
                "tags": ["alpha", "beta"],
            },
            "state-enum",
            "state",
            "state",
            "allowed set",
        ),
    ],
)
def test_require_tool_arguments_rejects_violations(
    tmp_path: Path,
    payload: dict[str, object],
    rule_id: str,
    constraint_id: str,
    path: str,
    message_fragment: str,
) -> None:
    engine, session = make_ado_tool_policy_engine(tmp_path)

    with pytest.raises(ToolArgumentPolicyError) as excinfo:
        engine.require_tool_arguments(session, ToolName.CREATE_ADO_BACKLOG_ITEM, payload)

    exc = excinfo.value
    assert exc.rule_id == rule_id
    assert exc.constraint_id == constraint_id
    assert exc.path == path
    assert exc.rule_name is not None
    assert "tool argument policy violation for create_ado_backlog_item" in str(exc)
    assert message_fragment in str(exc)


def test_require_approved_rejects_payload_mismatch(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    approval = ApprovalRequest(
        request_id="apr1",
        session_id="s1",
        action_type="send_email",
        payload_digest="abc",
        human_summary="test",
        status=ApprovalStatus.APPROVED,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    engine.approval_store.put("apr1", approval)

    try:
        engine.require_approved("apr1", {"subject": "different"})
    except PolicyError as exc:
        assert "mismatch" in str(exc)
    else:
        raise AssertionError("Expected PolicyError")


def test_build_payload_diagnostic_reports_changed_missing_and_extra_fields() -> None:
    approved = {
        "project": "Truman",
        "title": "Implement policy engine",
        "details": {"owner": "alice", "count": 1},
        "tags": ["alpha", "beta"],
    }
    attempted = {
        "project": "Truman",
        "title": "Implement approval diagnostics",
        "details": {"count": 1},
        "tags": ["alpha", "gamma", "delta"],
        "extra": "value",
    }

    diagnostic = build_payload_diagnostic(
        approved_payload=approved,
        attempted_payload=attempted,
        status="approved",
        reason="payload mismatch",
        redacted_keys=set(),
    )

    assert diagnostic["status"] == "approved"
    assert diagnostic["reason"] == "payload mismatch"
    assert diagnostic["payload_match"] is False
    assert diagnostic["approved_payload_digest"] == digest_payload(approved)
    assert diagnostic["attempted_payload_digest"] == digest_payload(attempted)
    assert diagnostic["approved_payload_summary"] == approved
    assert diagnostic["attempted_payload_summary"] == attempted
    changed = {entry["path"]: entry for entry in diagnostic["differences"]["changed"]["entries"]}
    missing = {entry["path"]: entry for entry in diagnostic["differences"]["missing"]["entries"]}
    extra = {entry["path"]: entry for entry in diagnostic["differences"]["extra"]["entries"]}
    assert changed == {
        "title": {
            "path": "title",
            "approved_value": "Implement policy engine",
            "attempted_value": "Implement approval diagnostics",
        },
        "tags[1]": {
            "path": "tags[1]",
            "approved_value": "beta",
            "attempted_value": "gamma",
        },
    }
    assert missing == {"details.owner": {"path": "details.owner", "approved_value": "alice"}}
    assert extra == {
        "tags[2]": {"path": "tags[2]", "attempted_value": "delta"},
        "extra": {"path": "extra", "attempted_value": "value"},
    }
    assert diagnostic["differences"]["truncated"] is False


def test_build_payload_diagnostic_redacts_sensitive_values() -> None:
    approved = {
        "token": "approved-secret",
        "authorization": "Bearer approved",
        "nested": {
            "cookie": "approved-cookie",
            "private_key": "approved-key",
            "safe": "ok",
        },
    }
    attempted = {
        "token": "attempted-secret",
        "authorization": "Bearer attempted",
        "nested": {
            "cookie": "attempted-cookie",
            "private_key": "attempted-key",
            "safe": "ok",
        },
    }

    diagnostic = build_payload_diagnostic(
        approved_payload=approved,
        attempted_payload=attempted,
        redacted_keys={"token"},
    )

    assert diagnostic["approved_payload_summary"]["token"] == "<redacted>"
    assert diagnostic["approved_payload_summary"]["authorization"] == "<redacted>"
    assert diagnostic["approved_payload_summary"]["nested"]["cookie"] == "<redacted>"
    assert diagnostic["approved_payload_summary"]["nested"]["private_key"] == "<redacted>"
    assert diagnostic["approved_payload_summary"]["nested"]["safe"] == "ok"
    assert diagnostic["attempted_payload_summary"]["token"] == "<redacted>"
    assert diagnostic["attempted_payload_summary"]["authorization"] == "<redacted>"
    assert diagnostic["attempted_payload_summary"]["nested"]["cookie"] == "<redacted>"
    assert diagnostic["attempted_payload_summary"]["nested"]["private_key"] == "<redacted>"
    assert diagnostic["attempted_payload_summary"]["nested"]["safe"] == "ok"
    changed = {entry["path"]: entry for entry in diagnostic["differences"]["changed"]["entries"]}
    assert changed == {
        "authorization": {
            "path": "authorization",
            "approved_value": "<redacted>",
            "attempted_value": "<redacted>",
        },
        "nested.cookie": {
            "path": "nested.cookie",
            "approved_value": "<redacted>",
            "attempted_value": "<redacted>",
        },
        "nested.private_key": {
            "path": "nested.private_key",
            "approved_value": "<redacted>",
            "attempted_value": "<redacted>",
        },
        "token": {
            "path": "token",
            "approved_value": "<redacted>",
            "attempted_value": "<redacted>",
        },
    }


def test_build_payload_diagnostic_uses_raw_attempted_payload_for_digest() -> None:
    approved = {"token": "approved-secret", "value": "same"}
    attempted = {"token": "attempted-secret", "value": "same"}

    diagnostic = build_payload_diagnostic(
        approved_payload=approved,
        attempted_payload=attempted,
        redacted_keys={"token"},
    )

    assert diagnostic["attempted_payload_digest"] == digest_payload(attempted)
    assert diagnostic["attempted_payload_digest"] != digest_payload(
        diagnostic["attempted_payload_summary"]
    )


def test_build_payload_diagnostic_payload_match_uses_digest_equality() -> None:
    approved = {"token": "<redacted>", "value": "same"}
    attempted = {"token": "<redacted>", "value": "same"}
    approved_payload_digest = digest_payload({"token": "approved-secret", "value": "same"})
    attempted_payload_digest = digest_payload(attempted)

    diagnostic = build_payload_diagnostic(
        approved_payload=approved,
        attempted_payload=attempted,
        redacted_keys={"token"},
        approved_payload_digest=approved_payload_digest,
        attempted_payload_digest=attempted_payload_digest,
    )

    assert approved_payload_digest != attempted_payload_digest
    assert diagnostic["payload_match"] is False
    assert diagnostic["differences"]["changed"]["entries"] == []
    assert diagnostic["differences"]["missing"]["entries"] == []
    assert diagnostic["differences"]["extra"]["entries"] == []


def test_build_payload_diagnostic_suppresses_redacted_diffs_when_digests_match() -> None:
    attempted = {"token": "actual-secret", "value": "same"}
    approved = {"token": "<redacted>", "value": "same"}
    canonical_digest = digest_payload(attempted)

    diagnostic = build_payload_diagnostic(
        approved_payload=approved,
        attempted_payload=attempted,
        redacted_keys={"token"},
        approved_payload_digest=canonical_digest,
        attempted_payload_digest=canonical_digest,
    )

    assert diagnostic["payload_match"] is True
    assert diagnostic["approved_payload_summary"]["token"] == "<redacted>"
    assert diagnostic["attempted_payload_summary"]["token"] == "<redacted>"
    assert diagnostic["differences"]["changed"]["entries"] == []
    assert diagnostic["differences"]["missing"]["entries"] == []
    assert diagnostic["differences"]["extra"]["entries"] == []


def test_build_payload_diagnostic_truncates_deep_array_diffs() -> None:
    approved = {"items": [{"value": index} for index in range(6)]}
    attempted = {"items": [{"value": index + 100} for index in range(6)]}

    diagnostic = build_payload_diagnostic(
        approved_payload=approved,
        attempted_payload=attempted,
        redacted_keys=set(),
        max_diff_entries=3,
    )

    assert diagnostic["differences"]["changed"]["entries"] == [
        {
            "path": "items[0].value",
            "approved_value": 0,
            "attempted_value": 100,
        },
        {
            "path": "items[1].value",
            "approved_value": 1,
            "attempted_value": 101,
        },
        {
            "path": "items[2].value",
            "approved_value": 2,
            "attempted_value": 102,
        },
    ]
    assert diagnostic["differences"]["changed"]["truncated"] is True
    assert diagnostic["differences"]["truncated"] is True


def test_create_approval_persists_redacted_payload(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.session_store.upsert_session(
        SessionManifest(
            session_id="s1",
            allowed_tools=[ToolName.APPROVAL_SUBMIT],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    approval = engine.create_approval(
        "s1",
        "email_send_approved",
        {
            "body": "secret body",
            "authorization": "Bearer abc",
            "nested": {"token": "nested-secret", "safe": "ok"},
        },
        "send email",
    )

    assert approval.payload["body"] == "<redacted>"
    assert approval.payload["authorization"] == "<redacted>"
    assert approval.payload["nested"]["token"] == "<redacted>"
    assert approval.payload["nested"]["safe"] == "ok"


def test_create_approval_persists_directory_session_and_run_scoped_notification_targets(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    engine.tool_approval_leases = JsonSessionToolApprovalLeaseRepository.from_runtime_dir(tmp_path)
    write_root_policy_overlay(
        engine.config,
        {"approval_notification_target_ids": [" OPS-Root ", "ops-shared"]},
    )
    write_root_communication_targets(
        engine.config,
        [
            {
                "id": "ops-root",
                "provider": "teams",
                "kind": "teams",
                "address": "team-a/channel-1",
                "policy": {"capabilities": ["notify"]},
            },
            {
                "id": "ops-session",
                "provider": "teams",
                "kind": "teams",
                "address": "team-a/channel-2",
                "policy": {"capabilities": ["notify"]},
            },
            {
                "id": "ops-lease",
                "provider": "teams",
                "kind": "teams",
                "address": "team-a/channel-3",
                "policy": {"capabilities": ["notify"]},
            },
            {
                "id": "ops-shared",
                "provider": "teams",
                "kind": "teams",
                "address": "team-a/channel-4",
                "policy": {"capabilities": ["notify"]},
            },
        ],
    )
    engine.session_store.upsert_session(
        SessionManifest(
            session_id="s1",
            allowed_tools=[ToolName.APPROVAL_SUBMIT],
            approval_notification_target_ids=[" Ops-Session ", "ops-shared"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    lease = engine.tool_approval_leases.upsert_lease(
        session_id="s1",
        workflow_run_id="run-1",
        tools=[ToolName.RUN_SCRIPT],
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
        approval_notification_target_ids=[" OPS-Lease ", "ops-shared"],
    )
    token = set_tool_approval_context(
        session_id="s1",
        workflow_run_id="run-1",
        lease_id=lease.lease_id,
    )
    try:
        approval = engine.create_approval(
            "s1",
            "email_send_approved",
            {"body": "secret body"},
            "send email",
        )
    finally:
        reset_tool_approval_context(token)

    assert approval.notification_target_ids == [
        "ops-root",
        "ops-shared",
        "ops-session",
        "ops-lease",
    ]
    assert approval.notification_status == ApprovalNotificationStatus.QUEUED
    assert approval.notification_attempted_at is not None


@pytest.mark.parametrize(
    ("context_session_id", "context_workflow_run_id", "lease_state"),
    [
        ("other-session", "run-1", "active"),
        ("s1", "other-run", "active"),
        ("s1", "run-1", "revoked"),
        ("s1", "run-1", "expired"),
    ],
)
def test_create_approval_ignores_nonmatching_or_inactive_run_scoped_notification_targets(
    tmp_path: Path,
    context_session_id: str,
    context_workflow_run_id: str,
    lease_state: str,
) -> None:
    engine = make_engine(tmp_path)
    engine.tool_approval_leases = JsonSessionToolApprovalLeaseRepository.from_runtime_dir(tmp_path)
    write_root_policy_overlay(
        engine.config,
        {"approval_notification_target_ids": ["ops-root"]},
    )
    write_root_communication_targets(
        engine.config,
        [
            {
                "id": "ops-root",
                "provider": "teams",
                "kind": "teams",
                "address": "team-a/channel-1",
                "policy": {"capabilities": ["notify"]},
            },
            {
                "id": "ops-session",
                "provider": "teams",
                "kind": "teams",
                "address": "team-a/channel-2",
                "policy": {"capabilities": ["notify"]},
            },
            {
                "id": "ops-lease",
                "provider": "teams",
                "kind": "teams",
                "address": "team-a/channel-3",
                "policy": {"capabilities": ["notify"]},
            },
        ],
    )
    engine.session_store.upsert_session(
        SessionManifest(
            session_id="s1",
            allowed_tools=[ToolName.APPROVAL_SUBMIT],
            approval_notification_target_ids=["ops-session"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    lease = engine.tool_approval_leases.upsert_lease(
        session_id="s1",
        workflow_run_id="run-1",
        tools=[ToolName.RUN_SCRIPT],
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
        approval_notification_target_ids=["ops-lease"],
    )
    if lease_state == "revoked":
        assert engine.tool_approval_leases.revoke_lease(lease.lease_id)
    elif lease_state == "expired":
        engine.tool_approval_leases.upsert_lease(
            session_id="s1",
            workflow_run_id="run-1",
            tools=[ToolName.RUN_SCRIPT],
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
            approval_notification_target_ids=["ops-lease"],
        )
        lease = engine.tool_approval_leases.get_lease(lease.lease_id) or lease

    token = set_tool_approval_context(
        session_id=context_session_id,
        workflow_run_id=context_workflow_run_id,
        lease_id=lease.lease_id,
    )
    try:
        approval = engine.create_approval(
            "s1",
            "email_send_approved",
            {"body": "secret body"},
            "send email",
        )
    finally:
        reset_tool_approval_context(token)

    assert approval.notification_target_ids == ["ops-root", "ops-session"]
    assert approval.notification_status == ApprovalNotificationStatus.QUEUED
    assert approval.notification_attempted_at is not None


def test_create_approval_refreshes_notification_targets_on_stale_submission(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    write_root_policy_overlay(
        engine.config,
        {"approval_notification_target_ids": ["ops-root"]},
    )
    write_root_communication_targets(
        engine.config,
        [
            {
                "id": "ops-root",
                "provider": "teams",
                "kind": "teams",
                "address": "team-a/channel-1",
                "policy": {"capabilities": ["notify"]},
            },
            {
                "id": "ops-session",
                "provider": "teams",
                "kind": "teams",
                "address": "team-a/channel-2",
                "policy": {"capabilities": ["notify"]},
            },
        ],
    )
    engine.session_store.upsert_session(
        SessionManifest(
            session_id="s1",
            allowed_tools=[ToolName.APPROVAL_SUBMIT],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    first = engine.create_approval(
        "s1",
        "email_send_approved",
        {"body": "secret body"},
        "send email",
    )
    engine.session_store.upsert_session(
        SessionManifest(
            session_id="s1",
            allowed_tools=[ToolName.APPROVAL_SUBMIT],
            approval_notification_target_ids=["ops-session"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    first.status = ApprovalStatus.DENIED
    first.approver = "alice"
    first.decision_at = datetime.now(UTC)
    first.decision_reason = "stale"
    engine.approval_store.put(first.request_id, first)

    second = engine.create_approval(
        "s1",
        "email_send_approved",
        {"body": "secret body"},
        "send email",
    )

    assert second.request_id == first.request_id
    assert second.notification_target_ids == ["ops-root", "ops-session"]
    assert second.notification_status == ApprovalNotificationStatus.QUEUED
    assert second.notification_attempted_at is not None


def test_get_approval_redacts_legacy_stored_payload(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.session_store.upsert_session(
        SessionManifest(
            session_id="s1",
            allowed_tools=[ToolName.APPROVAL_SUBMIT],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    engine.approval_store.put(
        "apr1",
        ApprovalRequest(
            request_id="apr1",
            session_id="s1",
            action_type="email_send_approved",
            payload_digest="abc",
            human_summary="send email",
            payload={"body": "secret", "nested": {"token": "secret-token", "safe": "ok"}},
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    approval = engine.get_approval("apr1")
    persisted = engine.approval_store.get("apr1")

    assert approval.payload["body"] == "<redacted>"
    assert approval.payload["nested"]["token"] == "<redacted>"
    assert approval.payload["nested"]["safe"] == "ok"
    assert persisted is not None
    assert persisted.payload == approval.payload


def test_require_discord_command_source_allows_session_channel(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
        discord_channel_allowlist=["chan-control"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    command = DiscordCommandEnvelope(
        command_id="cmd1",
        author_id="user-unlisted",
        channel_id="chan-control",
        content="!status",
        metadata={
            "source": "discord_bot_api",
            "channel_id": "chan-control",
            "author_id": "user-unlisted",
        },
    )

    engine.require_discord_command_source(session, command)


def test_require_discord_command_source_allows_operator_user(tmp_path: Path) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        audit_log_path=tmp_path / "audit.jsonl",
        session_store_path=tmp_path / "sessions.json",
        approval_store_path=tmp_path / "approvals.json",
        draft_store_path=tmp_path / "drafts.json",
        email_store_path=tmp_path / "emails.json",
        discord_command_store_path=tmp_path / "discord.json",
    )
    write_root_policy_overlay(config, {"discord_operator_user_allowlist": ["user-operator"]})
    sessions = JsonSessionManifestRepository(config.session_store_path)
    approvals = JsonStore(config.approval_store_path, ApprovalRequest)
    engine = PolicyEngine(config, sessions, approvals)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    command = DiscordCommandEnvelope(
        command_id="cmd1",
        author_id="user-operator",
        channel_id="chan-dm",
        content="!run",
        metadata={
            "source": "discord_bot_api",
            "channel_id": "chan-dm",
            "author_id": "user-operator",
        },
    )

    engine.require_discord_command_source(session, command)


def test_require_email_send_enabled_uses_effective_directory_policy(tmp_path: Path) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        audit_log_path=tmp_path / "audit.jsonl",
        session_store_path=tmp_path / "sessions.json",
        approval_store_path=tmp_path / "approvals.json",
        draft_store_path=tmp_path / "drafts.json",
        email_store_path=tmp_path / "emails.json",
        discord_command_store_path=tmp_path / "discord.json",
    )
    write_root_policy_overlay(config, {"email_send_enabled": True})
    sessions = JsonSessionManifestRepository(config.session_store_path)
    approvals = JsonStore(config.approval_store_path, ApprovalRequest)
    engine = PolicyEngine(config, sessions, approvals)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.EMAIL_SEND_APPROVED],
        email_send_enabled=False,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    engine.require_email_send_enabled(session)


def test_require_discord_command_source_rejects_untrusted_source(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
        discord_channel_allowlist=["chan-control"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    command = DiscordCommandEnvelope(
        command_id="cmd1",
        author_id="user-operator",
        channel_id="chan-control",
        content="!run",
        metadata={"source": "stub", "channel_id": "chan-control", "author_id": "user-operator"},
    )

    try:
        engine.require_discord_command_source(session, command)
    except PolicyError as exc:
        assert "untrusted source" in str(exc)
    else:
        raise AssertionError("Expected PolicyError")


def test_require_discord_command_source_rejects_missing_channel_metadata(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
        discord_channel_allowlist=["chan-control"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    command = DiscordCommandEnvelope(
        command_id="cmd1",
        author_id="user-operator",
        channel_id="chan-control",
        content="!run",
        metadata={"source": "discord_bot_api", "channel_id": "", "author_id": "user-operator"},
    )

    with pytest.raises(PolicyError, match="missing required source metadata"):
        engine.require_discord_command_source(session, command)


def test_require_discord_command_source_rejects_missing_author_metadata(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
        discord_channel_allowlist=["chan-control"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    command = DiscordCommandEnvelope(
        command_id="cmd1",
        author_id="user-operator",
        channel_id="chan-control",
        content="!run",
        metadata={"source": "discord_bot_api", "channel_id": "chan-control", "author_id": "  "},
    )

    with pytest.raises(PolicyError, match="missing required source metadata"):
        engine.require_discord_command_source(session, command)


def test_require_discord_command_source_rejects_non_allowlisted_channel_and_author(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
        discord_channel_allowlist=["chan-control"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    command = DiscordCommandEnvelope(
        command_id="cmd1",
        author_id="user-unknown",
        channel_id="chan-random",
        content="!run",
        metadata={
            "source": "discord_bot_api",
            "channel_id": "chan-random",
            "author_id": "user-unknown",
        },
    )

    with pytest.raises(PolicyError, match="source is not allowlisted"):
        engine.require_discord_command_source(session, command)


def test_require_discord_channel_rejects_unrestricted_target(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.DISCORD_POST_STATUS],
        discord_channel_allowlist=["chan-control"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    with pytest.raises(PolicyError, match="not allowlisted"):
        engine.require_discord_channel(session, "chan-unrestricted")


def test_require_discord_channel_allows_communication_target_refs(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    write_root_communication_targets(
        engine.config,
        [
            {
                "id": "discord-ops",
                "provider": "discord",
                "kind": "discord",
                "address": "123456789012345678",
                "policy": {"capabilities": ["read", "write", "reply"]},
            }
        ],
    )
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.DISCORD_POST_STATUS],
        discord_channel_allowlist=["discord-ops"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    assert (
        engine.require_discord_channel(session, "discord-ops", capability="write")
        == "123456789012345678"
    )
    assert (
        engine.require_discord_channel(session, "123456789012345678", capability="write")
        == "123456789012345678"
    )


def test_require_discord_channel_enforces_target_capability(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    write_root_communication_targets(
        engine.config,
        [
            {
                "id": "discord-read",
                "provider": "discord",
                "kind": "discord",
                "address": "123456789012345678",
                "policy": {"capabilities": ["read"]},
            }
        ],
    )
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.DISCORD_POST_STATUS],
        discord_channel_allowlist=["discord-read"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    with pytest.raises(PolicyError, match="does not allow write capability"):
        engine.require_discord_channel(session, "discord-read", capability="write")
    with pytest.raises(PolicyError, match="not allowlisted"):
        engine.require_discord_channel(session, "123456789012345678", capability="write")


@pytest.mark.parametrize(
    ("archived", "locked"),
    [
        (True, False),
        (False, True),
    ],
)
def test_require_discord_channel_allows_reading_but_rejects_replying_to_closed_threads(
    tmp_path: Path,
    archived: bool,
    locked: bool,
) -> None:
    engine = make_engine(tmp_path)
    parent_channel_id = "234567890123456789"
    thread_id = "345678901234567890"
    write_root_communication_targets(
        engine.config,
        [
            {
                "id": "discord-forum",
                "provider": "discord",
                "kind": "discord",
                "address": f"123456789012345678/{parent_channel_id}",
                "policy": {
                    "capabilities": ["read", "reply"],
                    "discord_conversation_scope": "public_threads",
                },
            }
        ],
    )
    DiscordInboxStore(tmp_path / "discord-inbox-store.json").upsert_thread_binding(
        thread_id=thread_id,
        parent_channel_id=parent_channel_id,
        guild_id="123456789012345678",
        thread_type=11,
        archived=archived,
        locked=locked,
        observed_at=datetime(2026, 8, 17, tzinfo=UTC),
    )
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
        communication_target_ids=["discord-forum"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    assert engine.require_discord_channel(session, thread_id, capability="read") == thread_id
    with pytest.raises(PolicyError):
        engine.require_discord_channel(session, thread_id, capability="reply")


def test_require_discord_command_source_rejects_unrestricted_polling_target(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
        discord_channel_allowlist=[],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    command = DiscordCommandEnvelope(
        command_id="cmd1",
        author_id="user-unknown",
        channel_id="chan-unrestricted",
        content="!run",
        metadata={
            "source": "discord_bot_api",
            "channel_id": "chan-unrestricted",
            "author_id": "user-unknown",
        },
    )

    with pytest.raises(PolicyError, match="source is not allowlisted"):
        engine.require_discord_command_source(session, command)


def test_require_discord_command_source_allows_communication_target_refs(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    write_root_communication_targets(
        engine.config,
        [
            {
                "id": "discord-inbox",
                "provider": "discord",
                "kind": "discord",
                "address": "123456789012345678",
                "policy": {"capabilities": ["read"]},
            }
        ],
    )
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
        discord_channel_allowlist=["discord-inbox"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    command = DiscordCommandEnvelope(
        command_id="cmd1",
        author_id="user-unknown",
        channel_id="123456789012345678",
        content="!run",
        metadata={
            "source": "discord_bot_api",
            "channel_id": "123456789012345678",
            "author_id": "user-unknown",
        },
    )

    engine.require_discord_command_source(session, command)


def test_discord_session_allowlist_resolves_communication_target_refs(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    write_root_communication_targets(
        engine.config,
        [
            {
                "id": "discord-inbox",
                "provider": "discord",
                "kind": "discord",
                "address": "123456789012345678",
                "policy": {"capabilities": ["read"]},
            }
        ],
    )
    session = SessionManifest(
        session_id="session-a",
        allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
        discord_channel_allowlist=["discord-inbox"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    engine.session_store.upsert_session(session)

    assert engine.policy_config.resolved_discord_channel_ids_for_session(
        session,
        capability="read",
    ) == ["123456789012345678"]
    assert engine.policy_config.allowed_discord_channel_ids_for_session_id("session-a") == [
        "123456789012345678"
    ]


def test_discord_session_write_allowlist_does_not_require_inbox_tool(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    write_root_communication_targets(
        engine.config,
        [
            {
                "id": "discord-outbound",
                "provider": "discord",
                "kind": "discord",
                "address": "234567890123456789",
                "policy": {"capabilities": ["write"]},
            }
        ],
    )
    session = SessionManifest(
        session_id="session-outbound",
        allowed_tools=[],
        communication_target_ids=["discord-outbound"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    engine.session_store.upsert_session(session)

    assert engine.policy_config.allowed_discord_channel_ids_for_session_id(
        "session-outbound",
        capability="write",
    ) == ["234567890123456789"]
    assert engine.policy_config.allowed_discord_channel_ids_for_session_id("session-outbound") == []


def test_canonical_session_target_ids_do_not_fall_back_to_raw_destinations(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    write_root_communication_targets(
        engine.config,
        [
            {
                "id": "123456789012345678",
                "provider": "discord",
                "kind": "discord",
                "address": "111111111111111111",
                "policy": {"capabilities": ["read"]},
            },
            {
                "id": "chat:stale-chat",
                "provider": "teams",
                "kind": "teams",
                "address": "chat:active-chat",
                "policy": {"capabilities": ["read"]},
            },
            {
                "id": "user:stale-user",
                "provider": "teams",
                "kind": "teams",
                "address": "user:active-user",
                "policy": {"capabilities": ["write"]},
            },
        ],
    )
    session = SessionManifest(
        session_id="canonical-targets",
        allowed_tools=[],
        communication_target_ids=[
            "123456789012345678",
            "chat:stale-chat",
            "user:stale-user",
        ],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    assert engine.policy_config.resolved_discord_channel_ids_for_session(
        session, capability="read"
    ) == ["111111111111111111"]
    assert engine.policy_config.resolved_teams_channel_targets_for_session(
        session, capability="read"
    ) == ["chat:active-chat"]
    assert engine.policy_config.resolved_teams_user_targets_for_session(
        session, capability="write"
    ) == ["user:active-user"]

    write_root_communication_targets(engine.config, [])

    assert (
        engine.policy_config.resolved_discord_channel_ids_for_session(session, capability="read")
        == []
    )
    assert (
        engine.policy_config.resolved_teams_channel_targets_for_session(session, capability="read")
        == []
    )
    assert (
        engine.policy_config.resolved_teams_user_targets_for_session(session, capability="write")
        == []
    )

    legacy_session = SessionManifest(
        session_id="legacy-targets",
        allowed_tools=[],
        discord_channel_allowlist=["123456789012345678"],
        teams_chat_allowlist=["chat:stale-chat"],
        teams_user_allowlist=["user:stale-user"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    assert engine.policy_config.resolved_discord_channel_ids_for_session(
        legacy_session, capability="read"
    ) == ["123456789012345678"]
    assert engine.policy_config.resolved_teams_channel_targets_for_session(
        legacy_session, capability="read"
    ) == ["chat:stale-chat"]
    assert engine.policy_config.resolved_teams_user_targets_for_session(
        legacy_session, capability="write"
    ) == ["user:stale-user"]


def test_require_teams_message_source_allows_session_channel(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.TEAMS_GET_MESSAGES],
        teams_channel_allowlist=["team-a/channel-1"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    message = TeamsMessageEnvelope(
        cursor="team-a/channel-1/msg-1",
        message_id="msg-1",
        author_id="user-1",
        team_id="team-a",
        channel_id="channel-1",
        thread_id="msg-1",
        content="hello",
        metadata={
            "source": "teams_graph_api",
            "team_id": "team-a",
            "channel_id": "channel-1",
            "author_id": "user-1",
        },
    )

    engine.require_teams_message_source(session, message)


def test_require_teams_message_source_rejects_unallowlisted_channel(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.TEAMS_GET_MESSAGES],
        teams_channel_allowlist=["team-a/channel-1"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    message = TeamsMessageEnvelope(
        cursor="team-b/channel-2/msg-1",
        message_id="msg-1",
        author_id="user-1",
        team_id="team-b",
        channel_id="channel-2",
        thread_id="msg-1",
        content="hello",
        metadata={
            "source": "teams_graph_api",
            "team_id": "team-b",
            "channel_id": "channel-2",
            "author_id": "user-1",
        },
    )

    with pytest.raises(PolicyError, match="source is not allowlisted"):
        engine.require_teams_message_source(session, message)


def test_require_teams_message_source_allows_session_chat(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.TEAMS_GET_MESSAGES],
        teams_chat_allowlist=["chat:chat-1"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    message = TeamsMessageEnvelope(
        cursor="chat:chat-1/msg-1",
        message_id="msg-1",
        author_id="user-1",
        chat_id="chat-1",
        content="hello",
        metadata={
            "source": "teams_graph_api",
            "chat_id": "chat-1",
            "author_id": "user-1",
        },
    )

    engine.require_teams_message_source(session, message)


def test_require_teams_message_source_allows_communication_target_refs(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    write_root_communication_targets(
        engine.config,
        [
            {
                "id": "teams-inbox",
                "provider": "teams",
                "kind": "teams",
                "address": "team-a/channel-1",
                "policy": {"capabilities": ["read"]},
            }
        ],
    )
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.TEAMS_GET_MESSAGES],
        teams_channel_allowlist=["teams-inbox"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    message = TeamsMessageEnvelope(
        cursor="team-a/channel-1/msg-1",
        message_id="msg-1",
        author_id="user-1",
        team_id="team-a",
        channel_id="channel-1",
        thread_id="msg-1",
        content="hello",
        metadata={
            "source": "teams_graph_api",
            "team_id": "team-a",
            "channel_id": "channel-1",
            "author_id": "user-1",
        },
    )

    engine.require_teams_message_source(session, message)


def test_require_teams_channel_target_validates_allowlist_and_shape(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.TEAMS_POST_STATUS],
        teams_channel_allowlist=["team-a/channel-1"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    assert engine.require_teams_channel_target(session, "team-a/channel-1") == "team-a/channel-1"
    with pytest.raises(PolicyError, match="expected 'team-id/channel-id'"):
        engine.require_teams_channel_target(session, "channel-1")
    with pytest.raises(PolicyError, match="not allowlisted"):
        engine.require_teams_channel_target(session, "team-b/channel-9")
    with pytest.raises(PolicyError, match="only supported for send operations"):
        engine.require_teams_channel_target(session, "user:operator@example.com")


def test_require_teams_channel_target_allows_chat_allowlist_entries(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.TEAMS_GET_MESSAGES],
        teams_chat_allowlist=["chat:chat-1"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    assert engine.require_teams_channel_target(session, "chat:chat-1") == "chat:chat-1"


def test_require_teams_targets_allow_communication_target_refs(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    write_root_communication_targets(
        engine.config,
        [
            {
                "id": "teams-ops",
                "provider": "teams",
                "kind": "teams",
                "address": "team-a/channel-1",
                "policy": {"capabilities": ["read", "write", "reply"]},
            },
            {
                "id": "teams-user",
                "provider": "teams",
                "kind": "teams",
                "address": "user:operator@example.com",
                "policy": {"capabilities": ["write"]},
            },
        ],
    )
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.TEAMS_POST_STATUS],
        teams_channel_allowlist=["teams-ops"],
        teams_user_allowlist=["teams-user"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    assert engine.require_teams_channel_target(session, "teams-ops") == "team-a/channel-1"
    assert engine.require_teams_send_target(session, "teams-ops") == "team-a/channel-1"
    assert engine.require_teams_send_target(session, "teams-user") == ("user:operator@example.com")
    assert engine.require_teams_send_target(session, "team-a/channel-1") == "team-a/channel-1"


def test_require_teams_targets_enforce_communication_target_capabilities(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    write_root_communication_targets(
        engine.config,
        [
            {
                "id": "teams-write",
                "provider": "teams",
                "kind": "teams",
                "address": "team-a/channel-1",
                "policy": {"capabilities": ["write"]},
            },
            {
                "id": "teams-user-readable",
                "provider": "teams",
                "kind": "teams",
                "address": "user:operator@example.com",
                "policy": {"capabilities": ["read", "write"]},
            },
        ],
    )
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.TEAMS_GET_MESSAGES],
        teams_channel_allowlist=["teams-write"],
        teams_user_allowlist=["teams-user-readable"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    with pytest.raises(PolicyError, match="does not allow read capability"):
        engine.require_teams_channel_target(session, "teams-write")
    with pytest.raises(PolicyError, match="not allowlisted"):
        engine.require_teams_channel_target(session, "team-a/channel-1")
    with pytest.raises(PolicyError, match="only supported for send operations"):
        engine.require_teams_channel_target(session, "teams-user-readable")


def test_require_teams_send_target_allows_declared_user_targets(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.TEAMS_POST_STATUS],
        teams_channel_allowlist=["team-a/channel-1"],
        teams_user_allowlist=["operator@example.com"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    assert engine.require_teams_send_target(session, "team-a/channel-1") == "team-a/channel-1"
    assert engine.require_teams_send_target(session, "user:operator@example.com") == (
        "user:operator@example.com"
    )
    with pytest.raises(PolicyError, match="Teams user target is not allowlisted"):
        engine.require_teams_send_target(session, "user:other@example.com")


def test_require_teams_send_target_allows_chat_allowlist_entries(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.TEAMS_POST_STATUS],
        teams_chat_allowlist=["chat:chat-1"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    assert engine.require_teams_send_target(session, "chat:chat-1") == "chat:chat-1"


def test_session_manifest_preserves_teams_chat_allowlist() -> None:
    manifest = SessionManifest.model_validate(
        {
            "session_id": "s1",
            "allowed_tools": [],
            "teams_chat_allowlist": ["chat:chat-1"],
            "expires_at": "2099-01-01T00:00:00Z",
        }
    )

    assert manifest.teams_chat_allowlist == ["chat:chat-1"]


def test_effective_teams_channel_targets_combines_channel_and_chat_allowlists() -> None:
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.TEAMS_GET_MESSAGES],
        teams_channel_allowlist=["team-a/channel-1", "chat:chat-1", "team-a/channel-1"],
        teams_chat_allowlist=[" chat:chat-1 ", "chat:chat-2"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    assert effective_teams_channel_targets(session) == [
        "team-a/channel-1",
        "chat:chat-1",
        "chat:chat-2",
    ]


def test_effective_teams_channel_targets_returns_chat_only_targets() -> None:
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.TEAMS_GET_MESSAGES],
        teams_chat_allowlist=[" chat:chat-1 "],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    assert effective_teams_channel_targets(session) == ["chat:chat-1"]


def test_normalize_teams_chat_targets_requires_chat_kind() -> None:
    with pytest.raises(ValueError, match="teams_chat_allowlist accepts chat targets only"):
        normalize_teams_chat_targets(["team-a/channel-1"])


def test_require_email_mailbox_and_folder_for_mailbox(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.EMAIL_LIST_UNREAD],
        email_mailbox_allowlist=["primary"],
        email_folder_allowlist_by_mailbox={"primary": ["inbox", "drafts"]},
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    engine.require_email_mailbox(session, "primary")
    engine.require_email_folder_for_mailbox(session, "primary", "inbox")
    with pytest.raises(PolicyError, match="mailbox is not allowlisted"):
        engine.require_email_mailbox(session, "secondary")
    with pytest.raises(PolicyError, match="folder is not allowlisted"):
        engine.require_email_folder_for_mailbox(session, "primary", "sent")


def test_require_tool_for_feedback_tools_requires_stream_metadata(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.session_store.upsert_session(
        SessionManifest(
            session_id="s1",
            allowed_tools=[ToolName.HAS_PENDING_FEEDBACK],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    with pytest.raises(PolicyError, match="missing metadata.pr_feedback_streams"):
        engine.require_tool("s1", ToolName.HAS_PENDING_FEEDBACK)


def test_require_tool_for_feedback_tools_accepts_valid_stream_metadata(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.session_store.upsert_session(
        SessionManifest(
            session_id="s1",
            allowed_tools=[ToolName.LIST_UNREAD_FEEDBACK],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata={
                "pr_feedback_streams": [{"provider": "github", "repo": "org/repo", "pr_id": "17"}]
            },
        ),
    )

    session = engine.require_tool("s1", ToolName.LIST_UNREAD_FEEDBACK)
    streams = engine.require_pr_feedback_streams(session)
    assert streams == [{"provider": "github", "repo": "org/repo", "pr_id": "17"}]


def test_require_tool_list_all_feedback_requires_stream_metadata(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    engine.session_store.upsert_session(
        SessionManifest(
            session_id="s1",
            allowed_tools=[ToolName.LIST_ALL_FEEDBACK],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    with pytest.raises(PolicyError, match="missing metadata.pr_feedback_streams"):
        engine.require_tool("s1", ToolName.LIST_ALL_FEEDBACK)


def test_require_tool_for_pr_metadata_tools_does_not_require_feedback_streams(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    engine.session_store.upsert_session(
        SessionManifest(
            session_id="s1",
            allowed_tools=[
                ToolName.LIST_PULL_REQUESTS,
                ToolName.GET_PULL_REQUEST,
                ToolName.UPDATE_PULL_REQUEST,
            ],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    assert engine.require_tool("s1", ToolName.LIST_PULL_REQUESTS).session_id == "s1"
    assert engine.require_tool("s1", ToolName.GET_PULL_REQUEST).session_id == "s1"
    assert engine.require_tool("s1", ToolName.UPDATE_PULL_REQUEST).session_id == "s1"


def test_update_pull_request_supports_configured_approval_policy(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.UPDATE_PULL_REQUEST],
        approval_required_tools=[ToolName.UPDATE_PULL_REQUEST],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    engine.session_store.upsert_session(session)

    visibility = engine.tool_visibility_for_session(session, ToolName.UPDATE_PULL_REQUEST)

    assert visibility.available is True
    assert visibility.visible is True
    assert visibility.requires_approval is True
    assert visibility.approval_locked is False


def test_require_pr_feedback_stream_enforces_allowlist(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.POST_PR_FEEDBACK_REPLY],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        metadata={
            "pr_feedback_streams": [{"provider": "github", "repo": "org/repo", "pr_id": "17"}]
        },
    )

    engine.require_pr_feedback_stream(session, provider="github", repo="org/repo", pr_id="17")
    with pytest.raises(PolicyError, match="is not allowlisted"):
        engine.require_pr_feedback_stream(session, provider="github", repo="org/repo", pr_id="18")


def test_require_pr_feedback_repo_enforces_repo_allowlist(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.GET_PULL_REQUEST],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        metadata={
            "pr_feedback_streams": [{"provider": "github", "repo": "org/repo", "pr_id": "17"}]
        },
    )

    engine.require_pr_feedback_repo(session, provider="github", repo="org/repo")
    with pytest.raises(PolicyError, match="is not allowlisted"):
        engine.require_pr_feedback_repo(session, provider="github", repo="org/other")


def test_require_draft_recipients_enforces_address_and_domain_allowlists(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    session = SessionManifest(
        session_id="s1",
        allowed_tools=[ToolName.EMAIL_CREATE_DRAFT],
        email_draft_recipient_allowlist=["approved@example.com"],
        email_draft_domain_allowlist=["example.com"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    engine.require_draft_recipients(session, ["approved@example.com"])
    with pytest.raises(PolicyError, match="Draft recipients are not allowlisted"):
        engine.require_draft_recipients(session, ["denied@example.com"])
    with pytest.raises(PolicyError, match="Draft recipient domains are not allowlisted"):
        engine.require_draft_recipients(
            SessionManifest(
                session_id="s2",
                allowed_tools=[ToolName.EMAIL_CREATE_DRAFT],
                email_draft_domain_allowlist=["example.com"],
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            ),
            ["person@other.com"],
        )


def test_canonical_email_mailbox_grants_distinguish_read_draft_and_send(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    read_session = SessionManifest(
        session_id="read",
        allowed_tools=[ToolName.EMAIL_LIST_UNREAD],
        email_mailbox_grants=[
            EmailMailboxGrant(mailbox_id="primary", access="read", folders=["inbox"])
        ],
        expires_at=expires_at,
    )
    engine.require_email_mailbox(read_session, "primary")
    engine.require_email_folder_for_mailbox(read_session, "primary", "inbox")
    with pytest.raises(PolicyError, match="requires draft access"):
        engine.require_email_draft_enabled(read_session, "primary")

    draft_session = read_session.model_copy(
        update={
            "session_id": "draft",
            "email_send_enabled": True,
            "email_mailbox_grants": [
                EmailMailboxGrant(
                    mailbox_id="primary", access=EmailMailboxAccess.DRAFT, folders=["drafts"]
                )
            ],
        }
    )
    engine.require_email_draft_enabled(draft_session, "primary")
    with pytest.raises(PolicyError, match="requires send access"):
        engine.require_email_send_enabled(draft_session, "primary")


def test_explicit_empty_email_mailbox_grants_deny_legacy_mailbox_fallback(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    session = SessionManifest(
        session_id="canonical-empty",
        allowed_tools=[ToolName.EMAIL_LIST_UNREAD],
        email_mailbox_allowlist=["primary"],
        email_mailbox_grants=[],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    with pytest.raises(PolicyError, match="not granted"):
        engine.require_email_mailbox(session, "primary")


def test_email_send_targets_require_matching_recipient_and_mailbox_profile(tmp_path: Path) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        session_store_path=tmp_path / "sessions.json",
        approval_store_path=tmp_path / "approvals.json",
    )
    write_root_communication_targets(
        config,
        [
            {
                "id": "primary-ops",
                "provider": "email",
                "kind": "email",
                "address": "ops@example.com",
                "policy": {"capabilities": ["write"], "auth_profile_ref": "email.primary"},
            },
            {
                "id": "secondary-ops",
                "provider": "email",
                "kind": "email",
                "address": "other@example.com",
                "policy": {"capabilities": ["write"], "auth_profile_ref": "email.secondary"},
            },
        ],
    )
    engine = make_engine_from_config(config)
    session = SessionManifest(
        session_id="send",
        allowed_tools=[ToolName.EMAIL_SEND],
        communication_target_ids=["primary-ops", "secondary-ops"],
        email_mailbox_grants=[
            EmailMailboxGrant(mailbox_id="primary", access="send", folders=["drafts"])
        ],
        email_send_enabled=True,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    assert engine.require_email_send_targets(session, "primary", ["ops@example.com"]) == [
        "primary-ops"
    ]
    with pytest.raises(PolicyError, match="not granted communication targets"):
        engine.require_email_send_targets(session, "primary", ["other@example.com"])


def test_communication_file_policy_denies_disallowed_mime_type(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    session = SessionManifest(
        session_id="s-file-mime",
        allowed_tools=[ToolName.DISCORD_POST_FILE],
        discord_channel_allowlist=["chan-allowed"],
        communication_file_send_enabled=True,
        communication_file_allowed_mime_types=["text/plain"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    with pytest.raises(PolicyError, match="MIME type"):
        engine.require_communication_file_artifacts(
            session,
            [
                {
                    "filename": "report.pdf",
                    "mime_type": "application/pdf",
                    "byte_count": 42,
                }
            ],
            provider="discord",
            channel_id="chan-allowed",
        )


def test_communication_file_policy_denies_oversized_files(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    session = SessionManifest(
        session_id="s-file-size",
        allowed_tools=[ToolName.DISCORD_POST_FILE],
        discord_channel_allowlist=["chan-allowed"],
        communication_file_send_enabled=True,
        communication_file_max_bytes=10,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    with pytest.raises(PolicyError, match="exceeds max bytes"):
        engine.require_communication_file_artifacts(
            session,
            [
                {
                    "filename": "report.txt",
                    "mime_type": "text/plain",
                    "byte_count": 11,
                }
            ],
            provider="discord",
            channel_id="chan-allowed",
        )


def test_communication_file_policy_denies_non_allowlisted_destination(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    session = SessionManifest(
        session_id="s-file-dest",
        allowed_tools=[ToolName.DISCORD_POST_FILE],
        discord_channel_allowlist=["chan-allowed"],
        communication_file_send_enabled=True,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    with pytest.raises(PolicyError, match="Discord channel is not allowlisted"):
        engine.require_communication_file_artifacts(
            session,
            [
                {
                    "filename": "report.txt",
                    "mime_type": "text/plain",
                    "byte_count": 1,
                }
            ],
            provider="discord",
            channel_id="chan-denied",
        )


def test_communication_file_policy_requires_send_approval_when_enabled(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    session = SessionManifest(
        session_id="s-file-approval",
        allowed_tools=[ToolName.APPROVAL_SUBMIT, ToolName.DISCORD_POST_FILE],
        discord_channel_allowlist=["chan-allowed"],
        communication_file_send_enabled=True,
        communication_file_require_approval_for_send=True,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    engine.session_store.upsert_session(session)
    payload = {
        "provider": "discord",
        "channel_id": "chan-allowed",
        "message": "see attached",
        "artifact_ids": ["artifact-1"],
    }

    with pytest.raises(PolicyError, match="requires approval"):
        engine.require_communication_file_send_approval(session, request_id=None, payload=payload)

    approval = engine.create_approval(
        session.session_id,
        "discord_post_file",
        payload,
        "Send report.txt to chan-allowed",
    )
    approval.status = ApprovalStatus.APPROVED
    engine.approval_store.put(approval.request_id, approval)

    approved = engine.require_communication_file_send_approval(
        session,
        request_id=approval.request_id,
        payload=payload,
    )

    assert approved is not None
    assert approved.request_id == approval.request_id


def test_communication_file_policy_rejects_unsafe_filename(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    session = SessionManifest(
        session_id="s-file-name",
        allowed_tools=[ToolName.DISCORD_POST_FILE],
        discord_channel_allowlist=["chan-allowed"],
        communication_file_send_enabled=True,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    with pytest.raises(PolicyError, match="path separators|parent traversal"):
        engine.require_communication_file_artifacts(
            session,
            [
                {
                    "filename": "../secret.txt",
                    "mime_type": "text/plain",
                    "byte_count": 1,
                }
            ],
            provider="discord",
            channel_id="chan-allowed",
        )
