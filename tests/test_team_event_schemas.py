from datetime import UTC, datetime

from team_runtime_support import _seed_team
from evander.events.schema import validate_payload
from evander_team_addon.event_schemas import event_payload_schema
from evander_team_addon.models import EphemeralAssignment, TeamCase, TeamCaseStatus
from evander_team_addon.services.domain.case_events import case_closed_event
from evander_team_addon.services.domain.ephemeral import _ephemeral_assignment_event_payload
from evander_team_addon.services.domain.wake import _append_agent_wake_requested_event


def test_case_closure_schema_accepts_optional_source_and_rejects_wrong_case_body():
    case = TeamCase(
        id="case",
        team_id="team",
        title="Example",
        status=TeamCaseStatus.CLOSED,
        closed_at=datetime.now(UTC),
    )
    event = case_closed_event(case, previous_status=TeamCaseStatus.OPEN)
    schema = event_payload_schema(event["event_type"])
    assert not validate_payload(schema, event["payload"])
    assert (
        validate_payload(schema, {**event["payload"], "case": "not an object"})[0]["path"]
        == "/case"
    )


def test_ephemeral_event_schemas_accept_producer_defaults(tmp_path):
    state = _seed_team(tmp_path).read()
    parent = next(iter(state.agents.values()))
    task = next(iter(state.tasks.values()))
    assignment = EphemeralAssignment(
        id="assignment",
        parent_agent_id=parent.id,
        project_id=task.project_id,
        task_id=task.id,
        session_id="ephemeral-session",
        title="Example",
        prompt="Report an outcome",
    )
    state.ephemeral_assignments[assignment.id] = assignment
    for name in ("team.ephemeral.requested", "team.ephemeral.completed"):
        event = _ephemeral_assignment_event_payload(state, assignment.id, event_type=name)
        assert not validate_payload(event_payload_schema(name), event["payload"])


def test_wake_schema_accepts_real_event_and_rejects_non_string_agent_id(tmp_path):
    state = _seed_team(tmp_path).read()
    agent = next(iter(state.agents.values()))
    result = {"events": []}
    _append_agent_wake_requested_event(
        state,
        agent_id=agent.id,
        result=result,
        reason="task_ready",
        now=datetime.now(UTC),
        delay_seconds=0,
    )
    event = result["events"][0]
    schema = event_payload_schema(event["event_type"])
    assert not validate_payload(schema, event["payload"])
    assert validate_payload(schema, {**event["payload"], "agent_id": 3})[0]["path"] == "/agent_id"
