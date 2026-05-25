from __future__ import annotations

import json
import os
import select
import socket
import subprocess
import time
import uuid
from pathlib import Path

from agent_sandbox.git_pr_notes import agent_pr_notes_ref_for_session
from agent_sandbox.triggers.queue import SqliteTriggerEventQueue


def test_handle_git_push_trigger_enqueues_matching_branch_updates(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "handle_git_push_trigger.sh"

    repo_path = (tmp_path / "git-remotes" / "sample.git").resolve()
    repo_path.mkdir(parents=True)
    queue_path = tmp_path / "runtime" / "trigger-events.json"
    wake_path = tmp_path / "runtime" / "effect-orchestrator.wake.sock"
    wake_path.parent.mkdir(parents=True, exist_ok=True)
    wake_listener = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    wake_listener.bind(str(wake_path))
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

    ready = []
    try:
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
        ready, _, _ = select.select([wake_listener], [], [], 1.0)
    finally:
        wake_listener.close()
        wake_path.unlink(missing_ok=True)

    assert result.returncode == 0, result.stderr
    assert ready

    queue_payload = json.loads(queue_path.read_text(encoding="utf-8"))
    assert list(queue_payload.keys()) == [f"git-main:main:{'1' * 40}"]
    record = queue_payload[f"git-main:main:{'1' * 40}"]
    assert record["trigger_type"] == "trigger:git:push"
    assert record["payload"]["branch_name"] == "main"
    assert record["payload"]["commit_hash"] == "1" * 40
    assert record["payload"]["repo_name"] == "sample"
    assert record["payload"]["repo_path"] == str(repo_path)


def test_handle_git_push_trigger_enqueues_to_sqlite_backend(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "handle_git_push_trigger.sh"

    repo_path = (tmp_path / "git-remotes" / "sample.git").resolve()
    repo_path.mkdir(parents=True)
    queue_path = tmp_path / "runtime" / "trigger-events.json"
    db_path = tmp_path / "runtime" / "orchestration.db"
    config_path = tmp_path / "trigger-config.json"
    config_path.write_text(
        json.dumps(
            {
                "runtime_dir": str(tmp_path / "runtime"),
                "event_queue_store_path": str(queue_path),
                "orchestration_store_backend": "sqlite",
                "orchestration_store_path": str(db_path),
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

    result = subprocess.run(
        [str(script), str(repo_path)],
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
    assert not queue_path.exists()
    queued = SqliteTriggerEventQueue(db_path).read_all()
    assert list(queued) == [f"git-main:main:{'1' * 40}"]
    record = queued[f"git-main:main:{'1' * 40}"]
    assert record.delivery_state == "pending"
    assert record.trigger_type == "trigger:git:push"
    assert record.payload["branch_name"] == "main"
    assert record.payload["commit_hash"] == "1" * 40


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
                        "path_glob": str(configured_repo_path.parent / "configured*.git"),
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


def test_handle_git_push_trigger_emits_repo_metadata_and_session_id(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "handle_git_push_trigger.sh"

    repo_path = (tmp_path / "git-remotes" / "sample.git").resolve()
    subprocess.run(
        ["git", "init", "--bare", str(repo_path)], check=True, capture_output=True, text=True
    )
    subprocess.run(
        [
            "git",
            f"--git-dir={repo_path}",
            "config",
            "agent-sandbox.trustedRepoPath",
            "/trusted/repo",
        ],
        check=True,
    )
    subprocess.run(
        ["git", f"--git-dir={repo_path}", "config", "agent-sandbox.baseBranch", "main"], check=True
    )
    subprocess.run(
        [
            "git",
            f"--git-dir={repo_path}",
            "config",
            "agent-sandbox.canonicalOriginUrl",
            "https://example.com/org/repo.git",
        ],
        check=True,
    )
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
                        "path_glob": str(repo_path.parent / "*.git"),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [str(script), str(repo_path)],
        input=f"{'0' * 40} {'1' * 40} refs/heads/agents/session-1/topic-branch\n",
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
    record = queue_payload[f"git-main:agents/session-1/topic-branch:{'1' * 40}"]
    assert record["payload"]["session_id"] == "session-1"
    assert record["payload"]["review_branch"] == "review/session-1/topic-branch"
    assert record["payload"]["notes_ref"] == agent_pr_notes_ref_for_session("session-1")
    assert record["payload"]["trusted_repo_path"] == "/trusted/repo"
    assert record["payload"]["base_branch"] == "main"
    assert record["payload"]["canonical_origin_url"] == "https://example.com/org/repo.git"


def test_handle_git_push_trigger_enqueues_notes_only_update_for_matching_agent_branch_tip(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "handle_git_push_trigger.sh"

    repo_path = (tmp_path / "git-remotes" / "sample.git").resolve()
    worktree = (tmp_path / "worktree").resolve()
    subprocess.run(
        ["git", "init", "--bare", str(repo_path)], check=True, capture_output=True, text=True
    )
    subprocess.run(["git", "init", str(worktree)], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(worktree), "config", "user.name", "Test User"], check=True)
    subprocess.run(
        ["git", "-C", str(worktree), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(
        ["git", "-C", str(worktree), "remote", "add", "origin", str(repo_path)], check=True
    )
    subprocess.run(
        [
            "git",
            f"--git-dir={repo_path}",
            "config",
            "agent-sandbox.trustedRepoPath",
            "/trusted/repo",
        ],
        check=True,
    )
    subprocess.run(
        ["git", f"--git-dir={repo_path}", "config", "agent-sandbox.baseBranch", "main"], check=True
    )

    (worktree / "hello.txt").write_text("Hello World", encoding="utf-8")
    subprocess.run(["git", "-C", str(worktree), "add", "hello.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(worktree), "commit", "-m", "Initial"],
        check=True,
        capture_output=True,
        text=True,
    )
    tip_sha = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "-C", str(worktree), "push", "origin", "HEAD:refs/heads/agents/session-1/topic"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(worktree), "notes", "--ref=agent-pr", "add", "-m", "Summary", "HEAD"],
        check=True,
    )
    old_notes_rev = "0" * 40
    subprocess.run(
        ["git", "-C", str(worktree), "push", "origin", "refs/notes/agent-pr"],
        check=True,
        capture_output=True,
        text=True,
    )
    new_notes_rev = subprocess.run(
        ["git", f"--git-dir={repo_path}", "rev-parse", "refs/notes/agent-pr"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

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

    result = subprocess.run(
        [str(script), str(repo_path)],
        input=f"{old_notes_rev} {new_notes_rev} refs/notes/agent-pr\n",
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
    record = queue_payload[f"git-main:agents/session-1/topic:{new_notes_rev}"]
    assert record["payload"]["branch_name"] == "agents/session-1/topic"
    assert record["payload"]["session_id"] == "session-1"
    assert record["payload"]["review_branch"] == "review/session-1/topic"
    assert record["payload"]["notes_ref"] == "refs/notes/agent-pr"
    assert record["payload"]["commit_hash"] == tip_sha
    assert record["payload"]["note_ref_new_rev"] == new_notes_rev


def test_handle_git_push_trigger_enqueues_per_session_notes_only_update(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "handle_git_push_trigger.sh"

    repo_path = (tmp_path / "git-remotes" / "sample.git").resolve()
    worktree = (tmp_path / "worktree").resolve()
    subprocess.run(
        ["git", "init", "--bare", str(repo_path)], check=True, capture_output=True, text=True
    )
    subprocess.run(["git", "init", str(worktree)], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(worktree), "config", "user.name", "Test User"], check=True)
    subprocess.run(
        ["git", "-C", str(worktree), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(
        ["git", "-C", str(worktree), "remote", "add", "origin", str(repo_path)], check=True
    )

    (worktree / "hello.txt").write_text("Hello World", encoding="utf-8")
    subprocess.run(["git", "-C", str(worktree), "add", "hello.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(worktree), "commit", "-m", "Initial"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(worktree), "push", "origin", "HEAD:refs/heads/agents/session-1/topic"],
        check=True,
        capture_output=True,
        text=True,
    )
    notes_ref = agent_pr_notes_ref_for_session("session-1")
    subprocess.run(
        ["git", "-C", str(worktree), "notes", f"--ref={notes_ref}", "add", "-m", "Summary", "HEAD"],
        check=True,
    )
    old_notes_rev = "0" * 40
    subprocess.run(
        ["git", "-C", str(worktree), "push", "origin", notes_ref],
        check=True,
        capture_output=True,
        text=True,
    )
    new_notes_rev = subprocess.run(
        ["git", f"--git-dir={repo_path}", "rev-parse", notes_ref],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

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

    result = subprocess.run(
        [str(script), str(repo_path)],
        input=f"{old_notes_rev} {new_notes_rev} {notes_ref}\n",
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
    record = queue_payload[f"git-main:agents/session-1/topic:{new_notes_rev}"]
    assert record["payload"]["notes_ref"] == notes_ref


def test_handle_git_push_trigger_is_idempotent_for_repeated_updates(tmp_path: Path) -> None:
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

    stdin_payload = f"{'0' * 40} {'1' * 40} refs/heads/main\n"

    first = subprocess.run(
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
    second = subprocess.run(
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

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    queue_payload = json.loads(queue_path.read_text(encoding="utf-8"))
    assert list(queue_payload.keys()) == [f"git-main:main:{'1' * 40}"]


def test_handle_git_push_trigger_resolves_relative_queue_paths_from_config_dir(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "handle_git_push_trigger.sh"

    repo_path = (tmp_path / "git-remotes" / "sample.git").resolve()
    repo_path.mkdir(parents=True)
    config_dir = tmp_path / "orchestrator"
    config_dir.mkdir()
    config_path = config_dir / "trigger-config.json"
    queue_path = config_dir / "runtime" / "trigger-orchestrator-events.json"
    config_path.write_text(
        json.dumps(
            {
                "runtime_dir": "runtime",
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

    bare_repo_cwd = tmp_path / "hook-cwd.git"
    bare_repo_cwd.mkdir()

    result = subprocess.run(
        [str(script), str(repo_path)],
        input=f"{'0' * 40} {'1' * 40} refs/heads/main\n",
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "AGENT_SANDBOX_TRIGGER_ORCHESTRATOR_CONFIG": str(config_path),
        },
        cwd=bare_repo_cwd,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert queue_path.exists()
    assert not (bare_repo_cwd / "runtime" / "trigger-orchestrator-events.json").exists()


def test_handle_git_push_trigger_resolves_relative_queue_paths_from_project_root_for_repo_config(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "handle_git_push_trigger.sh"

    repo_path = (tmp_path / "git-remotes" / "sample.git").resolve()
    repo_path.mkdir(parents=True)

    config_dir = root / "config" / "trigger-orchestrator"
    config_path = config_dir / f"test-trigger-config-{uuid.uuid4().hex}.json"
    relative_queue = Path(".tmp-trigger-tests") / f"{config_path.stem}-events.json"
    project_queue_path = root / relative_queue
    config_dir_queue_path = config_dir / relative_queue

    config_path.write_text(
        json.dumps(
            {
                "runtime_dir": "runtime",
                "event_queue_store_path": str(relative_queue),
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

    try:
        if project_queue_path.exists():
            project_queue_path.unlink()
        if config_dir_queue_path.exists():
            config_dir_queue_path.unlink()

        result = subprocess.run(
            [str(script), str(repo_path)],
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
        assert project_queue_path.exists()
        assert not config_dir_queue_path.exists()
    finally:
        config_path.unlink(missing_ok=True)
        project_queue_path.unlink(missing_ok=True)
        try:
            project_queue_path.parent.rmdir()
        except OSError:
            pass


def test_handle_git_push_trigger_resolves_git_push_triggers_from_workflow_packs(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "handle_git_push_trigger.sh"

    repo_path = (tmp_path / "git-remotes" / "sample.git").resolve()
    repo_path.mkdir(parents=True)

    config_dir = root / "config" / "trigger-orchestrator"
    packs_dir = root / "config" / "workflow-packs"
    unique = uuid.uuid4().hex
    config_path = config_dir / f"test-trigger-config-{unique}.json"
    pack_path = packs_dir / f"test-git-push-pack-{unique}.v1.json"
    relative_queue = Path(".tmp-trigger-tests") / f"{unique}-events.json"
    queue_path = root / relative_queue

    pack_path.write_text(
        json.dumps(
            {
                "pack_id": f"test-git-push-pack-{unique}",
                "pack_version": "v1",
                "ir": {
                    "version": "v1",
                    "nodes": [
                        {
                            "id": "git-pack-trigger",
                            "display_name": "Git push",
                            "type": "trigger:git:push",
                            "config": {
                                "path_to_repo": str(repo_path),
                            },
                        },
                        {
                            "id": "git-pack-trigger-disabled",
                            "display_name": "Disabled Git push",
                            "type": "trigger:git:push",
                            "enabled": False,
                            "config": {
                                "path_to_repo": str(repo_path),
                            },
                        }
                    ],
                    "edges": [],
                },
            }
        ),
        encoding="utf-8",
    )
    config_path.write_text(
        json.dumps(
            {
                "runtime_dir": "runtime",
                "event_queue_store_path": str(relative_queue),
                "triggers": [
                    {
                        "id": "system-chat",
                        "display_name": "System Chat",
                        "type": "trigger:system:chat-assistant",
                    }
                ],
                "workflow_packs": [pack_path.name],
            }
        ),
        encoding="utf-8",
    )

    try:
        queue_path.unlink(missing_ok=True)

        result = subprocess.run(
            [str(script), str(repo_path)],
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
        queue_payload = json.loads(queue_path.read_text(encoding="utf-8"))
        assert list(queue_payload.keys()) == [f"git-pack-trigger:main:{'1' * 40}"]
    finally:
        config_path.unlink(missing_ok=True)
        pack_path.unlink(missing_ok=True)
        queue_path.unlink(missing_ok=True)
        try:
            queue_path.parent.rmdir()
        except OSError:
            pass


def test_handle_git_push_trigger_serializes_concurrent_queue_updates(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "handle_git_push_trigger.sh"

    repo_path = (tmp_path / "git-remotes" / "sample.git").resolve()
    repo_path.mkdir(parents=True)
    queue_path = tmp_path / "runtime" / "trigger-events.json"
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    queue_path.write_text(
        json.dumps({f"seed-{index}": {"event_id": f"seed-{index}"} for index in range(2000)}),
        encoding="utf-8",
    )
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

    first = subprocess.Popen(
        [str(script), str(repo_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            **os.environ,
            "AGENT_SANDBOX_TRIGGER_ORCHESTRATOR_CONFIG": str(config_path),
            "AGENT_SANDBOX_TRIGGER_QUEUE_LOCK_HOLD_SECONDS": "0.2",
        },
        cwd=root,
    )
    assert first.stdin is not None
    first.stdin.write(f"{'0' * 40} {'1' * 40} refs/heads/main\n")
    first.stdin.close()

    time.sleep(0.05)

    second = subprocess.run(
        [str(script), str(repo_path)],
        input=f"{'0' * 40} {'2' * 40} refs/heads/feature\n",
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "AGENT_SANDBOX_TRIGGER_ORCHESTRATOR_CONFIG": str(config_path),
        },
        cwd=root,
        check=False,
    )
    assert first.wait(timeout=5) == 0
    first_stdout = "" if first.stdout is None else first.stdout.read()
    first_stderr = "" if first.stderr is None else first.stderr.read()

    assert second.returncode == 0, second.stderr
    assert first_stdout == ""
    assert first_stderr == ""
    queue_payload = json.loads(queue_path.read_text(encoding="utf-8"))
    assert f"git-main:main:{'1' * 40}" in queue_payload
    assert f"git-main:feature:{'2' * 40}" in queue_payload


def test_importing_config_does_not_require_trigger_registry() -> None:
    from agent_sandbox.config import GatewayConfig

    config = GatewayConfig()

    assert config.runtime_dir == Path("/var/lib/agent-sandbox")
