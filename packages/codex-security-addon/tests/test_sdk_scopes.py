"""Exercise the pinned public SDK with a synthetic CLI; never run a model."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from evander_codex_security_addon.codex_bridge import local_plugin_setup

PACKAGE = Path(__file__).resolve().parents[1]


def test_local_plugin_setup_cannot_bypass_broker_for_model_or_remote_operations():
    home = "/artifacts/security-scans/example/state/codex-home"
    assert local_plugin_setup(["plugin", "marketplace", "add", home + "/sdk-marketplace"], home)
    assert local_plugin_setup(
        ["plugin", "add", "--json", "codex-security@codex-security-sdk"], home
    )
    for args in (
        ["exec", "prompt"],
        ["login"],
        ["plugin", "add", "remote-plugin"],
        ["plugin", "marketplace", "add", "https://example.invalid"],
        ["plugin", "marketplace", "add", "/etc"],
    ):
        assert not local_plugin_setup(args, home)
    assert not local_plugin_setup(
        ["plugin", "add", "--json", "codex-security@codex-security-sdk"], "/etc"
    )


def test_public_scoped_standard_sdk_reaches_subscription_transport_with_exact_paths(
    tmp_path, monkeypatch
):
    node = shutil.which("node")
    if node is None or not (PACKAGE / "node_modules/@openai/codex-security").exists():
        pytest.skip("Install the pinned optional Node runtime to exercise SDK compatibility")
    cli = tmp_path / "codex"
    log = tmp_path / "requests.jsonl"
    cli.write_text(
        f"#!{sys.executable}\n"
        + r"""
import json, os, pathlib, sys
args = sys.argv[1:]
if 'exec' in args:
    prompt = sys.stdin.read()
    if prompt: args.append(prompt)
with open(os.environ['SYNTHETIC_SDK_LOG'], 'a') as out:
    out.write(json.dumps({'argv': args, 'cwd': os.getcwd(), 'environment': {k:v for k,v in os.environ.items() if k.startswith('CODEX_SECURITY_')}, 'api_key_present': bool(os.environ.get('OPENAI_API_KEY') or os.environ.get('CODEX_API_KEY'))})+'\n')
home = pathlib.Path(os.environ['CODEX_HOME'])
if args[:3] == ['plugin','marketplace','add']:
    with (home/'config.toml').open('a') as config:
        config.write('\n[marketplaces.codex-security-sdk]\nsource_type="local"\nsource='+json.dumps(args[3])+'\n')
elif args == ['plugin','add','--json','codex-security@codex-security-sdk']:
    plugin=home/'sdk-marketplace/plugins/codex-security'
    version=json.loads((plugin/'.codex-plugin/plugin.json').read_text())['version']
    print(json.dumps({'installedPath':str(plugin),'version':version}))
elif args == ['login','status']:
    print('Logged in using ChatGPT (synthetic subscription)')
elif args == ['--version']:
    print('codex-cli 0.146.0')
elif 'exec' in args:
    scope = pathlib.Path(os.environ['CODEX_SECURITY_TARGET_PATHS_FILE'])
    pathlib.Path(os.environ['SYNTHETIC_SDK_LOG']+'.scope').write_bytes(scope.read_bytes())
    print(json.dumps({'type':'thread.started','thread_id':'11111111-1111-4111-8111-111111111111'}))
    print(json.dumps({'type':'turn.failed','error':{'message':'Synthetic transport boundary reached; no model executed'}}))
else:
    raise SystemExit('Unexpected synthetic CLI command')
"""
    )
    cli.chmod(0o755)
    root = tmp_path / "review"
    repo = root / "checkout"
    repo.mkdir(parents=True)
    root.chmod(0o700)
    (repo / "included.py").write_text('print("review me")\n')
    (repo / "outside.py").write_text('print("supporting source only")\n')
    for args in (
        ["init", "-q"],
        ["add", "."],
        [
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    revision = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    home = root / "codex-home"
    home.mkdir(mode=0o700)
    (home / "config.toml").write_text('model_provider="openai"\nmodel="synthetic-model"\n')
    plugin = PACKAGE / "node_modules/@openai/codex-security/_bundled_plugin"
    (root / "sdk-setup.json").write_text(json.dumps({"plugin_root": str(plugin)}))
    (root / "request.json").write_text(
        json.dumps(
            {
                "scan_id": "a" * 32,
                "sdk_version": "0.1.31",
                "snapshot_commit": revision,
                "frozen_route": {"model": "synthetic-model", "reasoning_effort": "high"},
                "coverage_task": {
                    "kind": "files",
                    "paths": ["included.py"],
                    "inventory_paths": ["included.py", "outside.py"],
                    "architecture": {},
                    "deferred": {},
                    "prior_findings": [],
                    "accepted_file_reviews": {},
                },
            }
        )
    )
    env = {
        k: v for k, v in os.environ.items() if not k.startswith(("OPENAI_", "CODEX_", "EVANDER_"))
    }
    env.update(
        CODEX_CLI_PATH=str(cli),
        CODEX_HOME=str(home),
        CODEX_SECURITY_STATE_DIR=str(root / "state"),
        TMPDIR=str(tmp_path),
        EVANDER_CODEX_BROKER_SOCKET="synthetic",
        OPENAI_API_KEY="placeholder-must-not-be-selected",
        SYNTHETIC_SDK_LOG=str(log),
    )
    result = subprocess.run(
        [node, str(PACKAGE / "evander_codex_security_addon/coverage_sdk.mjs"), str(root)],
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert log.exists(), result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    turns = [x for x in calls if "exec" in x["argv"]]
    assert turns, result.stderr
    assert all(not x["api_key_present"] for x in turns)
    scope = json.loads(Path(str(log) + ".scope").read_text())
    assert scope == ["included.py"]
    from evander.worker import codex_runtime_bridge

    monkeypatch.setattr(codex_runtime_bridge, "allowed_path", lambda value, **kwargs: None)
    for turn in turns:
        mode, _, config, model, prompt = codex_runtime_bridge.parse_arguments(
            turn["argv"], turn["cwd"]
        )
        assert mode == "exec"
        assert model == "synthetic-model" or config["model"] == "synthetic-model"
        assert config["responses_api_metadata"] == {"codex_security_surface": "sdk"}
        assert turn["environment"]["CODEX_SECURITY_TARGET_REVISION"] == revision
        assert "every assigned file" in prompt
    value = json.loads((root / "sdk-result.json").read_text())
    assert value["status"] == "failed"
    assert "coverage_unit" not in value
