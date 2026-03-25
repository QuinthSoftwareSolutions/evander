from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _write_fake_git(path: Path, trusted_repo: Path) -> None:
    path.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail

log_file="${{FAKE_GIT_LOG:-}}"
if [[ -n "$log_file" ]]; then
  printf '%s\n' "$*" >> "$log_file"
fi

if [[ "${{1:-}}" == --git-dir=* ]]; then
  if [[ "${{2:-}}" == "config" && "${{3:-}}" == "--get" ]]; then
    case "${{4:-}}" in
      agent-sandbox.trustedRepoPath)
        echo "{trusted_repo}"
        exit 0
        ;;
      agent-sandbox.baseBranch)
        echo "main"
        exit 0
        ;;
    esac
  fi
  exit 1
fi

if [[ "${{1:-}}" == "-C" ]]; then
  repo="$2"
  shift 2
else
  repo=""
fi

cmd="${{1:-}}"
shift || true

case "$cmd" in
  remote)
    sub="${{1:-}}"
    if [[ "$sub" == "get-url" ]]; then
      if [[ "${{FAKE_REMOTE_EXISTS:-0}}" == "1" ]]; then
        echo "${{FAKE_REMOTE_URL:-/tmp/remote}}"
        exit 0
      fi
      exit 1
    fi
    if [[ "$sub" == "add" ]]; then
      export FAKE_REMOTE_EXISTS=1
      export FAKE_REMOTE_URL="$3"
      exit 0
    fi
    if [[ "$sub" == "set-url" ]]; then
      export FAKE_REMOTE_EXISTS=1
      export FAKE_REMOTE_URL="$3"
      exit 0
    fi
    ;;
  fetch)
    if [[ "$*" == *"refs/notes/agent-pr:refs/notes/agent-pr"* ]]; then
      if [[ "${{FAKE_NOTES_FETCH_FAIL:-0}}" == "1" ]]; then
        exit 1
      fi
    fi
    exit 0
    ;;
  branch)
    if [[ "${{1:-}}" == "--show-current" ]]; then
      echo "main"
      exit 0
    fi
    exit 0
    ;;
  show-ref)
    exit 1
    ;;
  rev-parse)
    if [[ "${{FAKE_SHA_RESOLVE_FAIL:-0}}" == "1" ]]; then
      exit 1
    fi
    echo "${{FAKE_RESOLVED_SHA:-1111111111111111111111111111111111111111}}"
    exit 0
    ;;
  notes)
    if [[ "${{FAKE_NOTES_READ_FAIL:-0}}" == "1" ]]; then
      exit 1
    fi
    if [[ -n "${{FAKE_NOTE_CONTENT:-}}" ]]; then
      printf '%s' "${{FAKE_NOTE_CONTENT}}"
      exit 0
    fi
    exit 1
    ;;
  *)
    exit 0
    ;;
esac

exit 0
""",
        encoding="utf-8",
    )
    os.chmod(path, 0o755)


def _write_fake_open_agent_pr(path: Path, args_log: Path) -> None:
    path.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "{args_log}"
printf 'AGENT_PR_RESULT {{"provider":"github","pr_id":"1"}}\n'
""",
        encoding="utf-8",
    )
    os.chmod(path, 0o755)


def _run_handle_push(
    tmp_path: Path,
    *,
    note_content: str | None,
    fixed_body: str | None,
    pr_body_max_bytes: int | None = None,
    notes_fetch_fail: bool = False,
    notes_read_fail: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    root = Path(__file__).resolve().parents[1]
    source_script = root / "scripts" / "handle_agent_push.sh"

    sandbox_root = tmp_path / "sandbox"
    scripts_dir = sandbox_root / "scripts"
    scripts_dir.mkdir(parents=True)

    handle_script = scripts_dir / "handle_agent_push.sh"
    handle_script.write_text(source_script.read_text(encoding="utf-8"), encoding="utf-8")
    os.chmod(handle_script, 0o755)

    args_log = tmp_path / "open-agent-pr-args.log"
    _write_fake_open_agent_pr(scripts_dir / "open_agent_pr.sh", args_log)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()

    trusted_repo = tmp_path / "trusted"
    (trusted_repo / ".git").mkdir(parents=True)

    bare_repo = tmp_path / "bare.git"
    bare_repo.mkdir()

    _write_fake_git(fake_bin / "git", trusted_repo)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["FAKE_GIT_LOG"] = str(tmp_path / "git.log")
    env["FAKE_NOTES_FETCH_FAIL"] = "1" if notes_fetch_fail else "0"
    env["FAKE_NOTES_READ_FAIL"] = "1" if notes_read_fail else "0"
    if note_content is not None:
        env["FAKE_NOTE_CONTENT"] = note_content
    if pr_body_max_bytes is not None:
        env["AGENT_SANDBOX_PR_BODY_MAX_BYTES"] = str(pr_body_max_bytes)

    fixed_body_file = tmp_path / "fixed.md"
    if fixed_body is not None:
        fixed_body_file.write_text(fixed_body, encoding="utf-8")
        env["AGENT_SANDBOX_PR_FIXED_BODY_FILE"] = str(fixed_body_file)

    result = subprocess.run(
        [str(handle_script), str(bare_repo)],
        input=(
            "0000000000000000000000000000000000000000 "
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa "
            "refs/heads/agents/session-123/topic\n"
        ),
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    event_log = sandbox_root / "runtime" / "agent-events.log"
    return result, args_log, event_log


def _extract_body_file_path(args_log: Path) -> str | None:
    entries = [line.strip() for line in args_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert entries, "open_agent_pr.sh was not invoked"
    latest = entries[-1].split()
    for index, token in enumerate(latest):
        if token == "--body-file":
            return latest[index + 1]
    return None


def test_handle_agent_push_body_file_cases(tmp_path: Path) -> None:
    fixed = "Static fixed body\n- item"
    note = "Agent note body\nMore detail"

    _, args_log, _ = _run_handle_push(tmp_path / "both", note_content=note, fixed_body=fixed)
    composed_path = _extract_body_file_path(args_log)
    assert composed_path is not None
    composed_content = Path(composed_path).read_text(encoding="utf-8")
    assert composed_content == (
        "Static fixed body\n- item\n\n---\n\n## Agent Summary\n\nAgent note body\nMore detail\n"
    )

    _, args_log, _ = _run_handle_push(tmp_path / "fixed_only", note_content=None, fixed_body=fixed)
    fixed_only_path = _extract_body_file_path(args_log)
    assert fixed_only_path is not None
    assert Path(fixed_only_path).read_text(encoding="utf-8") == fixed

    _, args_log, _ = _run_handle_push(tmp_path / "note_only", note_content=note, fixed_body=None)
    note_only_path = _extract_body_file_path(args_log)
    assert note_only_path is not None
    assert Path(note_only_path).read_text(encoding="utf-8") == f"{note}\n"


def test_handle_agent_push_force_fetches_agent_pr_notes(tmp_path: Path) -> None:
    _, _, _ = _run_handle_push(tmp_path / "force_fetch", note_content="Agent note", fixed_body=None)
    git_log = (tmp_path / "force_fetch" / "git.log").read_text(encoding="utf-8")
    assert "+refs/notes/agent-pr:refs/notes/agent-pr-cache/" in git_log

    _, args_log, _ = _run_handle_push(tmp_path / "neither", note_content=None, fixed_body=None)
    assert _extract_body_file_path(args_log) is None


def test_handle_agent_push_truncates_oversized_body_with_notice(tmp_path: Path) -> None:
    note = "A" * 300
    max_bytes = 120

    _, args_log, _ = _run_handle_push(
        tmp_path,
        note_content=note,
        fixed_body=None,
        pr_body_max_bytes=max_bytes,
    )

    body_path = _extract_body_file_path(args_log)
    assert body_path is not None
    body_bytes = Path(body_path).read_bytes()
    assert len(body_bytes) <= max_bytes
    assert b"Truncated by handle_agent_push.sh after 120 bytes." in body_bytes


def test_handle_agent_push_continues_when_notes_fetch_or_read_fails(tmp_path: Path) -> None:
    _, args_log, event_log = _run_handle_push(
        tmp_path,
        note_content=None,
        fixed_body="Fixed content",
        notes_fetch_fail=True,
        notes_read_fail=True,
    )

    assert _extract_body_file_path(args_log) is not None
    log_text = event_log.read_text(encoding="utf-8")
    assert "AGENT_PR_NOTES fetch failed (non-fatal)" in log_text
    assert "AGENT_PR_NOTES read failed (non-fatal)" in log_text
    assert "Agent branch promoted and PR prepared" in log_text
