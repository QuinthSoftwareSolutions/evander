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


def test_open_agent_pr_refreshes_review_branch_before_force_with_lease_push(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts/open_agent_pr.sh"
    host_repo = tmp_path / "repo"
    host_repo.mkdir()
    (host_repo / ".git").mkdir()

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    git_log = tmp_path / "git.log"

    (fake_bin / "git").write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "{git_log}"
args=("$@")
if [[ "${{args[0]}}" == "-C" ]]; then
  args=("${{args[@]:2}}")
fi
if [[ "${{args[0]}}" == "-c" ]]; then
  args=("${{args[@]:2}}")
fi
case "${{args[0]:-}}" in
  rev-parse)
    ;;
  remote)
    echo "https://github.com/example/repo.git"
    ;;
  ls-remote)
    echo "abc123\trefs/heads/review/session-123/work"
    ;;
  fetch)
    ;;
  push)
    ;;
  symbolic-ref)
    echo "origin/main"
    ;;
  log)
    echo "Automated PR title"
    ;;
  gh)
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

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["AGENT_SANDBOX_PR_MAP_PATH"] = str(tmp_path / "runtime" / "pr-map.json")

    subprocess.run(
        [str(script), "--create-pr", str(host_repo), "review/session-123/work"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    commands = git_log.read_text(encoding="utf-8").splitlines()
    fetch_index = next(
        i
        for i, command in enumerate(commands)
        if "fetch origin refs/heads/review/session-123/work:refs/remotes/origin/review/session-123/work" in command
    )
    push_index = next(i for i, command in enumerate(commands) if "push --force-with-lease origin" in command)
    assert fetch_index < push_index


def test_open_agent_pr_preserves_existing_azure_pr_title_by_default(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts/open_agent_pr.sh"
    host_repo = tmp_path / "repo"
    host_repo.mkdir()
    (host_repo / ".git").mkdir()

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    python_log = tmp_path / "python.log"

    (fake_bin / "git").write_text(
        """#!/usr/bin/env bash
set -euo pipefail
args=("$@")
if [[ "${args[0]}" == "-C" ]]; then
  args=("${args[@]:2}")
fi
if [[ "${args[0]}" == "-c" ]]; then
  args=("${args[@]:2}")
fi
case "${args[0]:-}" in
  rev-parse)
    ;;
  remote)
    echo "https://dev.azure.com/example-org/example-project/_git/example-repo"
    ;;
  push)
    ;;
  symbolic-ref)
    echo "origin/main"
    ;;
  log)
    echo "Latest commit title"
    ;;
  *)
    echo "unexpected git invocation: $*" >&2
    exit 1
    ;;
esac
""",
        encoding="utf-8",
    )
    (fake_bin / "python3").write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
script="$(cat)"

if [[ -n "${{ORIGIN_URL_INPUT:-}}" ]]; then
  printf '%s' "https://dev.azure.com/example-org/example-project/_git/example-repo"
  exit 0
fi

if [[ -n "${{MAP_PATH:-}}" ]]; then
  mkdir -p "$(dirname "${{MAP_PATH}}")"
  printf '{{"version":1,"entries":{{}}}}\\n' > "${{MAP_PATH}}"
  exit 0
fi

if [[ -n "${{PROVIDER:-}}" && -n "${{PR_URL:-}}" && -n "${{PR_ID:-}}" && -n "${{ORIGIN_BRANCH:-}}" ]]; then
  printf '{{"provider":"%s","pr_url":"%s","pr_id":"%s","origin_branch":"%s","base_branch":"%s","session_id":"%s"}}\\n' \
    "${{PROVIDER}}" "${{PR_URL}}" "${{PR_ID}}" "${{ORIGIN_BRANCH}}" "${{BASE_BRANCH}}" "${{SESSION_ID:-}}"
  exit 0
fi

if [[ -n "${{AZURE_RESULT:-}}" ]]; then
  if [[ "${{script}}" == *'payload.get("pr_url"'* ]]; then
    printf '%s' "https://dev.azure.com/example-org/example-project/_git/example-repo/pullrequest/42"
  else
    printf '%s' "42"
  fi
  exit 0
fi

if [[ -n "${{ORIGIN_URL:-}}" && -n "${{AZURE_DEVOPS_PAT_VALUE:-}}" ]]; then
  printf 'TITLE=%s\\nTITLE_EXPLICIT=%s\\n' "${{TITLE:-}}" "${{TITLE_EXPLICIT:-}}" >> "{python_log}"
  printf '%s\\n' '{{"pr_url":"https://dev.azure.com/example-org/example-project/_git/example-repo/pullrequest/42","pr_id":"42"}}'
  exit 0
fi

if [[ -n "${{ORIGIN_URL:-}}" ]]; then
  printf '%s' "example-org/example-project/example-repo"
  exit 0
fi

exit 1
""",
        encoding="utf-8",
    )
    os.chmod(fake_bin / "git", 0o755)
    os.chmod(fake_bin / "python3", 0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["AGENT_SANDBOX_PR_MAP_PATH"] = str(tmp_path / "runtime" / "pr-map.json")
    env["AZURE_DEVOPS_PAT"] = "test-pat"

    result = subprocess.run(
        [str(script), "--create-pr", "--provider", "azure", str(host_repo), "review/session-123/work"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    lines = [line for line in result.stdout.splitlines() if line.startswith("AGENT_PR_RESULT ")]
    assert lines, result.stdout
    logged = python_log.read_text(encoding="utf-8")
    assert "TITLE=Latest commit title" in logged
    assert "TITLE_EXPLICIT=0" in logged
