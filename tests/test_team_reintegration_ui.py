import subprocess

from team_ui_controller_harness import controller_script


def test_reintegration_dialog_fences_preview_races_and_reuses_submission_request():
    script = r"""
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const API_BASE = "/api";
const state = {tasks: {}};
const guardTeamMutation = () => true;
const escapeHtml = String, escapeAttr = String;
const responseText = async response => response.error;
const applyMutationResultPayload = async () => {};
const setStatus = () => {};
const elements = {};
const dialog = {
  open: false, addEventListener() {}, remove() {},
  showModal() { this.open = true; }, close() { this.open = false; },
  querySelector(key) { return elements[key] ||= {}; },
};
const document = { createElement: () => dialog, body: {append() {}} };
const pending = [], submitted = [];
let requests = 0;
let failSubmit = true;
const preview = owner => ({base_branch: "trunk", base_commit_sha: "a".repeat(40),
  owner_agent_id: owner, eligible_owners: [{id: owner, name: "Owner"}],
  contributions: [{title: "Accepted work"}], instructions: "Merge exactly these inputs",
  retiring_task_ids: [], retire_source_release: false});
const response = owner => ({ok: true, json: async () => ({preview: preview(owner)})});
const teamFetch = async (url, options) => {
  const body = JSON.parse(options.body);
  if (body.action === "submit") {
    submitted.push(body);
    if (failSubmit) return {ok: false, text: async () => "Network failed after commit"};
    return {ok: true, json: async () => ({})};
  }
  if (requests++ === 0) return response("initial");
  return new Promise(resolve => pending.push({resolve, owner: body.owner_agent_id}));
};
const tick = () => new Promise(resolve => setImmediate(resolve));
"""
    script += "// CONTROLLER"
    script += r"""
(async () => {
  await previewReleaseReintegration("release");
  assert.equal(elements["[data-submit-preview]"].disabled, false);
  elements["[data-preview-owner]"].onchange({target: {value: "slow"}});
  elements["[data-preview-owner]"].onchange({target: {value: "newest"}});
  assert.equal(elements["[data-submit-preview]"].disabled, true);
  pending[1].resolve(response("newest"));
  await tick();
  pending[0].resolve(response("slow"));
  await tick();
  await elements["[data-submit-preview]"].onclick();
  assert.equal(submitted[0].preview.owner_agent_id, "newest");
  failSubmit = false;
  await elements["[data-submit-preview]"].onclick();
  assert.equal(submitted[0].request_id, submitted[1].request_id);
  assert.equal(dialog.open, false);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    script = controller_script(
        "recovery",
        script,
        "{access: {guardTeamMutation, teamFetch}, mutations: {applyMutationResultPayload}, app: {state, setStatus}}",
        ["previewReleaseReintegration"],
    )
    result = subprocess.run(
        ["node", "--input-type=commonjs"], input=script, text=True, capture_output=True, timeout=15
    )
    assert result.returncode == 0, result.stderr
