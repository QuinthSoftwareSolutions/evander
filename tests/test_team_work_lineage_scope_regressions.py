from __future__ import annotations

import json
from datetime import UTC, datetime

from evander_team_addon.api import _team_state_payload_for_request
from evander_team_addon.models import (
    AgentProjectAssignment,
    AgentRuntime,
    AgentTask,
    Deliverable,
    HumanReviewRequest,
    HumanReviewRequirementSnapshot,
    PersistentAgent,
    Project,
    ProjectGitConfig,
    ReleasePackage,
    ReviewRequest,
    TeamBlocker,
    TeamBlockerSourceKind,
    TeamCase,
    TeamConversation,
    TeamConversationMessage,
    TeamDispatch,
    TeamDispatchReviewRoutingAuthority,
    Team,
    TeamRole,
    TeamState,
    TeamWorkLineage,
    WorkLineageChatSourceRef,
)
from evander_team_addon.providers import (
    _dispatch_work_scope_survives,
    _filtered_team_state_payload,
)
from evander_team_addon.services.domain.wake import _dispatch_case_payloads


def _release_case_state() -> TeamState:
    state = TeamState.empty()
    state.agents["reviewer"] = PersistentAgent(
        id="reviewer",
        team_id="team",
        role_id="reviewer-role",
        session_id="session-reviewer",
    )
    state.tasks["release-task"] = AgentTask(
        id="release-task",
        owner_agent_id="producer",
        project_id="project",
        case_id="release-case",
        priority=1,
        title="Prepare release input",
        description="Produce the deliverable included in the release.",
        created_by_actor="system:test",
    )
    state.cases["release-case"] = TeamCase(
        id="release-case",
        team_id="team",
        project_id="project",
        title="Release customer case",
        task_ids=["release-task"],
    )
    state.deliverables["release-deliverable"] = Deliverable(
        id="release-deliverable",
        task_id="release-task",
        producer_agent_id="producer",
        summary="Release input",
    )
    state.release_packages["release"] = ReleasePackage(
        id="release",
        project_id="project",
        deliverable_ids=["release-deliverable"],
    )
    return state


def test_release_backed_review_dispatch_includes_deliverable_case_detail() -> None:
    state = _release_case_state()
    state.review_requests["release-review"] = ReviewRequest(
        id="release-review",
        release_package_id="release",
        candidate_commit_sha="candidate-commit",
        reviewer_agent_id="reviewer",
    )
    dispatch = TeamDispatch(
        id="dispatch-release-review",
        agent_id="reviewer",
        session_id="session-reviewer",
        cause_kind="review",
        review_request_ids=["release-review"],
        project_ids=["project"],
    )

    payload = _dispatch_case_payloads(
        state,
        agent=state.agents["reviewer"],
        dispatch=dispatch,
    )

    assert [case["id"] for case in payload["cases"]] == ["release-case"]


def test_human_review_blocker_dispatch_includes_release_deliverable_case_detail() -> None:
    state = _release_case_state()
    state.human_review_requests["human-review"] = HumanReviewRequest(
        id="human-review",
        release_package_id="release",
        candidate_commit_sha="candidate-commit",
        candidate_tree_hash="candidate-tree",
        requirement_snapshot=HumanReviewRequirementSnapshot(
            directory_path="root",
            configured_group_ids=["release-reviewers"],
            initially_eligible_user_ids=[],
        ),
        state="preparing",
    )
    state.blockers["human-review-blocker"] = TeamBlocker(
        id="human-review-blocker",
        team_id="team",
        project_id="project",
        source_kind=TeamBlockerSourceKind.HUMAN_REVIEW_REQUEST,
        source_id="human-review",
        owner_agent_id="reviewer",
        summary="Human-review preparation needs recovery.",
    )
    dispatch = TeamDispatch(
        id="dispatch-human-review-blocker",
        agent_id="reviewer",
        session_id="session-reviewer",
        cause_kind="blocker",
        blocker_ids=["human-review-blocker"],
        project_ids=["project"],
    )

    payload = _dispatch_case_payloads(
        state,
        agent=state.agents["reviewer"],
        dispatch=dispatch,
    )

    assert [case["id"] for case in payload["cases"]] == ["release-case"]


def test_dispatch_scope_survival_rejects_mutated_routing_authority() -> None:
    raw_authority = {
        "kind": "reviewer_without_assigned_review_work",
        "reviewer_agent_id": "reviewer",
        "project_id": "project",
        "task_id": "task",
        "deliverable_id": "deliverable",
        "consumed_at": "2026-09-02T08:30:00Z",
        "consumed_by_review_request_id": "hidden-review",
    }
    scoped_authority = {
        **raw_authority,
        "consumed_at": None,
        "consumed_by_review_request_id": None,
    }

    assert not _dispatch_work_scope_survives(
        {"review_routing_authorities": [raw_authority]},
        {"review_routing_authorities": [scoped_authority]},
    )


def _chat_lineage_state() -> TeamState:
    timestamp = datetime(2026, 9, 2, 8, 0, tzinfo=UTC)
    conversations = {
        "conversation-alice-backend": TeamConversation(
            id="conversation-alice-backend",
            agent_id="backend",
            operator_user_id="alice@example.com",
            created_at=timestamp,
            updated_at=timestamp,
            last_interaction_at=timestamp,
        ),
        "conversation-bob-backend": TeamConversation(
            id="conversation-bob-backend",
            agent_id="backend",
            operator_user_id="bob@example.com",
            created_at=timestamp,
            updated_at=timestamp,
            last_interaction_at=timestamp,
        ),
        "conversation-alice-other": TeamConversation(
            id="conversation-alice-other",
            agent_id="other",
            operator_user_id="alice@example.com",
            created_at=timestamp,
            updated_at=timestamp,
            last_interaction_at=timestamp,
        ),
    }
    messages = {
        "message-alice-backend": TeamConversationMessage(
            id="message-alice-backend",
            conversation_id="conversation-alice-backend",
            sender="operator",
            body="Alice backend request",
            created_at=timestamp,
            updated_at=timestamp,
        ),
        "message-bob-backend": TeamConversationMessage(
            id="message-bob-backend",
            conversation_id="conversation-bob-backend",
            sender="operator",
            body="Bob private request",
            created_at=timestamp,
            updated_at=timestamp,
        ),
        "message-alice-other": TeamConversationMessage(
            id="message-alice-other",
            conversation_id="conversation-alice-other",
            sender="operator",
            body="Alice other-agent request",
            created_at=timestamp,
            updated_at=timestamp,
        ),
    }

    def lineage(
        lineage_id: str,
        *anchors: tuple[str, str],
    ) -> TeamWorkLineage:
        return TeamWorkLineage(
            id=lineage_id,
            chat_source_refs=[
                WorkLineageChatSourceRef(
                    conversation_id=conversation_id,
                    message_ids=[message_id],
                )
                for conversation_id, message_id in anchors
            ],
            created_at=timestamp,
            updated_at=timestamp,
        )

    return TeamState(
        agents={
            "backend": PersistentAgent(id="backend", session_id="session-backend"),
            "other": PersistentAgent(id="other", session_id="session-other"),
        },
        agent_runtimes={
            "backend": AgentRuntime(
                agent_id="backend",
                work_lineage_thread_ids={
                    "lineage-alice-backend": "thread-alice",
                    "lineage-bob-backend": "thread-bob",
                    "lineage-backend-runtime": "thread-backend",
                },
            ),
            "other": AgentRuntime(
                agent_id="other",
                work_lineage_thread_ids={"lineage-other-runtime": "thread-other"},
            ),
        },
        conversations=conversations,
        conversation_messages=messages,
        dispatches={
            "dispatch-backend": TeamDispatch(
                id="dispatch-backend",
                agent_id="backend",
                session_id="session-backend",
                cause_kind="task",
                work_lineage_id="lineage-backend-runtime",
            ),
            "dispatch-other": TeamDispatch(
                id="dispatch-other",
                agent_id="other",
                session_id="session-other",
                cause_kind="task",
                work_lineage_id="lineage-other-runtime",
            ),
            "dispatch-bob-lineage": TeamDispatch(
                id="dispatch-bob-lineage",
                agent_id="backend",
                session_id="session-backend",
                cause_kind="task",
                work_lineage_id="lineage-bob-backend",
            ),
        },
        work_lineages={
            "lineage-alice-backend": lineage(
                "lineage-alice-backend",
                ("conversation-alice-backend", "message-alice-backend"),
            ),
            "lineage-bob-backend": lineage(
                "lineage-bob-backend",
                ("conversation-bob-backend", "message-bob-backend"),
            ),
            "lineage-alice-other": lineage(
                "lineage-alice-other",
                ("conversation-alice-other", "message-alice-other"),
            ),
            "lineage-mixed-operators": lineage(
                "lineage-mixed-operators",
                ("conversation-alice-backend", "message-alice-backend"),
                ("conversation-bob-backend", "message-bob-backend"),
            ),
            "lineage-mixed-agents": lineage(
                "lineage-mixed-agents",
                ("conversation-alice-backend", "message-alice-backend"),
                ("conversation-alice-other", "message-alice-other"),
            ),
            "lineage-backend-runtime": lineage("lineage-backend-runtime"),
            "lineage-other-runtime": lineage("lineage-other-runtime"),
        },
    )


class _AuthStub:
    def __init__(self, *, can_read_all: bool) -> None:
        self.can_read_all = can_read_all

    def read_session(self, _request: object) -> dict[str, str]:
        return {"login": "Alice@Example.com", "name": "Alice"}

    def can(self, _request: object, _permission: str) -> bool:
        return self.can_read_all


def test_operator_state_filters_lineages_and_refs_to_visible_chats() -> None:
    state = _chat_lineage_state()

    payload = _team_state_payload_for_request(
        _AuthStub(can_read_all=False),
        object(),  # type: ignore[arg-type]
        state,
    )

    assert set(payload["work_lineages"]) == {
        "lineage-alice-backend",
        "lineage-alice-other",
        "lineage-mixed-operators",
        "lineage-mixed-agents",
        "lineage-backend-runtime",
        "lineage-other-runtime",
    }
    assert payload["work_lineages"]["lineage-mixed-operators"]["chat_source_refs"] == [
        {
            "conversation_id": "conversation-alice-backend",
            "message_ids": ["message-alice-backend"],
        }
    ]
    serialized = json.dumps(payload["work_lineages"], sort_keys=True)
    assert "conversation-bob-backend" not in serialized
    assert "message-bob-backend" not in serialized
    assert payload["agent_runtimes"]["backend"]["work_lineage_thread_ids"] == {
        "lineage-alice-backend": "thread-alice",
        "lineage-backend-runtime": "thread-backend",
    }
    assert payload["dispatches"]["dispatch-bob-lineage"]["work_lineage_id"] is None
    visible_lineage_ids = set(payload["work_lineages"])
    assert all(
        row["work_lineage_id"] is None or row["work_lineage_id"] in visible_lineage_ids
        for collection_name in ("tasks", "release_packages", "dispatches")
        for row in payload[collection_name].values()
    )
    full_serialized = json.dumps(payload, sort_keys=True)
    assert "lineage-bob-backend" not in full_serialized
    assert "thread-bob" not in full_serialized

    read_all = _team_state_payload_for_request(
        _AuthStub(can_read_all=True),
        object(),  # type: ignore[arg-type]
        state,
    )
    assert "lineage-bob-backend" in read_all["work_lineages"]
    assert len(read_all["work_lineages"]["lineage-mixed-operators"]["chat_source_refs"]) == 2


def test_filtered_team_state_derives_lineages_from_visible_agent_scope() -> None:
    payload = _filtered_team_state_payload(
        _chat_lineage_state(),
        team_id=None,
        project_id=None,
        agent_id="backend",
    )

    assert set(payload["work_lineages"]) == {
        "lineage-bob-backend",
        "lineage-backend-runtime",
    }
    serialized = json.dumps(payload["work_lineages"], sort_keys=True)
    assert "conversation-alice-other" not in serialized
    assert "message-alice-other" not in serialized
    assert "lineage-other-runtime" not in serialized
    assert payload["agent_runtimes"]["backend"]["work_lineage_thread_ids"] == {
        "lineage-bob-backend": "thread-bob",
        "lineage-backend-runtime": "thread-backend",
    }
    assert set(payload["dispatches"]) == {
        "dispatch-backend",
        "dispatch-bob-lineage",
    }
    visible_lineage_ids = set(payload["work_lineages"])
    assert all(
        row["work_lineage_id"] is None or row["work_lineage_id"] in visible_lineage_ids
        for collection_name in ("tasks", "release_packages", "dispatches")
        for row in payload[collection_name].values()
    )


def test_project_state_does_not_expose_runtime_lineage_bindings_from_another_project() -> None:
    state = _chat_lineage_state()
    state.agents["backend"].team_id = "team"
    state.agents["backend"].role_id = "project-a-role"
    state.teams["team"] = Team(
        id="team",
        name="Team",
        manager_agent_id="backend",
        project_ids=["project-a", "project-b"],
    )
    state.projects["project-a"] = Project(
        id="project-a",
        team_id="team",
        name="Project A",
        lead_agent_id="backend",
        git=ProjectGitConfig(managed_repo_id=f"mr_{'a' * 20}"),
    )
    state.projects["project-b"] = Project(
        id="project-b",
        team_id="team",
        name="Project B",
        lead_agent_id="backend",
        git=ProjectGitConfig(managed_repo_id=f"mr_{'b' * 20}"),
    )
    state.roles["project-a-role"] = TeamRole(
        id="project-a-role",
        team_id="team",
        project_id="project-a",
        name="Project A role",
    )
    state.project_assignments["assignment-a"] = AgentProjectAssignment(
        id="assignment-a",
        team_id="team",
        project_id="project-a",
        agent_id="backend",
        role_id="project-a-role",
    )
    state.project_assignments["assignment-b"] = AgentProjectAssignment(
        id="assignment-b",
        team_id="team",
        project_id="project-b",
        agent_id="backend",
    )
    for project_suffix in ("a", "b"):
        lineage_id = f"lineage-project-{project_suffix}"
        state.work_lineages[lineage_id] = TeamWorkLineage(id=lineage_id)
        state.tasks[f"task-{project_suffix}"] = AgentTask(
            id=f"task-{project_suffix}",
            owner_agent_id="backend",
            project_id=f"project-{project_suffix}",
            work_lineage_id=lineage_id,
            priority=1,
            title=f"Project {project_suffix.upper()} task",
            description="Scoped work",
            created_by_actor="system:test",
        )
    state.deliverables["deliverable-a"] = Deliverable(
        id="deliverable-a",
        task_id="task-a",
        producer_agent_id="backend",
        summary="Project A deliverable",
    )
    state.tasks["task-a"].deliverable_ids = ["deliverable-a"]
    state.review_requests["review-a"] = ReviewRequest(
        id="review-a",
        deliverable_id="deliverable-a",
        reviewer_agent_id="backend",
    )
    state.review_requests["review-hidden"] = ReviewRequest(
        id="review-hidden",
        deliverable_id="deliverable-a",
        reviewer_agent_id="other",
    )
    state.agent_runtimes["backend"].work_lineage_thread_ids.update(
        {
            "lineage-project-a": "thread-project-a",
            "lineage-project-b": "thread-project-b",
        }
    )
    state.dispatches.update(
        {
            "dispatch-project-a": TeamDispatch(
                id="dispatch-project-a",
                agent_id="backend",
                session_id="session-backend",
                cause_kind="task",
                work_lineage_id="lineage-project-a",
                task_ids=["task-a"],
                project_ids=["project-a"],
            ),
            "dispatch-project-b": TeamDispatch(
                id="dispatch-project-b",
                agent_id="backend",
                session_id="session-backend",
                cause_kind="task",
                work_lineage_id="lineage-project-b",
                task_ids=["task-b"],
                project_ids=["project-b"],
            ),
            "dispatch-mixed-projects": TeamDispatch(
                id="dispatch-mixed-projects",
                agent_id="backend",
                session_id="session-backend",
                cause_kind="task",
                cause_id="task-b",
                work_lineage_id="lineage-project-b",
                task_ids=["task-a", "task-b"],
                review_request_ids=["review-a", "review-hidden"],
                project_ids=["project-a", "project-b"],
                managed_repo_ids=[f"mr_{'a' * 20}", f"mr_{'b' * 20}"],
                action_feedback_dispatch_ids=[
                    "dispatch-project-a",
                    "dispatch-project-b",
                ],
                wake_work_record_fingerprints={
                    "review:review-a": "visible-review-fingerprint",
                    "review:review-hidden": "hidden-review-fingerprint",
                    "open_work:task-a": "visible-task-fingerprint",
                    "open_work:task-b": "hidden-task-fingerprint",
                    "routing_gaps:opaque-digest": "opaque-routing-fingerprint",
                    "future_kind:hidden-durable-id": "unknown-kind-fingerprint",
                },
            ),
            "dispatch-authorities": TeamDispatch(
                id="dispatch-authorities",
                agent_id="backend",
                session_id="session-backend",
                cause_kind="manager_fallback",
                work_lineage_id="lineage-project-a",
                task_ids=["task-a"],
                project_ids=["project-a"],
                review_routing_authorities=[
                    TeamDispatchReviewRoutingAuthority(
                        kind="reviewer_without_assigned_review_work",
                        reviewer_agent_id="backend",
                        project_id="project-a",
                        task_id="task-a",
                        deliverable_id="deliverable-a",
                        consumed_at=datetime(2026, 9, 2, 8, 30, tzinfo=UTC),
                        consumed_by_review_request_id="review-hidden",
                    ),
                    TeamDispatchReviewRoutingAuthority(
                        kind="reviewer_without_assigned_review_work",
                        reviewer_agent_id="other",
                        project_id="project-a",
                        task_id="task-a",
                        deliverable_id="deliverable-a",
                    ),
                ],
            ),
            "dispatch-stale-scope": TeamDispatch(
                id="dispatch-stale-scope",
                agent_id="backend",
                session_id="session-backend",
                cause_kind="task",
                cause_id="task-b",
                work_lineage_id="lineage-project-b",
                task_ids=["task-b"],
                project_ids=["project-a"],
            ),
            "dispatch-stale-fingerprint": TeamDispatch(
                id="dispatch-stale-fingerprint",
                agent_id="backend",
                session_id="session-backend",
                cause_kind="scheduled",
                work_lineage_id="lineage-project-b",
                project_ids=["project-a"],
                wake_work_record_fingerprints={
                    "review:review-hidden": "hidden-review-fingerprint"
                },
            ),
            "dispatch-stale-lineage": TeamDispatch(
                id="dispatch-stale-lineage",
                agent_id="backend",
                session_id="session-backend",
                cause_kind="task",
                cause_id="task-a",
                work_lineage_id="lineage-project-b",
                task_ids=["task-a"],
                project_ids=["project-a"],
            ),
            "dispatch-unknown-cause": TeamDispatch(
                id="dispatch-unknown-cause",
                agent_id="backend",
                session_id="session-backend",
                cause_kind="future_trigger",
                cause_id="hidden-durable-id",
                work_lineage_id="lineage-project-b",
                project_ids=["project-a"],
            ),
            "dispatch-invisible-agent": TeamDispatch(
                id="dispatch-invisible-agent",
                agent_id="other",
                session_id="session-other",
                cause_kind="task",
                work_lineage_id="lineage-project-a",
                task_ids=["task-a"],
                project_ids=["project-a"],
            ),
        }
    )
    state.agent_runtimes["backend"].active_dispatch_id = "dispatch-project-b"

    payload = _filtered_team_state_payload(
        state,
        team_id=None,
        project_id="project-a",
        agent_id=None,
    )

    assert set(payload["work_lineages"]) == {"lineage-project-a"}
    assert set(payload["dispatches"]) == {
        "dispatch-authorities",
        "dispatch-project-a",
        "dispatch-mixed-projects",
        "dispatch-stale-fingerprint",
        "dispatch-stale-lineage",
        "dispatch-stale-scope",
        "dispatch-unknown-cause",
    }
    assert payload["dispatches"]["dispatch-project-a"]["work_lineage_id"] == "lineage-project-a"
    assert payload["dispatches"]["dispatch-mixed-projects"]["project_ids"] == ["project-a"]
    assert payload["dispatches"]["dispatch-mixed-projects"]["task_ids"] == ["task-a"]
    assert payload["dispatches"]["dispatch-mixed-projects"]["managed_repo_ids"] == [
        f"mr_{'a' * 20}"
    ]
    assert payload["dispatches"]["dispatch-mixed-projects"][
        "action_feedback_dispatch_ids"
    ] == ["dispatch-project-a"]
    assert payload["dispatches"]["dispatch-mixed-projects"]["review_request_ids"] == [
        "review-a"
    ]
    assert payload["dispatches"]["dispatch-mixed-projects"][
        "wake_work_record_fingerprints"
    ] == {
        "review:review-a": "visible-review-fingerprint",
        "open_work:task-a": "visible-task-fingerprint",
        "routing_gaps:opaque-digest": "opaque-routing-fingerprint",
    }
    assert payload["dispatches"]["dispatch-authorities"][
        "review_routing_authorities"
    ] == [
        {
            "kind": "reviewer_without_assigned_review_work",
            "reviewer_agent_id": "backend",
            "project_id": "project-a",
            "task_id": "task-a",
            "deliverable_id": "deliverable-a",
            "consumed_at": None,
            "consumed_by_review_request_id": None,
        }
    ]
    assert payload["dispatches"]["dispatch-mixed-projects"]["cause_id"] is None
    assert payload["dispatches"]["dispatch-mixed-projects"]["work_lineage_id"] is None
    assert payload["dispatches"]["dispatch-stale-scope"]["task_ids"] == []
    assert payload["dispatches"]["dispatch-stale-scope"]["cause_id"] is None
    assert payload["dispatches"]["dispatch-stale-scope"]["work_lineage_id"] is None
    assert payload["dispatches"]["dispatch-stale-fingerprint"][
        "wake_work_record_fingerprints"
    ] == {}
    assert payload["dispatches"]["dispatch-stale-fingerprint"]["work_lineage_id"] is None
    assert payload["dispatches"]["dispatch-stale-lineage"]["work_lineage_id"] is None
    assert payload["dispatches"]["dispatch-unknown-cause"]["cause_id"] is None
    assert payload["dispatches"]["dispatch-unknown-cause"]["work_lineage_id"] is None
    assert payload["agent_runtimes"]["backend"]["work_lineage_thread_ids"] == {
        "lineage-project-a": "thread-project-a"
    }
    assert payload["agent_runtimes"]["backend"]["active_dispatch_id"] is None
    runtime_row = next(
        row for row in payload["runtime_operations"]["agents"] if row["id"] == "backend"
    )
    assert runtime_row["active_dispatch_id"] is None
    visible_lineage_ids = set(payload["work_lineages"])
    assert all(
        row["work_lineage_id"] is None or row["work_lineage_id"] in visible_lineage_ids
        for collection_name in ("tasks", "release_packages", "dispatches")
        for row in payload[collection_name].values()
    )
    serialized = json.dumps(payload, sort_keys=True)
    assert "lineage-project-b" not in serialized
    assert "thread-project-b" not in serialized
    assert "dispatch-project-b" not in serialized
    assert "dispatch-invisible-agent" not in serialized
    assert "future_kind:hidden-durable-id" not in serialized
