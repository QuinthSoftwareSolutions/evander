from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path


AGENT_BRANCH = "agents/session-1/topic"
REVIEW_BRANCH = "review/session-1/topic"
PROMOTION_REF = f"refs/agent-sandbox/promotions/{AGENT_BRANCH}"
RESOLVED_COMMIT = "0123456789abcdef0123456789abcdef01234567"


def _write_fake_git(
    git_path: Path,
    git_log: Path,
    *,
    review_branch_exists: bool,
) -> None:
    review_exit = "exit 0" if review_branch_exists else "exit 1"
    git_path.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            set -euo pipefail
            printf '%s\\n' "$*" >> "{git_log}"
            args=("$@")
            if [[ "${{args[0]:-}}" == "-C" ]]; then
              args=("${{args[@]:2}}")
            fi

            if [[ "$*" == *FETCH_HEAD* ]]; then
              echo "unexpected FETCH_HEAD usage: $*" >&2
              exit 1
            fi

            case "${{args[*]}}" in
              "remote get-url agent-dropbox-auto")
                exit 2
                ;;
              "remote add agent-dropbox-auto "*)
                exit 0
                ;;
              "show-ref --verify --quiet refs/heads/{REVIEW_BRANCH}")
                {review_exit}
                ;;
              "fetch agent-dropbox-auto "*)
                printf 'From %s\\n' "agent-dropbox-auto"
                printf ' * [new branch]      {AGENT_BRANCH} -> {PROMOTION_REF}\\n'
                exit 0
                ;;
              "rev-parse --verify {PROMOTION_REF}^{{commit}}")
                printf '%s\\n' "{RESOLVED_COMMIT}"
                exit 0
                ;;
              "switch -C {REVIEW_BRANCH} {RESOLVED_COMMIT}")
                exit 0
                ;;
              "switch -c {REVIEW_BRANCH} {RESOLVED_COMMIT}")
                exit 0
                ;;
              *)
                echo "unexpected git invocation: $*" >&2
                exit 1
                ;;
            esac
            """
        ),
        encoding="utf-8",
    )
    os.chmod(git_path, 0o755)


def _run_promote_agent_branch(
    tmp_path: Path,
    *,
    review_branch_exists: bool,
) -> tuple[subprocess.CompletedProcess[str], str]:
    script = Path(__file__).resolve().parents[1] / "scripts" / "promote_agent_branch.sh"
    host_repo = tmp_path / "repo"
    bare_repo = tmp_path / "sample.git"
    fake_bin = tmp_path / "bin"
    git_log = tmp_path / "git.log"

    (host_repo / ".git").mkdir(parents=True)
    bare_repo.mkdir()
    fake_bin.mkdir()

    _write_fake_git(fake_bin / "git", git_log, review_branch_exists=review_branch_exists)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    result = subprocess.run(
        [
            str(script),
            str(host_repo),
            str(bare_repo),
            AGENT_BRANCH,
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    return result, git_log.read_text(encoding="utf-8")


def test_promote_agent_branch_refreshes_existing_review_branch(tmp_path: Path) -> None:
    result, log_text = _run_promote_agent_branch(tmp_path, review_branch_exists=True)

    assert result.returncode == 0, result.stderr
    assert f"Fetched {AGENT_BRANCH} from agent-dropbox-auto" in result.stdout
    assert f"Refreshed local review branch: {REVIEW_BRANCH}" in result.stdout
    assert f"fetch agent-dropbox-auto +refs/heads/{AGENT_BRANCH}:{PROMOTION_REF}" in log_text
    assert f"rev-parse --verify {PROMOTION_REF}^{{commit}}" in log_text
    assert f"switch -C {REVIEW_BRANCH} {RESOLVED_COMMIT}" in log_text
    assert "FETCH_HEAD" not in log_text


def test_promote_agent_branch_creates_new_review_branch(tmp_path: Path) -> None:
    result, log_text = _run_promote_agent_branch(tmp_path, review_branch_exists=False)

    assert result.returncode == 0, result.stderr
    assert f"Fetched {AGENT_BRANCH} from agent-dropbox-auto" in result.stdout
    assert f"Created local review branch: {REVIEW_BRANCH}" in result.stdout
    assert f"fetch agent-dropbox-auto +refs/heads/{AGENT_BRANCH}:{PROMOTION_REF}" in log_text
    assert f"rev-parse --verify {PROMOTION_REF}^{{commit}}" in log_text
    assert f"switch -c {REVIEW_BRANCH} {RESOLVED_COMMIT}" in log_text
    assert "FETCH_HEAD" not in log_text


def test_promote_agent_branch_does_not_depend_on_fetch_head_after_successful_fetch(
    tmp_path: Path,
) -> None:
    result, log_text = _run_promote_agent_branch(tmp_path, review_branch_exists=True)

    assert result.returncode == 0, result.stderr
    assert f"fetch agent-dropbox-auto +refs/heads/{AGENT_BRANCH}:{PROMOTION_REF}" in log_text
    assert f"rev-parse --verify {PROMOTION_REF}^{{commit}}" in log_text
    assert f"switch -C {REVIEW_BRANCH} {RESOLVED_COMMIT}" in log_text
    assert "FETCH_HEAD" not in log_text
