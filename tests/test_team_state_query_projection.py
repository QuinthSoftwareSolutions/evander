from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from evander.events.models import WorkflowNodeEnvelope
from evander.queries.providers.base import QueryRuntimeContext
from evander_team_addon import activity_projections, attention_projections, runtime_projections
from evander_team_addon import provider_queries
from evander_team_addon.models import TeamCase
from evander_team_addon.provider_projection import _filtered_team_state_payload
from evander_team_addon.provider_queries import TeamStateQueryProvider
from evander_team_addon.provider_query_projection import _RECORD_FIELDS
from evander_team_addon.provider_schemas import TEAM_OUTPUT_SCHEMAS, TeamStateQueryDefinition
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.sqlite_store import TeamStateSqliteStore
from test_team_addon_runtime import _multi_team_state


@pytest.fixture
def repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TeamStateRepository:
    instant = datetime(2026, 10, 1, 12, tzinfo=UTC)
    for module in (activity_projections, attention_projections, runtime_projections):
        monkeypatch.setattr(module, "utc_now", lambda: instant)
    repo = TeamStateRepository(tmp_path)
    state = _multi_team_state()
    state.cases["alpha-case"] = TeamCase(
        id="alpha-case", team_id="alpha-team", project_id="alpha-project",
        title="Alpha", owner_agent_id="alpha-dev", task_ids=["alpha-task", "alpha-helper"],
        metadata={"large_context": "x" * 100_000},
    )
    state.cases["beta-case"] = TeamCase(
        id="beta-case", team_id="beta-team", project_id="beta-project",
        title="Beta", owner_agent_id="beta-dev", task_ids=["beta-task"],
    )
    state.tasks["alpha-task"].case_id = "alpha-case"
    state.tasks["alpha-helper"].case_id = "alpha-case"
    state.tasks["beta-task"].case_id = "beta-case"
    repo.store.write_object(state.model_dump(mode="json"))
    return repo


def _query(repository: TeamStateRepository, **config) -> TeamStateQueryProvider:
    provider = TeamStateQueryProvider()
    provider.initialize(
        TeamStateQueryDefinition(
            id="state", display_name="State", type="query:team:state", **config,
        ),
        QueryRuntimeContext(resolved_config={"runtime_dir": str(repository.runtime_dir)}),
    )
    return provider


@pytest.mark.parametrize("include_records", [None, False, True])
def test_default_query_retains_full_scoped_output(repository, include_records) -> None:
    expected = _filtered_team_state_payload(
        repository.read(), team_id=None, project_id="alpha-project", agent_id=None,
        session_records=None,
    )
    result = _query(repository, project_id="alpha-project", include_records=include_records).execute()
    assert result["state"] == expected
    for collection in ("teams", "projects", "agents", "tasks", "cases", "external_source_evidence_snapshots"):
        assert result[collection] == expected[collection]


@pytest.mark.parametrize("scope", [
    {}, {"team_id": "alpha-team"}, {"project_id": "alpha-project"},
    {"agent_id": "alpha-dev"}, {"project_id": "alpha-project", "agent_id": "beta-dev"},
    {"team_id": "beta-team", "project_id": "alpha-project"},
])
def test_compact_fields_match_full_query_scope_and_derived_references(repository, scope) -> None:
    # Exercise every supported public field against the existing scoped boundary,
    # including global assessments and trimmed cross-agent dependencies.
    fields = {collection: sorted(values) for collection, values in _RECORD_FIELDS.items()}
    expected = _query(repository, **scope).execute()["state"]
    actual = _query(repository, **scope, record_fields=fields).execute()
    assert actual["state"] == {
        collection: {
            key: {field: value for field, value in row.items() if field in fields[collection]}
            for key, row in expected[collection].items()
        }
        for collection in fields
    }
    assert actual["teams"] == actual["projects"] == actual["agents"] == {}


def test_compact_query_keeps_global_dependency_truth_without_disclosing_dependency(repository) -> None:
    result = _query(repository, agent_id="alpha-dev", record_fields={
        "tasks": ["id", "dependency_task_ids", "dependency_status", "assessment", "case_id"],
        "cases": ["id", "task_ids"],
    }).execute()["state"]
    assert set(result["tasks"]) == {"alpha-task"}
    task = result["tasks"]["alpha-task"]
    assert task["dependency_task_ids"] == []
    assert task["dependency_status"] == "waiting"
    assert task["assessment"]["unsatisfied_dependency_ids"] == []
    assert task["case_id"] == "alpha-case"
    assert result["cases"]["alpha-case"]["task_ids"] == ["alpha-task"]


def test_raw_projection_skips_unrelated_records_and_session_reads(repository, monkeypatch) -> None:
    loaded = []
    read_projection = TeamStateRepository.read_projection

    def tracked(self, *, collections):
        loaded.append(set(collections))
        return read_projection(self, collections=collections)

    def unexpected(*args, **kwargs):
        raise AssertionError("compact task query hydrated unrelated state")

    monkeypatch.setattr(TeamStateRepository, "read_projection", tracked)
    monkeypatch.setattr(TeamStateRepository, "read", unexpected)
    monkeypatch.setattr(TeamStateSqliteStore, "_read_conversation_messages", unexpected)
    monkeypatch.setattr(TeamStateSqliteStore, "_read_case_context", unexpected)
    monkeypatch.setattr(provider_queries, "_session_repository_from_context", unexpected)
    result = _query(repository, agent_id="alpha-dev", record_fields={"tasks": ["id", "state"]}).execute()
    assert result["tasks"] == {"alpha-task": {"id": "alpha-task", "state": "pending"}}
    assert loaded == [{"teams", "projects", "agents", "project_assignments", "tasks"}]


@pytest.mark.parametrize("value", [
    {}, [], False, {"memories": ["id"]}, {"tasks": ["unknown_field"]},
    {"tasks": []}, {"cases": "id"}, {"cases": [1]},
])
def test_projection_rejects_invalid_literal_and_resolved_values(repository, value) -> None:
    with pytest.raises(ValueError):
        _query(repository, record_fields=value)
    query = _query(repository, record_fields="$(projection)")
    with pytest.raises(ValueError):
        query.execute(resolved_config={"record_fields": value})


def test_envelope_materializes_projection_and_exposes_case_output(repository) -> None:
    provider = TeamStateQueryProvider()
    provider.initialize(
        WorkflowNodeEnvelope(
            id="state", display_name="State", type="query:team:state",
            config={"team_id": "alpha-team", "record_fields": {"cases": ["id", "status"]}},
        ),
        QueryRuntimeContext(resolved_config={"runtime_dir": str(repository.runtime_dir)}),
    )
    result = provider.execute()
    assert result["cases"] == {"alpha-case": {"id": "alpha-case", "status": "open"}}
    assert result["tasks"] == {}
    assert TEAM_OUTPUT_SCHEMAS["query:team:state"]["cases"] == {"type": "object"}
