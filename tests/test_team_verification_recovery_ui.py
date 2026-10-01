import subprocess

from team_ui_controller_harness import controller_script


def test_command_blocked_verification_exposes_operator_retry():
    script = (
        """
const assert = require('node:assert/strict');
const text = String, escapeAttr = String, escapeHtml = String, humanizeToken = String;
const state = {release_packages: {}};
const releaseHasSuccessor = () => false;
"""
        + "// CONTROLLER"
        + """
const release = {id: 'release', state: 'blocked', gate_failure: {
  stage: 'verification', kind: 'blocked', source_id: 'failed-command'
}};
const html = renderReleaseGateAction(release);
assert.match(html, /Retry verification gate/);
assert.match(html, /data-gate-id="failed-command"/);
assert.match(html, /data-gate-kind="verification"/);
assert.equal(renderReleaseGateAction({...release, state: 'cancelled'}), '');
assert.equal(renderReleaseGateAction({...release, state: 'approved'}), '');
"""
    )
    script = controller_script("releases", script, "{app: {state}}", ["renderReleaseGateAction"])
    result = subprocess.run(
        ["node", "--input-type=commonjs"],
        input=script,
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
