from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from agent_sandbox.admin_service import AdminService


def test_admin_service_promote_agent_branch_invokes_script_and_derives_review_branch(
    tmp_path: Path, monkeypatch: Any
) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    service = AdminService(runtime_dir)
    captured: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr("agent_sandbox.admin_service.subprocess.run", _fake_run)

    result = service.promote_agent_branch(
        host_repo_path="/trusted/repo",
        bare_repo_path="/git-remotes/sample.git",
        agent_branch="agents/session-1/topic",
    )

    assert captured == [[
        str(service.project_dir / "scripts" / "promote_agent_branch.sh"),
        "/trusted/repo",
        "/git-remotes/sample.git",
        "agents/session-1/topic",
    ]]
    assert result["success"] is True
    assert result["review_branch"] == "review/session-1/topic"
    assert result["remote_name"] == "agent-dropbox"


def test_admin_service_promote_agent_branch_passes_optional_flags(tmp_path: Path, monkeypatch: Any) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    service = AdminService(runtime_dir)
    captured: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr("agent_sandbox.admin_service.subprocess.run", _fake_run)

    result = service.promote_agent_branch(
        host_repo_path="/trusted/repo",
        bare_repo_name="sample",
        agent_branch="feature/topic",
        review_branch="review/custom",
        remote_name="agent-dropbox-auto",
    )

    assert captured == [[
        str(service.project_dir / "scripts" / "promote_agent_branch.sh"),
        "--remote-name",
        "agent-dropbox-auto",
        "/trusted/repo",
        "sample",
        "feature/topic",
        "review/custom",
    ]]
    assert result["bare_repo_path"] == "sample"
    assert result["review_branch"] == "review/custom"
