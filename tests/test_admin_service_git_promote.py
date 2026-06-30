from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from evander.admin.composition import AdminRuntimeComponents, build_admin_runtime


def test_admin_service_promote_agent_branch_invokes_script_and_derives_review_branch(
    tmp_path: Path, monkeypatch: Any
) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    service = build_admin_runtime(runtime_dir)
    captured: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.append(cmd)
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout="ok\nCommit: cafef00d\n",
            stderr="",
        )

    monkeypatch.setattr("evander.admin.services.process_helpers.subprocess.run", _fake_run)

    result = service.issue_git_admin.promote_agent_branch(
        host_repo_path="/trusted/repo",
        bare_repo_path="/git-remotes/sample.git",
        agent_branch="agents/session-1/topic",
    )

    assert captured == [
        [
            str(service.project_dir / "scripts" / "promote_agent_branch.sh"),
            "/trusted/repo",
            "/git-remotes/sample.git",
            "agents/session-1/topic",
        ]
    ]
    assert result["success"] is True
    assert result["review_branch"] == "review/session-1/topic"
    assert result["target_branch"] == "review/session-1/topic"
    assert result["commit_sha"] == "cafef00d"
    assert result["remote_name"] == "agent-dropbox-auto"


def test_admin_service_promote_agent_branch_passes_optional_flags(
    tmp_path: Path, monkeypatch: Any
) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    service = build_admin_runtime(runtime_dir)
    captured: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.append(cmd)
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout="ok\nCommit: cafef00d\n",
            stderr="",
        )

    monkeypatch.setattr("evander.admin.services.process_helpers.subprocess.run", _fake_run)

    result = service.issue_git_admin.promote_agent_branch(
        host_repo_path="/trusted/repo",
        bare_repo_name="sample",
        agent_branch="feature/topic",
        review_branch="review/custom",
        remote_name="agent-dropbox-auto",
    )

    assert captured == [
        [
            str(service.project_dir / "scripts" / "promote_agent_branch.sh"),
            "--remote-name",
            "agent-dropbox-auto",
            "/trusted/repo",
            "sample",
            "feature/topic",
            "review/custom",
        ]
    ]
    assert result["bare_repo_path"] == "sample"
    assert result["review_branch"] == "review/custom"
    assert result["target_branch"] == "review/custom"
    assert result["commit_sha"] == "cafef00d"


def test_admin_service_promote_agent_branch_accepts_target_branch(
    tmp_path: Path, monkeypatch: Any
) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    service = build_admin_runtime(runtime_dir)
    captured: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.append(cmd)
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout="ok\nCommit: 708b1b89\n",
            stderr="",
        )

    monkeypatch.setattr("evander.admin.services.process_helpers.subprocess.run", _fake_run)

    result = service.issue_git_admin.promote_agent_branch(
        host_repo_path="/trusted/repo",
        bare_repo_name="sample",
        agent_branch="agents/session-1/release",
        target_branch="team/team/releases/release-1",
        expected_commit_sha="708b1b89",
    )

    assert captured == [
        [
            str(service.project_dir / "scripts" / "promote_agent_branch.sh"),
            "--expected-commit-sha",
            "708b1b89",
            "/trusted/repo",
            "sample",
            "agents/session-1/release",
            "team/team/releases/release-1",
        ]
    ]
    assert result["review_branch"] == "team/team/releases/release-1"
    assert result["target_branch"] == "team/team/releases/release-1"
    assert result["commit_sha"] == "708b1b89"


def test_admin_service_promote_agent_branch_surfaces_phase_specific_script_error(
    tmp_path: Path, monkeypatch: Any
) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    service = build_admin_runtime(runtime_dir)

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=1,
            stdout="",
            stderr="Failed to resolve fetched commit for agents/session-1/topic\n",
        )

    monkeypatch.setattr("evander.admin.services.process_helpers.subprocess.run", _fake_run)

    with pytest.raises(RuntimeError, match="Failed to resolve fetched commit for agents/session-1/topic"):
        service.issue_git_admin.promote_agent_branch(
            host_repo_path="/trusted/repo",
            bare_repo_path="/git-remotes/sample.git",
            agent_branch="agents/session-1/topic",
        )
