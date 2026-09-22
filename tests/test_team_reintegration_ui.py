from pathlib import Path
import subprocess


def test_reintegration_dialog_fences_preview_races_and_reuses_submission_request():
    source = (
        Path(__file__).resolve().parents[1] / "src/evander_team_addon/ui_static/team-ui.js"
    ).read_text()
    function = source[
        source.index("async function previewReleaseReintegration(") : source.index(
            "async function startDependencyCompositionRecovery("
        )
    ]
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
    if (failSubmit) return {ok: false, error: "Network failed after commit"};
    return {ok: true, json: async () => ({})};
  }
  if (requests++ === 0) return response("initial");
  return new Promise(resolve => pending.push({resolve, owner: body.owner_agent_id}));
};
const tick = () => new Promise(resolve => setImmediate(resolve));
"""
    script += function
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
    result = subprocess.run(
        ["node", "--input-type=commonjs"], input=script, text=True, capture_output=True, timeout=15
    )
    assert result.returncode == 0, result.stderr


def test_generation_history_and_exception_labels_are_visible():
    source = (
        Path(__file__).resolve().parents[1] / "src/evander_team_addon/ui_static/team-ui.js"
    ).read_text()
    assert "Accepted with baseline exceptions" in source
    assert "Interrupted workspace generation" in source
    assert "Last recorded progress" in source
    assert "Read retained log" in source
    assert "Verifier toolchain identity" in source
