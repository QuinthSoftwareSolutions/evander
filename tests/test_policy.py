from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_sandbox.config import GatewayConfig
from agent_sandbox.directory_config import DirectoryConfigStore
from agent_sandbox.directories import DirectoryStore
from agent_sandbox.models import (
    ApprovalRequest,
    ApprovalStatus,
    DiscordCommandEnvelope,
    SessionManifest,
    TeamsMessageEnvelope,
    ToolName,
)
from agent_sandbox.policy import PolicyEngine, PolicyError, ToolArgumentPolicyError
from agent_sandbox.repositories.session_manifests import JsonSessionManifestRepository
from agent_sandbox.runtime import allowed_tools_for_capability_profile
from agent_sandbox.services import (
    effective_teams_channel_targets,
    normalize_teams_chat_targets,
)
from agent_sandbox.storage import JsonStore


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


def test_require_tool_for_pr_query_tools_does_not_require_feedback_streams(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    engine.session_store.upsert_session(
        SessionManifest(
            session_id="s1",
            allowed_tools=[ToolName.LIST_PULL_REQUESTS, ToolName.GET_PULL_REQUEST],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    assert engine.require_tool("s1", ToolName.LIST_PULL_REQUESTS).session_id == "s1"
    assert engine.require_tool("s1", ToolName.GET_PULL_REQUEST).session_id == "s1"


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
        engine.require_communication_file_send_approval(
            session, request_id=None, payload=payload
        )

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
