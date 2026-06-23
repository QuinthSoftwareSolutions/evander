from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from truman_team_addon.models import (
    AgentAvatar,
    AgentContextClearingPolicy,
    AgentInboxItem,
    AgentRuntime,
    AgentStandDownSource,
    AgentStatus,
    AgentVoiceProfile,
    EphemeralAgentPolicy,
    EphemeralAssignmentState,
    GovernanceAgendaItemType,
    GovernanceAgendaItemState,
    GovernanceRuntime,
    ProjectGitConfig,
    ProjectGoal,
    ProjectStatus,
    ReviewProfileCriterion,
    RolePermission,
    TeamInboxItemState,
    TeamInboxPriority,
    TeamInboxItemType,
    TeamRecordState,
    TeamTaskState,
    agent_project_ids,
    project_lead_agent_id,
)
from truman_team_addon.repository import TeamStateConflictError, TeamStateRepository
from truman_team_addon.sqlite_store import (
    SQLITE_SCHEMA_VERSION,
    _ensure_team_conversations_operator_scope,
)


def _seed_hierarchy(tmp_path: Path) -> TeamStateRepository:
    """manager -> lead (project) -> backend -> worker, all in one team/project."""

    repo = TeamStateRepository(tmp_path)
    repo.create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
        manager_display_name="  Team Manager  ",
        expected_revision=0,
    )
    repo.create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
        lead_display_name="Project Lead",
        goals=[ProjectGoal(id="goal", description="Ship work")],
        expected_revision=repo.read().revision,
    )
    repo.create_role(
        role_id="project__backend",
        team_id="team",
        project_id="project",
        name="Backend",
        expected_revision=repo.read().revision,
    )
    repo.create_role(
        role_id="project__worker",
        team_id="team",
        project_id="project",
        name="Worker",
        expected_revision=repo.read().revision,
    )
    repo.register_agent(
        agent_id="backend",
        team_id="team",
        project_id="project",
        role_id="project__backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
        display_name="Backend Agent",
        expected_revision=repo.read().revision,
    )
    repo.register_agent(
        agent_id="worker",
        team_id="team",
        project_id="project",
        role_id="project__worker",
        session_id="session-worker",
        reports_to_agent_id="backend",
        display_name="  Worker Agent  ",
        expected_revision=repo.read().revision,
    )
    return repo


def _register_release_operator(
    repo: TeamStateRepository,
    *,
    role_id: str = "project__release-operator",
    permissions: list[str] | None = None,
) -> None:
    if role_id not in repo.read().roles:
        repo.create_role(
            role_id=role_id,
            team_id="team",
            project_id="project",
            name="Release Operator",
            permissions=permissions if permissions is not None else ["request_release"],
            expected_revision=repo.read().revision,
        )
    repo.register_agent(
        agent_id="release-operator",
        team_id="team",
        project_id="project",
        role_id=role_id,
        session_id="session-release-operator",
        reports_to_agent_id="lead",
        expected_revision=repo.read().revision,
    )


def _auto_dev_squad_inputs(
    *,
    suffix: str = "auto-dev-squad",
    duplicate_reviewer_id: str | None = None,
    bad_git: bool = False,
) -> dict[str, object]:
    reviewer_agent_id = duplicate_reviewer_id or f"reviewer-{suffix}"
    project_git: dict[str, object] = {
        "host_repo_path": f"/srv/repos/{suffix}",
        "bare_repo_name": f"{suffix}.git",
    }
    if bad_git:
        project_git["bare_repo_path"] = f"/srv/git/{suffix}.git"
    return {
        "team": {
            "id": f"team-{suffix}",
            "name": "Auto-Dev Squad",
            "operator_communication_target_id": "slack://ops-channel",
        },
        "project": {
            "id": f"project-{suffix}",
            "name": "Reviewed Auto-Dev Delivery",
            "git": project_git,
        },
        "manager": {
            "agent_id": f"manager-{suffix}",
            "session_id": f"session-manager-{suffix}",
        },
        "project_lead": {
            "agent_id": f"project-lead-{suffix}",
            "session_id": f"session-project-lead-{suffix}",
        },
        "implementer": {
            "agent_id": f"implementer-{suffix}",
            "session_id": f"session-implementer-{suffix}",
        },
        "reviewer": {
            "agent_id": reviewer_agent_id,
            "session_id": f"session-reviewer-{suffix}",
        },
        "release_operator": {
            "agent_id": f"release-operator-{suffix}",
            "session_id": f"session-release-operator-{suffix}",
        },
    }


def test_repository_initializes_private_addon_state(tmp_path: Path) -> None:
    repo = TeamStateRepository(tmp_path)

    state = repo.read()

    assert state.revision == 0
    assert state.teams == {}
    assert (tmp_path / "addons" / "team-addon" / "state.db").is_file()
    assert not (tmp_path / "addons" / "team-addon" / "state.json").exists()


def test_repository_round_trips_agent_avatar_metadata(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    avatar = AgentAvatar(
        asset_id="av_0123456789abcdef0123456789abcdef",
        source="uploaded",
        mime_type="image/webp",
        sha256="a" * 64,
    )

    state = repo.set_agent_avatar(
        agent_id="backend",
        avatar=avatar,
        expected_revision=repo.read().revision,
    )

    assert state.agents["backend"].avatar == avatar
    reread = TeamStateRepository(tmp_path).read()
    assert reread.agents["backend"].avatar is not None
    assert reread.agents["backend"].avatar.asset_id == avatar.asset_id
    assert reread.agents["worker"].avatar is None


def test_repository_round_trips_agent_voice_profile(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    voice_profile = AgentVoiceProfile(
        voice="cedar",
        speaking_style="Calm, concise, and lightly upbeat.",
    )

    state = repo.update_agent(
        agent_id="backend",
        voice_profile=voice_profile,
        set_voice_profile=True,
        expected_revision=repo.read().revision,
    )

    assert state.agents["backend"].voice_profile == voice_profile
    reread = TeamStateRepository(tmp_path).read()
    assert reread.agents["backend"].voice_profile is not None
    assert reread.agents["backend"].voice_profile.voice == "cedar"
    assert reread.agents["backend"].voice_profile.speaking_style == (
        "Calm, concise, and lightly upbeat."
    )

    cleared = TeamStateRepository(tmp_path).update_agent(
        agent_id="backend",
        voice_profile=AgentVoiceProfile(),
        set_voice_profile=True,
        expected_revision=reread.revision,
    )

    assert cleared.agents["backend"].voice_profile is None


def test_repository_round_trips_team_chat_conversation_and_reply(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, conversation, created = repo.create_or_get_conversation(
        agent_id="backend",
        created_by="operator:alice",
        expected_revision=repo.read().revision,
    )
    assert created is True
    state, message = repo.record_operator_chat_message(
        conversation_id=conversation.id,
        body="Can you summarize the blocker?",
        created_by="operator:alice",
        expected_revision=state.revision,
    )
    state, message = repo.begin_chat_message_dispatch(
        message_id=message.id,
        dispatch_id="team-chat:test",
        lease_seconds=60,
    )

    assert message.status == "running"
    assert message.dispatch_id == "team-chat:test"
    assert state.agent_runtimes["backend"].active_dispatch_id == "team-chat:test"
    assert state.agent_runtimes["backend"].wake_reason == "team_chat"

    reread = TeamStateRepository(tmp_path).read()
    assert reread.conversations[conversation.id].agent_id == "backend"
    assert reread.conversation_messages[message.id].body == (
        "Can you summarize the blocker?"
    )

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "dispatch_id": "team-chat:test",
            "chat_replies": [
                {
                    "conversation_id": conversation.id,
                    "message_id": message.id,
                    "body": "The dependency task is still open.",
                }
            ],
        },
        dispatch_id="team-chat:test",
        expected_revision=repo.read().revision,
    )

    assert result["success"] is True
    assert result["applied"] is True
    assert result["created_chat_reply_message_ids"]
    assert result["replied_chat_message_ids"] == [message.id]
    assert state.conversation_messages[message.id].status == "replied"
    reply = state.conversation_messages[result["created_chat_reply_message_ids"][0]]
    assert reply.sender == "agent"
    assert reply.source_message_id == message.id
    assert reply.body == "The dependency task is still open."
    assert state.conversations[conversation.id].last_message_preview == (
        "The dependency task is still open."
    )
    assert state.agent_runtimes["backend"].active_dispatch_id is None
    assert state.agent_runtimes["backend"].wake_leased_until is None


def test_repository_fails_active_team_chat_message_when_reply_item_is_invalid(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    state, conversation, _created = repo.create_or_get_conversation(
        agent_id="backend",
        expected_revision=repo.read().revision,
    )
    state, message = repo.record_operator_chat_message(
        conversation_id=conversation.id,
        body="Can you summarize the blocker?",
        expected_revision=state.revision,
    )
    dispatch_id = "team-chat:test"
    state, message = repo.begin_chat_message_dispatch(
        message_id=message.id,
        dispatch_id=dispatch_id,
        lease_seconds=60,
    )

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "dispatch_id": dispatch_id,
            "chat_replies": [
                {
                    "conversation_id": conversation.id,
                    "message_id": message.id,
                }
            ],
        },
        dispatch_id=dispatch_id,
        expected_revision=repo.read().revision,
    )

    assert result["success"] is False
    assert result["applied"] is False
    assert result["created_chat_reply_message_ids"] == []
    failed_message = state.conversation_messages[message.id]
    assert failed_message.status == "failed"
    assert "chat_replies[0]" in (failed_message.error or "")
    assert state.agent_runtimes["backend"].active_dispatch_id is None
    assert state.conversations[conversation.id].last_message_preview.startswith("Reply failed:")


def test_repository_records_conversation_voice_call_idempotently(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    state, conversation, _created = repo.create_or_get_conversation(
        agent_id="backend",
        expected_revision=repo.read().revision,
    )
    state, first_message = repo.record_conversation_voice_call(
        conversation_id=conversation.id,
        voice_session_id="voice-1",
        status="running",
        summary="Voice call with Backend Agent",
    )
    revision = state.revision

    state, second_message = repo.record_conversation_voice_call(
        conversation_id=conversation.id,
        voice_session_id="voice-1",
        status="running",
        summary="Voice call with Backend Agent",
    )

    assert second_message.id == first_message.id
    assert state.revision == revision
    assert [
        message.id
        for message in state.conversation_messages.values()
        if message.voice_session_id == "voice-1"
    ] == [first_message.id]


def test_repository_gets_existing_team_chat_conversation_without_revision_bump(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    state, conversation, created = repo.create_or_get_conversation(
        agent_id="backend",
        created_by="operator:alice",
        expected_revision=repo.read().revision,
    )
    assert created is True
    revision = state.revision

    state, same_conversation, created = repo.create_or_get_conversation(
        agent_id="backend",
        created_by="operator:alice",
        expected_revision=revision,
    )

    assert created is False
    assert same_conversation.id == conversation.id
    assert state.revision == revision
    assert repo.read().revision == revision


def test_repository_creates_separate_team_chat_conversations_per_operator(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    state, alice_conversation, created = repo.create_or_get_conversation(
        agent_id="backend",
        operator_user_id="alice@example.com",
        operator_display_name="Alice",
        created_by="operator:alice@example.com",
        expected_revision=repo.read().revision,
    )
    assert created is True

    state, bob_conversation, created = repo.create_or_get_conversation(
        agent_id="backend",
        operator_user_id="bob@example.com",
        operator_display_name="Bob",
        created_by="operator:bob@example.com",
        expected_revision=state.revision,
    )

    assert created is True
    assert bob_conversation.id != alice_conversation.id
    assert state.conversations[alice_conversation.id].operator_user_id == "alice@example.com"
    assert state.conversations[bob_conversation.id].operator_user_id == "bob@example.com"


def test_repository_rejects_chat_reply_from_wrong_agent(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    state, conversation, _created = repo.create_or_get_conversation(
        agent_id="backend",
        expected_revision=repo.read().revision,
    )
    state, message = repo.record_operator_chat_message(
        conversation_id=conversation.id,
        body="Are you blocked?",
        expected_revision=state.revision,
    )

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "chat_replies": [
                {
                    "conversation_id": conversation.id,
                    "message_id": message.id,
                    "body": "I cannot answer for that agent.",
                }
            ],
        },
        expected_revision=state.revision,
    )

    assert result["success"] is False
    assert not result["created_chat_reply_message_ids"]
    assert "chat_replies[0]" in result["errors"][0]
    assert state.conversation_messages[message.id].status == "created"


def test_repository_agent_action_sends_chat_message_to_known_operator(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    state, _conversation, _created = repo.create_or_get_conversation(
        agent_id="lead",
        operator_user_id="alice@example.com",
        operator_display_name="Alice",
        expected_revision=repo.read().revision,
    )

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "operator_chat_messages": [
                {
                    "recipient_operator_user_id": "alice@example.com",
                    "body": "I found the issue.",
                    "summary": "Issue found",
                }
            ],
        },
        expected_revision=state.revision,
    )

    assert result["success"] is True
    assert result["created_operator_chat_message_ids"]
    message = state.conversation_messages[result["created_operator_chat_message_ids"][0]]
    conversation = state.conversations[message.conversation_id]
    assert conversation.agent_id == "backend"
    assert conversation.operator_user_id == "alice@example.com"
    assert message.sender == "agent"
    assert message.body == "I found the issue."
    assert message.metadata["recipient_operator_user_id"] == "alice@example.com"


def test_repository_agent_action_rejects_unknown_operator_chat_recipient(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "operator_chat_messages": [
                {
                    "recipient_operator_user_id": "nobody@example.com",
                    "body": "I found the issue.",
                }
            ],
        },
        expected_revision=repo.read().revision,
    )

    assert result["success"] is False
    assert result["created_operator_chat_message_ids"] == []
    assert "operator_chat_messages[0]" in result["errors"][0]
    assert state.conversation_messages == {}


def test_repository_migrates_action_feedback_inbox_and_low_priority_columns(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "addons" / "team-addon" / "state.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE team_state_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT INTO team_state_metadata(key, value)
            VALUES ('sqlite_schema_version', '1');
            CREATE TABLE team_action_records (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                dispatch_id TEXT NOT NULL,
                action_json TEXT NOT NULL,
                success INTEGER NOT NULL,
                error TEXT,
                created_at TEXT NOT NULL,
                dismissed_at TEXT,
                dismissed_by TEXT,
                dismissed_reason TEXT
            );
            """
        )

    repo = TeamStateRepository(tmp_path)

    assert repo.read().action_records == {}
    with sqlite3.connect(db_path) as connection:
        action_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(team_action_records)")
        }
        inbox_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(team_inbox)")
        }
        runtime_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(team_agent_runtimes)")
        }
        agent_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(team_agents)")
        }
        governance_runtime_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(team_governance_runtimes)")
        }
        task_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(team_tasks)")
        }
        metadata = dict(
            connection.execute("SELECT key, value FROM team_state_metadata").fetchall()
        )
    assert "feedback_delivered_at" in action_columns
    assert "feedback_delivered_by_dispatch_id" in action_columns
    assert "response_requested" in inbox_columns
    assert "priority" in inbox_columns
    assert "low_priority_batch_started_at" in runtime_columns
    assert "low_priority_batch_cycle_count" in runtime_columns
    assert "low_priority_batch_due_after_cycles" in runtime_columns
    assert "context_clearing_policy" in runtime_columns
    assert "voice_profile_json" in agent_columns
    assert "last_cost_signal_fingerprints_json" in governance_runtime_columns
    assert "last_blocked_signal_fingerprints_json" in governance_runtime_columns
    assert "state_entered_at" in task_columns
    assert metadata["sqlite_schema_version"] == str(SQLITE_SCHEMA_VERSION)


def test_repository_migrates_global_chat_conversation_unique_constraint(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "addons" / "team-addon" / "state.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE team_state_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT INTO team_state_metadata(key, value)
            VALUES ('sqlite_schema_version', '11');
            CREATE TABLE team_conversations (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL UNIQUE,
                created_by TEXT,
                last_message_preview TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_interaction_at TEXT NOT NULL
            );
            """
        )

    TeamStateRepository(tmp_path)

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(team_conversations)")
        }
        unique_indexes = [
            str(row["name"])
            for row in connection.execute("PRAGMA index_list(team_conversations)")
            if int(row["unique"] or 0)
        ]
        unique_index_columns = [
            [
                str(info_row["name"])
                for info_row in connection.execute(f"PRAGMA index_info({index_name})")
            ]
            for index_name in unique_indexes
        ]

    assert "operator_user_id" in columns
    assert "operator_display_name" in columns
    assert ["agent_id"] not in unique_index_columns
    assert ["agent_id", "operator_user_id"] in unique_index_columns


def test_repository_migrates_legacy_chat_operator_ids_to_lowercase() -> None:
    with sqlite3.connect(":memory:") as connection:
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE team_conversations (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL UNIQUE,
                created_by TEXT,
                last_message_preview TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_interaction_at TEXT NOT NULL
            );
            INSERT INTO team_conversations(
                id, agent_id, created_by, last_message_preview,
                created_at, updated_at, last_interaction_at
            )
            VALUES (
                'conversation-backend',
                'backend',
                'operator:Alice@Example.com',
                '',
                '2026-01-01T00:00:00+00:00',
                '2026-01-01T00:00:00+00:00',
                '2026-01-01T00:00:00+00:00'
            );
            """
        )

        _ensure_team_conversations_operator_scope(connection)
        row = connection.execute(
            """
            SELECT operator_user_id
            FROM team_conversations
            WHERE id = 'conversation-backend'
            """
        ).fetchone()

    assert row["operator_user_id"] == "alice@example.com"


def test_repository_normalizes_existing_chat_operator_ids_to_lowercase() -> None:
    with sqlite3.connect(":memory:") as connection:
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE team_conversations (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                operator_user_id TEXT NOT NULL,
                operator_display_name TEXT,
                created_by TEXT,
                last_message_preview TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_interaction_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX idx_team_conversations_agent_operator
            ON team_conversations(agent_id, operator_user_id);
            INSERT INTO team_conversations(
                id, agent_id, operator_user_id, operator_display_name, created_by,
                last_message_preview, created_at, updated_at, last_interaction_at
            )
            VALUES (
                'conversation-backend',
                'backend',
                'Jonathan',
                'Jonathan',
                'operator:Jonathan',
                '',
                '2026-01-01T00:00:00+00:00',
                '2026-01-01T00:00:00+00:00',
                '2026-01-01T00:00:00+00:00'
            );
            """
        )

        _ensure_team_conversations_operator_scope(connection)
        row = connection.execute(
            """
            SELECT operator_user_id, operator_display_name, created_by
            FROM team_conversations
            WHERE id = 'conversation-backend'
            """
        ).fetchone()

    assert row["operator_user_id"] == "jonathan"
    assert row["operator_display_name"] == "Jonathan"
    assert row["created_by"] == "operator:Jonathan"


def test_repository_imports_legacy_json_state_once(tmp_path: Path) -> None:
    legacy_path = tmp_path / "addons" / "team-addon" / "state.json"
    legacy_path.parent.mkdir(parents=True)
    state = _seed_hierarchy(tmp_path / "seed").read()
    legacy_path.write_text(
        json.dumps(state.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
    )

    repo = TeamStateRepository(tmp_path)

    imported = repo.read()
    assert imported.revision == state.revision
    assert imported.teams.keys() == state.teams.keys()
    assert imported.projects.keys() == state.projects.keys()
    assert legacy_path.is_file()
    with sqlite3.connect(tmp_path / "addons" / "team-addon" / "state.db") as connection:
        metadata = dict(
            connection.execute("SELECT key, value FROM team_state_metadata").fetchall()
        )
    assert metadata["legacy_json_path"] == str(legacy_path)
    assert len(metadata["legacy_json_sha256"]) == 64


def test_task_state_entered_at_changes_only_on_state_transition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    moments = iter(
        [
            datetime(2026, 6, 21, 10, 0, tzinfo=UTC),
            datetime(2026, 6, 21, 10, 0, 1, tzinfo=UTC),
            datetime(2026, 6, 21, 10, 1, tzinfo=UTC),
            datetime(2026, 6, 21, 10, 1, 1, tzinfo=UTC),
            datetime(2026, 6, 21, 10, 2, tzinfo=UTC),
            datetime(2026, 6, 21, 10, 2, 1, tzinfo=UTC),
        ]
    )
    monkeypatch.setattr("truman_team_addon.repository.utc_now", lambda: next(moments))

    state = repo.create_or_reprioritize_task(
        task_id="board-task",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Build board",
        description="Add a task board.",
        created_by_agent_id="lead",
        expected_revision=repo.read().revision,
    )
    original_entered_at = state.tasks["board-task"].state_entered_at
    assert original_entered_at == datetime(2026, 6, 21, 10, 0, tzinfo=UTC)

    state = repo.update_task(
        task_id="board-task",
        title="Build project board",
        expected_revision=state.revision,
    )
    assert state.tasks["board-task"].state_entered_at == original_entered_at

    state = repo.update_task(
        task_id="board-task",
        state=TeamTaskState.WAITING_FOR_REVIEW,
        expected_revision=state.revision,
    )
    assert state.tasks["board-task"].state_entered_at == datetime(
        2026,
        6,
        21,
        10,
        2,
        tzinfo=UTC,
    )


def test_repository_prefers_sqlite_when_legacy_json_also_exists(tmp_path: Path) -> None:
    repo = TeamStateRepository(tmp_path)
    repo.create_team(
        team_id="db-team",
        name="DB Team",
        manager_agent_id="db-manager",
        manager_session_id="session-db-manager",
        expected_revision=0,
    )
    legacy_path = tmp_path / "addons" / "team-addon" / "state.json"
    legacy_state = TeamStateRepository(tmp_path / "legacy").create_team(
        team_id="json-team",
        name="JSON Team",
        manager_agent_id="json-manager",
        manager_session_id="session-json-manager",
        expected_revision=0,
    )
    legacy_path.write_text(
        json.dumps(legacy_state.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
    )

    reread = TeamStateRepository(tmp_path).read()

    assert set(reread.teams) == {"db-team"}
    assert "json-team" not in reread.teams


def test_repository_writes_increment_revision_and_preserve_hierarchy(tmp_path: Path) -> None:
    repo = TeamStateRepository(tmp_path)

    state = repo.create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
        manager_display_name="  Team Manager  ",
        expected_revision=0,
    )
    assert state.revision == 1
    assert state.agents["manager"].display_name == "Team Manager"
    assert state.roles["team__manager"].permissions == [
        RolePermission.CREATE_ROOT_TASK
    ]

    state = repo.create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
        lead_display_name="Project Lead",
        goals=[ProjectGoal(id="goal", description="Complete project")],
        expected_revision=1,
    )
    assert state.revision == 2
    assert state.agents["lead"].display_name == "Project Lead"
    assert state.roles["project__project-lead"].permissions == [
        RolePermission.CREATE_ROOT_TASK
    ]

    repo.create_role(
        role_id="project__backend",
        team_id="team",
        project_id="project",
        name="Backend",
        expected_revision=2,
    )
    state = repo.register_agent(
        agent_id="backend",
        team_id="team",
        project_id="project",
        role_id="project__backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
        display_name="  Backend Agent  ",
        expected_revision=3,
    )
    assert state.revision == 4
    assert state.agents["manager"].subordinate_agent_ids == ["lead"]
    assert state.agents["lead"].subordinate_agent_ids == ["backend"]
    assert state.agents["backend"].display_name == "Backend Agent"


def test_create_project_can_assign_manager_as_project_lead_overlay(tmp_path: Path) -> None:
    repo = TeamStateRepository(tmp_path)
    state = repo.create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
        expected_revision=0,
    )
    manager_before = state.agents["manager"].model_copy(deep=True)

    state = repo.create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="manager",
        lead_session_id=None,
        expected_revision=state.revision,
    )

    assert state.projects["project"].lead_agent_id == "manager"
    assert project_lead_agent_id(state, "project") == "manager"
    assert agent_project_ids(state, "manager") == ["project"]
    assert state.agents["manager"].role_id == manager_before.role_id
    assert state.agents["manager"].reports_to_agent_id == manager_before.reports_to_agent_id
    assert state.agents["manager"].session_id == manager_before.session_id
    assert "manager" not in state.agents["manager"].subordinate_agent_ids
    assert state.roles["project__project-lead"].permissions == [
        RolePermission.CREATE_ROOT_TASK
    ]


def test_create_project_existing_project_lead_is_assignment_overlay(
    tmp_path: Path,
) -> None:
    repo = TeamStateRepository(tmp_path)
    state = repo.create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
        expected_revision=0,
    )
    state = repo.create_role(
        role_id="team__lead-candidate",
        team_id="team",
        name="Lead Candidate",
        expected_revision=state.revision,
    )
    state = repo.register_agent(
        agent_id="candidate",
        team_id="team",
        project_id=None,
        role_id="team__lead-candidate",
        session_id="session-candidate",
        reports_to_agent_id="manager",
        expected_revision=state.revision,
    )
    candidate_before = state.agents["candidate"].model_copy(deep=True)

    state = repo.create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="candidate",
        lead_session_id=None,
        expected_revision=state.revision,
    )

    assert state.projects["project"].lead_agent_id == "candidate"
    assert project_lead_agent_id(state, "project") == "candidate"
    assert agent_project_ids(state, "candidate") == ["project"]
    assert state.agents["candidate"].role_id == candidate_before.role_id
    assert state.agents["candidate"].reports_to_agent_id == candidate_before.reports_to_agent_id
    assert state.agents["candidate"].session_id == candidate_before.session_id

    def _add_candidate_inbox(state):
        state.inbox["candidate-inbox"] = AgentInboxItem(
            id="candidate-inbox",
            recipient_agent_id="candidate",
            item_type=TeamInboxItemType.PROGRESS_REPORT,
            priority=TeamInboxPriority.NORMAL,
            state=TeamInboxItemState.PENDING,
        )
        return state

    repo.update(_add_candidate_inbox, expected_revision=state.revision)
    state, leases = repo.lease_due_agents(agent_id="candidate")
    assert len(leases) == 1
    assert "create_root_task" in leases[0]["agent"]["effective_role_permissions"]

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "candidate",
            "dispatch_id": leases[0]["dispatch_id"],
            "task_creations": [
                {
                    "task_id": "root-task",
                    "owner_agent_id": "candidate",
                    "project_id": "project",
                    "title": "Root task",
                    "description": "Create a root task as project lead.",
                }
            ],
        },
        expected_revision=state.revision,
    )

    assert result["success"] is True
    assert result["created_task_ids"] == ["root-task"]
    assert state.tasks["root-task"].created_by_agent_id == "candidate"


def test_create_project_allows_same_agent_to_lead_multiple_projects(
    tmp_path: Path,
) -> None:
    repo = TeamStateRepository(tmp_path)
    state = repo.create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
        expected_revision=0,
    )
    state = repo.create_project(
        project_id="first-project",
        team_id="team",
        name="First Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
        expected_revision=state.revision,
    )

    state = repo.create_project(
        project_id="second-project",
        team_id="team",
        name="Second Project",
        lead_agent_id="lead",
        lead_session_id=None,
        expected_revision=state.revision,
    )

    assert project_lead_agent_id(state, "first-project") == "lead"
    assert project_lead_agent_id(state, "second-project") == "lead"
    assert agent_project_ids(state, "lead") == ["first-project", "second-project"]


def test_update_team_can_reassign_manager_to_existing_team_agent(tmp_path: Path) -> None:
    repo = TeamStateRepository(tmp_path)

    repo.create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
        expected_revision=0,
    )
    repo.create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
        expected_revision=1,
    )
    repo.create_role(
        role_id="team__ops",
        team_id="team",
        name="Ops",
        expected_revision=2,
    )
    repo.create_role(
        role_id="project__backend",
        team_id="team",
        project_id="project",
        name="Backend",
        expected_revision=3,
    )
    repo.register_agent(
        agent_id="ops",
        team_id="team",
        role_id="team__ops",
        session_id="session-ops",
        reports_to_agent_id="manager",
        expected_revision=4,
    )
    repo.register_agent(
        agent_id="backend",
        team_id="team",
        project_id="project",
        role_id="project__backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
        expected_revision=5,
    )

    state = repo.update_team(
        team_id="team",
        manager_agent_id="ops",
        expected_revision=6,
    )

    assert state.teams["team"].manager_agent_id == "ops"
    assert state.agents["ops"].reports_to_agent_id is None
    assert state.agents["ops"].subordinate_agent_ids == ["lead"]
    assert state.agents["manager"].reports_to_agent_id is None
    assert state.agents["manager"].subordinate_agent_ids == []
    assert state.agents["lead"].reports_to_agent_id == "ops"
    assert state.agents["lead"].subordinate_agent_ids == ["backend"]
    assert RolePermission.CREATE_ROOT_TASK in state.roles["team__ops"].permissions


def test_reassigned_project_lead_manager_can_create_tasks_across_team_projects(
    tmp_path: Path,
) -> None:
    repo = TeamStateRepository(tmp_path)

    repo.create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
        expected_revision=0,
    )
    repo.create_project(
        project_id="project-a",
        team_id="team",
        name="Project A",
        lead_agent_id="lead-a",
        lead_session_id="session-lead-a",
        expected_revision=1,
    )
    repo.create_project(
        project_id="project-b",
        team_id="team",
        name="Project B",
        lead_agent_id="lead-b",
        lead_session_id="session-lead-b",
        expected_revision=2,
    )
    repo.update_team(
        team_id="team",
        manager_agent_id="lead-a",
        expected_revision=3,
    )

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead-a",
            "task_creations": [
                {
                    "id": "cross-project-task",
                    "owner_agent_id": "lead-b",
                    "project_id": "project-b",
                    "priority": 1,
                    "title": "Coordinate project B",
                    "description": "Promoted manager assigns the other project lead.",
                }
            ],
        },
    )

    assert result["success"] is True
    assert result["errors"] == []
    assert state.tasks["cross-project-task"].owner_agent_id == "lead-b"
    assert state.tasks["cross-project-task"].created_by_agent_id == "lead-a"


def test_update_team_rejects_invalid_manager_reassignment(tmp_path: Path) -> None:
    from truman_team_addon.models import AgentStatus

    repo = TeamStateRepository(tmp_path)
    repo.create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
        expected_revision=0,
    )
    repo.create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
        expected_revision=1,
    )
    repo.create_role(
        role_id="project__backend",
        team_id="team",
        project_id="project",
        name="Backend",
        expected_revision=2,
    )
    repo.register_agent(
        agent_id="backend",
        team_id="team",
        project_id="project",
        role_id="project__backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
        expected_revision=3,
    )
    repo.create_team(
        team_id="other",
        name="Other",
        manager_agent_id="other-manager",
        manager_session_id="session-other-manager",
        expected_revision=4,
    )

    with pytest.raises(ValueError, match="manager agent missing"):
        repo.update_team(
            team_id="team",
            manager_agent_id="missing",
            expected_revision=5,
        )
    with pytest.raises(ValueError, match="manager agent other-manager is not in team team"):
        repo.update_team(
            team_id="team",
            manager_agent_id="other-manager",
            expected_revision=5,
        )
    with pytest.raises(ValueError, match="nested under current manager"):
        repo.update_team(
            team_id="team",
            manager_agent_id="backend",
            expected_revision=5,
        )

    def _pause_lead(state):
        state.agents["lead"].status = AgentStatus.PAUSED
        return state

    repo.update(_pause_lead)
    with pytest.raises(ValueError, match="manager agent lead is not active"):
        repo.update_team(
            team_id="team",
            manager_agent_id="lead",
            expected_revision=6,
        )


def test_repository_generates_missing_operator_ids_with_collision_suffixes(
    tmp_path: Path,
) -> None:
    repo = TeamStateRepository(tmp_path)

    state = repo.create_team(
        team_id=None,
        name="Platform Team",
        manager_agent_id=None,
        manager_session_id="session-manager",
        manager_display_name="Platform Manager",
    )
    assert "team-platform-team" in state.teams
    assert "team-platform-team__manager" in state.roles
    assert "agent-team-platform-team-platform-manager" in state.agents

    state = repo.create_project(
        project_id=None,
        team_id="team-platform-team",
        name="Runtime Upgrade",
        lead_agent_id=None,
        lead_session_id="session-lead",
        lead_display_name="Project Lead",
        goals=[{"description": "Ship runtime upgrade"}],
        expected_revision=state.revision,
    )
    project_id = "project-team-platform-team-runtime-upgrade"
    assert project_id in state.projects
    assert state.projects[project_id].lead_agent_id == (
        "agent-project-team-platform-team-runtime-upgrade-project-lead"
    )
    assert state.projects[project_id].goals[0].id == (
        "goal-project-team-platform-team-runtime-upgrade-ship-runtime-upgrade"
    )

    state = repo.create_role(
        role_id=None,
        team_id="team-platform-team",
        project_id=project_id,
        name="Backend",
        expected_revision=state.revision,
    )
    state = repo.create_role(
        role_id=None,
        team_id="team-platform-team",
        project_id=project_id,
        name="Backend",
        expected_revision=state.revision,
    )
    assert "role-project-team-platform-team-runtime-upgrade-backend" in state.roles
    assert "role-project-team-platform-team-runtime-upgrade-backend-2" in state.roles

    state = repo.register_agent(
        agent_id=None,
        team_id="team-platform-team",
        project_id=project_id,
        role_id="role-project-team-platform-team-runtime-upgrade-backend",
        session_id="session-backend",
        reports_to_agent_id="agent-project-team-platform-team-runtime-upgrade-project-lead",
        display_name="Backend Engineer",
        expected_revision=state.revision,
    )
    agent_id = "agent-project-team-platform-team-runtime-upgrade-backend-engineer"
    assert agent_id in state.agents

    state = repo.create_or_reprioritize_task(
        task_id=None,
        owner_agent_id=agent_id,
        project_id=project_id,
        priority=10,
        title="Implement API",
        description="Implement the runtime API.",
        created_by_agent_id=state.projects[project_id].lead_agent_id,
        expected_revision=state.revision,
    )
    task_id = "task-project-team-platform-team-runtime-upgrade-implement-api"
    assert task_id in state.tasks

    state = repo.record_deliverable(
        deliverable_id=None,
        task_id=task_id,
        producer_agent_id=agent_id,
        summary="API patch",
        expected_revision=state.revision,
    )
    deliverable_id = f"deliverable-{task_id}-api-patch"
    assert deliverable_id in state.deliverables

    state = repo.create_review_profile(
        profile_id=None,
        team_id="team-platform-team",
        project_id=project_id,
        name="Standard review",
        expected_revision=state.revision,
    )
    profile_id = "profile-project-team-platform-team-runtime-upgrade-standard-review"
    assert profile_id in state.review_profiles

    state = repo.record_review_request(
        review_request_id=None,
        deliverable_id=deliverable_id,
        reviewer_agent_id=state.projects[project_id].lead_agent_id,
        review_profile_id=profile_id,
        expected_revision=state.revision,
    )
    assert any(review_id.startswith(f"review-{deliverable_id}") for review_id in state.review_requests)


def test_repository_preserves_explicit_operator_ids(tmp_path: Path) -> None:
    repo = TeamStateRepository(tmp_path)

    state = repo.create_team(
        team_id="explicit-team",
        name="Display Name",
        manager_agent_id="explicit-manager",
        manager_session_id="session-manager",
        manager_role_id="explicit-manager-role",
    )

    assert "explicit-team" in state.teams
    assert "explicit-manager" in state.agents
    assert "explicit-manager-role" in state.roles
    assert "team-display-name" not in state.teams


def test_repository_updates_team_project_and_agent_records(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state = repo.update_team(
        team_id="team",
        name="Renamed Team",
        operator_communication_target_id=None,
        set_operator_communication_target_id=True,
        max_cost_usd="123.45",
        set_max_cost_usd=True,
        archived=True,
        expected_revision=repo.read().revision,
    )

    team = state.teams["team"]
    assert state.revision == 7
    assert team.name == "Renamed Team"
    assert team.operator_communication_target_id is None
    assert str(team.max_cost_usd) == "123.45"
    assert team.archived is True
    assert {agent.id for agent in state.agents.values() if agent.status is AgentStatus.ARCHIVED} == {
        "backend",
        "lead",
        "manager",
        "worker",
    }

    state = repo.update_project(
        project_id="project",
        name="Renamed Project",
        status=ProjectStatus.BLOCKED,
        git=ProjectGitConfig(host_repo_path="/srv/repo", bare_repo_name="repo.git"),
        set_git=True,
        goals=[ProjectGoal(id="goal-2", description="Updated goal")],
        expected_revision=repo.read().revision,
    )

    project = state.projects["project"]
    assert state.revision == 8
    assert project.name == "Renamed Project"
    assert project.status is ProjectStatus.BLOCKED
    assert project.git is not None
    assert project.git.bare_repo_name == "repo.git"
    assert [goal.id for goal in project.goals] == ["goal-2"]

    repo.create_role(
        role_id="project__qa",
        team_id="team",
        project_id="project",
        name="QA",
        expected_revision=repo.read().revision,
    )
    state = repo.update_agent(
        agent_id="worker",
        role_id="project__qa",
        session_id="session-worker-2",
        reports_to_agent_id="lead",
        set_reports_to_agent_id=True,
        display_name=None,
        set_display_name=True,
        ephemeral_policy=EphemeralAgentPolicy(max_concurrent=3),
        status=AgentStatus.PAUSED,
        expected_revision=repo.read().revision,
    )

    worker = state.agents["worker"]
    assert state.revision == 10
    assert worker.role_id == "project__qa"
    assert worker.session_id == "session-worker-2"
    assert worker.display_name is None
    assert worker.reports_to_agent_id == "lead"
    assert worker.ephemeral_policy.max_concurrent == 3
    assert worker.status is AgentStatus.PAUSED
    assert state.agents["backend"].subordinate_agent_ids == []
    assert state.agents["lead"].subordinate_agent_ids == ["backend", "worker"]

    state = repo.update_team(
        team_id="team",
        max_cost_usd=None,
        set_max_cost_usd=True,
    )
    assert state.teams["team"].max_cost_usd is None

    try:
        repo.update_team(
            team_id="team",
            max_cost_usd="-1",
            set_max_cost_usd=True,
        )
    except ValueError as exc:
        assert "max_cost_usd" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("negative team max cost should be rejected")


def test_repository_rejects_agent_update_reporting_cycle_without_writing(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    with pytest.raises(ValidationError, match="reporting cycle"):
        repo.update_agent(
            agent_id="lead",
            reports_to_agent_id="worker",
            set_reports_to_agent_id=True,
            expected_revision=repo.read().revision,
        )

    state = repo.read()
    assert state.revision == 6
    assert state.agents["lead"].reports_to_agent_id == "manager"
    assert state.agents["manager"].subordinate_agent_ids == ["lead"]


def test_repository_materializes_auto_dev_squad_atomically(tmp_path: Path) -> None:
    repo = TeamStateRepository(tmp_path)

    state, result = repo.materialize_template(
        "auto-dev-squad",
        inputs=_auto_dev_squad_inputs(),
        expected_revision=0,
    )

    assert result["success"] is True
    assert result["template_id"] == "auto-dev-squad"
    assert result["template_name"] == "Auto-Dev Squad"
    assert result["team_id"] == "team-auto-dev-squad"
    assert result["project_id"] == "project-auto-dev-squad"
    assert result["agent_ids"] == {
        "manager": "manager-auto-dev-squad",
        "project_lead": "project-lead-auto-dev-squad",
        "implementer": "implementer-auto-dev-squad",
        "reviewer": "reviewer-auto-dev-squad",
        "release_operator": "release-operator-auto-dev-squad",
    }
    assert result["goal_ids"] == ["project-auto-dev-squad-auto-dev-reviewed-delivery"]
    assert result["review_profile_ids"] == ["project-auto-dev-squad-auto-dev-review"]
    assert result["task_ids"] == [
        "project-auto-dev-squad-implement-scoped-slice",
        "project-auto-dev-squad-release-handoff",
    ]
    assert result["revision"] == 1

    assert state.revision == 1
    team = state.teams["team-auto-dev-squad"]
    assert team.manager_agent_id == "manager-auto-dev-squad"
    assert team.operator_communication_target_id == "slack://ops-channel"
    assert team.project_ids == ["project-auto-dev-squad"]

    project = state.projects["project-auto-dev-squad"]
    assert project.team_id == "team-auto-dev-squad"
    assert project.lead_agent_id == "project-lead-auto-dev-squad"
    assert project.git is not None
    assert project.git.host_repo_path == "/srv/repos/auto-dev-squad"
    assert project.git.bare_repo_name == "auto-dev-squad.git"
    assert [goal.id for goal in project.goals] == [
        "project-auto-dev-squad-auto-dev-reviewed-delivery"
    ]

    assert state.agents["manager-auto-dev-squad"].subordinate_agent_ids == [
        "project-lead-auto-dev-squad"
    ]
    assert state.agents["project-lead-auto-dev-squad"].subordinate_agent_ids == [
        "implementer-auto-dev-squad",
        "reviewer-auto-dev-squad",
        "release-operator-auto-dev-squad",
    ]
    assert state.agents["project-lead-auto-dev-squad"].reports_to_agent_id == "manager-auto-dev-squad"
    assert state.agents["implementer-auto-dev-squad"].reports_to_agent_id == "project-lead-auto-dev-squad"
    assert state.agents["reviewer-auto-dev-squad"].reports_to_agent_id == "project-lead-auto-dev-squad"
    assert state.agents["release-operator-auto-dev-squad"].reports_to_agent_id == "project-lead-auto-dev-squad"
    assert {
        role_key: state.agents[agent_id].ephemeral_policy.max_concurrent
        for role_key, agent_id in result["agent_ids"].items()
    } == {
        "manager": 0,
        "project_lead": 1,
        "implementer": 2,
        "reviewer": 1,
        "release_operator": 1,
    }

    profile = state.review_profiles["project-auto-dev-squad-auto-dev-review"]
    assert profile.team_id == "team-auto-dev-squad"
    assert profile.project_id == "project-auto-dev-squad"
    assert [criterion.id for criterion in profile.criteria] == [
        "scope",
        "tests",
        "risk",
        "handoff",
    ]

    implement_task = state.tasks["project-auto-dev-squad-implement-scoped-slice"]
    release_task = state.tasks["project-auto-dev-squad-release-handoff"]
    assert implement_task.owner_agent_id == "implementer-auto-dev-squad"
    assert implement_task.created_by_agent_id == "project-lead-auto-dev-squad"
    assert release_task.owner_agent_id == "release-operator-auto-dev-squad"
    assert release_task.dependency_task_ids == [
        "project-auto-dev-squad-implement-scoped-slice"
    ]


def test_repository_materializes_auto_dev_squad_with_team_project_and_agents_shape(
    tmp_path: Path,
) -> None:
    repo = TeamStateRepository(tmp_path)

    state, result = repo.materialize_template(
        "auto-dev-squad",
        inputs={
            "team": {
                "id": "team-auto-dev-squad",
                "name": "Auto-Dev Squad",
                "operator_communication_target_id": "slack://ops-channel",
                "manager_agent_id": "manager-auto-dev-squad",
                "manager_session_id": "session-manager-auto-dev-squad",
            },
            "project": {
                "id": "project-auto-dev-squad",
                "name": "Reviewed Auto-Dev Delivery",
                "lead_agent_id": "project-lead-auto-dev-squad",
                "lead_session_id": "session-project-lead-auto-dev-squad",
                "git": {
                    "host_repo_path": "/srv/repos/auto-dev-squad",
                    "bare_repo_name": "auto-dev-squad.git",
                },
            },
            "agents": {
                "implementer": {
                    "agent_id": "implementer-auto-dev-squad",
                    "session_id": "session-implementer-auto-dev-squad",
                },
                "reviewer": {
                    "agent_id": "reviewer-auto-dev-squad",
                    "session_id": "session-reviewer-auto-dev-squad",
                },
                "release_operator": {
                    "agent_id": "release-operator-auto-dev-squad",
                    "session_id": "session-release-operator-auto-dev-squad",
                },
            },
        },
        expected_revision=0,
    )

    assert result["success"] is True
    assert state.projects["project-auto-dev-squad"].lead_agent_id == "project-lead-auto-dev-squad"
    assert state.agents["manager-auto-dev-squad"].subordinate_agent_ids == [
        "project-lead-auto-dev-squad"
    ]
    assert state.agents["project-lead-auto-dev-squad"].subordinate_agent_ids == [
        "implementer-auto-dev-squad",
        "reviewer-auto-dev-squad",
        "release-operator-auto-dev-squad",
    ]


def test_repository_materialize_template_allows_multiple_unique_squads(
    tmp_path: Path,
) -> None:
    repo = TeamStateRepository(tmp_path)

    first_state, first_result = repo.materialize_template(
        "auto-dev-squad",
        inputs=_auto_dev_squad_inputs(suffix="alpha"),
        expected_revision=0,
    )
    second_state, second_result = repo.materialize_template(
        "auto-dev-squad",
        inputs=_auto_dev_squad_inputs(suffix="beta"),
        expected_revision=first_state.revision,
    )

    assert first_result["review_profile_ids"] == ["project-alpha-auto-dev-review"]
    assert second_result["review_profile_ids"] == ["project-beta-auto-dev-review"]
    assert second_state.revision == 2
    assert set(second_state.teams) == {"team-alpha", "team-beta"}
    assert set(second_state.projects) == {"project-alpha", "project-beta"}
    assert {
        "project-alpha-implement-scoped-slice",
        "project-alpha-release-handoff",
        "project-beta-implement-scoped-slice",
        "project-beta-release-handoff",
    }.issubset(second_state.tasks)
    assert (
        second_state.tasks["project-beta-release-handoff"].dependency_task_ids
        == ["project-beta-implement-scoped-slice"]
    )


def test_repository_materialize_template_rejects_duplicate_ids_without_writing(
    tmp_path: Path,
) -> None:
    repo = TeamStateRepository(tmp_path)

    with pytest.raises(ValueError, match="materialization ids contains duplicate ids"):
        repo.materialize_template(
            "auto-dev-squad",
            inputs=_auto_dev_squad_inputs(duplicate_reviewer_id="implementer-auto-dev-squad"),
            expected_revision=0,
        )

    state = repo.read()
    assert state.revision == 0
    assert state.teams == {}
    assert state.projects == {}
    assert state.agents == {}
    assert state.review_profiles == {}
    assert state.tasks == {}


def test_repository_materialize_template_rejects_stale_revision_without_writing(
    tmp_path: Path,
) -> None:
    repo = TeamStateRepository(tmp_path)
    repo.create_team(
        team_id="existing-team",
        name="Existing Team",
        manager_agent_id="existing-manager",
        manager_session_id="existing-session",
        expected_revision=0,
    )

    with pytest.raises(TeamStateConflictError):
        repo.materialize_template(
            "auto-dev-squad",
            inputs=_auto_dev_squad_inputs(),
            expected_revision=0,
        )

    state = repo.read()
    assert state.revision == 1
    assert state.projects == {}


def test_repository_materialize_template_rejects_malformed_git_config(
    tmp_path: Path,
) -> None:
    repo = TeamStateRepository(tmp_path)

    with pytest.raises(ValidationError, match="exactly one of bare_repo_name or bare_repo_path must be set"):
        repo.materialize_template(
            "auto-dev-squad",
            inputs=_auto_dev_squad_inputs(bad_git=True),
            expected_revision=0,
        )

    assert repo.read().revision == 0


def test_repository_enforces_parent_ephemeral_concurrency(tmp_path: Path) -> None:
    repo = TeamStateRepository(tmp_path)
    repo.create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
        expected_revision=0,
    )
    repo.create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
        expected_revision=1,
    )
    repo.create_role(
        role_id="project__backend",
        team_id="team",
        project_id="project",
        name="Backend",
        expected_revision=2,
    )
    repo.register_agent(
        agent_id="backend",
        team_id="team",
        project_id="project",
        role_id="project__backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
        ephemeral_policy=EphemeralAgentPolicy(max_concurrent=1),
        expected_revision=3,
    )
    repo.create_or_reprioritize_task(
        task_id="task",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Build backend",
        description="Implement backend work.",
        created_by_agent_id="lead",
        expected_revision=repo.read().revision,
    )

    with pytest.raises(ValueError, match="max_concurrent=1"):
        repo.request_ephemeral_assignments(
            parent_agent_id="backend",
            requests=[
                {"task_id": "task", "title": "One", "prompt": "Do one thing."},
                {"task_id": "task", "title": "Two", "prompt": "Do another thing."},
            ],
        )

    assert repo.read().ephemeral_assignments == {}


def test_repository_stale_revision_rejects_without_writing(tmp_path: Path) -> None:
    repo = TeamStateRepository(tmp_path)
    repo.create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
        expected_revision=0,
    )

    with pytest.raises(TeamStateConflictError):
        repo.create_project(
            project_id="project",
            team_id="team",
            name="Project",
            lead_agent_id="lead",
            lead_session_id="session-lead",
            expected_revision=0,
        )

    state = repo.read()
    assert state.revision == 1
    assert state.projects == {}


def test_repository_rejects_malformed_existing_state(tmp_path: Path) -> None:
    repo = TeamStateRepository(tmp_path)
    with sqlite3.connect(tmp_path / "addons" / "team-addon" / "state.db") as connection:
        connection.execute(
            """
            INSERT INTO team_teams(
                id, name, manager_agent_id, operator_communication_target_id,
                max_cost_usd, created_at, updated_at, archived
            )
            VALUES ('team', 'Team', 'missing', NULL, NULL, '2026-01-01T00:00:00+00:00',
                    '2026-01-01T00:00:00+00:00', 0)
            """
        )

    with pytest.raises(ValidationError, match="missing manager"):
        repo.read()


def test_deleting_addon_state_does_not_affect_sessions_file(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    repo = TeamStateRepository(runtime_dir)
    sessions_path = runtime_dir / "sessions.json"
    sessions_path.write_text('{"session": {"metadata": {}}}', encoding="utf-8")

    shutil.rmtree(runtime_dir / "addons" / "team-addon")

    assert sessions_path.read_text(encoding="utf-8") == '{"session": {"metadata": {}}}'
    assert not (runtime_dir / "addons" / "team-addon").exists()
    assert repo.runtime_dir == runtime_dir


def test_apply_agent_action_allows_self_and_subordinate_task_creation(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "task_creations": [
                {
                    "id": "task-self",
                    "owner_agent_id": "lead",
                    "project_id": "project",
                    "priority": 1,
                    "title": "Lead self task",
                    "description": "Work the lead owns.",
                },
                {
                    "id": "task-sub",
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 2,
                    "title": "Backend task",
                    "description": "Work for a direct subordinate.",
                },
            ],
        },
    )

    assert result["success"] is True
    assert set(result["created_task_ids"]) == {"task-self", "task-sub"}
    assert state.tasks["task-self"].created_by_agent_id == "lead"
    assert state.tasks["task-self"].state == TeamTaskState.PENDING
    assert state.tasks["task-sub"].owner_agent_id == "backend"


def test_apply_agent_action_task_creation_batch_allows_sibling_dependencies(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "task_creations": [
                {
                    "task_id": "task-scaffold",
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 1,
                    "title": "Scaffold",
                    "description": "Create the project scaffold.",
                },
                {
                    "task_id": "task-netcode",
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 2,
                    "title": "Netcode",
                    "description": "Build the authoritative loop.",
                    "dependency_task_ids": ["task-scaffold"],
                },
                {
                    "task_id": "task-review",
                    "owner_agent_id": "worker",
                    "project_id": "project",
                    "priority": 3,
                    "title": "Review",
                    "description": "Review the completed slice.",
                    "dependency_task_ids": ["task-netcode"],
                },
            ],
        },
    )

    assert result["success"] is True
    assert result["errors"] == []
    assert result["created_task_ids"] == [
        "task-scaffold",
        "task-netcode",
        "task-review",
    ]
    assert state.tasks["task-netcode"].dependency_task_ids == ["task-scaffold"]
    assert state.tasks["task-review"].dependency_task_ids == ["task-netcode"]


def test_apply_agent_action_project_lead_can_target_project_hierarchy(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    # worker is in the project but reports to backend, not directly to the lead.
    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "task_creations": [
                {
                    "owner_agent_id": "worker",
                    "project_id": "project",
                    "priority": 1,
                    "title": "Deep task",
                    "description": "Task for an indirect report in the project.",
                }
            ],
        },
    )

    assert result["success"] is True
    assert len(result["created_task_ids"]) == 1
    created_id = result["created_task_ids"][0]
    assert state.tasks[created_id].owner_agent_id == "worker"


def test_apply_agent_action_manager_can_assign_project_leads(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "manager",
            "task_creations": [
                {
                    "id": "task-lead",
                    "owner_agent_id": "lead",
                    "project_id": "project",
                    "priority": 0,
                    "title": "Lead directive",
                    "description": "Manager assigns the project lead.",
                }
            ],
        },
    )

    assert result["success"] is True
    assert state.tasks["task-lead"].owner_agent_id == "lead"
    assert state.tasks["task-lead"].created_by_agent_id == "manager"


def test_apply_agent_action_rejects_root_task_without_permission(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "task_creations": [
                {
                    "id": "task-root",
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 1,
                    "title": "Unmandated root",
                    "description": "Backend cannot open a top-level task.",
                }
            ],
        },
    )

    assert result["success"] is False
    assert result["created_task_ids"] == []
    assert any("create_root_task" in error for error in result["errors"])
    assert "task-root" not in state.tasks


def test_operator_stand_down_blocks_subtree_wakes_and_active_dispatch_actions(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="backend-task", owner_agent_id="backend")

    _state, leases = repo.lease_due_agents(
        agent_id="backend",
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert len(leases) == 1
    dispatch_id = leases[0]["dispatch_id"]

    state = repo.stand_down_agent(
        agent_id="lead",
        reason="Pause project work.",
        set_by="operator:test",
    )
    assert state.stand_downs
    assert state.stand_downs[next(iter(state.stand_downs))].agent_id == "lead"

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "dispatch_id": dispatch_id,
            "task_updates": [{"task_id": "backend-task", "state": "in_progress"}],
        },
        dispatch_id=dispatch_id,
    )

    assert result["success"] is False
    assert result["applied"] is False
    assert any("stood down" in error for error in result["errors"])
    assert state.tasks["backend-task"].state == TeamTaskState.PENDING
    assert state.agent_runtimes["backend"].active_dispatch_id is None

    _state, leases = repo.lease_due_agents(
        now=datetime(2026, 1, 1, minute=5, tzinfo=UTC),
    )
    assert "backend" not in [lease["agent"]["id"] for lease in leases]

    state = repo.revoke_operator_stand_downs(
        agent_id="lead",
        revoked_by="operator:test",
    )
    assert all(stand_down.cleared_at is not None for stand_down in state.stand_downs.values())

    _state, leases = repo.lease_due_agents(
        agent_id="backend",
        now=datetime(2026, 1, 1, minute=10, tzinfo=UTC),
    )
    assert len(leases) == 1


def test_governance_group_persists_without_relaxing_same_team_reporting(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    repo.create_team(
        team_id="other",
        name="Other",
        manager_agent_id="other-manager",
        manager_session_id="session-other-manager",
        expected_revision=repo.read().revision,
    )

    state = repo.create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="manager",
        member_team_ids=["team", "other"],
        goals=[{"description": "Keep teams governed independently"}],
        max_cost_usd="25.00",
        expected_revision=repo.read().revision,
    )

    group = state.governance_groups["portfolio"]
    assert group.member_team_ids == ["team", "other"]
    assert group.goals[0].id.startswith("governance-goal-portfolio")
    assert float(group.max_cost_usd) == 25.0

    reloaded = TeamStateRepository(tmp_path).read()
    assert reloaded.governance_groups["portfolio"].member_team_ids == ["team", "other"]

    with pytest.raises(ValueError, match="superior agent other-manager is not in team team"):
        repo.update_agent(
            agent_id="backend",
            reports_to_agent_id="other-manager",
            set_reports_to_agent_id=True,
            expected_revision=repo.read().revision,
        )


def test_governance_agenda_derives_and_validates_reference_team_membership(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    repo.create_team(
        team_id="other",
        name="Other",
        manager_agent_id="other-manager",
        manager_session_id="session-other-manager",
        expected_revision=repo.read().revision,
    )
    repo.create_project(
        project_id="other-project",
        team_id="other",
        name="Other Project",
        lead_agent_id="other-lead",
        lead_session_id="session-other-lead",
        expected_revision=repo.read().revision,
    )
    repo.create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="manager",
        member_team_ids=["team"],
        expected_revision=repo.read().revision,
    )

    state = repo.create_governance_agenda_item(
        agenda_item_id="project-agenda",
        governance_group_id="portfolio",
        item_type="operator_directive",
        title="Review project",
        summary="Review project state.",
        project_id="project",
        expected_revision=repo.read().revision,
    )

    assert state.governance_agenda["project-agenda"].team_id == "team"

    with pytest.raises(ValueError, match="team other is not in governance group portfolio"):
        repo.create_governance_agenda_item(
            agenda_item_id="bad-project-agenda",
            governance_group_id="portfolio",
            item_type="operator_directive",
            title="Bad project",
            summary="This project is outside the group.",
            project_id="other-project",
            expected_revision=repo.read().revision,
        )

    with pytest.raises(ValueError, match="agent other-lead does not belong to team team"):
        repo.create_governance_agenda_item(
            agenda_item_id="bad-agent-agenda",
            governance_group_id="portfolio",
            item_type="operator_directive",
            title="Bad agent",
            summary="This agent is outside the team.",
            team_id="team",
            agent_id="other-lead",
            expected_revision=repo.read().revision,
        )


def test_governance_group_member_removal_handles_removed_team_agenda(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    repo.create_team(
        team_id="other",
        name="Other",
        manager_agent_id="other-manager",
        manager_session_id="session-other-manager",
        expected_revision=repo.read().revision,
    )
    repo.create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="manager",
        member_team_ids=["team", "other"],
        expected_revision=repo.read().revision,
    )
    repo.create_governance_agenda_item(
        agenda_item_id="other-agenda",
        governance_group_id="portfolio",
        item_type="operator_directive",
        title="Review other team",
        summary="Review other team status.",
        team_id="other",
        expected_revision=repo.read().revision,
    )

    state = repo.update_governance_group(
        governance_group_id="portfolio",
        member_team_ids=["team"],
        set_member_team_ids=True,
        expected_revision=repo.read().revision,
    )

    assert state.governance_groups["portfolio"].member_team_ids == ["team"]
    assert (
        state.governance_agenda["other-agenda"].state
        == GovernanceAgendaItemState.HANDLED
    )
    membership_agenda = [
        item
        for item in state.governance_agenda.values()
        if item.id != "other-agenda"
    ]
    assert len(membership_agenda) == 1
    assert membership_agenda[0].item_type == GovernanceAgendaItemType.POLICY_DECISION
    assert membership_agenda[0].team_id is None
    assert membership_agenda[0].payload["removed_team_ids"] == ["other"]
    assert membership_agenda[0].payload["current_member_team_ids"] == ["team"]


def test_governance_group_member_addition_creates_due_agenda(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    repo.create_team(
        team_id="other",
        name="Other",
        manager_agent_id="other-manager",
        manager_session_id="session-other-manager",
        expected_revision=repo.read().revision,
    )
    repo.create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="manager",
        member_team_ids=["team"],
        expected_revision=repo.read().revision,
    )

    def _future_governance_wake(state):
        state.governance_runtimes["portfolio"] = GovernanceRuntime(
            governance_group_id="portfolio",
            next_wake_at=now + timedelta(hours=12),
        )
        return state

    repo.update(_future_governance_wake)

    state = repo.update_governance_group(
        governance_group_id="portfolio",
        member_team_ids=["team", "other"],
        set_member_team_ids=True,
        expected_revision=repo.read().revision,
    )

    agenda = list(state.governance_agenda.values())
    assert len(agenda) == 1
    assert agenda[0].item_type == GovernanceAgendaItemType.POLICY_DECISION
    assert agenda[0].title == "Governance member teams changed"
    assert agenda[0].payload == {
        "change_type": "member_teams_changed",
        "added_team_ids": ["other"],
        "removed_team_ids": [],
        "previous_member_team_ids": ["team"],
        "current_member_team_ids": ["team", "other"],
    }
    assert state.governance_runtimes["portfolio"].next_wake_at is None

    state, leases = repo.lease_due_governance_managers(now=now)

    assert len(leases) == 1
    assert leases[0]["reason"] == "policy_decision"
    assert leases[0]["agenda"][0]["id"] == agenda[0].id
    assert [item["team"]["id"] for item in leases[0]["member_teams"]] == [
        "team",
        "other",
    ]
    assert state.governance_runtimes["portfolio"].wake_reason == "policy_decision"


def test_governance_group_member_reorder_does_not_create_agenda(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    repo.create_team(
        team_id="other",
        name="Other",
        manager_agent_id="other-manager",
        manager_session_id="session-other-manager",
        expected_revision=repo.read().revision,
    )
    repo.create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="manager",
        member_team_ids=["team", "other"],
        expected_revision=repo.read().revision,
    )

    state = repo.update_governance_group(
        governance_group_id="portfolio",
        member_team_ids=["other", "team"],
        set_member_team_ids=True,
        expected_revision=repo.read().revision,
    )

    assert state.governance_groups["portfolio"].member_team_ids == ["other", "team"]
    assert state.governance_agenda == {}


def test_governance_manager_skips_when_agent_runtime_is_leased(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    repo.create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="manager",
        member_team_ids=["team"],
        expected_revision=repo.read().revision,
    )
    repo.create_governance_agenda_item(
        agenda_item_id="agenda",
        governance_group_id="portfolio",
        item_type="operator_directive",
        title="Review status",
        summary="Review team status.",
        team_id="team",
        expected_revision=repo.read().revision,
    )

    def _lease_manager(state):
        state.agent_runtimes["manager"] = AgentRuntime(
            agent_id="manager",
            wake_leased_until=now + timedelta(minutes=10),
            active_dispatch_id="team-dispatch-manager",
        )
        return state

    repo.update(_lease_manager)

    _state, leases = repo.lease_due_governance_managers(now=now)

    assert leases == []


def test_governance_cost_signal_wakes_only_on_status_change(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    repo.create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="manager",
        member_team_ids=["team"],
        expected_revision=repo.read().revision,
    )
    over_cap_costs = {
        "team_costs": {
            "team": {
                "estimated_cost": 12,
                "max_cost_usd": 10,
                "cost_limit_reached": True,
            }
        }
    }
    clear_costs = {
        "team_costs": {
            "team": {
                "estimated_cost": 2,
                "max_cost_usd": 10,
                "cost_limit_reached": False,
            }
        }
    }

    _state, leases = repo.lease_due_governance_managers(
        now=now,
        cost_payload=over_cap_costs,
    )

    assert len(leases) == 1
    assert leases[0]["reason"] == "cost_cap_reached"
    assert leases[0]["cost_signals"][0]["team_id"] == "team"
    repo.complete_governance_dispatch(
        governance_group_id="portfolio",
        dispatch_id=leases[0]["dispatch_id"],
    )

    state, leases = repo.lease_due_governance_managers(
        now=now + timedelta(hours=2),
        cost_payload=over_cap_costs,
    )

    assert leases == []
    assert state.governance_runtimes["portfolio"].last_cost_signal_fingerprints == [
        "team:cost_cap_reached"
    ]

    state, leases = repo.lease_due_governance_managers(
        now=now + timedelta(hours=3),
        cost_payload=clear_costs,
    )

    assert leases == []
    assert state.governance_runtimes["portfolio"].last_cost_signal_fingerprints == []

    _state, leases = repo.lease_due_governance_managers(
        now=now + timedelta(hours=4),
        cost_payload=over_cap_costs,
    )

    assert len(leases) == 1
    assert leases[0]["reason"] == "cost_cap_reached"


def test_governance_blocked_signal_wakes_only_on_blocker_change(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    repo.create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="manager",
        member_team_ids=["team"],
        expected_revision=repo.read().revision,
    )
    repo.create_or_reprioritize_task(
        task_id="blocked-a",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Blocked A",
        description="Blocked work.",
        created_by_agent_id="lead",
        state=TeamTaskState.BLOCKED,
        expected_revision=repo.read().revision,
    )

    _state, leases = repo.lease_due_governance_managers(now=now)

    assert len(leases) == 1
    assert leases[0]["reason"] == "blocked_team"
    assert leases[0]["blocked_team_signals"][0]["blocked_task_ids"] == ["blocked-a"]
    repo.complete_governance_dispatch(
        governance_group_id="portfolio",
        dispatch_id=leases[0]["dispatch_id"],
    )

    _state, leases = repo.lease_due_governance_managers(now=now + timedelta(hours=2))

    assert leases == []

    repo.create_or_reprioritize_task(
        task_id="blocked-b",
        owner_agent_id="backend",
        project_id="project",
        priority=2,
        title="Blocked B",
        description="More blocked work.",
        created_by_agent_id="lead",
        state=TeamTaskState.BLOCKED,
        expected_revision=repo.read().revision,
    )

    _state, leases = repo.lease_due_governance_managers(now=now + timedelta(hours=3))

    assert len(leases) == 1
    assert leases[0]["reason"] == "blocked_team"
    assert leases[0]["blocked_team_signals"][0]["blocked_task_ids"] == [
        "blocked-a",
        "blocked-b",
    ]


def test_superior_action_stand_downs_and_revokes_direct_subordinate(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="backend-task", owner_agent_id="backend")

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "stand_downs": [
                {"agent_id": "backend", "reason": "Await revised project plan."}
            ],
        },
    )

    assert result["success"] is True
    assert len(result["created_stand_down_ids"]) == 1
    stand_down_id = result["created_stand_down_ids"][0]
    assert state.stand_downs[stand_down_id].source == AgentStandDownSource.SUPERIOR
    assert state.stand_downs[stand_down_id].set_by == "lead"

    _state, leases = repo.lease_due_agents(
        agent_id="backend",
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert leases == []

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "stand_down_revocations": [{"agent_id": "backend"}],
        },
    )

    assert result["success"] is True
    assert result["revoked_stand_down_ids"] == [stand_down_id]
    assert state.stand_downs[stand_down_id].cleared_by == "lead"

    _state, leases = repo.lease_due_agents(
        agent_id="backend",
        now=datetime(2026, 1, 1, minute=5, tzinfo=UTC),
    )
    assert len(leases) == 1


def test_superior_action_cannot_revoke_operator_stand_down(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    state = repo.stand_down_agent(
        agent_id="backend",
        reason="Operator hold.",
        set_by="operator:test",
    )
    operator_stand_down_id = next(iter(state.stand_downs))

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "stand_down_revocations": [{"agent_id": "backend"}],
        },
    )

    assert result["success"] is False
    assert result["revoked_stand_down_ids"] == []
    assert state.stand_downs[operator_stand_down_id].cleared_at is None
    assert any("no active superior stand-down" in error for error in result["errors"])


def test_apply_agent_action_item_errors_do_not_abort_valid_task_creation(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "manager",
            "task_updates": [{"state": "finished"}],
            "task_creations": [
                {
                    "id": "task-lead",
                    "owner_agent_id": "lead",
                    "project_id": "project",
                    "priority": 0,
                    "title": "Lead directive",
                    "description": "Manager assigns the project lead.",
                }
            ],
        },
    )

    assert result["success"] is True
    assert result["applied"] is True
    assert result["created_task_ids"] == ["task-lead"]
    assert result["updated_task_ids"] == []
    assert result["errors"] == [
        "task_updates[0]: task_id must be a non-empty string"
    ]
    assert state.tasks["task-lead"].owner_agent_id == "lead"
    assert state.agent_runtimes["lead"].no_progress_count == 0
    action_record = next(iter(state.action_records.values()))
    assert action_record.success is False
    assert action_record.error == "task_updates[0]: task_id must be a non-empty string"


def test_apply_agent_action_rejects_unrelated_agent_task_creation(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    # backend cannot create a task for its own superior.
    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "task_creations": [
                {
                    "owner_agent_id": "lead",
                    "project_id": "project",
                    "priority": 1,
                    "title": "Nope",
                    "description": "Backend cannot assign its superior.",
                }
            ],
        },
    )

    assert result["success"] is False
    assert result["created_task_ids"] == []
    assert any("not permitted" in error for error in result["errors"])
    assert state.tasks == {}
    assert state.agent_runtimes["backend"].no_progress_count == 1


def test_apply_agent_action_rejects_cross_project_task_creation(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    repo.create_project(
        project_id="project-2",
        team_id="team",
        name="Second Project",
        lead_agent_id="lead-2",
        lead_session_id="session-lead-2",
        expected_revision=repo.read().revision,
    )

    # lead leads "project" but tries to place a task on backend inside "project-2".
    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "task_creations": [
                {
                    "owner_agent_id": "backend",
                    "project_id": "project-2",
                    "priority": 1,
                    "title": "Cross project",
                    "description": "Backend does not belong to project-2.",
                }
            ],
        },
    )

    assert result["success"] is False
    assert result["created_task_ids"] == []
    assert any("outside project" in error for error in result["errors"])
    assert state.tasks == {}


def test_apply_agent_action_rejects_cross_project_self_task_creation(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    repo.create_project(
        project_id="project-2",
        team_id="team",
        name="Second Project",
        lead_agent_id="lead-2",
        lead_session_id="session-lead-2",
        expected_revision=repo.read().revision,
    )

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "task_creations": [
                {
                    "owner_agent_id": "backend",
                    "project_id": "project-2",
                    "priority": 1,
                    "title": "Cross project self",
                    "description": "Backend cannot assign itself into project-2.",
                }
            ],
        },
    )

    assert result["success"] is False
    assert result["created_task_ids"] == []
    assert any("outside project" in error for error in result["errors"])
    assert state.tasks == {}


def test_apply_agent_action_rejects_terminal_task_creation_state(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "task_creations": [
                {
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 1,
                    "title": "Terminal",
                    "description": "Cannot be created already accepted.",
                    "state": "accepted",
                }
            ],
        },
    )

    assert result["success"] is False
    assert any("terminal" in error for error in result["errors"])
    assert state.tasks == {}


def test_apply_agent_action_accepts_explicit_non_terminal_state(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "task_creations": [
                {
                    "id": "task-progress",
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 0,
                    "title": "In progress",
                    "description": "Created already in progress.",
                    "state": "in_progress",
                }
            ],
        },
    )

    assert result["success"] is True
    assert state.tasks["task-progress"].state == TeamTaskState.IN_PROGRESS


def test_apply_agent_action_creates_status_request_for_subordinate(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "status_requests": [
                {"recipient_agent_id": "backend", "question": "Status on the backend slice?"}
            ],
        },
    )

    assert result["success"] is True
    assert len(result["created_status_request_ids"]) == 1
    item_id = result["created_status_request_ids"][0]
    item = state.inbox[item_id]
    assert item.recipient_agent_id == "backend"
    assert item.sender_agent_id == "lead"
    assert item.item_type == TeamInboxItemType.STATUS_REQUEST
    assert item.response_requested is True
    assert item.priority == TeamInboxPriority.NORMAL
    assert item.payload["question"] == "Status on the backend slice?"
    assert "response_requested" not in item.payload
    assert "priority" not in item.payload


def test_apply_agent_action_sets_reply_intent_defaults_and_overrides(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "questions": [
                {
                    "recipient_agent_id": "lead",
                    "question": "This is just confirming direction.",
                    "response_requested": False,
                    "priority": "high",
                }
            ],
            "status_reports": [
                {
                    "recipient_agent_id": "lead",
                    "summary": "Done with the FYI check.",
                }
            ],
        },
    )

    assert result["success"] is True
    items = [state.inbox[item_id] for item_id in result["created_inbox_item_ids"]]
    question = next(item for item in items if item.item_type == TeamInboxItemType.QUESTION)
    report = next(
        item for item in items if item.item_type == TeamInboxItemType.PROGRESS_REPORT
    )
    assert question.response_requested is False
    assert report.response_requested is False
    assert question.priority == TeamInboxPriority.HIGH
    assert report.priority == TeamInboxPriority.LOW
    assert "response_requested" not in question.payload
    assert "response_requested" not in report.payload
    assert "priority" not in question.payload
    assert "priority" not in report.payload

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "status_reports": [
                {
                    "recipient_agent_id": "lead",
                    "summary": "Please confirm this plan.",
                    "response_requested": True,
                    "priority": "normal",
                }
            ],
        },
    )

    assert result["success"] is True
    report = state.inbox[result["created_inbox_item_ids"][0]]
    assert report.response_requested is True
    assert report.priority == TeamInboxPriority.NORMAL


def test_apply_agent_action_rejects_non_boolean_reply_intent(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "questions": [
                {
                    "recipient_agent_id": "lead",
                    "question": "Can I use a string here?",
                    "response_requested": "false",
                }
            ],
        },
    )

    assert result["success"] is False
    assert state.inbox == {}
    assert any("response_requested must be a boolean" in error for error in result["errors"])


def test_apply_agent_action_rejects_invalid_inbox_priority(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "questions": [
                {
                    "recipient_agent_id": "lead",
                    "question": "Can I invent a priority?",
                    "priority": "eventually",
                }
            ],
        },
    )

    assert result["success"] is False
    assert state.inbox == {}
    assert any("priority must be one of: low, normal, high" in error for error in result["errors"])


def test_repository_round_trips_inbox_reply_intent(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "status_reports": [
                {
                    "recipient_agent_id": "lead",
                    "summary": "No reply needed.",
                    "priority": "low",
                }
            ],
        },
    )

    item_id = result["created_inbox_item_ids"][0]
    assert state.inbox[item_id].response_requested is False
    assert state.inbox[item_id].priority == TeamInboxPriority.LOW
    reloaded = TeamStateRepository(tmp_path).read()
    assert reloaded.inbox[item_id].response_requested is False
    assert reloaded.inbox[item_id].priority == TeamInboxPriority.LOW


def test_apply_agent_action_rejects_status_request_to_non_subordinate(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "status_requests": [
                {"recipient_agent_id": "lead", "question": "Status?"}
            ],
        },
    )

    assert result["success"] is False
    assert result["created_status_request_ids"] == []
    assert any("direct subordinate" in error for error in result["errors"])
    assert state.inbox == {}


def test_apply_agent_action_rejects_question_to_non_superior(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    # backend's superior is lead; sending to worker (a subordinate) is rejected.
    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "questions": [{"recipient_agent_id": "worker", "question": "?"}],
        },
    )

    assert result["success"] is False
    assert any("direct superior" in error for error in result["errors"])
    assert state.inbox == {}


def test_apply_agent_action_manager_question_redirected_to_escalations(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "manager",
            "questions": [{"recipient_agent_id": "lead", "question": "?"}],
        },
    )

    assert result["success"] is False
    assert any("operator_escalations" in error for error in result["errors"])
    assert state.inbox == {}


def test_apply_agent_action_task_creation_resets_no_progress_count(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="parent", owner_agent_id="backend")

    repo.apply_agent_action(action={"agent_id": "backend"}, valid=False, error="boom")
    assert repo.read().agent_runtimes["backend"].no_progress_count == 1

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "task_creations": [
                {
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 1,
                    "title": "Self serve",
                    "description": "Backend assigns itself follow-up work.",
                    "parent_task_id": "parent",
                }
            ],
        },
    )

    assert result["success"] is True
    assert state.agent_runtimes["backend"].no_progress_count == 0


def test_context_clearing_is_disabled_by_default_for_idle_agents(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state = repo.create_or_reprioritize_task(
        task_id="already-done",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Already done",
        description="Terminal task should not opt in automatically.",
        created_by_agent_id="lead",
        state=TeamTaskState.ACCEPTED,
    )

    runtime = state.agent_runtimes["backend"]
    assert runtime.auto_context_clearing_enabled is False
    assert runtime.context_rotation_pending is False


def test_manual_context_rotation_marks_pending_even_when_agent_has_work(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    repo.create_or_reprioritize_task(
        task_id="active-work",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Active work",
        description="Manual rotation is allowed with active work.",
        created_by_agent_id="lead",
    )

    state = repo.request_agent_context_rotation(
        agent_id="backend",
        requested_by="operator:test",
        request_note="controlled test",
    )

    runtime = state.agent_runtimes["backend"]
    assert runtime.auto_context_clearing_enabled is False
    assert runtime.context_rotation_pending is True
    assert runtime.context_rotation_reason == "manual"
    assert runtime.context_rotation_requested_by == "operator:test"
    assert runtime.context_rotation_request_note == "controlled test"
    assert runtime.next_wake_at == runtime.context_rotation_requested_at
    assert "Active tasks:" in str(runtime.last_handoff_summary)
    assert "active-work" in str(runtime.last_handoff_summary)


def test_auto_context_rotation_marks_pending_when_enabled_agent_becomes_idle(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    repo.create_or_reprioritize_task(
        task_id="finish-me",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Finish me",
        description="Work that will make the agent non-idle.",
        created_by_agent_id="lead",
    )

    state = repo.update_agent(
        agent_id="backend",
        auto_context_clearing_enabled=True,
        set_auto_context_clearing_enabled=True,
    )

    assert state.agent_runtimes["backend"].auto_context_clearing_enabled is True
    assert state.agent_runtimes["backend"].context_rotation_pending is False

    state = repo.update_task(task_id="finish-me", state=TeamTaskState.ACCEPTED)

    runtime = state.agent_runtimes["backend"]
    assert runtime.context_rotation_pending is True
    assert runtime.context_rotation_reason == "idle"
    assert runtime.next_wake_at == runtime.context_rotation_requested_at
    assert runtime.last_handoff_summary is not None
    assert "Active tasks: none." in runtime.last_handoff_summary


def test_auto_context_rotation_waits_for_activity_after_fresh_context(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    repo.create_or_reprioritize_task(
        task_id="finish-me",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Finish me",
        description="Work that will make the agent non-idle.",
        created_by_agent_id="lead",
    )
    repo.update_agent(
        agent_id="backend",
        auto_context_clearing_enabled=True,
        set_auto_context_clearing_enabled=True,
    )
    repo.update_task(task_id="finish-me", state=TeamTaskState.ACCEPTED)
    _state, leases = repo.lease_due_agents(agent_id="backend")
    assert len(leases) == 1
    dispatch_id = leases[0]["dispatch_id"]
    repo.record_context_rotation_dispatch_started(
        agent_id="backend",
        dispatch_id=dispatch_id,
        thread_id="fresh-thread",
    )

    state, result = repo.apply_agent_action(
        action={"agent_id": "backend"},
        dispatch_id=dispatch_id,
    )

    assert result["success"] is True
    assert result["applied"] is False
    runtime = state.agent_runtimes["backend"]
    assert runtime.context_rotation_pending is False
    assert runtime.last_context_rotated_at is not None
    assert runtime.no_progress_count == 1

    state = repo.create_or_reprioritize_task(
        task_id="post-rotation-work",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Post-rotation work",
        description="New work should allow a later idle rotation.",
        created_by_agent_id="lead",
    )
    assert state.agent_runtimes["backend"].context_rotation_pending is False

    state = repo.update_task(
        task_id="post-rotation-work",
        state=TeamTaskState.ACCEPTED,
    )

    runtime = state.agent_runtimes["backend"]
    assert runtime.context_rotation_pending is True
    assert runtime.context_rotation_reason == "idle"


def test_auto_context_rotation_does_not_mark_pending_with_pending_inbox(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    repo.create_operator_directive(
        team_id="team",
        recipient_agent_id="backend",
        message="Handle this first.",
        created_by="operator:test",
    )

    state = repo.update_agent(
        agent_id="backend",
        auto_context_clearing_enabled=True,
        set_auto_context_clearing_enabled=True,
    )

    runtime = state.agent_runtimes["backend"]
    assert runtime.auto_context_clearing_enabled is True
    assert runtime.context_clearing_policy == AgentContextClearingPolicy.IDLE
    assert runtime.context_rotation_pending is False


def test_every_wake_context_policy_rotates_ready_task_wakes(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    repo.create_or_reprioritize_task(
        task_id="active-work",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Active work",
        description="Every wake policy should rotate when this wake is leased.",
        created_by_agent_id="lead",
    )
    state = repo.update_agent(
        agent_id="backend",
        context_clearing_policy=AgentContextClearingPolicy.EVERY_WAKE,
        set_context_clearing_policy=True,
    )
    assert state.agent_runtimes["backend"].context_rotation_pending is False

    state, leases = repo.lease_due_agents(agent_id="backend", now=base_time)

    assert len(leases) == 1
    runtime = state.agent_runtimes["backend"]
    assert runtime.context_clearing_policy == AgentContextClearingPolicy.EVERY_WAKE
    assert runtime.auto_context_clearing_enabled is True
    assert runtime.context_rotation_pending is True
    assert runtime.context_rotation_reason == "every_wake"
    assert runtime.context_rotation_requested_at == base_time
    assert leases[0]["runtime"]["context_clearing_policy"] == "every_wake"
    assert leases[0]["runtime"]["context_rotation_pending"] is True
    assert leases[0]["runtime"]["context_rotation_reason"] == "every_wake"


def test_every_wake_context_policy_does_not_wake_idle_agent(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    state = repo.update_agent(
        agent_id="backend",
        context_clearing_policy="every_wake",
        set_context_clearing_policy=True,
    )
    assert state.agent_runtimes["backend"].context_clearing_policy == (
        AgentContextClearingPolicy.EVERY_WAKE
    )

    state, leases = repo.lease_due_agents(agent_id="backend")

    assert leases == []
    runtime = state.agent_runtimes["backend"]
    assert runtime.context_rotation_pending is False
    assert runtime.context_rotation_reason is None


def test_every_wake_context_policy_preserves_low_priority_batching(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    repo.update_agent(
        agent_id="backend",
        context_clearing_policy=AgentContextClearingPolicy.EVERY_WAKE,
        set_context_clearing_policy=True,
    )

    def _add_low_inbox(state):
        state.inbox["low-1"] = AgentInboxItem(
            id="low-1",
            recipient_agent_id="backend",
            item_type=TeamInboxItemType.PROGRESS_REPORT,
            priority=TeamInboxPriority.LOW,
            state=TeamInboxItemState.PENDING,
        )
        return state

    repo.update(_add_low_inbox)

    state, leases = repo.lease_due_agents(
        agent_id="backend",
        now=base_time,
        low_priority_wake_after_cycles=2,
    )
    assert leases == []
    runtime = state.agent_runtimes["backend"]
    assert runtime.context_rotation_pending is False
    assert runtime.low_priority_batch_cycle_count == 1

    state, leases = repo.lease_due_agents(
        agent_id="backend",
        now=base_time + timedelta(minutes=15),
        low_priority_wake_after_cycles=2,
    )

    assert len(leases) == 1
    assert leases[0]["reason"] == "low_priority_inbox"
    runtime = state.agent_runtimes["backend"]
    assert runtime.context_rotation_pending is True
    assert runtime.context_rotation_reason == "every_wake"
    assert leases[0]["runtime"]["context_rotation_pending"] is True


def test_low_priority_inbox_wakes_after_configured_wake_cycles(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    base_time = datetime(2026, 1, 1, tzinfo=UTC)

    def _add_low_inbox(state):
        state.inbox["low-1"] = AgentInboxItem(
            id="low-1",
            recipient_agent_id="backend",
            item_type=TeamInboxItemType.PROGRESS_REPORT,
            priority=TeamInboxPriority.LOW,
            state=TeamInboxItemState.PENDING,
        )
        return state

    repo.update(_add_low_inbox)

    state, leases = repo.lease_due_agents(
        agent_id="backend",
        now=base_time,
        low_priority_wake_after_cycles=3,
    )
    assert leases == []
    runtime = state.agent_runtimes["backend"]
    assert runtime.low_priority_batch_started_at == base_time
    assert runtime.low_priority_batch_cycle_count == 1
    assert runtime.low_priority_batch_due_after_cycles == 3

    def _add_second_low_inbox(state):
        state.inbox["low-2"] = AgentInboxItem(
            id="low-2",
            recipient_agent_id="backend",
            item_type=TeamInboxItemType.PROGRESS_REPORT,
            priority=TeamInboxPriority.LOW,
            state=TeamInboxItemState.PENDING,
        )
        return state

    repo.update(_add_second_low_inbox)

    state, leases = repo.lease_due_agents(
        agent_id="backend",
        now=base_time.replace(minute=15),
        low_priority_wake_after_cycles=3,
    )
    assert leases == []
    runtime = state.agent_runtimes["backend"]
    assert runtime.low_priority_batch_started_at == base_time
    assert runtime.low_priority_batch_cycle_count == 2

    state, leases = repo.lease_due_agents(
        agent_id="backend",
        now=base_time.replace(minute=30),
        low_priority_wake_after_cycles=3,
    )

    assert len(leases) == 1
    assert leases[0]["reason"] == "low_priority_inbox"
    assert [item["id"] for item in leases[0]["inbox"]] == ["low-1", "low-2"]
    runtime = state.agent_runtimes["backend"]
    assert runtime.low_priority_batch_started_at is None
    assert runtime.low_priority_batch_cycle_count == 0
    assert runtime.low_priority_batch_due_after_cycles is None


def test_normal_inbox_cancels_low_priority_batch_countdown(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    base_time = datetime(2026, 1, 1, tzinfo=UTC)

    def _add_low_inbox(state):
        state.inbox["low-1"] = AgentInboxItem(
            id="low-1",
            recipient_agent_id="backend",
            item_type=TeamInboxItemType.PROGRESS_REPORT,
            priority=TeamInboxPriority.LOW,
            state=TeamInboxItemState.PENDING,
        )
        return state

    repo.update(_add_low_inbox)
    state, leases = repo.lease_due_agents(
        agent_id="backend",
        now=base_time,
        low_priority_wake_after_cycles=4,
    )
    assert leases == []
    assert state.agent_runtimes["backend"].low_priority_batch_cycle_count == 1

    def _add_normal_inbox(state):
        state.inbox["normal-1"] = AgentInboxItem(
            id="normal-1",
            recipient_agent_id="backend",
            item_type=TeamInboxItemType.STATUS_REQUEST,
            priority=TeamInboxPriority.NORMAL,
            state=TeamInboxItemState.PENDING,
        )
        return state

    repo.update(_add_normal_inbox)
    state, leases = repo.lease_due_agents(
        agent_id="backend",
        now=base_time.replace(minute=15),
        low_priority_wake_after_cycles=4,
    )

    assert len(leases) == 1
    assert leases[0]["reason"] == "inbox"
    assert [item["id"] for item in leases[0]["inbox"]] == ["low-1", "normal-1"]
    runtime = state.agent_runtimes["backend"]
    assert runtime.low_priority_batch_started_at is None
    assert runtime.low_priority_batch_cycle_count == 0


def test_handled_low_priority_inbox_clears_batch_countdown(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    base_time = datetime(2026, 1, 1, tzinfo=UTC)

    def _add_low_inbox(state):
        state.inbox["low-1"] = AgentInboxItem(
            id="low-1",
            recipient_agent_id="backend",
            item_type=TeamInboxItemType.PROGRESS_REPORT,
            priority=TeamInboxPriority.LOW,
            state=TeamInboxItemState.PENDING,
        )
        return state

    repo.update(_add_low_inbox)
    state, leases = repo.lease_due_agents(
        agent_id="backend",
        now=base_time,
        low_priority_wake_after_cycles=2,
    )
    assert leases == []
    assert state.agent_runtimes["backend"].low_priority_batch_cycle_count == 1

    def _handle_low_inbox(state):
        state.inbox["low-1"].state = TeamInboxItemState.HANDLED
        return state

    repo.update(_handle_low_inbox)
    state, leases = repo.lease_due_agents(
        agent_id="backend",
        now=base_time.replace(minute=15),
        low_priority_wake_after_cycles=2,
    )

    assert leases == []
    runtime = state.agent_runtimes["backend"]
    assert runtime.low_priority_batch_started_at is None
    assert runtime.low_priority_batch_cycle_count == 0


def test_operator_task_creation_resets_no_progress_and_wakes_owner(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    def _throttle_manager(state):
        state.agent_runtimes["manager"] = AgentRuntime(
            agent_id="manager",
            no_progress_count=10,
        )
        return state

    repo.update(_throttle_manager)

    state = repo.create_or_reprioritize_task(
        task_id="manager-delegate-prototype",
        owner_agent_id="manager",
        project_id="project",
        priority=1,
        title="Delegate prototype",
        description="Create a project-lead task for the prototype.",
        created_by_agent_id="manager",
    )

    runtime = state.agent_runtimes["manager"]
    assert runtime.no_progress_count == 0
    assert runtime.next_wake_at is None

    _state, leases = repo.lease_due_agents(max_no_progress_count=3)

    assert [lease["agent"]["id"] for lease in leases] == ["manager"]


def test_create_operator_directive_wakes_manager_inbox(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    def _throttle_manager(state):
        state.agent_runtimes["manager"] = AgentRuntime(
            agent_id="manager",
            no_progress_count=10,
        )
        return state

    repo.update(_throttle_manager)
    repo.create_or_reprioritize_task(
        task_id="status-sweep",
        owner_agent_id="manager",
        project_id="project",
        priority=1,
        title="Status sweep",
        description="Ask implementers for status.",
        created_by_agent_id="manager",
    )

    state, item = repo.create_operator_directive(
        team_id="team",
        message="Ask the lead for a status report on next Team dispatch.",
        title="Status report request",
        project_id="project",
        task_id="status-sweep",
        source="discord",
        source_message_id="discord-msg-1",
        created_by="operator:jonathan",
        requesting_session_id="session-manager",
    )

    assert item.item_type == TeamInboxItemType.OPERATOR_DIRECTIVE
    assert item.recipient_agent_id == "manager"
    assert item.task_id == "status-sweep"
    assert item.payload == {
        "title": "Status report request",
        "message": "Ask the lead for a status report on next Team dispatch.",
        "team_id": "team",
        "project_id": "project",
        "source": "discord",
        "source_message_id": "discord-msg-1",
        "created_by": "operator:jonathan",
        "requesting_session_id": "session-manager",
    }
    runtime = state.agent_runtimes["manager"]
    assert runtime.no_progress_count == 0
    assert runtime.next_wake_at is None

    _state, leases = repo.lease_due_agents(max_no_progress_count=3)

    assert [lease["agent"]["id"] for lease in leases] == ["manager"]
    assert leases[0]["reason"] == "inbox"
    assert leases[0]["inbox"][0]["id"] == item.id


def test_create_operator_directive_rejects_non_manager_session(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    with pytest.raises(ValueError, match="team manager session"):
        repo.create_operator_directive(
            team_id="team",
            message="Ask for status.",
            requesting_session_id="session-lead",
        )


def test_create_operator_directive_accepts_governance_manager_session(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    repo.register_agent(
        agent_id="governance-manager",
        session_id="session-governance",
        expected_revision=repo.read().revision,
    )
    repo.create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="governance-manager",
        member_team_ids=["team"],
        expected_revision=repo.read().revision,
    )

    state, item = repo.create_operator_directive(
        team_id="team",
        message="Confirm the exact blocker and avoid retrying failed promotion.",
        title="Governance blocker check",
        requesting_session_id="session-governance",
    )

    assert item.item_type == TeamInboxItemType.OPERATOR_DIRECTIVE
    assert item.recipient_agent_id == "manager"
    assert item.payload["title"] == "Governance blocker check"
    assert "created_by" not in item.payload
    assert item.payload["requesting_session_id"] == "session-governance"
    assert state.agent_runtimes["manager"].next_wake_at is None


def test_mark_governance_agenda_handled_accepts_governance_manager_session(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    repo.register_agent(
        agent_id="governance-manager",
        session_id="session-governance",
        expected_revision=repo.read().revision,
    )
    repo.create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="governance-manager",
        member_team_ids=["team"],
        expected_revision=repo.read().revision,
    )
    repo.create_governance_agenda_item(
        agenda_item_id="agenda",
        governance_group_id="portfolio",
        item_type="operator_directive",
        title="Review status",
        summary="Check status.",
        team_id="team",
        expected_revision=repo.read().revision,
    )

    state, item = repo.mark_governance_agenda_handled_by_session(
        agenda_item_id="agenda",
        requesting_session_id="session-governance",
        expected_revision=repo.read().revision,
    )

    assert item.state == GovernanceAgendaItemState.HANDLED
    assert state.governance_agenda["agenda"].state == GovernanceAgendaItemState.HANDLED

    state, item = repo.mark_governance_agenda_handled_by_session(
        agenda_item_id="agenda",
        requesting_session_id="session-governance",
        expected_revision=state.revision,
    )
    assert item.state == GovernanceAgendaItemState.HANDLED
    assert state.governance_agenda["agenda"].state == GovernanceAgendaItemState.HANDLED


def test_mark_governance_agenda_handled_rejects_non_governance_manager_session(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    repo.register_agent(
        agent_id="governance-manager",
        session_id="session-governance",
        expected_revision=repo.read().revision,
    )
    repo.create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="governance-manager",
        member_team_ids=["team"],
        expected_revision=repo.read().revision,
    )
    repo.create_governance_agenda_item(
        agenda_item_id="agenda",
        governance_group_id="portfolio",
        item_type="operator_directive",
        title="Review status",
        summary="Check status.",
        team_id="team",
        expected_revision=repo.read().revision,
    )

    with pytest.raises(ValueError, match="active governance manager session"):
        repo.mark_governance_agenda_handled_by_session(
            agenda_item_id="agenda",
            requesting_session_id="session-manager",
            expected_revision=repo.read().revision,
        )


def test_mark_governance_agenda_handled_rejects_other_group_manager_session(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    repo.register_agent(
        agent_id="governance-manager",
        session_id="session-governance",
        expected_revision=repo.read().revision,
    )
    repo.register_agent(
        agent_id="other-governance-manager",
        session_id="session-other-governance",
        expected_revision=repo.read().revision,
    )
    repo.create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="governance-manager",
        member_team_ids=["team"],
        expected_revision=repo.read().revision,
    )
    repo.create_governance_group(
        governance_group_id="other-portfolio",
        name="Other Portfolio",
        manager_agent_id="other-governance-manager",
        member_team_ids=["team"],
        expected_revision=repo.read().revision,
    )
    repo.create_governance_agenda_item(
        agenda_item_id="agenda",
        governance_group_id="portfolio",
        item_type="operator_directive",
        title="Review status",
        summary="Check status.",
        team_id="team",
        expected_revision=repo.read().revision,
    )

    with pytest.raises(ValueError, match="active governance manager session"):
        repo.mark_governance_agenda_handled_by_session(
            agenda_item_id="agenda",
            requesting_session_id="session-other-governance",
            expected_revision=repo.read().revision,
        )


@pytest.mark.parametrize("status", [AgentStatus.PAUSED, AgentStatus.ARCHIVED])
def test_mark_governance_agenda_handled_rejects_inactive_governance_manager_session(
    tmp_path: Path,
    status: AgentStatus,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    repo.register_agent(
        agent_id="governance-manager",
        session_id="session-governance",
        expected_revision=repo.read().revision,
    )
    repo.create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="governance-manager",
        member_team_ids=["team"],
        expected_revision=repo.read().revision,
    )
    repo.create_governance_agenda_item(
        agenda_item_id="agenda",
        governance_group_id="portfolio",
        item_type="operator_directive",
        title="Review status",
        summary="Check status.",
        team_id="team",
        expected_revision=repo.read().revision,
    )

    def _mark_inactive(state):
        state.agents["governance-manager"].status = status
        return state

    repo.update(_mark_inactive)

    with pytest.raises(ValueError, match="active governance manager session"):
        repo.mark_governance_agenda_handled_by_session(
            agenda_item_id="agenda",
            requesting_session_id="session-governance",
            expected_revision=repo.read().revision,
        )


@pytest.mark.parametrize("status", [AgentStatus.PAUSED, AgentStatus.ARCHIVED])
def test_create_operator_directive_rejects_inactive_governance_manager_session(
    tmp_path: Path,
    status: AgentStatus,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    repo.register_agent(
        agent_id="governance-manager",
        session_id="session-governance",
        expected_revision=repo.read().revision,
    )
    repo.create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="governance-manager",
        member_team_ids=["team"],
        expected_revision=repo.read().revision,
    )

    def _mark_inactive(state):
        state.agents["governance-manager"].status = status
        return state

    repo.update(_mark_inactive)

    with pytest.raises(ValueError, match="governance manager session"):
        repo.create_operator_directive(
            team_id="team",
            message="This should not route after the governance manager is inactive.",
            requesting_session_id="session-governance",
        )


def test_operator_task_creation_can_use_operator_actor(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state = repo.create_or_reprioritize_task(
        task_id="manager-delegate-prototype",
        owner_agent_id="manager",
        project_id="project",
        priority=1,
        title="Delegate prototype",
        description="Create a project-lead task for the prototype.",
        created_by_actor="operator:jonathan",
    )

    task = state.tasks["manager-delegate-prototype"]
    assert task.created_by_agent_id is None
    assert task.created_by_actor == "operator:jonathan"


def _set_operator_target(repo: TeamStateRepository, target: str) -> None:
    def _mutate(state):
        state.teams["team"].operator_communication_target_id = target
        return state

    repo.update(_mutate)


def test_apply_agent_action_manager_operator_escalation_emits_event(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_operator_target(repo, "operator:ops-channel")

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "manager",
            "operator_escalations": [
                {
                    "title": "Need a decision",
                    "message": "Blocked waiting on operator input.",
                    "urgency": "high",
                    "project_id": "project",
                }
            ],
        },
    )

    assert result["success"] is True
    assert len(result["operator_escalation_event_ids"]) == 1
    assert state.agent_runtimes["manager"].no_progress_count == 0
    assert len(result["events"]) == 1
    event = result["events"][0]
    assert event["event_type"] == "team.operator.escalation_requested"
    assert event["payload"] == {
        "team_id": "team",
        "manager_agent_id": "manager",
        "communication_target_id": "operator:ops-channel",
        "title": "Need a decision",
        "message": "Blocked waiting on operator input.",
        "urgency": "high",
        "project_id": "project",
    }
    escalation_items = [
        item
        for item in state.inbox.values()
        if item.item_type == TeamInboxItemType.ESCALATION
    ]
    assert len(escalation_items) == 1
    audit = escalation_items[0]
    assert audit.recipient_agent_id == "manager"
    assert audit.payload["communication_target_id"] == "operator:ops-channel"
    assert audit.payload["urgency"] == "high"


def test_apply_agent_action_rejects_non_manager_operator_escalation(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_operator_target(repo, "operator:ops-channel")

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "operator_escalations": [
                {"title": "Hi", "message": "Please look."}
            ],
        },
    )

    assert result["success"] is False
    assert result["operator_escalation_event_ids"] == []
    assert result["events"] == []
    assert any("not permitted" in error for error in result["errors"])
    assert all(
        item.item_type != TeamInboxItemType.ESCALATION for item in state.inbox.values()
    )


def test_apply_agent_action_escalation_without_target_records_failure(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "manager",
            "operator_escalations": [
                {"title": "Hi", "message": "Please look."}
            ],
        },
    )

    assert result["success"] is False
    assert result["operator_escalation_event_ids"] == []
    assert result["events"] == []
    assert any(
        "operator_communication_target_id" in error for error in result["errors"]
    )
    assert state.inbox == {}
    record = next(iter(state.action_records.values()))
    assert record.success is False


def _seed_task(
    repo: TeamStateRepository,
    *,
    task_id: str,
    owner_agent_id: str = "backend",
) -> None:
    repo.create_or_reprioritize_task(
        task_id=task_id,
        owner_agent_id=owner_agent_id,
        project_id="project",
        priority=1,
        title=f"Task {task_id}",
        description="Work to deliver.",
        created_by_agent_id="lead",
    )


def _seed_deliverable(
    repo: TeamStateRepository,
    *,
    deliverable_id: str,
    task_id: str,
    producer_agent_id: str = "backend",
) -> None:
    repo.record_deliverable(
        deliverable_id=deliverable_id,
        task_id=task_id,
        producer_agent_id=producer_agent_id,
        summary="Deliverable summary.",
    )


def test_apply_agent_action_review_request_creates_pending_review(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")

    # lead leads the project, so it may request review of backend's deliverable.
    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-1",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "instructions": "Check the slice carefully.",
                }
            ],
        },
    )

    assert result["success"] is True
    assert result["created_review_request_ids"] == ["review-1"]
    review = state.review_requests["review-1"]
    assert review.reviewer_agent_id == "worker"
    assert review.state == TeamRecordState.PENDING
    assert "review-1" in state.deliverables["deliverable"].review_request_ids
    assert state.deliverables["deliverable"].state == TeamRecordState.PENDING
    inbox_items = [
        item
        for item in state.inbox.values()
        if item.item_type == TeamInboxItemType.STATUS_REQUEST
        and item.recipient_agent_id == "worker"
    ]
    assert len(inbox_items) == 1
    assert inbox_items[0].payload["review_request_id"] == "review-1"
    assert inbox_items[0].payload["instructions"] == "Check the slice carefully."


def test_apply_agent_action_review_request_rejected_for_unrelated_agent(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")

    # worker neither produced nor supervises the producer (backend) and is not a lead.
    state, result = repo.apply_agent_action(
        action={
            "agent_id": "worker",
            "review_requests": [
                {
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "lead",
                    "instructions": "Please review.",
                }
            ],
        },
    )

    assert result["success"] is False
    assert result["created_review_request_ids"] == []
    assert any("not permitted" in error for error in result["errors"])
    assert state.review_requests == {}


def test_apply_agent_action_review_accept_cascades_to_task(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-1",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "instructions": "Review.",
                }
            ],
        },
    )

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [
                {
                    "review_request_id": "review-1",
                    "state": "accepted",
                    "findings": ["Looks good."],
                }
            ],
        },
    )

    assert result["success"] is True
    assert result["decided_review_request_ids"] == ["review-1"]
    review = state.review_requests["review-1"]
    assert review.state == TeamRecordState.ACCEPTED
    assert review.decision_at is not None
    assert review.findings == ["Looks good."]
    assert state.deliverables["deliverable"].state == TeamRecordState.ACCEPTED
    assert state.tasks["task"].state == TeamTaskState.ACCEPTED


def test_apply_agent_action_review_decision_reviewer_only(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-1",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "instructions": "Review.",
                }
            ],
        },
    )

    # lead is not the assigned reviewer (worker is).
    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "review_decisions": [
                {"review_request_id": "review-1", "state": "accepted"}
            ],
        },
    )

    assert result["success"] is False
    assert any("assigned reviewer" in error for error in result["errors"])
    assert state.review_requests["review-1"].state == TeamRecordState.PENDING


def test_apply_agent_action_multi_review_holds_back_deliverable(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-1",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "instructions": "First.",
                },
                {
                    "id": "review-2",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "backend",
                    "instructions": "Second.",
                },
            ],
        },
    )

    # Accepting only the first review must not accept the deliverable yet.
    state, _ = repo.apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [
                {"review_request_id": "review-1", "state": "accepted"}
            ],
        },
    )
    assert state.deliverables["deliverable"].state == TeamRecordState.PENDING
    assert state.tasks["task"].state != TeamTaskState.ACCEPTED

    state, _ = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "review_decisions": [
                {"review_request_id": "review-2", "state": "accepted"}
            ],
        },
    )
    assert state.deliverables["deliverable"].state == TeamRecordState.ACCEPTED
    assert state.tasks["task"].state == TeamTaskState.ACCEPTED


def test_apply_agent_action_multi_deliverable_holds_back_task(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable-a", task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable-b", task_id="task")
    repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-a",
                    "deliverable_id": "deliverable-a",
                    "reviewer_agent_id": "worker",
                    "instructions": "A.",
                },
                {
                    "id": "review-b",
                    "deliverable_id": "deliverable-b",
                    "reviewer_agent_id": "worker",
                    "instructions": "B.",
                },
            ],
        },
    )

    state, _ = repo.apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [
                {"review_request_id": "review-a", "state": "accepted"}
            ],
        },
    )
    assert state.deliverables["deliverable-a"].state == TeamRecordState.ACCEPTED
    assert state.tasks["task"].state != TeamTaskState.ACCEPTED

    state, _ = repo.apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [
                {"review_request_id": "review-b", "state": "accepted"}
            ],
        },
    )
    assert state.tasks["task"].state == TeamTaskState.ACCEPTED


def test_apply_agent_action_review_rejected_blocks_task(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-1",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "instructions": "Review.",
                }
            ],
        },
    )

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [
                {
                    "review_request_id": "review-1",
                    "state": "rejected",
                    "findings": ["Needs rework."],
                }
            ],
        },
    )

    assert result["success"] is True
    assert state.review_requests["review-1"].state == TeamRecordState.REJECTED
    assert state.deliverables["deliverable"].state == TeamRecordState.REJECTED
    assert state.tasks["task"].state == TeamTaskState.BLOCKED
    assert state.projects["project"].status == ProjectStatus.BLOCKED


def test_apply_agent_action_review_blocked_blocks_all(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-1",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "instructions": "Review.",
                }
            ],
        },
    )

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [
                {"review_request_id": "review-1", "state": "blocked"}
            ],
        },
    )

    assert result["success"] is True
    assert state.review_requests["review-1"].state == TeamRecordState.BLOCKED
    assert state.deliverables["deliverable"].state == TeamRecordState.BLOCKED
    assert state.tasks["task"].state == TeamTaskState.BLOCKED
    assert state.projects["project"].status == ProjectStatus.BLOCKED


def test_apply_agent_action_goal_update_permission(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    # backend is neither the project lead nor the team manager.
    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "goal_updates": [
                {"project_id": "project", "goal_id": "goal", "state": "accepted"}
            ],
        },
    )
    assert result["success"] is False
    assert any("not permitted" in error for error in result["errors"])
    assert state.projects["project"].goals[0].state == TeamRecordState.PENDING

    # the project lead may update the goal.
    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "goal_updates": [
                {"project_id": "project", "goal_id": "goal", "state": "in_progress"}
            ],
        },
    )
    assert result["success"] is True
    assert result["updated_goal_ids"] == ["goal"]
    assert state.projects["project"].goals[0].state == TeamRecordState.IN_PROGRESS

    # the team manager may also update the goal.
    state, result = repo.apply_agent_action(
        action={
            "agent_id": "manager",
            "goal_updates": [
                {"project_id": "project", "goal_id": "goal", "state": "accepted"}
            ],
        },
    )
    assert result["success"] is True
    assert state.projects["project"].goals[0].state == TeamRecordState.ACCEPTED


def test_apply_agent_action_project_status_completes(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-1",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "instructions": "Review.",
                }
            ],
        },
    )
    repo.apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [
                {"review_request_id": "review-1", "state": "accepted"}
            ],
        },
    )
    # Task is accepted; project not yet complete until the goal is accepted.
    assert repo.read().projects["project"].status == ProjectStatus.ACTIVE

    state, _ = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "goal_updates": [
                {"project_id": "project", "goal_id": "goal", "state": "accepted"}
            ],
        },
    )

    assert state.projects["project"].status == ProjectStatus.COMPLETED


def test_apply_agent_action_goal_update_resets_no_progress_count(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    repo.apply_agent_action(action={"agent_id": "lead"}, valid=False, error="boom")
    assert repo.read().agent_runtimes["lead"].no_progress_count == 1

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "goal_updates": [
                {"project_id": "project", "goal_id": "goal", "state": "in_progress"}
            ],
        },
    )

    assert result["success"] is True
    assert state.agent_runtimes["lead"].no_progress_count == 0


def _set_project_git(
    repo: TeamStateRepository,
    *,
    bare_repo_name: str | None = "Truman.git",
    canonical_origin_url: str | None = "https://example.invalid/Truman.git",
    provider: str | None = "azure_devops",
) -> None:
    def _mutate(state):
        state.projects["project"].git = ProjectGitConfig(
            host_repo_path="/opt/repos/Truman",
            bare_repo_name=bare_repo_name,
            canonical_origin_url=canonical_origin_url,
            provider=provider,
        )
        return state

    repo.update(_mutate)


def _seed_completed_ephemeral(
    repo: TeamStateRepository,
    *,
    assignment_id: str = "ephemeral-1",
    session_id: str = "eph-1",
    agent_branch: str | None = None,
) -> None:
    def _enable(state):
        state.agents["backend"].ephemeral_policy = EphemeralAgentPolicy(max_concurrent=2)
        return state

    repo.update(_enable)
    _seed_task(repo, task_id="task")
    repo.request_ephemeral_assignments(
        parent_agent_id="backend",
        requests=[
            {
                "id": assignment_id,
                "task_id": "task",
                "session_id": session_id,
                "title": "Slice",
                "prompt": "Implement a slice.",
            }
        ],
    )
    repo.apply_ephemeral_action(
        session_id=session_id,
        action={
            "ephemeral_assignment_id": assignment_id,
            "summary": "Done.",
            "agent_branch": agent_branch
            if agent_branch is not None
            else f"agents/{session_id}/{assignment_id}",
            "commit_sha": "abc1234",
        },
    )


def test_apply_ephemeral_action_accepts_matching_agent_branch(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    def _enable(state):
        state.agents["backend"].ephemeral_policy = EphemeralAgentPolicy(max_concurrent=1)
        return state

    repo.update(_enable)
    _seed_task(repo, task_id="task")
    repo.request_ephemeral_assignments(
        parent_agent_id="backend",
        requests=[
            {
                "id": "ephemeral-1",
                "task_id": "task",
                "session_id": "eph-1",
                "title": "Slice",
                "prompt": "Implement a slice.",
            }
        ],
    )

    state, result = repo.apply_ephemeral_action(
        session_id="eph-1",
        action={
            "ephemeral_assignment_id": "ephemeral-1",
            "summary": "Done.",
            "agent_branch": "agents/eph-1/ephemeral-1",
            "commit_sha": "deadbeef",
        },
    )

    assert result["success"] is True
    assignment = state.ephemeral_assignments["ephemeral-1"]
    assert assignment.state == EphemeralAssignmentState.COMPLETED
    assert assignment.agent_branch == "agents/eph-1/ephemeral-1"
    assert assignment.commit_sha == "deadbeef"


def test_apply_ephemeral_action_rejects_mismatched_agent_branch(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    def _enable(state):
        state.agents["backend"].ephemeral_policy = EphemeralAgentPolicy(max_concurrent=1)
        return state

    repo.update(_enable)
    _seed_task(repo, task_id="task")
    repo.request_ephemeral_assignments(
        parent_agent_id="backend",
        requests=[
            {
                "id": "ephemeral-1",
                "task_id": "task",
                "session_id": "eph-1",
                "title": "Slice",
                "prompt": "Implement a slice.",
            }
        ],
    )

    state, result = repo.apply_ephemeral_action(
        session_id="eph-1",
        action={
            "ephemeral_assignment_id": "ephemeral-1",
            "summary": "Done.",
            "agent_branch": "agents/other/wrong",
        },
    )

    assert result["success"] is False
    assignment = state.ephemeral_assignments["ephemeral-1"]
    assert assignment.state == EphemeralAssignmentState.FAILED
    assert assignment.agent_branch is None
    assert "does not match" in (result["error"] or "")


def test_apply_agent_action_git_review_promotion_parent_only(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_completed_ephemeral(repo)

    # lead is not the parent of the ephemeral assignment and cannot promote it.
    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "git_review_promotions": [{"assignment_id": "ephemeral-1"}],
        },
    )
    assert result["success"] is False
    assert any("cannot promote" in error for error in result["errors"])
    assert state.git_promotions == {}

    # backend (the parent) may promote its completed assignment's branch.
    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "git_review_promotions": [{"assignment_id": "ephemeral-1"}],
        },
    )
    assert result["success"] is True
    assert len(result["git_promotion_event_ids"]) == 1
    promotion_id = result["git_promotion_event_ids"][0]
    promotion = state.git_promotions[promotion_id]
    assert promotion.promotion_kind == "review"
    assert promotion.state == "pending"
    assert promotion.source_branch == "agents/eph-1/ephemeral-1"
    assert promotion.target_branch == "team/team/reviews/ephemeral-1"
    assert promotion.project_id == "project"
    event = result["events"][0]
    assert event["event_type"] == "team.git.promotion_requested"
    assert event["payload"]["promotion_kind"] == "review"
    assert event["payload"]["host_repo_path"] == "/opt/repos/Truman"
    assert event["payload"]["bare_repo_name"] == "Truman.git"
    assert event["payload"]["source_branch"] == "agents/eph-1/ephemeral-1"
    assert event["payload"]["target_branch"] == "team/team/reviews/ephemeral-1"


def test_apply_agent_action_git_review_promotion_rejects_unrelated_deliverable(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_completed_ephemeral(repo)
    _seed_task(repo, task_id="other-task")
    _seed_deliverable(
        repo,
        deliverable_id="other-deliverable",
        task_id="other-task",
    )

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "git_review_promotions": [
                {
                    "assignment_id": "ephemeral-1",
                    "deliverable_id": "other-deliverable",
                }
            ],
        },
    )

    assert result["success"] is False
    assert any("does not belong" in error for error in result["errors"])
    assert result["git_promotion_event_ids"] == []
    assert state.git_promotions == {}


def test_apply_agent_action_git_review_promotion_requires_git_config(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_completed_ephemeral(repo)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "git_review_promotions": [{"assignment_id": "ephemeral-1"}],
        },
    )

    assert result["success"] is False
    assert any("no git configuration" in error for error in result["errors"])
    assert state.git_promotions == {}


def test_apply_agent_action_records_deliverable_git_refs(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "deliverables": [
                {
                    "id": "deliverable",
                    "task_id": "task",
                    "summary": "Ready for review.",
                    "git_refs": {
                        "agent_branch": "agents/session-backend/work",
                        "commit_sha": "deadbeef",
                    },
                }
            ],
        },
    )

    assert result["success"] is True
    refs = state.deliverables["deliverable"].git_refs
    assert refs.agent_branch == "agents/session-backend/work"
    assert refs.commit_sha == "deadbeef"


def test_apply_agent_action_persistent_git_review_promotion_from_commit(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_task(repo, task_id="task")
    repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "deliverables": [
                {
                    "id": "deliverable",
                    "task_id": "task",
                    "summary": "Ready for review.",
                    "git_refs": {"commit_sha": "deadbeef"},
                }
            ],
        },
    )

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "git_review_promotions": [{"deliverable_id": "deliverable"}],
        },
    )

    assert result["success"] is True
    promotion_id = result["git_promotion_event_ids"][0]
    promotion = state.git_promotions[promotion_id]
    assert promotion.promotion_kind == "review"
    assert promotion.deliverable_id == "deliverable"
    assert promotion.task_id == "task"
    assert promotion.source_branch == "commit/deadbeef"
    assert promotion.target_branch == "team/team/reviews/deliverable"
    assert promotion.commit_sha == "deadbeef"
    event = result["events"][0]
    assert event["payload"]["promotion_kind"] == "review"
    assert event["payload"]["deliverable_id"] == "deliverable"
    assert event["payload"]["target_branch"] == "team/team/reviews/deliverable"
    assert event["payload"]["commit_sha"] == "deadbeef"


def test_apply_agent_action_persistent_git_review_promotion_rejects_missing_source(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "git_review_promotions": [{"deliverable_id": "deliverable"}],
        },
    )

    assert result["success"] is False
    assert any("requires source_branch or commit_sha" in error for error in result["errors"])
    assert state.git_promotions == {}


def test_apply_agent_action_persistent_git_review_promotion_checks_authority(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_task(repo, task_id="task")
    repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "deliverables": [
                {
                    "id": "deliverable",
                    "task_id": "task",
                    "summary": "Ready for review.",
                    "git_refs": {"commit_sha": "deadbeef"},
                }
            ],
        },
    )

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "worker",
            "git_review_promotions": [{"deliverable_id": "deliverable"}],
        },
    )

    assert result["success"] is False
    assert any("not permitted to promote deliverable" in error for error in result["errors"])
    assert len(state.git_promotions) == 0


def test_apply_agent_action_git_feature_update_default_branch(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_task(repo, task_id="task")

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "git_feature_updates": [
                {
                    "task_id": "task",
                    "source_review_branches": [
                        "team/team/reviews/ephemeral-1",
                        "team/team/reviews/ephemeral-2",
                    ],
                }
            ],
        },
    )

    assert result["success"] is True
    assert len(result["git_promotion_event_ids"]) == 1
    promotion = state.git_promotions[result["git_promotion_event_ids"][0]]
    assert promotion.promotion_kind == "feature"
    assert promotion.target_branch == "team/team/features/task-backend"
    event = result["events"][0]
    assert event["payload"]["promotion_kind"] == "feature"
    assert event["payload"]["source_branches"] == [
        "team/team/reviews/ephemeral-1",
        "team/team/reviews/ephemeral-2",
    ]
    assert event["payload"]["target_branch"] == "team/team/features/task-backend"


def test_apply_agent_action_git_feature_update_supervisor_allowed(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_task(repo, task_id="task")

    # lead supervises backend (the task owner) and may consolidate the task branches.
    _state, lead_result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "git_feature_updates": [
                {"task_id": "task", "source_review_branches": ["team/team/reviews/x"]}
            ],
        },
    )
    assert lead_result["success"] is True

    # worker neither owns nor supervises the task owner.
    _state, worker_result = repo.apply_agent_action(
        action={
            "agent_id": "worker",
            "git_feature_updates": [
                {"task_id": "task", "source_review_branches": ["team/team/reviews/x"]}
            ],
        },
    )
    assert worker_result["success"] is False
    assert any("not permitted" in error for error in worker_result["errors"])


def test_apply_agent_action_git_feature_update_rejects_bad_branch(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_task(repo, task_id="task")

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "git_feature_updates": [
                {
                    "task_id": "task",
                    "source_review_branches": ["bad:branch"],
                }
            ],
        },
    )

    assert result["success"] is False
    assert any("invalid branch name" in error for error in result["errors"])
    assert state.git_promotions == {}


def _seed_accepted_feature_deliverable(
    repo: TeamStateRepository,
    *,
    deliverable_id: str,
    task_id: str,
    feature: str | None,
    artifact_refs: list[str] | None = None,
) -> None:
    _seed_task(repo, task_id=task_id)
    repo.record_deliverable(
        deliverable_id=deliverable_id,
        task_id=task_id,
        producer_agent_id="backend",
        summary="Deliverable summary.",
        artifact_refs=artifact_refs or [],
    )

    def _mutate(state):
        deliverable = state.deliverables[deliverable_id]
        deliverable.state = TeamRecordState.ACCEPTED
        if feature is not None:
            deliverable.git_refs.feature_branch = feature
        return state

    repo.update(_mutate)


def test_apply_agent_action_git_release_request_creates_package(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature="team/team/features/task-backend",
    )

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {
                    "id": "rel-1",
                    "project_id": "project",
                    "deliverable_ids": ["deliverable"],
                }
            ],
        },
    )

    assert result["success"] is True
    assert result["release_package_ids"] == ["rel-1"]
    release = state.release_packages["rel-1"]
    assert release.state == TeamRecordState.PENDING
    assert release.feature_branches == ["team/team/features/task-backend"]
    assert release.release_branch == "team/team/releases/rel-1"
    assert release.canonical_branch == "releases/project/rel-1"
    assert len(release.git_promotion_record_ids) == 1
    kinds = sorted(
        state.git_promotions[pid].promotion_kind
        for pid in release.git_promotion_record_ids
    )
    assert kinds == ["release"]
    assert len(result["git_promotion_event_ids"]) == 1
    payloads = {event["payload"]["promotion_kind"]: event["payload"] for event in result["events"]}
    assert payloads["release"]["source_branches"] == ["team/team/features/task-backend"]
    assert payloads["release"]["target_branch"] == "team/team/releases/rel-1"
    assert "canonical_push" not in payloads

    release_promotion_id = release.git_promotion_record_ids[0]
    state, promotion_result = repo.record_git_promotion_result(
        promotion_id=release_promotion_id,
        success=True,
        commit_sha="feedface",
    )
    release = state.release_packages["rel-1"]
    canonical_promotions = [
        state.git_promotions[promotion_id]
        for promotion_id in release.git_promotion_record_ids
        if state.git_promotions[promotion_id].promotion_kind == "canonical_push"
    ]
    assert len(canonical_promotions) == 1
    canonical = canonical_promotions[0]
    assert canonical.source_branch == "team/team/releases/rel-1"
    assert canonical.target_branch == "releases/project/rel-1"
    assert promotion_result["git_promotion_event_ids"] == [canonical.id]
    payloads = {
        event["payload"]["promotion_kind"]: event["payload"]
        for event in promotion_result["events"]
    }
    assert payloads["canonical_push"]["source_branch"] == "team/team/releases/rel-1"
    assert payloads["canonical_push"]["target_branch"] == "releases/project/rel-1"
    assert payloads["canonical_push"]["canonical_origin_url"] == (
        "https://example.invalid/Truman.git"
    )
    assert payloads["canonical_push"]["provider"] == "azure_devops"
    assert state.projects["project"].status == ProjectStatus.ACTIVE


def test_apply_agent_action_git_release_request_artifact_only(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature=None,
        artifact_refs=["artifact-space:/teams/team/result"],
    )

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {"id": "rel-1", "project_id": "project", "deliverable_ids": ["deliverable"]}
            ],
        },
    )

    assert result["success"] is True
    release = state.release_packages["rel-1"]
    assert release.feature_branches == []
    assert release.artifact_refs == ["artifact-space:/teams/team/result"]
    assert release.release_branch is None
    assert release.canonical_branch is None
    assert release.git_promotion_record_ids == []
    assert result["git_promotion_event_ids"] == []
    assert state.git_promotions == {}


def test_apply_agent_action_git_release_request_mixed_deliverables_requires_git(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="git-deliverable",
        task_id="git-task",
        feature="team/team/features/git-task-backend",
    )
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="artifact-deliverable",
        task_id="artifact-task",
        feature=None,
        artifact_refs=["artifact-space:/teams/team/result"],
    )

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {
                    "id": "rel-1",
                    "project_id": "project",
                    "deliverable_ids": ["git-deliverable", "artifact-deliverable"],
                }
            ],
        },
    )

    assert result["success"] is False
    assert state.release_packages == {}
    assert any("no git configuration" in error for error in result["errors"])


def test_apply_agent_action_git_release_request_git_only_requires_git_config(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature="team/team/features/task-backend",
    )

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {"id": "rel-1", "project_id": "project", "deliverable_ids": ["deliverable"]}
            ],
        },
    )

    assert result["success"] is False
    assert state.release_packages == {}
    assert any("no git configuration" in error for error in result["errors"])


def test_apply_agent_action_git_release_request_requires_feature_promoted_git_deliverable(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature=None,
    )

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {"id": "rel-1", "project_id": "project", "deliverable_ids": ["deliverable"]}
            ],
        },
    )

    assert result["success"] is False
    assert state.release_packages == {}
    assert any("git_feature_updates" in error for error in result["errors"])
    assert any("git_refs.feature_branch" in error for error in result["errors"])


def test_apply_agent_action_git_release_request_allows_project_release_operator(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _register_release_operator(repo)
    _set_project_git(repo)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature="team/team/features/task-backend",
    )

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "release-operator",
            "git_release_requests": [
                {
                    "id": "rel-1",
                    "project_id": "project",
                    "deliverable_ids": ["deliverable"],
                }
            ],
        },
    )

    assert result["success"] is True
    assert result["release_package_ids"] == ["rel-1"]
    release = state.release_packages["rel-1"]
    assert release.feature_branches == ["team/team/features/task-backend"]
    assert release.release_branch == "team/team/releases/rel-1"


def test_apply_agent_action_git_release_request_rejects_unaccepted(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "git_release_requests": [
                {"project_id": "project", "deliverable_ids": ["deliverable"]}
            ],
        },
    )

    assert result["success"] is False
    assert any("not accepted" in error for error in result["errors"])
    assert state.release_packages == {}


def test_apply_agent_action_git_release_request_requires_lead_or_manager(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _set_project_git(repo)
    _seed_accepted_feature_deliverable(
        repo,
        deliverable_id="deliverable",
        task_id="task",
        feature="team/team/features/task-backend",
    )

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "git_release_requests": [
                {"project_id": "project", "deliverable_ids": ["deliverable"]}
            ],
        },
    )

    assert result["success"] is False
    assert any("not permitted" in error for error in result["errors"])
    assert state.release_packages == {}


def test_apply_agent_action_creates_child_task_with_graph_fields(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="parent", owner_agent_id="backend")
    _seed_task(repo, task_id="dep", owner_agent_id="backend")

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "task_creations": [
                {
                    "id": "child",
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 1,
                    "title": "Child task",
                    "description": "Decomposed work.",
                    "parent_task_id": "parent",
                    "dependency_task_ids": ["dep"],
                }
            ],
        },
    )

    assert result["success"] is True
    assert "child" in result["created_task_ids"]
    child = state.tasks["child"]
    assert child.parent_task_id == "parent"
    assert child.dependency_task_ids == ["dep"]
    assert state.child_task_ids("parent") == ["child"]


def test_apply_agent_action_rejects_batch_child_under_subordinate_parent(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="backend-parent", owner_agent_id="backend")

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "task_creations": [
                {
                    "id": "worker-parent",
                    "owner_agent_id": "worker",
                    "project_id": "project",
                    "priority": 1,
                    "title": "Worker parent",
                    "description": "Subordinate-owned parent task.",
                    "parent_task_id": "backend-parent",
                },
                {
                    "id": "worker-child",
                    "owner_agent_id": "worker",
                    "project_id": "project",
                    "priority": 2,
                    "title": "Worker child",
                    "description": "Must not bypass mandate through batch-local parent.",
                    "parent_task_id": "worker-parent",
                },
            ],
        },
    )

    assert result["created_task_ids"] == ["worker-parent"]
    assert any(
        "can only create child tasks under its own active tasks" in error
        for error in result["errors"]
    )
    assert "worker-parent" in state.tasks
    assert "worker-child" not in state.tasks


def test_apply_agent_action_task_creation_rejects_missing_parent(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "task_creations": [
                {
                    "id": "child",
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 1,
                    "title": "Child",
                    "description": "Refers to a missing parent.",
                    "parent_task_id": "ghost",
                }
            ],
        },
    )

    assert result["success"] is False
    assert result["created_task_ids"] == []
    assert any("missing parent" in error for error in result["errors"])
    assert "child" not in state.tasks


def test_apply_agent_action_task_creation_rejects_cross_project_dependency(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="parent", owner_agent_id="backend")
    repo.create_project(
        project_id="project-2",
        team_id="team",
        name="Second Project",
        lead_agent_id="lead-2",
        lead_session_id="session-lead-2",
        expected_revision=repo.read().revision,
    )
    repo.create_or_reprioritize_task(
        task_id="other",
        owner_agent_id="lead-2",
        project_id="project-2",
        priority=1,
        title="Other project task",
        description="Lives in project-2.",
        created_by_agent_id="lead-2",
    )

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "task_creations": [
                {
                    "id": "child",
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 1,
                    "title": "Child",
                    "description": "Depends across projects.",
                    "parent_task_id": "parent",
                    "dependency_task_ids": ["other"],
                }
            ],
        },
    )

    assert result["success"] is False
    assert result["created_task_ids"] == []
    assert any("belongs to project" in error for error in result["errors"])
    assert "child" not in state.tasks


def test_apply_agent_action_task_creation_batch_rejects_sibling_cycle(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="parent", owner_agent_id="backend")

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "task_creations": [
                {
                    "id": "a",
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 1,
                    "title": "A",
                    "description": "First task.",
                    "parent_task_id": "parent",
                    "dependency_task_ids": ["b"],
                },
                {
                    "id": "b",
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 2,
                    "title": "B",
                    "description": "Second task.",
                    "parent_task_id": "parent",
                    "dependency_task_ids": ["a"],
                },
            ],
        },
    )

    assert result["success"] is False
    assert result["created_task_ids"] == []
    assert any("dependency cycle" in error for error in result["errors"])
    assert "a" not in state.tasks
    assert "b" not in state.tasks


def test_apply_agent_action_task_creation_rejects_conflicting_task_id_alias(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "task_creations": [
                {
                    "task_id": "canonical",
                    "id": "legacy",
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "title": "Conflicting ids",
                    "description": "Both id forms disagree.",
                }
            ],
        },
    )

    assert result["success"] is False
    assert result["created_task_ids"] == []
    assert any("conflicting task_id and id" in error for error in result["errors"])
    assert "canonical" not in state.tasks
    assert "legacy" not in state.tasks


def test_apply_agent_action_owner_edits_dependencies(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="a", owner_agent_id="backend")
    _seed_task(repo, task_id="b", owner_agent_id="backend")

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "task_updates": [{"task_id": "a", "dependency_task_ids": ["b"]}],
        },
    )

    assert result["success"] is True
    assert state.tasks["a"].dependency_task_ids == ["b"]
    assert "a" in result["updated_task_ids"]


def test_apply_agent_action_task_update_rejects_dependency_cycle(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="a", owner_agent_id="backend")
    _seed_task(repo, task_id="b", owner_agent_id="backend")
    repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "task_updates": [{"task_id": "a", "dependency_task_ids": ["b"]}],
        },
    )

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "task_updates": [{"task_id": "b", "dependency_task_ids": ["a"]}],
        },
    )

    assert result["success"] is False
    assert any("cycle" in error for error in result["errors"])
    assert state.tasks["b"].dependency_task_ids == []
    assert state.tasks["a"].dependency_task_ids == ["b"]


def test_apply_agent_action_supervisor_edits_dependencies(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="w1", owner_agent_id="worker")
    _seed_task(repo, task_id="w2", owner_agent_id="worker")

    # backend supervises worker but is neither project lead nor team manager.
    state, result = repo.apply_agent_action(
        action={
            "agent_id": "backend",
            "task_updates": [{"task_id": "w1", "dependency_task_ids": ["w2"]}],
        },
    )

    assert result["success"] is True
    assert state.tasks["w1"].dependency_task_ids == ["w2"]


def test_apply_agent_action_lead_and_manager_edit_dependencies(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="t1", owner_agent_id="backend")
    _seed_task(repo, task_id="t2", owner_agent_id="backend")

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "task_updates": [{"task_id": "t1", "dependency_task_ids": ["t2"]}],
        },
    )
    assert result["success"] is True
    assert state.tasks["t1"].dependency_task_ids == ["t2"]

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "manager",
            "task_updates": [{"task_id": "t1", "dependency_task_ids": []}],
        },
    )
    assert result["success"] is True
    assert state.tasks["t1"].dependency_task_ids == []


def test_apply_agent_action_unrelated_agent_cannot_edit_dependencies(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="b1", owner_agent_id="backend")
    _seed_task(repo, task_id="b2", owner_agent_id="backend")

    # worker reports to backend; it does not supervise backend and is not a lead/manager.
    state, result = repo.apply_agent_action(
        action={
            "agent_id": "worker",
            "task_updates": [{"task_id": "b1", "dependency_task_ids": ["b2"]}],
        },
    )

    assert result["success"] is False
    assert any("not permitted" in error for error in result["errors"])
    assert state.tasks["b1"].dependency_task_ids == []


def test_apply_agent_action_non_owner_state_change_still_rejected(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="b1", owner_agent_id="backend")

    # lead supervises backend and leads the project, but state edits remain owner-only.
    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "task_updates": [{"task_id": "b1", "state": "in_progress"}],
        },
    )

    assert result["success"] is False
    assert any("cannot update task" in error for error in result["errors"])
    assert state.tasks["b1"].state == TeamTaskState.PENDING


def _seed_review_profile(
    repo: TeamStateRepository,
    *,
    profile_id: str = "profile",
    project_id: str | None = "project",
    applicable_role_ids: list[str] | None = None,
    criteria: list[ReviewProfileCriterion] | None = None,
) -> None:
    repo.create_review_profile(
        profile_id=profile_id,
        team_id="team",
        project_id=project_id,
        name="Backend review",
        description="Checklist for backend deliverables.",
        applicable_role_ids=(
            applicable_role_ids
            if applicable_role_ids is not None
            else ["project__backend", "project__worker"]
        ),
        criteria=criteria
        or [
            ReviewProfileCriterion(id="tests", label="Tests pass", required=True),
            ReviewProfileCriterion(
                id="docs", label="Docs updated", description="README/CHANGELOG", required=False
            ),
        ],
    )


def test_create_update_archive_review_profile(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_review_profile(repo)

    state = repo.read()
    profile = state.review_profiles["profile"]
    assert profile.team_id == "team"
    assert profile.name == "Backend review"
    assert [c.id for c in profile.criteria] == ["tests", "docs"]
    assert profile.applicable_role_ids == ["project__backend", "project__worker"]
    assert profile.archived is False

    state = repo.update_review_profile(
        profile_id="profile",
        name="Renamed review",
        criteria=[ReviewProfileCriterion(id="tests", label="Tests pass")],
        archived=True,
    )
    profile = state.review_profiles["profile"]
    assert profile.name == "Renamed review"
    assert [c.id for c in profile.criteria] == ["tests"]
    assert profile.archived is True


def test_create_review_profile_rejects_duplicate(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_review_profile(repo)

    with pytest.raises(ValueError, match="already exists"):
        _seed_review_profile(repo)


def test_apply_agent_action_review_request_with_profile(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _seed_review_profile(repo)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-1",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "review_profile_id": "profile",
                    "instructions": "Use the checklist.",
                }
            ],
        },
    )

    assert result["success"] is True
    review = state.review_requests["review-1"]
    assert review.review_profile_id == "profile"
    assert review.instructions == "Use the checklist."
    inbox_items = [
        item
        for item in state.inbox.values()
        if item.item_type == TeamInboxItemType.STATUS_REQUEST
        and item.recipient_agent_id == "worker"
    ]
    assert len(inbox_items) == 1
    payload = inbox_items[0].payload
    assert payload["instructions"] == "Use the checklist."
    assert payload["review_profile"]["id"] == "profile"
    assert payload["review_profile"]["name"] == "Backend review"
    criterion_ids = [c["id"] for c in payload["review_profile"]["criteria"]]
    assert criterion_ids == ["tests", "docs"]
    assert payload["review_profile"]["criteria"][0]["required"] is True


def test_apply_agent_action_review_request_archived_profile_errors(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _seed_review_profile(repo)
    repo.update_review_profile(profile_id="profile", archived=True)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "review_profile_id": "profile",
                }
            ],
        },
    )

    assert result["success"] is False
    assert any("archived" in error for error in result["errors"])
    assert state.review_requests == {}


def test_apply_agent_action_review_request_cross_project_profile_errors(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    repo.create_project(
        project_id="project-2",
        team_id="team",
        name="Other project",
        lead_agent_id="lead-2",
        lead_session_id="session-lead-2",
        goals=[ProjectGoal(id="goal-2", description="Other work")],
    )
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    # Profile is scoped to a different project than the deliverable's task ("project").
    _seed_review_profile(repo, project_id="project-2", applicable_role_ids=[])

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "review_profile_id": "profile",
                }
            ],
        },
    )

    assert result["success"] is False
    assert any("does not apply to project" in error for error in result["errors"])
    assert state.review_requests == {}


def test_apply_agent_action_review_request_role_profile_errors(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _seed_review_profile(repo, applicable_role_ids=["project__backend"])

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "review_profile_id": "profile",
                }
            ],
        },
    )

    assert result["success"] is False
    assert any("reviewer role project__worker" in error for error in result["errors"])
    assert state.review_requests == {}


def test_apply_agent_action_review_request_missing_profile_errors(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "review_profile_id": "missing",
                }
            ],
        },
    )

    assert result["success"] is False
    assert any("missing review profile" in error for error in result["errors"])
    assert state.review_requests == {}


def _request_review_with_profile(repo: TeamStateRepository) -> None:
    repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-1",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                    "review_profile_id": "profile",
                    "instructions": "Use the checklist.",
                }
            ],
        },
    )


def test_apply_agent_action_review_decision_records_criteria(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _seed_review_profile(repo)
    _request_review_with_profile(repo)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [
                {
                    "review_request_id": "review-1",
                    "state": "accepted",
                    "findings": ["Looks good."],
                    "criteria": [
                        {"criterion_id": "tests", "state": "passed", "finding": "All green."},
                        {"criterion_id": "docs", "state": "skipped"},
                    ],
                }
            ],
        },
    )

    assert result["success"] is True
    review = state.review_requests["review-1"]
    assert review.state == TeamRecordState.ACCEPTED
    assert review.findings == ["Looks good."]
    assert {c.criterion_id: c.state for c in review.criteria} == {
        "tests": "passed",
        "docs": "skipped",
    }
    tests_result = next(c for c in review.criteria if c.criterion_id == "tests")
    assert tests_result.finding == "All green."
    # Criterion results never alter acceptance cascade.
    assert state.deliverables["deliverable"].state == TeamRecordState.ACCEPTED
    assert state.tasks["task"].state == TeamTaskState.ACCEPTED


def test_apply_agent_action_review_decision_unknown_criterion_errors(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _seed_review_profile(repo)
    _request_review_with_profile(repo)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [
                {
                    "review_request_id": "review-1",
                    "state": "accepted",
                    "criteria": [{"criterion_id": "nope", "state": "passed"}],
                }
            ],
        },
    )

    assert result["success"] is False
    assert any("unknown criterion nope" in error for error in result["errors"])
    review = state.review_requests["review-1"]
    assert review.criteria == []
    assert review.state == TeamRecordState.PENDING


def test_apply_agent_action_review_decision_criteria_without_profile_errors(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    repo.apply_agent_action(
        action={
            "agent_id": "lead",
            "review_requests": [
                {
                    "id": "review-1",
                    "deliverable_id": "deliverable",
                    "reviewer_agent_id": "worker",
                }
            ],
        },
    )

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [
                {
                    "review_request_id": "review-1",
                    "state": "accepted",
                    "criteria": [{"criterion_id": "tests", "state": "passed"}],
                }
            ],
        },
    )

    assert result["success"] is False
    assert any("no review profile" in error for error in result["errors"])
    assert state.review_requests["review-1"].state == TeamRecordState.PENDING


def test_apply_agent_action_review_decision_without_criteria_still_works(
    tmp_path: Path,
) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _seed_review_profile(repo)
    _request_review_with_profile(repo)

    state, result = repo.apply_agent_action(
        action={
            "agent_id": "worker",
            "review_decisions": [
                {"review_request_id": "review-1", "state": "accepted"}
            ],
        },
    )

    assert result["success"] is True
    review = state.review_requests["review-1"]
    assert review.state == TeamRecordState.ACCEPTED
    assert review.criteria == []


def test_record_review_decision_repository_stores_criteria(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    _seed_task(repo, task_id="task")
    _seed_deliverable(repo, deliverable_id="deliverable", task_id="task")
    _seed_review_profile(repo)
    repo.record_review_request(
        review_request_id="review-1",
        deliverable_id="deliverable",
        reviewer_agent_id="worker",
        review_profile_id="profile",
        instructions="Operator instructions.",
    )

    from truman_team_addon.models import ReviewCriterionResult

    state = repo.record_review_decision(
        review_request_id="review-1",
        state=TeamRecordState.ACCEPTED,
        findings=["ok"],
        criteria=[ReviewCriterionResult(criterion_id="tests", state="passed")],
    )

    review = state.review_requests["review-1"]
    assert review.review_profile_id == "profile"
    assert review.instructions == "Operator instructions."
    assert review.state == TeamRecordState.ACCEPTED
    assert [c.criterion_id for c in review.criteria] == ["tests"]



def test_register_teamless_agent(tmp_path: Path) -> None:
    repo = TeamStateRepository(tmp_path)

    state = repo.register_agent(
        agent_id="unassigned",
        session_id="session-unassigned",
        display_name="Unassigned",
        expected_revision=repo.read().revision,
    )

    agent = state.agents["unassigned"]
    assert agent.team_id is None
    assert agent_project_ids(state, "unassigned") == []
    assert agent.role_id is None
    assert agent.display_name == "Unassigned"


def test_remove_agent_from_team_blocks_structural_references(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)

    with pytest.raises(ValueError, match="cannot be removed from team"):
        repo.update_agent(
            agent_id="manager",
            team_id=None,
            set_team_id=True,
            expected_revision=repo.read().revision,
        )


def test_remove_unreferenced_agent_from_team(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    repo.create_role(
        role_id="team__observer",
        team_id="team",
        name="Observer",
        expected_revision=repo.read().revision,
    )
    repo.register_agent(
        agent_id="observer",
        team_id="team",
        role_id="team__observer",
        session_id="session-observer",
        expected_revision=repo.read().revision,
    )

    state = repo.update_agent(
        agent_id="observer",
        team_id=None,
        set_team_id=True,
        expected_revision=repo.read().revision,
    )

    agent = state.agents["observer"]
    assert agent.team_id is None
    assert agent.role_id is None



def test_remove_agent_from_team_preserves_terminal_task_history(tmp_path: Path) -> None:
    repo = _seed_hierarchy(tmp_path)
    repo.create_role(
        role_id="team__observer",
        team_id="team",
        name="Observer",
        expected_revision=repo.read().revision,
    )
    repo.register_agent(
        agent_id="observer",
        team_id="team",
        role_id="team__observer",
        session_id="session-observer",
        expected_revision=repo.read().revision,
    )
    repo.create_or_reprioritize_task(
        task_id="completed-history",
        owner_agent_id="observer",
        project_id="project",
        priority=1,
        title="Completed history",
        description="This completed task should retain historical ownership.",
        created_by_agent_id="observer",
    )
    repo.update(lambda state: _mark_task_state(state, "completed-history", TeamTaskState.ACCEPTED))

    state = repo.update_agent(
        agent_id="observer",
        team_id=None,
        set_team_id=True,
        expected_revision=repo.read().revision,
    )

    assert state.agents["observer"].team_id is None
    assert state.tasks["completed-history"].owner_agent_id == "observer"


def _mark_task_state(state, task_id: str, state_value: TeamTaskState):
    state.tasks[task_id].state = state_value
    return state
