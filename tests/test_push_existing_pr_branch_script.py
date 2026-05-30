from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def _write_fake_open_agent_pr(path: Path, *, body: str, exit_code: int = 0) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"cat <<'EOF'\n{body.rstrip()}\nEOF\n"
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _run_wrapper(tmp_path: Path, fake_open_agent_pr: Path) -> subprocess.CompletedProcess[str]:
    script = Path(__file__).resolve().parents[1] / "scripts" / "push_existing_pr_branch.sh"
    env = os.environ.copy()
    env["OPEN_AGENT_PR"] = str(fake_open_agent_pr)
    return subprocess.run(
        [str(script), "--provider", "azure_devops", "/repo", "review/session/work", "feature/topic"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
    )


def test_push_existing_pr_branch_wraps_successful_push_only_output(tmp_path: Path) -> None:
    fake_open_agent_pr = tmp_path / "open_agent_pr.sh"
    _write_fake_open_agent_pr(
        fake_open_agent_pr,
        body=(
            "Pushed review/session/work to agent-sandbox-pr-origin/feature/topic\n\n"
            "Open a PR manually or rerun with --create-pr."
        ),
    )

    result = _run_wrapper(tmp_path, fake_open_agent_pr)

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload == {
        "action": "push_existing_pr_branch",
        "message": (
            "Pushed review/session/work to agent-sandbox-pr-origin/feature/topic\n\n"
            "Open a PR manually or rerun with --create-pr."
        ),
        "success": True,
    }
    assert "Pushed review/session/work" in result.stderr


def test_push_existing_pr_branch_fails_on_logical_preflight_failure(tmp_path: Path) -> None:
    fake_open_agent_pr = tmp_path / "open_agent_pr.sh"
    _write_fake_open_agent_pr(
        fake_open_agent_pr,
        body=(
            'AGENT_PR_RESULT {"success": false, "reason": "destination_branch_ahead", '
            '"message": "merge first", "origin_branch": "feature/topic"}'
        ),
    )

    result = _run_wrapper(tmp_path, fake_open_agent_pr)

    assert result.returncode == 20
    payload = json.loads(result.stdout)
    assert payload["success"] is False
    assert payload["action"] == "push_existing_pr_branch"
    assert payload["reason"] == "destination_branch_ahead"
    assert payload["message"] == "merge first"
    assert payload["origin_branch"] == "feature/topic"
    assert "AGENT_PR_RESULT" in result.stderr


def test_push_existing_pr_branch_normalizes_ref_prefixed_branch_args(tmp_path: Path) -> None:
    fake_open_agent_pr = tmp_path / "open_agent_pr.sh"
    args_log = tmp_path / "args.log"
    fake_open_agent_pr.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"printf '%s\\n' \"$@\" > {args_log}\n"
        "printf '%s\\n' 'Pushed branch'\n",
        encoding="utf-8",
    )
    fake_open_agent_pr.chmod(0o755)

    script = Path(__file__).resolve().parents[1] / "scripts" / "push_existing_pr_branch.sh"
    env = os.environ.copy()
    env["OPEN_AGENT_PR"] = str(fake_open_agent_pr)
    result = subprocess.run(
        [
            str(script),
            "--provider",
            "azure_devops",
            "--base",
            "refs/heads/main",
            "/repo",
            "review/session/work",
            "refs/heads/bugfix/topic",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
    )

    assert result.returncode == 0
    assert args_log.read_text(encoding="utf-8").splitlines() == [
        "--provider",
        "azure_devops",
        "--base",
        "main",
        "/repo",
        "review/session/work",
        "bugfix/topic",
    ]
