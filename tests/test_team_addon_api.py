from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from starlette.testclient import TestClient

from agent_sandbox.admin.service import AdminService
from agent_sandbox.admin.web.app import create_app
from agent_sandbox.events.internal import InternalEventBus, internal_event_store_path
from agent_sandbox.model_usage.models import ModelInferenceUsageEvent
from agent_sandbox.model_usage.store import ModelUsageStore
from truman_team_addon.models import (
    AgentActionRecord,
    AgentInboxItem,
    AgentRuntime,
    EphemeralAssignment,
    EphemeralAssignmentState,
    GitPromotionRecord,
    ReleasePackage,
    TeamInboxItemState,
    TeamInboxItemType,
    TeamRecordState,
    TeamTaskState,
)
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

def _seed_runtime_operations_state(runtime_dir: Path) -> TeamStateRepository:
    repo = TeamStateRepository(runtime_dir)
    repo.create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
    )
    repo.create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
    )
    repo.register_agent(
        agent_id="backend",
        team_id="team",
        project_id="project",
        role="backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
    )
    repo.create_or_reprioritize_task(
        task_id="blocked-task",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Blocked task",
        description="This task is blocked.",
        created_by_agent_id="lead",
        state=TeamTaskState.BLOCKED,
    )
    repo.create_or_reprioritize_task(
        task_id="dependency-task",
        owner_agent_id="backend",
        project_id="project",
        priority=2,
        title="Dependency task",
        description="This task is still open.",
        created_by_agent_id="lead",
    )
    repo.create_or_reprioritize_task(
        task_id="waiting-task",
        owner_agent_id="backend",
        project_id="project",
        priority=3,
        title="Waiting task",
        description="This task waits on another task.",
        created_by_agent_id="lead",
        dependency_task_ids=["dependency-task"],
    )
    repo.record_deliverable(
        deliverable_id="deliverable",
        task_id="waiting-task",
        producer_agent_id="backend",
        summary="Waiting task deliverable.",
        artifact_refs=["artifacts/backend.tar.gz"],
    )

    now = datetime(2026, 1, 1, tzinfo=UTC)
    lease_until = datetime(2099, 1, 1, tzinfo=UTC)
    next_wake_at = datetime(2000, 1, 1, tzinfo=UTC)

    def _mutate(state):
        state.agent_runtimes["backend"] = AgentRuntime(
            agent_id="backend",
            wake_leased_until=lease_until,
            active_dispatch_id="dispatch-backend",
            wake_reason="inbox",
            last_wake_at=now - timedelta(minutes=5),
            next_wake_at=next_wake_at,
            no_progress_count=4,
            last_progress_at=now - timedelta(minutes=10),
            updated_at=now,
        )
        state.inbox["inbox-1"] = AgentInboxItem(
            id="inbox-1",
            recipient_agent_id="backend",
            sender_agent_id="lead",
            task_id="waiting-task",
            item_type=TeamInboxItemType.STATUS_REQUEST,
            state=TeamInboxItemState.PENDING,
            payload={"question": "Status update?"},
            created_at=now,
            updated_at=now,
        )
        state.inbox["escalation-1"] = AgentInboxItem(
            id="escalation-1",
            recipient_agent_id="backend",
            sender_agent_id="lead",
            task_id="waiting-task",
            item_type=TeamInboxItemType.ESCALATION,
            state=TeamInboxItemState.PENDING,
            payload={"reason": "blocked too long"},
            created_at=now,
            updated_at=now,
        )
        state.action_records["action-1"] = AgentActionRecord(
            id="action-1",
            agent_id="backend",
            dispatch_id="dispatch-backend",
            action={"type": "build"},
            success=False,
            error="build failed",
            created_at=now,
        )
        state.release_packages["release-1"] = ReleasePackage(
            id="release-1",
            project_id="project",
            deliverable_ids=["deliverable"],
            artifact_refs=["artifacts/backend.tar.gz"],
            feature_branches=["team/team/features/waiting-task"],
            release_branch="team/team/releases/release-1",
            canonical_branch="team/releases/release-1",
            git_promotion_record_ids=["promotion-1"],
            state=TeamRecordState.BLOCKED,
            error="manual hold",
            created_at=now,
        )
        state.git_promotions["promotion-1"] = GitPromotionRecord(
            id="promotion-1",
            project_id="project",
            actor_agent_id="backend",
            source_branch="team/team/features/waiting-task",
            target_branch="team/team/releases/release-1",
            promotion_kind="release",
            task_id="waiting-task",
            deliverable_id="deliverable",
            release_package_id="release-1",
            state="failed",
            commit_sha="abc123",
            outputs={"reason": "merge conflict"},
            error="merge conflict",
            created_at=now,
            updated_at=now,
        )
        state.ephemeral_assignments["ephemeral-failed"] = EphemeralAssignment(
            id="ephemeral-failed",
            parent_agent_id="backend",
            project_id="project",
            task_id="waiting-task",
            session_id="session-backend-ephemeral",
            title="Failed ephemeral work",
            prompt="Run the failing ephemeral task.",
            state=EphemeralAssignmentState.FAILED,
            error="spawn failed",
            created_at=now,
            updated_at=now,
        )
        state.ephemeral_assignments["ephemeral-cleanup"] = EphemeralAssignment(
            id="ephemeral-cleanup",
            parent_agent_id="backend",
            project_id="project",
            task_id="waiting-task",
            session_id="session-backend-cleanup",
            title="Cleanup ephemeral work",
            prompt="Clean up the ephemeral task.",
            state=EphemeralAssignmentState.CLEANUP_PENDING,
            created_at=now,
            updated_at=now,
        )
        return state

    repo.update(_mutate)
    return repo


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
    assert disabled.get("/api/addons/team-addon/timeline").status_code == 404
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
    state = response.json()
    assert state["revision"] == 0
    assert state["runtime_operations"]["agents"] == []
    assert state["runtime_operations"]["attention"] == []

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

    timeline = enabled.get("/api/addons/team-addon/timeline")
    assert timeline.status_code == 200
    assert timeline.json() == {
        "revision": 0,
        "derived": True,
        "audit_log": False,
        "timestampless_count": 0,
        "entries": [],
        "source": "derived_from_current_team_state",
        "description": "Derived timeline from the current TeamState snapshot.",
        "filters": {},
    }

    ui = enabled.get("/addons/team-addon/")
    assert ui.status_code == 200
    assert "data-team-addon-ui" in ui.text
    assert 'name="manager_display_name"' in ui.text
    assert 'name="lead_display_name"' in ui.text
    assert 'name="display_name"' in ui.text
    assert "Review &amp; Delivery" in ui.text
    assert 'id="review-delivery-view"' in ui.text
    assert 'id="delivery-filter-blocked"' in ui.text
    assert 'name="parent_task_id"' in ui.text
    assert ui.text.count('name="lead_session_id"') == 1
    assert 'name="review_profile_id"' in ui.text
    assert 'id="create-review-profile-form"' in ui.text
    assert 'name="bare_repo"' in ui.text
    assert 'data-options="git-providers"' in ui.text
    assert "data-agent-source-picker" in ui.text
    assert 'id="edit-modal"' in ui.text
    assert 'id="edit-record-form"' in ui.text

    script = enabled.get("/addons/team-addon/team-ui.js")
    assert script.status_code == 200
    assert 'const API_BASE = "/api/addons/team-addon";' in script.text
    assert "renderRuntimeOperations" in script.text
    assert "runtimeOperationsView" in script.text
    assert "data-runtime-filter" in script.text
    assert "needs_attention" in script.text
    assert "due_leased_agents" in script.text
    assert "blocked_waiting_work" in script.text
    assert "ephemeral_work" in script.text
    assert "dependency_task_ids" in script.text
    assert "review_profile_id" in script.text
    assert "parseCriterionResults" in script.text
    assert "renderReviewDeliveryWorkspace" in script.text
    assert "submitReviewDecision" in script.text
    assert "bindWorkspaceReviewDecisionForms" in script.text
    assert "bindGeneratedIds" in script.text
    assert "bindExistingAgentPickers" in script.text
    assert "submitEditForm" in script.text
    assert "elements.editModal?.contains(event.target)" in script.text
    assert "firstEditableField?.focus()" in script.text
    assert "refreshSelects({ includeEditForm = false } = {})" in script.text
    assert "elements.editForm?.contains(select)" in script.text
    assert "gitToBareRepo" in script.text
    assert "created_by_agent_id: text(data.created_by_agent_id)" in script.text
    assert "fieldTooltips" in script.text
    assert "create-task-form:created_by_agent_id" in script.text
    assert "decorateFieldTooltips" in script.text
    assert "data-dismiss-action-id" in script.text
    assert 'fetch(`${API_BASE}/costs`' in script.text
    assert "renderTeamCostCounter" in script.text
    assert "renderProjectCostCounter" in script.text
    assert "max_cost_usd" in script.text
    assert "cost_limit_reached" in script.text

    stylesheet = enabled.get("/addons/team-addon/team-ui.css")
    assert stylesheet.status_code == 200
    assert ".team-shell" in stylesheet.text
    assert ".review-delivery-panel" in stylesheet.text
    assert ".review-delivery-view" in stylesheet.text
    assert ".delivery-filter" in stylesheet.text
    assert ".state-pill.rejected" in stylesheet.text
    assert ".state-pill.blocked" in stylesheet.text
    assert ".edit-modal" in stylesheet.text
    assert ".record-actions" in stylesheet.text
    assert ".field-tooltip" in stylesheet.text
    assert ".field-tooltip-bubble" in stylesheet.text
    assert ".cost-counter" in stylesheet.text
    assert ".cost-counter--warning" in stylesheet.text

    html = enabled.get("/addons/team-addon/")
    assert html.status_code == 200
    assert 'name="max_cost_usd"' in html.text


def test_team_addon_admin_api_runtime_operations_smoke(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    _seed_runtime_operations_state(runtime_dir)
    client = _client(tmp_path)

    response = client.get("/api/addons/team-addon/state")
    assert response.status_code == 200
    state = response.json()

    agents = {row["id"]: row for row in state["runtime_operations"]["agents"]}
    assert {row["id"] for row in state["runtime_operations"]["agents"]} == {
        "backend",
        "lead",
        "manager",
    }
    backend = agents["backend"]
    assert backend["wake_due"] is True
    assert backend["lease_active"] is True
    assert backend["active_dispatch_id"] == "dispatch-backend"
    assert backend["wake_reason"] == "inbox"

    attention = {row["kind"]: row for row in state["runtime_operations"]["attention"]}
    assert set(attention) == {
        "blocked_task",
        "waiting_dependency_task",
        "pending_inbox_item",
        "pending_escalation",
        "failed_action_record",
        "failed_git_promotion",
        "blocked_release_package",
        "failed_ephemeral_assignment",
        "cleanup_pending_ephemeral_assignment",
    }
    assert attention["blocked_task"]["id"] == "blocked-task"
    assert attention["blocked_task"]["state"] == "blocked"
    assert attention["waiting_dependency_task"]["id"] == "waiting-task"
    assert attention["waiting_dependency_task"]["dependency_status"] == "waiting"
    assert attention["pending_inbox_item"]["inbox_item_id"] == "inbox-1"
    assert attention["pending_escalation"]["inbox_item_id"] == "escalation-1"
    assert attention["failed_action_record"]["dispatch_id"] == "dispatch-backend"
    assert attention["failed_action_record"]["error"] == "build failed"
    assert attention["failed_git_promotion"]["promotion_id"] == "promotion-1"
    assert attention["failed_git_promotion"]["release_package_id"] == "release-1"
    assert attention["blocked_release_package"]["release_package_id"] == "release-1"
    assert attention["blocked_release_package"]["state"] == "blocked"
    assert attention["failed_ephemeral_assignment"]["assignment_id"] == "ephemeral-failed"
    assert attention["failed_ephemeral_assignment"]["state"] == "failed"
    assert attention["cleanup_pending_ephemeral_assignment"]["assignment_id"] == "ephemeral-cleanup"
    assert attention["cleanup_pending_ephemeral_assignment"]["state"] == "cleanup_pending"


def test_team_addon_admin_api_costs_smoke(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    _seed_runtime_operations_state(runtime_dir)
    ModelUsageStore(runtime_dir / "model-usage.db").upsert_event(
        ModelInferenceUsageEvent(
            event_id="usage-1",
            occurred_at=datetime(2026, 6, 16, 12, 0, tzinfo=UTC),
            provider="openai",
            provider_endpoint_identifier="responses",
            model="gpt-5.2-codex",
            input_tokens=1000,
            output_tokens=500,
            total_tokens=1500,
            request_status="success",
            normalized_usage_status="available",
            raw_usage_metadata={
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 500,
                    "total_tokens": 1500,
                }
            },
            session_id="session-backend",
        )
    )
    client = _client(tmp_path)

    response = client.get("/api/addons/team-addon/costs?backfill=false")

    assert response.status_code == 200
    payload = response.json()
    assert payload["team_costs"]["team"]["events_count"] == 1
    assert payload["project_costs"]["project"]["events_count"] == 1
    assert payload["project_costs"]["project"]["estimated_cost"] is not None


def test_team_addon_admin_api_dismisses_failed_action_attention(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    _seed_runtime_operations_state(runtime_dir)
    client = _client(tmp_path)
    revision = client.get("/api/addons/team-addon/state").json()["revision"]

    response = client.post(
        "/api/addons/team-addon/action-records/action-1/dismiss",
        json={
            "dismissed_by": "operator",
            "dismissed_reason": "Superseded by follow-up wake.",
            "expected_revision": revision,
        },
    )

    assert response.status_code == 200
    state = response.json()
    record = state["action_records"]["action-1"]
    assert record["success"] is False
    assert record["dismissed_by"] == "operator"
    assert record["dismissed_reason"] == "Superseded by follow-up wake."
    assert record["dismissed_at"]
    assert all(
        row["kind"] != "failed_action_record"
        for row in state["runtime_operations"]["attention"]
    )


def test_team_addon_timeline_route_filters_current_state(tmp_path: Path) -> None:
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
    client.post(
        "/api/addons/team-addon/projects",
        json={
            "id": "project",
            "team_id": "team",
            "name": "Project",
            "lead_agent_id": "lead",
            "lead_session_id": "session-lead",
            "expected_revision": 1,
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
            "expected_revision": 2,
        },
    )
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
            "state": "accepted",
            "expected_revision": 3,
        },
    )
    assert response.status_code == 201
    response = client.patch(
        "/api/addons/team-addon/tasks/task",
        json={
            "title": "Build backend v2",
            "expected_revision": 4,
        },
    )
    assert response.status_code == 200
    response = client.post(
        "/api/addons/team-addon/deliverables",
        json={
            "id": "deliverable",
            "task_id": "task",
            "producer_agent_id": "backend",
            "summary": "Backend task is ready.",
            "expected_revision": 5,
        },
    )
    assert response.status_code == 201
    response = client.post(
        "/api/addons/team-addon/review-requests",
        json={
            "id": "review",
            "deliverable_id": "deliverable",
            "reviewer_agent_id": "lead",
            "state": "accepted",
            "decision_at": "2026-01-01T12:00:00",
            "expected_revision": 6,
        },
    )
    assert response.status_code == 201

    timeline = client.get(
        "/api/addons/team-addon/timeline",
        params=[
            ("type", "task"),
            ("type", "review"),
            ("status", "accepted"),
            ("attention_needed", "false"),
            ("agent_id", "lead"),
        ],
    )

    assert timeline.status_code == 200
    payload = timeline.json()
    assert payload["derived"] is True
    assert payload["audit_log"] is False
    assert payload["source"] == "derived_from_current_team_state"
    assert payload["filters"] == {
        "agent_id": "lead",
        "type": ["task", "review"],
        "status": ["accepted"],
        "attention_needed": False,
    }
    assert payload["timestampless_count"] == 0
    assert {entry["id"] for entry in payload["entries"]} == {
        "task:task:created",
        "task:task:updated",
        "review:review:created",
        "review:review:decision",
    }
    assert all(entry["status"] == "accepted" for entry in payload["entries"])
    assert all(entry["attention_needed"] is False for entry in payload["entries"])


def test_team_addon_admin_api_create_flow(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
            "manager_display_name": "  Team Manager  ",
            "expected_revision": 0,
        },
    )
    assert response.status_code == 201
    assert response.json()["revision"] == 1
    assert response.json()["agents"]["manager"]["display_name"] == "Team Manager"

    response = client.post(
        "/api/addons/team-addon/projects",
        json={
            "id": "project",
            "team_id": "team",
            "name": "Project",
            "lead_agent_id": "lead",
            "lead_session_id": "session-lead",
            "lead_display_name": "  Project Lead  ",
            "goals": [{"id": "goal", "description": "Ship reviewed work"}],
            "expected_revision": 1,
        },
    )
    assert response.status_code == 201
    assert response.json()["revision"] == 2
    assert response.json()["agents"]["lead"]["display_name"] == "Project Lead"

    response = client.post(
        "/api/addons/team-addon/agents",
        json={
            "id": "backend",
            "team_id": "team",
            "project_id": "project",
            "role": "backend",
            "session_id": "session-backend",
            "reports_to_agent_id": "lead",
            "display_name": "  Backend Agent  ",
            "ephemeral_policy": {"max_concurrent": 2},
            "expected_revision": 2,
        },
    )
    assert response.status_code == 201
    assert response.json()["revision"] == 3
    assert response.json()["agents"]["backend"]["display_name"] == "Backend Agent"

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
    assert "runtime_operations" in state
    assert {row["id"] for row in state["runtime_operations"]["agents"]} == {
        "backend",
        "lead",
        "manager",
    }


def test_team_addon_admin_api_create_task_defaults_creator_to_operator(tmp_path: Path) -> None:
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

    response = client.post(
        "/api/addons/team-addon/tasks",
        json={
            "id": "task",
            "owner_agent_id": "backend",
            "project_id": "project",
            "priority": 10,
            "title": "Build backend",
            "description": "Implement the backend task.",
        },
    )

    assert response.status_code == 201
    task = response.json()["tasks"]["task"]
    assert task["created_by_agent_id"] is None
    assert task["created_by_actor"] == "operator:admin"

    timeline = client.get("/api/addons/team-addon/timeline?type=task").json()
    created = next(
        entry for entry in timeline["entries"] if entry["id"] == "task:task:created"
    )
    assert created["actor_agent_id"] is None
    assert created["actor_subject"] == "operator:admin"


def test_team_addon_admin_api_edits_core_records(tmp_path: Path) -> None:
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
    response = client.post(
        "/api/addons/team-addon/projects",
        json={
            "id": "project",
            "team_id": "team",
            "name": "Project",
            "lead_agent_id": "lead",
            "lead_session_id": "session-lead",
            "expected_revision": 1,
        },
    )
    assert response.status_code == 201
    response = client.post(
        "/api/addons/team-addon/agents",
        json={
            "id": "backend",
            "team_id": "team",
            "project_id": "project",
            "role": "backend",
            "session_id": "session-backend",
            "reports_to_agent_id": "lead",
            "expected_revision": 2,
        },
    )
    assert response.status_code == 201

    response = client.patch(
        "/api/addons/team-addon/teams/team",
        json={
            "name": "Renamed Team",
            "operator_communication_target_id": None,
            "archived": True,
            "expected_revision": 3,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["revision"] == 4
    assert body["teams"]["team"]["name"] == "Renamed Team"
    assert body["teams"]["team"]["operator_communication_target_id"] is None
    assert body["teams"]["team"]["archived"] is True
    assert body["agents"]["manager"]["status"] == "archived"
    assert body["agents"]["lead"]["status"] == "archived"
    assert body["agents"]["backend"]["status"] == "archived"

    response = client.patch(
        "/api/addons/team-addon/projects/project",
        json={
            "name": "Renamed Project",
            "status": "blocked",
            "goals": [{"id": "goal", "description": "Updated goal"}],
            "git": {
                "host_repo_path": "/srv/repo",
                "bare_repo": "/srv/git/repo.git",
                "provider": "github",
            },
            "expected_revision": 4,
        },
    )
    assert response.status_code == 200
    body = response.json()
    project = body["projects"]["project"]
    assert body["revision"] == 5
    assert project["name"] == "Renamed Project"
    assert project["status"] == "blocked"
    assert project["goals"][0]["id"] == "goal"
    assert project["git"]["bare_repo_path"] == "/srv/git/repo.git"
    assert project["git"]["provider"] == "github"

    response = client.patch(
        "/api/addons/team-addon/agents/backend",
        json={
            "project_id": "project",
            "role": "implementer",
            "session_id": "session-backend-2",
            "reports_to_agent_id": "manager",
            "display_name": None,
            "ephemeral_policy": {"max_concurrent": 4},
            "status": "paused",
            "expected_revision": 5,
        },
    )
    assert response.status_code == 200
    body = response.json()
    backend = body["agents"]["backend"]
    assert body["revision"] == 6
    assert backend["role"] == "implementer"
    assert backend["session_id"] == "session-backend-2"
    assert backend["reports_to_agent_id"] == "manager"
    assert backend["display_name"] is None
    assert backend["ephemeral_policy"]["max_concurrent"] == 4
    assert backend["status"] == "paused"
    assert body["agents"]["lead"]["subordinate_agent_ids"] == []
    assert body["agents"]["manager"]["subordinate_agent_ids"] == ["lead", "backend"]


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
    assert {
        role_key: body["agents"][agent_id]["ephemeral_policy"]["max_concurrent"]
        for role_key, agent_id in body["materialized"]["agent_ids"].items()
    } == {
        "manager": 0,
        "project_lead": 1,
        "implementer": 2,
        "reviewer": 1,
        "release_operator": 1,
    }
    assert len(body["projects"]) == 1
    assert len(body["review_profiles"]) == 1
    assert len(body["tasks"]) == 2
    assert body["teams"]["team-auto-dev-squad"]["project_ids"] == [
        "project-auto-dev-squad"
    ]
    assert body["projects"]["project-auto-dev-squad"]["goals"][0]["id"] == (
        "project-auto-dev-squad-auto-dev-reviewed-delivery"
    )
    assert body["tasks"]["project-auto-dev-squad-release-handoff"]["dependency_task_ids"] == [
        "project-auto-dev-squad-implement-scoped-slice"
    ]
    assert (
        body["review_profiles"]["project-auto-dev-squad-auto-dev-review"]["criteria"][0]["id"]
        == "scope"
    )


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
            "max_cost_usd": 10.5,
        },
    )
    assert response.status_code == 201
    assert (
        response.json()["teams"]["team"]["operator_communication_target_id"]
        == "ops-channel"
    )
    assert response.json()["teams"]["team"]["max_cost_usd"] == 10.5

    response = client.patch(
        "/api/addons/team-addon/teams/team",
        json={"operator_communication_target_id": "ops-channel-2", "max_cost_usd": 12.25},
    )
    assert response.status_code == 200
    assert (
        response.json()["teams"]["team"]["operator_communication_target_id"]
        == "ops-channel-2"
    )
    assert response.json()["teams"]["team"]["max_cost_usd"] == 12.25

    response = client.patch(
        "/api/addons/team-addon/teams/team",
        json={"max_cost_usd": None},
    )
    assert response.status_code == 200
    assert response.json()["teams"]["team"]["max_cost_usd"] is None

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


def test_team_addon_admin_api_creates_operator_directive(tmp_path: Path) -> None:
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

    response = client.post(
        "/api/addons/team-addon/operator-directives",
        json={
            "team_id": "team",
            "project_id": "project",
            "title": "Status report",
            "message": "Ask the lead for a status report.",
            "source": "discord",
            "source_message_id": "discord-msg-1",
        },
    )

    assert response.status_code == 201
    body = response.json()
    item = body["operator_directive"]
    assert item["recipient_agent_id"] == "manager"
    assert item["item_type"] == "operator_directive"
    assert item["payload"]["title"] == "Status report"
    assert item["payload"]["message"] == "Ask the lead for a status report."
    assert item["payload"]["project_id"] == "project"
    assert item["payload"]["source_message_id"] == "discord-msg-1"
    assert item["payload"]["created_by"] == "operator:admin"
    assert body["agent_runtimes"]["manager"]["next_wake_at"] is None


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

    # Empty strings are meaningful on edit: they clear the optional description.
    response = client.patch(
        "/api/addons/team-addon/review-profiles/profile",
        json={"description": ""},
    )
    assert response.status_code == 200
    response = client.get("/api/addons/team-addon/review-profiles/profile")
    profile = response.json()
    assert profile["description"] == ""

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
