from pathlib import Path
import subprocess


def test_command_blocked_verification_exposes_operator_retry():
    source = (
        Path(__file__).resolve().parents[1] / "src/evander_team_addon/ui_static/team-ui.js"
    ).read_text()
    function = source[
        source.index("function renderReleaseGateAction(") : source.index(
            "async function retryReleaseGate("
        )
    ]
    script = (
        """
const assert = require('node:assert/strict');
const text = String, escapeAttr = String, escapeHtml = String, humanizeToken = String;
const state = {release_packages: {}};
const releaseHasSuccessor = () => false;
"""
        + function
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
    result = subprocess.run(
        ["node", "--input-type=commonjs"],
        input=script,
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
