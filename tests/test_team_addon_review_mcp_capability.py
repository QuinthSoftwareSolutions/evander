from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from evander.models import SessionManifest
from evander.repositories.mcp_transport_bindings import JsonMcpTransportBindingRepository
from evander_team_addon.models import (
    AgentStatus,
    AgentInboxItem,
    Deliverable,
    EphemeralAgentPolicy,
    GitWorkContext,
    GitWorkContextState,
    ProjectGoal,
    ReviewRequest,
    TeamBlocker,
    TeamBlockerRoute,
    TeamBlockerState,
    TeamBlockerSourceKind,
    TeamDispatch,
    TeamDispatchMcpCapabilityReceipt,
    TeamDispatchMcpCapabilityStatus,
    TeamDispatchStatus,
    TeamInboxItemType,
    TeamMcpCapabilityBlockerIdentity,
    TeamTaskState,
)
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.services.admin_service import TeamAdminService
from evander_team_addon.services.runtime_service import TeamRuntimeService
from evander_team_addon.services.domain.blockers import open_agent_mcp_capability_blocker
from evander_team_addon.providers import (
    _review_mcp_capability_observation,
    _set_session_team_mcp_tools,
)


def _seed_review_dispatch(runtime_dir: Path) -> TeamStateRepository:
    repo = TeamStateRepository(runtime_dir)
    admin = TeamAdminService(repo)
    admin.create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="session-manager",
        expected_revision=0,
    )
    admin.create_project(
        project_id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
        lead_session_id="session-lead",
        goals=[ProjectGoal(id="goal", description="Ship work")],
        expected_revision=repo.read().revision,
    )
    admin.create_role(
        role_id="project__backend",
        team_id="team",
        project_id="project",
        name="Backend",
        expected_revision=repo.read().revision,
    )
    admin.register_agent(
        agent_id="backend",
        team_id="team",
        project_id="project",
        role_id="project__backend",
        session_id="session-backend",
        reports_to_agent_id="lead",
        ephemeral_policy=EphemeralAgentPolicy(),
        expected_revision=repo.read().revision,
    )
    admin.create_or_reprioritize_task(
        task_id="task",
        owner_agent_id="backend",
        project_id="project",
        priority=1,
        title="Build backend",
        description="Implement backend work.",
        created_by_agent_id="lead",
        expected_revision=repo.read().revision,
    )

    def _seed_review(state):
        state.tasks["task"].state = TeamTaskState.WAITING_FOR_REVIEW
        state.tasks["task"].deliverable_ids = ["deliverable"]
        state.deliverables["deliverable"] = Deliverable(
            id="deliverable",
            task_id="task",
            producer_agent_id="lead",
            summary="Review this work.",
            review_request_ids=["review"],
        )
        state.review_requests["review"] = ReviewRequest(
            id="review",
            deliverable_id="deliverable",
            reviewer_agent_id="backend",
        )
        state.inbox["review-assignment"] = AgentInboxItem(
            id="review-assignment",
            recipient_agent_id="backend",
            item_type=TeamInboxItemType.DELIVERABLE_NOTICE,
            payload={"kind": "review_request", "review_request_id": "review"},
        )
        return state

    repo.update(_seed_review)
    return repo


def _lease_review_dispatch(
    runtime: TeamRuntimeService,
    *,
    now: datetime,
) -> str:
    _state, leases = runtime.lease_due_agents(agent_id="backend", now=now)
    assert len(leases) == 1
    dispatch_id = str(leases[0]["dispatch_id"])
    assert leases[0]["dispatch"]["review_request_ids"] == ["review"]
    receipt = leases[0]["dispatch"]["mcp_capability_receipt"]
    assert receipt is not None, leases[0]["dispatch"]
    assert receipt["required_tool_names"] == ["team_decide_review"]
    assert receipt["verification_status"] == "pending"
    assert receipt["missing_tool_names"] == []
    assert set(receipt) == {
        "required_tool_names",
        "attempt",
        "grant_confirmed_at",
        "verification_status",
        "observed_transport_session_id",
        "observed_at",
        "observed_tool_fingerprint",
        "missing_tool_names",
        "diagnostic",
    }
    runtime.confirm_agent_dispatch_mcp_capability_grant(
        agent_id="backend",
        dispatch_id=dispatch_id,
        granted_tool_names=["team_decide_review"],
        now=now,
    )
    runtime.mark_agent_dispatch_leased(
        agent_id="backend",
        dispatch_id=dispatch_id,
        now=now,
    )
    return dispatch_id


def test_review_capability_columns_migrate_existing_database(
    tmp_path: Path,
) -> None:
    TeamStateRepository(tmp_path).read()
    database_path = tmp_path / "addons" / "team-addon" / "state.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "ALTER TABLE team_dispatches DROP COLUMN mcp_capability_receipt_json"
        )
        connection.execute(
            "ALTER TABLE team_blockers DROP COLUMN mcp_capability_identity_json"
        )

    TeamStateRepository(tmp_path).read()

    with sqlite3.connect(database_path) as connection:
        dispatch_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(team_dispatches)")
        }
    assert "mcp_capability_receipt_json" in dispatch_columns
    with sqlite3.connect(database_path) as connection:
        blocker_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(team_blockers)")
        }
    assert "mcp_capability_identity_json" in blocker_columns


def test_session_tool_grant_requires_persisted_policy_confirmation(tmp_path: Path) -> None:
    class NonPersistingSessionRepository:
        def get_session(self, _session_id: str) -> SessionManifest:
            return SessionManifest(
                session_id="session-backend",
                allowed_tools=[],
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )

        def upsert_session(self, _session: SessionManifest) -> None:
            return None

    result = _set_session_team_mcp_tools(
        SimpleNamespace(
            resolved_config=SimpleNamespace(runtime_dir=tmp_path),
            session_repository=NonPersistingSessionRepository(),
        ),
        session_id="session-backend",
        tool_names=["team_decide_review"],
    )

    assert result["error"] == (
        "Team MCP policy did not retain requested tool(s): team_decide_review"
    )


def test_review_capability_observation_is_absent_without_post_lease_tools_list(
    tmp_path: Path,
) -> None:
    observation = _review_mcp_capability_observation(
        SimpleNamespace(resolved_config=SimpleNamespace(runtime_dir=tmp_path)),
        dispatch=TeamDispatch(
            id="dispatch-review",
            agent_id="backend",
            session_id="session-backend",
            cause_kind="review",
            mcp_capability_receipt=TeamDispatchMcpCapabilityReceipt(
                required_tool_names=["team_decide_review"]
            ),
        ),
    )

    assert observation is None


def test_review_capability_mismatch_retries_once_then_blocks_without_rewaking_review(
    tmp_path: Path,
) -> None:
    repo = _seed_review_dispatch(tmp_path)
    runtime = TeamRuntimeService(repo)
    first_at = datetime(2026, 8, 9, tzinfo=UTC)
    first_dispatch_id = _lease_review_dispatch(runtime, now=first_at)
    first_dispatch = repo.read().dispatches[first_dispatch_id]
    assert first_dispatch.mcp_capability_receipt is not None
    assert first_dispatch.mcp_capability_receipt.attempt == 1
    JsonMcpTransportBindingRepository.from_runtime_dir(tmp_path).record_visible_tools(
        transport_session_id="mcp-session-1",
        bound_session_id="session-backend",
        visible_tool_names=["team_get_task"],
        now=first_at + timedelta(seconds=1),
    )
    first_observation = _review_mcp_capability_observation(
        SimpleNamespace(resolved_config=SimpleNamespace(runtime_dir=tmp_path)),
        dispatch=repo.read().dispatches[first_dispatch_id],
    )
    assert first_observation is not None
    assert first_observation["verification_status"] == "missing"
    assert first_observation["missing_tool_names"] == ["team_decide_review"]
    assert first_observation["observed_transport_session_id"] == "mcp-session-1"
    assert "visible_tool_names" not in first_observation

    first_state, first_result = runtime.finalize_agent_dispatch_from_activity(
        session_id="session-backend",
        dispatch_id=first_dispatch_id,
        mcp_capability_observation=first_observation,
    )

    assert first_result["mcp_capability_retry_scheduled"] is True
    assert first_state.dispatches[first_dispatch_id].status == TeamDispatchStatus.FAILED
    assert first_state.dispatches[first_dispatch_id].mcp_capability_receipt is not None
    assert (
        first_state.dispatches[first_dispatch_id].mcp_capability_receipt.verification_status
        == TeamDispatchMcpCapabilityStatus.MISSING
    )
    assert first_state.agent_runtimes["backend"].no_progress_count == 0

    second_at = first_at + timedelta(seconds=1)
    _state, second_leases = runtime.lease_due_agents(agent_id="backend", now=second_at)
    assert len(second_leases) == 1
    second_dispatch_id = str(second_leases[0]["dispatch_id"])
    assert second_leases[0]["dispatch"]["mcp_capability_receipt"]["attempt"] == 2
    runtime.mark_agent_dispatch_leased(
        agent_id="backend",
        dispatch_id=second_dispatch_id,
        now=second_at,
    )

    second_state, second_result = runtime.finalize_agent_dispatch_from_activity(
        session_id="session-backend",
        dispatch_id=second_dispatch_id,
        mcp_capability_observation={
            "verification_status": "missing",
            "missing_tool_names": ["team_decide_review"],
            "diagnostic": "The authenticated MCP tools/list response did not include the tool.",
        },
    )

    assert second_result["mcp_capability_retry_scheduled"] is False
    assert second_state.agent_runtimes["backend"].no_progress_count == 0
    blockers = [
        blocker
        for blocker in second_state.blockers.values()
        if blocker.source_kind == TeamBlockerSourceKind.MCP_CAPABILITY
    ]
    assert len(blockers) == 1
    assert blockers[0].source_id.startswith("mcp-capability:")
    assert blockers[0].mcp_capability_identity is not None
    assert blockers[0].mcp_capability_identity.agent_id == "backend"
    assert blockers[0].mcp_capability_identity.review_request_ids == ["review"]
    assert blockers[0].reason_code == "required_review_tool_unavailable"
    assert blockers[0].route.value == "routing_failed"
    assert second_state.inbox["review-assignment"].state.value == "pending"

    _state, blocked_review_leases = runtime.lease_due_agents(
        agent_id="backend",
        now=second_at + timedelta(seconds=1),
    )
    assert blocked_review_leases == []


def test_policy_grant_failure_does_not_complete_unstarted_dispatch_scope(
    tmp_path: Path,
) -> None:
    repo = _seed_review_dispatch(tmp_path)
    runtime = TeamRuntimeService(repo)
    failure_at = datetime(2026, 8, 9, 12, tzinfo=UTC)
    _state, leases = runtime.lease_due_agents(
        agent_id="backend",
        now=failure_at,
    )
    assert len(leases) == 1
    dispatch_id = str(leases[0]["dispatch_id"])

    def _seed_unrelated_scope(state):
        state.blockers["blocker-unrelated"] = TeamBlocker(
            id="blocker-unrelated",
            team_id="team",
            task_id="task",
            source_kind=TeamBlockerSourceKind.EXPLICIT,
            source_id="unrelated",
            owner_agent_id="backend",
            affected_task_ids=["task"],
            summary="Unrelated operational blocker",
            active_dispatch_id=dispatch_id,
        )
        state.git_work_contexts["gitctx-unstarted"] = GitWorkContext(
            id="gitctx-unstarted",
            agent_id="backend",
            task_id="task",
            agent_branch="agents/session-backend/task",
            dispatch_id=dispatch_id,
        )
        return state

    repo.update(_seed_unrelated_scope)
    state, result = runtime.record_agent_mcp_capability_preparation_failure(
        session_id="session-backend",
        agent_id="backend",
        dispatch_id=dispatch_id,
        diagnostic="MCP policy grant confirmation failed before resume.",
        now=failure_at + timedelta(seconds=1),
    )

    assert result["pre_execution_failure"] is True
    assert result["mcp_capability_retry_scheduled"] is True
    assert state.dispatches[dispatch_id].status == TeamDispatchStatus.FAILED
    assert state.agent_runtimes["backend"].no_progress_count == 0
    assert state.blockers["blocker-unrelated"].state == TeamBlockerState.OPEN
    assert state.blockers["blocker-unrelated"].owner_agent_id == "backend"
    assert state.blockers["blocker-unrelated"].active_dispatch_id is None
    assert (
        state.git_work_contexts["gitctx-unstarted"].state
        == GitWorkContextState.ACTIVE
    )
    assert state.git_work_contexts["gitctx-unstarted"].dispatch_id == dispatch_id


def test_manager_fallback_does_not_rewake_its_capability_blocked_review(
    tmp_path: Path,
) -> None:
    repo = _seed_review_dispatch(tmp_path)

    def _assign_manager_and_block(state):
        state.review_requests["review"].reviewer_agent_id = "manager"
        state.inbox["review-assignment"].recipient_agent_id = "manager"
        state.agents["backend"].status = AgentStatus.ARCHIVED
        state.agents["lead"].status = AgentStatus.ARCHIVED
        state.blockers["blocker-manager-capability"] = TeamBlocker(
            id="blocker-manager-capability",
            team_id="team",
            source_kind=TeamBlockerSourceKind.MCP_CAPABILITY,
            source_id="mcp-capability:test-manager",
            mcp_capability_identity=TeamMcpCapabilityBlockerIdentity(
                agent_id="manager",
                review_request_ids=["review"],
            ),
            reason_code="required_review_tool_unavailable",
            reporter_agent_id="manager",
            state=TeamBlockerState.OPEN,
            route=TeamBlockerRoute.ROUTING_FAILED,
            summary="Manager cannot access the review decision tool",
        )
        return state

    repo.update(_assign_manager_and_block)

    _state, leases = TeamRuntimeService(repo).lease_due_agents(
        agent_id="manager",
        now=datetime(2026, 8, 9, 13, tzinfo=UTC),
    )

    assert leases == []


def test_capability_blocker_identity_accepts_delimiters_in_domain_ids(
    tmp_path: Path,
) -> None:
    repo = _seed_review_dispatch(tmp_path)
    admin = TeamAdminService(repo)
    admin.register_agent(
        agent_id="backend:one",
        team_id="team",
        project_id="project",
        role_id="project__backend",
        session_id="session-backend-one",
        reports_to_agent_id="lead",
        ephemeral_policy=EphemeralAgentPolicy(),
        expected_revision=repo.read().revision,
    )

    def _seed_delimited_review(state):
        state.deliverables["deliverable"].review_request_ids.append("review|one")
        state.review_requests["review|one"] = ReviewRequest(
            id="review|one",
            deliverable_id="deliverable",
            reviewer_agent_id="backend:one",
        )
        state.inbox["review-assignment-delimited"] = AgentInboxItem(
            id="review-assignment-delimited",
            recipient_agent_id="backend:one",
            item_type=TeamInboxItemType.DELIVERABLE_NOTICE,
            payload={"kind": "review_request", "review_request_id": "review|one"},
        )
        result: dict[str, object] = {}
        open_agent_mcp_capability_blocker(
            state,
            agent_id="backend:one",
            review_request_ids=["review|one"],
            affected_task_ids=["task"],
            dispatch_id="dispatch-delimited",
            required_tool_names=["team_decide_review"],
            diagnostic="Tool remained unavailable.",
            result=result,
            now=datetime(2026, 8, 9, 14, tzinfo=UTC),
        )
        return state

    state = repo.update(_seed_delimited_review)
    blockers = [
        blocker
        for blocker in state.blockers.values()
        if blocker.source_kind == TeamBlockerSourceKind.MCP_CAPABILITY
    ]
    assert len(blockers) == 1
    blocker = blockers[0]
    assert blocker.mcp_capability_identity is not None
    assert blocker.mcp_capability_identity.agent_id == "backend:one"
    assert blocker.mcp_capability_identity.review_request_ids == ["review|one"]
    assert "backend:one" not in blocker.source_id
    assert "review|one" not in blocker.source_id

    _state, leases = TeamRuntimeService(repo).lease_due_agents(
        agent_id="backend:one",
        now=datetime(2026, 8, 9, 14, 1, tzinfo=UTC),
    )
    assert leases == []


def test_review_capability_evidence_starts_after_confirmed_grant(
    tmp_path: Path,
) -> None:
    leased_at = datetime(2026, 8, 9, 15, tzinfo=UTC)
    grant_confirmed_at = leased_at + timedelta(seconds=2)
    dispatch = TeamDispatch(
        id="dispatch-review",
        agent_id="backend",
        session_id="session-backend",
        cause_kind="review",
        status=TeamDispatchStatus.LEASED,
        leased_at=leased_at,
        lease_expires_at=leased_at + timedelta(minutes=15),
        mcp_capability_receipt=TeamDispatchMcpCapabilityReceipt(
            required_tool_names=["team_decide_review"],
            grant_confirmed_at=grant_confirmed_at,
        ),
    )
    bindings = JsonMcpTransportBindingRepository.from_runtime_dir(tmp_path)
    bindings.record_visible_tools(
        transport_session_id="mcp-session-pre-grant",
        bound_session_id="session-backend",
        visible_tool_names=["team_get_task"],
        now=leased_at + timedelta(seconds=1),
    )
    bindings.record_visible_tools(
        transport_session_id="mcp-session-at-grant",
        bound_session_id="session-backend",
        visible_tool_names=["team_get_task"],
        now=grant_confirmed_at,
    )

    assert (
        _review_mcp_capability_observation(
            SimpleNamespace(resolved_config=SimpleNamespace(runtime_dir=tmp_path)),
            dispatch=dispatch,
        )
        is None
    )

    bindings.record_visible_tools(
        transport_session_id="mcp-session-post-grant",
        bound_session_id="session-backend",
        visible_tool_names=["team_get_task"],
        now=grant_confirmed_at + timedelta(seconds=1),
    )
    observation = _review_mcp_capability_observation(
        SimpleNamespace(resolved_config=SimpleNamespace(runtime_dir=tmp_path)),
        dispatch=dispatch,
    )
    assert observation is not None
    assert observation["verification_status"] == "missing"
    assert observation["observed_transport_session_id"] == "mcp-session-post-grant"
