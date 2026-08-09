from __future__ import annotations

import asyncio
import json
import subprocess
import zipfile
from io import BytesIO
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MethodType, SimpleNamespace

import httpx
from PIL import Image
import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from evander.addons import AddonContext
from evander.admin.services.managed_git_common import ManagedGitConflictError
from evander.admin.composition import build_admin_runtime
from evander.admin.web.app import create_app
from evander.config import GatewayConfig
from evander.events.internal import InternalEventBus, internal_event_store_path
from evander.events.scheduled import (
    ScheduledInternalEventStore,
    scheduled_internal_event_store_path,
)
from evander.model_usage.models import ModelInferenceUsageEvent
from evander.model_usage.store import ModelUsageStore
from evander.models import SessionManifest
from evander.repositories.session_manifests import JsonSessionManifestRepository
from evander_team_addon import api as team_addon_api
from evander_team_addon.api import (
    _bootstrap_payload_for_request,
    _conversation_snapshot_events,
    _require_human_review_decision_identity,
    _resolved_release_policy_for_admin,
    _state_snapshot_events,
    _strict_human_identity_from_request,
    _team_state_payload_for_request,
)
from evander_team_addon.models import (
    AgentActionRecord,
    AgentContextClearingPolicy,
    AgentInboxItem,
    AgentRuntime,
    AgentStandDownSource,
    AgentTask,
    AgentStatus,
    AgentVoiceProfile,
    Deliverable,
    DeliverableReviewManifest,
    DeliverableVerification,
    EphemeralAssignment,
    EphemeralAssignmentState,
    GitPromotionRecord,
    HumanReviewAssignment,
    HumanReviewFinding,
    HumanReviewRequest,
    HumanReviewRequirement,
    HumanReviewRequirementSnapshot,
    ProjectGitConfig,
    ProjectReleasePolicy,
    ReleaseCandidateManifest,
    ReleaseGateFailure,
    ReleasePackage,
    ReleaseScopeValidation,
    ReviewRequest,
    TeamConversationMessage,
    TeamBlocker,
    TeamBlockerEventType,
    TeamBlockerRoute,
    TeamBlockerSourceKind,
    TeamBlockerState,
    TeamCase,
    TeamCaseStatus,
    TeamDispatchActivity,
    TeamDispatchActivityStatus,
    TeamInboxItemState,
    TeamInboxItemType,
    TeamOutboxRecord,
    TeamOutboxStatus,
    TeamRecordState,
    TeamTaskState,
)
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.providers import (
    _filtered_team_state_payload,
    build_voice_reconciliation_context,
)
from evander_team_addon.services.domain.chat import CHAT_MESSAGE_QUEUED_EVENT_TYPE
from evander_team_addon.services.domain.git_common import GIT_PROMOTION_EVENT_TYPE
from evander_team_addon.services.commands.outbox import (
    TEAM_OUTBOX_PUBLISH_REQUESTED_EVENT_TYPE,
)
from evander_team_addon.services.domain.human_reviews import (
    HUMAN_REVIEW_ASSIGNMENT_UNAVAILABLE,
    HUMAN_REVIEW_PREPARATION_FAILED,
    HUMAN_REVIEW_TARGET_EXPIRED,
    HUMAN_REVIEW_TESTING_BLOCKED,
)
from evander_team_addon.services.agent_action_service import TeamAgentActionService
from evander_team_addon.services.admin_service import TeamAdminService
from evander_team_addon.services.chat_service import TeamChatService
from evander_team_addon.services.commands import TeamTaskUpdateCommandService
from evander_team_addon.services.runtime_service import TeamRuntimeService


def _team_admin_service(repo: TeamStateRepository) -> TeamAdminService:
    return TeamAdminService(repo)


def _team_chat_service(repo: TeamStateRepository) -> TeamChatService:
    return TeamChatService(repo)


def _team_runtime_service(repo: TeamStateRepository) -> TeamRuntimeService:
    return TeamRuntimeService(repo)


def test_team_outbox_publication_wake_is_durable_and_notifies_trigger_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notified_lock_paths: list[Path] = []
    monkeypatch.setattr(
        team_addon_api,
        "notify_wake_socket_for_lock",
        lambda lock_path: notified_lock_paths.append(Path(lock_path)) or True,
    )
    context = AddonContext(
        addon_id="team-addon",
        runtime_dir=tmp_path / "runtime",
        addon_runtime_dir=tmp_path / "runtime" / "addons" / "team-addon",
        project_dir=tmp_path,
        config=GatewayConfig(runtime_dir=tmp_path / "runtime"),
    )

    first = team_addon_api._request_team_outbox_publication(
        context,
        correlation_key="candidate-correction-recovery:blocker-1:12",
    )
    second = team_addon_api._request_team_outbox_publication(
        context,
        correlation_key="candidate-correction-recovery:blocker-1:12",
    )

    events = InternalEventBus(internal_event_store_path(context.runtime_dir)).list_events()
    assert first["event_ids"] == second["event_ids"]
    assert first["wake_notified"] is True
    assert second["wake_notified"] is True
    assert len(events) == 1
    assert events[0].event_type == TEAM_OUTBOX_PUBLISH_REQUESTED_EVENT_TYPE
    assert events[0].correlation_key == "candidate-correction-recovery:blocker-1:12"
    assert notified_lock_paths == [
        context.runtime_dir / "trigger-orchestrator.lock",
        context.runtime_dir / "trigger-orchestrator.lock",
    ]


def _team_agent_action_service(repo: TeamStateRepository) -> TeamAgentActionService:
    return TeamAgentActionService(repo)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_human_release_policy_resolves_directory_groups_and_rejects_empty_pool() -> None:
    assignments = [
        SimpleNamespace(
            login="Reviewer@Example.com",
            group_id="release-reviewers",
            directory_path="root/team",
        )
    ]
    permitted = {"reviewer@example.com"}
    user_store = SimpleNamespace(
        get_group=lambda group_id: (
            SimpleNamespace(group_id=group_id)
            if group_id == "release-reviewers"
            else None
        ),
        list_user_group_assignments=lambda: list(assignments),
        get_user=lambda login: (
            SimpleNamespace(login=login)
            if str(login).casefold() == "reviewer@example.com"
            else None
        ),
        can=lambda login, permission, directory_path=None: (
            str(login).casefold() in permitted
            and permission == "team.human_reviews.decide"
            and directory_path == "root/team/project"
        ),
    )
    payload = {
        "release_policy": {
            "human_review_requirement": {
                "reviewer_group_ids": ["release-reviewers"],
                "checklist": [{"id": "smoke", "label": "Smoke test"}],
            }
        }
    }

    policy = _resolved_release_policy_for_admin(
        SimpleNamespace(user_store=user_store),
        payload,
        directory_path="root/team/project",
    )

    assert policy is not None
    assert policy.human_review_requirement is not None
    assert policy.human_review_requirement.directory_path == "root/team/project"
    assert policy.human_review_requirement.resolved_reviewer_user_ids == [
        "reviewer@example.com"
    ]

    permitted.clear()
    with pytest.raises(ValueError, match="no active reviewer"):
        _resolved_release_policy_for_admin(
            SimpleNamespace(user_store=user_store),
            payload,
            directory_path="root/team/project",
        )


@pytest.mark.parametrize(
    ("git_payload", "expected_directory", "expected_reviewer"),
    [
        (
            {
                "managed_repo_id": "mr_0123456789abcdefabcd",
                "directory_path": "root/new-project",
            },
            "root/new-project",
            "new-reviewer@example.com",
        ),
        (None, "root", "root-reviewer@example.com"),
    ],
)
def test_project_git_patch_re_resolves_existing_human_review_scope(
    tmp_path: Path,
    git_payload: dict[str, str] | None,
    expected_directory: str,
    expected_reviewer: str,
) -> None:
    runtime_dir = tmp_path / "runtime"
    repository = TeamStateRepository(runtime_dir)
    admin = TeamAdminService(repository)
    state = admin.create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
    )
    state = admin.create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
        git=ProjectGitConfig(
            managed_repo_id="mr_0123456789abcdefabcd",
            directory_path="root/old-project",
        ),
        release_policy=ProjectReleasePolicy(
            human_review_requirement=HumanReviewRequirement(
                reviewer_group_ids=["release-reviewers"],
                directory_path="root/old-project",
                resolved_reviewer_user_ids=["old-reviewer@example.com"],
            )
        ),
        expected_revision=state.revision,
    )
    assignments = [
        SimpleNamespace(
            login="old-reviewer@example.com",
            group_id="release-reviewers",
            directory_path="root/old-project",
        ),
        SimpleNamespace(
            login=expected_reviewer,
            group_id="release-reviewers",
            directory_path=expected_directory,
        ),
    ]
    known_users = {
        "old-reviewer@example.com",
        expected_reviewer,
    }
    user_store = SimpleNamespace(
        get_group=lambda group_id: (
            SimpleNamespace(group_id=group_id)
            if group_id == "release-reviewers"
            else None
        ),
        list_user_group_assignments=lambda: list(assignments),
        get_user=lambda login: (
            SimpleNamespace(login=str(login).casefold())
            if str(login).casefold() in known_users
            else None
        ),
        can=lambda login, permission, directory_path=None: (
            permission == "team.human_reviews.decide"
            and str(login).casefold() in known_users
            and directory_path == expected_directory
        ),
    )
    auth = _HumanApiAuthStub(
        login=expected_reviewer,
        owner=True,
    )
    auth.user_store = user_store
    client = TestClient(
        Starlette(
            routes=team_addon_api.admin_api_routes(
                AddonContext(
                    addon_id="team-addon",
                    runtime_dir=runtime_dir,
                    addon_runtime_dir=runtime_dir / "addons" / "team-addon",
                    auth=auth,
                )
            )
        )
    )

    response = client.patch(
        "/projects/project",
        json={
            "git": git_payload,
            "expected_revision": state.revision,
        },
    )

    assert response.status_code == 200, response.text
    requirement = response.json()["projects"]["project"]["release_policy"][
        "human_review_requirement"
    ]
    assert requirement["directory_path"] == expected_directory
    assert requirement["resolved_reviewer_user_ids"] == [expected_reviewer]


def test_team_addon_registers_release_gate_retry_api_route(tmp_path: Path) -> None:
    routes = team_addon_api.admin_api_routes(
        AddonContext(
            addon_id="team-addon",
            runtime_dir=tmp_path,
            addon_runtime_dir=tmp_path / "addons" / "team-addon",
        )
    )

    retry_route = next(
        route
        for route in routes
        if route.path == "/release-packages/{release_package_id}/retry-gate"
    )
    assert retry_route.methods == {"POST"}

    verification_live_route = next(
        route
        for route in routes
        if route.path == "/release-packages/{release_package_id}/verification-live"
    )
    assert verification_live_route.methods == {"GET", "HEAD"}

    blocker_resolve_route = next(
        route
        for route in routes
        if route.path == "/blockers/{blocker_id}/resolve"
    )
    assert blocker_resolve_route.methods == {"POST"}
    dependency_composition_recovery_route = next(
        route
        for route in routes
        if route.path
        == "/blockers/{blocker_id}/dependency-composition-recovery"
    )
    assert dependency_composition_recovery_route.methods == {"POST"}
    candidate_recovery_route = next(
        route
        for route in routes
        if route.path == "/candidate-correction-recoveries/{blocker_id}"
    )
    assert candidate_recovery_route.methods == {"POST"}
    review_retire_route = next(
        route
        for route in routes
        if route.path == "/review-requests/{review_request_id}/retire"
    )
    assert review_retire_route.methods == {"POST"}
    task_repair_route = next(
        route
        for route in routes
        if route.path == "/tasks/{task_id}/repair-state"
    )
    assert task_repair_route.methods == {"POST"}
    review_withdraw_route = next(
        route
        for route in routes
        if route.path == "/review-requests/{review_request_id}/withdraw"
    )
    assert review_withdraw_route.methods == {"POST"}
    human_review_collection_route = next(
        route for route in routes if route.path == "/human-review-requests"
    )
    assert human_review_collection_route.methods == {"GET", "HEAD"}
    human_review_detail_route = next(
        route
        for route in routes
        if route.path
        == "/human-review-requests/{human_review_request_id}"
    )
    assert human_review_detail_route.methods == {"GET", "HEAD"}
    for suffix in (
        "decisions",
        "reassign",
        "resume",
        "preparation-result",
    ):
        mutation_route = next(
            route
            for route in routes
            if route.path
            == f"/human-review-requests/{{human_review_request_id}}/{suffix}"
        )
        assert mutation_route.methods == {"POST"}
    report_issue_route = next(
        route
        for route in routes
        if route.path
        == "/release-packages/{release_package_id}/report-issue"
    )
    assert report_issue_route.methods == {"POST"}
    for path in (
        "/human-review-findings/{human_review_finding_id}/resolution-proposal",
        "/human-review-findings/{human_review_finding_id}/route",
        "/cases/{case_id}/close-post-release",
    ):
        mutation_route = next(route for route in routes if route.path == path)
        assert mutation_route.methods == {"POST"}
    activity_route = next(route for route in routes if route.path == "/dispatch-events")
    assert activity_route.methods == {"GET", "HEAD"}
    outcome_collection_routes = [
        route for route in routes if route.path == "/outcome-kinds"
    ]
    assert {frozenset(route.methods or set()) for route in outcome_collection_routes} == {
        frozenset({"GET", "HEAD"}),
        frozenset({"POST"}),
    }
    outcome_detail_routes = [
        route
        for route in routes
        if route.path == "/outcome-kinds/{definition_id}"
    ]
    assert {frozenset(route.methods or set()) for route in outcome_detail_routes} == {
        frozenset({"GET", "HEAD"}),
        frozenset({"PATCH"}),
    }


@pytest.fixture(autouse=True)
def _isolate_managed_git_authorized_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVANDER_GIT_USER", "evander-team-tests-no-such-user")


def _png_avatar_bytes(color: tuple[int, int, int] = (23, 111, 122)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (32, 24), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _write_config(
    tmp_path: Path,
    *,
    enable_team_addon: bool,
    enable_directory_scoped_config: bool = False,
) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "managed-git-repositories.json").write_text(
        json.dumps(
            {
                "schema": "evander.managed-git-repositories/v1",
                "schema_version": 1,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
                "repositories": {
                    "mr_0123456789abcdefabcd": {
                        "managed_repo_id": "mr_0123456789abcdefabcd",
                        "directory_path": "root",
                        "canonical_repository_identity": "test://team-project",
                        "canonical_repository_url": "test://team-project",
                        "provider": "generic",
                        "display_name": "Team project test repository",
                        "default_branch": "main",
                        "default_branch_source": "upstream",
                        "refresh_source": "test://team-project",
                        "refresh_source_kind": "remote",
                        "internal_bare_path": str(
                            runtime_dir / "managed-git" / "mr_0123456789abcdefabcd.git"
                        ),
                        "host_commit_identity": {
                            "name": "Evander Control Plane",
                            "email": "evander@localhost",
                        },
                        "bindings": [],
                        "created_at": "2026-01-01T00:00:00Z",
                        "updated_at": "2026-01-01T00:00:00Z",
                        "refreshed_at": None,
                        "maintenance_at": None,
                    }
                },
                "migrations": {"test-fixture": {"status": "applied"}},
            }
        ),
        encoding="utf-8",
    )
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
    if enable_directory_scoped_config:
        lines.append("enable_directory_scoped_config: true")
    if enable_team_addon:
        lines.extend(
            [
                "addon_loader:",
                "  modules:",
                '    - "evander_team_addon"',
            ]
        )
    config_path.write_text("\n".join(lines), encoding="utf-8")
    return config_path


def _client(
    tmp_path: Path, *, enable_team_addon: bool = True, unlock_privileged: bool = True
) -> TestClient:
    client = TestClient(
        create_app(
            build_admin_runtime(_write_config(tmp_path, enable_team_addon=enable_team_addon))
        )
    )
    if unlock_privileged:
        _unlock_privileged_operations(client)
    return client


def _unlock_privileged_operations(client: TestClient) -> None:
    response = client.post("/api/admin/privileged/unlock")
    assert response.status_code == 200


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _git_dir(git_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", f"--git-dir={git_dir}", *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _post_backend_role(
    client: TestClient,
    *,
    expected_revision: int | None = None,
    role_id: str = "project__backend",
    project_id: str = "project",
) -> dict:
    payload: dict[str, object] = {
        "id": role_id,
        "team_id": "team",
        "project_id": project_id,
        "name": "Backend",
    }
    if expected_revision is not None:
        payload["expected_revision"] = expected_revision
    response = client.post("/api/addons/team-addon/roles", json=payload)
    assert response.status_code == 201
    return response.json()


def _seed_runtime_operations_state(runtime_dir: Path) -> TeamStateRepository:
    repo = TeamStateRepository(runtime_dir)
    _team_admin_service(repo).create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
    )
    _team_admin_service(repo).create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
    )
    _team_admin_service(repo).create_role(
        role_id="project__backend",
        team_id="team",
        project_id="project",
        name="Backend",
    )
    _team_admin_service(repo).register_agent(
        agent_id="backend",
        team_id="team",
        project_id="project",
        role_id="project__backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
    )
    _team_admin_service(repo).create_or_reprioritize_task(
        task_id="blocked-task",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Blocked task",
        description="This task is blocked.",
        created_by_agent_id="lead",
        state=TeamTaskState.BLOCKED,
    )
    _team_admin_service(repo).create_or_reprioritize_task(
        task_id="dependency-task",
        owner_agent_id="backend",
        project_id="project",
        priority=2,
        title="Dependency task",
        description="This task is still open.",
        created_by_agent_id="lead",
    )
    _team_admin_service(repo).create_or_reprioritize_task(
        task_id="waiting-task",
        owner_agent_id="backend",
        project_id="project",
        priority=3,
        title="Waiting task",
        description="This task waits on another task.",
        created_by_agent_id="lead",
        dependency_task_ids=["dependency-task"],
    )
    _team_admin_service(repo).record_deliverable(
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
            policy_version=2,
            scope_validation=ReleaseScopeValidation(state="pending"),
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


def _attach_pending_human_review(repo: TeamStateRepository) -> None:
    requirement = HumanReviewRequirementSnapshot(
        directory_path="root/team",
        configured_group_ids=["human-reviewers"],
        initially_eligible_user_ids=["reviewer@example.com"],
        test_instructions="Exercise the candidate before deciding.",
    )
    now = datetime(2026, 1, 2, tzinfo=UTC)

    def _mutate(state):
        release = state.release_packages["release-1"]
        release.candidate_commit_sha = "candidate-sha"
        release.candidate_tree_hash = "candidate-tree"
        release.manifest = ReleaseCandidateManifest(
            project_id=release.project_id,
            release_package_id=release.id,
            candidate_commit_sha="candidate-sha",
            candidate_tree_hash="candidate-tree",
            release_branch=release.release_branch,
            deliverables=[{"deliverable_id": "deliverable"}],
            human_review_requirement=requirement,
        )
        state.human_review_requests["human-review-1"] = HumanReviewRequest(
            id="human-review-1",
            release_package_id=release.id,
            candidate_commit_sha="candidate-sha",
            candidate_tree_hash="candidate-tree",
            requirement_snapshot=requirement,
            current_assignment=HumanReviewAssignment(
                eligible_user_ids=["reviewer@example.com"],
            ),
            state="pending",
            ready_at=now,
            created_at=now,
        )
        return state

    repo.update(_mutate)


class _HumanApiAuthStub:
    def __init__(
        self,
        *,
        login: str,
        auth_mode: str = "local_password",
        owner: bool = False,
        privileged: bool = True,
        allow_decide: bool = True,
        group_assignments: list[object] | None = None,
        known_users: list[str] | None = None,
        permission_users: list[str] | None = None,
        team_chat_mutation_directories: set[str] | None = None,
    ) -> None:
        self.config = SimpleNamespace(auth_mode=auth_mode)
        self.login = login
        self.owner = owner
        self.privileged = privileged
        self.allow_decide = allow_decide
        self.permission_calls: list[tuple[str, str | None]] = []
        self.team_chat_mutation_directories = set(team_chat_mutation_directories or ())
        known = {
            value.casefold()
            for value in (known_users or [login])
        }
        permitted = {
            value.casefold()
            for value in (permission_users or [login])
        }
        self.user_store = SimpleNamespace(
            list_user_group_assignments=lambda _login=None: list(
                group_assignments or ()
            ),
            get_user=lambda value: (
                SimpleNamespace(login=str(value).casefold())
                if str(value).casefold() in known
                else None
            ),
            can=lambda value, permission, directory_path=None: (
                permission == "team.human_reviews.decide"
                and str(value).casefold() in permitted
                and directory_path is not None
            ),
        )

    def read_session(self, _request) -> dict[str, str]:
        return {
            "sid": f"session-{self.login}",
            "login": self.login,
            "name": self.login.split("@", 1)[0].title(),
        }

    def require(self, request) -> dict[str, str]:
        return self.read_session(request)

    def privileged_for_request(self, _request) -> bool:
        return self.privileged

    def can(
        self,
        _request,
        permission: str,
        directory_path: str | None = None,
    ) -> bool:
        if self.owner:
            return True
        return (
            permission == "team_chats.mutate"
            and directory_path in self.team_chat_mutation_directories
        )

    def require_permission(
        self,
        _request,
        permission: str,
        directory_path: str | None = None,
    ) -> None:
        self.permission_calls.append((permission, directory_path))
        if permission == "team.human_reviews.decide" and self.allow_decide:
            return
        if permission == "admin.owner" and self.owner:
            return
        raise PermissionError(f"Permission required: {permission}")


def test_human_identity_rejects_generic_admin_fallback() -> None:
    class FallbackAuth:
        config = SimpleNamespace(auth_mode="local_password")

        @staticmethod
        def read_session(_request):
            return None

        @staticmethod
        def require(_request):
            return {
                "sid": "local-dev",
                "login": "admin",
                "name": "Admin",
            }

    with pytest.raises(PermissionError, match="real authenticated"):
        _strict_human_identity_from_request(FallbackAuth(), object())  # type: ignore[arg-type]

    auth_none = _HumanApiAuthStub(
        login="reviewer@example.com",
        auth_mode="none",
    )
    with pytest.raises(PermissionError, match="real authenticated"):
        _strict_human_identity_from_request(auth_none, object())  # type: ignore[arg-type]


def test_human_decision_identity_enforces_assignment_group_scope_and_permission() -> None:
    auth = _HumanApiAuthStub(
        login="Reviewer@Example.com",
        group_assignments=[
            SimpleNamespace(
                login="reviewer@example.com",
                group_id="human-reviewers",
                directory_path="root/team",
            )
        ],
    )
    human_request = SimpleNamespace(
        requirement_snapshot=SimpleNamespace(
            directory_path="root/team/sub",
            configured_group_ids=["human-reviewers"],
        ),
        current_assignment=SimpleNamespace(
            eligible_user_ids=["reviewer@example.com"],
            explicitly_assigned_user_ids=[],
        ),
    )

    identity = _require_human_review_decision_identity(
        auth,
        object(),  # type: ignore[arg-type]
        human_request,
        payload={"decision": "approved"},
    )

    assert identity["user_id"] == "reviewer@example.com"
    assert auth.permission_calls == [
        ("team.human_reviews.decide", "root/team/sub")
    ]
    with pytest.raises(ValueError, match="derived from the authenticated session"):
        _require_human_review_decision_identity(
            auth,
            object(),  # type: ignore[arg-type]
            human_request,
            payload={
                "decision": "approved",
                "reviewer_user_id": "spoofed@example.com",
            },
        )

    stale_membership_auth = _HumanApiAuthStub(login="reviewer@example.com")
    with pytest.raises(PermissionError, match="no longer a member"):
        _require_human_review_decision_identity(
            stale_membership_auth,
            object(),  # type: ignore[arg-type]
            human_request,
        )

    class PermissionDeniedAuth(_HumanApiAuthStub):
        def require_permission(
            self,
            _request,
            permission: str,
            directory_path: str | None = None,
        ) -> None:
            del directory_path
            raise PermissionError(f"Permission required: {permission}")

    permission_denied_auth = PermissionDeniedAuth(
        login="reviewer@example.com",
        group_assignments=[
            SimpleNamespace(
                login="reviewer@example.com",
                group_id="human-reviewers",
                directory_path="root/team",
            )
        ],
    )
    with pytest.raises(PermissionError, match="team.human_reviews.decide"):
        _require_human_review_decision_identity(
            permission_denied_auth,
            object(),  # type: ignore[arg-type]
            human_request,
        )


def test_explicit_human_assignment_can_decide_but_agent_principal_cannot(
    tmp_path: Path,
) -> None:
    explicit_runtime_dir = tmp_path / "explicit" / "runtime"
    explicit_repository = _seed_runtime_operations_state(explicit_runtime_dir)
    _attach_pending_human_review(explicit_repository)

    def _assign_explicit_user(state):
        request = state.human_review_requests["human-review-1"]
        request.current_assignment = HumanReviewAssignment(
            eligible_user_ids=["explicit@example.com"],
            explicitly_assigned_user_ids=["explicit@example.com"],
            assigned_by_user_id="owner@example.com",
            assigned_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
        return state

    explicit_repository.update(_assign_explicit_user)
    explicit_client = TestClient(
        Starlette(
            routes=team_addon_api.admin_api_routes(
                AddonContext(
                    addon_id="team-addon",
                    runtime_dir=explicit_runtime_dir,
                    addon_runtime_dir=(
                        explicit_runtime_dir / "addons" / "team-addon"
                    ),
                    auth=_HumanApiAuthStub(login="explicit@example.com"),
                )
            )
        )
    )
    decided = explicit_client.post(
        "/human-review-requests/human-review-1/decisions",
        json={
            "decision": "blocked",
            "summary": "The assigned manual test is temporarily unavailable.",
            "expected_revision": 0,
            "request_id": "explicit-assignment-decision",
        },
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["human_review_request"]["state"] == "blocked"
    assert next(
        iter(explicit_repository.read().human_review_decisions.values())
    ).reviewer_user_id == "explicit@example.com"

    agent_runtime_dir = tmp_path / "agent" / "runtime"
    agent_repository = _seed_runtime_operations_state(agent_runtime_dir)
    _attach_pending_human_review(agent_repository)

    class _AgentPrincipalAuth(_HumanApiAuthStub):
        def read_session(self, request) -> dict[str, str]:
            session = super().read_session(request)
            session["kind"] = "agent"
            session["agent_id"] = "backend"
            return session

    agent_client = TestClient(
        Starlette(
            routes=team_addon_api.admin_api_routes(
                AddonContext(
                    addon_id="team-addon",
                    runtime_dir=agent_runtime_dir,
                    addon_runtime_dir=agent_runtime_dir / "addons" / "team-addon",
                    auth=_AgentPrincipalAuth(
                        login="reviewer@example.com",
                        group_assignments=[
                            SimpleNamespace(
                                login="reviewer@example.com",
                                group_id="human-reviewers",
                                directory_path="root/team",
                            )
                        ],
                    ),
                )
            )
        )
    )
    rejected = agent_client.post(
        "/human-review-requests/human-review-1/decisions",
        json={
            "decision": "blocked",
            "summary": "An agent must not decide a human gate.",
            "expected_revision": 0,
            "request_id": "agent-principal-decision",
        },
    )
    assert rejected.status_code == 403
    assert "real authenticated Admin Web session" in rejected.json()["error"]
    assert agent_repository.read().human_review_decisions == {}


def test_human_review_api_is_strict_scoped_and_projects_release_detail(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    repo = _seed_runtime_operations_state(runtime_dir)

    def _attach_project_git(state):
        state.projects["project"].git = ProjectGitConfig(
            managed_repo_id="mr_0123456789abcdefabcd",
            directory_path="root/team",
            base_branch="main",
        )
        return state

    repo.update(_attach_project_git)
    _attach_pending_human_review(repo)
    _team_admin_service(repo).create_or_reprioritize_task(
        task_id="source-task",
        owner_agent_id="backend",
        project_id="project",
        priority=4,
        title="Source contribution",
        description="Provide the source contribution.",
        created_by_agent_id="lead",
    )
    _team_admin_service(repo).record_deliverable(
        deliverable_id="source-deliverable",
        task_id="source-task",
        producer_agent_id="backend",
        summary="Source contribution summary.",
    )

    def _attach_contribution_graph(state):
        state.release_packages["release-1"].manifest.contribution_graph = {
            "deliverable": ["source-deliverable"],
        }
        return state

    repo.update(_attach_contribution_graph)
    managed_repo_calls: list[tuple[str, str | None]] = []

    class _ManagedGit:
        @staticmethod
        def get_repository(
            managed_repo_id: str,
            *,
            directory_path: str | None = None,
        ) -> dict[str, str]:
            managed_repo_calls.append((managed_repo_id, directory_path))
            return {
                "managed_repo_id": managed_repo_id,
                "canonical_repository_identity": "azure-devops:org/project/app",
                "display_name": "ExampleIndustryAPP",
            }

    addon_service = SimpleNamespace(managed_git_admin=_ManagedGit())
    reviewer_auth = _HumanApiAuthStub(login="reviewer@example.com")
    reviewer_app = Starlette(
        routes=team_addon_api.admin_api_routes(
            AddonContext(
                addon_id="team-addon",
                runtime_dir=runtime_dir,
                addon_runtime_dir=runtime_dir / "addons" / "team-addon",
                auth=reviewer_auth,
                service=addon_service,
            )
        )
    )
    reviewer_client = TestClient(reviewer_app)

    listed = reviewer_client.get(
        "/human-review-requests?assigned_to=me&state=pending"
    )
    detail = reviewer_client.get("/human-review-requests/human-review-1")
    release_detail = reviewer_client.get("/release-packages/release-1")

    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()["human_review_requests"]] == [
        "human-review-1"
    ]
    assert detail.status_code == 200
    assert detail.json()["release"]["candidate_commit_sha"] == "candidate-sha"
    assert detail.json()["project"]["name"] == "Project"
    assert detail.json()["target_deliverable_options"] == [
        {
            "deliverable_id": "deliverable",
            "task_id": "waiting-task",
            "task_title": "Waiting task",
            "producer": {
                "agent_id": "backend",
                "display_name": "backend",
            },
            "summary": "Waiting task deliverable.",
            "is_top_level": True,
        },
        {
            "deliverable_id": "source-deliverable",
            "task_id": "source-task",
            "task_title": "Source contribution",
            "producer": {
                "agent_id": "backend",
                "display_name": "backend",
            },
            "summary": "Source contribution summary.",
            "is_top_level": False,
        },
    ]
    assert release_detail.status_code == 200
    assert release_detail.json()["human_review"]["requests"][0]["id"] == (
        "human-review-1"
    )
    assert release_detail.json()["human_review"]["requests"][0][
        "target_deliverable_options"
    ] == detail.json()["target_deliverable_options"]
    assert release_detail.json()["candidate_checkout"] == {
        "canonical_repository_identity": "azure-devops:org/project/app",
        "repository_display_name": "ExampleIndustryAPP",
        "managed_repo_id": "mr_0123456789abcdefabcd",
        "release_ref": "refs/heads/team/team/releases/release-1",
        "candidate_commit_sha": "candidate-sha",
        "candidate_tree_hash": "candidate-tree",
        "checkout_command": (
            "evander-cli team checkout-release-candidate release-1 "
            "--target ./release-candidate-release-1 "
            "--expected-commit-sha candidate-sha "
            f"--runtime-dir {runtime_dir}"
        ),
    }
    assert managed_repo_calls == [
        ("mr_0123456789abcdefabcd", "root/team")
    ]

    compact_state = _team_state_payload_for_request(
        reviewer_auth,
        object(),  # type: ignore[arg-type]
        repo.read(),
    )
    assert "human_review_requests" not in compact_state
    assert "human_review_decisions" not in compact_state
    assert "human_review_findings" not in compact_state
    release_summary = compact_state["release_packages"]["release-1"]
    assert release_summary["human_review"]["state"] == "pending"
    assert release_summary["manifest"]["human_review_requirement"] == {
        "required": True,
        "preparation_mode": "manual",
    }
    agent_payload = _filtered_team_state_payload(
        repo.read(),
        team_id="team",
        project_id="project",
        agent_id="backend",
    )
    assert "human_review_requests" not in agent_payload
    assert agent_payload["release_packages"]["release-1"]["human_review"][
        "state"
    ] == "pending"
    assert agent_payload["release_packages"]["release-1"]["manifest"][
        "human_review_requirement"
    ] == {
        "required": True,
        "preparation_mode": "manual",
    }

    outsider_auth = _HumanApiAuthStub(login="outsider@example.com")
    outsider_app = Starlette(
        routes=team_addon_api.admin_api_routes(
            AddonContext(
                addon_id="team-addon",
                runtime_dir=runtime_dir,
                addon_runtime_dir=runtime_dir / "addons" / "team-addon",
                auth=outsider_auth,
                service=addon_service,
            )
        )
    )
    outsider_client = TestClient(outsider_app)
    assert (
        outsider_client.get("/human-review-requests/human-review-1").status_code
        == 403
    )
    outsider_release = outsider_client.get("/release-packages/release-1")
    assert outsider_release.status_code == 200
    assert "requests" not in outsider_release.json()["human_review"]
    assert outsider_release.json()["manifest"]["human_review_requirement"] == {
        "required": True,
        "preparation_mode": "manual",
    }
    assert "candidate_checkout" not in outsider_release.json()
    assert managed_repo_calls == [
        ("mr_0123456789abcdefabcd", "root/team")
    ]


def test_human_review_bundle_download_is_pinned_and_scoped(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    repo = _seed_runtime_operations_state(runtime_dir)

    def _attach_project_git(state):
        state.projects["project"].git = ProjectGitConfig(
            managed_repo_id="mr_0123456789abcdefabcd",
            directory_path="root/team",
        )
        return state

    repo.update(_attach_project_git)
    _attach_pending_human_review(repo)
    archive_calls: list[dict[str, object]] = []

    class _ManagedGit:
        @staticmethod
        def export_pinned_source_archive(
            managed_repo_id: str,
            directory_path: str,
            *,
            source_ref: str,
            commit_sha: str,
            tree_hash: str,
            archive_path: Path,
        ) -> dict[str, str]:
            archive_calls.append(
                {
                    "managed_repo_id": managed_repo_id,
                    "directory_path": directory_path,
                    "source_ref": source_ref,
                    "commit_sha": commit_sha,
                    "tree_hash": tree_hash,
                }
            )
            with zipfile.ZipFile(archive_path, mode="w") as archive:
                archive.writestr("src/Feature.cs", "namespace ReviewBundle;\n")
            return {
                "managed_repo_id": managed_repo_id,
                "directory_path": directory_path,
                "source_ref": source_ref,
                "commit_sha": commit_sha,
                "tree_hash": tree_hash,
            }

    addon_service = SimpleNamespace(managed_git_admin=_ManagedGit())
    reviewer_auth = _HumanApiAuthStub(login="reviewer@example.com")
    reviewer_client = TestClient(
        Starlette(
            routes=team_addon_api.admin_api_routes(
                AddonContext(
                    addon_id="team-addon",
                    runtime_dir=runtime_dir,
                    addon_runtime_dir=runtime_dir / "addons" / "team-addon",
                    auth=reviewer_auth,
                    service=addon_service,
                )
            )
        )
    )

    response = reviewer_client.get("/human-review-requests/human-review-1/review-bundle")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/zip")
    assert response.headers["cache-control"] == "no-store"
    assert 'filename="release-1-review-bundle.zip"' in response.headers["content-disposition"]
    assert archive_calls == [
        {
            "managed_repo_id": "mr_0123456789abcdefabcd",
            "directory_path": "root/team",
            "source_ref": "refs/heads/team/team/releases/release-1",
            "commit_sha": "candidate-sha",
            "tree_hash": "candidate-tree",
        }
    ]
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        assert archive.namelist() == ["src/Feature.cs", "EVANDER-REVIEW.json"]
        assert archive.read("src/Feature.cs") == b"namespace ReviewBundle;\n"
        manifest = json.loads(archive.read("EVANDER-REVIEW.json"))
    assert {
        key: manifest[key]
        for key in (
            "schema",
            "release_package_id",
            "human_review_request_id",
            "project",
            "candidate",
            "test_instructions",
        )
    } == {
        "schema": "evander.team.human-review-bundle/v1",
        "release_package_id": "release-1",
        "human_review_request_id": "human-review-1",
        "project": {
            "id": "project",
            "name": "Project",
            "directory_path": "root/team",
        },
        "candidate": {
            "source_ref": "refs/heads/team/team/releases/release-1",
            "commit_sha": "candidate-sha",
            "tree_hash": "candidate-tree",
        },
        "test_instructions": "Exercise the candidate before deciding.",
    }
    assert manifest["release_manifest"]["candidate_commit_sha"] == "candidate-sha"
    assert manifest["release_manifest"]["candidate_tree_hash"] == "candidate-tree"
    assert manifest["release_manifest"]["release_branch"] == "team/team/releases/release-1"
    assert [
        deliverable["deliverable_id"]
        for deliverable in manifest["release_manifest"]["deliverables"]
    ] == ["deliverable"]
    assert (
        manifest["release_manifest"]["human_review_requirement"]["test_instructions"]
        == "Exercise the candidate before deciding."
    )

    outsider_auth = _HumanApiAuthStub(login="outsider@example.com")
    outsider_client = TestClient(
        Starlette(
            routes=team_addon_api.admin_api_routes(
                AddonContext(
                    addon_id="team-addon",
                    runtime_dir=runtime_dir,
                    addon_runtime_dir=runtime_dir / "addons" / "team-addon",
                    auth=outsider_auth,
                    service=addon_service,
                )
            )
        )
    )
    assert (
        outsider_client.get("/human-review-requests/human-review-1/review-bundle").status_code
        == 403
    )
    assert len(archive_calls) == 1

    class _MovedReleaseManagedGit:
        @staticmethod
        def export_pinned_source_archive(*_args, **_kwargs) -> None:
            raise ManagedGitConflictError("Release ref no longer equals the pinned candidate")

    moved_release_client = TestClient(
        Starlette(
            routes=team_addon_api.admin_api_routes(
                AddonContext(
                    addon_id="team-addon",
                    runtime_dir=runtime_dir,
                    addon_runtime_dir=runtime_dir / "addons" / "team-addon",
                    auth=reviewer_auth,
                    service=SimpleNamespace(managed_git_admin=_MovedReleaseManagedGit()),
                )
            )
        )
    )
    moved_release = moved_release_client.get(
        "/human-review-requests/human-review-1/review-bundle"
    )
    assert moved_release.status_code == 409
    assert moved_release.json()["error"] == "Release ref no longer equals the pinned candidate"


@pytest.mark.parametrize(
    "reserved_member",
    [
        "EVANDER-REVIEW.json",
        "./evander-review.json",
        ".\\EVANDER-REVIEW.json",
        "source/../EVANDER-REVIEW.json",
    ],
)
def test_human_review_bundle_rejects_reserved_manifest_members(
    tmp_path: Path,
    reserved_member: str,
) -> None:
    archive_path = tmp_path / "candidate.zip"
    with zipfile.ZipFile(archive_path, mode="w") as archive:
        archive.writestr(reserved_member, "candidate-controlled\n")

    with pytest.raises(ValueError, match="reserved review-bundle path"):
        team_addon_api._append_human_review_bundle_manifest(
            archive_path,
            {"schema": "evander.team.human-review-bundle/v1"},
        )

    with zipfile.ZipFile(archive_path) as archive:
        assert archive.namelist() == [reserved_member]
        assert archive.read(reserved_member) == b"candidate-controlled\n"


def test_human_review_decision_api_is_privileged_revisioned_and_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_dir = tmp_path / "runtime"
    repo = _seed_runtime_operations_state(runtime_dir)
    _attach_pending_human_review(repo)
    auth = _HumanApiAuthStub(
        login="Reviewer@Example.com",
        privileged=False,
        group_assignments=[
            SimpleNamespace(
                login="reviewer@example.com",
                group_id="human-reviewers",
                directory_path="root/team",
            )
        ],
    )
    client = TestClient(
        Starlette(
            routes=team_addon_api.admin_api_routes(
                AddonContext(
                    addon_id="team-addon",
                    runtime_dir=runtime_dir,
                    addon_runtime_dir=runtime_dir / "addons" / "team-addon",
                    auth=auth,
                )
            )
        )
    )
    payload = {
        "decision": "blocked",
        "summary": "Preview credentials are unavailable.",
        "expected_revision": 0,
        "request_id": "decision-request-1",
    }

    locked = client.post(
        "/human-review-requests/human-review-1/decisions",
        json=payload,
    )
    assert locked.status_code == 403
    assert locked.json()["error"] == "Privileged operations are locked."

    observed_at = datetime(2026, 8, 7, 7, 50, tzinfo=UTC)
    monkeypatch.setattr(
        team_addon_api,
        "human_review_request_detail_payload",
        lambda *_args, **_kwargs: {
            "state": "blocked",
            "decision_projection": {"recorded_at": observed_at},
        },
    )
    auth.privileged = True
    decided = client.post(
        "/human-review-requests/human-review-1/decisions",
        json=payload,
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["result"]["idempotent_replay"] is False
    assert decided.json()["human_review_request"]["state"] == "blocked"
    assert decided.json()["human_review_request"]["decision_projection"] == {
        "recorded_at": "2026-08-07T07:50:00Z"
    }
    first_revision = decided.json()["revision"]
    state = repo.read()
    assert len(state.human_review_decisions) == 1
    decision = next(iter(state.human_review_decisions.values()))
    assert decision.reviewer_user_id == "reviewer@example.com"
    assert decision.reviewer_display_name_snapshot == "Reviewer"

    replay = client.post(
        "/human-review-requests/human-review-1/decisions",
        json=payload,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["result"]["idempotent_replay"] is True
    assert replay.json()["revision"] == first_revision
    assert repo.read().revision == first_revision
    assert len(repo.read().human_review_decisions) == 1

    conflict = client.post(
        "/human-review-requests/human-review-1/decisions",
        json={**payload, "summary": "Different inputs."},
    )
    assert conflict.status_code == 409
    assert "idempotency key reused" in conflict.json()["error"]

    spoofed = client.post(
        "/human-review-requests/human-review-1/decisions",
        json={
            **payload,
            "request_id": "decision-request-spoofed",
            "reviewer_user_id": "somebody-else@example.com",
        },
    )
    assert spoofed.status_code == 400
    assert "derived from the authenticated session" in spoofed.json()["error"]


def test_human_review_changes_requested_publishes_correction_wake_before_cron(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    repo = _seed_runtime_operations_state(runtime_dir)
    _attach_pending_human_review(repo)
    client = TestClient(
        Starlette(
            routes=team_addon_api.admin_api_routes(
                AddonContext(
                    addon_id="team-addon",
                    runtime_dir=runtime_dir,
                    addon_runtime_dir=runtime_dir / "addons" / "team-addon",
                    auth=_HumanApiAuthStub(
                        login="Reviewer@Example.com",
                        group_assignments=[
                            SimpleNamespace(
                                login="reviewer@example.com",
                                group_id="human-reviewers",
                                directory_path="root/team",
                            )
                        ],
                    ),
                )
            )
        )
    )
    payload = {
        "decision": "changes_requested",
        "summary": "The release needs a correction.",
        "findings": [
            {
                "text": "Correct the release-wide workflow failure.",
                "target_scope": "release_candidate",
                "evidence_refs": ["artifact-space:reports/release-failure.txt"],
            }
        ],
        "expected_revision": 0,
        "request_id": "changes-requested-1",
    }

    missing_scope = client.post(
        "/human-review-requests/human-review-1/decisions",
        json={
            **payload,
            "request_id": "changes-requested-missing-scope",
            "findings": [
                {
                    "text": "Correct the release-wide workflow failure.",
                    "evidence_refs": [
                        "artifact-space:reports/release-failure.txt"
                    ],
                }
            ],
        },
    )
    assert missing_scope.status_code == 400
    assert "must select the entire release candidate" in missing_scope.json()[
        "error"
    ]

    decided = client.post(
        "/human-review-requests/human-review-1/decisions",
        json=payload,
    )

    assert decided.status_code == 200, decided.text
    decided_payload = decided.json()
    assert decided_payload["result"]["idempotent_replay"] is False
    assert len(decided_payload["result"]["outbox_record_ids"]) == 1
    assert len(decided_payload["published_event_ids"]) == 1
    wake_events = [
        event
        for event in InternalEventBus(
            internal_event_store_path(runtime_dir)
        ).list_events()
        if event.event_type == "team.agent.wake_requested"
    ]
    assert len(wake_events) == 1
    assert wake_events[0].event_id == decided_payload["published_event_ids"][0]
    assert wake_events[0].payload["agent_id"] == "lead"
    assert wake_events[0].payload["reason"] == "inbox"

    replay = client.post(
        "/human-review-requests/human-review-1/decisions",
        json=payload,
    )

    assert replay.status_code == 200, replay.text
    replay_payload = replay.json()
    assert replay_payload["result"]["idempotent_replay"] is True
    assert replay_payload["published_event_ids"] == decided_payload["published_event_ids"]
    replay_wake_events = [
        event
        for event in InternalEventBus(
            internal_event_store_path(runtime_dir)
        ).list_events()
        if event.event_type == "team.agent.wake_requested"
    ]
    assert len(replay_wake_events) == 1


def test_human_review_owner_reassign_resume_and_preparation_result_api(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    repo = _seed_runtime_operations_state(runtime_dir)
    _attach_pending_human_review(repo)

    def _block_for_assignment(state):
        request = state.human_review_requests["human-review-1"]
        request.state = "blocked"
        request.ready_at = None
        request.current_assignment.eligible_user_ids = []
        state.release_packages["release-1"].state = TeamRecordState.BLOCKED
        return state

    repo.update(_block_for_assignment)
    owner_auth = _HumanApiAuthStub(
        login="owner@example.com",
        owner=True,
        known_users=["owner@example.com", "reviewer@example.com"],
        permission_users=["reviewer@example.com"],
    )
    client = TestClient(
        Starlette(
            routes=team_addon_api.admin_api_routes(
                AddonContext(
                    addon_id="team-addon",
                    runtime_dir=runtime_dir,
                    addon_runtime_dir=runtime_dir / "addons" / "team-addon",
                    auth=owner_auth,
                )
            )
        )
    )

    reassigned = client.post(
        "/human-review-requests/human-review-1/reassign",
        json={
            "user_ids": ["Reviewer@Example.com"],
            "expected_revision": 0,
            "request_id": "reassign-request-1",
        },
    )
    assert reassigned.status_code == 200, reassigned.text
    reassigned_request = reassigned.json()["human_review_request"]
    assert reassigned_request["state"] == "pending"
    assert reassigned_request["assignment_generation"] == 2
    assert reassigned_request["current_assignment"][
        "explicitly_assigned_user_ids"
    ] == ["reviewer@example.com"]
    assert reassigned_request["current_assignment"]["assigned_by_user_id"] == (
        "owner@example.com"
    )
    reassign_revision = reassigned.json()["revision"]
    notification_count = len(
        repo.list_outbox_records(
            intent_type="team.release.human_review_requested",
        )
    )
    assert notification_count == 1
    replay = client.post(
        "/human-review-requests/human-review-1/reassign",
        json={
            "user_ids": ["Reviewer@Example.com"],
            "expected_revision": 0,
            "request_id": "reassign-request-1",
        },
    )
    assert replay.status_code == 200
    assert replay.json()["result"]["idempotent_replay"] is True
    assert replay.json()["revision"] == reassign_revision
    assert (
        len(
            repo.list_outbox_records(
                intent_type="team.release.human_review_requested",
            )
        )
        == notification_count
    )

    repeated_assignment = client.post(
        "/human-review-requests/human-review-1/reassign",
        json={
            "user_ids": ["Reviewer@Example.com"],
            "expected_revision": 1,
            "request_id": "reassign-request-2",
        },
    )
    assert repeated_assignment.status_code == 200, repeated_assignment.text
    repeated_request = repeated_assignment.json()["human_review_request"]
    assert repeated_request["assignment_generation"] == 3
    generation_notifications = repo.list_outbox_records(
        intent_type="team.release.human_review_requested",
    )
    assert len(generation_notifications) == notification_count + 1
    assert {
        (
            record.payload["payload"]["recipient_user_id"],
            record.payload["payload"]["assignment_generation"],
        )
        for record in generation_notifications
    } == {
        ("reviewer@example.com", 2),
        ("reviewer@example.com", 3),
    }

    def _block_for_resume(state):
        request = state.human_review_requests["human-review-1"]
        request.state = "blocked"
        request.ready_at = None
        state.release_packages["release-1"].state = TeamRecordState.BLOCKED
        return state

    repo.update(_block_for_resume)
    resumed = client.post(
        "/human-review-requests/human-review-1/resume",
        json={
            "expected_revision": 2,
            "request_id": "resume-request-1",
        },
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["human_review_request"]["state"] == "pending"
    assert resumed.json()["human_review_request"]["revision"] == 3

    def _prepare_again(state):
        request = state.human_review_requests["human-review-1"]
        request.state = "preparing"
        request.ready_at = None
        request.requirement_snapshot.preparation_mode = "workflow"
        release = state.release_packages["release-1"]
        release.state = TeamRecordState.PENDING
        if release.manifest is not None:
            release.manifest.human_review_requirement.preparation_mode = "workflow"
        return state

    repo.update(_prepare_again)
    prepared = client.post(
        "/human-review-requests/human-review-1/preparation-result",
        json={
            "candidate_commit_sha": "candidate-sha",
            "candidate_tree_hash": "candidate-tree",
            "preparation_generation": 1,
            "target": {
                "url": "https://preview.example.test/release-1",
                "alias": "release-preview",
            },
            "expected_revision": 3,
            "request_id": "preparation-request-1",
        },
    )
    assert prepared.status_code == 200, prepared.text
    prepared_request = prepared.json()["human_review_request"]
    assert prepared_request["state"] == "pending"
    assert prepared_request["test_target"]["alias"] == "release-preview"

    non_owner_auth = _HumanApiAuthStub(login="reviewer@example.com")
    non_owner_client = TestClient(
        Starlette(
            routes=team_addon_api.admin_api_routes(
                AddonContext(
                    addon_id="team-addon",
                    runtime_dir=runtime_dir,
                    addon_runtime_dir=runtime_dir / "addons" / "team-addon",
                    auth=non_owner_auth,
                )
            )
        )
    )
    denied = non_owner_client.post(
        "/human-review-requests/human-review-1/reassign",
        json={
            "user_ids": ["reviewer@example.com"],
            "expected_revision": 4,
            "request_id": "reassign-request-denied",
        },
    )
    assert denied.status_code == 403
    assert "admin.owner" in denied.json()["error"]


@pytest.mark.parametrize(
    ("reason_code", "recovery_action"),
    [
        (HUMAN_REVIEW_ASSIGNMENT_UNAVAILABLE, "reassign"),
        (HUMAN_REVIEW_TESTING_BLOCKED, "resume"),
        (HUMAN_REVIEW_PREPARATION_FAILED, "resume"),
        (HUMAN_REVIEW_TARGET_EXPIRED, "resume"),
    ],
)
def test_generic_operator_blocker_resolution_rejects_human_review_recovery_without_mutation(
    tmp_path: Path,
    reason_code: str,
    recovery_action: str,
) -> None:
    runtime_dir = tmp_path / "runtime"
    repo = _seed_runtime_operations_state(runtime_dir)
    _attach_pending_human_review(repo)

    def _block_human_review(state):
        human_request = state.human_review_requests["human-review-1"]
        human_request.state = "blocked"
        human_request.ready_at = None
        if reason_code == HUMAN_REVIEW_ASSIGNMENT_UNAVAILABLE:
            human_request.current_assignment.eligible_user_ids = []
        state.release_packages["release-1"].state = TeamRecordState.BLOCKED
        state.blockers["human-review-terminal-blocker"] = TeamBlocker(
            id="human-review-terminal-blocker",
            team_id="team",
            project_id="project",
            source_kind=TeamBlockerSourceKind.HUMAN_REVIEW_REQUEST,
            source_id=human_request.id,
            reason_code=reason_code,
            owner_agent_id="lead",
            route=TeamBlockerRoute.OPERATOR,
            summary="Human review recovery requires an audited action.",
        )
        return state

    repo.update(_block_human_review)
    before = repo.read().model_dump(mode="json")
    client = TestClient(
        Starlette(
            routes=team_addon_api.admin_api_routes(
                AddonContext(
                    addon_id="team-addon",
                    runtime_dir=runtime_dir,
                    addon_runtime_dir=runtime_dir / "addons" / "team-addon",
                    auth=_HumanApiAuthStub(login="operator@example.com"),
                )
            )
        )
    )

    rejected = client.post(
        "/blockers/human-review-terminal-blocker/resolve",
        json={
            "note": "Attempt generic terminal resolution.",
            "expected_revision": before["revision"],
        },
    )

    assert rejected.status_code == 400
    assert (
        f"POST /human-review-requests/human-review-1/{recovery_action}"
        in rejected.json()["error"]
    )
    assert "generic blocker resolution cannot bypass" in rejected.json()["error"]
    assert repo.read().model_dump(mode="json") == before


def test_post_release_report_api_uses_session_identity_and_idempotency(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    repo = _seed_runtime_operations_state(runtime_dir)

    def _publish(state):
        release = state.release_packages["release-1"]
        release.state = TeamRecordState.ACCEPTED
        release.candidate_commit_sha = "candidate-sha"
        release.approved_commit_sha = "candidate-sha"
        release.published_commit_sha = "candidate-sha"
        release.published_at = datetime(2026, 1, 3, tzinfo=UTC)
        release.error = None
        return state

    repo.update(_publish)
    published_before = repo.read().release_packages["release-1"].model_copy(
        deep=True
    )
    release_ids_before = set(repo.read().release_packages)
    promotion_ids_before = set(repo.read().git_promotions)
    auth = _HumanApiAuthStub(login="Reporter@Example.com")
    client = TestClient(
        Starlette(
            routes=team_addon_api.admin_api_routes(
                AddonContext(
                    addon_id="team-addon",
                    runtime_dir=runtime_dir,
                    addon_runtime_dir=runtime_dir / "addons" / "team-addon",
                    auth=auth,
                )
            )
        )
    )
    expected_revision = repo.read().revision
    missing_evidence = client.post(
        "/release-packages/release-1/report-issue",
        json={
            "report": "This report has no supporting evidence.",
            "evidence_refs": [],
            "expected_revision": expected_revision,
            "request_id": "report-request-without-evidence",
        },
    )
    assert missing_evidence.status_code == 400
    assert repo.read().revision == expected_revision

    payload = {
        "report": "The published workflow fails on the final confirmation.",
        "evidence_refs": ["artifact-space:reports/release-1.png"],
        "expected_revision": expected_revision,
        "request_id": "report-request-1",
    }

    reported = client.post(
        "/release-packages/release-1/report-issue",
        json=payload,
    )
    assert reported.status_code == 200, reported.text
    reported_payload = reported.json()
    assert reported_payload["case"]["source"] == "post_release_feedback"
    assert reported_payload["case"]["metadata"]["reporter_user_id"] == (
        "reporter@example.com"
    )
    assert len(reported_payload["published_event_ids"]) == 1
    first_revision = reported_payload["revision"]
    first_result = reported_payload["result"]
    reported_state = repo.read()
    feedback_cases = [
        case
        for case in reported_state.cases.values()
        if case.source == "post_release_feedback"
    ]
    assert len(feedback_cases) == 1
    feedback_case = feedback_cases[0]
    assert feedback_case.id == first_result["case_id"]
    assert feedback_case.task_ids == [first_result["triage_task_id"]]
    triage_task = reported_state.tasks[first_result["triage_task_id"]]
    assert triage_task.parent_task_id is None
    assert triage_task.case_id == feedback_case.id
    feedback_inbox = [
        item
        for item in reported_state.inbox.values()
        if item.payload.get("case_id") == feedback_case.id
    ]
    assert len(feedback_inbox) == 1
    assert feedback_inbox[0].task_id == triage_task.id
    assert reported_state.agent_runtimes["lead"].next_wake_at is None
    assert len(first_result["outbox_record_ids"]) == 1
    report_outbox = {
        record.id: record for record in repo.list_outbox_records()
    }[first_result["outbox_record_ids"][0]]
    assert report_outbox.intent_type == "team.agent.wake_requested"
    assert report_outbox.payload["payload"]["agent_id"] == "lead"
    assert set(reported_state.release_packages) == release_ids_before
    wake_events = [
        event
        for event in InternalEventBus(
            internal_event_store_path(runtime_dir)
        ).list_events()
        if event.event_type == "team.agent.wake_requested"
    ]
    assert len(wake_events) == 1
    assert wake_events[0].event_id == reported_payload["published_event_ids"][0]
    assert wake_events[0].payload["agent_id"] == "lead"

    replay = client.post(
        "/release-packages/release-1/report-issue",
        json=payload,
    )
    assert replay.status_code == 200
    replay_payload = replay.json()
    assert replay_payload["result"]["idempotent_replay"] is True
    assert replay_payload["revision"] == first_revision
    assert replay_payload["published_event_ids"] == reported_payload["published_event_ids"]
    replay_state = repo.read()
    assert len(
        [
            case
            for case in replay_state.cases.values()
            if case.source == "post_release_feedback"
        ]
    ) == 1
    assert len(
        [
            task
            for task in replay_state.tasks.values()
            if task.case_id == feedback_case.id
        ]
    ) == 1
    assert len(
        [
            item
            for item in replay_state.inbox.values()
            if item.payload.get("case_id") == feedback_case.id
        ]
    ) == 1
    assert {
        record.id
        for record in repo.list_outbox_records()
        if record.id in first_result["outbox_record_ids"]
    } == set(first_result["outbox_record_ids"])
    assert len(
        [
            event
            for event in InternalEventBus(
                internal_event_store_path(runtime_dir)
            ).list_events()
            if event.event_type == "team.agent.wake_requested"
        ]
    ) == 1

    conflict = client.post(
        "/release-packages/release-1/report-issue",
        json={**payload, "report": "A different report."},
    )
    assert conflict.status_code == 409

    other_reporter = TestClient(
        Starlette(
            routes=team_addon_api.admin_api_routes(
                AddonContext(
                    addon_id="team-addon",
                    runtime_dir=runtime_dir,
                    addon_runtime_dir=runtime_dir / "addons" / "team-addon",
                    auth=_HumanApiAuthStub(login="other@example.com"),
                )
            )
        )
    )
    caller_collision = other_reporter.post(
        "/release-packages/release-1/report-issue",
        json={
            **payload,
            "expected_revision": first_revision,
        },
    )
    assert caller_collision.status_code == 400
    assert "request_id was reused" in caller_collision.json()["error"]

    spoofed = client.post(
        "/release-packages/release-1/report-issue",
        json={
            **payload,
            "request_id": "report-request-spoofed",
            "reporter_user_id": "somebody-else@example.com",
        },
    )
    assert spoofed.status_code == 400
    assert "derived from the authenticated session" in spoofed.json()["error"]

    correction_created = client.post(
        "/tasks",
        json={
            "id": "post-release-correction-root",
            "owner_agent_id": "backend",
            "project_id": "project",
            "case_id": feedback_case.id,
            "priority": 1,
            "title": "Correct the reported production regression",
            "description": "Implement and verify the triaged correction.",
            "originating_task_id": triage_task.id,
            "expected_revision": repo.read().revision,
        },
    )
    assert correction_created.status_code == 201, correction_created.text
    final_state = repo.read()
    correction_root = final_state.tasks["post-release-correction-root"]
    assert correction_root.parent_task_id is None
    assert correction_root.originating_task_id == triage_task.id
    assert correction_root.case_id == feedback_case.id
    assert correction_root.git_base_branch == published_before.release_branch
    assert correction_root.id in final_state.cases[feedback_case.id].task_ids

    published_after = final_state.release_packages["release-1"]
    assert published_after == published_before
    assert published_after.state == published_before.state
    assert published_after.candidate_commit_sha == (
        published_before.candidate_commit_sha
    )
    assert published_after.approved_commit_sha == (
        published_before.approved_commit_sha
    )
    assert published_after.published_commit_sha == (
        published_before.published_commit_sha
    )
    assert published_after.published_at == published_before.published_at
    assert set(final_state.release_packages) == release_ids_before
    assert set(final_state.git_promotions) == promotion_ids_before
    assert not any(
        promotion.promotion_kind == "rollback"
        for promotion in final_state.git_promotions.values()
    )


def test_human_finding_correction_and_post_release_closure_admin_api(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    repo = _seed_runtime_operations_state(runtime_dir)
    now = datetime(2026, 1, 4, tzinfo=UTC)

    def _seed(state):
        release = state.release_packages["release-1"]
        release.candidate_commit_sha = "candidate-sha"
        release.candidate_tree_hash = "candidate-tree"
        state.human_review_requests["human-review-correction"] = (
            HumanReviewRequest(
                id="human-review-correction",
                release_package_id=release.id,
                candidate_commit_sha="candidate-sha",
                candidate_tree_hash="candidate-tree",
                requirement_snapshot=HumanReviewRequirementSnapshot(
                    directory_path="root",
                    configured_group_ids=["release-reviewers"],
                    initially_eligible_user_ids=["reviewer@example.com"],
                ),
                state="changes_requested",
                completed_at=now,
            )
        )
        state.cases["human-correction-case"] = TeamCase(
            id="human-correction-case",
            team_id="team",
            project_id="project",
            title="Human correction case",
            status=TeamCaseStatus.IN_PROGRESS,
            owner_agent_id="lead",
            source="human_release_review",
            source_ref="human-review-correction",
            task_ids=["human-correction-triage"],
        )
        state.tasks["human-correction-triage"] = AgentTask(
            id="human-correction-triage",
            owner_agent_id="lead",
            project_id="project",
            case_id="human-correction-case",
            priority=0,
            state=TeamTaskState.PENDING,
            task_kind="support",
            title="Triage release-wide finding",
            description="Route this finding to an independent correction.",
            created_by_actor="system:human-review",
        )
        state.human_review_findings["human-finding-api"] = HumanReviewFinding(
            id="human-finding-api",
            source_human_review_request_id="human-review-correction",
            text="Release-wide behavior must be corrected.",
            correction_task_id="human-correction-triage",
        )
        state.cases["post-release-case-api"] = TeamCase(
            id="post-release-case-api",
            team_id="team",
            project_id="project",
            title="Post-release feedback",
            status=TeamCaseStatus.IN_PROGRESS,
            owner_agent_id="lead",
            source="post_release_feedback",
            source_ref="release-1:post-release-api",
            metadata={
                "release_package_id": "release-1",
                "evidence_refs": ["artifact:production-report"],
            },
            task_ids=["post-release-triage-api"],
        )
        state.tasks["post-release-triage-api"] = AgentTask(
            id="post-release-triage-api",
            owner_agent_id="lead",
            project_id="project",
            case_id="post-release-case-api",
            priority=0,
            state=TeamTaskState.FINISHED,
            task_kind="support",
            title="Triage post-release feedback",
            description="The correction has been verified.",
            created_by_actor="system:post-release-feedback",
        )
        return state

    repo.update(_seed)
    client = TestClient(
        Starlette(
            routes=team_addon_api.admin_api_routes(
                AddonContext(
                    addon_id="team-addon",
                    runtime_dir=runtime_dir,
                    addon_runtime_dir=runtime_dir / "addons" / "team-addon",
                    auth=_HumanApiAuthStub(login="operator@example.com"),
                )
            )
        )
    )

    created = client.post(
        "/human-review-findings/human-finding-api/route",
        json={
            "owner_agent_id": "backend",
            "expected_revision": repo.read().revision,
            "request_id": "route-correction-api",
        },
    )
    assert created.status_code == 200, created.text
    correction_task_id = created.json()["correction_task"]["id"]
    assert correction_task_id == "human-correction-triage"
    aggregation_task_id = repo.read().cases[
        "human-correction-case"
    ].metadata["human_correction_aggregation_task_id"]
    assert (
        created.json()["correction_task"]["parent_task_id"]
        == aggregation_task_id
    )
    assert created.json()["correction_task"]["assessment"]["task_id"] == (
        correction_task_id
    )

    def _accept_correction(state):
        task = state.tasks[correction_task_id]
        task.state = TeamTaskState.FINISHED
        task.deliverable_ids.append("human-correction-api-deliverable")
        state.deliverables["human-correction-api-deliverable"] = Deliverable(
            id="human-correction-api-deliverable",
            task_id=correction_task_id,
            producer_agent_id="backend",
            summary="Corrected release-wide behavior.",
            state=TeamRecordState.ACCEPTED,
        )
        return state

    repo.update(_accept_correction)
    proposed = client.post(
        "/human-review-findings/human-finding-api/resolution-proposal",
        json={
            "proposed_resolution": "The verified safe-state path is restored.",
            "evidence_refs": ["test:safe-state"],
            "proposed_by_deliverable_ids": [
                "human-correction-api-deliverable"
            ],
            "expected_revision": repo.read().revision,
            "request_id": "propose-resolution-api",
        },
    )
    assert proposed.status_code == 200, proposed.text
    assert proposed.json()["human_review_finding"][
        "proposed_by_deliverable_ids"
    ] == ["human-correction-api-deliverable"]

    before_close_revision = repo.read().revision
    generic_close = client.patch(
        "/cases/post-release-case-api",
        json={
            "status": "closed",
            "expected_revision": before_close_revision,
        },
    )
    assert generic_close.status_code == 400
    assert "evidence-backed" in generic_close.json()["error"]
    assert repo.read().revision == before_close_revision

    closed = client.post(
        "/cases/post-release-case-api/close-post-release",
        json={
            "reason": "The correction was verified in production.",
            "evidence_refs": ["monitoring:recovery"],
            "expected_revision": repo.read().revision,
            "request_id": "close-post-release-case-api",
        },
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["case"]["status"] == "closed"
    assert closed.json()["case"]["metadata"][
        "post_release_feedback_resolution"
    ]["actor_id"] == "operator:operator@example.com"


def test_successor_api_requires_resolved_human_case_and_authorized_corrections(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    repo = _seed_runtime_operations_state(runtime_dir)
    now = datetime(2026, 1, 5, tzinfo=UTC)
    candidate_sha = "a" * 40
    candidate_tree = "b" * 40
    requirement = HumanReviewRequirementSnapshot(
        directory_path="root",
        configured_group_ids=["release-reviewers"],
        initially_eligible_user_ids=["reviewer@example.com"],
    )

    def _seed(state):
        state.projects["project"].git = ProjectGitConfig(
            managed_repo_id="mr_0123456789abcdefabcd",
            base_branch="main",
        )
        state.projects["project"].release_policy = ProjectReleasePolicy(
            allow_ungated=True
        )
        state.tasks["human-source-task"] = AgentTask(
            id="human-source-task",
            owner_agent_id="backend",
            project_id="project",
            priority=1,
            state=TeamTaskState.FINISHED,
            title="Accepted predecessor work",
            description="The immutable predecessor release scope.",
            created_by_agent_id="lead",
            deliverable_ids=["human-source-deliverable"],
        )
        state.deliverables["human-source-deliverable"] = Deliverable(
            id="human-source-deliverable",
            task_id="human-source-task",
            producer_agent_id="backend",
            artifact_refs=["artifact:predecessor"],
            summary="Accepted predecessor work",
            state=TeamRecordState.ACCEPTED,
            verification=DeliverableVerification(status="passed"),
        )
        predecessor = state.release_packages["release-1"]
        predecessor.deliverable_ids = ["human-source-deliverable"]
        predecessor.candidate_commit_sha = candidate_sha
        predecessor.candidate_tree_hash = candidate_tree
        predecessor.state = TeamRecordState.REJECTED
        predecessor.gate_failure = ReleaseGateFailure(
            stage="human_review",
            kind="changes_requested",
            source_id="human-review-successor-api",
            message="Human review requested corrections.",
        )
        predecessor.error = predecessor.gate_failure.message
        predecessor.manifest = ReleaseCandidateManifest(
            project_id="project",
            release_package_id=predecessor.id,
            candidate_commit_sha=candidate_sha,
            candidate_tree_hash=candidate_tree,
            release_branch=predecessor.release_branch,
            deliverables=[{"deliverable_id": "human-source-deliverable"}],
            human_review_requirement=requirement,
        )
        state.human_review_requests["human-review-successor-api"] = (
            HumanReviewRequest(
                id="human-review-successor-api",
                release_package_id=predecessor.id,
                candidate_commit_sha=candidate_sha,
                candidate_tree_hash=candidate_tree,
                requirement_snapshot=requirement,
                state="changes_requested",
                completed_at=now,
            )
        )
        state.cases["human-successor-case-api"] = TeamCase(
            id="human-successor-case-api",
            team_id="team",
            project_id="project",
            title="Human review corrections",
            status=TeamCaseStatus.IN_PROGRESS,
            owner_agent_id="lead",
            source="human_release_review",
            source_ref="human-review-successor-api",
            task_ids=[
                "human-stale-triage-api",
                "human-current-correction-api",
            ],
        )
        state.tasks["human-stale-triage-api"] = AgentTask(
            id="human-stale-triage-api",
            owner_agent_id="lead",
            project_id="project",
            case_id="human-successor-case-api",
            priority=1,
            state=TeamTaskState.PENDING,
            task_kind="support",
            title="Original ambiguous-finding triage",
            description="This task was left behind after retargeting.",
            created_by_actor="system:human-review",
        )
        state.tasks["human-current-correction-api"] = AgentTask(
            id="human-current-correction-api",
            owner_agent_id="backend",
            project_id="project",
            case_id="human-successor-case-api",
            priority=1,
            state=TeamTaskState.FINISHED,
            task_kind="support",
            title="Current human correction",
            description="The retargeted correction root.",
            created_by_agent_id="lead",
            originating_task_id="human-source-task",
            deliverable_ids=["human-correction-deliverable-api"],
        )
        state.deliverables["human-correction-deliverable-api"] = Deliverable(
            id="human-correction-deliverable-api",
            task_id="human-current-correction-api",
            producer_agent_id="backend",
            artifact_refs=["artifact:correction"],
            summary="Accepted human correction",
            state=TeamRecordState.ACCEPTED,
            verification=DeliverableVerification(status="passed"),
        )
        correction = state.deliverables["human-correction-deliverable-api"]
        correction.git_refs.feature_branch = (
            "team/team/features/human-current-correction-api-backend"
        )
        correction.git_refs.feature_commit_sha = "c" * 40
        correction.review_request_ids = ["human-correction-review-api"]
        state.review_requests["human-correction-review-api"] = ReviewRequest(
            id="human-correction-review-api",
            deliverable_id=correction.id,
            reviewer_agent_id="lead",
            state=TeamRecordState.ACCEPTED,
            reviewed_commit_sha="c" * 40,
        )
        state.human_review_findings["human-finding-successor-api"] = (
            HumanReviewFinding(
                id="human-finding-successor-api",
                source_human_review_request_id="human-review-successor-api",
                text="Correct the release-wide behavior.",
                correction_task_id="human-current-correction-api",
                proposed_resolution="The retargeted correction is verified.",
                resolution_evidence_refs=["test:retargeted-correction"],
                proposed_by_deliverable_ids=[
                    "human-correction-deliverable-api"
                ],
            )
        )
        state.tasks["unrelated-accepted-task-api"] = AgentTask(
            id="unrelated-accepted-task-api",
            owner_agent_id="backend",
            project_id="project",
            priority=2,
            state=TeamTaskState.FINISHED,
            title="Unrelated accepted work",
            description="This task is outside the human correction case.",
            created_by_agent_id="lead",
            deliverable_ids=["unrelated-accepted-deliverable-api"],
        )
        state.deliverables["unrelated-accepted-deliverable-api"] = Deliverable(
            id="unrelated-accepted-deliverable-api",
            task_id="unrelated-accepted-task-api",
            producer_agent_id="backend",
            artifact_refs=["artifact:unrelated"],
            summary="Unrelated accepted work",
            state=TeamRecordState.ACCEPTED,
            verification=DeliverableVerification(status="passed"),
        )
        return state

    seeded = repo.update(_seed)
    client = TestClient(
        Starlette(
            routes=team_addon_api.admin_api_routes(
                AddonContext(
                    addon_id="team-addon",
                    runtime_dir=runtime_dir,
                    addon_runtime_dir=runtime_dir / "addons" / "team-addon",
                    auth=_HumanApiAuthStub(login="operator@example.com"),
                )
            )
        )
    )

    unresolved = client.post(
        "/release-packages",
        json={
            "id": "successor-before-triage",
            "project_id": "project",
            "deliverable_ids": ["human-correction-deliverable-api"],
            "supersedes_release_package_id": "release-1",
            "expected_revision": seeded.revision,
        },
    )
    assert unresolved.status_code == 400
    assert "human-stale-triage-api" in unresolved.json()["error"]
    assert repo.read().revision == seeded.revision

    def _complete_triage(state):
        state.tasks["human-stale-triage-api"].state = TeamTaskState.FINISHED
        return state

    completed = repo.update(_complete_triage)
    unrelated = client.post(
        "/release-packages",
        json={
            "id": "successor-with-unrelated-work",
            "project_id": "project",
            "deliverable_ids": [
                "human-correction-deliverable-api",
                "unrelated-accepted-deliverable-api",
            ],
            "supersedes_release_package_id": "release-1",
            "expected_revision": completed.revision,
        },
    )
    assert unrelated.status_code == 400
    assert (
        "unrelated-accepted-deliverable-api is not authorized by human review case"
        in unrelated.json()["error"]
    )
    assert repo.read().revision == completed.revision

    accepted = client.post(
        "/release-packages",
        json={
            "id": "successor-after-complete-triage",
            "project_id": "project",
            "deliverable_ids": ["human-correction-deliverable-api"],
            "supersedes_release_package_id": "release-1",
            "expected_revision": completed.revision,
        },
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["release_packages"][
        "successor-after-complete-triage"
    ]["deliverable_ids"] == [
        "human-source-deliverable",
        "human-correction-deliverable-api",
    ]


def test_operator_api_resolves_terminal_blocker_with_audit_identity(tmp_path: Path) -> None:
    repo = _seed_runtime_operations_state(tmp_path / "runtime")

    def _route_to_operator(state):
        blocker = next(iter(state.blockers.values()))
        blocker.route = TeamBlockerRoute.OPERATOR
        blocker.terminal_reference_ids = ["escalation-api-test"]
        return state

    state = repo.update(_route_to_operator)
    blocker = next(iter(state.blockers.values()))
    client = _client(tmp_path)

    response = client.post(
        f"/api/addons/team-addon/blockers/{blocker.id}/resolve",
        json={
            "note": "Operator confirmed the external dependency recovered.",
            "resolution_refs": ["incident-api-123"],
            "expected_revision": state.revision,
        },
    )

    assert response.status_code == 200
    reread = repo.read()
    assert reread.blockers[blocker.id].state == TeamBlockerState.RESOLVED
    resolution_event = next(
        event
        for event in reread.blocker_events.values()
        if event.blocker_id == blocker.id
        and event.event_type == TeamBlockerEventType.RESOLVED
    )
    assert resolution_event.actor_agent_id is None
    assert resolution_event.metadata["operator_actor_id"].startswith("operator:")
    assert resolution_event.reference_ids == ["incident-api-123"]


def _seed_long_chat_history(
    runtime_dir: Path,
    *,
    message_count: int = 75,
) -> tuple[TeamStateRepository, str]:
    repo = _seed_runtime_operations_state(runtime_dir)
    _state, conversation, _created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        operator_user_id="admin",
        operator_display_name="Admin",
        created_by="operator:admin",
        expected_revision=repo.read().revision,
    )
    conversation_id = conversation.id
    base_time = datetime(2026, 1, 1, tzinfo=UTC)

    def _add_messages(state):
        for index in range(message_count):
            created_at = base_time + timedelta(minutes=index)
            state.conversation_messages[f"msg-{index:03d}"] = TeamConversationMessage(
                id=f"msg-{index:03d}",
                conversation_id=conversation_id,
                sender="operator" if index % 2 == 0 else "agent",
                body=f"Message {index:03d}",
                status="sent",
                created_at=created_at,
                updated_at=created_at,
            )
        state.conversations[
            conversation_id
        ].last_message_preview = f"Message {message_count - 1:03d}"
        state.conversations[conversation_id].last_interaction_at = base_time + timedelta(
            minutes=message_count - 1
        )
        return state

    repo.update(_add_messages)
    return repo, conversation_id


class _FakeAvatarGenerator:
    model = "gpt-image-2"

    async def generate(self, *, prompt: str) -> bytes:
        assert "Backend" in prompt
        return _png_avatar_bytes((140, 60, 170))


class _FailingAvatarGenerator:
    model = "gpt-image-2"

    async def generate(self, *, prompt: str) -> bytes:
        raise RuntimeError("OpenAI image generation request failed: timeout")


def test_agent_avatar_upload_normalizes_and_serves_image(tmp_path: Path) -> None:
    repo = _seed_runtime_operations_state(tmp_path / "runtime")
    client = _client(tmp_path)

    response = client.post(
        "/api/addons/team-addon/agents/backend/avatar/upload",
        data={"expected_revision": str(repo.read().revision)},
        files={"file": ("avatar.png", _png_avatar_bytes(), "image/png")},
    )

    assert response.status_code == 200
    payload = response.json()
    avatar = payload["agents"]["backend"]["avatar"]
    assert avatar["source"] == "uploaded"
    assert avatar["mime_type"] == "image/webp"
    assert avatar["asset_id"].startswith("av_")
    assert len(avatar["sha256"]) == 64

    image_response = client.get(f"/api/addons/team-addon/avatars/{avatar['asset_id']}")
    assert image_response.status_code == 200
    assert image_response.headers["content-type"].startswith("image/webp")
    assert image_response.content.startswith(b"RIFF")


def test_agent_avatar_upload_rejects_non_image(tmp_path: Path) -> None:
    repo = _seed_runtime_operations_state(tmp_path / "runtime")
    client = _client(tmp_path)

    response = client.post(
        "/api/addons/team-addon/agents/backend/avatar/upload",
        data={"expected_revision": str(repo.read().revision)},
        files={"file": ("avatar.txt", b"not an image", "text/plain")},
    )

    assert response.status_code == 400
    assert "valid PNG, JPEG, or WebP" in response.json()["error"]


def test_agent_avatar_upload_rejects_corrupt_recognized_image(tmp_path: Path) -> None:
    repo = _seed_runtime_operations_state(tmp_path / "runtime")
    client = _client(tmp_path)
    corrupt_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24

    response = client.post(
        "/api/addons/team-addon/agents/backend/avatar/upload",
        data={"expected_revision": str(repo.read().revision)},
        files={"file": ("avatar.png", corrupt_png, "image/png")},
    )

    assert response.status_code == 400
    assert "valid PNG, JPEG, or WebP" in response.json()["error"]


def test_generated_agent_avatar_preview_wraps_transport_failures(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _seed_runtime_operations_state(tmp_path / "runtime")
    monkeypatch.setattr(
        "evander_team_addon.api.openai_avatar_generator_from_config",
        lambda _config: _FailingAvatarGenerator(),
    )
    client = _client(tmp_path)

    response = client.post(
        "/api/addons/team-addon/agents/backend/avatar/previews",
        json={},
    )

    assert response.status_code == 503
    assert "OpenAI image generation request failed" in response.json()["error"]


def test_generated_agent_avatar_preview_is_accepted_with_revision(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = _seed_runtime_operations_state(tmp_path / "runtime")
    monkeypatch.setattr(
        "evander_team_addon.api.openai_avatar_generator_from_config",
        lambda _config: _FakeAvatarGenerator(),
    )
    client = _client(tmp_path)

    preview_response = client.post(
        "/api/addons/team-addon/agents/backend/avatar/previews",
        json={"style_preset": "minimal", "prompt_note": "teal accent"},
    )

    assert preview_response.status_code == 201
    preview = preview_response.json()["preview"]
    assert preview["preview_id"].startswith("av_")
    assert preview["style_preset"] == "minimal"
    assert repo.read().agents["backend"].avatar is None
    assert client.get(preview["url"]).status_code == 200

    accept_response = client.post(
        "/api/addons/team-addon/agents/backend/avatar/accept-preview",
        json={
            "expected_revision": repo.read().revision,
            "preview_id": preview["preview_id"],
        },
    )

    assert accept_response.status_code == 200
    avatar = accept_response.json()["agents"]["backend"]["avatar"]
    assert avatar["source"] == "generated"
    assert avatar["asset_id"] == preview["preview_id"]
    assert avatar["style_preset"] == "minimal"
    assert client.get(f"/api/addons/team-addon/avatars/{avatar['asset_id']}").status_code == 200


def test_agent_avatar_accept_preview_requires_current_revision(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = _seed_runtime_operations_state(tmp_path / "runtime")
    monkeypatch.setattr(
        "evander_team_addon.api.openai_avatar_generator_from_config",
        lambda _config: _FakeAvatarGenerator(),
    )
    client = _client(tmp_path)
    preview = client.post(
        "/api/addons/team-addon/agents/backend/avatar/previews",
        json={},
    ).json()["preview"]

    response = client.post(
        "/api/addons/team-addon/agents/backend/avatar/accept-preview",
        json={"expected_revision": repo.read().revision - 1, "preview_id": preview["preview_id"]},
    )

    assert response.status_code == 409
    assert repo.read().agents["backend"].avatar is None
    assert client.get(preview["url"]).status_code == 200


def test_openai_avatar_generator_wraps_http_transport_errors() -> None:
    from evander_team_addon.avatars import OpenAIAvatarGenerator

    async def _handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns failed")

    generator = OpenAIAvatarGenerator(
        api_key="token",
        api_key_source="OPENAI_API_KEY",
        transport=httpx.MockTransport(_handler),
    )

    try:
        asyncio.run(generator.generate(prompt="avatar"))
    except RuntimeError as exc:
        assert "OpenAI image generation request failed" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_agent_avatar_delete_clears_state_and_asset(tmp_path: Path) -> None:
    repo = _seed_runtime_operations_state(tmp_path / "runtime")
    client = _client(tmp_path)
    upload = client.post(
        "/api/addons/team-addon/agents/backend/avatar/upload",
        data={"expected_revision": str(repo.read().revision)},
        files={"file": ("avatar.png", _png_avatar_bytes(), "image/png")},
    ).json()
    asset_id = upload["agents"]["backend"]["avatar"]["asset_id"]

    response = client.request(
        "DELETE",
        "/api/addons/team-addon/agents/backend/avatar",
        json={"expected_revision": upload["revision"]},
    )

    assert response.status_code == 200
    assert response.json()["agents"]["backend"]["avatar"] is None
    assert client.get(f"/api/addons/team-addon/avatars/{asset_id}").status_code == 404


def test_team_addon_admin_api_conversation_queues_without_inline_dispatch_and_reply(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = _write_config(tmp_path, enable_team_addon=True)
    repo = _seed_runtime_operations_state(tmp_path / "runtime")

    def _clear_backend_runtime(state):
        state.agent_runtimes.pop("backend", None)
        return state

    repo.update(_clear_backend_runtime)
    service = build_admin_runtime(config_path)
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-backend",
            vm_id="vm-backend",
            directory_path="root",
            model="gpt-5",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    resume_calls: list[dict[str, object]] = []
    copy_calls: list[dict[str, object]] = []

    def _fake_resume_agent(target, prompt, **kwargs):
        resume_calls.append(
            {
                "target": target,
                "prompt": prompt,
                **kwargs,
            }
        )
        return {
            "success": True,
            "action": "resume_issue_agent",
            "vm_name": "vm-backend",
            "message": "started",
            "dispatch_id": kwargs.get("dispatch_id"),
        }

    def _fake_vm_copy_files(**kwargs):
        copy_calls.append(kwargs)
        return {
            "success": True,
            "vm_name": kwargs.get("target") or "vm-backend",
            "copied_count": len(kwargs.get("files") or []),
            "copied_paths": [
                str(file.get("destination_path")) for file in kwargs.get("files") or []
            ],
        }

    monkeypatch.setattr(service.vm_agent_admin, "resume_agent", _fake_resume_agent)
    monkeypatch.setattr(service.vm_agent_admin, "vm_copy_files", _fake_vm_copy_files)
    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)

    created = client.post(
        "/api/addons/team-addon/conversations",
        json={"agent_id": "backend", "expected_revision": repo.read().revision},
    )
    assert created.status_code == 201
    conversation = created.json()["conversations"][0]
    assert conversation["agent_id"] == "backend"
    assert conversation["operator_user_id"] == "admin"
    assert conversation["read_only"] is False
    assert created.json()["current_operator"]["user_id"] == "admin"
    assert created.json()["selected_operator"]["user_id"] == "admin"
    assert {agent["id"] for agent in created.json()["eligible_agents"]} >= {
        "backend",
        "lead",
        "manager",
    }

    sent = client.post(
        f"/api/addons/team-addon/conversations/{conversation['id']}/messages",
        json={
            "body": "Can you summarize the blocker?",
            "expected_revision": created.json()["revision"],
        },
    )

    assert sent.status_code == 202
    sent_payload = sent.json()
    assert sent_payload["dispatch"]["success"] is False
    assert sent_payload["dispatch"]["queued"] is True
    assert sent_payload["dispatch"]["dispatch_id"].startswith("team-chat:")
    assert "queued for asynchronous agent dispatch" in sent_payload["dispatch"]["message"]
    assert len(sent_payload["published_event_ids"]) == 1
    assert copy_calls == []
    message = sent_payload["message"]
    assert message["sender"] == "operator"
    assert message["status"] == "queued"
    assert message["dispatch_id"] == sent_payload["dispatch"]["dispatch_id"]
    assert message["metadata"]["sender_kind"] == "human_operator"
    assert message["metadata"]["operator_user_id"] == "admin"
    assert resume_calls == []
    events = InternalEventBus(internal_event_store_path(tmp_path / "runtime")).list_events()
    assert len(events) == 1
    assert events[0].event_type == CHAT_MESSAGE_QUEUED_EVENT_TYPE
    assert events[0].correlation_key == message["dispatch_id"]
    assert events[0].idempotency_key == f"team-chat-message-queued:{message['id']}"
    assert events[0].payload == {
        "team_id": "team",
        "agent_id": "backend",
        "session_id": "session-backend",
        "conversation_id": conversation["id"],
        "message_id": message["id"],
        "dispatch_id": message["dispatch_id"],
    }

    state, result = _team_agent_action_service(repo).apply_agent_action(
        action={
            "agent_id": "backend",
            "dispatch_id": message["dispatch_id"],
            "chat_replies": [
                {
                    "conversation_id": conversation["id"],
                    "message_id": message["id"],
                    "body": "The dependency task is still open.",
                }
            ],
        },
        dispatch_id=message["dispatch_id"],
        expected_revision=repo.read().revision,
    )
    assert result["success"] is True
    assert state.agent_runtimes["backend"].active_dispatch_id is None

    detail = client.get(
        f"/api/addons/team-addon/conversations/{conversation['id']}",
    )
    assert detail.status_code == 200
    messages = detail.json()["conversation"]["messages"]
    assert [item["sender"] for item in messages] == ["operator", "agent"]
    assert messages[0]["status"] == "replied"
    assert messages[1]["body"] == "The dependency task is still open."


def test_team_addon_admin_api_allows_locked_chat_as_reply_only(tmp_path: Path) -> None:
    repo = _seed_runtime_operations_state(tmp_path / "runtime")
    client = _client(tmp_path, unlock_privileged=False)

    created = client.post(
        "/api/addons/team-addon/conversations",
        json={"agent_id": "backend", "expected_revision": repo.read().revision},
    )

    assert created.status_code == 201
    conversation = created.json()["conversations"][0]

    sent = client.post(
        f"/api/addons/team-addon/conversations/{conversation['id']}/messages",
        json={
            "body": "Can you summarize the blocker?",
            "expected_revision": created.json()["revision"],
        },
    )

    assert sent.status_code == 202
    message = sent.json()["message"]
    assert message["status"] == "queued"
    assert message["metadata"]["allow_team_mutations"] is False
    reread = TeamStateRepository(tmp_path / "runtime").read()
    stored_message = reread.conversation_messages[message["id"]]
    assert stored_message.metadata["allow_team_mutations"] is False


def test_team_chat_mutation_scope_requires_operator_rbac_and_snapshots_projects(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    repo = _seed_runtime_operations_state(runtime_dir)
    _team_admin_service(repo).create_project(
        project_id="other-project",
        team_id="team",
        name="Other Project",
        lead_agent_id="lead",
        lead_session_id=None,
    )

    def _bind_project_directory(state):
        state.projects["project"].git = ProjectGitConfig(
            managed_repo_id="mr_0123456789abcdefabcd",
            directory_path="root/team-a",
        )
        state.projects["other-project"].git = ProjectGitConfig(
            managed_repo_id="mr_11111111111111111111",
            directory_path="root/team-b",
        )
        return state

    repo.update(_bind_project_directory)
    session_repo = JsonSessionManifestRepository(runtime_dir / "sessions.json")
    session_repo.upsert_session(
        SessionManifest(
            session_id="session-lead",
            vm_id="vm-lead",
            directory_path="root/team-a",
            model="gpt-5",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    service = SimpleNamespace(runtime=SimpleNamespace(session_repository=session_repo))

    viewer_auth = _HumanApiAuthStub(login="viewer@example.com", privileged=True)
    viewer_client = TestClient(
        Starlette(
            routes=team_addon_api.admin_api_routes(
                AddonContext(
                    addon_id="team-addon",
                    runtime_dir=runtime_dir,
                    addon_runtime_dir=runtime_dir / "addons" / "team-addon",
                    auth=viewer_auth,
                    service=service,
                )
            )
        )
    )
    viewer_conversation = viewer_client.post(
        "/conversations",
        json={"agent_id": "lead"},
    ).json()["conversations"][0]
    viewer_send = viewer_client.post(
        f"/conversations/{viewer_conversation['id']}/messages",
        json={"body": "Cancel the worker task."},
    )

    assert viewer_send.status_code == 202, viewer_send.text
    viewer_message = viewer_send.json()["message"]
    viewer_authorization = viewer_message["metadata"]["team_mutation_authorization"]
    assert viewer_authorization["mode"] == "reply_only"
    assert viewer_authorization["reason"] == "permission_denied"
    state, _ = TeamChatService(repo).begin_chat_message_dispatch(
        message_id=viewer_message["id"],
        dispatch_id=viewer_message["dispatch_id"],
    )
    viewer_dispatch = state.dispatches[viewer_message["dispatch_id"]]
    assert viewer_dispatch.project_ids == []
    assert viewer_dispatch.managed_repo_ids == []
    state, denied_cancellation = TeamTaskUpdateCommandService(repo).cancel_task_command(
        session_id="session-lead",
        task_id="dependency-task",
        reason_code="superseded",
        reason="Attempted through an unlocked Viewer-owned Team chat.",
        request_id="viewer-team-chat-cancellation",
    )
    assert denied_cancellation["success"] is False
    assert denied_cancellation["errors"][0]["code"] == "dispatch_scope_forbidden"
    assert state.tasks["dependency-task"].state == TeamTaskState.PENDING

    operator_auth = _HumanApiAuthStub(
        login="operator@example.com",
        privileged=True,
        team_chat_mutation_directories={"root/team-a"},
    )
    operator_client = TestClient(
        Starlette(
            routes=team_addon_api.admin_api_routes(
                AddonContext(
                    addon_id="team-addon",
                    runtime_dir=runtime_dir,
                    addon_runtime_dir=runtime_dir / "addons" / "team-addon",
                    auth=operator_auth,
                    service=service,
                )
            )
        )
    )
    operator_conversation = operator_client.post(
        "/conversations",
        json={"agent_id": "lead"},
    ).json()["conversations"][0]
    operator_send = operator_client.post(
        f"/conversations/{operator_conversation['id']}/messages",
        json={"body": "Please handle the project task."},
    )

    assert operator_send.status_code == 202, operator_send.text
    operator_authorization = operator_send.json()["message"]["metadata"][
        "team_mutation_authorization"
    ]
    assert operator_authorization["mode"] == "project_scoped"
    assert operator_authorization["project_ids"] == ["project"]
    assert operator_authorization["managed_repo_ids"] == [
        "mr_0123456789abcdefabcd"
    ]


def test_team_addon_chat_ignores_stale_global_state_revision(tmp_path: Path) -> None:
    repo = _seed_runtime_operations_state(tmp_path / "runtime")
    client = _client(tmp_path)

    stale_open_revision = repo.read().revision
    repo.update(lambda state: state)
    created = client.post(
        "/api/addons/team-addon/conversations",
        json={"agent_id": "backend", "expected_revision": stale_open_revision},
    )

    assert created.status_code == 201
    conversation = created.json()["conversations"][0]
    stale_send_revision = created.json()["revision"]
    repo.update(lambda state: state)
    sent = client.post(
        f"/api/addons/team-addon/conversations/{conversation['id']}/messages",
        json={
            "body": "Please continue with the current task.",
            "expected_revision": stale_send_revision,
        },
    )

    assert sent.status_code == 202
    assert sent.json()["message"]["body"] == "Please continue with the current task."


def test_team_addon_chat_rejects_stale_sends_after_wake_blockers(tmp_path: Path) -> None:
    for blocker, expected_error in (
        ("paused", "agent backend is not active"),
        ("stood_down", "agent backend is stood down"),
    ):
        repo = _seed_runtime_operations_state(tmp_path / blocker / "runtime")
        client = _client(tmp_path / blocker)
        created = client.post(
            "/api/addons/team-addon/conversations",
            json={"agent_id": "backend", "expected_revision": repo.read().revision},
        )
        assert created.status_code == 201
        conversation = created.json()["conversations"][0]
        stale_send_revision = created.json()["revision"]

        if blocker == "paused":

            def _pause_backend(state):
                state.agents["backend"].status = AgentStatus.PAUSED
                return state

            repo.update(_pause_backend)
        else:
            _team_runtime_service(repo).stand_down_agent(
                agent_id="backend",
                reason="Operator hold.",
                set_by="operator:test",
                expected_revision=repo.read().revision,
            )

        sent = client.post(
            f"/api/addons/team-addon/conversations/{conversation['id']}/messages",
            json={
                "body": "Please continue with the current task.",
                "expected_revision": stale_send_revision,
            },
        )

        assert sent.status_code == 400
        assert expected_error in sent.json()["error"]
        assert repo.read().conversation_messages == {}


def test_team_addon_chat_uploads_renders_and_downloads_attachments(tmp_path: Path) -> None:
    repo = _seed_runtime_operations_state(tmp_path / "runtime")
    client = _client(tmp_path)
    created = client.post(
        "/api/addons/team-addon/conversations",
        json={"agent_id": "backend", "expected_revision": repo.read().revision},
    )
    assert created.status_code == 201
    conversation_id = created.json()["conversations"][0]["id"]

    sent = client.post(
        f"/api/addons/team-addon/conversations/{conversation_id}/messages",
        data={"body": "Please inspect these."},
        files=[
            ("files", ("notes.txt", b"hello from chat", "text/plain")),
            ("files", ("pixel.png", _png_avatar_bytes(), "image/png")),
        ],
    )

    assert sent.status_code == 202, sent.text
    message = sent.json()["message"]
    assert message["body"] == "Please inspect these."
    assert [item["filename"] for item in message["attachments"]] == [
        "notes.txt",
        "pixel.png",
    ]
    text_attachment = message["attachments"][0]
    download = client.get(text_attachment["download_url"])
    assert download.status_code == 200
    assert download.content == b"hello from chat"
    assert "attachment" in download.headers["content-disposition"]

    reread = repo.read()
    stored_message = reread.conversation_messages[message["id"]]
    assert stored_message.attachment_ids == [item["id"] for item in message["attachments"]]
    assert all(item_id in reread.chat_attachments for item_id in stored_message.attachment_ids)


def test_team_addon_chat_attachment_policy_is_addon_owned_and_enforced(
    tmp_path: Path,
) -> None:
    repo = _seed_runtime_operations_state(tmp_path / "runtime")
    client = _client(tmp_path)
    policy = client.get(
        "/api/addons/team-addon/chat/attachment-policy?directory_path=root/engineering"
    )
    assert policy.status_code == 200
    assert policy.json()["effective"]["operator_to_agent_enabled"] is True
    assert policy.json()["effective"]["agent_send_requires_approval"] is True

    saved = client.put(
        "/api/addons/team-addon/chat/attachment-policy",
        json={
            "directory_path": "root",
            "expected_revision": repo.read().revision,
            "policy": {
                "operator_to_agent_enabled": False,
                "agent_to_operator_enabled": True,
                "agent_send_requires_approval": True,
                "max_attachment_bytes": 1024,
                "max_attachments_per_message": 2,
                "allowed_mime_types": ["text/plain"],
                "allowed_extensions": [".txt"],
                "blocked_extensions": [".exe"],
            },
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["effective"]["operator_to_agent_enabled"] is False

    created = client.post(
        "/api/addons/team-addon/conversations",
        json={"agent_id": "backend"},
    )
    assert created.status_code == 201
    conversation_id = created.json()["conversations"][0]["id"]
    denied = client.post(
        f"/api/addons/team-addon/conversations/{conversation_id}/messages",
        data={"body": "Attachment only."},
        files=[("files", ("notes.txt", b"hello", "text/plain"))],
    )
    assert denied.status_code == 403
    assert "disabled" in denied.json()["error"]


def test_team_addon_admin_api_queues_chat_when_agent_has_active_dispatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = _write_config(tmp_path, enable_team_addon=True)
    repo = _seed_runtime_operations_state(tmp_path / "runtime")
    service = build_admin_runtime(config_path)
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-backend",
            vm_id="vm-backend",
            directory_path="root",
            model="gpt-5",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    resume_calls: list[dict[str, object]] = []

    def _fake_resume_agent(target, prompt, **kwargs):
        resume_calls.append({"target": target, "prompt": prompt, **kwargs})
        return {"success": True, "dispatch_id": kwargs.get("dispatch_id")}

    monkeypatch.setattr(service.vm_agent_admin, "resume_agent", _fake_resume_agent)
    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)

    created = client.post(
        "/api/addons/team-addon/conversations",
        json={"agent_id": "backend", "expected_revision": repo.read().revision},
    )
    assert created.status_code == 201
    conversation = created.json()["conversations"][0]

    sent = client.post(
        f"/api/addons/team-addon/conversations/{conversation['id']}/messages",
        json={
            "body": "Can you look at this once your current wake finishes?",
            "expected_revision": created.json()["revision"],
        },
    )

    assert sent.status_code == 202
    sent_payload = sent.json()
    assert sent_payload["dispatch"]["success"] is False
    assert sent_payload["dispatch"]["queued"] is True
    assert sent_payload["dispatch"]["dispatch_id"].startswith("team-chat:")
    assert "queued for asynchronous agent dispatch" in sent_payload["dispatch"]["message"]
    assert len(sent_payload["published_event_ids"]) == 1
    assert resume_calls == []
    message = sent_payload["message"]
    assert message["status"] == "queued"
    assert message["dispatch_id"] == sent_payload["dispatch"]["dispatch_id"]
    assert message["source_message_ids"] == []
    assert message["seen_at"] is None
    assert message["seen_dispatch_id"] is None
    reread = TeamStateRepository(tmp_path / "runtime").read()
    assert reread.conversation_messages[message["id"]].status == "queued"
    assert reread.agent_runtimes["backend"].active_dispatch_id == "dispatch-backend"


def test_team_addon_conversation_messages_are_paginated(tmp_path: Path) -> None:
    _repo, conversation_id = _seed_long_chat_history(tmp_path / "runtime")
    client = _client(tmp_path)

    detail = client.get(f"/api/addons/team-addon/conversations/{conversation_id}")
    assert detail.status_code == 200
    detail_conversation = detail.json()["conversation"]
    assert detail_conversation["message_count"] == 75
    assert detail_conversation["has_older"] is True
    assert detail_conversation["oldest_message_id"] == "msg-025"
    assert detail_conversation["newest_message_id"] == "msg-074"
    assert [message["body"] for message in detail_conversation["messages"][:2]] == [
        "Message 025",
        "Message 026",
    ]
    assert detail_conversation["messages"][-1]["body"] == "Message 074"
    assert len(detail_conversation["messages"]) == 50

    latest_page = client.get(
        f"/api/addons/team-addon/conversations/{conversation_id}/messages?limit=10"
    )
    assert latest_page.status_code == 200
    latest_payload = latest_page.json()
    assert latest_payload["message_count"] == 75
    assert latest_payload["has_older"] is True
    assert latest_payload["oldest_message_id"] == "msg-065"
    assert latest_payload["newest_message_id"] == "msg-074"
    assert [message["id"] for message in latest_payload["messages"]] == [
        f"msg-{index:03d}" for index in range(65, 75)
    ]

    older_page = client.get(
        f"/api/addons/team-addon/conversations/{conversation_id}/messages?before=msg-065&limit=10"
    )
    assert older_page.status_code == 200
    older_payload = older_page.json()
    assert older_payload["has_older"] is True
    assert older_payload["oldest_message_id"] == "msg-055"
    assert older_payload["newest_message_id"] == "msg-064"
    assert [message["id"] for message in older_payload["messages"]] == [
        f"msg-{index:03d}" for index in range(55, 65)
    ]

    first_page = client.get(
        f"/api/addons/team-addon/conversations/{conversation_id}/messages?before=msg-005&limit=10"
    )
    assert first_page.status_code == 200
    first_payload = first_page.json()
    assert first_payload["has_older"] is False
    assert [message["id"] for message in first_payload["messages"]] == [
        f"msg-{index:03d}" for index in range(0, 5)
    ]


def test_team_addon_conversation_list_can_inline_selected_message_page(
    tmp_path: Path,
) -> None:
    _repo, conversation_id = _seed_long_chat_history(tmp_path / "runtime")
    client = _client(tmp_path)

    default_response = client.get("/api/addons/team-addon/conversations")
    assert default_response.status_code == 200
    default_conversation = default_response.json()["conversations"][0]
    assert default_conversation["message_count"] == 75
    assert "messages" not in default_conversation

    selected_response = client.get(
        "/api/addons/team-addon/conversations"
        f"?include_selected_messages=1&selected_conversation_id={conversation_id}"
    )
    assert selected_response.status_code == 200
    payload = selected_response.json()
    assert payload["selected_conversation_id"] == conversation_id
    conversation = payload["conversations"][0]
    assert conversation["id"] == conversation_id
    assert conversation["message_count"] == 75
    assert conversation["messages_limit"] == 50
    assert conversation["has_older"] is True
    assert conversation["oldest_message_id"] == "msg-025"
    assert conversation["newest_message_id"] == "msg-074"
    assert [message["id"] for message in conversation["messages"]] == [
        f"msg-{index:03d}" for index in range(25, 75)
    ]


def test_team_addon_bootstrap_and_chat_view_are_scoped(tmp_path: Path) -> None:
    _repo, conversation_id = _seed_long_chat_history(tmp_path / "runtime")
    client = _client(tmp_path)

    bootstrap = client.get("/api/addons/team-addon/bootstrap")
    assert bootstrap.status_code == 200
    bootstrap_payload = bootstrap.json()
    assert bootstrap_payload["revision"] > 0
    assert "chat" in bootstrap_payload["views"]
    assert "overview" not in bootstrap_payload["views"]
    assert bootstrap_payload["counts"]["conversations"] == 1
    assert "tasks" not in bootstrap_payload
    assert "conversation_messages" not in bootstrap_payload

    chat_view = client.get(
        f"/api/addons/team-addon/views/chat?selected_conversation_id={conversation_id}"
    )
    assert chat_view.status_code == 200
    payload = chat_view.json()
    assert payload["view"] == "chat"
    assert set(payload["data"]) == {"chat", "state"}
    assert payload["data"]["chat"]["selected_conversation_id"] == conversation_id
    conversation = payload["data"]["chat"]["conversations"][0]
    assert conversation["messages_limit"] == 50
    assert len(conversation["messages"]) == 50
    assert "tasks" not in payload["data"]["state"]
    assert "conversation_messages" not in payload["data"]["state"]
    assert set(payload["resource_tokens"]) == {"agents"}

    missing_view = client.get("/api/addons/team-addon/views/overview")
    assert missing_view.status_code == 404
    assert missing_view.json() == {"error": "view not found"}


def test_team_addon_planning_and_operations_views_include_renderer_dependencies(
    tmp_path: Path,
) -> None:
    repo = _seed_runtime_operations_state(tmp_path / "runtime")
    _team_admin_service(repo).create_review_profile(
        profile_id="planning-readiness",
        team_id="team",
        project_id="project",
        name="Planning readiness",
    )
    client = _client(tmp_path)

    planning = client.get("/api/addons/team-addon/views/planning")

    assert planning.status_code == 200
    planning_payload = planning.json()
    planning_state = planning_payload["data"]["state"]
    assert set(planning_state["blockers"]) == set(repo.read().blockers)
    assert set(planning_state["blocker_events"]) == set(repo.read().blocker_events)
    assert planning_state["review_profiles"]["planning-readiness"]["name"] == (
        "Planning readiness"
    )
    assert set(planning_payload["resource_tokens"]["review_profiles"]) == {
        "planning-readiness"
    }
    assert all(
        event["blocker_id"] in planning_state["blockers"]
        for event in planning_state["blocker_events"].values()
    )

    operations = client.get("/api/addons/team-addon/views/operations")

    assert operations.status_code == 200
    operations_state = operations.json()["data"]["state"]
    assert set(operations_state["deliverables"]) == {"deliverable"}
    assert set(operations_state["release_packages"]) == {"release-1"}
    assert set(operations_state["ephemeral_assignments"]) == {
        "ephemeral-cleanup",
        "ephemeral-failed",
    }
    attention = operations_state["runtime_operations"]["attention"]
    blocked_release = next(
        row for row in attention if row["kind"] == "blocked_release_package"
    )
    assert blocked_release["release_package_id"] in operations_state["release_packages"]
    ephemeral_rows = [
        row
        for row in attention
        if row["kind"]
        in {"failed_ephemeral_assignment", "cleanup_pending_ephemeral_assignment"}
    ]
    assert {
        row["assignment_id"] for row in ephemeral_rows
    } == set(operations_state["ephemeral_assignments"])


def test_team_addon_delivery_view_serializes_derived_datetime_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(tmp_path)
    observed_at = datetime(2026, 8, 7, 7, 30, tzinfo=UTC)

    def _delivery_payload(*_args, **_kwargs) -> dict[str, object]:
        return {
            "view": "delivery",
            "generated_at": observed_at,
            "data": {"state": {"runtime_operations": {"attention": [{"at": observed_at}]}}},
        }

    monkeypatch.setattr(team_addon_api, "_view_payload_for_request", _delivery_payload)

    response = client.get("/api/addons/team-addon/views/delivery")

    assert response.status_code == 200
    payload = response.json()
    assert payload["generated_at"] == "2026-08-07T07:30:00Z"
    assert payload["data"]["state"]["runtime_operations"]["attention"] == [
        {"at": "2026-08-07T07:30:00Z"}
    ]


def test_team_api_json_response_serializes_derived_datetime_values() -> None:
    observed_at = datetime(2026, 8, 7, 7, 40, tzinfo=UTC)

    response = team_addon_api.JSONResponse(
        {"result": {"nested": [{"observed_at": observed_at}]}}
    )

    assert json.loads(response.body) == {
        "result": {"nested": [{"observed_at": "2026-08-07T07:40:00Z"}]}
    }


def test_team_addon_release_detail_serializes_derived_datetime_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_runtime_operations_state(tmp_path / "runtime")
    client = _client(tmp_path)
    sealed_at = datetime(2026, 8, 7, 7, 45, tzinfo=UTC)

    def _release_payload(*_args, **_kwargs) -> dict[str, object]:
        return {
            "id": "release-1",
            "release_review_cohort": {
                "sealed_at": sealed_at,
                "slot_events": [{"occurred_at": sealed_at}],
            },
        }

    monkeypatch.setattr(team_addon_api, "release_package_api_payload", _release_payload)

    response = client.get("/api/addons/team-addon/release-packages/release-1")

    assert response.status_code == 200
    assert response.json()["release_review_cohort"] == {
        "sealed_at": "2026-08-07T07:45:00Z",
        "slot_events": [{"occurred_at": "2026-08-07T07:45:00Z"}],
    }


def test_team_addon_views_filter_non_actionable_history(tmp_path: Path) -> None:
    repo = _seed_runtime_operations_state(tmp_path / "runtime")
    now = datetime(2026, 1, 2, tzinfo=UTC)

    def _mutate(state):
        state.release_packages["release-1"].state = TeamRecordState.REJECTED
        state.inbox["handled-inbox"] = AgentInboxItem(
            id="handled-inbox",
            recipient_agent_id="backend",
            sender_agent_id="lead",
            item_type=TeamInboxItemType.STATUS_REQUEST,
            state=TeamInboxItemState.HANDLED,
            payload={"question": "Already handled"},
            created_at=now,
            updated_at=now,
        )
        state.action_records["dismissed-action"] = AgentActionRecord(
            id="dismissed-action",
            agent_id="backend",
            dispatch_id="dispatch-backend",
            action={"type": "dismissed"},
            success=False,
            error="dismissed failure",
            created_at=state.action_records["action-1"].created_at - timedelta(seconds=2),
            dismissed_at=now,
            dismissed_by="operator",
        )
        state.action_records["older-failed-action"] = AgentActionRecord(
            id="older-failed-action",
            agent_id="backend",
            dispatch_id="dispatch-backend-older",
            action={"type": "old-build"},
            success=False,
            error="older failed action",
            created_at=state.action_records["action-1"].created_at - timedelta(seconds=1),
        )
        state.action_records["successful-action"] = AgentActionRecord(
            id="successful-action",
            agent_id="lead",
            dispatch_id="dispatch-lead",
            action={"type": "ok"},
            success=True,
            created_at=now,
        )

    repo.update(_mutate)
    client = _client(tmp_path)

    people = client.get("/api/addons/team-addon/views/people")
    assert people.status_code == 200
    people_state = people.json()["data"]["state"]
    assert {"inbox-1", "escalation-1"}.issubset(people_state["inbox"])
    assert any(
        item["payload"].get("kind") == "blocker_reported"
        for item in people_state["inbox"].values()
    )
    assert "action_records" not in people_state

    operations = client.get("/api/addons/team-addon/views/operations")
    assert operations.status_code == 200
    operations_state = operations.json()["data"]["state"]
    assert {"inbox-1", "escalation-1"}.issubset(operations_state["inbox"])
    assert set(operations_state["release_packages"]) == {"release-1"}
    assert set(operations_state["action_records"]) == {"action-1"}
    assert not any(
        row["kind"] in {"blocked_release_package", "failed_git_promotion"}
        for row in operations_state["runtime_operations"]["attention"]
    )
    assert set(operations_state["resource_tokens"]["inbox"]) == set(
        operations_state["inbox"]
    )
    assert set(operations_state["resource_tokens"]["action_records"]) == {"action-1"}


def test_team_addon_inbox_history_is_searchable_filterable_and_paginated(
    tmp_path: Path,
) -> None:
    repo = _seed_runtime_operations_state(tmp_path / "runtime")
    created_at = datetime(2026, 1, 2, tzinfo=UTC)
    handled_at = created_at + timedelta(hours=2)

    def _mutate(state):
        state.inbox["handled-progress"] = AgentInboxItem(
            id="handled-progress",
            recipient_agent_id="backend",
            sender_agent_id="lead",
            task_id="waiting-task",
            item_type=TeamInboxItemType.PROGRESS_REPORT,
            state=TeamInboxItemState.HANDLED,
            response_requested=False,
            payload={
                "summary": "Quiet deployment update",
                "message": "The deployment is ready for the private follow-up.",
            },
            created_at=created_at,
            updated_at=handled_at,
        )

    repo.update(_mutate)
    client = _client(tmp_path)

    mail_view = client.get("/api/addons/team-addon/views/mail")
    assert mail_view.status_code == 200
    assert set(mail_view.json()["data"]["state"]) >= {"teams", "roles", "agents"}
    assert "inbox" not in mail_view.json()["data"]["state"]

    page = client.get("/api/addons/team-addon/inbox?agent_id=backend&limit=1")
    assert page.status_code == 200
    payload = page.json()
    assert payload["total_count"] == 4
    assert payload["state_counts"] == {"pending": 3, "handled": 1}
    assert payload["has_more"] is True
    assert payload["items"][0]["payload"]["kind"] == "blocker_reported"

    searched = client.get(
        "/api/addons/team-addon/inbox",
        params={"agent_id": "backend", "state": "handled", "q": "private follow-up"},
    )
    assert searched.status_code == 200
    assert searched.json()["total_count"] == 1
    item = searched.json()["items"][0]
    assert item["id"] == "handled-progress"
    assert item["direction"] == "received"
    assert item["sender"]["id"] == "lead"
    assert item["recipient"]["id"] == "backend"
    assert item["task"] == {
        "id": "waiting-task",
        "title": "Waiting task",
        "project_id": "project",
        "state": "pending",
    }
    assert item["payload"]["message"] == "The deployment is ready for the private follow-up."

    sent = client.get(
        "/api/addons/team-addon/inbox",
        params={"agent_id": "lead", "direction": "sent", "item_type": "progress_report"},
    )
    assert sent.status_code == 200
    assert sent.json()["total_count"] == 1
    assert sent.json()["items"][0]["direction"] == "sent"

    invalid = client.get("/api/addons/team-addon/inbox?direction=sent")
    assert invalid.status_code == 400
    assert invalid.json() == {"error": "agent_id is required when filtering inbox direction"}


def test_team_addon_dispatch_events_are_structured_filterable_and_redacted(
    tmp_path: Path,
) -> None:
    repo = _seed_runtime_operations_state(tmp_path / "runtime")
    repo.append_dispatch_activity(
        TeamDispatchActivity(
            id="activity-failed",
            tool_name="team_submit_deliverable",
            caller_session_id="secret-session-id",
            caller_identity="agent:backend",
            agent_id="backend",
            dispatch_id="dispatch-1",
            status=TeamDispatchActivityStatus.FAILURE,
            error_code="stale_dispatch_context",
            error_message="A later directive must be handled.",
            correction_context_summary="inbox_item_id=inbox-late",
        )
    )
    client = _client(tmp_path)

    activity_view = client.get("/api/addons/team-addon/views/activity")
    assert activity_view.status_code == 200
    assert set(activity_view.json()["data"]["state"]) >= {"teams", "roles", "agents"}
    assert "inbox" not in activity_view.json()["data"]["state"]

    response = client.get(
        "/api/addons/team-addon/dispatch-events",
        params={
            "team_id": "team",
            "agent_id": "backend",
            "status": "failure",
            "q": "later directive",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["retention_days"] == 30
    assert payload["total_count"] == 1
    assert payload["events"] == [
        {
            "id": "activity-failed",
            "dispatch_id": "dispatch-1",
            "agent_id": "backend",
            "agent_display_name": "backend",
            "team_id": "team",
            "kind": "tool_failed",
            "tool_name": "team_submit_deliverable",
            "status": "failure",
            "progress_classification": "none",
            "summary": "A later directive must be handled.",
            "error_code": "stale_dispatch_context",
            "correction_context_summary": "inbox_item_id=inbox-late",
            "occurred_at": payload["events"][0]["occurred_at"],
        }
    ]
    serialized = json.dumps(payload)
    assert "secret-session-id" not in serialized
    assert "agent:backend" not in serialized


def test_team_addon_admin_api_creates_outcome_task_contract(
    tmp_path: Path,
) -> None:
    repo = _seed_runtime_operations_state(tmp_path / "runtime")

    async def _exercise() -> None:
        app = create_app(
            build_admin_runtime(_write_config(tmp_path, enable_team_addon=True))
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            unlock = await client.post("/api/admin/privileged/unlock")
            assert unlock.status_code == 200

            for kind in ("prepared_action", "report"):
                definition_response = await client.post(
                    "/api/addons/team-addon/outcome-kinds",
                    json={
                        "id": f"outcome-kind-{kind}",
                        "team_id": "team",
                        "project_id": "project",
                        "kind": kind,
                        "name": kind.replace("_", " ").title(),
                        "description": f"Definition for {kind}.",
                        "reference_requirements": "Provide a durable reference.",
                        "expected_revision": repo.read().revision,
                    },
                )
                assert definition_response.status_code == 201, definition_response.text

            response = await client.post(
                "/api/addons/team-addon/tasks",
                json={
                    "id": "outcome-task",
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 20,
                    "title": "Prepare an external action",
                    "description": "Produce a durable action, not only a report.",
                    "created_by_agent_id": "lead",
                    "task_kind": "outcome",
                    "outcome_contract": {
                        "required_outcome_kinds": ["prepared_action"],
                        "match": "all",
                        "allow_evidenced_noop": False,
                        "forbidden_outcome_kinds": ["report"],
                    },
                    "expected_revision": repo.read().revision,
                },
            )

            assert response.status_code == 201, response.text
            task = response.json()["tasks"]["outcome-task"]
            assert task["task_kind"] == "outcome"
            assert task["outcome_contract"]["required_outcome_kinds"] == [
                "prepared_action"
            ]
            assert task["outcome_contract"]["match"] == "all"
            assert task["outcome_contract"]["allow_evidenced_noop"] is False
            assert task["outcome_contract"]["forbidden_outcome_kinds"] == ["report"]
            assert {
                definition["kind"]
                for definition in task["outcome_contract"][
                    "outcome_kind_definitions"
                ]
            } == {"prepared_action", "report"}

            report_only = await client.post(
                "/api/addons/team-addon/deliverables",
                json={
                    "id": "report-only-deliverable",
                    "task_id": "outcome-task",
                    "producer_agent_id": "backend",
                    "summary": "Only a report was produced.",
                    "outcomes": [
                        {
                            "kind": "report",
                            "reference_ids": ["arbitrary-report-reference"],
                        }
                    ],
                    "expected_revision": response.json()["revision"],
                },
            )

            assert report_only.status_code == 400
            assert "forbidden outcome" in report_only.text
            assert "report-only-deliverable" not in repo.read().deliverables

            evidence_only = await client.post(
                "/api/addons/team-addon/deliverables",
                json={
                    "id": "evidence-only-deliverable",
                    "task_id": "outcome-task",
                    "producer_agent_id": "backend",
                    "summary": "The action was described but not persisted.",
                    "outcomes": [
                        {
                            "kind": "prepared_action",
                            "evidence_refs": ["report:prepared-action"],
                        }
                    ],
                    "expected_revision": response.json()["revision"],
                },
            )

            assert evidence_only.status_code == 400
            assert "reference_ids" in evidence_only.text
            assert "evidence-only-deliverable" not in repo.read().deliverables

            listing = await client.get("/api/addons/team-addon/outcome-kinds")
            assert listing.status_code == 200
            assert {
                definition["id"]
                for definition in listing.json()["outcome_kind_definitions"]
            } == {"outcome-kind-prepared_action", "outcome-kind-report"}
            updated = await client.patch(
                "/api/addons/team-addon/outcome-kinds/outcome-kind-prepared_action",
                json={
                    "name": "Prepared Action Updated",
                    "expected_revision": repo.read().revision,
                },
            )
            assert updated.status_code == 200, updated.text
            detail = await client.get(
                "/api/addons/team-addon/outcome-kinds/"
                "outcome-kind-prepared_action"
            )
            assert detail.status_code == 200
            assert detail.json()["name"] == "Prepared Action Updated"
            task_snapshot = (
                repo.read()
                .tasks["outcome-task"]
                .outcome_contract.outcome_kind_definitions[0]
            )
            assert task_snapshot.name == "Prepared Action"

    asyncio.run(_exercise())


def test_team_addon_admin_api_designates_explicit_integration_boundary(
    tmp_path: Path,
) -> None:
    repo = _seed_runtime_operations_state(tmp_path / "runtime")

    async def _exercise() -> None:
        app = create_app(
            build_admin_runtime(_write_config(tmp_path, enable_team_addon=True))
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            unlock = await client.post("/api/admin/privileged/unlock")
            assert unlock.status_code == 200

            ordinary = await client.post(
                "/api/addons/team-addon/tasks",
                json={
                    "id": "ordinary-edit",
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 20,
                    "title": "Ordinary task",
                    "description": "Submit then edit this task.",
                    "created_by_agent_id": "lead",
                    "expected_revision": repo.read().revision,
                },
            )
            assert ordinary.status_code == 201, ordinary.text
            ordinary_deliverable = await client.post(
                "/api/addons/team-addon/deliverables",
                json={
                    "id": "ordinary-deliverable",
                    "task_id": "ordinary-edit",
                    "producer_agent_id": "backend",
                    "summary": "Ordinary submission.",
                    "artifact_refs": ["artifact:ordinary"],
                    "expected_revision": repo.read().revision,
                },
            )
            assert ordinary_deliverable.status_code == 201, (
                ordinary_deliverable.text
            )
            ordinary_title_edit = await client.patch(
                "/api/addons/team-addon/tasks/ordinary-edit",
                json={
                    "title": "Ordinary task renamed",
                    "integration_boundary": False,
                    "deliverable_contract": None,
                    "expected_revision": repo.read().revision,
                },
            )
            assert ordinary_title_edit.status_code == 200, (
                ordinary_title_edit.text
            )
            assert ordinary_title_edit.json()["tasks"]["ordinary-edit"][
                "title"
            ] == "Ordinary task renamed"

            parent = await client.post(
                "/api/addons/team-addon/tasks",
                json={
                    "id": "clarification",
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "priority": 10,
                    "title": "Clarify and integrate",
                    "description": "Coordinate implementation contributions.",
                    "created_by_agent_id": "lead",
                    "expected_revision": repo.read().revision,
                },
            )
            assert parent.status_code == 201, parent.text
            child = await client.post(
                "/api/addons/team-addon/tasks",
                json={
                    "id": "implementation",
                    "owner_agent_id": "backend",
                    "project_id": "project",
                    "parent_task_id": "clarification",
                    "priority": 10,
                    "title": "Implement",
                    "description": "Produce the implementation contribution.",
                    "created_by_agent_id": "lead",
                    "expected_revision": repo.read().revision,
                },
            )
            assert child.status_code == 201, child.text

            designated = await client.patch(
                "/api/addons/team-addon/tasks/clarification",
                json={
                    "integration_boundary": True,
                    "deliverable_contract": {
                        "require_git_provenance": True,
                        "required_contribution_task_ids": ["implementation"],
                    },
                    "expected_revision": repo.read().revision,
                },
            )

            assert designated.status_code == 200, designated.text
            task = designated.json()["tasks"]["clarification"]
            assert task["integration_boundary"] is True
            assert task["deliverable_contract"] == {
                "require_git_provenance": True,
                "required_contribution_task_ids": ["implementation"],
            }

            detach_contribution = await client.patch(
                "/api/addons/team-addon/tasks/implementation",
                json={
                    "parent_task_id": None,
                    "expected_revision": repo.read().revision,
                },
            )
            assert detach_contribution.status_code == 400
            assert "must remain a direct child or dependency" in (
                detach_contribution.text
            )

            contribution = await client.post(
                "/api/addons/team-addon/deliverables",
                json={
                    "id": "implementation-deliverable",
                    "task_id": "implementation",
                    "producer_agent_id": "backend",
                    "summary": "Accepted implementation contribution.",
                    "artifact_refs": ["artifact:implementation"],
                    "state": "accepted",
                    "expected_revision": repo.read().revision,
                },
            )
            assert contribution.status_code == 201, contribution.text
            finished_child = await client.patch(
                "/api/addons/team-addon/tasks/implementation",
                json={
                    "state": "finished",
                    "expected_revision": repo.read().revision,
                },
            )
            assert finished_child.status_code == 200, finished_child.text

            artifact_only = await client.post(
                "/api/addons/team-addon/deliverables",
                json={
                    "id": "integration-artifact-only",
                    "task_id": "clarification",
                    "producer_agent_id": "backend",
                    "summary": "Aggregate without authoritative Git provenance.",
                    "artifact_refs": ["artifact:aggregate"],
                    "source_deliverable_ids": ["implementation-deliverable"],
                    "expected_revision": repo.read().revision,
                },
            )
            assert artifact_only.status_code == 400
            assert "cannot bind server-verified git_refs" in artifact_only.text
            assert "integration-artifact-only" not in repo.read().deliverables

            direct_finish = await client.patch(
                "/api/addons/team-addon/tasks/clarification",
                json={
                    "state": "finished",
                    "expected_revision": repo.read().revision,
                },
            )
            assert direct_finish.status_code == 400
            assert "accepted deliverable" in direct_finish.text

    asyncio.run(_exercise())


def test_team_addon_chat_defaults_to_current_operator_and_inspects_others_read_only(
    tmp_path: Path,
) -> None:
    repo = _seed_runtime_operations_state(tmp_path / "runtime")
    _state, conversation, _created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        operator_user_id="alice@example.com",
        operator_display_name="Alice",
        expected_revision=repo.read().revision,
    )
    client = _client(tmp_path)

    mine = client.get("/api/addons/team-addon/conversations")
    assert mine.status_code == 200
    assert mine.json()["current_operator"]["user_id"] == "admin"
    assert mine.json()["selected_operator"]["user_id"] == "admin"
    assert mine.json()["read_only"] is False
    assert mine.json()["conversations"] == []

    inspected = client.get(
        "/api/addons/team-addon/conversations?operator_user_id=alice@example.com"
    )
    assert inspected.status_code == 200
    inspected_payload = inspected.json()
    assert inspected_payload["selected_operator"]["user_id"] == "alice@example.com"
    assert inspected_payload["read_only"] is True
    assert inspected_payload["conversations"][0]["id"] == conversation.id
    assert inspected_payload["conversations"][0]["read_only"] is True

    blocked_send = client.post(
        f"/api/addons/team-addon/conversations/{conversation.id}/messages",
        json={"body": "I should not be able to send here."},
    )
    assert blocked_send.status_code == 403


def test_team_addon_state_payload_filters_chats_without_read_all(
    tmp_path: Path,
) -> None:
    repo = _seed_runtime_operations_state(tmp_path / "runtime")
    _state, alice_conversation, _created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        operator_user_id="alice@example.com",
        operator_display_name="Alice",
        expected_revision=repo.read().revision,
    )
    _team_chat_service(repo).record_operator_chat_message(
        conversation_id=alice_conversation.id,
        body="Alice private question",
        created_by="operator:alice@example.com",
        expected_revision=repo.read().revision,
    )
    _state, bob_conversation, _created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        operator_user_id="bob@example.com",
        operator_display_name="Bob",
        expected_revision=repo.read().revision,
    )
    _team_chat_service(repo).record_operator_chat_message(
        conversation_id=bob_conversation.id,
        body="Bob private question",
        created_by="operator:bob@example.com",
        expected_revision=repo.read().revision,
    )

    class AuthStub:
        def __init__(self, *, can_read_all: bool) -> None:
            self.can_read_all = can_read_all

        def read_session(self, _request) -> dict[str, str]:
            return {"login": "Alice@Example.com", "name": "Alice"}

        def can(self, _request, _permission: str) -> bool:
            return self.can_read_all

    scoped_payload = _team_state_payload_for_request(
        AuthStub(can_read_all=False),
        object(),  # type: ignore[arg-type]
        repo.read(),
    )
    assert set(scoped_payload["conversations"]) == {alice_conversation.id}
    assert bob_conversation.id not in scoped_payload["conversations"]
    assert {message["body"] for message in scoped_payload["conversation_messages"].values()} == {
        "Alice private question"
    }
    assert all(
        message["conversation_id"] == alice_conversation.id
        for message in scoped_payload["conversation_messages"].values()
    )

    read_all_payload = _team_state_payload_for_request(
        AuthStub(can_read_all=True),
        object(),  # type: ignore[arg-type]
        repo.read(),
    )
    assert {alice_conversation.id, bob_conversation.id}.issubset(
        set(read_all_payload["conversations"])
    )


def test_team_addon_state_payload_windows_history_but_keeps_current_records(
    tmp_path: Path,
) -> None:
    _repo, _conversation_id = _seed_long_chat_history(
        tmp_path / "runtime",
        message_count=125,
    )
    client = _client(tmp_path)

    response = client.get("/api/addons/team-addon/state?history_limit=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["conversation_messages_total_count"] == 125
    assert payload["conversation_messages_returned_count"] == 1
    assert payload["conversation_messages_windowed"] is True
    assert set(payload["conversation_messages"]) == {"msg-124"}
    assert payload["inbox_total_count"] == 3
    assert payload["inbox_returned_count"] == 3
    assert payload["inbox_windowed"] is False
    assert {"inbox-1", "escalation-1"}.issubset(payload["inbox"])
    assert set(payload["action_records"]) == {"action-1"}
    assert set(payload["release_packages"]) == {"release-1"}
    assert set(payload["git_promotions"]) == {"promotion-1"}
    assert set(payload["ephemeral_assignments"]) == {
        "ephemeral-cleanup",
        "ephemeral-failed",
    }
    assert set(payload["resource_tokens"]["inbox"]) == set(payload["inbox"])
    assert set(payload["resource_tokens"]["action_records"]) == {"action-1"}


def test_team_addon_state_payload_adds_release_outbox_metadata(tmp_path: Path) -> None:
    repo = _seed_runtime_operations_state(tmp_path / "runtime")
    repo.enqueue_outbox_record(
        TeamOutboxRecord(
            id="outbox-promotion-1",
            intent_type=GIT_PROMOTION_EVENT_TYPE,
            aggregate_type="git_promotion",
            aggregate_id="promotion-1",
            status=TeamOutboxStatus.CLAIMED,
            attempt_count=3,
            claimed_by="effect-worker",
            last_error_code="stale_index_lock",
            last_error_message="index.lock exists",
        )
    )
    payload = team_addon_api.team_state_payload(repo.read())

    team_addon_api._add_release_outbox_metadata(payload, repo)

    release = payload["release_packages"]["release-1"]
    assert release["promotion_attempt_count"] == 3
    assert release["active_outbox_record_ids"] == ["outbox-promotion-1"]
    assert release["outbox_records"] == [
        {
            "id": "outbox-promotion-1",
            "status": "claimed",
            "aggregate_id": "promotion-1",
            "available_at": release["outbox_records"][0]["available_at"],
            "attempt_count": 3,
            "claimed_by": "effect-worker",
            "last_error_code": "stale_index_lock",
            "last_error_message": "index.lock exists",
        }
    ]


def test_team_addon_filtered_state_treats_git_work_contexts_as_collection() -> None:
    payload = {
        "revision": 1,
        "tasks": {"task": {"id": "task"}},
        "git_work_contexts": {
            "alpha-context": {"id": "alpha-context"},
            "beta-context": {"id": "beta-context"},
        },
    }

    without_contexts = team_addon_api._filter_state_payload(
        dict(payload),
        collections={"tasks"},
        ids_by_collection={},
    )
    assert "tasks" in without_contexts
    assert "git_work_contexts" not in without_contexts

    scoped_contexts = team_addon_api._filter_state_payload(
        dict(payload),
        collections={"git_work_contexts"},
        ids_by_collection={"git_work_contexts": {"alpha-context"}},
    )
    assert set(scoped_contexts["git_work_contexts"]) == {"alpha-context"}


def test_team_addon_bootstrap_counts_only_current_operator_chats_without_read_all(
    tmp_path: Path,
) -> None:
    repo = _seed_runtime_operations_state(tmp_path / "runtime")
    _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        operator_user_id="alice@example.com",
        operator_display_name="Alice",
        expected_revision=repo.read().revision,
    )
    _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        operator_user_id="bob@example.com",
        operator_display_name="Bob",
        expected_revision=repo.read().revision,
    )

    class AuthStub:
        def read_session(self, _request) -> dict[str, str]:
            return {"login": "Alice@Example.com", "name": "Alice"}

        def can(self, _request, _permission: str) -> bool:
            return False

    payload = _bootstrap_payload_for_request(
        AuthStub(),
        object(),  # type: ignore[arg-type]
        repo.read(),
    )

    assert payload["selected_operator"]["user_id"] == "alice@example.com"
    assert payload["counts"]["conversations"] == 1


def test_team_addon_conversation_stream_snapshots_are_bounded(tmp_path: Path) -> None:
    repo, conversation_id = _seed_long_chat_history(tmp_path / "runtime")

    class RequestStub:
        async def is_disconnected(self) -> bool:
            return False

    async def read_first_event() -> str:
        stream = _conversation_snapshot_events(
            repo,
            RequestStub(),  # type: ignore[arg-type]
            conversation_id,
            poll_interval_seconds=0,
        )
        try:
            return await asyncio.wait_for(anext(stream), timeout=1.0)
        finally:
            await stream.aclose()

    event = asyncio.run(read_first_event())
    line = next(line for line in event.splitlines() if line.startswith("data: "))
    payload = json.loads(line.removeprefix("data: "))

    assert event.startswith("event: snapshot\n")
    assert payload["conversation"]["message_count"] == 75
    assert len(payload["conversation"]["messages"]) == 50
    assert payload["conversation"]["messages"][0]["id"] == "msg-025"
    assert payload["conversation"]["messages"][-1]["id"] == "msg-074"


def test_team_addon_conversation_stream_emits_updated_snapshots(tmp_path: Path) -> None:
    repo = _seed_runtime_operations_state(tmp_path / "runtime")

    def _clear_backend_runtime(state):
        state.agent_runtimes.pop("backend", None)
        return state

    repo.update(_clear_backend_runtime)
    _state, conversation, _created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        created_by="operator",
    )

    class RequestStub:
        async def is_disconnected(self) -> bool:
            return False

    def _payload_from_event(event: str) -> dict:
        line = next(line for line in event.splitlines() if line.startswith("data: "))
        return json.loads(line.removeprefix("data: "))

    async def read_two_events() -> tuple[str, str]:
        stream = _conversation_snapshot_events(
            repo,
            RequestStub(),  # type: ignore[arg-type]
            conversation.id,
            poll_interval_seconds=0,
        )
        try:
            first = await asyncio.wait_for(anext(stream), timeout=1.0)
            _, message = _team_chat_service(repo).record_operator_chat_message(
                conversation_id=conversation.id,
                body="Can you summarize the blocker?",
                expected_revision=repo.read().revision,
            )
            dispatch_id = f"team-chat:{message.id}"
            _team_chat_service(repo).begin_chat_message_dispatch(
                message_id=message.id,
                dispatch_id=dispatch_id,
            )
            _team_agent_action_service(repo).apply_agent_action(
                action={
                    "agent_id": "backend",
                    "dispatch_id": dispatch_id,
                    "chat_replies": [
                        {
                            "conversation_id": conversation.id,
                            "message_id": message.id,
                            "body": "The dependency task is still open.",
                        }
                    ],
                },
                dispatch_id=dispatch_id,
                expected_revision=repo.read().revision,
            )
            second = await asyncio.wait_for(anext(stream), timeout=1.0)
            return first, second
        finally:
            await stream.aclose()

    first_event, second_event = asyncio.run(read_two_events())
    first_payload = _payload_from_event(first_event)
    second_payload = _payload_from_event(second_event)

    assert first_event.startswith("event: snapshot\n")
    assert first_payload["conversation"]["messages"] == []
    assert [item["sender"] for item in second_payload["conversation"]["messages"]] == [
        "operator",
        "agent",
    ]
    assert second_payload["conversation"]["messages"][1]["body"] == (
        "The dependency task is still open."
    )


def test_team_addon_state_stream_emits_updated_snapshots(tmp_path: Path) -> None:
    repo = _seed_runtime_operations_state(tmp_path / "runtime")

    class RequestStub:
        async def is_disconnected(self) -> bool:
            return False

    def _payload_from_event(event: str) -> dict:
        line = next(line for line in event.splitlines() if line.startswith("data: "))
        return json.loads(line.removeprefix("data: "))

    async def read_two_events() -> tuple[str, str]:
        stream = _state_snapshot_events(
            lambda: team_addon_api.team_state_payload(repo.read()),
            RequestStub(),  # type: ignore[arg-type]
            poll_interval_seconds=0,
        )
        try:
            first = await asyncio.wait_for(anext(stream), timeout=1.0)
            _team_admin_service(repo).create_or_reprioritize_task(
                task_id="live-task",
                owner_agent_id="backend",
                project_id="project",
                priority=4,
                title="Live task",
                description="Task created while the UI stream is connected.",
                created_by_agent_id="lead",
            )
            second = await asyncio.wait_for(anext(stream), timeout=1.0)
            return first, second
        finally:
            await stream.aclose()

    first_event, second_event = asyncio.run(read_two_events())
    first_payload = _payload_from_event(first_event)
    second_payload = _payload_from_event(second_event)

    assert first_event.startswith("event: snapshot\n")
    assert second_event.startswith("event: snapshot\n")
    assert second_payload["revision"] > first_payload["revision"]
    assert "live-task" in second_payload["tasks"]


def test_team_addon_revision_state_stream_keeps_alive_when_revision_unchanged(
    tmp_path: Path,
) -> None:
    repo = _seed_runtime_operations_state(tmp_path / "runtime")

    class RequestStub:
        async def is_disconnected(self) -> bool:
            return False

    async def read_two_events() -> tuple[str, str]:
        stream = _state_snapshot_events(
            lambda: team_addon_api._state_revision_payload(repo.read()),
            RequestStub(),  # type: ignore[arg-type]
            poll_interval_seconds=0,
        )
        try:
            first = await asyncio.wait_for(anext(stream), timeout=1.0)
            second = await asyncio.wait_for(anext(stream), timeout=1.0)
            return first, second
        finally:
            await stream.aclose()

    first_event, second_event = asyncio.run(read_two_events())

    assert first_event.startswith("event: snapshot\n")
    assert second_event == ": keepalive\n\n"


def test_agent_voice_session_create_query_and_end(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = _write_config(tmp_path, enable_team_addon=True)
    repo = _seed_runtime_operations_state(tmp_path / "runtime")
    _team_admin_service(repo).update_agent(
        agent_id="backend",
        voice_profile=AgentVoiceProfile(
            voice="cedar",
            speaking_style="Calm, concise, and lightly upbeat.",
        ),
        set_voice_profile=True,
        expected_revision=repo.read().revision,
    )
    service = build_admin_runtime(config_path)
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-backend",
            directory_path="root",
            model="gpt-5",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    client_secret_calls: list[dict[str, object]] = []

    class _FakeRealtimeClientSecretService:
        def __init__(self, *, config) -> None:
            self.config = config

        def create_client_secret(self, **kwargs):
            client_secret_calls.append(kwargs)
            return {"value": "ek_test", "expires_at": 123}

    monkeypatch.setattr(
        "evander_team_addon.api.OpenAIRealtimeClientSecretService",
        _FakeRealtimeClientSecretService,
    )
    operator_identity = {
        "user_id": "jonathan",
        "display_name": "Jonathan",
        "actor": "operator:jonathan",
    }
    monkeypatch.setattr(
        "evander_team_addon.api._operator_identity_from_request",
        lambda _auth, _request: dict(operator_identity),
    )
    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)

    response = client.post("/api/addons/team-addon/agents/backend/voice-sessions", json={})

    assert response.status_code == 201
    payload = response.json()
    assert payload["channel"] == "browser_agent_call"
    assert payload["status"] == "connecting"
    assert payload["client_secret"]["value"] == "ek_test"
    assert payload["realtime"]["transport"] == "webrtc_browser"
    assert payload["realtime"]["voice"] == "cedar"
    assert payload["realtime"]["speaking_style"] == "Calm, concise, and lightly upbeat."
    assert payload["projection"]["metadata"]["agent_id"] == "backend"
    assert payload["projection"]["context"]["open_tasks"][0]["title"] == "Blocked task"
    prompt = client_secret_calls[0]["instructions"]
    assert "Selected agent: backend (backend)" in prompt
    assert "Blocked task" in prompt
    assert "Status update?" in prompt
    assert "Waiting task deliverable." in prompt
    assert "Voice delivery style for this agent:" in prompt
    assert "Calm, concise, and lightly upbeat." in prompt
    assert client_secret_calls[0]["safety_identifier"] == "team-addon:backend"
    assert client_secret_calls[0]["voice"] == "cedar"
    record = service.runtime.voice_session_store.get_by_voice_session_id(
        payload["voice_session_id"]
    )
    assert record is not None
    assert record.channel == "browser_agent_call"
    assert record.origin_session_id == "session-backend"
    assert record.prompt_metadata["voice_profile"]["voice"] == "cedar"
    assert record.caller_metadata["voice_profile"]["speaking_style"] == (
        "Calm, concise, and lightly upbeat."
    )
    assert record.caller_metadata["operator_user_id"] == "jonathan"
    assert record.caller_metadata["operator_display_name"] == "Jonathan"

    event_response = client.post(
        f"/api/addons/team-addon/voice-sessions/{payload['voice_session_id']}/events",
        json={
            "status": "active",
            "text": "What is blocked?",
            "speaker": "operator",
            "event_type": "browser_call.transcript",
        },
    )
    assert event_response.status_code == 200
    assert event_response.json()["transcript_events"][0]["text"] == "What is blocked?"
    assert event_response.json()["transcript_events"][0]["speaker"] == "Jonathan (jonathan)"
    assert event_response.json()["transcript_events"][0]["metadata"]["speaker_role"] == "operator"

    operator_identity.update(
        user_id="ada",
        display_name="Ada",
        actor="operator:ada",
    )
    foreign_event = client.post(
        f"/api/addons/team-addon/voice-sessions/{payload['voice_session_id']}/events",
        json={"status": "active", "text": "Can you hear me?", "speaker": "operator"},
    )
    assert foreign_event.status_code == 403
    assert foreign_event.json()["error"] == "voice session belongs to a different operator"
    operator_identity.update(
        user_id="jonathan",
        display_name="Jonathan",
        actor="operator:jonathan",
    )

    query_response = client.post(
        f"/api/addons/team-addon/voice-sessions/{payload['voice_session_id']}/queries",
        json={
            "question": "Which files changed?",
            "transcript_excerpt": "operator: Which files changed?",
            "deadline_seconds": 60,
        },
    )
    assert query_response.status_code == 201
    query = query_response.json()
    assert query["status"] == "pending"
    assert query["transcript_excerpt"] == "Jonathan (jonathan): Which files changed?"
    assert service.runtime.wake_store.get_request(f"voice-query:{query['query_id']}") is not None

    def _clear_backend_runtime(state):
        state.agent_runtimes.pop("backend", None)
        return state

    repo.update(_clear_backend_runtime)

    end_response = client.post(
        f"/api/addons/team-addon/voice-sessions/{payload['voice_session_id']}/end",
        json={
            "status": "ended",
            "summary": "Operator asked about blockers.",
            "transcript": "operator: What is blocked?\nagent: The task is blocked.",
        },
    )
    assert end_response.status_code == 200
    ended = end_response.json()
    assert ended["status"] == "ended"
    assert ended["reconciliation_status"] == "scheduled"
    completed_record = service.runtime.voice_session_store.get_by_voice_session_id(
        payload["voice_session_id"]
    )
    assert completed_record is not None
    assert completed_record.transcript == (
        "Jonathan (jonathan): What is blocked?\nbackend: The task is blocked."
    )
    assert completed_record.reconciliation_context["reason"] == "voice_session_reconciliation"
    assert completed_record.reconciliation_context["tasks"][0]["title"] == "Blocked task"
    wake = service.runtime.wake_store.get_request(
        f"voice-session-reconcile:{payload['voice_session_id']}"
    )
    assert wake is not None
    assert completed_record.reconciliation_prompt is not None
    assert "Current Team context JSON:" in completed_record.reconciliation_prompt
    assert '"reason": "voice_session_reconciliation"' in completed_record.reconciliation_prompt
    assert "Blocked task" in completed_record.reconciliation_prompt
    assert wake.resume_prompt.prompt == completed_record.reconciliation_prompt


def test_voice_reconciliation_context_keeps_actionable_blockers_and_reviews(
    tmp_path: Path,
) -> None:
    state = _seed_runtime_operations_state(tmp_path / "runtime").read()
    state.agent_runtimes.pop("backend", None)
    oldest = datetime(2026, 7, 1, tzinfo=UTC)

    for index in range(8):
        created_at = oldest + timedelta(minutes=index)
        state.blockers[f"resolved-blocker-{index}"] = TeamBlocker(
            id=f"resolved-blocker-{index}",
            team_id="team",
            project_id="project",
            source_kind=TeamBlockerSourceKind.EXPLICIT,
            source_id=f"resolved-source-{index}",
            state=TeamBlockerState.RESOLVED,
            route=TeamBlockerRoute.RESOLVED,
            summary=f"Resolved blocker {index}",
            created_at=created_at,
            updated_at=created_at,
            resolved_at=created_at,
        )
        state.review_requests[f"terminal-review-{index}"] = ReviewRequest(
            id=f"terminal-review-{index}",
            deliverable_id="deliverable",
            reviewer_agent_id="backend",
            state=(
                TeamRecordState.ACCEPTED
                if index % 2 == 0
                else TeamRecordState.REJECTED
            ),
            created_at=created_at,
        )

    current_at = oldest + timedelta(days=1)
    state.blockers["open-blocker"] = TeamBlocker(
        id="open-blocker",
        team_id="team",
        project_id="project",
        source_kind=TeamBlockerSourceKind.EXPLICIT,
        source_id="open-source",
        owner_agent_id="backend",
        summary="Current open blocker",
        created_at=current_at,
        updated_at=current_at,
    )
    for review_id, review_state in (
        ("pending-review", TeamRecordState.PENDING),
        ("blocked-review", TeamRecordState.BLOCKED),
    ):
        state.review_requests[review_id] = ReviewRequest(
            id=review_id,
            deliverable_id="deliverable",
            reviewer_agent_id="backend",
            state=review_state,
            created_at=current_at,
        )

    context = build_voice_reconciliation_context(
        state,
        agent_id="backend",
        session_records={},
    )

    blocker_ids = {blocker["id"] for blocker in context["blockers"]}
    assert "open-blocker" in blocker_ids
    assert blocker_ids.isdisjoint(
        {f"resolved-blocker-{index}" for index in range(8)}
    )
    assert {blocker["state"] for blocker in context["blockers"]} == {"open"}
    assert {review["id"] for review in context["reviews"]} == {
        "pending-review",
        "blocked-review",
    }


def test_agent_voice_session_records_chat_conversation_message(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = _write_config(tmp_path, enable_team_addon=True)
    repo = _seed_runtime_operations_state(tmp_path / "runtime")
    state, conversation, _created = _team_chat_service(repo).create_or_get_conversation(
        agent_id="backend",
        expected_revision=repo.read().revision,
    )
    service = build_admin_runtime(config_path)
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-backend",
            directory_path="root",
            model="gpt-5",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )

    class _FakeRealtimeClientSecretService:
        def __init__(self, *, config) -> None:
            self.config = config

        def create_client_secret(self, **_kwargs):
            return {"value": "ek_test", "expires_at": 123}

    monkeypatch.setattr(
        "evander_team_addon.api.OpenAIRealtimeClientSecretService",
        _FakeRealtimeClientSecretService,
    )
    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)

    created = client.post(
        "/api/addons/team-addon/agents/backend/voice-sessions",
        json={"conversation_id": conversation.id, "request_id": "chat-call-request-1"},
    )

    assert created.status_code == 201
    payload = created.json()
    assert payload["created"] is True
    assert payload["revision"] > state.revision
    assert payload["conversation"]["id"] == conversation.id
    assert payload["conversation_message"]["message_type"] == "voice_call"
    assert payload["conversation_message"]["status"] == "running"
    assert payload["conversation_message"]["voice_session_id"] == payload["voice_session_id"]
    record = service.runtime.voice_session_store.get_by_voice_session_id(
        payload["voice_session_id"]
    )
    assert record is not None
    assert record.caller_metadata["call_surface"] == "team_chat_tab"
    assert record.caller_metadata["conversation_id"] == conversation.id

    retry = client.post(
        "/api/addons/team-addon/agents/backend/voice-sessions",
        json={"conversation_id": conversation.id, "request_id": "chat-call-request-1"},
    )

    assert retry.status_code == 200
    retry_payload = retry.json()
    assert retry_payload["created"] is False
    assert retry_payload["voice_session_id"] == payload["voice_session_id"]
    assert retry_payload["conversation_message"]["id"] == payload["conversation_message"]["id"]
    assert retry_payload["revision"] == payload["revision"]
    reread_before_end = TeamStateRepository(tmp_path / "runtime").read()
    assert [
        message.id
        for message in reread_before_end.conversation_messages.values()
        if message.voice_session_id == payload["voice_session_id"]
    ] == [payload["conversation_message"]["id"]]

    ended = client.post(
        f"/api/addons/team-addon/voice-sessions/{payload['voice_session_id']}/end",
        json={"status": "ended", "summary": "Operator asked about blockers."},
    )

    assert ended.status_code == 200
    reread = TeamStateRepository(tmp_path / "runtime").read()
    chat_message = reread.conversation_messages[payload["conversation_message"]["id"]]
    assert chat_message.status == "ended"
    assert chat_message.metadata["summary"] == "Operator asked about blockers."
    assert reread.conversations[conversation.id].last_message_preview == "Voice call ended"
    assert [
        message.id
        for message in reread.conversation_messages.values()
        if message.voice_session_id == payload["voice_session_id"]
    ] == [payload["conversation_message"]["id"]]


def test_agent_voice_session_can_end_by_request_id(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = _write_config(tmp_path, enable_team_addon=True)
    _seed_runtime_operations_state(tmp_path / "runtime")
    service = build_admin_runtime(config_path)
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-backend",
            directory_path="root",
            model="gpt-5",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )

    class _FakeRealtimeClientSecretService:
        def __init__(self, *, config) -> None:
            self.config = config

        def create_client_secret(self, **_kwargs):
            return {"value": "ek_test", "expires_at": 123}

    monkeypatch.setattr(
        "evander_team_addon.api.OpenAIRealtimeClientSecretService",
        _FakeRealtimeClientSecretService,
    )
    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)
    request_id = "browser-call:test-request"
    created = client.post(
        "/api/addons/team-addon/agents/backend/voice-sessions",
        json={"request_id": request_id},
    )
    assert created.status_code == 201

    ended = client.post(
        f"/api/addons/team-addon/voice-session-requests/{request_id}/end",
        json={"status": "ended", "error": "Cancelled during startup."},
    )

    assert ended.status_code == 200
    payload = ended.json()
    assert payload["request_id"] == request_id
    assert payload["status"] == "ended"
    assert payload["reconciliation_status"] == "scheduled"


def _auto_dev_squad_materialization_payload(
    *,
    duplicate_reviewer_agent_id: str | None = None,
    bad_git: bool = False,
    expected_revision: int = 0,
) -> dict[str, object]:
    git_payload: dict[str, object] = {
        "managed_repo_id": "mr_0123456789abcdefabcd",
    }
    if bad_git:
        git_payload["managed_repo_id"] = "not-a-managed-repository"

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
                "agent_id": (duplicate_reviewer_agent_id or "reviewer-auto-dev-squad"),
                "session_id": "session-reviewer-auto-dev-squad",
            },
            "release_operator": {
                "agent_id": "release-operator-auto-dev-squad",
                "session_id": "session-release-operator-auto-dev-squad",
            },
        },
    }


def _accounting_department_materialization_payload(
    *,
    expected_revision: int = 0,
) -> dict[str, object]:
    return {
        "expected_revision": expected_revision,
        "team": {
            "name": "Accounting Department",
            "operator_communication_target_id": "teams://accounting-ops",
        },
        "project": {
            "name": "Bookkeeping Operations",
        },
        "roles": {
            "manager": {"session_id": "session-manager-accounting"},
            "project_lead": {"session_id": "session-bookkeeping-lead-accounting"},
            "bookkeeping_clerk": {"session_id": "session-bookkeeping-clerk-accounting"},
            "invoice_reviewer": {"session_id": "session-invoice-reviewer-accounting"},
            "payment_preparer": {"session_id": "session-payment-preparer-accounting"},
            "tax_compliance_reviewer": {"session_id": "session-tax-compliance-reviewer-accounting"},
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
        "auto-dev-squad",
        "accounting-department",
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
        "total_count": 0,
        "limit": 50,
        "offset": 0,
        "has_more": False,
        "facets": {"types": [], "statuses": []},
        "entries": [],
        "source": "derived_from_current_team_state",
        "kind": "activity_timeline",
        "completeness": "retained_current_state_only",
        "retention_boundary": (
            "Only records and timestamps retained in the current TeamState snapshot are "
            "represented."
        ),
        "description": (
            "Activity reconstructed from records retained in the current TeamState snapshot; "
            "earlier transitions, deleted or compacted records, and previous field values may "
            "be unavailable. This is not an audit log."
        ),
        "filters": {},
    }

    ui = enabled.get("/addons/team-addon/")
    assert ui.status_code == 200
    assert ui.headers["cache-control"] == "no-cache"
    assert "data-team-addon-ui" in ui.text
    assert 'name="manager_display_name"' in ui.text
    assert 'name="lead_agent_id"' in ui.text
    assert 'name="lead_display_name"' not in ui.text
    assert 'class="workspace-dashboard-page"' in ui.text
    assert "data-workspace-dashboard-metrics" in ui.text
    assert "data-workspace-attention-column" in ui.text
    assert "data-workspace-status-column" in ui.text
    assert "Review &amp; Delivery" in ui.text
    assert "data-review-delivery-view" in ui.text
    assert 'data-filter="blocked"' in ui.text
    assert 'name="lead_session_id"' not in ui.text
    assert 'data-team-view="projects"' in ui.text
    assert 'id="team-projects-page"' in ui.text
    assert 'id="create-project-from-projects-form"' in ui.text
    assert 'data-form-id="create-project-form"' in ui.text
    assert "data-projects-focus-list" in ui.text
    assert "data-projects-focus-detail" in ui.text
    assert 'data-team-view="cases"' in ui.text
    assert 'id="team-cases-page"' in ui.text
    assert 'data-team-view="governance"' in ui.text
    assert "data-governance-create-toggle" in ui.text
    assert 'id="governance-create-panel"' in ui.text
    assert 'id="create-governance-group-form"' in ui.text
    assert ui.text.count('id="create-governance-group-form"') == 1
    assert 'data-team-view="chat"' in ui.text
    assert 'id="team-chat-page"' in ui.text
    assert "data-chat-conversations-view" in ui.text
    assert 'name="repo_source"' in ui.text
    assert 'name="managed_repo_id"' in ui.text
    assert 'name="managed_repo_directory_path"' in ui.text
    assert 'name="bare_repo"' not in ui.text
    assert "data-agent-source-picker" in ui.text
    assert "data-id-field=" not in ui.text
    assert "Team ID <input" not in ui.text
    assert "Project ID <input" not in ui.text
    assert "Agent ID <input" not in ui.text
    assert "Task ID <input" not in ui.text
    assert "Profile ID <input" not in ui.text
    assert "Deliverable ID <input" not in ui.text
    assert "Review ID <input" not in ui.text
    assert 'id="edit-modal"' in ui.text
    assert 'id="edit-record-form"' in ui.text
    assert "Default base branch for new tasks" in ui.text

    script = enabled.get("/addons/team-addon/team-ui.js")
    assert script.status_code == 200
    assert script.headers["cache-control"] == "no-cache"
    assert 'const API_BASE = "/api/addons/team-addon";' in script.text
    assert "createTeamMailController" in script.text
    assert 'const linkAttributes = image' in script.text
    assert 'download="${escapeAttr(filename)}"' in script.text

    mail_script = enabled.get("/addons/team-addon/team-mail.js")
    assert mail_script.status_code == 200
    assert "createTeamMailController" in mail_script.text
    assert "/inbox?" in mail_script.text
    activity_script = enabled.get("/addons/team-addon/team-activity.js")
    assert activity_script.status_code == 200
    assert "createTeamActivityController" in activity_script.text
    assert "/dispatch-events?" in activity_script.text

    assert "renderWorkspaceDashboard" in script.text
    assert "data-team-link-view" in script.text
    assert "renderRuntimeOperations" in script.text
    assert "runtimeOperationsView" in script.text
    assert "data-runtime-filter" in script.text
    assert "needs_attention" in script.text
    assert "due_leased_agents" in script.text
    assert "blocked_waiting_work" in script.text
    assert "ephemeral_work" in script.text
    assert "renderTemplateRoleFields" in script.text
    assert "data-template-role-fields" in script.text
    assert "data-template-git-fields" in script.text
    assert "const roles = {};" in script.text
    assert "dependency_task_ids" in script.text
    assert "parent_task_id" in script.text
    assert "caseStatuses" in script.text
    assert "renderCases" in script.text
    assert "create-case-form" in script.text
    assert "case_id" in script.text
    assert "review_profile_id" in script.text
    assert "parseCriterionResults" in script.text
    assert "data-review-profile-criteria-editor" in script.text
    assert "criterion_label" in script.text
    assert "renderReviewDeliveryWorkspace" in script.text
    assert "submitReviewDecision" in script.text
    assert "bindWorkspaceReviewDecisionForms" in script.text
    assert "bindExistingAgentPickers" in script.text
    assert "bindTeamAgentSessionControls" in script.text
    assert "data-core-agent-session-picker" in script.text
    assert "team-agent-create:created" in script.text
    assert "data-team-inline-create-toggle" in script.text
    assert "data-team-inline-create-form" in script.text
    assert "projectLeadAgentOptionsForTeam" in script.text
    assert '"projects"' in script.text
    assert "renderProjectFocus" in script.text
    assert "data-project-focus-id" in script.text
    assert "projectReviewRoleOptionsForProject" in script.text
    assert "create-review-profile-for-project-form" in script.text
    assert "data-delegated-create-form" in script.text
    assert "setAutogeneratedValue" not in script.text
    assert "submitEditForm" in script.text
    assert "voice_profile: agent?.voice_profile || null" in script.text
    assert "elements.editModal?.contains(event.target)" in script.text
    assert "firstEditableField?.focus()" in script.text
    assert "refreshSelects({ includeEditForm = false } = {})" in script.text
    assert "elements.editForm?.contains(select)" in script.text
    assert 'select.dataset.options === "review-profile-edit-outcome-kinds"' in script.text
    assert "function reviewProfileEditOutcomeOptions(select)" in script.text
    assert "state.projects[text(profile.project_id)] || null" in script.text
    assert "gitToBareRepo" not in script.text
    assert "managed_repo_id" in script.text
    assert "managed_repo_directory_path: git.directory_path" in script.text
    assert "git.directory_path = directoryPath" in script.text
    assert "created_by_agent_id: text(data.created_by_agent_id)" in script.text
    assert "fieldTooltips" in script.text
    assert "create-task-form:created_by_agent_id" in script.text
    assert "create-task-form:git_base_branch" in script.text
    assert "data-task-board-create-form" in script.text
    assert 'readonlyField("Git base branch (locked)"' in script.text
    assert '["Git base branch", text(task.git_base_branch)]' in script.text
    assert '["Git base branch", release.git_base_branch]' in script.text
    assert "decorateFieldTooltips" in script.text
    assert "data-dismiss-action-id" in script.text
    assert "data-resume-release-recovery-blocker-id" in script.text
    assert "resumeReleaseRecovery" in script.text
    assert "admin_ui_release_recovery" in script.text
    assert "Resume release recovery" in script.text
    assert "data-resolve-terminal-blocker-id" in script.text
    assert "resolveTerminalBlocker" in script.text
    assert "data-candidate-correction-recovery-id" in script.text
    assert "recoverCandidateCorrectionSuccessor" in script.text
    assert "candidateCorrectionRecoveryTaskForBlocker" in script.text
    assert "data-candidate-correction-recovery-task-id" in script.text
    assert "View replacement task" in script.text
    assert "Create verified replacement" in script.text
    assert "Add instructions for the verified replacement task" in script.text
    assert "payload.instruction" in script.text
    assert "Authorize waiver instead" in script.text
    assert "existing_recovery_task_id" in script.text
    assert "/candidate-correction-recoveries/" in script.text
    assert "data-start-dependency-composition-recovery-id" in script.text
    assert "dependencyCompositionRecoveryForBlocker" in script.text
    assert "startDependencyCompositionRecovery" in script.text
    assert "/dependency-composition-recovery" in script.text
    assert '["Git base commit pin", text(task.git_base_commit_sha)]' in script.text
    assert "teamFetch(`${API_BASE}/costs?backfill=false`" in script.text
    assert "teamFetch(`${API_BASE}/costs/backfill`" in script.text
    assert "data-cost-backfill-refresh" in script.text
    assert 'guardTeamMutation("refresh transcript costs")' in script.text
    assert '["[data-cost-backfill-refresh]", "refresh transcript costs"]' in script.text
    assert 'lockedAttrs("refresh transcript costs")' in script.text
    assert "renderTeamCostCounter" in script.text
    assert "renderTeamCostBlockedNotice" in script.text
    assert "Blocked: estimated cost cap reached" in script.text
    assert "renderProjectCostCounter" in script.text
    assert "renderAgentInboxHeaderIndicators" in script.text
    assert "renderAgentInboxIndicatorIcon" in script.text
    assert "data-agent-call-agent-id" in script.text
    assert "RTCPeerConnection" in script.text
    assert "oai-events" in script.text
    assert "query_origin_agent" in script.text
    assert "cancelRequested" in script.text
    assert "agentVoiceCallIsCurrent" in script.text
    assert "Retry end" in script.text
    assert "voice-session-requests" in script.text
    assert "agent-inbox-indicator" in script.text
    assert 'direction: "down"' in script.text
    assert 'direction: "up"' in script.text
    assert "agent-inbox-icon--${escapeAttr(direction)}" in script.text
    assert "agent-inbox-bubble" in script.text
    assert "inboxReplyIntentLabel" in script.text
    assert "no reply needed" in script.text
    assert "Status requests" in script.text
    assert "Progress reports" in script.text
    assert "max_cost_usd" in script.text
    assert "cost_limit_reached" in script.text
    assert "renderGovernance" in script.text
    assert "governance_group_costs" in script.text
    assert "data-governance-agenda-handle-id" in script.text
    assert "loadConversations" in script.text
    assert "renderChat" in script.text
    assert "data-chat-message-form" in script.text
    assert 'document.addEventListener("submit"' in script.text
    assert 'closest?.("[data-chat-message-form]")' in script.text
    assert "data-agent-call-conversation-id" in script.text
    assert "ensureStateStream" in script.text
    assert "/state/stream" in script.text
    assert "applyStateSnapshot" in script.text
    assert "timelineSearchParams" in script.text
    assert 'search.set("limit", String(timelinePageSize))' in script.text
    assert "reloadActiveTeamView({ preserveForms: true })" in script.text
    assert "invalidateLoadedTeamViews();" in script.text
    assert "function refetchTimelineAfterPageClamp" in script.text
    assert "void loadTimeline();" in script.text
    assert "teamViewCacheGeneration += 1" in script.text
    assert "loadedTeamViews.clear()" in script.text
    assert "loadView(activeTeamView, { force: true, preserveForms: true })" in script.text
    assert "restoreFormDrafts(drafts)" in script.text
    assert "chatReadMarkers" in script.text
    assert "chatConversationHasUnread" in script.text
    assert "team-view-tab-badge" in script.text
    assert "chatNotificationSoundStorageKey" in script.text
    assert "data-chat-notification-sound" in script.text
    assert "maybePlayChatNotificationSound" in script.text
    assert "maybePlayChatCollectionNotificationSound" in script.text
    assert "document.hasFocus" in script.text
    assert "AudioContext" in script.text
    assert "syncTeamStateFromChatPayload(payload)" in script.text
    assert "new EventSource" in script.text
    assert 'document.addEventListener("visibilitychange"' in script.text
    assert 'activeTeamView === "chat"' in script.text
    assert "teamRequestTimeoutMs" in script.text
    assert "ensureChatStream" in script.text
    assert "/stream" in script.text
    assert "revision: state.revision,\n    conversations" in script.text

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
    assert ".team-inline-create-form" in stylesheet.text
    assert ".team-agent-session-controls" in stylesheet.text
    assert ".projects-workbench" in stylesheet.text
    assert ".projects-focus-row" in stylesheet.text
    assert ".project-review-profile-form" in stylesheet.text
    assert ".field-tooltip" in stylesheet.text
    assert ".chat-conversation-row.has-unread" in stylesheet.text
    assert ".chat-unread-badge" in stylesheet.text
    assert ".team-view-tab.has-unread" in stylesheet.text
    assert ".chat-sound-toggle" in stylesheet.text
    assert ".field-tooltip-bubble" in stylesheet.text
    assert ".cost-counter" in stylesheet.text
    assert ".cost-counter--warning" in stylesheet.text
    assert ".team-cost-blocked-notice" in stylesheet.text
    assert ".state-pill.neutral" in stylesheet.text
    assert ".candidate-correction-recovery-status" in stylesheet.text
    assert ".dependency-composition-recovery-status" in stylesheet.text

    org_flow_script = enabled.get("/addons/team-addon/team-org-flow.js")
    assert org_flow_script.status_code == 200
    assert "team-org-flow:edit-agent" in org_flow_script.text
    assert "data-team-org-flow-canvas" in org_flow_script.text
    assert "org-flow-activity-bubble" in org_flow_script.text
    assert "org-flow-message-pulse" in org_flow_script.text

    org_flow_stylesheet = enabled.get("/addons/team-addon/team-org-flow.css")
    assert org_flow_stylesheet.status_code == 200
    assert ".team-org-flow-canvas" in org_flow_stylesheet.text
    assert ".org-flow-governance-node" in org_flow_stylesheet.text
    assert "org-flow-active-halo" in org_flow_stylesheet.text
    assert "prefers-reduced-motion:reduce" in org_flow_stylesheet.text

    agent_create_script = enabled.get("/addons/team-addon/team-agent-create.js")
    assert agent_create_script.status_code == 200
    assert "team-agent-create:created" in agent_create_script.text
    assert "admin-privileged-operations" in agent_create_script.text
    assert "Create and register agent" in agent_create_script.text

    agent_create_stylesheet = enabled.get("/addons/team-addon/team-agent-create.css")
    assert agent_create_stylesheet.status_code == 200
    assert ".agent-crud-modal" in agent_create_stylesheet.text
    assert ".agent-inbox-indicator" in stylesheet.text
    assert ".agent-inbox-icon" in stylesheet.text
    assert ".agent-inbox-popover" in stylesheet.text
    assert ".agent-inbox-bubble" in stylesheet.text
    assert ".agent-voice-call-panel" in stylesheet.text
    assert ".agent-voice-transcript" in stylesheet.text
    assert ".chat-layout" in stylesheet.text
    assert ".chat-message-bubble" in stylesheet.text
    assert ".chat-composer" in stylesheet.text

    html = enabled.get("/addons/team-addon/")
    assert html.status_code == 200
    assert 'name="max_cost_usd"' in html.text
    assert "team-agent-create.js" in html.text
    assert "team-agent-create.css" in html.text


def test_team_addon_admin_api_mutations_require_privileged_unlock(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AUTH_MODE", "none")
    client = _client(tmp_path, unlock_privileged=False)

    create_payload = {
        "expected_revision": 0,
        "id": "team",
        "name": "Team",
        "manager_agent_id": "manager",
        "manager_session_id": "session-manager",
    }
    locked_response = client.post("/api/addons/team-addon/teams", json=create_payload)
    read_response = client.get("/api/addons/team-addon/state")
    unlock_response = client.post("/api/admin/privileged/unlock")
    unlocked_response = client.post("/api/addons/team-addon/teams", json=create_payload)

    assert locked_response.status_code == 403
    assert locked_response.json()["error"] == "Privileged operations are locked."
    assert read_response.status_code == 200
    assert read_response.json()["revision"] == 0
    assert unlock_response.status_code == 200
    assert unlock_response.json()["privileged_operations"]["unlocked"] is True
    assert unlocked_response.status_code == 201
    assert unlocked_response.json()["teams"]["team"]["name"] == "Team"


def test_team_addon_admin_api_governance_group_flow(tmp_path: Path) -> None:
    client = _client(tmp_path)

    team = client.post(
        "/api/addons/team-addon/teams",
        json={
            "expected_revision": 0,
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
        },
    )
    assert team.status_code == 201

    group = client.post(
        "/api/addons/team-addon/governance-groups",
        json={
            "expected_revision": team.json()["revision"],
            "id": "portfolio",
            "name": "Portfolio",
            "manager_agent_id": "manager",
            "member_team_ids": ["team"],
            "goals": [{"description": "Keep the team under budget"}],
            "max_cost_usd": "10.00",
        },
    )
    assert group.status_code == 201
    state = group.json()
    assert state["governance_groups"]["portfolio"]["member_team_ids"] == ["team"]
    assert state["governance_groups"]["portfolio"]["goals"][0]["id"].startswith(
        "governance-goal-portfolio"
    )
    assert (
        state["governance_groups"]["portfolio"]["goals"][0]["name"]
        == "Keep the team under budget"
    )

    updated = client.patch(
        "/api/addons/team-addon/governance-groups/portfolio",
        json={
            "expected_revision": state["revision"],
            "goals": [
                {
                    "name": "Budget stewardship",
                    "description": "Keep the team within its approved budget.",
                }
            ],
        },
    )
    assert updated.status_code == 200
    state = updated.json()
    updated_goal = state["governance_groups"]["portfolio"]["goals"][0]
    assert updated_goal["id"].startswith("governance-goal-portfolio-budget-stewardship")
    assert updated_goal["name"] == "Budget stewardship"
    assert updated_goal["description"] == "Keep the team within its approved budget."

    agenda = client.post(
        "/api/addons/team-addon/governance-agenda",
        json={
            "expected_revision": state["revision"],
            "governance_group_id": "portfolio",
            "item_type": "operator_directive",
            "priority": "high",
            "team_id": "team",
            "title": "Review team status",
            "summary": "Check whether the team needs intervention.",
        },
    )
    assert agenda.status_code == 201
    agenda_state = agenda.json()
    agenda_id = next(iter(agenda_state["governance_agenda"]))
    assert agenda_state["governance_agenda"][agenda_id]["state"] == "pending"

    org_view = client.get("/api/addons/team-addon/views/org")
    assert org_view.status_code == 200
    org_state = org_view.json()["data"]["state"]
    assert "portfolio" in org_state["governance_groups"]
    assert agenda_id in org_state["governance_agenda"]
    assert "governance_runtimes" in org_state

    handled = client.post(
        f"/api/addons/team-addon/governance-agenda/{agenda_id}/handle",
        json={"expected_revision": agenda_state["revision"]},
    )
    assert handled.status_code == 200
    assert handled.json()["governance_agenda"][agenda_id]["state"] == "handled"


def test_team_addon_admin_api_generates_missing_operator_ids(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/api/addons/team-addon/teams",
        json={
            "name": "Platform Team",
            "manager_session_id": "session-manager",
            "manager_display_name": "Platform Manager",
            "expected_revision": 0,
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert "team-platform-team" in body["teams"]
    manager_id = "agent-team-platform-team-platform-manager"
    assert manager_id in body["agents"]

    response = client.post(
        "/api/addons/team-addon/teams",
        json={
            "name": "Platform Team",
            "manager_session_id": "session-manager-2",
            "expected_revision": 1,
        },
    )
    assert response.status_code == 201
    assert "team-platform-team-2" in response.json()["teams"]

    response = client.post(
        "/api/addons/team-addon/projects",
        json={
            "team_id": "team-platform-team",
            "name": "Runtime Upgrade",
            "lead_session_id": "session-lead",
            "lead_display_name": "Project Lead",
            "goals": [{"description": "Ship runtime upgrade"}],
            "expected_revision": 2,
        },
    )
    assert response.status_code == 201
    body = response.json()
    project_id = "project-team-platform-team-runtime-upgrade"
    assert project_id in body["projects"]
    assert body["projects"][project_id]["goals"][0]["id"] == (
        "goal-project-team-platform-team-runtime-upgrade-ship-runtime-upgrade"
    )

    response = client.post(
        "/api/addons/team-addon/projects",
        json={
            "team_id": "team-platform-team",
            "name": "Manager Led",
            "lead_agent_id": manager_id,
            "expected_revision": body["revision"],
        },
    )
    assert response.status_code == 201
    body = response.json()
    manager_project_id = "project-team-platform-team-manager-led"
    assert body["projects"][manager_project_id]["lead_agent_id"] == manager_id
    manager = body["agents"][manager_id]
    assert manager["project_ids"] == [manager_project_id]
    assert manager["lead_project_ids"] == [manager_project_id]
    assert manager["role_id"] == "team-platform-team__manager"
    assert manager["reports_to_agent_id"] is None
    assert manager_id not in manager["subordinate_agent_ids"]

    response = client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "explicit-team",
            "name": "Explicit Team",
            "manager_agent_id": "explicit-manager",
            "manager_session_id": "session-explicit-manager",
            "manager_role_id": "explicit-manager-role",
            "expected_revision": 4,
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert "explicit-team" in body["teams"]
    assert "explicit-manager" in body["agents"]
    assert "explicit-manager-role" in body["roles"]


def test_team_addon_admin_api_runtime_operations_smoke(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    _seed_runtime_operations_state(runtime_dir)
    session_repo = JsonSessionManifestRepository(runtime_dir / "sessions.json")
    session_repo.upsert_session(
        SessionManifest(
            session_id="session-backend",
            vm_id="vm-backend",
            allowed_tools=[],
            expires_at=datetime(2025, 12, 31, tzinfo=UTC),
        )
    )
    session_repo.upsert_session(
        SessionManifest(
            session_id="session-lead",
            vm_id="vm-lead",
            allowed_tools=[],
            expires_at=datetime(2099, 1, 1, tzinfo=UTC),
        )
    )
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
    assert backend["session_is_expired"] is True
    assert backend["session_expires_at"] == "2025-12-31T00:00:00+00:00"

    attention_rows = state["runtime_operations"]["attention"]
    attention = {row["kind"]: row for row in attention_rows}
    assert set(attention) == {
        "expired_agent_session",
        "blocked_task",
        "team_blocker",
        "waiting_dependency_task",
        "pending_inbox_item",
        "pending_escalation",
        "failed_action_record",
        "failed_git_promotion",
        "blocked_release_package",
        "failed_ephemeral_assignment",
        "cleanup_pending_ephemeral_assignment",
    }
    assert attention["expired_agent_session"]["agent_id"] == "backend"
    assert attention["expired_agent_session"]["session_id"] == "session-backend"
    assert attention["blocked_task"]["id"] == "blocked-task"
    assert attention["blocked_task"]["state"] == "blocked"
    assert attention["waiting_dependency_task"]["id"] == "waiting-task"
    assert attention["waiting_dependency_task"]["dependency_status"] == "waiting"
    assert any(
        row["kind"] == "pending_inbox_item" and row["inbox_item_id"] == "inbox-1"
        for row in attention_rows
    )
    assert attention["pending_escalation"]["inbox_item_id"] == "escalation-1"
    assert attention["failed_action_record"]["dispatch_id"] == "dispatch-backend"
    assert attention["failed_action_record"]["error"] == "build failed"
    assert attention["failed_git_promotion"]["promotion_id"] == "promotion-1"
    assert attention["failed_git_promotion"]["release_package_id"] == "release-1"
    assert attention["failed_git_promotion"]["commit_sha"] == "abc123"
    assert attention["blocked_release_package"]["release_package_id"] == "release-1"
    assert attention["blocked_release_package"]["state"] == "blocked"
    assert attention["failed_ephemeral_assignment"]["assignment_id"] == "ephemeral-failed"
    assert attention["failed_ephemeral_assignment"]["state"] == "failed"
    assert attention["cleanup_pending_ephemeral_assignment"]["assignment_id"] == "ephemeral-cleanup"
    assert attention["cleanup_pending_ephemeral_assignment"]["state"] == "cleanup_pending"


def test_team_addon_admin_api_runtime_operations_use_admin_web_addon_service_adapter(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_dir = tmp_path / "runtime"
    _seed_runtime_operations_state(runtime_dir)
    session_repo = JsonSessionManifestRepository(runtime_dir / "sessions.json")
    session_repo.upsert_session(
        SessionManifest(
            session_id="session-backend",
            vm_provider="container_worker_vm",
            allowed_tools=[],
            expires_at=datetime(2099, 1, 1, tzinfo=UTC),
        )
    )
    service = build_admin_runtime(_write_config(tmp_path, enable_team_addon=True))
    list_agents_calls: list[dict[str, object]] = []

    def list_agents(self, **kwargs):
        del self
        list_agents_calls.append(kwargs)
        return [
            SimpleNamespace(
                session_id="session-backend",
                backend="container_worker_vm",
                status="running",
                host={
                    "active_execution_count": 2,
                    "latest_execution_id": "exec-backend",
                },
            )
        ]

    monkeypatch.setattr(
        service.vm_agent_admin,
        "list_agents",
        MethodType(list_agents, service.vm_agent_admin),
    )
    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)

    response = client.get("/api/addons/team-addon/state")

    assert response.status_code == 200
    assert list_agents_calls == [
        {
            "include_inactive": True,
            "direct_only": False,
            "all_directories": True,
            "reconcile_containers": False,
            "omit_vm_state": True,
            "summary_only": True,
            "backend": "container_worker_vm",
        }
    ]
    session_health = {
        row["agent_id"]: row for row in response.json()["runtime_operations"]["session_health"]
    }
    backend_health = session_health["backend"]
    assert backend_health["agent_execution_backend"] == "container_worker_vm"
    assert backend_health["agent_execution_status"] == "running"
    assert backend_health["agent_active_execution_count"] == 2
    assert backend_health["agent_latest_execution_id"] == "exec-backend"


def test_team_addon_view_payload_avoids_agent_execution_inventory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_dir = tmp_path / "runtime"
    _seed_runtime_operations_state(runtime_dir)
    session_repo = JsonSessionManifestRepository(runtime_dir / "sessions.json")
    session_repo.upsert_session(
        SessionManifest(
            session_id="session-backend",
            vm_provider="container_worker_vm",
            allowed_tools=[],
            expires_at=datetime(2099, 1, 1, tzinfo=UTC),
        )
    )
    service = build_admin_runtime(_write_config(tmp_path, enable_team_addon=True))

    def fail_agent_inventory(self, **_kwargs):
        del self
        raise AssertionError("scoped Team views must not list every agent execution")

    monkeypatch.setattr(
        service.vm_agent_admin,
        "list_agents",
        MethodType(fail_agent_inventory, service.vm_agent_admin),
    )
    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)

    response = client.get("/api/addons/team-addon/views/people")

    assert response.status_code == 200
    state = response.json()["data"]["state"]
    session_health = {row["agent_id"]: row for row in state["runtime_operations"]["session_health"]}
    assert session_health["backend"]["session_present"] is True
    assert session_health["backend"]["status"] == "active"
    assert "agent_execution_status" not in session_health["backend"]


def test_team_addon_state_stream_payload_is_revision_scoped(tmp_path: Path) -> None:
    repo = _seed_runtime_operations_state(tmp_path / "runtime")

    payload = team_addon_api._state_revision_payload(repo.read())

    assert payload["response_scope"] == "revision"
    assert payload["revision"] == repo.read().revision
    assert "teams" not in payload
    assert "runtime_operations" not in payload


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


def test_team_addon_admin_api_costs_default_skips_backfill_and_uses_threadpool(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_dir = tmp_path / "runtime"
    _seed_runtime_operations_state(runtime_dir)
    cost_calls: list[bool] = []
    threadpool_calls: list[dict[str, object]] = []

    async def fake_run_in_threadpool(func, *args, **kwargs):
        threadpool_calls.append({"func": getattr(func, "__name__", ""), "kwargs": dict(kwargs)})
        return func(*args, **kwargs)

    def fake_costs_payload(self, *, backfill_transcripts: bool = False):
        cost_calls.append(backfill_transcripts)
        return {
            "generated_at": "2026-06-16T12:00:00+00:00",
            "coverage_status": "complete",
            "coverage_reasons": [],
            "team_costs": {},
            "governance_group_costs": {},
            "project_costs": {},
            "unattributed_costs": None,
            "source_counts": {},
            "events_count": 0,
            "priced_events_count": 0,
            "unpriced_events_count": 0,
        }

    monkeypatch.setattr(team_addon_api, "run_in_threadpool", fake_run_in_threadpool)
    monkeypatch.setattr(team_addon_api.TeamCostService, "costs_payload", fake_costs_payload)
    client = _client(tmp_path)

    response = client.get("/api/addons/team-addon/costs")

    assert response.status_code == 200
    assert cost_calls == [False]
    assert threadpool_calls == [
        {"func": "_team_costs_payload", "kwargs": {"backfill_transcripts": False}}
    ]
    assert "backfill" not in response.json()


def test_team_addon_admin_api_costs_get_backfill_requires_privileged_unlock(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AUTH_MODE", "none")
    runtime_dir = tmp_path / "runtime"
    _seed_runtime_operations_state(runtime_dir)
    cost_calls: list[bool] = []

    async def fake_run_in_threadpool(func, *args, **kwargs):
        return func(*args, **kwargs)

    def fake_costs_payload(self, *, backfill_transcripts: bool = False):
        cost_calls.append(backfill_transcripts)
        return {
            "generated_at": "2026-06-16T12:00:00+00:00",
            "coverage_status": "complete",
            "coverage_reasons": [],
            "team_costs": {},
            "governance_group_costs": {},
            "project_costs": {},
            "unattributed_costs": None,
            "source_counts": {},
            "events_count": 0,
            "priced_events_count": 0,
            "unpriced_events_count": 0,
        }

    monkeypatch.setattr(team_addon_api, "run_in_threadpool", fake_run_in_threadpool)
    monkeypatch.setattr(team_addon_api.TeamCostService, "costs_payload", fake_costs_payload)
    client = _client(tmp_path, unlock_privileged=False)

    locked_response = client.get("/api/addons/team-addon/costs?backfill=true")
    read_response = client.get("/api/addons/team-addon/costs?backfill=false")
    unlock_response = client.post("/api/admin/privileged/unlock")
    unlocked_response = client.get("/api/addons/team-addon/costs?backfill=true")

    assert locked_response.status_code == 403
    assert locked_response.json()["error"] == "Privileged operations are locked."
    assert read_response.status_code == 200
    assert unlock_response.status_code == 200
    assert unlock_response.json()["privileged_operations"]["unlocked"] is True
    assert unlocked_response.status_code == 200
    assert cost_calls == [False, True]


def test_team_addon_admin_api_costs_backfill_endpoint_runs_backfill_in_threadpool(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_dir = tmp_path / "runtime"
    _seed_runtime_operations_state(runtime_dir)
    cost_calls: list[bool] = []
    threadpool_calls: list[dict[str, object]] = []

    async def fake_run_in_threadpool(func, *args, **kwargs):
        threadpool_calls.append({"func": getattr(func, "__name__", ""), "kwargs": dict(kwargs)})
        return func(*args, **kwargs)

    def fake_costs_payload(self, *, backfill_transcripts: bool = False):
        cost_calls.append(backfill_transcripts)
        return {
            "generated_at": "2026-06-16T12:00:00+00:00",
            "coverage_status": "complete",
            "coverage_reasons": [],
            "team_costs": {},
            "governance_group_costs": {},
            "project_costs": {},
            "unattributed_costs": None,
            "source_counts": {},
            "events_count": 0,
            "priced_events_count": 0,
            "unpriced_events_count": 0,
            "backfill": {
                "coverage_status": "complete",
                "sources_checked": 0,
                "sources_missing": 0,
                "files_scanned": 0,
                "records_seen": 0,
                "records_imported": 0,
                "duplicate_records": 0,
                "records_skipped": 0,
                "errors": [],
            },
        }

    monkeypatch.setattr(team_addon_api, "run_in_threadpool", fake_run_in_threadpool)
    monkeypatch.setattr(team_addon_api.TeamCostService, "costs_payload", fake_costs_payload)
    client = _client(tmp_path)

    response = client.post("/api/addons/team-addon/costs/backfill")

    assert response.status_code == 200
    assert cost_calls == [True]
    assert threadpool_calls == [
        {"func": "_team_costs_payload", "kwargs": {"backfill_transcripts": True}}
    ]
    assert response.json()["backfill"]["coverage_status"] == "complete"


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
        row["kind"] != "failed_action_record" for row in state["runtime_operations"]["attention"]
    )


def test_task_blocked_state_requires_audited_operator_repair_api(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    assert client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
            "expected_revision": 0,
        },
    ).status_code == 201
    assert client.post(
        "/api/addons/team-addon/projects",
        json={
            "id": "project",
            "team_id": "team",
            "name": "Project",
            "lead_agent_id": "lead",
            "lead_session_id": "session-lead",
            "expected_revision": 1,
        },
    ).status_code == 201
    _post_backend_role(client, expected_revision=2)
    assert client.post(
        "/api/addons/team-addon/agents",
        json={
            "id": "backend",
            "team_id": "team",
            "project_id": "project",
            "role_id": "project__backend",
            "session_id": "session-backend",
            "reports_to_agent_id": "lead",
            "expected_revision": 3,
        },
    ).status_code == 201
    assert client.post(
        "/api/addons/team-addon/tasks",
        json={
            "id": "task",
            "owner_agent_id": "backend",
            "project_id": "project",
            "priority": 1,
            "title": "Repair legacy state",
            "description": "Exercise the reason-required repair path.",
            "created_by_agent_id": "lead",
            "expected_revision": 4,
        },
    ).status_code == 201

    rejected = client.patch(
        "/api/addons/team-addon/tasks/task",
        json={"state": "blocked", "expected_revision": 5},
    )
    assert rejected.status_code == 400
    assert "operator repair endpoint" in rejected.json()["error"]

    blocked = client.post(
        "/api/addons/team-addon/tasks/task/repair-state",
        json={
            "state": "blocked",
            "reason": "Imported legacy state omitted its intrinsic blocker.",
            "reference_ids": ["incident-42"],
            "expected_revision": 5,
        },
    )
    assert blocked.status_code == 200
    blocked_payload = blocked.json()
    assert blocked_payload["tasks"]["task"]["state"] == "blocked"
    blocker = next(iter(blocked_payload["blockers"].values()))
    assert blocker["reason_code"] == "operator_repair"
    assert blocker["reporter_actor"]

    rejected_exit = client.patch(
        "/api/addons/team-addon/tasks/task",
        json={"state": "in_progress", "expected_revision": 6},
    )
    assert rejected_exit.status_code == 400

    repaired = client.post(
        "/api/addons/team-addon/tasks/task/repair-state",
        json={
            "state": "in_progress",
            "reason": "The dependency was repaired and verified.",
            "reference_ids": ["incident-42", "verification-7"],
            "expected_revision": 6,
        },
    )
    assert repaired.status_code == 200
    repaired_payload = repaired.json()
    assert repaired_payload["tasks"]["task"]["state"] == "in_progress"
    assert next(iter(repaired_payload["blockers"].values()))["state"] == "resolved"


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
    _post_backend_role(client, expected_revision=2)
    client.post(
        "/api/addons/team-addon/agents",
        json={
            "id": "backend",
            "team_id": "team",
            "project_id": "project",
            "role_id": "project__backend",
            "session_id": "session-backend",
            "reports_to_agent_id": "lead",
            "expected_revision": 3,
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
            "state": "pending",
            "expected_revision": 4,
        },
    )
    assert response.status_code == 201
    response = client.patch(
        "/api/addons/team-addon/tasks/task",
        json={
            "title": "Build backend v2",
            "expected_revision": 5,
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
            "expected_revision": 6,
        },
    )
    assert response.status_code == 201
    response = client.post(
        "/api/addons/team-addon/review-requests",
        json={
            "id": "review",
            "deliverable_id": "deliverable",
            "reviewer_agent_id": "lead",
            "expected_revision": 7,
        },
    )
    assert response.status_code == 201
    response = client.post(
        "/api/addons/team-addon/review-requests/review/decision",
        json={
            "state": "accepted",
            "expected_revision": 8,
        },
    )
    assert response.status_code == 200, response.text

    timeline = client.get(
        "/api/addons/team-addon/timeline",
        params=[
            ("type", "task"),
            ("type", "review"),
            ("status", "accepted"),
            ("status", "finished"),
            ("attention_needed", "false"),
            ("agent_id", "lead"),
        ],
    )

    assert timeline.status_code == 200
    payload = timeline.json()
    assert payload["derived"] is True
    assert payload["audit_log"] is False
    assert payload["source"] == "derived_from_current_team_state"
    assert payload["kind"] == "activity_timeline"
    assert payload["completeness"] == "retained_current_state_only"
    assert payload["retention_boundary"] == (
        "Only records and timestamps retained in the current TeamState snapshot are "
        "represented."
    )
    assert payload["description"] == (
        "Activity reconstructed from records retained in the current TeamState snapshot; "
        "earlier transitions, deleted or compacted records, and previous field values may "
        "be unavailable. This is not an audit log."
    )
    assert payload["filters"] == {
        "agent_id": "lead",
        "type": ["task", "review"],
        "status": ["accepted", "finished"],
        "attention_needed": False,
    }
    assert payload["timestampless_count"] == 0
    assert {entry["id"] for entry in payload["entries"]} == {
        "task:task:created",
        "task:task:updated",
        "review:review:created",
        "review:review:decision",
    }
    assert all(
        entry["status"] == ("finished" if entry["type"] == "task" else "accepted")
        for entry in payload["entries"]
    )
    assert all(entry["attention_needed"] is False for entry in payload["entries"])

    windowed = client.get(
        "/api/addons/team-addon/timeline",
        params=[
            ("type", "task"),
            ("type", "review"),
            ("status", "accepted"),
            ("status", "finished"),
            ("attention_needed", "false"),
            ("agent_id", "lead"),
            ("limit", "2"),
            ("offset", "1"),
        ],
    )

    assert windowed.status_code == 200
    windowed_payload = windowed.json()
    assert windowed_payload["total_count"] == 4
    assert windowed_payload["limit"] == 2
    assert windowed_payload["offset"] == 1
    assert windowed_payload["has_more"] is True
    assert len(windowed_payload["entries"]) == 2
    assert windowed_payload["facets"] == {
        "types": ["deliverable", "review", "task"],
        "statuses": ["accepted", "finished"],
    }

    clamped = client.get("/api/addons/team-addon/timeline?limit=999")
    assert clamped.status_code == 200
    assert clamped.json()["limit"] == team_addon_api.MAX_TIMELINE_LIMIT


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

    _post_backend_role(client, expected_revision=2)

    response = client.post(
        "/api/addons/team-addon/agents",
        json={
            "id": "backend",
            "team_id": "team",
            "project_id": "project",
            "role_id": "project__backend",
            "session_id": "session-backend",
            "reports_to_agent_id": "lead",
            "display_name": "  Backend Agent  ",
            "ephemeral_policy": {"max_concurrent": 2},
            "expected_revision": 3,
        },
    )
    assert response.status_code == 201
    assert response.json()["revision"] == 4
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
            "expected_revision": 4,
        },
    )
    assert response.status_code == 201
    assert response.json()["tasks"]["task"]["state"] == "pending"

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
    assert response.json()["tasks"]["task"]["state"] == "pending"
    assert response.json()["tasks"]["task"]["waiting_reason"] is None

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
    assert response.json()["revision"] == 7
    assert response.json()["deliverables"]["deliverable"][
        "review_request_ids"
    ] == ["review"]

    response = client.patch(
        "/api/addons/team-addon/tasks/task",
        json={
            "state": "waiting_for_review",
            "expected_revision": 7,
        },
    )
    assert response.status_code == 200
    state = response.json()
    assert state["revision"] == 8
    assert state["tasks"]["task"]["state"] == "waiting_for_review"
    assert state["tasks"]["task"]["waiting_reason"] == "review"
    assert "runtime_operations" in state
    assert {row["id"] for row in state["runtime_operations"]["agents"]} == {
        "backend",
        "lead",
        "manager",
    }


def test_team_addon_admin_api_case_flow(tmp_path: Path) -> None:
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
    _post_backend_role(client, expected_revision=2)
    response = client.post(
        "/api/addons/team-addon/agents",
        json={
            "id": "backend",
            "team_id": "team",
            "project_id": "project",
            "role_id": "project__backend",
            "session_id": "session-backend",
            "reports_to_agent_id": "lead",
            "expected_revision": 3,
        },
    )
    assert response.status_code == 201

    response = client.post(
        "/api/addons/team-addon/cases",
        json={
            "id": "case",
            "team_id": "team",
            "project_id": "project",
            "title": "Customer ticket",
            "description": "External support ticket.",
            "status": "triage",
            "priority": "high",
            "owner_agent_id": "lead",
            "source": "github",
            "source_ref": "issue-42",
            "tags": ["support", "bug"],
            "expected_revision": 4,
        },
    )
    assert response.status_code == 201
    state = response.json()
    assert state["cases"]["case"]["status"] == "triage"
    assert state["cases"]["case"]["source_ref"] == "issue-42"

    response = client.post(
        "/api/addons/team-addon/tasks",
        json={
            "id": "task",
            "owner_agent_id": "backend",
            "project_id": "project",
            "case_id": "case",
            "priority": 10,
            "title": "Investigate ticket",
            "description": "Reproduce and fix the issue.",
            "created_by_agent_id": "lead",
            "expected_revision": 5,
        },
    )
    assert response.status_code == 201
    state = response.json()
    assert state["tasks"]["task"]["case_id"] == "case"
    assert state["cases"]["case"]["task_ids"] == ["task"]

    response = client.patch(
        "/api/addons/team-addon/cases/case",
        json={
            "status": "in_progress",
            "description": "",
            "source_ref": None,
            "task_ids": [],
            "expected_revision": 6,
        },
    )
    assert response.status_code == 200
    state = response.json()
    assert state["cases"]["case"]["status"] == "in_progress"
    assert state["cases"]["case"]["description"] == ""
    assert state["cases"]["case"]["source_ref"] is None
    assert state["cases"]["case"]["task_ids"] == []
    assert state["tasks"]["task"]["case_id"] is None


def test_team_addon_admin_api_create_task_defaults_creator_to_operator(tmp_path: Path) -> None:
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
    _post_backend_role(client, expected_revision=2)
    client.post(
        "/api/addons/team-addon/agents",
        json={
            "id": "backend",
            "team_id": "team",
            "project_id": "project",
            "role_id": "project__backend",
            "session_id": "session-backend",
            "reports_to_agent_id": "lead",
            "expected_revision": 3,
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
            "expected_revision": 4,
        },
    )

    assert response.status_code == 201
    task = response.json()["tasks"]["task"]
    assert task["created_by_agent_id"] is None
    assert task["created_by_actor"] == "operator:admin"

    timeline = client.get("/api/addons/team-addon/timeline?type=task").json()
    created = next(entry for entry in timeline["entries"] if entry["id"] == "task:task:created")
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
    _post_backend_role(client, expected_revision=2)
    response = client.post(
        "/api/addons/team-addon/agents",
        json={
            "id": "backend",
            "team_id": "team",
            "project_id": "project",
            "role_id": "project__backend",
            "session_id": "session-backend",
            "reports_to_agent_id": "lead",
            "expected_revision": 3,
        },
    )
    assert response.status_code == 201

    response = client.patch(
        "/api/addons/team-addon/teams/team",
        json={
            "name": "Renamed Team",
            "operator_communication_target_id": None,
            "archived": True,
            "expected_revision": 4,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["revision"] == 5
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
                "managed_repo_id": "mr_0123456789abcdefabcd",
            },
            "expected_revision": 5,
        },
    )
    assert response.status_code == 200
    body = response.json()
    project = body["projects"]["project"]
    assert body["revision"] == 6
    assert project["name"] == "Renamed Project"
    assert project["status"] == "blocked"
    assert project["goals"][0]["id"] == "goal"
    assert project["git"]["managed_repo_id"] == "mr_0123456789abcdefabcd"

    response = client.post(
        "/api/addons/team-addon/roles",
        json={
            "id": "project__implementer",
            "team_id": "team",
            "project_id": "project",
            "name": "Implementer",
            "expected_revision": 6,
        },
    )
    assert response.status_code == 201

    response = client.patch(
        "/api/addons/team-addon/agents/backend",
        json={
            "project_id": "project",
            "role_id": "project__implementer",
            "session_id": "session-backend-2",
            "reports_to_agent_id": "manager",
            "display_name": None,
            "voice_profile": {
                "voice": "sage",
                "speaking_style": "Measured and direct.",
            },
            "ephemeral_policy": {"max_concurrent": 4},
            "status": "paused",
            "expected_revision": 7,
        },
    )
    assert response.status_code == 200
    body = response.json()
    backend = body["agents"]["backend"]
    assert body["revision"] == 8
    assert backend["role_id"] == "project__implementer"
    assert backend["role_name"] == "Implementer"
    assert backend["session_id"] == "session-backend-2"
    assert backend["reports_to_agent_id"] == "manager"
    assert backend["display_name"] is None
    assert backend["voice_profile"] == {
        "voice": "sage",
        "speaking_style": "Measured and direct.",
    }
    assert backend["ephemeral_policy"]["max_concurrent"] == 4
    assert backend["status"] == "paused"
    assert body["agents"]["lead"]["subordinate_agent_ids"] == []
    assert body["agents"]["manager"]["subordinate_agent_ids"] == ["lead", "backend"]


def test_team_addon_admin_api_agent_context_clearing_toggle(tmp_path: Path) -> None:
    client = _client(tmp_path)
    runtime_dir = tmp_path / "runtime"
    repo = TeamStateRepository(runtime_dir)
    _team_admin_service(repo).create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
    )
    _team_admin_service(repo).create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
    )
    _team_admin_service(repo).create_role(
        role_id="project__backend",
        team_id="team",
        project_id="project",
        name="Backend",
    )
    _team_admin_service(repo).register_agent(
        agent_id="backend",
        team_id="team",
        project_id="project",
        role_id="project__backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
    )

    response = client.patch(
        "/api/addons/team-addon/agents/backend",
        json={
            "role_id": "project__backend",
            "session_id": "session-backend",
            "auto_context_clearing_enabled": True,
            "expected_revision": 4,
        },
    )

    assert response.status_code == 200
    runtime = response.json()["agent_runtimes"]["backend"]
    assert runtime["context_clearing_policy"] == "idle"
    assert runtime["auto_context_clearing_enabled"] is True
    assert runtime["context_rotation_pending"] is True
    assert runtime["context_rotation_reason"] == "idle"
    assert "Context continuity checkpoint" in runtime["last_handoff_summary"]
    assert "Active tasks:" not in runtime["last_handoff_summary"]

    response = client.patch(
        "/api/addons/team-addon/agents/backend",
        json={
            "role_id": "project__backend",
            "session_id": "session-backend",
            "auto_context_clearing_enabled": False,
            "expected_revision": 5,
        },
    )

    assert response.status_code == 200
    runtime = response.json()["agent_runtimes"]["backend"]
    assert runtime["context_clearing_policy"] == "off"
    assert runtime["auto_context_clearing_enabled"] is False
    assert runtime["context_rotation_pending"] is False
    assert runtime["context_rotation_reason"] is None
    assert runtime["context_rotation_requested_at"] is None
    assert runtime["next_wake_at"] is None
    assert runtime["last_handoff_summary"] is None

    response = client.patch(
        "/api/addons/team-addon/agents/backend",
        json={
            "context_clearing_policy": "every_wake",
            "expected_revision": 6,
        },
    )

    assert response.status_code == 200
    runtime = response.json()["agent_runtimes"]["backend"]
    assert runtime["context_clearing_policy"] == "every_wake"
    assert runtime["auto_context_clearing_enabled"] is True
    assert runtime["context_rotation_pending"] is False
    assert runtime["next_wake_at"] is None


def test_team_addon_state_projects_reset_bounded_input_tokens(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    repo = _seed_runtime_operations_state(runtime_dir)
    reset_at = datetime(2026, 6, 16, 12, 0, tzinfo=UTC)

    def _set_reset_boundaries(state):
        state.agent_runtimes["backend"].last_context_rotated_at = reset_at
        state.agent_runtimes["lead"].last_context_rotated_at = reset_at
        state.agent_runtimes["manager"] = AgentRuntime(agent_id="manager")
        return state

    repo.update(_set_reset_boundaries)
    _team_admin_service(repo).update_agent(
        agent_id="lead",
        context_clearing_policy=AgentContextClearingPolicy.IDLE,
        set_context_clearing_policy=True,
    )
    usage_store = ModelUsageStore(runtime_dir / "model-usage.db")
    for event_id, occurred_at, session_id, input_tokens in (
        ("before-reset", reset_at - timedelta(seconds=1), "session-backend", 3),
        ("at-reset", reset_at, "session-backend", 5),
        ("after-reset-1", reset_at + timedelta(seconds=1), "session-backend", 7),
        ("after-reset-2", reset_at + timedelta(minutes=1), "session-backend", 9),
        ("unavailable-usage", reset_at + timedelta(minutes=2), "session-backend", None),
        ("other-session", reset_at + timedelta(seconds=1), "session-manager", 99),
        ("lead-after-reset", reset_at + timedelta(seconds=1), "session-lead", 11),
    ):
        usage_store.upsert_event(
            ModelInferenceUsageEvent(
                event_id=event_id,
                occurred_at=occurred_at,
                provider="openai",
                provider_endpoint_identifier="responses",
                input_tokens=input_tokens,
                request_status="success",
                normalized_usage_status=("available" if input_tokens is not None else "usage_unavailable"),
                raw_usage_metadata={},
                session_id=session_id,
            )
        )

    client = _client(tmp_path)

    response = client.get("/api/addons/team-addon/state")

    assert response.status_code == 200
    runtimes = response.json()["agent_runtimes"]
    assert runtimes["backend"]["last_context_rotated_at"] == "2026-06-16T12:00:00Z"
    assert runtimes["backend"]["context_reset_usage"] == {
        "input_tokens_since_reset": 16,
    }
    assert runtimes["lead"]["context_reset_usage"] == {
        "input_tokens_since_reset": 11,
    }
    assert runtimes["manager"]["context_reset_usage"] == {
        "input_tokens_since_reset": None,
    }

    people_response = client.get("/api/addons/team-addon/views/people")

    assert people_response.status_code == 200
    people_runtime = people_response.json()["data"]["state"]["agent_runtimes"]["backend"]
    assert people_runtime["context_reset_usage"]["input_tokens_since_reset"] == 16


def test_team_addon_state_omits_reset_usage_for_every_wake_agents(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    repo = _seed_runtime_operations_state(runtime_dir)
    reset_at = datetime(2026, 6, 16, 12, 0, tzinfo=UTC)

    def _set_reset_boundary(state):
        state.agent_runtimes["backend"].last_context_rotated_at = reset_at
        return state

    repo.update(_set_reset_boundary)
    _team_admin_service(repo).update_agent(
        agent_id="backend",
        context_clearing_policy=AgentContextClearingPolicy.EVERY_WAKE,
        set_context_clearing_policy=True,
    )
    ModelUsageStore(runtime_dir / "model-usage.db").upsert_event(
        ModelInferenceUsageEvent(
            event_id="after-reset",
            occurred_at=reset_at + timedelta(seconds=1),
            provider="openai",
            provider_endpoint_identifier="responses",
            input_tokens=7,
            request_status="success",
            normalized_usage_status="available",
            raw_usage_metadata={},
            session_id="session-backend",
        )
    )

    response = _client(tmp_path).get("/api/addons/team-addon/state")

    assert response.status_code == 200
    assert "context_reset_usage" not in response.json()["agent_runtimes"]["backend"]


def test_team_addon_admin_api_manual_context_rotation_request(tmp_path: Path) -> None:
    client = _client(tmp_path)
    runtime_dir = tmp_path / "runtime"
    repo = TeamStateRepository(runtime_dir)
    _team_admin_service(repo).create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
    )
    _team_admin_service(repo).create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
    )
    _team_admin_service(repo).create_role(
        role_id="project__backend",
        team_id="team",
        project_id="project",
        name="Backend",
    )
    _team_admin_service(repo).register_agent(
        agent_id="backend",
        team_id="team",
        project_id="project",
        role_id="project__backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
    )
    _team_admin_service(repo).create_or_reprioritize_task(
        task_id="active-work",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Active work",
        description="Manual context clearing should still queue.",
        created_by_agent_id="lead",
    )

    response = client.post(
        "/api/addons/team-addon/agents/backend/context-rotation",
        json={
            "reason": "controlled smoke",
            "requested_by": "operator:test",
            "expected_revision": 5,
        },
    )

    assert response.status_code == 200
    runtime = response.json()["agent_runtimes"]["backend"]
    assert runtime["auto_context_clearing_enabled"] is False
    assert runtime["context_rotation_pending"] is True
    assert runtime["context_rotation_reason"] == "manual"
    assert runtime["context_rotation_requested_by"] == "operator:test"
    assert runtime["context_rotation_request_note"] == "controlled smoke"
    assert "controlled smoke" in runtime["last_handoff_summary"]
    assert "active-work" not in runtime["last_handoff_summary"]


def test_team_addon_admin_api_manual_context_rotation_rejects_archived_agent(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    runtime_dir = tmp_path / "runtime"
    repo = TeamStateRepository(runtime_dir)
    _team_admin_service(repo).create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
    )
    _team_admin_service(repo).create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
    )
    _team_admin_service(repo).create_role(
        role_id="project__backend",
        team_id="team",
        project_id="project",
        name="Backend",
    )
    _team_admin_service(repo).register_agent(
        agent_id="backend",
        team_id="team",
        project_id="project",
        role_id="project__backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
    )
    _team_admin_service(repo).update_agent(agent_id="backend", status=AgentStatus.ARCHIVED)

    response = client.post(
        "/api/addons/team-addon/agents/backend/context-rotation",
        json={"expected_revision": 5},
    )

    assert response.status_code == 400
    assert "archived" in response.json()["error"]


def test_team_addon_admin_api_operator_stand_down_and_revoke(tmp_path: Path) -> None:
    client = _client(tmp_path)
    runtime_dir = tmp_path / "runtime"
    repo = TeamStateRepository(runtime_dir)
    _team_admin_service(repo).create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
    )
    _team_admin_service(repo).create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
    )
    _team_admin_service(repo).create_role(
        role_id="project__backend",
        team_id="team",
        project_id="project",
        name="Backend",
    )
    _team_admin_service(repo).register_agent(
        agent_id="backend",
        team_id="team",
        project_id="project",
        role_id="project__backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
    )

    state = _team_runtime_service(repo).stand_down_agent(
        agent_id="backend",
        reason="Wait for the lead to finish upstream work.",
        set_by="lead",
        source=AgentStandDownSource.SUPERIOR,
        scope="agent",
        expected_revision=4,
    )

    response = client.post(
        "/api/addons/team-addon/agents/backend/stand-down/revoke",
        json={"expected_revision": state.revision},
    )

    assert response.status_code == 200
    body = response.json()
    agent_stand_down = next(iter(body["stand_downs"].values()))
    assert agent_stand_down["source"] == "superior"
    assert agent_stand_down["cleared_at"] is not None
    assert body["agents"]["backend"]["stand_down_active"] is False

    response = client.post(
        "/api/addons/team-addon/agents/lead/stand-down",
        json={"reason": "Await operator follow-up.", "expected_revision": 6},
    )

    assert response.status_code == 201
    body = response.json()
    assert len(body["stand_downs"]) == 2
    stand_down = next(
        item
        for item in body["stand_downs"].values()
        if item["agent_id"] == "lead" and item["cleared_at"] is None
    )
    assert stand_down["agent_id"] == "lead"
    assert stand_down["scope"] == "subtree"
    assert stand_down["source"] == "operator"
    assert body["agents"]["lead"]["stand_down_active"] is True
    assert body["agents"]["backend"]["stand_down_active"] is True
    assert body["agents"]["backend"]["effective_stand_downs"][0]["inherited"] is True

    response = client.post(
        "/api/addons/team-addon/agents/lead/stand-down/revoke",
        json={"expected_revision": 7},
    )

    assert response.status_code == 200
    body = response.json()
    stand_down = next(
        item for item in body["stand_downs"].values() if item["agent_id"] == "lead"
    )
    assert stand_down["cleared_at"] is not None
    assert body["agents"]["lead"]["stand_down_active"] is False
    assert body["agents"]["backend"]["stand_down_active"] is False

    response = client.post(
        "/api/addons/team-addon/agents/lead/stand-down",
        json={
            "reason": "Pause only the lead.",
            "scope": "agent",
            "expected_revision": 8,
        },
    )

    assert response.status_code == 201
    body = response.json()
    active_stand_down = next(
        stand_down
        for stand_down in body["stand_downs"].values()
        if stand_down["cleared_at"] is None
    )
    assert active_stand_down["scope"] == "agent"
    assert body["agents"]["lead"]["stand_down_active"] is True
    assert body["agents"]["backend"]["stand_down_active"] is False


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
    assert body["teams"]["team-auto-dev-squad"]["project_ids"] == ["project-auto-dev-squad"]
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


def test_team_addon_admin_api_materializes_accounting_department(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/api/addons/team-addon/templates/accounting-department/materialize",
        json=_accounting_department_materialization_payload(),
    )
    assert response.status_code == 201
    body = response.json()
    materialized = body["materialized"]

    assert materialized["success"] is True
    assert materialized["template_id"] == "accounting-department"
    assert materialized["template_name"] == "Accounting Department"
    assert materialized["revision"] == 1
    assert body["revision"] == 1
    assert set(materialized["agent_ids"]) == {
        "manager",
        "project_lead",
        "bookkeeping_clerk",
        "invoice_reviewer",
        "payment_preparer",
        "tax_compliance_reviewer",
    }
    assert len(body["agents"]) == 6
    assert len(body["projects"]) == 1
    assert len(body["review_profiles"]) == 1
    assert len(body["tasks"]) == 5

    project = body["projects"][materialized["project_id"]]
    assert project["git"] is None
    assert project["lead_agent_id"] == materialized["agent_ids"]["project_lead"]
    assert project["goals"][0]["id"] == materialized["goal_ids"][0]

    roles = body["roles"]
    assert roles[materialized["role_ids"]["payment_preparer"]]["permissions"] == [
        "prepare_bookkeeping_action",
        "request_payment_preparation",
    ]
    assert roles[materialized["role_ids"]["tax_compliance_reviewer"]]["permissions"] == [
        "review_bookkeeping_action"
    ]

    assert body["teams"][materialized["team_id"]]["project_ids"] == [materialized["project_id"]]
    review_profile = body["review_profiles"][materialized["review_profile_ids"][0]]
    assert review_profile["applicable_role_ids"] == [
        materialized["role_ids"]["invoice_reviewer"],
        materialized["role_ids"]["tax_compliance_reviewer"],
    ]
    assert [criterion["id"] for criterion in review_profile["criteria"]] == [
        "supporting_documentation",
        "bokio_mapping",
        "tax_treatment",
        "approval_boundary",
    ]
    assert (
        body["tasks"][f"{materialized['project_id']}-prepare-bank-payments"]["owner_agent_id"]
        == materialized["agent_ids"]["payment_preparer"]
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
            "expected_revision": 0,
        },
    )
    assert response.status_code == 201
    assert response.json()["teams"]["team"]["operator_communication_target_id"] == "ops-channel"
    assert response.json()["teams"]["team"]["max_cost_usd"] == 10.5

    response = client.patch(
        "/api/addons/team-addon/teams/team",
        json={
            "operator_communication_target_id": "ops-channel-2",
            "max_cost_usd": 12.25,
            "expected_revision": 1,
        },
    )
    assert response.status_code == 200
    assert response.json()["teams"]["team"]["operator_communication_target_id"] == "ops-channel-2"
    assert response.json()["teams"]["team"]["max_cost_usd"] == 12.25

    response = client.patch(
        "/api/addons/team-addon/teams/team",
        json={"max_cost_usd": None, "expected_revision": 2},
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
            "git": {
                "managed_repo_id": "mr_0123456789abcdefabcd",
                "agent_author_identity": {
                    "name": "Jonathan Quinth",
                    "email": "operator@example.invalid",
                },
                "agent_committer_identity": {
                    "name": "Evander Bot",
                    "email": "evander@quinth.se",
                },
            },
            "release_policy": {
                "allow_ungated": True,
                "resource_limits": {
                    "memory": "6g",
                    "cpus": 4,
                    "pids_limit": 1536,
                },
            },
            "expected_revision": 3,
        },
    )
    assert response.status_code == 201
    assert response.json()["projects"]["project"]["git"]["managed_repo_id"] == (
        "mr_0123456789abcdefabcd"
    )
    assert response.json()["projects"]["project"]["git"]["agent_author_identity"] == {
        "name": "Jonathan Quinth",
        "email": "operator@example.invalid",
    }
    assert response.json()["projects"]["project"]["release_policy"]["resource_limits"] == {
        "memory": "6g",
        "cpus": 4.0,
        "pids_limit": 1536,
    }

    response = client.patch(
        "/api/addons/team-addon/projects/project",
        json={
            "git": {
                "managed_repo_id": "mr_0123456789abcdefabcd",
                "base_branch": "develop",
                "agent_author_identity": {
                    "name": "Jonathan Quinth",
                    "email": "operator@example.invalid",
                },
                "agent_committer_identity": {
                    "name": "Evander Bot",
                    "email": "evander@quinth.se",
                },
            },
            "expected_revision": 4,
        },
    )
    assert response.status_code == 200
    git = response.json()["projects"]["project"]["git"]
    assert git["managed_repo_id"] == "mr_0123456789abcdefabcd"
    assert git["base_branch"] == "develop"
    assert git["agent_committer_identity"] == {
        "name": "Evander Bot",
        "email": "evander@quinth.se",
    }
    assert response.json()["projects"]["project"]["release_policy"]["resource_limits"] == {
        "memory": "6g",
        "cpus": 4.0,
        "pids_limit": 1536,
    }

    response = client.post(
        "/api/addons/team-addon/tasks",
        json={
            "id": "task",
            "owner_agent_id": "lead",
            "project_id": "project",
            "priority": 1,
            "title": "Snapshot the default",
            "description": "Exercise task Git-base API projection.",
            "created_by_agent_id": "lead",
            "expected_revision": 5,
        },
    )
    assert response.status_code == 201
    assert response.json()["tasks"]["task"]["git_base_branch"] == "develop"

    response = client.patch(
        "/api/addons/team-addon/tasks/task",
        json={
            "git_base_branch": "feature/foo",
            "expected_revision": 6,
        },
    )
    assert response.status_code == 200
    assert response.json()["tasks"]["task"]["git_base_branch"] == "feature/foo"

    response = client.post(
        "/api/addons/team-addon/projects",
        json={
            "id": "project-2",
            "team_id": "team",
            "name": "Project 2",
            "lead_agent_id": "lead-2",
            "lead_session_id": "session-lead-2",
            "git": {
                "managed_repo_id": "legacy-repo",
            },
            "expected_revision": 7,
        },
    )
    assert response.status_code == 400
    assert "managed_repo_id must match mr_<20-hex>" in response.json()["error"]


def test_team_addon_admin_api_imports_project_git_repo(tmp_path: Path) -> None:
    source_repo = tmp_path / "source-repo"
    source_repo.mkdir()
    _git(source_repo, "init", "-q", "-b", "main")
    _git(source_repo, "config", "user.email", "team@example.invalid")
    _git(source_repo, "config", "user.name", "Team Test")
    (source_repo / "README.md").write_text("# Source\n", encoding="utf-8")
    _git(source_repo, "add", "README.md")
    _git(source_repo, "commit", "-q", "-m", "initial")
    source_commit = _git(source_repo, "rev-parse", "refs/heads/main").stdout.strip()

    config_path = _write_config(
        tmp_path,
        enable_team_addon=True,
        enable_directory_scoped_config=True,
    )
    service = build_admin_runtime(config_path)
    service.directory_admin.create_child(parent="root", name="team-a")
    service.project_dir = PROJECT_ROOT
    service.vm_agent_support.project_dir = PROJECT_ROOT
    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)

    response = client.post(
        "/api/addons/team-addon/projects/git/import",
        json={
            "repo_source": str(source_repo),
            "directory_path": "root/team-a",
        },
    )

    assert response.status_code == 201
    body = response.json()
    imported = body["imported"]
    managed_repo_id = imported["managed_repo_id"]
    bare_repo, _managed_root = service.managed_git_admin.resolve_internal_repository(
        managed_repo_id,
        "root/team-a",
    )
    assert managed_repo_id.startswith("mr_")
    assert imported["canonical_repository_url"] == str(source_repo.resolve())
    assert imported["default_branch"] == "main"
    assert body["git"] == {
        "managed_repo_id": managed_repo_id,
        "directory_path": "root/team-a",
        "base_branch": "main",
    }
    created_team = client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
            "expected_revision": 0,
        },
    )
    assert created_team.status_code == 201
    created_project = client.post(
        "/api/addons/team-addon/projects",
        json={
            "id": "project",
            "team_id": "team",
            "name": "Project",
            "lead_agent_id": "lead",
            "lead_session_id": "session-lead",
            "git": body["git"],
            "expected_revision": 1,
        },
    )
    assert created_project.status_code == 201
    assert created_project.json()["projects"]["project"]["git"]["directory_path"] == ("root/team-a")
    assert service.managed_git_admin.get_repository(managed_repo_id)["bindings_by_kind"] == {
        "team_projects": ["project"]
    }
    assert _git_dir(bare_repo, "rev-parse", "refs/heads/main").stdout.strip() == source_commit
    assert _git_dir(bare_repo, "config", "--get", "evander.managedRepoId").stdout.strip() == (
        managed_repo_id
    )
    (source_repo / "README.md").write_text("# Source\n\nRefresh\n", encoding="utf-8")
    _git(source_repo, "add", "README.md")
    _git(source_repo, "commit", "-q", "-m", "refresh")
    refreshed_commit = _git(source_repo, "rev-parse", "refs/heads/main").stdout.strip()

    refresh_response = client.post(
        "/api/addons/team-addon/projects/git/import",
        json={
            "repo_source": str(source_repo),
            "directory_path": "root/team-a",
        },
    )

    assert refresh_response.status_code == 201
    assert refresh_response.json()["imported"]["managed_repo_id"] == managed_repo_id
    assert _git_dir(bare_repo, "rev-parse", "refs/heads/main").stdout.strip() == refreshed_commit

    _git_dir(bare_repo, "update-ref", "refs/heads/agents/session-1/work", refreshed_commit)

    preserved_response = client.post(
        "/api/addons/team-addon/projects/git/import",
        json={
            "repo_source": str(source_repo),
            "directory_path": "root/team-a",
        },
    )

    assert preserved_response.status_code == 201
    assert (
        _git_dir(bare_repo, "rev-parse", "refs/heads/agents/session-1/work").stdout.strip()
        == refreshed_commit
    )


def test_team_addon_admin_api_imports_project_git_repo_in_threadpool(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = _write_config(tmp_path, enable_team_addon=True)
    service = build_admin_runtime(config_path)
    calls: dict[str, object] = {}

    def fake_register_repository(
        *,
        repo_source: str,
        directory_path: str = "root",
    ) -> dict[str, object]:
        calls["import"] = {
            "repo_source": repo_source,
            "directory_path": directory_path,
        }
        return {
            "managed_repo_id": "mr_0123456789abcdefabcd",
            "directory_path": directory_path,
            "default_branch": "main",
        }

    async def fake_run_in_threadpool(func, *args, **kwargs):
        calls["threadpool"] = True
        return func(*args, **kwargs)

    monkeypatch.setattr(
        service.managed_git_admin,
        "register_repository",
        fake_register_repository,
    )
    monkeypatch.setattr(team_addon_api, "run_in_threadpool", fake_run_in_threadpool)
    client = TestClient(create_app(service))
    _unlock_privileged_operations(client)

    response = client.post(
        "/api/addons/team-addon/projects/git/import",
        json={
            "repo_source": "/srv/repos/project",
            "directory_path": "root",
        },
    )

    assert response.status_code == 201
    assert calls["threadpool"] is True
    assert calls["import"] == {
        "repo_source": "/srv/repos/project",
        "directory_path": "root",
    }
    assert response.json()["git"]["directory_path"] == "root"


def test_team_addon_admin_api_creates_operator_directive(tmp_path: Path) -> None:
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

    response = client.post(
        "/api/addons/team-addon/operator-directives",
        json={
            "team_id": "team",
            "project_id": "project",
            "title": "Status report",
            "message": "Ask the lead for a status report.",
            "source": "discord",
            "source_message_id": "discord-msg-1",
            "expected_revision": 2,
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
    assert len(body["published_event_ids"]) == 1
    scheduled = ScheduledInternalEventStore(
        scheduled_internal_event_store_path(tmp_path / "runtime")
    ).list_due(datetime.now(UTC) + timedelta(minutes=1))
    assert len(scheduled) == 1
    assert scheduled[0].schedule_id == body["published_event_ids"][0]
    assert scheduled[0].event_type == "team.agent.wake_requested"
    assert scheduled[0].payload["agent_id"] == "manager"
    assert scheduled[0].payload["reason"] == "inbox"


def test_team_addon_admin_api_reassigns_team_manager(tmp_path: Path) -> None:
    client = _client(tmp_path)
    create_team = client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
            "expected_revision": 0,
        },
    )
    assert create_team.status_code == 201
    create_project = client.post(
        "/api/addons/team-addon/projects",
        json={
            "id": "project",
            "team_id": "team",
            "name": "Project",
            "lead_agent_id": "lead",
            "lead_session_id": "session-lead",
            "expected_revision": create_team.json()["revision"],
        },
    )
    assert create_project.status_code == 201

    response = client.patch(
        "/api/addons/team-addon/teams/team",
        json={
            "manager_agent_id": "lead",
            "expected_revision": create_project.json()["revision"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["teams"]["team"]["manager_agent_id"] == "lead"
    assert body["agents"]["lead"]["reports_to_agent_id"] is None
    assert body["agents"]["manager"]["subordinate_agent_ids"] == []

    directive = client.post(
        "/api/addons/team-addon/operator-directives",
        json={
            "team_id": "team",
            "message": "Check project state.",
            "expected_revision": body["revision"],
        },
    )
    assert directive.status_code == 201
    assert directive.json()["operator_directive"]["recipient_agent_id"] == "lead"


def test_team_addon_admin_api_task_graph_fields(tmp_path: Path) -> None:
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
        "/api/addons/team-addon/projects",
        json={
            "id": "other-project",
            "team_id": "team",
            "name": "Other Project",
            "lead_agent_id": "other-lead",
            "lead_session_id": "session-other-lead",
            "expected_revision": 2,
        },
    )
    _post_backend_role(client, expected_revision=3)
    client.post(
        "/api/addons/team-addon/agents",
        json={
            "id": "backend",
            "team_id": "team",
            "project_id": "project",
            "role_id": "project__backend",
            "session_id": "session-backend",
            "reports_to_agent_id": "lead",
            "expected_revision": 4,
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
                "expected_revision": 5,
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
                "expected_revision": 6,
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
            "expected_revision": 7,
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
        json={"dependency_task_ids": [], "expected_revision": 8},
    )
    assert response.status_code == 200
    child = response.json()["tasks"]["child"]
    assert child["dependency_task_ids"] == []
    assert child["dependency_status"] == "satisfied"

    # PATCH can clear a parent link with explicit null.
    response = client.patch(
        "/api/addons/team-addon/tasks/child",
        json={"parent_task_id": None, "expected_revision": 9},
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
            "expected_revision": 10,
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
            "expected_revision": 11,
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
            "expected_revision": 11,
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
            "goals": [{"id": "goal", "description": "Ship reviewed work"}],
            "git": {"managed_repo_id": "mr_0123456789abcdefabcd"},
            "expected_revision": 1,
        },
    )
    _post_backend_role(client, expected_revision=2)
    client.post(
        "/api/addons/team-addon/agents",
        json={
            "id": "backend",
            "team_id": "team",
            "project_id": "project",
            "role_id": "project__backend",
            "session_id": "session-backend",
            "reports_to_agent_id": "lead",
            "expected_revision": 3,
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
            "expected_revision": 4,
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
            "expected_revision": 5,
        },
    )
    client.post(
        "/api/addons/team-addon/review-requests",
        json={
            "id": "review",
            "deliverable_id": "deliverable",
            "reviewer_agent_id": "lead",
            "expected_revision": 6,
        },
    )

    response = client.post(
        "/api/addons/team-addon/review-requests/review/decision",
        json={"state": "accepted", "findings": ["looks good"], "expected_revision": 7},
    )
    assert response.status_code == 200, response.text
    state = response.json()
    assert state["review_requests"]["review"]["state"] == "accepted"
    assert state["review_requests"]["review"]["findings"] == ["looks good"]
    assert state["review_requests"]["review"]["decision_at"] is not None
    assert state["deliverables"]["deliverable"]["state"] == "accepted"
    assert state["tasks"]["task"]["state"] == "finished"

    repo = TeamStateRepository(tmp_path / "runtime")

    def _attach_feature_branch(current):
        deliverable = current.deliverables["deliverable"]
        deliverable.verification = DeliverableVerification(status="passed")
        deliverable.git_refs.commit_sha = "reviewed-commit"
        deliverable.git_refs.feature_branch = "team/team/features/task-backend"
        deliverable.git_refs.feature_commit_sha = "reviewed-commit"
        deliverable.review_manifest = DeliverableReviewManifest(
            base_commit_sha="base-commit",
            reviewed_commit_sha="reviewed-commit",
            tree_hash="reviewed-tree",
            commit_shas=["reviewed-commit"],
            changed_paths=["src/backend.py"],
        )
        current.review_requests["review"].reviewed_commit_sha = "reviewed-commit"
        current.projects["project"].release_policy = ProjectReleasePolicy(allow_ungated=True)
        return current

    repo.update(_attach_feature_branch)

    response = client.post(
        "/api/addons/team-addon/release-packages",
        json={
            "id": "release",
            "project_id": "project",
            "deliverable_ids": ["deliverable"],
            "summary": "Publish the reviewed backend delivery.",
            "expected_revision": 9,
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["release_packages"]["release"]["deliverable_ids"] == ["deliverable"]
    assert (
        payload["release_packages"]["release"]["summary"]
        == "Publish the reviewed backend delivery."
    )
    assert len(payload["published_event_ids"]) == 1
    events = InternalEventBus(internal_event_store_path(tmp_path / "runtime")).list_events()
    assert len(events) == 1
    assert events[0].event_type == "team.git.promotion_requested"
    assert events[0].payload["promotion_kind"] == "release"
    assert events[0].payload["target_branch"] == "team/team/releases/release"

    response = client.get("/api/addons/team-addon/release-packages")
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["release_packages"]] == ["release"]
    assert (
        response.json()["release_packages"][0]["summary"]
        == "Publish the reviewed backend delivery."
    )

    response = client.get("/api/addons/team-addon/release-packages/release")
    assert response.status_code == 200
    assert response.json()["project_id"] == "project"
    assert response.json()["summary"] == "Publish the reviewed backend delivery."

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
    _post_backend_role(client, expected_revision=2)
    client.post(
        "/api/addons/team-addon/agents",
        json={
            "id": "backend",
            "team_id": "team",
            "project_id": "project",
            "role_id": "project__backend",
            "session_id": "session-backend",
            "reports_to_agent_id": "lead",
            "expected_revision": 3,
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
            "expected_revision": 4,
        },
    )
    client.post(
        "/api/addons/team-addon/deliverables",
        json={
            "id": "deliverable",
            "task_id": "task",
            "producer_agent_id": "backend",
            "summary": "Backend task is ready.",
            "expected_revision": 5,
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
            "applicable_role_ids": ["project__project-lead", "project__backend"],
            "criteria": [
                {"id": "authz", "label": "Authorization", "required": True},
                {
                    "id": "logging",
                    "label": "Logging",
                    "description": "No secrets",
                    "required": False,
                },
            ],
            "expected_revision": 6,
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
        json={"name": "Security review v2", "archived": True, "expected_revision": 7},
    )
    assert response.status_code == 200
    response = client.get("/api/addons/team-addon/review-profiles/profile")
    profile = response.json()
    assert profile["name"] == "Security review v2"
    assert profile["archived"] is True

    # Empty strings are meaningful on edit: they clear the optional description.
    response = client.patch(
        "/api/addons/team-addon/review-profiles/profile",
        json={"description": "", "expected_revision": 8},
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
            "expected_revision": 9,
        },
    )
    assert response.status_code == 400
    assert "archived" in response.json()["error"]

    # Un-archive so the profile can be attached to a review request.
    client.patch(
        "/api/addons/team-addon/review-profiles/profile",
        json={"archived": False, "expected_revision": 9},
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
            "expected_revision": 10,
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
            "expected_revision": 11,
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
    _post_backend_role(client, expected_revision=2)
    client.post(
        "/api/addons/team-addon/agents",
        json={
            "id": "backend",
            "team_id": "team",
            "project_id": "project",
            "role_id": "project__backend",
            "session_id": "session-backend",
            "reports_to_agent_id": "lead",
            "expected_revision": 3,
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
            "expected_revision": 4,
        },
    )
    client.post(
        "/api/addons/team-addon/deliverables",
        json={
            "id": "deliverable",
            "task_id": "task",
            "producer_agent_id": "backend",
            "summary": "Backend task is ready.",
            "expected_revision": 5,
        },
    )

    # Regression: review request without a profile still succeeds.
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
    assert response.json()["review_requests"]["review"]["review_profile_id"] is None

    # Unknown profile id on a review request returns 400.
    response = client.post(
        "/api/addons/team-addon/review-requests",
        json={
            "id": "review-2",
            "deliverable_id": "deliverable",
            "reviewer_agent_id": "lead",
            "review_profile_id": "missing-profile",
            "expected_revision": 7,
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
            "expected_revision": 0,
        },
    )

    response = client.patch(
        "/api/addons/team-addon/teams/team",
        json={"operator_communication_target_id": "ops", "expected_revision": 0},
    )

    assert response.status_code == 409
    assert response.json()["current_revision"] == 1


def test_team_addon_admin_api_exposes_resource_tokens(tmp_path: Path) -> None:
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

    state = client.get("/api/addons/team-addon/state").json()

    token = state["resource_tokens"]["teams"]["team"]
    assert token.startswith("sha256:")
    assert state["teams"]["team"]["resource_token"] == token


def test_team_addon_mutation_can_return_scoped_view_envelope(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/api/addons/team-addon/teams",
        headers={"X-Team-Addon-Response-Scope": "view"},
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
            "expected_revision": 0,
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["response_scope"] == "view"
    assert payload["revision"] == 1
    assert "people" in payload["invalidated_views"]
    assert "teams" not in payload
    assert "runtime_operations" not in payload


def _seed_rebase_backend_agent(client: TestClient) -> dict:
    team = client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
            "expected_revision": 0,
        },
    )
    assert team.status_code == 201
    project = client.post(
        "/api/addons/team-addon/projects",
        json={
            "id": "project",
            "team_id": "team",
            "name": "Project",
            "lead_agent_id": "lead",
            "lead_session_id": "session-lead",
            "expected_revision": team.json()["revision"],
        },
    )
    assert project.status_code == 201
    role = client.post(
        "/api/addons/team-addon/roles",
        json={
            "id": "team__backend",
            "team_id": "team",
            "name": "Backend",
            "expected_revision": project.json()["revision"],
        },
    )
    assert role.status_code == 201
    agent = client.post(
        "/api/addons/team-addon/agents",
        json={
            "id": "backend",
            "team_id": "team",
            "project_id": "project",
            "role_id": "team__backend",
            "session_id": "session-backend",
            "reports_to_agent_id": "lead",
            "expected_revision": role.json()["revision"],
        },
    )
    assert agent.status_code == 201
    return agent.json()


def test_team_addon_admin_api_rebases_stale_create_when_target_is_new(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    team = client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
            "expected_revision": 0,
        },
    ).json()
    stale_revision = team["revision"]
    bump = client.post(
        "/api/addons/team-addon/roles",
        json={
            "id": "team__reviewer",
            "team_id": "team",
            "name": "Reviewer",
            "expected_revision": stale_revision,
        },
    )
    assert bump.status_code == 201

    response = client.post(
        "/api/addons/team-addon/projects",
        json={
            "id": "project",
            "team_id": "team",
            "name": "Project",
            "lead_agent_id": "lead",
            "lead_session_id": "session-lead",
            "expected_revision": stale_revision,
            "conflict_policy": "target_unchanged",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["rebased_from_revision"] == stale_revision
    assert payload["projects"]["project"]["name"] == "Project"


def test_team_addon_admin_api_rejects_stale_no_id_create(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    team = client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
            "expected_revision": 0,
        },
    ).json()
    stale_revision = team["revision"]
    bump = client.post(
        "/api/addons/team-addon/roles",
        json={
            "id": "team__reviewer",
            "team_id": "team",
            "name": "Reviewer",
            "expected_revision": stale_revision,
        },
    )
    assert bump.status_code == 201

    response = client.post(
        "/api/addons/team-addon/projects",
        json={
            "team_id": "team",
            "name": "Project",
            "lead_agent_id": "lead",
            "lead_session_id": "session-lead",
            "expected_revision": stale_revision,
            "conflict_policy": "target_unchanged",
        },
    )

    assert response.status_code == 409
    assert response.json()["current_revision"] == bump.json()["revision"]


def test_team_addon_admin_api_rebases_stale_patch_when_target_token_matches(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    team = client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
            "expected_revision": 0,
        },
    ).json()
    stale_revision = team["revision"]
    base_token = team["resource_tokens"]["teams"]["team"]
    bump = client.post(
        "/api/addons/team-addon/roles",
        json={
            "id": "team__reviewer",
            "team_id": "team",
            "name": "Reviewer",
            "expected_revision": stale_revision,
        },
    )
    assert bump.status_code == 201

    response = client.patch(
        "/api/addons/team-addon/teams/team",
        json={
            "operator_communication_target_id": "ops",
            "expected_revision": stale_revision,
            "conflict_policy": "target_unchanged",
            "base_resource_token": base_token,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["rebased_from_revision"] == stale_revision
    assert payload["teams"]["team"]["operator_communication_target_id"] == "ops"


def test_team_addon_admin_api_rejects_stale_patch_when_target_changed(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    team = client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
            "expected_revision": 0,
        },
    ).json()
    stale_revision = team["revision"]
    base_token = team["resource_tokens"]["teams"]["team"]
    changed = client.patch(
        "/api/addons/team-addon/teams/team",
        json={"name": "Changed", "expected_revision": stale_revision},
    )
    assert changed.status_code == 200

    response = client.patch(
        "/api/addons/team-addon/teams/team",
        json={
            "operator_communication_target_id": "ops",
            "expected_revision": stale_revision,
            "conflict_policy": "target_unchanged",
            "base_resource_token": base_token,
        },
    )

    assert response.status_code == 409
    assert response.json()["current_revision"] == changed.json()["revision"]


def test_team_addon_admin_api_rejects_stale_agent_patch_when_assignment_changed(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    state = _seed_rebase_backend_agent(client)
    stale_revision = state["revision"]
    base_token = state["resource_tokens"]["agents"]["backend"]
    other_project = client.post(
        "/api/addons/team-addon/projects",
        json={
            "id": "project-2",
            "team_id": "team",
            "name": "Project 2",
            "lead_agent_id": "lead-2",
            "lead_session_id": "session-lead-2",
            "expected_revision": stale_revision,
        },
    )
    assert other_project.status_code == 201
    changed = client.patch(
        "/api/addons/team-addon/agents/backend",
        json={
            "project_id": "project-2",
            "expected_revision": other_project.json()["revision"],
        },
    )
    assert changed.status_code == 200
    assert changed.json()["resource_tokens"]["agents"]["backend"] != base_token

    response = client.patch(
        "/api/addons/team-addon/agents/backend",
        json={
            "display_name": "Backend 2",
            "expected_revision": stale_revision,
            "conflict_policy": "target_unchanged",
            "base_resource_token": base_token,
        },
    )

    assert response.status_code == 409
    assert response.json()["current_revision"] == changed.json()["revision"]


def test_team_addon_admin_api_rejects_stale_agent_patch_when_runtime_changed(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    state = _seed_rebase_backend_agent(client)
    stale_revision = state["revision"]
    base_token = state["resource_tokens"]["agents"]["backend"]
    changed = client.patch(
        "/api/addons/team-addon/agents/backend",
        json={
            "context_clearing_policy": "idle",
            "expected_revision": stale_revision,
        },
    )
    assert changed.status_code == 200
    assert changed.json()["resource_tokens"]["agents"]["backend"] != base_token

    response = client.patch(
        "/api/addons/team-addon/agents/backend",
        json={
            "display_name": "Backend 2",
            "expected_revision": stale_revision,
            "conflict_policy": "target_unchanged",
            "base_resource_token": base_token,
        },
    )

    assert response.status_code == 409
    assert response.json()["current_revision"] == changed.json()["revision"]


def test_team_addon_admin_api_rejects_stale_stand_down_revoke_when_hold_changed(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    runtime_dir = tmp_path / "runtime"
    repo = TeamStateRepository(runtime_dir)
    seeded = _seed_rebase_backend_agent(client)
    _team_admin_service(repo).create_or_reprioritize_task(
        task_id="backend-task",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Backend work",
        description="Ready work must remain held after a stale revoke.",
        created_by_agent_id="lead",
        expected_revision=seeded["revision"],
    )
    runtime = _team_runtime_service(repo)
    runtime.stand_down_agent(
        agent_id="backend",
        reason="Operator investigation.",
        set_by="operator:admin",
        source=AgentStandDownSource.OPERATOR,
        scope="agent",
        expected_revision=repo.read().revision,
    )
    snapshot = client.get("/api/addons/team-addon/state").json()
    stale_revision = snapshot["revision"]
    base_token = snapshot["resource_tokens"]["agents"]["backend"]

    changed = runtime.stand_down_agent(
        agent_id="backend",
        reason="New superior safety hold.",
        set_by="lead",
        source=AgentStandDownSource.SUPERIOR,
        scope="agent",
        expected_revision=stale_revision,
    )
    current = client.get("/api/addons/team-addon/state").json()
    assert current["resource_tokens"]["agents"]["backend"] != base_token

    response = client.post(
        "/api/addons/team-addon/agents/backend/stand-down/revoke",
        json={
            "expected_revision": stale_revision,
            "conflict_policy": "target_unchanged",
            "base_resource_token": base_token,
        },
    )

    assert response.status_code == 409
    assert response.json()["current_revision"] == changed.revision
    active_holds = [
        stand_down
        for stand_down in repo.read().stand_downs.values()
        if stand_down.agent_id == "backend" and stand_down.active
    ]
    assert len(active_holds) == 2
    assert {stand_down.source for stand_down in active_holds} == {
        AgentStandDownSource.OPERATOR,
        AgentStandDownSource.SUPERIOR,
    }
    assert repo.list_outbox_records() == []
    assert InternalEventBus(internal_event_store_path(runtime_dir)).list_events() == []


def test_team_addon_admin_api_requires_expected_revision(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "expected_revision is required"

    response = client.post(
        "/api/addons/team-addon/teams",
        json={
            "id": "team",
            "name": "Team",
            "manager_agent_id": "manager",
            "manager_session_id": "session-manager",
            "expected_revision": None,
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "expected_revision is required"
    assert client.get("/api/addons/team-addon/state").json()["revision"] == 0


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
