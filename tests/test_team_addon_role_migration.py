from __future__ import annotations

import importlib.util
from pathlib import Path

from evander_team_addon.models import RolePermission, TeamState


def _load_migrate_payload():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "migrate_team_addon_roles.py"
    spec = importlib.util.spec_from_file_location("team_role_migration", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.migrate_payload


def test_migrate_payload_converts_legacy_role_strings() -> None:
    migrate_payload = _load_migrate_payload()
    payload = {
        "schema_version": 1,
        "revision": 4,
        "teams": {
            "team": {
                "id": "team",
                "name": "Team",
                "manager_agent_id": "manager",
                "project_ids": ["project"],
            }
        },
        "projects": {
            "project": {
                "id": "project",
                "team_id": "team",
                "name": "Project",
                "lead_agent_id": "lead",
            }
        },
        "agents": {
            "manager": {
                "id": "manager",
                "team_id": "team",
                "role": "manager",
                "session_id": "session-manager",
                "subordinate_agent_ids": ["lead"],
                "ephemeral_policy": {"max_concurrent": 0},
            },
            "lead": {
                "id": "lead",
                "team_id": "team",
                "project_id": "project",
                "role": "project_lead",
                "session_id": "session-lead",
                "reports_to_agent_id": "manager",
                "subordinate_agent_ids": ["reviewer", "release"],
                "ephemeral_policy": {"max_concurrent": 1},
            },
            "reviewer": {
                "id": "reviewer",
                "team_id": "team",
                "project_id": "project",
                "role": "reviewer",
                "session_id": "session-reviewer",
                "reports_to_agent_id": "lead",
                "ephemeral_policy": {"max_concurrent": 1},
            },
            "release": {
                "id": "release",
                "team_id": "team",
                "project_id": "project",
                "role": "release_operator",
                "session_id": "session-release",
                "reports_to_agent_id": "lead",
                "ephemeral_policy": {"max_concurrent": 1},
            },
        },
        "review_profiles": {
            "profile": {
                "id": "profile",
                "team_id": "team",
                "project_id": "project",
                "name": "Review",
                "applicable_roles": ["reviewer"],
            }
        },
    }

    assert migrate_payload(payload) is True
    state = TeamState.model_validate(payload)

    assert payload["schema_version"] == 2
    assert "role" not in payload["agents"]["release"]
    assert state.roles["team__manager"].permissions == [
        RolePermission.CREATE_ROOT_TASK
    ]
    assert state.roles["project__project-lead"].permissions == [
        RolePermission.CREATE_ROOT_TASK
    ]
    assert state.agents["release"].role_id == "project__release-operator"
    assert state.roles["project__release-operator"].permissions == [
        RolePermission.REQUEST_RELEASE
    ]
    assert state.review_profiles["profile"].applicable_role_ids == [
        "project__reviewer"
    ]
    assert "applicable_roles" not in payload["review_profiles"]["profile"]
