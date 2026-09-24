from __future__ import annotations

from evander_team_addon import api_projection, api_authorization

import asyncio
import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from evander.addons import AddonContext
from evander.config import GatewayConfig
from evander_team_addon import (
    api,
    runtime_projections,
    attention_projections,
    activity_projections,
)
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.services.admin_service import TeamAdminService
from evander_team_addon.services.chat_service import TeamChatService
from evander_team_addon.sqlite_store import TeamStateReadProjection
from test_team_addon_api import _seed_runtime_operations_state


def _request(view: str, query: bytes = b"") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": f"/views/{view}",
            "path_params": {"view": view},
            "query_string": query,
            "headers": [],
        }
    )


@pytest.fixture
def repository(tmp_path: Path) -> TeamStateRepository:
    return _seed_runtime_operations_state(tmp_path / "runtime")


@pytest.mark.parametrize("view", api.TEAM_VIEW_IDS)
def test_view_projection_matches_full_state(
    repository: TeamStateRepository,
    view: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instant = datetime(2026, 9, 22, 12, tzinfo=UTC)
    for module in (api_projection, runtime_projections, attention_projections, activity_projections):
        monkeypatch.setattr(module, "utc_now", lambda: instant)
    full_state = repository.read()
    projection = repository.read_projection(collections=sorted(api._view_read_collections(view)))
    request = _request(view)
    assert api._view_payload_for_request(None, request, projection, view=view) == (
        api._view_payload_for_request(None, request, full_state, view=view)
    )


def test_projection_skips_unrelated_tables_and_cannot_be_persisted(
    repository: TeamStateRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = repository.read().model_dump(mode="json")

    def unexpected(*_args):
        raise AssertionError("projection hydrated unrelated history")

    monkeypatch.setattr(repository.store, "_read_case_context", unexpected)
    monkeypatch.setattr(repository.store, "_read_conversation_messages", unexpected)
    projection = repository.read_projection(collections=["teams", "agents"])
    assert isinstance(projection, TeamStateReadProjection)
    assert set(projection.agents) == set(before["agents"])
    with pytest.raises(ValueError, match="did not load collection 'tasks'"):
        _ = projection.tasks
    monkeypatch.undo()
    with pytest.raises(ValueError, match="projections cannot be persisted"):
        repository.update(lambda _state: projection)
    with pytest.raises(ValueError, match="projections cannot be persisted"):
        repository.commit_prepared_state_with_command_transaction(
            previous_payload=before,
            candidate=projection,
            command_writer=lambda _tx: None,
        )
    assert repository.read().model_dump(mode="json") == before


def test_projection_keeps_one_revision_during_concurrent_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = TeamStateRepository(tmp_path / "runtime")
    writer = TeamStateRepository(tmp_path / "runtime")
    TeamAdminService(repository).create_team(
        team_id="team",
        name="Team",
        manager_agent_id="manager",
        manager_session_id="manager",
    )
    expected = repository.read()
    read_teams = repository.store._read_teams

    def read_then_commit(connection, payload):
        read_teams(connection, payload)
        TeamAdminService(writer).create_project(
            project_id="new",
            team_id="team",
            name="New",
            lead_agent_id="lead",
            lead_session_id="lead",
        )

    monkeypatch.setattr(repository.store, "_read_teams", read_then_commit)
    projection = repository.read_projection(collections=["teams", "agents", "projects"])
    assert projection.revision == expected.revision
    assert projection.teams == expected.teams
    assert projection.agents == expected.agents
    assert projection.projects == expected.projects
    assert writer.read_revision() > projection.revision


def test_bootstrap_projection_matches_full_state(
    repository: TeamStateRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api_projection, "utc_now", lambda: datetime(2026, 9, 22, tzinfo=UTC))
    projection = repository.read_projection(collections=sorted(api._BOOTSTRAP_COLLECTIONS))
    request = _request("bootstrap")
    assert api._bootstrap_payload_for_request(None, request, projection) == (
        api._bootstrap_payload_for_request(None, request, repository.read())
    )


def test_chat_projection_retains_operator_scope_and_mutation_authorization(
    repository: TeamStateRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api_projection, "utc_now", lambda: datetime(2026, 9, 22, tzinfo=UTC))
    monkeypatch.setattr(api_authorization, "request_has_privileged_operations", lambda **_kwargs: True)
    for operator in ("admin", "other"):
        TeamChatService(repository).create_or_get_conversation(
            agent_id="backend",
            operator_user_id=operator,
            operator_display_name=operator,
            created_by=f"operator:{operator}",
        )
    full_state = repository.read()
    projection = repository.read_projection(collections=sorted(api._view_read_collections("chat")))
    auth = SimpleNamespace(
        read_session=lambda _request: {"login": "admin"},
        can=lambda *_args, **_kwargs: True,
    )
    sessions = SimpleNamespace(get_session=lambda _id: SimpleNamespace(directory_path="root"))
    for query in (b"", b"operator_user_id=other"):
        request = _request("chat", query)
        expected = api._view_payload_for_request(
            auth,
            request,
            full_state,
            view="chat",
            session_repository=sessions,
        )
        actual = api._view_payload_for_request(
            auth,
            request,
            projection,
            view="chat",
            session_repository=sessions,
        )
        assert actual == expected
        conversations = actual["data"]["chat"]["conversations"]
        assert len(conversations) == 1
        assert conversations[0]["mutation_mode"] == ("reply_only" if query else "project_scoped")
    with pytest.raises(PermissionError, match="Permission required"):
        api._view_payload_for_request(
            None,
            _request("chat", b"operator_user_id=other"),
            projection,
            view="chat",
        )


@pytest.mark.parametrize("view", ["people", "chat", "bootstrap"])
def test_view_read_projection_and_response_encoding_run_off_event_loop(
    repository: TeamStateRepository,
    view: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    threads: list[int] = []
    read_projection = TeamStateRepository.read_projection
    render = api.TeamJSONResponse.render

    def tracked_read(self, **kwargs):
        threads.append(threading.get_ident())
        return read_projection(self, **kwargs)

    def tracked_render(self, content):
        threads.append(threading.get_ident())
        return render(self, content)

    monkeypatch.setattr(TeamStateRepository, "read_projection", tracked_read)
    monkeypatch.setattr(api.TeamJSONResponse, "render", tracked_render)
    context = AddonContext(
        addon_id="team-addon",
        runtime_dir=repository.runtime_dir,
        addon_runtime_dir=repository.runtime_dir / "addons" / "team-addon",
        project_dir=repository.runtime_dir.parent,
        config=GatewayConfig(runtime_dir=repository.runtime_dir),
    )
    route_path = "/bootstrap" if view == "bootstrap" else "/views/{view}"
    endpoint = next(
        route.endpoint for route in api.admin_api_routes(context) if route.path == route_path
    )

    async def invoke():
        event_loop_thread = threading.get_ident()
        response = await endpoint(_request(view))
        assert response.status_code == 200, json.loads(response.body)
        return event_loop_thread

    event_loop_thread = asyncio.run(invoke())
    assert len(threads) == 2
    assert all(thread != event_loop_thread for thread in threads)
