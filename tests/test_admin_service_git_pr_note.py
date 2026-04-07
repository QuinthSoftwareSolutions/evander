from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from agent_sandbox.admin_service import AdminService


def test_admin_service_git_pr_note_query_reads_note_from_cached_ref(tmp_path: Path, monkeypatch: Any) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    service = AdminService(runtime_dir)
    captured: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.append(cmd)
        if cmd[:6] == ["git", "-C", "/repo", "remote", "get-url", "agent-dropbox"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="/git-remotes/sample.git\n", stderr="")
        if cmd[:5] == ["git", "-C", "/repo", "fetch", "agent-dropbox"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if cmd[:5] == ["git", "-C", "/repo", "rev-parse", "review/session-1/topic"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=f"{'a'*40}\n", stderr="")
        if cmd[:5] == ["git", "-C", "/repo", "notes", "--ref=refs/notes/agent-pr-cache/sample"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="Agent note body\n", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="unexpected")

    monkeypatch.setattr("agent_sandbox.admin_service.subprocess.run", _fake_run)

    result = service.git_pr_note_query(host_repo_path="/repo", local_branch="review/session-1/topic")

    assert result["has_note"] is True
    assert result["body"] == "Agent note body"
    assert result["local_notes_ref"] == "refs/notes/agent-pr-cache/sample"
    assert result["remote_name"] == "agent-dropbox"
    assert result["commit_sha"] == "a" * 40


def test_admin_service_git_pr_note_query_returns_has_note_false_when_note_missing(tmp_path: Path, monkeypatch: Any) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    service = AdminService(runtime_dir)

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[:6] == ["git", "-C", "/repo", "remote", "get-url", "agent-dropbox"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="/git-remotes/sample.git\n", stderr="")
        if cmd[:5] == ["git", "-C", "/repo", "fetch", "agent-dropbox"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if cmd[:5] == ["git", "-C", "/repo", "rev-parse", "review/session-1/topic"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=f"{'a'*40}\n", stderr="")
        if cmd[:5] == ["git", "-C", "/repo", "notes", "--ref=refs/notes/agent-pr-cache/sample"]:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="missing")
        return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="unexpected")

    monkeypatch.setattr("agent_sandbox.admin_service.subprocess.run", _fake_run)

    result = service.git_pr_note_query(host_repo_path="/repo", local_branch="review/session-1/topic")

    assert result["has_note"] is False
    assert result["body"] is None
