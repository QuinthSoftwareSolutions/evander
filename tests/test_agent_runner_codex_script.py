from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    os.chmod(path, 0o755)


def test_codex_runner_streams_json_and_emits_session_id(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    args_log = tmp_path / "codex-args.log"
    _write_executable(
        fake_bin / "codex",
        f"""#!/usr/bin/env bash
set -euo pipefail
if [[ "${{1:-}}" == "features" && "${{2:-}}" == "list" ]]; then
  printf '%s\\n' 'non_prefixed_mcp_tool_names          under development  false'
  exit 0
fi
for arg in "$@"; do
  printf '%s\\n' "$arg" >> "{args_log}"
done
cat <<'EOF'
{{"type":"session_meta","payload":{{"id":"codex-thread-1"}}}}
{{"type":"response_item","payload":{{"type":"message","role":"assistant","content":[{{"type":"output_text","text":"Done."}}]}}}}
EOF
""",
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["AGENT_WEB_SEARCH_MODE"] = "disabled"
    env["EVANDER_MCP_GATEWAY_URL"] = "http://gateway.test:8000/mcp?session_id=session-1"
    command = (
        f"source '{ROOT / 'scripts' / 'lib' / 'agent_runner_codex.sh'}'; "
        "agent_runner_codex_exec --model gpt-test 'Continue'"
    )

    result = subprocess.run(
        ["bash", "-lc", command],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    assert result.stdout == "session id: codex-thread-1\nDone.\n"
    args = args_log.read_text(encoding="utf-8").splitlines()
    assert args[:2] == ["exec", "-c"]
    assert 'web_search="disabled"' in args
    assert 'mcp_servers.evander.url="http://gateway.test:8000/mcp?session_id=session-1"' in args
    assert "--enable" in args
    assert "non_prefixed_mcp_tool_names" in args
    assert "--json" in args


def test_codex_runner_streams_bounded_reasoning_and_tool_progress(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "codex",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "features" && "${2:-}" == "list" ]]; then
  exit 0
fi
cat <<'EOF'
{"type":"session_meta","payload":{"id":"codex-thread-progress"}}
{"type":"event_msg","payload":{"type":"agent_reasoning","text":"Inspecting the existing patterns before editing."}}
{"type":"response_item","payload":{"type":"reasoning","summary":[{"type":"summary_text","text":"Inspecting the existing patterns before editing."}]}}
{"type":"response_item","payload":{"type":"function_call","name":"team_get_task","call_id":"call-team","arguments":"{\\"task_id\\":\\"sensitive-structured-value\\"}"}}
{"type":"response_item","payload":{"type":"function_call_output","call_id":"call-team","output":"large sensitive tool output"}}
{"type":"response_item","payload":{"type":"function_call","name":"exec_command","call_id":"call-exec","arguments":"{\\"cmd\\":\\"rg --files src\\",\\"workdir\\":\\"/home/agent/repo\\"}"}}
{"type":"response_item","payload":{"type":"function_call_output","call_id":"call-exec","output":"src/one.py\\nsrc/two.py"}}
{"type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"Done."}]}}
EOF
""",
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    command = (
        f"source '{ROOT / 'scripts' / 'lib' / 'agent_runner_codex.sh'}'; "
        "agent_runner_codex_exec --model local-test 'Continue'"
    )

    result = subprocess.run(
        ["bash", "-lc", command],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    assert result.stdout == (
        "session id: codex-thread-progress\n"
        "reasoning: Inspecting the existing patterns before editing.\n"
        "tool started: team_get_task\n"
        "tool completed: team_get_task\n"
        "tool started: exec_command rg --files src cwd=/home/agent/repo\n"
        "tool completed: exec_command rg --files src cwd=/home/agent/repo\n"
        "Done.\n"
    )
    assert "sensitive-structured-value" not in result.stdout
    assert "large sensitive tool output" not in result.stdout


def test_codex_runner_skips_unknown_optional_feature(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    args_log = tmp_path / "codex-args.log"
    _write_executable(
        fake_bin / "codex",
        f"""#!/usr/bin/env bash
set -euo pipefail
if [[ "${{1:-}}" == "features" && "${{2:-}}" == "list" ]]; then
  printf '%s\\n' 'browser_use                          stable             true'
  exit 0
fi
for arg in "$@"; do
  printf '%s\\n' "$arg" >> "{args_log}"
done
cat <<'EOF'
{{"type":"response_item","payload":{{"type":"message","role":"assistant","content":[{{"type":"output_text","text":"Done."}}]}}}}
EOF
""",
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["EVANDER_MCP_GATEWAY_URL"] = "http://gateway.test:8000/mcp?session_id=session-1"
    command = (
        f"source '{ROOT / 'scripts' / 'lib' / 'agent_runner_codex.sh'}'; "
        "agent_runner_codex_exec --model gpt-test 'Continue'"
    )

    result = subprocess.run(
        ["bash", "-lc", command],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    assert result.stdout == "Done.\n"
    args = args_log.read_text(encoding="utf-8").splitlines()
    assert 'mcp_servers.evander.url="http://gateway.test:8000/mcp?session_id=session-1"' in args
    assert "--enable" not in args
    assert "non_prefixed_mcp_tool_names" not in args
