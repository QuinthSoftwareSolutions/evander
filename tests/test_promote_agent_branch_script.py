from __future__ import annotations

import os
import subprocess
from pathlib import Path


def test_promote_agent_branch_refreshes_existing_review_branch(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "promote_agent_branch.sh"
    host_repo = tmp_path / "repo"
    bare_repo = tmp_path / "sample.git"
    fake_bin = tmp_path / "bin"
    git_log = tmp_path / "git.log"

    (host_repo / ".git").mkdir(parents=True)
    bare_repo.mkdir()
    fake_bin.mkdir()

    (fake_bin / "git").write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "{git_log}"
args=("$@")
if [[ "${{args[0]:-}}" == "-C" ]]; then
  repo_path="${{args[1]}}"
  args=("${{args[@]:2}}")
fi
    case "${{args[*]}}" in
      "remote get-url agent-dropbox")
        exit 2
        ;;
      "remote add agent-dropbox "*)
        exit 0
        ;;
      "show-ref --verify --quiet refs/heads/review/session-1/topic")
        exit 0
        ;;
  "fetch agent-dropbox refs/heads/agents/session-1/topic")
    exit 0
    ;;
  "switch -C review/session-1/topic FETCH_HEAD")
    exit 0
    ;;
  *)
    echo "unexpected git invocation: $*" >&2
    exit 1
    ;;
esac
""",
        encoding="utf-8",
    )
    os.chmod(fake_bin / "git", 0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    result = subprocess.run(
        [
            str(script),
            str(host_repo),
            str(bare_repo),
            "agents/session-1/topic",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert "Fetched agents/session-1/topic from agent-dropbox" in result.stdout
    assert "Refreshed local review branch: review/session-1/topic" in result.stdout
    log_text = git_log.read_text(encoding="utf-8")
    assert "fetch agent-dropbox refs/heads/agents/session-1/topic" in log_text
    assert "switch -C review/session-1/topic FETCH_HEAD" in log_text
