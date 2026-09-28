"""Recovery must remain incident-scoped through the actual API and MCP adapters."""

import asyncio
import json

import pytest

from evander.addons import AddonContext
from evander_team_addon import api, mcp
from evander_team_addon.repository import TeamStateRepository
from evander_team_addon.services.git_recovery_service import TeamGitRecoveryService
from test_team_autonomous_git_recovery import setup_recovery, lease_blocker
from test_team_addon_api import _HumanApiAuthStub, _json_route_request
from test_team_addon_mcp import (
    _runtime,
    _upsert_session,
    _make_request_context,
    _call_tool_with_context,
    create_server,
)


@pytest.mark.parametrize("owner,privileged", [(False, True), (True, False), (True, True)])
def test_recovery_api_requires_directory_owner_and_privileged_unlock(
    tmp_path, monkeypatch, owner, privileged
):
    repo, service, blocker_id = setup_recovery(tmp_path)
    calls = []
    original = service.preview

    def preview(_self, *args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(TeamGitRecoveryService, "preview", preview)
    routes = api.admin_api_routes(
        AddonContext(
            addon_id="team-addon",
            runtime_dir=tmp_path / "team",
            addon_runtime_dir=tmp_path / "team/addons/team-addon",
            auth=_HumanApiAuthStub(
                login="operator@example.invalid", owner=owner, privileged=privileged
            ),
        )
    )
    endpoint = next(route.endpoint for route in routes if route.path.endswith("/git-recovery"))
    request = _json_route_request(
        method="POST",
        path=f"/blockers/{blocker_id}/git-recovery",
        path_params={"blocker_id": blocker_id},
        payload={"action": "preview"},
    )
    before = repo.read().revision
    response = asyncio.run(endpoint(request))
    assert response.status_code == (200 if owner and privileged else 403)
    assert bool(calls) == (owner and privileged)
    assert repo.read().revision == before
    if owner and privileged:
        assert json.loads(response.body)["preview"]["actions"][0]["id"] == "reprepare"


def test_mcp_recovery_preview_apply_and_transport_replay_from_blocker_only_dispatch(
    tmp_path, monkeypatch
):
    names = [mcp.TEAM_PREVIEW_GIT_RECOVERY_TOOL, mcp.TEAM_APPLY_GIT_RECOVERY_TOOL]
    context = _runtime(tmp_path, allowed_tools=[])
    _upsert_session(context, session_id="session-lead", allowed_tools=names)
    seeded, service, blocker_id = setup_recovery(tmp_path / "fixture")
    repo = TeamStateRepository(context.config.runtime_dir)
    repo.update(lambda _: seeded.read())
    lease_blocker(repo, blocker_id)
    monkeypatch.setattr(
        mcp, "_team_mcp_managed_git_admin", lambda _: service.admin._managed_git_admin
    )
    monkeypatch.setattr(
        mcp, "request_team_outbox_publication_for_records", lambda *args, **kwargs: None
    )
    server = create_server(context)
    caller = _make_request_context(session_id="session-lead")
    preview = _call_tool_with_context(server, names[0], {"blocker_id": blocker_id}, caller)[
        "preview"
    ]
    arguments = {
        "blocker_id": blocker_id,
        "preview_token": preview["preview_token"],
        "action_id": "reprepare",
        "request_id": "mcp-recovery",
    }
    result = _call_tool_with_context(server, names[1], arguments, caller)
    replay = _call_tool_with_context(server, names[1], arguments, caller)
    assert result["success"], result
    assert replay["success"] and replay["incident_id"] == result["incident_id"]
    state = repo.read()
    incident = state.git_recovery_incidents[result["incident_id"]]
    assert incident.preparation_retries == 1
    dispatch = state.dispatches["recovery-dispatch"]
    assert dispatch.task_ids == dispatch.project_ids == dispatch.managed_repo_ids == []
    assert len(repo.list_outbox_records(intent_type="team.agent.wake_requested")) == 1

    repo.update(lambda state: setattr(state.dispatches[dispatch.id], "blocker_ids", []))
    with pytest.raises(Exception, match="outside.*dispatch"):
        _call_tool_with_context(server, names[0], {"blocker_id": blocker_id}, caller)


def test_recovery_dialog_fences_stale_previews_and_reuses_request_on_retry():
    from pathlib import Path
    import subprocess

    source = (
        Path(__file__).resolve().parents[1] / "src/evander_team_addon/ui_static/team-ui.js"
    ).read_text()
    function = source[
        source.index("async function previewGitRecovery(") : source.index(
            "function renderRecordBlockers("
        )
    ]
    script = r"""
const assert = require("node:assert/strict"), crypto = require("node:crypto");
const API_BASE = "/api", guardTeamMutation = () => true, text = String, escapeHtml = String;
const responseText = async response => response.error;
const applyMutationResultPayload = async () => {}, setStatus = () => {};
const elements = {};
const dialog = {open: false, addEventListener() {}, remove() {},
 showModal() {this.open = true;}, close() {this.open = false;},
 querySelector(key) {return elements[key] ||= {};}};
const document = {createElement: () => dialog, body: {append() {}}};
const pending = [], submitted = [];
let calls = 0, fail = true;
const response = token => ({ok: true, json: async () => ({preview: {
 preview_token: token, cause: "workspace_missing", actions: [{id: "reprepare"}],
 inputs: {base_commit_sha: "a".repeat(40)}, preparation_retries: 0, candidate_generations: 0,
 git_points: {composed_start: "b".repeat(40), candidate: "c".repeat(40), observed_destination: "d".repeat(40)}
}})});
const teamFetch = async (url, options) => {
 const body = JSON.parse(options.body);
 if (body.action === "apply") {
   submitted.push(body);
   return fail ? {ok: false, error: "Response lost"} : {ok: true, json: async () => ({})};
 }
 if (calls++ === 0) return response("initial");
 return new Promise(resolve => pending.push(resolve));
};
const tick = () => new Promise(resolve => setImmediate(resolve));
"""
    script += function
    script += r"""
(async () => {
 await previewGitRecovery("blocker");
 assert.match(elements["[data-recovery-content]"].innerHTML, /Composed starting commit/);
 assert.match(elements["[data-recovery-content]"].innerHTML, /Observed publication destination/);
 const refresh = elements["[data-recovery-refresh]"], apply = elements["[data-recovery-apply]"];
 refresh.onclick(); refresh.onclick();
 assert.equal(apply.disabled, true);
 pending[1](response("latest")); await tick();
 pending[0](response("stale")); await tick();
 await apply.onclick();
 assert.equal(submitted[0].preview_token, "latest");
 fail = false; await apply.onclick();
 assert.equal(submitted[0].request_id, submitted[1].request_id);
 assert.equal(dialog.open, false);
})().catch(error => {console.error(error); process.exitCode = 1;});
"""
    result = subprocess.run(
        ["node", "--input-type=commonjs"], input=script, text=True, capture_output=True, timeout=15
    )
    assert result.returncode == 0, result.stderr
