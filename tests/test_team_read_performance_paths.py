from __future__ import annotations

from datetime import UTC, datetime

import pytest

from evander_team_addon.models import (
    AgentInboxItem,
    AgentProjectAssignment,
    AgentTask,
    Deliverable,
    PersistentAgent,
    Project,
    ReleasePackage,
    ReviewRequest,
    Team,
    TeamBlocker,
    TeamCase,
    TeamState,
)
from evander_team_addon.services.read_service import TeamReadService

NOW = datetime(2026, 10, 7, 12, tzinfo=UTC)


class CountedRecords(dict):
    def __init__(self, records):
        super().__init__(records)
        self.scans = 0
        self.lookups = 0

    def values(self):
        self.scans += 1
        return super().values()

    def get(self, *args):
        self.lookups += 1
        return super().get(*args)


def _state() -> TeamState:
    actor = PersistentAgent(id="zz-actor", team_id="team", role_id="role", session_id="session")
    return TeamState.model_construct(
        agents={actor.id: actor},
        teams={"team": Team(id="team", name="Team", manager_agent_id=actor.id)},
        projects={"project": Project(
            id="project", team_id="team", name="Project", lead_agent_id=actor.id,
        )},
    )


def _record(kind, record_id, *, hidden=False):
    team_id = "hidden" if hidden else "team"
    project_id = "hidden" if hidden else "project"
    agent_id = "hidden" if hidden else "zz-actor"
    common = {"id": record_id, "created_at": NOW}
    if kind == "inbox":
        return AgentInboxItem(
            **common, recipient_agent_id=agent_id, item_type="question",
            payload={"message": "Review the result"}, updated_at=NOW,
        )
    if kind == "blockers":
        return TeamBlocker(
            **common, team_id=team_id, project_id=project_id, source_kind="task",
            source_id=record_id, owner_agent_id=agent_id,
            summary="Waiting on review", updated_at=NOW,
        )
    if kind == "cases":
        return TeamCase(
            **common, team_id=team_id, project_id=project_id,
            title="Investigate the result", updated_at=NOW,
        )
    if kind == "deliverables":
        return Deliverable(
            **common, task_id=record_id, producer_agent_id=agent_id, summary="Result ready",
        )
    if kind == "review_requests":
        return ReviewRequest(
            **common, deliverable_id=record_id, reviewer_agent_id=agent_id,
            instructions="Review the result",
        )
    if kind == "release_packages":
        return ReleasePackage(**common, project_id=project_id, summary="Result ready")
    return PersistentAgent(
        id=record_id, team_id=team_id, role_id="role", session_id=f"session-{record_id}",
        display_name="Reviewer",
    )


@pytest.mark.parametrize(("collection", "projection", "field", "expected"), [
    ("inbox", "_inbox_row", "message", "Review the result"),
    ("blockers", "_blocker_row", "event_count", 0),
    ("cases", "_case_row", "title", "Investigate the result"),
    ("deliverables", "_deliverable_row", "summary", "Result ready"),
    ("review_requests", "_review_request_row", "instructions", "Review the result"),
    ("release_packages", "_release_row", "summary", "Result ready"),
    ("agents", "_agent_row", "display_name", "Reviewer"),
])
def test_collection_pages_project_only_visible_requested_rows(
    monkeypatch, collection, projection, field, expected,
) -> None:
    state = _state()
    records = getattr(state, collection)
    for index in range(80):
        record_id = f"record-{index:03d}"
        records[record_id] = _record(collection, record_id)
    records["00-hidden"] = _record(collection, "00-hidden", hidden=True)
    service = TeamReadService(state=state, actor=state.agents["zz-actor"])
    projected = []
    original = getattr(service, projection)

    def project(record):
        projected.append(record.id)
        return original(record)

    monkeypatch.setattr(service, projection, project)
    read = getattr(service, f"list_{collection}")
    page = read(limit=2, cursor="3")
    assert [row["id"] for row in page["records"]] == ["record-003", "record-004"]
    assert all(row[field] == expected for row in page["records"])
    assert projected == ["record-003", "record-004"]
    total = 81 if collection == "agents" else 80
    assert page["total"] == page["total_count"] == total
    assert page["count"] == 2
    assert page["offset"] == 3
    assert page["next_cursor"] == "5"
    assert page["omitted_count"] == total - 5
    assert page["has_more"] is True
    assert read(limit=2, offset=100)["records"] == []
    assert len(projected) == 2
    assert read(limit=100)["count"] == 50
    assert len(projected) == 52


def test_reporting_visibility_and_assignment_reads_scale_linearly() -> None:
    actor = PersistentAgent(id="actor", team_id="team", role_id="role", session_id="session")
    agents = {actor.id: actor}
    projects = {}
    assignments = {}
    previous = actor.id
    for index in range(200):
        agent_id = f"agent-{index:03d}"
        project_id = f"project-{index:03d}"
        agents[agent_id] = PersistentAgent(
            id=agent_id, team_id="other", role_id="role", session_id=agent_id,
            reports_to_agent_id=previous,
        )
        projects[project_id] = Project(
            id=project_id, team_id="other", name=project_id, lead_agent_id=agent_id,
        )
        assignments[agent_id] = AgentProjectAssignment(
            id=agent_id, team_id="other", project_id=project_id, agent_id=agent_id,
        )
        previous = agent_id
    state = TeamState.model_construct(
        agents=CountedRecords(agents), projects=projects,
        project_assignments=CountedRecords(assignments),
    )
    service = TeamReadService(state=state, actor=actor)
    page = service.list_projects(limit=2)
    assert [row["id"] for row in page["records"]] == ["project-000", "project-001"]
    assert page["total"] == 200
    assert service.list_agents()["total"] == 1
    assert state.project_assignments.scans == 1
    assert state.agents.lookups < 800


def test_visibility_keeps_explicit_and_reporting_graphs_separate_with_cycles() -> None:
    actor = PersistentAgent(
        id="actor", team_id="team", role_id="role", session_id="session",
        subordinate_agent_ids=["explicit", "missing"],
        reports_to_agent_id="reports-child",
    )
    agents = {actor.id: actor}
    definitions = {
        "explicit": {"subordinate_agent_ids": ["explicit-child"]},
        "explicit-child": {"subordinate_agent_ids": ["explicit"]},
        "reports-child": {"reports_to_agent_id": "actor"},
        "reports-grandchild": {"reports_to_agent_id": "reports-child"},
        "mixed-hidden": {"reports_to_agent_id": "explicit-child"},
        "cycle-hidden-a": {"reports_to_agent_id": "cycle-hidden-b"},
        "cycle-hidden-b": {"reports_to_agent_id": "cycle-hidden-a"},
    }
    for agent_id, fields in definitions.items():
        agents[agent_id] = PersistentAgent(
            id=agent_id, team_id="other", role_id="role", session_id=agent_id, **fields,
        )
    state = TeamState.model_construct(
        agents=agents,
        projects={key: Project(
            id=key, team_id="other", name=key, lead_agent_id=key,
        ) for key in definitions},
        project_assignments={key: AgentProjectAssignment(
            id=key, team_id="other", project_id=key, agent_id=key,
        ) for key in definitions},
    )
    service = TeamReadService(state=state, actor=actor)
    assert [row["id"] for row in service.list_projects()["records"]] == [
        "explicit", "explicit-child", "reports-child", "reports-grandchild",
    ]
    assert service.get_project("mixed-hidden") is None
    assert service.get_project("cycle-hidden-a") is None


def test_supervisory_overview_scans_each_collection_once_and_retains_counts() -> None:
    state = _state()
    state.projects = {f"project-{index:03d}": Project(
        id=f"project-{index:03d}", team_id="team", name=f"Project {index}",
        lead_agent_id="zz-actor",
    ) for index in range(30)}
    state.projects["00-hidden"] = Project(
        id="00-hidden", team_id="other", name="Hidden", lead_agent_id="other",
    )
    state.tasks = CountedRecords({f"task-{index}": AgentTask(
        id=f"task-{index}", project_id=f"project-{index % 30:03d}",
        owner_agent_id="zz-actor", priority=1,
        title="Implement", description="Work", created_by_actor="operator",
        state="finished" if index >= 30 else "pending",
    ) for index in range(60)})
    state.blockers = CountedRecords({f"blocker-{index}": TeamBlocker(
        id=f"blocker-{index}", team_id="team", project_id=f"project-{index % 30:03d}",
        owner_agent_id="zz-actor",
        source_kind="task", source_id=f"task-{index}", summary="Waiting",
        state="resolved" if index >= 30 else "open",
        route="resolved" if index >= 30 else "owned",
        resolved_at=NOW if index >= 30 else None,
    ) for index in range(60)})
    state.release_packages = CountedRecords({f"release-{index}": ReleasePackage(
        id=f"release-{index}", project_id=f"project-{index:03d}",
    ) for index in range(30)})
    service = TeamReadService(state=state, actor=state.agents["zz-actor"])
    overview = service.supervisory_overview()
    assert overview["project_count"] == 30
    assert overview["omitted_project_count"] == 22
    assert [row["id"] for row in overview["projects"]] == [
        f"project-{index:03d}" for index in range(8)
    ]
    for row in overview["projects"]:
        assert row["task_counts"] == {"pending": 1, "finished": 1}
        assert row["open_blocker_count"] == 1
        assert row["release_counts"] == {"pending": 1}
    assert state.tasks.scans == state.blockers.scans == state.release_packages.scans == 1
