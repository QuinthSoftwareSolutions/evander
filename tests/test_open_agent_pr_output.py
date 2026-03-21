from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def test_open_agent_pr_emits_structured_output_and_writes_mapping(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts/open_agent_pr.sh"
    host_repo = tmp_path / "repo"
    host_repo.mkdir()
    (host_repo / ".git").mkdir()

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()

    (fake_bin / "git").write_text(
        """#!/usr/bin/env bash
set -euo pipefail
args=("$@")
if [[ "${args[0]}" == "-C" ]]; then
  args=("${args[@]:2}")
fi
case "${args[0]:-}" in
  branch)
    echo "review/session-123/work"
    ;;
  rev-parse)
    ;;
  push)
    ;;
  symbolic-ref)
    echo "origin/main"
    ;;
  log)
    echo "Automated PR title"
    ;;
  remote)
    echo "https://github.com/example/repo.git"
    ;;
  *)
    echo "unexpected git invocation: $*" >&2
    exit 1
    ;;
esac
""",
        encoding="utf-8",
    )
    (fake_bin / "gh").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\necho \"https://github.com/example/repo/pull/42\"\n",
        encoding="utf-8",
    )
    os.chmod(fake_bin / "git", 0o755)
    os.chmod(fake_bin / "gh", 0o755)

    pr_map = tmp_path / "runtime" / "pr-map.json"
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["AGENT_SANDBOX_PR_MAP_PATH"] = str(pr_map)

    result = subprocess.run(
        [str(script), "--create-pr", str(host_repo), "review/session-123/work"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    lines = [line for line in result.stdout.splitlines() if line.startswith("AGENT_PR_RESULT ")]
    assert lines, result.stdout
    payload = json.loads(lines[-1].split(" ", 1)[1])
    assert payload == {
        "base_branch": "main",
        "origin_branch": "review/session-123/work",
        "provider": "github",
        "pr_id": "42",
        "pr_url": "https://github.com/example/repo/pull/42",
        "session_id": "session-123",
    }

    mapping = json.loads(pr_map.read_text(encoding="utf-8"))
    entry = mapping["entries"]["github:example/repo:42"]
    assert entry["provider"] == "github"
    assert entry["repo"] == "example/repo"
    assert entry["pr_id"] == "42"
    assert entry["pr_url"] == "https://github.com/example/repo/pull/42"
    assert entry["origin_branch"] == "review/session-123/work"
    assert entry["base_branch"] == "main"
    assert entry["session_id"] == "session-123"
