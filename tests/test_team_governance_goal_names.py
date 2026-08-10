from __future__ import annotations

import sqlite3

from evander_team_addon.api import TEAM_VIEW_COLLECTIONS
from evander_team_addon.models import GovernanceGoal, TeamRecordState
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.services.admin_service import TeamAdminService
from evander_team_addon.services.domain.governance import (
    _governance_goals_with_generated_ids,
)
from evander_team_addon.sqlite_store import (
    SQLITE_SCHEMA_VERSION,
    TeamStateSqliteStore,
)


def test_governance_goal_legacy_payload_defaults_name_to_stable_id() -> None:
    goal = GovernanceGoal.model_validate(
        {
            "id": "cost-guardrail",
            "description": "Keep spend within the agreed guardrail.",
        }
    )

    assert goal.name == "cost-guardrail"
    assert goal.state == TeamRecordState.PENDING
    assert goal.model_dump(mode="json")["name"] == "cost-guardrail"


def test_governance_goal_generation_prefers_human_name() -> None:
    goals = _governance_goals_with_generated_ids(
        [
            {
                "name": "Cost guardrail",
                "description": "Keep spend within the agreed guardrail.",
            },
            {
                "description": "Keep releases reviewable.",
            },
        ],
        governance_group_id="portfolio",
    )

    assert goals[0].id == "governance-goal-portfolio-cost-guardrail"
    assert goals[0].name == "Cost guardrail"
    assert goals[1].id == "governance-goal-portfolio-keep-releases-reviewable"
    assert goals[1].name == "Keep releases reviewable."


def test_governance_goal_names_round_trip_and_update_generates_new_ids(
    tmp_path,
) -> None:
    repository = TeamStateRepository(tmp_path)
    service = TeamAdminService(repository)
    state = service.create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
    )
    state = service.create_governance_group(
        governance_group_id="portfolio",
        name="Portfolio",
        manager_agent_id="manager",
        member_team_ids=["team"],
        goals=[
            {
                "id": "cost",
                "name": "Cost guardrail",
                "description": "Keep spend within the agreed guardrail.",
            }
        ],
        expected_revision=state.revision,
    )
    state = service.update_governance_group(
        governance_group_id="portfolio",
        goals=[
            {
                "id": "cost",
                "name": "Budget stewardship",
                "description": "Keep spend within the approved budget.",
                "state": "in_progress",
            },
            {
                "name": "Delivery quality",
                "description": "Keep releases reviewable.",
            },
        ],
        expected_revision=state.revision,
    )

    reloaded = repository.read()
    goals = reloaded.governance_groups["portfolio"].goals
    assert [(goal.id, goal.name, goal.description, goal.state.value) for goal in goals] == [
        (
            "cost",
            "Budget stewardship",
            "Keep spend within the approved budget.",
            "in_progress",
        ),
        (
            "governance-goal-portfolio-delivery-quality",
            "Delivery quality",
            "Keep releases reviewable.",
            "pending",
        ),
    ]


def test_sqlite_schema_migrates_and_backfills_governance_goal_names(
    tmp_path,
) -> None:
    database_path = tmp_path / "addons" / "team-addon" / "state.db"
    database_path.parent.mkdir(parents=True)
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE team_state_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT INTO team_state_metadata(key, value) VALUES ('revision', '0');
            INSERT INTO team_state_metadata(key, value)
            VALUES ('sqlite_schema_version', '34');
            CREATE TABLE team_governance_group_goals (
                governance_group_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                goal_id TEXT NOT NULL,
                description TEXT NOT NULL,
                state TEXT NOT NULL,
                PRIMARY KEY(governance_group_id, position)
            );
            INSERT INTO team_governance_group_goals(
                governance_group_id, position, goal_id, description, state
            )
            VALUES ('portfolio', 0, 'legacy-cost', 'Keep spend bounded.', 'pending');
            """
        )

    TeamStateSqliteStore(tmp_path)

    with sqlite3.connect(database_path) as connection:
        goal_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(team_governance_group_goals)")
        }
        goal_name = connection.execute(
            """
            SELECT goal_name
            FROM team_governance_group_goals
            WHERE governance_group_id = 'portfolio' AND position = 0
            """
        ).fetchone()[0]
        schema_version = connection.execute(
            """
            SELECT value
            FROM team_state_metadata
            WHERE key = 'sqlite_schema_version'
            """
        ).fetchone()[0]

    assert "goal_name" in goal_columns
    assert goal_name == "legacy-cost"
    assert schema_version == str(SQLITE_SCHEMA_VERSION)


def test_governance_view_includes_roles_for_named_manager_choices() -> None:
    assert "roles" in TEAM_VIEW_COLLECTIONS["governance"]
