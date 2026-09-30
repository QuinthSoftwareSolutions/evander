import shutil
import subprocess

import pytest

from team_ui_controller_harness import controller_script


def test_cost_refresh_coalesces_events_and_keeps_deferred_and_explicit_updates():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to exercise Team UI refresh scheduling")
    script = (
        r"""
const assert = require("node:assert/strict");
let now = 100000;
Date.now = () => now;
let timerId = 0;
const timers = new Map();
const window = {
  setTimeout(callback, delay) { const id = ++timerId; timers.set(id, { callback, delay }); return id; },
  clearTimeout(id) { timers.delete(id); },
};
const COSTS_REFRESH_INTERVAL_MS = 15000;
const pending = [];
function teamFetch(url) { return new Promise(resolve => pending.push({url, resolve})); }
const API_BASE = "/team";
let costState = {};
const activeTeamView = "team";
const teamViewUsesCostState = () => true;
const normalizeCosts = payload => payload;
const emptyCostState = () => ({});
const captureFormDrafts = () => ({});
const restoreFormDrafts = () => {};
const render = () => {};
const setStatus = () => {};
const responseText = async response => "request failed";
function finish(index, payload) { pending[index].resolve({ok: true, json: async () => payload}); }
async function tick() { for (let i = 0; i < 8; i++) await Promise.resolve(); }
"""
        + "// CONTROLLER"
        + r"""
(async () => {
  const first = loadCosts();
  const second = loadCosts();
  assert.equal(first, second);
  assert.equal(pending.length, 1);
  finish(0, {version: 1});
  await first;
  assert.equal(controller.costState.version, 1);
  assert.equal(timers.size, 1);
  await loadCosts();
  assert.equal(pending.length, 1);
  assert.equal(timers.size, 1);
  now += COSTS_REFRESH_INTERVAL_MS;
  const timer = [...timers.values()][0];
  timers.clear();
  timer.callback();
  assert.equal(pending.length, 2);
  finish(1, {version: 2});
  await tick();
  assert.equal(controller.costState.version, 2);
  assert.equal(timers.size, 0);

  // A forced refresh observes a post-mutation read, even if a prior GET exists.
  const beforeMutation = loadCosts({force: true});
  const afterMutation = loadCosts({force: true});
  const afterAnotherMutation = loadCosts({force: true});
  assert.equal(afterMutation, afterAnotherMutation);
  assert.equal(pending.length, 3);
  finish(2, {version: 3});
  await beforeMutation;
  await tick();
  assert.equal(pending.length, 4);
  finish(3, {version: 4});
  await afterMutation;
  await afterAnotherMutation;
  assert.equal(controller.costState.version, 4);

  // An old GET cannot overwrite the result of an explicit transcript backfill.
  const stale = loadCosts({force: true});
  const backfill = refreshCostBackfill();
  assert.equal(pending.length, 6);
  await loadCosts();
  assert.equal(pending.length, 6);
  const resetDuringBackfill = loadCosts({force: true});
  assert.equal(pending.length, 6);
  finish(5, {version: 6});
  await backfill;
  finish(4, {version: 5});
  await stale;
  assert.equal(controller.costState.version, 6);
  await tick();
  assert.equal(pending.length, 7);
  finish(6, {version: 7});
  await resetDuringBackfill;
  assert.equal(controller.costState.version, 7);

  // A second UI instance has its own requests and cost snapshot.
  const independent = createTeamCostsController(controllerPeers);
  assert.notEqual(independent.costState, controller.costState);
  const independentLoad = independent.loadCosts();
  assert.equal(pending.length, 8);
  finish(7, {version: 8});
  await independentLoad;
  assert.equal(independent.costState.version, 8);
  assert.equal(controller.costState.version, 7);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    )
    script = controller_script(
        "costs",
        script,
        "{app: {activeTeamView, teamViewUsesCostState, render, setStatus}, access: {teamFetch}, drafts: {captureFormDrafts, restoreFormDrafts}}",
        ["loadCosts", "refreshCostBackfill"],
    )
    result = subprocess.run(
        [node, "--input-type=commonjs"],
        input=script,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
