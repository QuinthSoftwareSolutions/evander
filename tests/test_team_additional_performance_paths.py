from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from evander_team_addon import attention_projections, provider_wake_chat
from evander_team_addon.models import (
    AgentProjectAssignment,
    AgentRuntime,
    AgentTask,
    Deliverable,
    PersistentAgent,
    Project,
    Team,
    TeamDispatch,
    TeamMemoryRecord,
    TeamState,
    TeamTaskState,
    TaskCancellation,
)
from evander_team_addon.services import memory_service
from evander_team_addon.services.domain import project_status, task_assessment

NOW = datetime(2026, 10, 8, 12, tzinfo=UTC)


class CountedRecords(dict):
    def __init__(self, records):
        super().__init__(records)
        self.scans = 0

    def values(self):
        self.scans += 1
        return super().values()


def _memory(record_id, *, project_id=None, agent_id=None, team_id="team", status="active"):
    return TeamMemoryRecord(
        id=record_id,
        team_id=team_id,
        project_id=project_id,
        agent_id=agent_id,
        scope="project" if project_id else "agent_private" if agent_id else "team_global",
        title="Decision",
        summary="Retain the accepted result",
        body="Full evidence",
        status=status,
        created_at=NOW,
        updated_at=NOW,
    )


def _memory_state():
    actor = PersistentAgent(id="actor", team_id="team", role_id="role", session_id="session")
    child = PersistentAgent(
        id="child",
        team_id="team",
        role_id="role",
        session_id="child-session",
        reports_to_agent_id="actor",
    )
    return TeamState.model_construct(
        agents={actor.id: actor, child.id: child},
        teams=CountedRecords({"team": Team(id="team", name="Team", manager_agent_id="manager")}),
        projects={
            "project": Project(id="project", team_id="team", name="Project", lead_agent_id="lead")
        },
        project_assignments={
            "assignment": AgentProjectAssignment(
                id="assignment",
                team_id="team",
                project_id="project",
                agent_id="child",
            )
        },
        memories={
            **{
                f"memory-{i:03d}": _memory(f"memory-{i:03d}", project_id="project")
                for i in range(80)
            },
            "global": _memory("global"),
            "own-private": _memory("own-private", agent_id="actor"),
            "hidden-private": _memory("hidden-private", agent_id="child"),
            "hidden-project": _memory("hidden-project", project_id="other"),
            "hidden-team": _memory("hidden-team", team_id="other"),
            "archived": _memory("archived", status="archived"),
        },
    )


def test_memory_listing_reuses_visibility_and_refreshes_after_scope_changes(monkeypatch):
    state = _memory_state()
    calls = []
    original = memory_service._visible_project_ids

    def visible_projects(snapshot, actor):
        calls.append(actor.id)
        return original(snapshot, actor)

    monkeypatch.setattr(memory_service, "_visible_project_ids", visible_projects)
    actor = state.agents["actor"]
    page = memory_service.list_memory_records(state, actor=actor, limit=2, offset=2)
    assert [row.id for row in page["records"]] == ["memory-078", "memory-077"]
    assert page["total_count"] == 82
    assert page["next_cursor"] == "4"
    assert page["omitted_count"] == 78
    assert calls == [actor.id]
    # Team membership and project membership each resolve once, rather than once per memory.
    assert state.teams.scans == 2

    state.project_assignments.clear()
    refreshed = memory_service.list_memory_records(state, actor=actor)
    assert [row.id for row in refreshed["records"]] == ["own-private", "global"]
    assert calls == [actor.id, actor.id]
    assert state.teams.scans == 4


def test_memory_visibility_skips_unused_project_scope(monkeypatch):
    state = _memory_state()
    state.memories = {"global": _memory("global"), "private": _memory("private", agent_id="actor")}

    def fail(*_args):
        raise AssertionError("project scope is unnecessary for global or private memories")

    monkeypatch.setattr(memory_service, "_visible_project_ids", fail)
    assert memory_service.list_memory_records(state, actor=state.agents["actor"])["total"] == 2
    assert state.teams.scans == 1
    assert memory_service.list_memory_records(state, actor=None)["total"] == 2
    assert state.teams.scans == 1


def test_semantic_memory_candidates_share_canonical_authorization(monkeypatch):
    state = _memory_state()
    points = [
        SimpleNamespace(payload={"memory_id": key}, score=1 - index / 100)
        for index, key in enumerate(
            [
                "missing",
                "hidden-private",
                "hidden-project",
                "archived",
                "memory-001",
                "memory-002",
                "memory-001",
                "global",
            ]
        )
    ]
    client = SimpleNamespace(query_points=lambda *_args, **_kwargs: points)
    monkeypatch.setattr(
        memory_service, "QdrantClient", lambda *_args, **_kwargs: nullcontext(client)
    )
    monkeypatch.setattr(
        memory_service,
        "resolve_settings",
        lambda *_args, **_kwargs: SimpleNamespace(qdrant_base_url="http://synthetic"),
    )
    monkeypatch.setattr(memory_service, "resolve_qdrant_api_key", lambda *_args: None)
    monkeypatch.setattr(
        memory_service,
        "require_collection_exists",
        lambda *_args, **_kwargs: SimpleNamespace(name="memories"),
    )
    monkeypatch.setattr(memory_service, "require_collection_schema", lambda *_args: None)
    monkeypatch.setattr(memory_service, "embed_text", lambda *_args: [0.0])
    monkeypatch.setattr(memory_service, "semantic_output_base", lambda *_args, **_kwargs: {})
    result = memory_service.TeamMemorySemanticService(None).semantic_search(
        state,
        query_text="Decision",
        actor=state.agents["actor"],
        team_id="team",
        limit=20,
    )
    assert [row["id"] for row in result["records"]] == ["memory-001", "memory-002", "global"]
    assert result["records"][0]["score"] == points[4].score
    assert all("body" not in row for row in result["records"])
    assert result["semantic_point_count"] == 8
    assert state.teams.scans == 2


def _wake_state():
    actor = PersistentAgent(id="actor", team_id="team", role_id="role", session_id="session")
    tasks = {
        f"task-{i:02d}": AgentTask(
            id=f"task-{i:02d}",
            project_id=f"project-{i % 2}",
            owner_agent_id=actor.id,
            title=f"Task {i}",
            description="Implement",
            created_by_actor="operator",
            priority=1,
            created_at=NOW,
            updated_at=NOW,
        )
        for i in range(12)
    }
    return TeamState.model_construct(
        agents={actor.id: actor},
        agent_runtimes={actor.id: AgentRuntime(agent_id=actor.id)},
        teams={"team": Team(id="team", name="Team", manager_agent_id="manager")},
        projects={
            f"project-{i}": Project(
                id=f"project-{i}", team_id="team", name=f"Project {i}", lead_agent_id="lead"
            )
            for i in range(2)
        },
        project_assignments={
            f"assignment-{i}": AgentProjectAssignment(
                id=f"assignment-{i}",
                team_id="team",
                project_id=f"project-{i}",
                agent_id=actor.id,
            )
            for i in range(2)
        },
        tasks=tasks,
        dispatches={
            "dispatch": TeamDispatch(
                id="dispatch",
                agent_id=actor.id,
                session_id=actor.session_id,
                cause_kind="team_chat",
                task_ids=list(tasks),
                project_ids=["project-0", "project-1"],
            )
        },
    )


@pytest.mark.parametrize("kind", ["chat", "voice"])
def test_chat_and_voice_contexts_share_task_graph_but_refresh_next_context(monkeypatch, kind):
    state = _wake_state()
    actor = state.agents["actor"]
    builds = []
    original = task_assessment.TaskAssessmentService

    def construct(snapshot):
        builds.append(snapshot)
        return original(snapshot)

    monkeypatch.setattr(task_assessment, "TaskAssessmentService", construct)
    monkeypatch.setattr(project_status, "TaskAssessmentService", construct)
    monkeypatch.setattr(provider_wake_chat, "TaskAssessmentService", construct)

    def context():
        if kind == "chat":
            return provider_wake_chat._team_chat_wake_lease(
                state,
                agent=actor,
                dispatch_id="dispatch",
                session_records={},
                mutation_allowed=False,
            )
        return provider_wake_chat.build_voice_reconciliation_context(
            state,
            agent_id=actor.id,
            session_records={},
        )

    first = context()
    assert builds == [state]
    expected_task_ids = set(state.tasks) if kind == "chat" else set(sorted(state.tasks)[:10])
    assert {row["id"] for row in first["tasks"]} == expected_task_ids
    assert {row["id"] for row in first["projects"]} == set(state.projects)
    if kind == "chat":
        assert all(row["assessment"]["resolution_status"] == "unresolved" for row in first["tasks"])
        assert first["dispatch_id"] == "dispatch"
    state.tasks["task-00"].state = TeamTaskState.CANCELLED
    state.tasks["task-00"].cancellation = TaskCancellation(
        reason_code="work_withdrawn",
        reason="The operator withdrew this work",
        actor_kind="operator",
        actor_id="operator",
        cancelled_at=NOW,
    )
    second = context()
    assert builds == [state, state]
    if kind == "chat":
        row = next(row for row in second["tasks"] if row["id"] == "task-00")
        assert row["assessment"]["resolution_status"] == "resolved"
    else:
        assert "task-00" not in {row["id"] for row in second["tasks"]}


@pytest.mark.parametrize("kind", ["chat", "voice"])
def test_idle_chat_and_voice_contexts_do_not_build_task_graph(monkeypatch, kind):
    state = _wake_state()
    state.tasks.clear()
    state.projects.clear()
    state.project_assignments.clear()

    def fail(_snapshot):
        raise AssertionError("idle context should not rebuild the task graph")

    monkeypatch.setattr(provider_wake_chat, "TaskAssessmentService", fail)
    if kind == "chat":
        result = provider_wake_chat._team_chat_wake_lease(
            state,
            agent=state.agents["actor"],
            dispatch_id="dispatch",
            session_records={},
            mutation_allowed=False,
        )
    else:
        result = provider_wake_chat.build_voice_reconciliation_context(
            state,
            agent_id="actor",
            session_records={},
        )
    assert result.get("tasks", []) == result.get("projects", []) == []


def test_attention_task_links_scan_deliverables_once_and_refresh_per_projection():
    state = _wake_state()
    state.tasks["task-00"].state = TeamTaskState.BLOCKED
    state.tasks["task-01"].dependency_task_ids = ["task-02"]
    state.deliverables = CountedRecords(
        {
            "z-result": Deliverable(
                id="z-result", task_id="task-00", producer_agent_id="actor", summary="Later"
            ),
            "a-result": Deliverable(
                id="a-result", task_id="task-00", producer_agent_id="actor", summary="First"
            ),
            "waiting-result": Deliverable(
                id="waiting-result", task_id="task-01", producer_agent_id="actor", summary="Waiting"
            ),
            **{
                f"unrelated-{i}": Deliverable(
                    id=f"unrelated-{i}",
                    task_id="elsewhere",
                    producer_agent_id="other",
                    summary="Unrelated",
                )
                for i in range(100)
            },
        }
    )
    rows = attention_projections._runtime_attention_payload(state, now=NOW)
    assert {(row["kind"], row["task_id"], row["deliverable_id"]) for row in rows} == {
        ("blocked_task", "task-00", "a-result"),
        ("waiting_dependency_task", "task-01", "waiting-result"),
    }
    assert state.deliverables.scans == 1
    state.deliverables.pop("a-result")
    refreshed = attention_projections._runtime_attention_payload(state, now=NOW)
    assert (
        next(row for row in refreshed if row["task_id"] == "task-00")["deliverable_id"]
        == "z-result"
    )
    assert state.deliverables.scans == 2
    state.tasks["task-00"].state = TeamTaskState.PENDING
    state.tasks["task-01"].dependency_task_ids = []
    assert attention_projections._runtime_attention_payload(state, now=NOW) == []
    assert state.deliverables.scans == 2
