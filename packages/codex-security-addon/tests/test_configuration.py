import json
import os
from pathlib import Path
import shutil
import subprocess
import tomllib

import pytest

from evander_codex_security_addon import runner
from evander.gate_runtime import filter_when_matches
from evander.workflow.packs import WorkflowPack


def test_example_is_inert_until_operator_enables_it():
    path = Path(__file__).resolve().parents[1] / "examples/security-scan.v1-example.json"
    pack = WorkflowPack.model_validate_json(path.read_text())
    gate = next(item.node for item in pack.ir.nodes if item.node.id == "scan-enable")
    assert filter_when_matches(gate.config["when"]) is False


@pytest.mark.parametrize("subscription", [False, True])
def test_runtime_configuration_preserves_relay_route_and_isolates_other_tools(
    tmp_path, subscription
):
    package = Path(__file__).resolve().parents[1]
    if not (package / "node_modules/@openai/codex-security").exists() or not shutil.which("node"):
        pytest.skip("Run npm ci in the optional provider to test its pinned Node runtime")
    home = tmp_path / "original"
    home.mkdir()
    (home / "config.toml").write_text("""model = "synthetic-model"
model_provider = "session_relay"
[model_providers.session_relay]
name = "Synthetic relay"
base_url = "http://127.0.0.1:11434/v1/session/example"
env_key = "OPENAI_API_KEY"
wire_api = "responses"
[mcp_servers.unrelated]
command = "unrelated-secret-command"
""")
    root = tmp_path / "scan"
    root.mkdir()
    (root / "request.json").write_text('{"timeout_seconds":90}')
    env = {**os.environ, "CODEX_HOME": str(home)}
    if subscription:
        env["EVANDER_CODEX_BROKER_SOCKET"] = "/run/evander/codex-runtime/broker.sock"
    else:
        env.pop("EVANDER_CODEX_BROKER_SOCKET", None)
    result = subprocess.run(
        ["node", str(runner.INSTALL_ROOT / "configure.mjs"), str(root)],
        check=True,
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )
    setup = json.loads(result.stdout)
    configured = tomllib.loads((root / "codex-home/config.toml").read_text())
    assert configured["model"] == "synthetic-model"
    assert configured["model_provider"] == ("openai" if subscription else "session_relay")
    assert list(configured["mcp_servers"]) == ["codex-security"]
    assert configured["default_permissions"] == "codex_security_scan"
    assert configured["projects"][str(root)]["trust_level"] == "trusted"
    assert "unrelated-secret-command" not in (root / "codex-home/config.toml").read_text()
    assert Path(setup["plugin_root"], "scripts/validate_scan_contract.py").is_file()
    assert not (root / "codex-home/auth.json").exists()
