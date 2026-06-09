from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from agent_sandbox.admin.service import AdminService
from agent_sandbox.admin.web.app import create_app


def _write_config(tmp_path: Path, *, enable_team_addon: bool) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    runtime_dir = tmp_path / "runtime"
    config_path = tmp_path / "gateway.yaml"
    lines = [
        f'runtime_dir: "{runtime_dir}"',
        f'audit_log_path: "{runtime_dir / "audit.jsonl"}"',
        f'session_store_path: "{runtime_dir / "sessions.json"}"',
        f'approval_store_path: "{runtime_dir / "approvals.json"}"',
        f'draft_store_path: "{runtime_dir / "drafts.json"}"',
        f'email_store_path: "{runtime_dir / "emails.json"}"',
        f'discord_command_store_path: "{runtime_dir / "discord_commands.json"}"',
    ]
    if enable_team_addon:
        lines.extend(
            [
                "addon_loader:",
                "  modules:",
                '    - "truman_team_addon"',
            ]
        )
    config_path.write_text("\n".join(lines), encoding="utf-8")
    return config_path


def _client(tmp_path: Path, *, enable_team_addon: bool = True) -> TestClient:
    return TestClient(create_app(AdminService(_write_config(tmp_path, enable_team_addon=enable_team_addon))))


def test_team_addon_routes_are_available_only_when_enabled(tmp_path: Path) -> None:
    disabled = _client(tmp_path / "disabled", enable_team_addon=False)
    enabled = _client(tmp_path / "enabled", enable_team_addon=True)

    assert disabled.get("/api/addons/team-addon/state").status_code == 404
    assert disabled.get("/addons/team-addon/").status_code == 404

    admin_state = enabled.get("/api/admin/state")
    assert admin_state.status_code == 200
    assert admin_state.json()["addons"] == [
        {
            "addon_id": "team-addon",
            "display_name": "Team Add-on",
            "tab_key": "addon:team-addon",
            "label": "Teams",
            "short_label": "Teams",
            "description": "Hierarchical agent-team orchestration.",
            "api_base_path": "/api/addons/team-addon",
            "ui_path": "/addons/team-addon/",
        }
    ]

    response = enabled.get("/api/addons/team-addon/state")

    assert response.status_code == 200
    assert response.json()["revision"] == 0

    ui = enabled.get("/addons/team-addon/")
    assert ui.status_code == 200
    assert "data-team-addon-ui" in ui.text

    script = enabled.get("/addons/team-addon/team-ui.js")
    assert script.status_code == 200
    assert 'const API_BASE = "/api/addons/team-addon";' in script.text

    stylesheet = enabled.get("/addons/team-addon/team-ui.css")
    assert stylesheet.status_code == 200
    assert ".team-shell" in stylesheet.text


def test_team_addon_admin_api_create_flow(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
            "expected_revision": 0,
        },
    )
    assert response.status_code == 201
    assert response.json()["revision"] == 1

    response = client.post(
        "/api/addons/team-addon/projects",
        json={
            "id": "project",
            "team_id": "team",
            "name": "Project",
            "lead_agent_id": "lead",
            "lead_session_id": "session-lead",
            "goals": [{"id": "goal", "description": "Ship reviewed work"}],
            "expected_revision": 1,
        },
    )
    assert response.status_code == 201
    assert response.json()["revision"] == 2

    response = client.post(
        "/api/addons/team-addon/agents",
        json={
            "id": "backend",
            "team_id": "team",
            "project_id": "project",
            "role": "backend",
            "session_id": "session-backend",
            "reports_to_agent_id": "lead",
            "ephemeral_policy": {"max_concurrent": 2},
            "expected_revision": 2,
        },
    )
    assert response.status_code == 201
    assert response.json()["revision"] == 3

    response = client.post(
        "/api/addons/team-addon/tasks",
        json={
            "id": "task",
            "owner_agent_id": "backend",
            "project_id": "project",
            "priority": 10,
            "title": "Build backend",
            "description": "Implement the backend task.",
            "created_by_agent_id": "lead",
            "expected_revision": 3,
        },
    )
    assert response.status_code == 201
    assert response.json()["tasks"]["task"]["state"] == "pending"

    response = client.patch(
        "/api/addons/team-addon/tasks/task",
        json={
            "state": "waiting_for_review",
            "expected_revision": 4,
        },
    )
    assert response.status_code == 200
    assert response.json()["tasks"]["task"]["state"] == "waiting_for_review"

    response = client.post(
        "/api/addons/team-addon/deliverables",
        json={
            "id": "deliverable",
            "task_id": "task",
            "producer_agent_id": "backend",
            "summary": "Backend task is ready.",
            "artifact_refs": ["artifacts/backend.tar.gz"],
            "expected_revision": 5,
        },
    )
    assert response.status_code == 201
    assert response.json()["tasks"]["task"]["deliverable_ids"] == ["deliverable"]

    response = client.post(
        "/api/addons/team-addon/review-requests",
        json={
            "id": "review",
            "deliverable_id": "deliverable",
            "reviewer_agent_id": "lead",
            "expected_revision": 6,
        },
    )
    assert response.status_code == 201
    state = response.json()
    assert state["revision"] == 7
    assert state["deliverables"]["deliverable"]["review_request_ids"] == ["review"]


def test_team_addon_admin_api_rejects_stale_revision(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
            "expected_revision": 0,
        },
    )

    response = client.post(
        "/api/addons/team-addon/projects",
        json={
            "id": "project",
            "team_id": "team",
            "name": "Project",
            "lead_agent_id": "lead",
            "lead_session_id": "session-lead",
            "expected_revision": 0,
        },
    )

    assert response.status_code == 409
    assert response.json()["current_revision"] == 1
