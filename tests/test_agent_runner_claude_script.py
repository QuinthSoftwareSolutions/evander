from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    os.chmod(path, 0o755)


def test_claude_runner_streams_json_and_applies_mcp_gateway_override(tmp_path: Path) -> None:
    home = tmp_path / "home"
    claude_config_dir = home / ".claude"
    claude_config_dir.mkdir(parents=True)
    (claude_config_dir / "mcp-config.json").write_text(
        '{"mcpServers":{"agent_sandbox":{"url":"http://static.test/mcp"}}}\n',
        encoding="utf-8",
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    args_log = tmp_path / "claude-args.log"
    _write_executable(
        fake_bin / "claude",
        f"""#!/usr/bin/env bash
set -euo pipefail
for arg in "$@"; do
  printf '%s\\n' "$arg" >> "{args_log}"
done
cat <<'EOF'
{{"type":"system","session_id":"claude-thread-1"}}
{{"type":"assistant","message":{{"content":[{{"type":"text","text":"Done."}}]}}}}
EOF
""",
    )

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["AGENT_SANDBOX_MCP_GATEWAY_URL"] = (
        "http://gateway.test:8000/mcp?"
        "session_id=claude-session&workflow_run_id=run-1&mcp_tool_approval_lease_id=lease-1"
    )
    command = (
        f"source '{ROOT / 'scripts' / 'lib' / 'agent_runner_claude.sh'}'; "
        f"agent_runner_claude_exec --model claude-sonnet-4-6 -C '{tmp_path}' 'Continue'"
    )

    result = subprocess.run(
        ["bash", "-lc", command],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    assert result.stdout == "session id: claude-thread-1\nDone.\n"
    args = args_log.read_text(encoding="utf-8").splitlines()
    override_config = claude_config_dir / "agent-sandbox-mcp-config.json"
    assert "--output-format" in args
    assert "stream-json" in args
    assert "--verbose" in args
    assert "--mcp-config" in args
    assert args[args.index("--mcp-config") + 1] == str(override_config)
    assert "--model" in args
    assert args[args.index("--model") + 1] == "claude-sonnet-4-6"
    override_content = override_config.read_text(encoding="utf-8")
    assert "mcp_tool_approval_lease_id=lease-1" in override_content
    assert "http://static.test/mcp" not in override_content
