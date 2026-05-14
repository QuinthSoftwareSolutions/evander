from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_open_agent_pr_emits_structured_output_and_writes_mapping(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts/open_agent_pr.sh"
    host_repo = tmp_path / "repo"
    host_repo.mkdir()
    (host_repo / ".git").mkdir()

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    attach_log = tmp_path / "attach.log"

    (fake_bin / "git").write_text(
        """#!/usr/bin/env bash
set -euo pipefail
args=("$@")
if [[ "${args[0]}" == "-C" ]]; then
  args=("${args[@]:2}")
fi
while [[ "${args[0]:-}" == "-c" ]]; do
  args=("${args[@]:2}")
done
case "${args[0]:-}" in
  branch)
    echo "review/session-123/work"
    ;;
  rev-parse)
    ;;
  push)
    ;;
  ls-remote)
    exit 2
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
        '#!/usr/bin/env bash\nset -euo pipefail\necho "https://github.com/example/repo/pull/42"\n',
        encoding="utf-8",
    )
    (fake_bin / "agent-sandbox-cli").write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "{attach_log}"
""",
        encoding="utf-8",
    )
    os.chmod(fake_bin / "git", 0o755)
    os.chmod(fake_bin / "gh", 0o755)
    os.chmod(fake_bin / "agent-sandbox-cli", 0o755)

    pr_map = tmp_path / "runtime" / "pr-map.json"
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["AGENT_SANDBOX_PR_MAP_PATH"] = str(pr_map)
    env["AGENT_SANDBOX_CLI_BIN"] = str(fake_bin / "agent-sandbox-cli")

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
        "work_item_id": "",
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
    attach_args = attach_log.read_text(encoding="utf-8").strip()
    assert "attach-pr-feedback" in attach_args
    assert "--provider github" in attach_args
    assert "--repo example/repo" in attach_args
    assert "--pr-id 42" in attach_args
    assert "session-123" in attach_args


def test_open_agent_pr_defaults_origin_branch_to_feature_issue_when_session_encodes_issue(
    tmp_path: Path,
) -> None:
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
while [[ "${{args[0]:-}}" == "-c" ]]; do
  args=("${{args[@]:2}}")
done
case "${{args[0]:-}}" in
  rev-parse)
    ;;
  remote)
    echo "https://github.com/example/repo.git"
    ;;
  push)
    ;;
  ls-remote)
    exit 2
    ;;
  symbolic-ref)
    echo "origin/main"
    ;;
  log)
    echo "Automated PR title"
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
        '#!/usr/bin/env bash\nset -euo pipefail\necho "https://github.com/example/repo/pull/42"\n',
        encoding="utf-8",
    )
    (fake_bin / "agent-sandbox-cli").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n",
        encoding="utf-8",
    )
    os.chmod(fake_bin / "git", 0o755)
    os.chmod(fake_bin / "gh", 0o755)
    os.chmod(fake_bin / "agent-sandbox-cli", 0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["AGENT_SANDBOX_PR_MAP_PATH"] = str(tmp_path / "runtime" / "pr-map.json")
    env["AGENT_SANDBOX_CLI_BIN"] = str(fake_bin / "agent-sandbox-cli")

    result = subprocess.run(
        [str(script), "--create-pr", str(host_repo), "review/issue-14084-abc/work"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    lines = [line for line in result.stdout.splitlines() if line.startswith("AGENT_PR_RESULT ")]
    assert lines, result.stdout
    payload = json.loads(lines[-1].split(" ", 1)[1])
    assert payload["origin_branch"] == "feature/issue-14084"
    assert payload["session_id"] == "issue-14084-abc"
    assert payload["work_item_id"] == "14084"

    commands = git_log.read_text(encoding="utf-8").splitlines()
    assert any(
        "push origin review/issue-14084-abc/work:refs/heads/feature/issue-14084" in command
        for command in commands
    )


def test_open_agent_pr_preflight_reports_destination_branch_ahead(tmp_path: Path) -> None:
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
while [[ "${{args[0]:-}}" == "-c" ]]; do
  args=("${{args[@]:2}}")
done
case "${{args[0]:-}}" in
  rev-parse)
    if [[ "${{args[-1]}}" == "refs/remotes/origin/feature/issue-1^{{commit}}" ]]; then
      echo "remote-tip"
    elif [[ "${{args[-1]}}" == "review/issue-1/work^{{commit}}" ]]; then
      echo "local-tip"
    fi
    ;;
  remote)
    echo "https://github.com/example/repo.git"
    ;;
  ls-remote)
    echo "remote-tip\\trefs/heads/feature/issue-1"
    ;;
  fetch)
    ;;
  merge-base)
    exit 1
    ;;
  rev-list)
    echo "3 2"
    ;;
  push)
    echo "push should not run" >&2
    exit 1
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
            "--create-pr",
            "--base",
            "main",
            "--session-id",
            "issue-1-session",
            str(host_repo),
            "review/issue-1/work",
            "feature/issue-1",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    lines = [line for line in result.stdout.splitlines() if line.startswith("AGENT_PR_RESULT ")]
    assert lines, result.stdout
    payload = json.loads(lines[-1].split(" ", 1)[1])
    assert payload["success"] is False
    assert payload["preflight_success"] is False
    assert payload["reason"] == "destination_branch_ahead"
    assert payload["origin_branch"] == "feature/issue-1"
    assert payload["local_branch"] == "review/issue-1/work"
    assert payload["destination_commit"] == "remote-tip"
    assert payload["local_commit"] == "local-tip"
    assert payload["destination_ahead_count"] == 3
    assert payload["local_ahead_count"] == 2
    assert not any(
        command.startswith("push ") for command in git_log.read_text(encoding="utf-8").splitlines()
    )


def test_open_agent_pr_refreshes_review_branch_before_force_with_lease_push(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts/open_agent_pr.sh"
    host_repo = tmp_path / "repo"
    host_repo.mkdir()
    (host_repo / ".git").mkdir()

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    git_log = tmp_path / "git.log"
    attach_log = tmp_path / "attach.log"

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
  merge-base)
    ;;
  push)
    ;;
  ls-remote)
    exit 2
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
        '#!/usr/bin/env bash\nset -euo pipefail\necho "https://github.com/example/repo/pull/42"\n',
        encoding="utf-8",
    )
    (fake_bin / "agent-sandbox-cli").write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "{attach_log}"
""",
        encoding="utf-8",
    )
    os.chmod(fake_bin / "git", 0o755)
    os.chmod(fake_bin / "gh", 0o755)
    os.chmod(fake_bin / "agent-sandbox-cli", 0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["AGENT_SANDBOX_PR_MAP_PATH"] = str(tmp_path / "runtime" / "pr-map.json")
    env["AGENT_SANDBOX_CLI_BIN"] = str(fake_bin / "agent-sandbox-cli")
    env["AGENT_SANDBOX_CANONICAL_ORIGIN_URL"] = "git@github.com:example/repo.git"

    subprocess.run(
        [str(script), "--create-pr", "--base", "main", str(host_repo), "review/session-123/work"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    commands = git_log.read_text(encoding="utf-8").splitlines()
    fetch_index = next(
        i
        for i, command in enumerate(commands)
        if "fetch agent-sandbox-pr-origin refs/heads/review/session-123/work:refs/remotes/agent-sandbox-pr-origin/review/session-123/work"
        in command
    )
    push_index = next(
        i
        for i, command in enumerate(commands)
        if "push --force-with-lease agent-sandbox-pr-origin" in command
    )
    assert fetch_index < push_index


def test_open_agent_pr_uses_https_with_gh_credentials_for_github_pushes(tmp_path: Path) -> None:
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
while [[ "${{args[0]:-}}" == "-c" ]]; do
  args=("${{args[@]:2}}")
done
case "${{args[0]:-}}" in
  branch)
    echo "review/session-123/work"
    ;;
  rev-parse)
    ;;
  push)
    ;;
  ls-remote)
    exit 2
    ;;
  symbolic-ref)
    echo "origin/main"
    ;;
  log)
    echo "Automated PR title"
    ;;
  remote)
    echo "git@github.com:example/repo.git"
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
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "auth" && "${2:-}" == "status" ]]; then
  exit 0
fi
echo "https://github.com/example/repo/pull/42"
""",
        encoding="utf-8",
    )
    (fake_bin / "agent-sandbox-cli").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n",
        encoding="utf-8",
    )
    os.chmod(fake_bin / "git", 0o755)
    os.chmod(fake_bin / "gh", 0o755)
    os.chmod(fake_bin / "agent-sandbox-cli", 0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["AGENT_SANDBOX_PR_MAP_PATH"] = str(tmp_path / "runtime" / "pr-map.json")
    env["AGENT_SANDBOX_CLI_BIN"] = str(fake_bin / "agent-sandbox-cli")
    env["AGENT_SANDBOX_CANONICAL_ORIGIN_URL"] = "git@github.com:example/repo.git"

    subprocess.run(
        [str(script), "--create-pr", "--base", "main", str(host_repo), "review/session-123/work"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    commands = git_log.read_text(encoding="utf-8").splitlines()
    assert any(
        "agent-sandbox-pr-origin https://github.com/example/repo.git" in command
        and ("remote add " in command or "remote set-url " in command)
        for command in commands
    )
    assert any(
        "credential.https://github.com.helper=!/usr/bin/gh auth git-credential push" in command
        for command in commands
    )


def test_open_agent_pr_fetches_base_branch_before_github_fill_on_canonical_remote(
    tmp_path: Path,
) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts/open_agent_pr.sh"
    host_repo = tmp_path / "repo"
    host_repo.mkdir()
    (host_repo / ".git").mkdir()

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    git_log = tmp_path / "git.log"
    gh_log = tmp_path / "gh.log"

    (fake_bin / "git").write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "{git_log}"
args=("$@")
if [[ "${{args[0]}}" == "-C" ]]; then
  args=("${{args[@]:2}}")
fi
while [[ "${{args[0]:-}}" == "-c" ]]; do
  args=("${{args[@]:2}}")
done
case "${{args[0]:-}}" in
  rev-parse)
    ;;
  remote)
    echo "git@github.com:example/repo.git"
    ;;
  ls-remote)
    branch_ref="${{args[-1]}}"
    if [[ "${{branch_ref}}" == "refs/heads/review/session-123/work" || "${{branch_ref}}" == "refs/heads/main" ]]; then
      echo "abc123\t${{branch_ref}}"
    fi
    ;;
  fetch)
    ;;
  merge-base)
    ;;
  push)
    ;;
  ls-remote)
    exit 2
    ;;
  symbolic-ref)
    echo "origin/main"
    ;;
  log)
    echo "Automated PR title"
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
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "{gh_log}"
echo "https://github.com/example/repo/pull/42"
""",
        encoding="utf-8",
    )
    (fake_bin / "agent-sandbox-cli").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n",
        encoding="utf-8",
    )
    os.chmod(fake_bin / "git", 0o755)
    os.chmod(fake_bin / "gh", 0o755)
    os.chmod(fake_bin / "agent-sandbox-cli", 0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["AGENT_SANDBOX_PR_MAP_PATH"] = str(tmp_path / "runtime" / "pr-map.json")
    env["AGENT_SANDBOX_CLI_BIN"] = str(fake_bin / "agent-sandbox-cli")
    env["AGENT_SANDBOX_CANONICAL_ORIGIN_URL"] = "git@github.com:example/repo.git"

    subprocess.run(
        [str(script), "--create-pr", "--base", "main", str(host_repo), "review/session-123/work"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    commands = git_log.read_text(encoding="utf-8").splitlines()
    base_fetch_index = next(
        i
        for i, command in enumerate(commands)
        if "fetch agent-sandbox-pr-origin refs/heads/main:refs/remotes/agent-sandbox-pr-origin/main"
        in command
    )
    review_fetch_index = next(
        i
        for i, command in enumerate(commands)
        if "fetch agent-sandbox-pr-origin refs/heads/review/session-123/work:refs/remotes/agent-sandbox-pr-origin/review/session-123/work"
        in command
    )
    assert review_fetch_index < base_fetch_index

    gh_commands = gh_log.read_text(encoding="utf-8").splitlines()
    assert any(
        command.startswith(
            "pr create --repo git@github.com:example/repo.git --base main --head review/session-123/work --title Automated PR title --fill"
        )
        for command in gh_commands
    )


def test_open_agent_pr_preserves_existing_azure_pr_title_by_default(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts/open_agent_pr.sh"
    host_repo = tmp_path / "repo"
    host_repo.mkdir()
    (host_repo / ".git").mkdir()

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    python_log = tmp_path / "python.log"
    attach_log = tmp_path / "attach.log"

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
  ls-remote)
    exit 2
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

if [[ -n "${{RUNTIME_DIR_PATH:-}}" ]]; then
  printf '%s' "${{script}}" | "{sys.executable}" - "$@"
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
  elif [[ "${{script}}" == *'uploaded_attachments'* ]]; then
    printf '%s' "[]"
  else
    printf '%s' "42"
  fi
  exit 0
fi

if [[ -n "${{ORIGIN_URL:-}}" && -n "${{AZURE_DEVOPS_PR_PAT_VALUE:-}}" ]]; then
  printf 'TITLE=%s\\nTITLE_EXPLICIT=%s\\nCREATE_PRS_AS_DRAFT=%s\\n' "${{TITLE:-}}" "${{TITLE_EXPLICIT:-}}" "${{CREATE_PRS_AS_DRAFT:-}}" >> "{python_log}"
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
    (fake_bin / "agent-sandbox-cli").write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "{attach_log}"
""",
        encoding="utf-8",
    )
    os.chmod(fake_bin / "git", 0o755)
    os.chmod(fake_bin / "python3", 0o755)
    os.chmod(fake_bin / "agent-sandbox-cli", 0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["AGENT_SANDBOX_PR_MAP_PATH"] = str(tmp_path / "runtime" / "pr-map.json")
    env["AGENT_SANDBOX_CLI_BIN"] = str(fake_bin / "agent-sandbox-cli")
    env["AZURE_DEVOPS_PR_PAT"] = "test-pat"

    result = subprocess.run(
        [
            str(script),
            "--create-pr",
            "--provider",
            "azure_devops",
            str(host_repo),
            "review/session-123/work",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    lines = [line for line in result.stdout.splitlines() if line.startswith("AGENT_PR_RESULT ")]
    assert lines, result.stdout
    payload = json.loads(lines[-1].split(" ", 1)[1])
    assert payload["provider"] == "azure_devops"
    logged = python_log.read_text(encoding="utf-8")
    assert "TITLE=Latest commit title" in logged
    assert "TITLE_EXPLICIT=0" in logged
    assert "CREATE_PRS_AS_DRAFT=0" in logged
    attach_args = attach_log.read_text(encoding="utf-8").strip()
    assert "attach-pr-feedback" in attach_args
    assert "--provider azure_devops" in attach_args
    assert "--repo example-org/example-project/example-repo" in attach_args
    assert "--pr-id 42" in attach_args
    assert "session-123" in attach_args


def test_open_agent_pr_truncates_existing_azure_pr_description(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts/open_agent_pr.sh"
    host_repo = tmp_path / "repo"
    host_repo.mkdir()
    (host_repo / ".git").mkdir()
    body_file = tmp_path / "body.md"
    body_file.write_text("A" * 4500, encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    request_log = tmp_path / "requests.jsonl"
    sitecustomize_dir = tmp_path / "pythonpath"
    sitecustomize_dir.mkdir()

    (fake_bin / "git").write_text(
        """#!/usr/bin/env bash
set -euo pipefail
args=("$@")
if [[ "${args[0]}" == "-C" ]]; then
  args=("${args[@]:2}")
fi
while [[ "${args[0]:-}" == "-c" ]]; do
  args=("${args[@]:2}")
done
case "${args[0]:-}" in
  rev-parse)
    ;;
  remote)
    if [[ "${args[1]:-}" == "get-url" ]]; then
      echo "https://dev.azure.com/example-org/example-project/_git/example-repo"
    else
      echo "unexpected git remote invocation: $*" >&2
      exit 1
    fi
    ;;
  push)
    ;;
  ls-remote)
    exit 2
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
        f'#!/usr/bin/env bash\nexec "{sys.executable}" "$@"\n',
        encoding="utf-8",
    )
    (fake_bin / "agent-sandbox-cli").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n",
        encoding="utf-8",
    )
    (sitecustomize_dir / "sitecustomize.py").write_text(
        f"""
import io
import json
import urllib.request

log_path = {str(request_log)!r}


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def response(payload):
    return Response(json.dumps(payload).encode("utf-8"))


def fake_urlopen(request):
    method = request.get_method()
    url = request.full_url
    data = request.data or b""
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {{
                    "method": method,
                    "url": url,
                    "body": data.decode("utf-8", "replace"),
                }},
                sort_keys=True,
            )
            + "\\n"
        )
    if "searchCriteria.status=active" in url:
        return response(
            {{
                "count": 1,
                "value": [
                    {{
                        "pullRequestId": 42,
                        "repository": {{"id": "repo-guid", "project": {{"id": "project-guid"}}}},
                    }}
                ],
            }}
        )
    if method == "PATCH" and url.endswith("/pullrequests/42?api-version=7.1"):
        return response(
            {{
                "pullRequestId": 42,
                "_links": {{
                    "web": {{
                        "href": "https://dev.azure.com/example-org/example-project/_git/example-repo/pullrequest/42"
                    }}
                }},
            }}
        )
    raise AssertionError(f"unexpected request: {{method}} {{url}}")


urllib.request.urlopen = fake_urlopen
""",
        encoding="utf-8",
    )
    os.chmod(fake_bin / "git", 0o755)
    os.chmod(fake_bin / "python3", 0o755)
    os.chmod(fake_bin / "agent-sandbox-cli", 0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["PYTHONPATH"] = str(sitecustomize_dir)
    env["AGENT_SANDBOX_PR_MAP_PATH"] = str(tmp_path / "runtime" / "pr-map.json")
    env["AGENT_SANDBOX_CLI_BIN"] = str(fake_bin / "agent-sandbox-cli")
    env["AZURE_DEVOPS_PR_PAT"] = "test-pat"

    subprocess.run(
        [
            str(script),
            "--create-pr",
            "--provider",
            "azure_devops",
            "--body-file",
            str(body_file),
            str(host_repo),
            "review/session-123/work",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    requests = [
        json.loads(line)
        for line in request_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    patch_payload = json.loads(next(item for item in requests if item["method"] == "PATCH")["body"])
    assert patch_payload["description"] == "A" * 4000


def test_open_agent_pr_associates_azure_pr_with_work_item_derived_from_session_id(
    tmp_path: Path,
) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts/open_agent_pr.sh"
    host_repo = tmp_path / "repo"
    host_repo.mkdir()
    (host_repo / ".git").mkdir()

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    python_log = tmp_path / "python.log"
    attach_log = tmp_path / "attach.log"

    (fake_bin / "git").write_text(
        """#!/usr/bin/env bash
set -euo pipefail
args=("$@")
if [[ "${args[0]}" == "-C" ]]; then
  args=("${args[@]:2}")
fi
while [[ "${args[0]:-}" == "-c" ]]; do
  args=("${args[@]:2}")
done
case "${args[0]:-}" in
  rev-parse)
    ;;
  remote)
    echo "https://dev.azure.com/example-org/example-project/_git/example-repo"
    ;;
  push)
    ;;
  ls-remote)
    exit 2
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

if [[ -n "${{RUNTIME_DIR_PATH:-}}" ]]; then
  printf '%s' "${{script}}" | "{sys.executable}" - "$@"
  exit 0
fi

if [[ -n "${{MAP_PATH:-}}" ]]; then
  mkdir -p "$(dirname "${{MAP_PATH}}")"
  printf '{{"version":1,"entries":{{}}}}\\n' > "${{MAP_PATH}}"
  exit 0
fi

if [[ -n "${{PROVIDER:-}}" && -n "${{PR_URL:-}}" && -n "${{PR_ID:-}}" && -n "${{ORIGIN_BRANCH:-}}" ]]; then
  printf '{{"provider":"%s","pr_url":"%s","pr_id":"%s","origin_branch":"%s","base_branch":"%s","session_id":"%s","work_item_id":"%s"}}\\n' \
    "${{PROVIDER}}" "${{PR_URL}}" "${{PR_ID}}" "${{ORIGIN_BRANCH}}" "${{BASE_BRANCH}}" "${{SESSION_ID:-}}" "${{WORK_ITEM_ID:-}}"
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

if [[ -n "${{ORIGIN_URL:-}}" && -n "${{AZURE_DEVOPS_PR_PAT_VALUE:-}}" ]]; then
  printf 'WORK_ITEM_ID=%s\\n' "${{WORK_ITEM_ID:-}}" >> "{python_log}"
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
    (fake_bin / "agent-sandbox-cli").write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "{attach_log}"
""",
        encoding="utf-8",
    )
    os.chmod(fake_bin / "git", 0o755)
    os.chmod(fake_bin / "python3", 0o755)
    os.chmod(fake_bin / "agent-sandbox-cli", 0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["AGENT_SANDBOX_PR_MAP_PATH"] = str(tmp_path / "runtime" / "pr-map.json")
    env["AGENT_SANDBOX_CLI_BIN"] = str(fake_bin / "agent-sandbox-cli")
    env["AZURE_DEVOPS_PR_PAT"] = "test-pat"

    result = subprocess.run(
        [
            str(script),
            "--create-pr",
            "--provider",
            "azure_devops",
            str(host_repo),
            "review/issue-14084-abc/work",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    lines = [line for line in result.stdout.splitlines() if line.startswith("AGENT_PR_RESULT ")]
    assert lines, result.stdout
    payload = json.loads(lines[-1].split(" ", 1)[1])
    assert payload["provider"] == "azure_devops"
    assert payload["origin_branch"] == "feature/issue-14084"
    assert payload["work_item_id"] == "14084"
    logged = python_log.read_text(encoding="utf-8")
    assert "WORK_ITEM_ID=14084" in logged


def test_open_agent_pr_uses_existing_https_helper_remote_for_azure_push(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts/open_agent_pr.sh"
    host_repo = tmp_path / "repo"
    host_repo.mkdir()
    (host_repo / ".git").mkdir()

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    git_log = tmp_path / "git.log"
    attach_log = tmp_path / "attach.log"

    (fake_bin / "git").write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "{git_log}"
args=("$@")
if [[ "${{args[0]}}" == "-C" ]]; then
  args=("${{args[@]:2}}")
fi
while [[ "${{args[0]:-}}" == "-c" ]]; do
  args=("${{args[@]:2}}")
done
case "${{args[0]:-}}" in
  rev-parse)
    ;;
  remote)
    if [[ "${{args[1]:-}}" == "get-url" && "${{args[2]:-}}" == "origin" ]]; then
      echo "git@ssh.dev.azure.com:v3/example-org/example-project/example-repo"
    elif [[ "${{args[1]:-}}" == "get-url" && "${{args[2]:-}}" == "agent-sandbox-pr-origin" ]]; then
      echo "https://dev.azure.com/example-org/example-project/_git/example-repo"
    else
      echo "unexpected git remote invocation: $*" >&2
      exit 1
    fi
    ;;
  push)
    ;;
  symbolic-ref)
    echo "agent-sandbox-pr-origin/main"
    ;;
  log)
    echo "Latest commit title"
    ;;
  ls-remote)
    exit 2
    ;;
  fetch)
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

if [[ -n "${{RUNTIME_DIR_PATH:-}}" ]]; then
  printf '%s' "${{script}}" | "{sys.executable}" - "$@"
  exit 0
fi

if [[ -n "${{MAP_PATH:-}}" ]]; then
  mkdir -p "$(dirname "${{MAP_PATH}}")"
  printf '{{"version":1,"entries":{{}}}}\\n' > "${{MAP_PATH}}"
  exit 0
fi

if [[ -n "${{PROVIDER:-}}" && -n "${{PR_URL:-}}" && -n "${{PR_ID:-}}" && -n "${{ORIGIN_BRANCH:-}}" ]]; then
  printf '{{"provider":"%s","pr_url":"%s","pr_id":"%s","origin_branch":"%s","base_branch":"%s","session_id":"%s","work_item_id":"%s"}}\\n' \\
    "${{PROVIDER}}" "${{PR_URL}}" "${{PR_ID}}" "${{ORIGIN_BRANCH}}" "${{BASE_BRANCH}}" "${{SESSION_ID:-}}" "${{WORK_ITEM_ID:-}}"
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

if [[ -n "${{ORIGIN_URL:-}}" ]]; then
  printf '%s' "example-org/example-project/example-repo"
  exit 0
fi

exit 1
""",
        encoding="utf-8",
    )
    (fake_bin / "agent-sandbox-cli").write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "{attach_log}"
""",
        encoding="utf-8",
    )
    os.chmod(fake_bin / "git", 0o755)
    os.chmod(fake_bin / "python3", 0o755)
    os.chmod(fake_bin / "agent-sandbox-cli", 0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["AGENT_SANDBOX_PR_MAP_PATH"] = str(tmp_path / "runtime" / "pr-map.json")
    env["AGENT_SANDBOX_CLI_BIN"] = str(fake_bin / "agent-sandbox-cli")
    env["AZURE_DEVOPS_PR_PAT"] = "test-pat"

    result = subprocess.run(
        [
            str(script),
            "--create-pr",
            "--provider",
            "azure_devops",
            str(host_repo),
            "review/issue-123/work",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert (
        "Pushed review/issue-123/work to agent-sandbox-pr-origin/feature/issue-123" in result.stdout
    )
    git_commands = git_log.read_text(encoding="utf-8")
    assert (
        "push agent-sandbox-pr-origin review/issue-123/work:refs/heads/feature/issue-123"
        in git_commands
    )


def test_open_agent_pr_creates_azure_draft_pr_from_directory_config(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts/open_agent_pr.sh"
    host_repo = tmp_path / "repo"
    host_repo.mkdir()
    (host_repo / ".git").mkdir()

    runtime_dir = tmp_path / "runtime"
    (runtime_dir / "directory-config" / "root" / "team-a").mkdir(parents=True)
    (runtime_dir / "sessions.json").write_text(
        json.dumps({"session-123": {"directory_path": "root/team-a"}}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (runtime_dir / "directory-config" / "root" / "ado-worker.json").write_text(
        json.dumps({"create_prs_as_draft": False}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (runtime_dir / "directory-config" / "root" / "team-a" / "ado-worker.json").write_text(
        json.dumps({"create_prs_as_draft": True}, indent=2, sort_keys=True),
        encoding="utf-8",
    )

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
while [[ "${args[0]:-}" == "-c" ]]; do
  args=("${args[@]:2}")
done
case "${args[0]:-}" in
  rev-parse)
    ;;
  remote)
    echo "https://dev.azure.com/example-org/example-project/_git/example-repo"
    ;;
  push)
    ;;
  ls-remote)
    exit 2
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

if [[ -n "${{RUNTIME_DIR_PATH:-}}" ]]; then
  printf '%s' "${{script}}" | "{sys.executable}" - "$@"
  exit 0
fi

if [[ -n "${{MAP_PATH:-}}" ]]; then
  mkdir -p "$(dirname "${{MAP_PATH}}")"
  printf '{{"version":1,"entries":{{}}}}\\n' > "${{MAP_PATH}}"
  exit 0
fi

if [[ -n "${{PROVIDER:-}}" && -n "${{PR_URL:-}}" && -n "${{PR_ID:-}}" && -n "${{ORIGIN_BRANCH:-}}" ]]; then
  printf '{{"provider":"%s","pr_url":"%s","pr_id":"%s","origin_branch":"%s","base_branch":"%s","session_id":"%s","work_item_id":"%s"}}\\n' \
    "${{PROVIDER}}" "${{PR_URL}}" "${{PR_ID}}" "${{ORIGIN_BRANCH}}" "${{BASE_BRANCH}}" "${{SESSION_ID:-}}" "${{WORK_ITEM_ID:-}}"
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

if [[ -n "${{ORIGIN_URL:-}}" && -n "${{AZURE_DEVOPS_PR_PAT_VALUE:-}}" ]]; then
  printf 'CREATE_PRS_AS_DRAFT=%s\\n' "${{CREATE_PRS_AS_DRAFT:-}}" >> "{python_log}"
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
    (fake_bin / "agent-sandbox-cli").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n",
        encoding="utf-8",
    )
    os.chmod(fake_bin / "git", 0o755)
    os.chmod(fake_bin / "python3", 0o755)
    os.chmod(fake_bin / "agent-sandbox-cli", 0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["AGENT_SANDBOX_PR_MAP_PATH"] = str(tmp_path / "runtime" / "pr-map.json")
    env["AGENT_SANDBOX_CLI_BIN"] = str(fake_bin / "agent-sandbox-cli")
    env["AGENT_SANDBOX_RUNTIME_DIR"] = str(runtime_dir)
    env["AZURE_DEVOPS_PR_PAT"] = "test-pat"

    subprocess.run(
        [
            str(script),
            "--create-pr",
            "--provider",
            "azure_devops",
            str(host_repo),
            "review/session-123/work",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    logged = python_log.read_text(encoding="utf-8")
    assert "CREATE_PRS_AS_DRAFT=1" in logged


def test_open_agent_pr_uploads_azure_body_artifact_and_updates_description(
    tmp_path: Path,
) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts/open_agent_pr.sh"
    host_repo = tmp_path / "repo"
    host_repo.mkdir()
    (host_repo / ".git").mkdir()

    runtime_dir = tmp_path / "runtime"
    artifact_dir = runtime_dir / "communication-artifacts"
    artifact_dir.mkdir(parents=True)
    artifact_id = "comm_11111111111111111111111111111111"
    (artifact_dir / f"{artifact_id}.bin").write_bytes(b"png-bytes")
    (artifact_dir / f"{artifact_id}.json").write_text(
        json.dumps(
            {
                "artifact_id": artifact_id,
                "session_id": "session-1",
                "filename": "screen.png",
                "normalized_safe_filename": "screen.png",
                "original_filename": "screen.png",
                "mime_type": "image/png",
                "byte_count": len(b"png-bytes"),
            }
        ),
        encoding="utf-8",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    request_log = tmp_path / "requests.jsonl"
    sitecustomize_dir = tmp_path / "pythonpath"
    sitecustomize_dir.mkdir()

    (fake_bin / "git").write_text(
        """#!/usr/bin/env bash
set -euo pipefail
args=("$@")
if [[ "${args[0]}" == "-C" ]]; then
  args=("${args[@]:2}")
fi
while [[ "${args[0]:-}" == "-c" ]]; do
  args=("${args[@]:2}")
done
case "${args[0]:-}" in
  rev-parse)
    ;;
  remote)
    echo "https://dev.azure.com/example-org/example-project/_git/example-repo"
    ;;
  push)
    ;;
  ls-remote)
    exit 2
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
        f'#!/usr/bin/env bash\nexec "{sys.executable}" "$@"\n',
        encoding="utf-8",
    )
    (fake_bin / "agent-sandbox-cli").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n",
        encoding="utf-8",
    )
    (sitecustomize_dir / "sitecustomize.py").write_text(
        f"""
import base64
import io
import json
import os
import urllib.request

log_path = {str(request_log)!r}


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def response(payload):
    return Response(json.dumps(payload).encode("utf-8"))


def fake_urlopen(request):
    method = request.get_method()
    url = request.full_url
    data = request.data or b""
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {{
                    "method": method,
                    "url": url,
                    "headers": dict(request.header_items()),
                    "body": data.decode("utf-8", "replace"),
                    "body_base64": base64.b64encode(data).decode("ascii"),
                }},
                sort_keys=True,
            )
            + "\\n"
        )
    if "searchCriteria.status=active" in url:
        return response({{"count": 0, "value": []}})
    if method == "POST" and url.endswith("/pullrequests?api-version=7.1"):
        return response(
            {{
                "pullRequestId": 42,
                "repository": {{"id": "repo-guid", "project": {{"id": "project-guid"}}}},
                "_links": {{
                    "web": {{
                        "href": "https://dev.azure.com/example-org/example-project/_git/example-repo/pullrequest/42"
                    }}
                }},
            }}
        )
    if method == "POST" and url.endswith("/attachments/screen.png?api-version=7.1"):
        return response(
            {{
                "url": "https://dev.azure.com/example-org/example-project/_apis/git/repositories/example-repo/pullRequests/42/attachments/screen.png"
            }}
        )
    if method == "PATCH" and url.endswith("/pullrequests/42?api-version=7.1"):
        return response(
            {{
                "pullRequestId": 42,
                "_links": {{
                    "web": {{
                        "href": "https://dev.azure.com/example-org/example-project/_git/example-repo/pullrequest/42"
                    }}
                }},
            }}
        )
    raise AssertionError(f"unexpected request: {{method}} {{url}}")


urllib.request.urlopen = fake_urlopen
""",
        encoding="utf-8",
    )
    os.chmod(fake_bin / "git", 0o755)
    os.chmod(fake_bin / "python3", 0o755)
    os.chmod(fake_bin / "agent-sandbox-cli", 0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["PYTHONPATH"] = str(sitecustomize_dir)
    env["AGENT_SANDBOX_PR_MAP_PATH"] = str(tmp_path / "runtime" / "pr-map.json")
    env["AGENT_SANDBOX_CLI_BIN"] = str(fake_bin / "agent-sandbox-cli")
    env["AGENT_SANDBOX_RUNTIME_DIR"] = str(runtime_dir)
    env["AZURE_DEVOPS_PR_PAT"] = "test-pat"

    result = subprocess.run(
        [
            str(script),
            "--create-pr",
            "--provider",
            "azure_devops",
            "--session-id",
            "session-1",
            "--body-artifact-id",
            artifact_id,
            str(host_repo),
            "review/session-1/work",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    requests = [
        json.loads(line)
        for line in request_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    upload_request = next(
        item
        for item in requests
        if item["method"] == "POST" and "/attachments/screen.png" in item["url"]
    )
    assert upload_request["url"].endswith("/attachments/screen.png?api-version=7.1")
    assert upload_request["body_base64"] == "cG5nLWJ5dGVz"
    patch_payload = json.loads(next(item for item in requests if item["method"] == "PATCH")["body"])
    assert (
        "![screen.png](https://dev.azure.com/example-org/example-project/_apis/git/repositories/example-repo/pullRequests/42/attachments/screen.png)"
        in patch_payload["description"]
    )
    lines = [line for line in result.stdout.splitlines() if line.startswith("AGENT_PR_RESULT ")]
    payload = json.loads(lines[-1].split(" ", 1)[1])
    assert payload["uploaded_attachments"][0]["filename"] == "screen.png"
