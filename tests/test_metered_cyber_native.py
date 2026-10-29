"""Opt-in native request capture; no live model requests or real credentials."""

from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess

import pytest

from evander.effects.providers.agent_session import _codex_config_content, container_prompt_command
from evander.models import SessionManifest


@pytest.mark.parametrize("backend", ["container", "vm"])
def test_metered_native_turns_send_program_and_scoped_relay_auth_on_resume(tmp_path, backend):
    binary = os.environ.get("EVANDER_TEST_NATIVE_CODEX")
    if not binary or not shutil.which("bwrap") or not shutil.which("zstd"):
        pytest.skip("Set EVANDER_TEST_NATIVE_CODEX and install bwrap/zstd for local native checks")
    root = Path(__file__).resolve().parents[1]
    commands = []
    programs = [None, "daybreak_blue", "daybreak_red", "standard", None]
    for index, program in enumerate(programs):
        if backend == "container":
            session = SessionManifest(
                session_id="synthetic",
                vm_id="synthetic",
                model="gpt-6-sol",
                allowed_tools=[],
                cyber_access_program=program,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                metadata={"agent_provider": "codex", "model_provider_mode": "openai_public"},
            )
            command = container_prompt_command(
                prompt="Reply briefly.",
                model="gpt-6-sol",
                session=session,
                resume_thread_id="__THREAD_ID__" if index else None,
            )
        else:
            action = (
                'agent_runner_codex_resume_thread "__THREAD_ID__"'
                if index
                else "agent_runner_codex_exec"
            )
            command = (
                "set -euo pipefail\nsource /tmp/agent_runner_codex.sh\n"
                f"export AGENT_CYBER_ACCESS_PROGRAM={shlex.quote(program or '')}\n"
                f"{action} --model gpt-6-sol --dangerously-bypass-approvals-and-sandbox "
                "--skip-git-repo-check 'Reply briefly.'\n"
            )
        commands.append(command)
    config = _codex_config_content(
        model="gpt-6-sol",
        openai_base_url="http://relay.invalid",
        gateway_url="http://unused.invalid",
    ).replace("enabled = true", "enabled = false")
    # An obsolete native endpoint must not redirect the selected program's turn.
    config = 'openai_base_url="http://127.0.0.1:1/stale-endpoint"\n' + config
    inputs = tmp_path / "inputs.json"
    inputs.write_text(json.dumps({"backend": backend, "config": config, "commands": commands}))
    result = subprocess.run(
        [
            "bwrap",
            "--ro-bind",
            "/",
            "/",
            "--unshare-net",
            "--dev-bind",
            "/dev",
            "/dev",
            "--proc",
            "/proc",
            "--tmpfs",
            "/tmp",
            "--tmpfs",
            "/home",
            "--dir",
            "/home/agent",
            "--dir",
            "/tmp/bin",
            "--ro-bind",
            str(Path(binary).resolve()),
            "/tmp/bin/codex",
            "--ro-bind",
            str(inputs),
            "/tmp/inputs.json",
            "--ro-bind",
            str(root / "tests/fixtures/native_metered_cyber.py"),
            "/tmp/driver.py",
            "--ro-bind",
            str(root / "scripts/lib/agent_runner_codex.sh"),
            "/tmp/agent_runner_codex.sh",
            "--chdir",
            "/home/agent",
            "/usr/bin/env",
            "-i",
            "PATH=/usr/bin:/bin",
            "/usr/bin/python3",
            "/tmp/driver.py",
        ],
        capture_output=True,
        text=True,
        timeout=145,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    captured = json.loads(result.stdout)
    assert [output["code"] for output in captured["outputs"][:5]] == [0] * 5, json.dumps(
        captured["outputs"], indent=2
    )
    assert len(captured["calls"]) == 5, captured
    assert captured["thread_id"]
    assert [
        call["body"].get("access_programs", {}).get("cyber") for call in captured["calls"]
    ] == programs
    assert {call["path"] for call in captured["calls"]} == {
        "/v1/session/synthetic/auth/scoped-token/responses"
    }
    assert {call["auth"] for call in captured["calls"]} == {"Bearer scoped-token"}
    assert all(call["body"]["model"] == "gpt-6-sol" for call in captured["calls"])
    assert len(captured["outputs"]) == 7
    for output in captured["outputs"][5:]:
        assert output["code"] != 0
        assert "Cyber access requires a configured session relay URL" in output["stderr"]
