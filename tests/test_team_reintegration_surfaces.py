import asyncio
import json

import pytest

from evander.addons import AddonContext
from evander_team_addon import api, mcp
from evander_team_addon.services.reintegration_service import TeamReintegrationService
from test_team_reintegration_service import setup_service
from test_team_addon_api import _HumanApiAuthStub, _json_route_request
from test_team_addon_mcp import (
    _runtime,
    _upsert_session,
    _lease_agent,
    _make_request_context,
    _call_tool_with_context,
    create_server,
)


@pytest.mark.parametrize("owner,privileged", [(False, True), (True, False), (True, True)])
def test_reintegration_preview_api_requires_owner_and_privileged_unlock(
    tmp_path, monkeypatch, owner, privileged
):
    repo, service, _ = setup_service(tmp_path)
    calls = []

    def preview(_self, *args, **kwargs):
        calls.append(args)
        return service.preview(*args, **kwargs)

    monkeypatch.setattr(TeamReintegrationService, "preview", preview)
    # Avoid recursive invocation after patching the method.
    original_preview = service._preview
    service.preview = lambda release_id, **kwargs: original_preview(
        repo.read(), release_id, owner_agent_id=kwargs.get("owner_agent_id")
    )
    auth = _HumanApiAuthStub(login="operator@example.com", owner=owner, privileged=privileged)
    routes = api.admin_api_routes(
        AddonContext(
            addon_id="team-addon",
            runtime_dir=tmp_path,
            addon_runtime_dir=tmp_path / "addons/team-addon",
            auth=auth,
        )
    )
    endpoint = next(r.endpoint for r in routes if r.path.endswith("/reintegration"))
    request = _json_route_request(
        method="POST",
        path="/release-packages/release-1/reintegration",
        path_params={"release_package_id": "release-1"},
        payload={"action": "preview"},
    )
    response = asyncio.run(endpoint(request))
    assert response.status_code == (200 if owner and privileged else 403)
    assert bool(calls) == (owner and privileged)


@pytest.mark.parametrize("privileged", [False, True])
def test_command_blocked_gate_api_requires_unlocked_operator_retry(tmp_path, privileged):
    from test_team_addon_git_effects import _seed_team, _seed_release_verification
    from test_team_verification_recovery import run_structured, RESULT_PREFIX

    repo = _seed_team(tmp_path)
    _seed_release_verification(repo)
    run, _ = run_structured(repo, {
        "success": True, "exit_code": 0,
        "stdout": RESULT_PREFIX + json.dumps({
            "schema_version": 1, "outcome": "failed", "explanation": "Check prerequisites",
            "candidate_commit_sha": "candidate-sha",
        }),
    })
    saved = repo.read()
    routes = api.admin_api_routes(AddonContext(
        addon_id="team-addon", runtime_dir=tmp_path,
        addon_runtime_dir=tmp_path / "addons/team-addon",
        auth=_HumanApiAuthStub(login="operator@example.com", owner=True, privileged=privileged),
    ))
    endpoint = next(r.endpoint for r in routes if r.path.endswith("/retry-gate"))
    request = _json_route_request(
        method="POST", path="/release-packages/release-1/retry-gate",
        path_params={"release_package_id": "release-1"},
        payload={
            "gate_kind": "verification", "gate_id": run.id,
            "reason": "Prerequisites repaired", "expected_revision": saved.revision,
        },
    )
    response = asyncio.run(endpoint(request))
    assert response.status_code == (200 if privileged else 403)
    after = repo.read()
    if privileged:
        release = after.release_packages["release-1"]
        assert release.state == "pending"
        assert release.gate_failure is None
        new_run = after.release_verification_runs[release.verification_run_ids[-1]]
        assert new_run.supersedes_verification_run_id == run.id
        assert new_run.candidate_commit_sha == run.candidate_commit_sha
    else:
        assert after.revision == saved.revision


def test_mcp_preview_submit_and_replay_use_shared_service(tmp_path, monkeypatch):
    name = mcp.TEAM_REINTEGRATE_RELEASE_TOOL
    context = _runtime(tmp_path, allowed_tools=[])
    _upsert_session(context, session_id="session-lead", allowed_tools=[name])
    repo, _, managed = setup_service(context.config.runtime_dir)
    _lease_agent(repo, agent_id="lead", dispatch_id="lead-dispatch")
    monkeypatch.setattr(mcp, "_team_mcp_managed_git_admin", lambda context: managed)
    monkeypatch.setattr(
        mcp, "request_team_outbox_publication_for_records", lambda *args, **kwargs: None
    )
    server = create_server(context)
    caller = _make_request_context(session_id="session-lead")
    result = _call_tool_with_context(server, name, {"release_package_id": "release-1"}, caller)
    preview = result["preview"]
    submitted = _call_tool_with_context(
        server,
        name,
        {
            "release_package_id": "release-1",
            "action": "submit",
            "preview": preview,
            "request_id": "mcp-integration",
        },
        caller,
    )
    assert submitted["task_id"] in repo.read().tasks
    replay = _call_tool_with_context(
        server,
        name,
        {
            "release_package_id": "release-1",
            "action": "submit",
            "preview": preview,
            "request_id": "mcp-integration",
        },
        caller,
    )
    assert replay["task_id"] == submitted["task_id"]
    assert len(repo.read().tasks) == 2


def test_mcp_reintegration_rejects_unrelated_dispatch(tmp_path, monkeypatch):
    name = mcp.TEAM_REINTEGRATE_RELEASE_TOOL
    context = _runtime(tmp_path, allowed_tools=[])
    _upsert_session(context, session_id="session-lead", allowed_tools=[name])
    repo, _, managed = setup_service(context.config.runtime_dir)
    _lease_agent(repo, agent_id="lead", dispatch_id="lead-dispatch")

    def descope(state):
        state.dispatches["lead-dispatch"].project_ids = []
        return state

    repo.update(descope)
    monkeypatch.setattr(mcp, "_team_mcp_managed_git_admin", lambda context: managed)
    server = create_server(context)
    with pytest.raises(Exception, match="outside.*dispatch scope"):
        _call_tool_with_context(
            server,
            name,
            {"release_package_id": "release-1"},
            _make_request_context(session_id="session-lead"),
        )


def test_retained_log_api_includes_interrupted_history_and_rejects_other_jobs(tmp_path):
    from evander_team_addon.models import ReleaseVerificationRun
    from evander_team_addon.services.verification_logs import retain_verification_log

    repo, _, _ = setup_service(tmp_path)

    def archive(state):
        state.release_packages["release-1"].verification_run_ids = ["run-1"]
        state.release_verification_runs["run-1"] = ReleaseVerificationRun(
            id="run-1",
            release_package_id="release-1",
            candidate_commit_sha="a" * 40,
            session_id="deleted-session",
            generation_history=[{"generation": 1, "command_results": [{"job_id": "old-job"}]}],
        )
        return state

    repo.update(archive)
    retain_verification_log(tmp_path, job_id="old-job", text="retained output", retention_days=1)
    auth = _HumanApiAuthStub(login="operator@example.com", owner=True)
    routes = api.admin_api_routes(
        AddonContext(
            addon_id="team-addon",
            runtime_dir=tmp_path,
            addon_runtime_dir=tmp_path / "addons/team-addon",
            auth=auth,
        )
    )
    endpoint = next(r.endpoint for r in routes if r.path.endswith("/verification-live"))
    for job_id, expected in [("old-job", 200), ("unrelated-job", 404)]:
        request = _json_route_request(
            method="GET",
            path="/release-packages/release-1/verification-live",
            path_params={"release_package_id": "release-1"},
            payload={},
        )
        request.scope["query_string"] = f"job_id={job_id}".encode()
        response = asyncio.run(endpoint(request))
        assert response.status_code == expected
        if expected == 200:
            assert json.loads(response.body)["log"]["text"] == "retained output"
