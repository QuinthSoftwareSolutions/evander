from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def test_handle_git_push_trigger_enqueues_matching_branch_updates(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "handle_git_push_trigger.sh"

    repo_path = (tmp_path / "git-remotes" / "sample.git").resolve()
    repo_path.mkdir(parents=True)
    queue_path = tmp_path / "runtime" / "trigger-events.json"
    config_path = tmp_path / "trigger-config.json"
    config_path.write_text(
        json.dumps(
            {
                "runtime_dir": str(tmp_path / "runtime"),
                "event_queue_store_path": str(queue_path),
                "triggers": [
                    {
                        "id": "git-main",
                        "display_name": "Git main",
                        "type": "trigger:git:push",
                        "path_to_repo": str(repo_path),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    stdin_payload = "\n".join(
        [
            f"{'0' * 40} {'1' * 40} refs/heads/main",
            f"{'1' * 40} {'2' * 40} refs/tags/v1.0.0",
        ]
    )

    result = subprocess.run(
        [str(script), str(repo_path)],
        input=stdin_payload,
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "AGENT_SANDBOX_TRIGGER_ORCHESTRATOR_CONFIG": str(config_path),
        },
        cwd=root,
        check=False,
    )

    assert result.returncode == 0, result.stderr

    queue_payload = json.loads(queue_path.read_text(encoding="utf-8"))
    assert list(queue_payload.keys()) == [f"git-main:main:{'1' * 40}"]
    record = queue_payload[f"git-main:main:{'1' * 40}"]
    assert record["trigger_type"] == "trigger:git:push"
    assert record["payload"] == {"branch_name": "main", "commit_hash": "1" * 40}


def test_handle_git_push_trigger_ignores_non_matching_repo(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "handle_git_push_trigger.sh"

    configured_repo_path = (tmp_path / "git-remotes" / "configured.git").resolve()
    configured_repo_path.mkdir(parents=True)
    pushed_repo_path = (tmp_path / "git-remotes" / "incoming.git").resolve()
    pushed_repo_path.mkdir(parents=True)
    queue_path = tmp_path / "runtime" / "trigger-events.json"
    config_path = tmp_path / "trigger-config.json"
    config_path.write_text(
        json.dumps(
            {
                "runtime_dir": str(tmp_path / "runtime"),
                "event_queue_store_path": str(queue_path),
                "triggers": [
                    {
                        "id": "git-main",
                        "display_name": "Git main",
                        "type": "trigger:git:push",
                        "path_to_repo": str(configured_repo_path),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [str(script), str(pushed_repo_path)],
        input=f"{'0' * 40} {'1' * 40} refs/heads/main\n",
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "AGENT_SANDBOX_TRIGGER_ORCHESTRATOR_CONFIG": str(config_path),
        },
        cwd=root,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    if queue_path.exists():
        assert json.loads(queue_path.read_text(encoding="utf-8")) == {}
