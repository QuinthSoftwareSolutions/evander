from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    os.chmod(path, 0o755)


def test_claude_runner_streams_prompt_on_stdin_and_writes_mcp_config(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    args_log = tmp_path / "claude-args.log"
    stdin_log = tmp_path / "claude-stdin.log"
    _write_executable(
        fake_bin / "claude",
        f"""#!/usr/bin/env bash
set -euo pipefail
for arg in "$@"; do
  printf '%s\\n' "$arg" >> "{args_log}"
done
cat > "{stdin_log}"
cat <<'EOF'
{{"session_id":"claude-thread-1"}}
{{"type":"assistant","message":{{"content":[{{"text":"Done."}}]}}}}
EOF
""",
    )

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["AGENT_SANDBOX_MCP_GATEWAY_URL"] = (
        "http://gateway.test:8000/mcp?session_id=session-1"
    )
    command = (
        f"source '{ROOT / 'scripts' / 'lib' / 'agent_runner_claude.sh'}'; "
        "agent_runner_claude_exec --model claude-test 'Continue'"
    )

    result = subprocess.run(
        ["bash", "-lc", command],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    assert result.stdout == "session id: claude-thread-1\nDone.\n"
    assert stdin_log.read_text(encoding="utf-8") == "Continue"
    args = args_log.read_text(encoding="utf-8").splitlines()
    assert args == [
        "-p",
        "--dangerously-skip-permissions",
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        "claude-test",
        "--mcp-config",
        str(home / ".claude" / "agent-sandbox-mcp-config.json"),
    ]
    mcp_config = json.loads(
        (home / ".claude" / "agent-sandbox-mcp-config.json").read_text(
            encoding="utf-8"
        )
    )
    assert mcp_config["mcpServers"]["agent_sandbox"]["url"] == (
        "http://gateway.test:8000/mcp?session_id=session-1"
    )
