from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from agent_sandbox.admin.service import AdminService
from agent_sandbox.admin.web.app import create_app
from agent_sandbox.events.internal import InternalEventBus, internal_event_store_path
from truman_team_addon.repository import TeamStateRepository


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


def _auto_dev_squad_materialization_payload(
    *,
    duplicate_reviewer_agent_id: str | None = None,
    bad_git: bool = False,
    expected_revision: int = 0,
) -> dict[str, object]:
    git_payload: dict[str, object] = {
        "host_repo_path": "/srv/repos/auto-dev-squad",
        "bare_repo_name": "auto-dev-squad.git",
    }
    if bad_git:
        git_payload["bare_repo_path"] = "/srv/git/auto-dev-squad.git"

    return {
        "expected_revision": expected_revision,
        "team": {
            "id": "team-auto-dev-squad",
            "name": "Auto-Dev Squad",
            "manager_agent_id": "manager-auto-dev-squad",
            "manager_session_id": "session-manager-auto-dev-squad",
            "operator_communication_target_id": "slack://ops-channel",
        },
        "project": {
            "id": "project-auto-dev-squad",
            "name": "Reviewed Auto-Dev Delivery",
            "lead_agent_id": "project-lead-auto-dev-squad",
            "lead_session_id": "session-project-lead-auto-dev-squad",
            "git": git_payload,
        },
        "agents": {
            "implementer": {
                "agent_id": "implementer-auto-dev-squad",
                "session_id": "session-implementer-auto-dev-squad",
            },
            "reviewer": {
                "agent_id": (
                    duplicate_reviewer_agent_id or "reviewer-auto-dev-squad"
                ),
                "session_id": "session-reviewer-auto-dev-squad",
            },
            "release_operator": {
                "agent_id": "release-operator-auto-dev-squad",
                "session_id": "session-release-operator-auto-dev-squad",
            },
        },
    }


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

    catalog = enabled.get("/api/addons/team-addon/templates")
    assert catalog.status_code == 200
    assert [template["id"] for template in catalog.json()["templates"]] == [
        "auto-dev-squad"
    ]

    template = enabled.get("/api/addons/team-addon/templates/auto-dev-squad")
    assert template.status_code == 200
    assert template.json()["root_role_key"] == "manager"

    missing_template = enabled.get("/api/addons/team-addon/templates/missing")
    assert missing_template.status_code == 404

    ui = enabled.get("/addons/team-addon/")
    assert ui.status_code == 200
    assert "data-team-addon-ui" in ui.text
    assert 'name="parent_task_id"' in ui.text
    assert 'name="review_profile_id"' in ui.text
    assert 'id="create-review-profile-form"' in ui.text

    script = enabled.get("/addons/team-addon/team-ui.js")
    assert script.status_code == 200
    assert 'const API_BASE = "/api/addons/team-addon";' in script.text
    assert "dependency_task_ids" in script.text
    assert "review_profile_id" in script.text
    assert "parseCriterionResults" in script.text

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


def test_team_addon_admin_api_materializes_auto_dev_squad(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/api/addons/team-addon/templates/auto-dev-squad/materialize",
        json=_auto_dev_squad_materialization_payload(),
    )
    assert response.status_code == 201
    body = response.json()

    assert body["materialized"]["success"] is True
    assert body["materialized"]["template_id"] == "auto-dev-squad"
    assert body["materialized"]["revision"] == 1
    assert body["revision"] == 1
    assert len(body["agents"]) == 5
    assert len(body["projects"]) == 1
    assert len(body["review_profiles"]) == 1
    assert len(body["tasks"]) == 2
    assert body["teams"]["team-auto-dev-squad"]["project_ids"] == [
        "project-auto-dev-squad"
    ]
    assert body["projects"]["project-auto-dev-squad"]["goals"][0]["id"] == (
        "auto-dev-reviewed-delivery"
    )
    assert body["tasks"]["release-handoff"]["dependency_task_ids"] == [
        "implement-scoped-slice"
    ]
    assert body["review_profiles"]["auto-dev-review"]["criteria"][0]["id"] == "scope"


def test_team_addon_admin_api_materialize_template_rejects_duplicates_and_reruns(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)

    duplicate_response = client.post(
        "/api/addons/team-addon/templates/auto-dev-squad/materialize",
        json=_auto_dev_squad_materialization_payload(
            duplicate_reviewer_agent_id="implementer-auto-dev-squad"
        ),
    )
    assert duplicate_response.status_code == 400
    assert "duplicate ids" in duplicate_response.json()["error"]
    assert client.get("/api/addons/team-addon/state").json()["revision"] == 0

    success_response = client.post(
        "/api/addons/team-addon/templates/auto-dev-squad/materialize",
        json=_auto_dev_squad_materialization_payload(),
    )
    assert success_response.status_code == 201
    assert success_response.json()["revision"] == 1

    rerun_response = client.post(
        "/api/addons/team-addon/templates/auto-dev-squad/materialize",
        json=_auto_dev_squad_materialization_payload(expected_revision=1),
    )
    assert rerun_response.status_code == 400
    assert client.get("/api/addons/team-addon/state").json()["revision"] == 1


def test_team_addon_admin_api_materialize_template_rejects_stale_revision(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)

    client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "existing-team",
            "name": "Existing Team",
            "manager_agent_id": "existing-manager",
            "manager_session_id": "existing-session",
            "expected_revision": 0,
        },
    )

    response = client.post(
        "/api/addons/team-addon/templates/auto-dev-squad/materialize",
        json=_auto_dev_squad_materialization_payload(),
    )
    assert response.status_code == 409
    assert response.json()["current_revision"] == 1


def test_team_addon_admin_api_operator_target_and_git_config(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
            "operator_communication_target_id": "ops-channel",
        },
    )
    assert response.status_code == 201
    assert (
        response.json()["teams"]["team"]["operator_communication_target_id"]
        == "ops-channel"
    )

    response = client.patch(
        "/api/addons/team-addon/teams/team",
        json={"operator_communication_target_id": "ops-channel-2"},
    )
    assert response.status_code == 200
    assert (
        response.json()["teams"]["team"]["operator_communication_target_id"]
        == "ops-channel-2"
    )

    response = client.post(
        "/api/addons/team-addon/projects",
        json={
            "id": "project",
            "team_id": "team",
            "name": "Project",
            "lead_agent_id": "lead",
            "lead_session_id": "session-lead",
            "git": {"host_repo_path": "/srv/repo", "bare_repo_name": "repo.git"},
        },
    )
    assert response.status_code == 201
    assert response.json()["projects"]["project"]["git"]["bare_repo_name"] == "repo.git"

    response = client.patch(
        "/api/addons/team-addon/projects/project",
        json={
            "git": {
                "host_repo_path": "/srv/repo",
                "bare_repo_path": "/srv/bare/repo.git",
                "base_branch": "develop",
            }
        },
    )
    assert response.status_code == 200
    git = response.json()["projects"]["project"]["git"]
    assert git["bare_repo_path"] == "/srv/bare/repo.git"
    assert git["base_branch"] == "develop"
    assert git["bare_repo_name"] is None

    response = client.post(
        "/api/addons/team-addon/projects",
        json={
            "id": "project-2",
            "team_id": "team",
            "name": "Project 2",
            "lead_agent_id": "lead-2",
            "lead_session_id": "session-lead-2",
            "git": {
                "host_repo_path": "/srv/repo",
                "bare_repo_name": "a.git",
                "bare_repo_path": "/srv/b.git",
            },
        },
    )
    assert response.status_code == 400
    assert "exactly one" in response.json()["error"]


def test_team_addon_admin_api_task_graph_fields(tmp_path: Path) -> None:
    client = _client(tmp_path)

    client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
        },
    )
    client.post(
        "/api/addons/team-addon/projects",
        json={
            "id": "project",
            "team_id": "team",
            "name": "Project",
            "lead_agent_id": "lead",
            "lead_session_id": "session-lead",
        },
    )
    client.post(
        "/api/addons/team-addon/projects",
        json={
            "id": "other-project",
            "team_id": "team",
            "name": "Other Project",
            "lead_agent_id": "other-lead",
            "lead_session_id": "session-other-lead",
        },
    )
    client.post(
        "/api/addons/team-addon/agents",
        json={
            "id": "backend",
            "team_id": "team",
            "project_id": "project",
            "role": "backend",
            "session_id": "session-backend",
            "reports_to_agent_id": "lead",
        },
    )

    # Parent task and a dependency task that the child will depend on.
    assert (
        client.post(
            "/api/addons/team-addon/tasks",
            json={
                "id": "parent",
                "owner_agent_id": "backend",
                "project_id": "project",
                "priority": 10,
                "title": "Parent task",
                "description": "Coordinating task.",
                "created_by_agent_id": "lead",
            },
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/addons/team-addon/tasks",
            json={
                "id": "dependency",
                "owner_agent_id": "backend",
                "project_id": "project",
                "priority": 10,
                "title": "Dependency task",
                "description": "Must finish first.",
                "created_by_agent_id": "lead",
            },
        ).status_code
        == 201
    )

    # Child task with parent + dependency graph fields.
    response = client.post(
        "/api/addons/team-addon/tasks",
        json={
            "id": "child",
            "owner_agent_id": "backend",
            "project_id": "project",
            "priority": 10,
            "title": "Child task",
            "description": "Depends on the dependency task.",
            "created_by_agent_id": "lead",
            "parent_task_id": "parent",
            "dependency_task_ids": ["dependency"],
        },
    )
    assert response.status_code == 201
    state = response.json()
    child = state["tasks"]["child"]
    assert child["parent_task_id"] == "parent"
    assert child["dependency_task_ids"] == ["dependency"]
    assert child["dependency_status"] == "waiting"
    assert state["tasks"]["parent"]["child_task_ids"] == ["child"]

    # PATCH can update dependency links.
    response = client.patch(
        "/api/addons/team-addon/tasks/child",
        json={"dependency_task_ids": []},
    )
    assert response.status_code == 200
    child = response.json()["tasks"]["child"]
    assert child["dependency_task_ids"] == []
    assert child["dependency_status"] == "satisfied"

    # PATCH can clear a parent link with explicit null.
    response = client.patch(
        "/api/addons/team-addon/tasks/child",
        json={"parent_task_id": None},
    )
    assert response.status_code == 200
    state = response.json()
    assert state["tasks"]["child"]["parent_task_id"] is None
    assert state["tasks"]["parent"]["child_task_ids"] == []

    # Cross-project parent is rejected with a 400.
    client.post(
        "/api/addons/team-addon/tasks",
        json={
            "id": "other-task",
            "owner_agent_id": "lead",
            "project_id": "other-project",
            "priority": 10,
            "title": "Other project task",
            "description": "Belongs to a different project.",
            "created_by_agent_id": "other-lead",
        },
    )
    response = client.post(
        "/api/addons/team-addon/tasks",
        json={
            "id": "bad-parent",
            "owner_agent_id": "backend",
            "project_id": "project",
            "priority": 10,
            "title": "Bad parent",
            "description": "Parent in another project.",
            "created_by_agent_id": "lead",
            "parent_task_id": "other-task",
        },
    )
    assert response.status_code == 400

    # Payloads WITHOUT the new keys still work (regression).
    response = client.post(
        "/api/addons/team-addon/tasks",
        json={
            "id": "plain",
            "owner_agent_id": "backend",
            "project_id": "project",
            "priority": 10,
            "title": "Plain task",
            "description": "No graph fields.",
            "created_by_agent_id": "lead",
        },
    )
    assert response.status_code == 201
    plain = response.json()["tasks"]["plain"]
    assert plain["parent_task_id"] is None
    assert plain["dependency_task_ids"] == []


def test_team_addon_admin_api_review_decision_and_release_flow(tmp_path: Path) -> None:
    client = _client(tmp_path)

    client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
        },
    )
    client.post(
        "/api/addons/team-addon/projects",
        json={
            "id": "project",
            "team_id": "team",
            "name": "Project",
            "lead_agent_id": "lead",
            "lead_session_id": "session-lead",
            "goals": [{"id": "goal", "description": "Ship reviewed work"}],
            "git": {"host_repo_path": "/srv/repo", "bare_repo_name": "repo.git"},
        },
    )
    client.post(
        "/api/addons/team-addon/agents",
        json={
            "id": "backend",
            "team_id": "team",
            "project_id": "project",
            "role": "backend",
            "session_id": "session-backend",
            "reports_to_agent_id": "lead",
        },
    )
    client.post(
        "/api/addons/team-addon/tasks",
        json={
            "id": "task",
            "owner_agent_id": "backend",
            "project_id": "project",
            "priority": 10,
            "title": "Build backend",
            "description": "Implement the backend task.",
            "created_by_agent_id": "lead",
        },
    )
    client.post(
        "/api/addons/team-addon/deliverables",
        json={
            "id": "deliverable",
            "task_id": "task",
            "producer_agent_id": "backend",
            "summary": "Backend task is ready.",
            "artifact_refs": ["artifacts/backend.tar.gz"],
        },
    )
    client.post(
        "/api/addons/team-addon/review-requests",
        json={
            "id": "review",
            "deliverable_id": "deliverable",
            "reviewer_agent_id": "lead",
        },
    )

    response = client.post(
        "/api/addons/team-addon/review-requests/review/decision",
        json={"state": "accepted", "findings": ["looks good"]},
    )
    assert response.status_code == 200
    state = response.json()
    assert state["review_requests"]["review"]["state"] == "accepted"
    assert state["review_requests"]["review"]["findings"] == ["looks good"]
    assert state["review_requests"]["review"]["decision_at"] is not None
    assert state["deliverables"]["deliverable"]["state"] == "accepted"
    assert state["tasks"]["task"]["state"] == "accepted"

    repo = TeamStateRepository(tmp_path / "runtime")

    def _attach_feature_branch(current):
        current.deliverables["deliverable"].git_refs.feature_branch = (
            "team/team/features/task-backend"
        )
        return current

    repo.update(_attach_feature_branch)

    response = client.post(
        "/api/addons/team-addon/release-packages",
        json={
            "id": "release",
            "project_id": "project",
            "deliverable_ids": ["deliverable"],
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["release_packages"]["release"]["deliverable_ids"] == [
        "deliverable"
    ]
    assert len(payload["published_event_ids"]) == 1
    events = InternalEventBus(internal_event_store_path(tmp_path / "runtime")).list_events()
    assert len(events) == 1
    assert events[0].event_type == "team.git.promotion_requested"
    assert events[0].payload["promotion_kind"] == "release"
    assert events[0].payload["target_branch"] == "team/team/releases/release"

    response = client.get("/api/addons/team-addon/release-packages")
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["release_packages"]] == ["release"]

    response = client.get("/api/addons/team-addon/release-packages/release")
    assert response.status_code == 200
    assert response.json()["project_id"] == "project"

    response = client.get("/api/addons/team-addon/release-packages/missing")
    assert response.status_code == 404

    response = client.get("/api/addons/team-addon/git-promotions")
    assert response.status_code == 200
    assert isinstance(response.json()["git_promotions"], list)

    response = client.get("/api/addons/team-addon/git-promotions/missing")
    assert response.status_code == 404


def test_team_addon_admin_api_review_profile_flow(tmp_path: Path) -> None:
    client = _client(tmp_path)

    client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
        },
    )
    client.post(
        "/api/addons/team-addon/projects",
        json={
            "id": "project",
            "team_id": "team",
            "name": "Project",
            "lead_agent_id": "lead",
            "lead_session_id": "session-lead",
        },
    )
    client.post(
        "/api/addons/team-addon/agents",
        json={
            "id": "backend",
            "team_id": "team",
            "project_id": "project",
            "role": "backend",
            "session_id": "session-backend",
            "reports_to_agent_id": "lead",
        },
    )
    client.post(
        "/api/addons/team-addon/tasks",
        json={
            "id": "task",
            "owner_agent_id": "backend",
            "project_id": "project",
            "priority": 10,
            "title": "Build backend",
            "description": "Implement the backend task.",
            "created_by_agent_id": "lead",
        },
    )
    client.post(
        "/api/addons/team-addon/deliverables",
        json={
            "id": "deliverable",
            "task_id": "task",
            "producer_agent_id": "backend",
            "summary": "Backend task is ready.",
        },
    )

    # Create a review profile.
    response = client.post(
        "/api/addons/team-addon/review-profiles",
        json={
            "id": "profile",
            "team_id": "team",
            "project_id": "project",
            "name": "Security review",
            "description": "Checklist for security-sensitive work.",
            "applicable_roles": ["reviewer", "project_lead"],
            "criteria": [
                {"id": "authz", "label": "Authorization", "required": True},
                {"id": "logging", "label": "Logging", "description": "No secrets", "required": False},
            ],
        },
    )
    assert response.status_code == 201

    # List shows it.
    response = client.get("/api/addons/team-addon/review-profiles")
    assert response.status_code == 200
    listing = response.json()
    assert [item["id"] for item in listing["review_profiles"]] == ["profile"]

    # Get by id.
    response = client.get("/api/addons/team-addon/review-profiles/profile")
    assert response.status_code == 200
    profile = response.json()
    assert profile["name"] == "Security review"
    assert [criterion["id"] for criterion in profile["criteria"]] == ["authz", "logging"]

    # Missing profile returns 404.
    assert client.get("/api/addons/team-addon/review-profiles/missing").status_code == 404

    # Patch name and archived flag.
    response = client.patch(
        "/api/addons/team-addon/review-profiles/profile",
        json={"name": "Security review v2", "archived": True},
    )
    assert response.status_code == 200
    response = client.get("/api/addons/team-addon/review-profiles/profile")
    profile = response.json()
    assert profile["name"] == "Security review v2"
    assert profile["archived"] is True

    # Archived profiles cannot be assigned through the Admin API.
    response = client.post(
        "/api/addons/team-addon/review-requests",
        json={
            "id": "review-archived",
            "deliverable_id": "deliverable",
            "reviewer_agent_id": "lead",
            "review_profile_id": "profile",
        },
    )
    assert response.status_code == 400
    assert "archived" in response.json()["error"]

    # Un-archive so the profile can be attached to a review request.
    client.patch(
        "/api/addons/team-addon/review-profiles/profile",
        json={"archived": False},
    )

    # Create a review request with the profile + instructions.
    response = client.post(
        "/api/addons/team-addon/review-requests",
        json={
            "id": "review",
            "deliverable_id": "deliverable",
            "reviewer_agent_id": "lead",
            "review_profile_id": "profile",
            "instructions": "Pay attention to authz.",
        },
    )
    assert response.status_code == 201
    review = response.json()["review_requests"]["review"]
    assert review["review_profile_id"] == "profile"
    assert review["instructions"] == "Pay attention to authz."

    # GET /state shows the profile on the review.
    state = client.get("/api/addons/team-addon/state").json()
    assert state["review_requests"]["review"]["review_profile_id"] == "profile"

    # Decision with criterion results persists them.
    response = client.post(
        "/api/addons/team-addon/review-requests/review/decision",
        json={
            "state": "accepted",
            "findings": ["looks good"],
            "criteria": [
                {"criterion_id": "authz", "state": "passed", "finding": "scoped"},
                {"criterion_id": "logging", "state": "skipped"},
            ],
        },
    )
    assert response.status_code == 200
    review = response.json()["review_requests"]["review"]
    assert review["state"] == "accepted"
    assert [result["criterion_id"] for result in review["criteria"]] == ["authz", "logging"]
    assert review["criteria"][0]["state"] == "passed"
    assert review["criteria"][0]["finding"] == "scoped"


def test_team_addon_admin_api_review_request_without_profile_still_works(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)

    client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
        },
    )
    client.post(
        "/api/addons/team-addon/projects",
        json={
            "id": "project",
            "team_id": "team",
            "name": "Project",
            "lead_agent_id": "lead",
            "lead_session_id": "session-lead",
        },
    )
    client.post(
        "/api/addons/team-addon/agents",
        json={
            "id": "backend",
            "team_id": "team",
            "project_id": "project",
            "role": "backend",
            "session_id": "session-backend",
            "reports_to_agent_id": "lead",
        },
    )
    client.post(
        "/api/addons/team-addon/tasks",
        json={
            "id": "task",
            "owner_agent_id": "backend",
            "project_id": "project",
            "priority": 10,
            "title": "Build backend",
            "description": "Implement the backend task.",
            "created_by_agent_id": "lead",
        },
    )
    client.post(
        "/api/addons/team-addon/deliverables",
        json={
            "id": "deliverable",
            "task_id": "task",
            "producer_agent_id": "backend",
            "summary": "Backend task is ready.",
        },
    )

    # Regression: review request without a profile still succeeds.
    response = client.post(
        "/api/addons/team-addon/review-requests",
        json={
            "id": "review",
            "deliverable_id": "deliverable",
            "reviewer_agent_id": "lead",
        },
    )
    assert response.status_code == 201
    assert response.json()["review_requests"]["review"]["review_profile_id"] is None

    # Unknown profile id on a review request returns 400.
    response = client.post(
        "/api/addons/team-addon/review-requests",
        json={
            "id": "review-2",
            "deliverable_id": "deliverable",
            "reviewer_agent_id": "lead",
            "review_profile_id": "missing-profile",
        },
    )
    assert response.status_code == 400


def test_team_addon_admin_api_patch_team_rejects_stale_revision(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
        },
    )

    response = client.patch(
        "/api/addons/team-addon/teams/team",
        json={"operator_communication_target_id": "ops", "expected_revision": 0},
    )

    assert response.status_code == 409
    assert response.json()["current_revision"] == 1


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
