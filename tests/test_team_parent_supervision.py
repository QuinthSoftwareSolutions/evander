from __future__ import annotations

from evander.models import utc_now
from evander_team_addon.models import (
    AgentTask,
    Deliverable,
    PersistentAgent,
    Project,
    ProjectReleasePolicy,
    ReviewRequest,
    Team,
    TeamInboxItemState,
    TeamRecordState,
    TeamState,
    TeamTaskKind,
    TeamTaskState,
)
from evander_team_addon.services.commands.deliverable_helpers import (
    _apply_command_deliverable_submission,
)
from evander_team_addon.services.domain.blockers import (
    open_or_get_blocker,
    open_or_update_agent_execution_stall,
)
from evander_team_addon.services.domain.deliverables import (
    _accept_deliverable_and_maybe_task,
)
from evander_team_addon.services.domain.review import _cascade_review_decision
from evander_team_addon.services.domain.task_supervision import (
    queue_parent_task_transition,
)


def _state_with_hierarchy(*, child_count: int = 1) -> TeamState:
    state = TeamState()
    state.teams["team"] = Team(
        id="team",
        name="Team",
        manager_agent_id="manager",
        project_ids=["project"],
    )
    state.projects["project"] = Project(
        id="project",
        team_id="team",
        name="Project",
        lead_agent_id="lead",
    )
    for agent_id, reports_to in (
        ("manager", None),
        ("lead", "manager"),
        ("parent-owner", "lead"),
        # The child owner does not report to the parent owner. Containment,
        # rather than the org chart, must drive parent supervision.
        ("child-owner", "lead"),
        ("reviewer", "lead"),
    ):
        state.agents[agent_id] = PersistentAgent(
            id=agent_id,
            team_id="team",
            role_id="role",
            session_id=f"session-{agent_id}",
            reports_to_agent_id=reports_to,
        )
    state.tasks["parent"] = AgentTask(
        id="parent",
        owner_agent_id="parent-owner",
        project_id="project",
        priority=1,
        task_kind=TeamTaskKind.SUPPORT,
        title="Integrate children",
        description="Combine the constituent results.",
        created_by_actor="operator:test",
    )
    for index in range(child_count):
        child_id = f"child-{index + 1}"
        state.tasks[child_id] = AgentTask(
            id=child_id,
            owner_agent_id="child-owner",
            project_id="project",
            parent_task_id="parent",
            priority=index + 1,
            task_kind=TeamTaskKind.SUPPORT,
            title=f"Child {index + 1}",
            description="Produce one constituent result.",
            created_by_agent_id="parent-owner",
        )
    return state


def _pending_for(state: TeamState, agent_id: str) -> list:
    return [
        item
        for item in state.inbox.values()
        if item.recipient_agent_id == agent_id
        and item.state == TeamInboxItemState.PENDING
    ]


def test_child_blocker_routes_to_distinct_parent_owner_and_marks_work_due() -> None:
    state = _state_with_hierarchy()
    now = utc_now()

    blocker, created = open_or_get_blocker(
        state,
        source_kind="task",
        source_id="child-1",
        summary="Child cannot proceed",
        details="Needs the parent owner's decision.",
        reporter_agent_id="child-owner",
        team_id="team",
        project_id="project",
        task_id="child-1",
        result={"events": []},
        now=now,
    )

    assert created is True
    assert blocker.owner_agent_id == "parent-owner"
    notice = next(
        item
        for item in _pending_for(state, "parent-owner")
        if item.payload.get("blocker_id") == blocker.id
    )
    assert notice.payload["transition"] == "blocker"
    assert notice.payload["parent_task_id"] == "parent"
    assert notice.payload["child_task_id"] == "child-1"
    assert state.agent_runtimes["parent-owner"].next_wake_at is None


def test_child_handoff_routes_to_parent_owner_instead_of_org_superior() -> None:
    state = _state_with_hierarchy()
    child = state.tasks["child-1"]

    created_inbox_ids = _apply_command_deliverable_submission(
        state,
        agent=state.agents["child-owner"],
        task=child,
        deliverable={
            "summary": "Child contribution",
            "artifact_refs": ["artifact:child"],
            "git_refs": {},
            "outcomes": [],
            "source_deliverable_ids": [],
        },
        deliverable_id="deliverable-child",
        published_records=[],
        suppress_superior_notice=False,
    )

    deliverable = state.deliverables["deliverable-child"]
    assert deliverable.acceptance_recipient_agent_id == "parent-owner"
    assert len(created_inbox_ids) == 1
    notice = state.inbox[created_inbox_ids[0]]
    assert notice.recipient_agent_id == "parent-owner"
    assert notice.payload["transition"] == "handoff"
    assert notice.payload["parent_task_id"] == "parent"
    assert _pending_for(state, "lead") == []
    assert state.agent_runtimes["parent-owner"].next_wake_at is None


def test_parent_transition_coalescing_preserves_all_reference_ids() -> None:
    state = _state_with_hierarchy(child_count=2)
    now = utc_now()

    first = queue_parent_task_transition(
        state,
        child_task_id="child-1",
        transition="handoff",
        sender_agent_id="child-owner",
        now=now,
        references={
            "deliverable_id": "deliverable-1",
            "review_request_id": "review-1",
        },
    )
    second = queue_parent_task_transition(
        state,
        child_task_id="child-2",
        transition="handoff",
        sender_agent_id="child-owner",
        now=now,
        references={
            "deliverable_id": "deliverable-2",
            "review_request_id": "review-2",
        },
    )

    assert first is not None
    assert second is first
    assert first.payload["deliverable_id"] == "deliverable-1"
    assert first.payload["deliverable_ids"] == ["deliverable-1", "deliverable-2"]
    assert first.payload["review_request_id"] == "review-1"
    assert first.payload["review_request_ids"] == ["review-1", "review-2"]


def test_formal_review_handoff_still_notifies_parent_owner() -> None:
    state = _state_with_hierarchy()
    child = state.tasks["child-1"]

    created_inbox_ids = _apply_command_deliverable_submission(
        state,
        agent=state.agents["child-owner"],
        task=child,
        deliverable={
            "summary": "Specialist-reviewed contribution",
            "artifact_refs": ["artifact:child"],
            "git_refs": {},
            "outcomes": [],
            "source_deliverable_ids": [],
        },
        deliverable_id="deliverable-reviewed-child",
        published_records=[],
        suppress_superior_notice=True,
    )

    deliverable = state.deliverables["deliverable-reviewed-child"]
    assert deliverable.acceptance_recipient_agent_id is None
    assert len(created_inbox_ids) == 1
    notice = state.inbox[created_inbox_ids[0]]
    assert notice.recipient_agent_id == "parent-owner"
    assert notice.payload["transition"] == "handoff"
    assert notice.payload["deliverable_id"] == deliverable.id
    assert state.agent_runtimes["parent-owner"].next_wake_at is None


def test_formal_review_handoff_does_not_wake_its_own_parent_owner() -> None:
    state = _state_with_hierarchy()
    child = state.tasks["child-1"]
    child.owner_agent_id = "parent-owner"

    created_inbox_ids = _apply_command_deliverable_submission(
        state,
        agent=state.agents["parent-owner"],
        task=child,
        deliverable={
            "summary": "Parent owner's reviewed contribution",
            "artifact_refs": ["artifact:child"],
            "git_refs": {},
            "outcomes": [],
            "source_deliverable_ids": [],
        },
        deliverable_id="deliverable-self-reviewed-child",
        published_records=[],
        suppress_superior_notice=True,
    )

    assert created_inbox_ids == []
    assert _pending_for(state, "parent-owner") == []
    assert "parent-owner" not in state.agent_runtimes


def test_redundant_parent_handoff_and_rejection_do_not_notify_sender() -> None:
    for transition in ("handoff", "rejection"):
        state = _state_with_hierarchy()
        state.tasks["child-1"].owner_agent_id = "parent-owner"

        notice = queue_parent_task_transition(
            state,
            child_task_id="child-1",
            transition=transition,
            sender_agent_id="parent-owner",
            result={"events": [], "created_inbox_item_ids": []},
            now=utc_now(),
        )

        assert notice is None
        assert _pending_for(state, "parent-owner") == []
        assert "parent-owner" not in state.agent_runtimes


def test_self_observed_parent_resolution_is_retained_as_handled_history() -> None:
    state = _state_with_hierarchy()
    state.tasks["child-1"].owner_agent_id = "parent-owner"
    result = {"events": [], "created_inbox_item_ids": []}

    notice = queue_parent_task_transition(
        state,
        child_task_id="child-1",
        transition="resolution",
        sender_agent_id="parent-owner",
        result=result,
        now=utc_now(),
    )

    assert notice is not None
    assert notice.recipient_agent_id == "parent-owner"
    assert notice.sender_agent_id == "parent-owner"
    assert notice.state == TeamInboxItemState.HANDLED
    assert notice.response_requested is False
    assert notice.payload["transition"] == "resolution"
    assert result["created_inbox_item_ids"] == [notice.id]
    assert result["events"] == []
    assert _pending_for(state, "parent-owner") == []
    assert "parent-owner" not in state.agent_runtimes


def test_actionable_self_parent_transitions_still_notify_owner() -> None:
    for transition in ("blocker", "integration_ready"):
        state = _state_with_hierarchy()
        state.tasks["child-1"].owner_agent_id = "parent-owner"

        notice = queue_parent_task_transition(
            state,
            child_task_id="child-1",
            transition=transition,
            sender_agent_id="parent-owner",
            result={"events": [], "created_inbox_item_ids": []},
            now=utc_now(),
        )

        assert notice is not None
        assert notice.recipient_agent_id == "parent-owner"
        assert notice.payload["transition"] == transition


def test_formal_review_rejection_notifies_child_for_rework_and_parent_for_supervision() -> None:
    state = _state_with_hierarchy()
    child = state.tasks["child-1"]
    child.state = TeamTaskState.WAITING_FOR_REVIEW
    deliverable = Deliverable(
        id="deliverable-child",
        task_id=child.id,
        producer_agent_id="child-owner",
        summary="Child contribution",
        state=TeamRecordState.PENDING,
        review_request_ids=["review-child"],
    )
    review = ReviewRequest(
        id="review-child",
        deliverable_id=deliverable.id,
        reviewer_agent_id="reviewer",
    )
    state.deliverables[deliverable.id] = deliverable
    state.review_requests[review.id] = review
    child.deliverable_ids = [deliverable.id]
    result: dict[str, object] = {"events": [], "created_inbox_item_ids": []}

    _cascade_review_decision(
        state,
        review=review,
        decision_state=TeamRecordState.REJECTED,
        findings=["Fix the boundary."],
        projects_to_recalc=set(),
        result=result,
    )

    assert child.state == TeamTaskState.IN_PROGRESS
    assert any(
        item.payload.get("kind") == "review_rejected"
        for item in _pending_for(state, "child-owner")
    )
    parent_notice = next(
        item
        for item in _pending_for(state, "parent-owner")
        if item.payload.get("transition") == "rejection"
    )
    assert parent_notice.payload["parent_task_id"] == "parent"
    assert parent_notice.payload["child_task_ids"] == ["child-1"]
    assert state.agent_runtimes["parent-owner"].next_wake_at is None


def test_child_resolution_coalesces_and_emits_integration_ready_transition() -> None:
    state = _state_with_hierarchy(child_count=2)

    for index in (1, 2):
        child = state.tasks[f"child-{index}"]
        deliverable = Deliverable(
            id=f"deliverable-child-{index}",
            task_id=child.id,
            producer_agent_id="child-owner",
            summary=f"Child contribution {index}",
            state=TeamRecordState.PENDING,
        )
        state.deliverables[deliverable.id] = deliverable
        child.deliverable_ids = [deliverable.id]
        assert _accept_deliverable_and_maybe_task(
            state,
            deliverable=deliverable,
            task=child,
            now=utc_now(),
        )

    parent_notices = _pending_for(state, "parent-owner")
    resolution = next(
        item
        for item in parent_notices
        if item.payload.get("transition") == "resolution"
    )
    integration_ready = next(
        item
        for item in parent_notices
        if item.payload.get("transition") == "integration_ready"
    )
    assert resolution.payload["child_task_ids"] == ["child-1", "child-2"]
    assert integration_ready.payload["parent_task_id"] == "parent"
    assert integration_ready.payload["child_task_ids"] == ["child-2"]
    assert state.agent_runtimes["parent-owner"].next_wake_at is None


def test_final_review_acceptance_emits_parent_wake_event() -> None:
    state = _state_with_hierarchy()
    child = state.tasks["child-1"]
    deliverable = Deliverable(
        id="deliverable-child-reviewed",
        task_id=child.id,
        producer_agent_id="child-owner",
        summary="Reviewed child contribution",
        state=TeamRecordState.PENDING,
    )
    review = ReviewRequest(
        id="review-child-accepted",
        deliverable_id=deliverable.id,
        reviewer_agent_id="reviewer",
    )
    state.deliverables[deliverable.id] = deliverable
    state.review_requests[review.id] = review
    child.deliverable_ids = [deliverable.id]
    deliverable.review_request_ids = [review.id]
    result: dict[str, object] = {"events": [], "created_inbox_item_ids": []}

    _cascade_review_decision(
        state,
        review=review,
        decision_state=TeamRecordState.ACCEPTED,
        findings=[],
        projects_to_recalc=set(),
        result=result,
    )

    assert child.state == TeamTaskState.FINISHED
    assert {
        item.payload.get("transition")
        for item in _pending_for(state, "parent-owner")
    } == {"resolution", "integration_ready"}
    wake_events = [
        event
        for event in result["events"]
        if event["event_type"] == "team.agent.wake_requested"
    ]
    assert wake_events
    assert {
        event["payload"]["agent_id"] for event in wake_events
    } == {"parent-owner"}
    assert {
        event["payload"]["reason"] for event in wake_events
    } == {"inbox"}


def test_final_root_review_acceptance_wakes_project_lead_for_release() -> None:
    state = _state_with_hierarchy()
    root = state.tasks["child-1"]
    root.parent_task_id = None
    state.projects["project"].release_policy = ProjectReleasePolicy(
        allow_ungated=True,
    )
    deliverable = Deliverable(
        id="deliverable-root-reviewed",
        task_id=root.id,
        producer_agent_id="child-owner",
        summary="Reviewed root contribution",
        state=TeamRecordState.PENDING,
    )
    review = ReviewRequest(
        id="review-root-accepted",
        deliverable_id=deliverable.id,
        reviewer_agent_id="reviewer",
    )
    state.deliverables[deliverable.id] = deliverable
    state.review_requests[review.id] = review
    root.deliverable_ids = [deliverable.id]
    deliverable.review_request_ids = [review.id]
    result: dict[str, object] = {"events": [], "created_inbox_item_ids": []}

    _cascade_review_decision(
        state,
        review=review,
        decision_state=TeamRecordState.ACCEPTED,
        findings=[],
        projects_to_recalc=set(),
        result=result,
    )

    assert root.state == TeamTaskState.FINISHED
    notice = next(
        item
        for item in _pending_for(state, "lead")
        if item.payload.get("kind") == "root_task_resolution"
    )
    assert notice.payload["task_id"] == root.id
    assert notice.payload["deliverable_id"] == deliverable.id
    assert notice.payload["release_ready"] is True
    wake_events = [
        event
        for event in result["events"]
        if event["event_type"] == "team.agent.wake_requested"
    ]
    assert wake_events
    assert {
        event["payload"]["agent_id"] for event in wake_events
    } == {"lead"}


def test_execution_stall_notifies_every_distinct_parent_owner_and_coalesces() -> None:
    state = _state_with_hierarchy(child_count=4)
    state.tasks["parent-2"] = AgentTask(
        id="parent-2",
        owner_agent_id="reviewer",
        project_id="project",
        priority=2,
        task_kind=TeamTaskKind.SUPPORT,
        title="Integrate alternate children",
        description="Combine a separate constituent result.",
        created_by_actor="operator:test",
    )
    state.tasks["parent-3"] = AgentTask(
        id="parent-3",
        owner_agent_id="parent-owner",
        project_id="project",
        priority=3,
        task_kind=TeamTaskKind.SUPPORT,
        title="Integrate final children",
        description="Combine another constituent result.",
        created_by_actor="operator:test",
    )
    state.tasks["child-3"].parent_task_id = "parent-2"
    state.tasks["child-4"].parent_task_id = "parent-3"
    affected_task_ids = [f"child-{index}" for index in range(1, 5)]

    first_result: dict[str, object] = {
        "events": [],
        "created_inbox_item_ids": [],
    }
    blocker = open_or_update_agent_execution_stall(
        state,
        agent_id="child-owner",
        affected_task_ids=affected_task_ids,
        exhausted_dispatch_id="dispatch-1",
        exhausted_dispatch_task_ids=["child-1", "child-3"],
        no_progress_count=3,
        max_no_progress_count=3,
        result=first_result,
        now=utc_now(),
    )

    parent_notices = [
        item
        for item in state.inbox.values()
        if item.payload.get("kind") == "agent_execution_stall_parent_notice"
    ]
    assert len(parent_notices) == 2
    notices_by_recipient = {
        item.recipient_agent_id: item for item in parent_notices
    }
    assert notices_by_recipient[
        "parent-owner"
    ].payload["accountable_parent_task_ids"] == ["parent", "parent-3"]
    assert notices_by_recipient[
        "parent-owner"
    ].payload["affected_child_task_ids"] == [
        "child-1",
        "child-2",
        "child-4",
    ]
    assert notices_by_recipient[
        "reviewer"
    ].payload["accountable_parent_task_ids"] == ["parent-2"]
    assert notices_by_recipient["reviewer"].payload["affected_child_task_ids"] == [
        "child-3"
    ]

    second_result: dict[str, object] = {
        "events": [],
        "created_inbox_item_ids": [],
    }
    repeated = open_or_update_agent_execution_stall(
        state,
        agent_id="child-owner",
        affected_task_ids=affected_task_ids,
        exhausted_dispatch_id="dispatch-2",
        exhausted_dispatch_task_ids=["child-2", "child-4"],
        no_progress_count=4,
        max_no_progress_count=3,
        result=second_result,
        now=utc_now(),
    )

    assert repeated.id == blocker.id
    assert len(
        [
            item
            for item in state.inbox.values()
            if item.payload.get("kind")
            == "agent_execution_stall_parent_notice"
        ]
    ) == 2
    for notice in notices_by_recipient.values():
        assert notice.payload["exhausted_dispatch_ids"] == [
            "dispatch-1",
            "dispatch-2",
        ]
