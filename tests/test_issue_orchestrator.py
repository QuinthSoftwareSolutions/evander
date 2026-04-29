from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from agent_sandbox.daemons.issue_orchestrator import IssueOrchestratorWorker
from agent_sandbox.models import SessionManifest, ToolName


def _session(*, session_id: str, vm_id: str, role: str) -> SessionManifest:
    return SessionManifest(
        session_id=session_id,
        vm_id=vm_id,
        directory_path="root/agent-sandbox/test",
        allowed_tools=[ToolName.LIST_ISSUES],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        metadata={"automation_role": role},
    )


def test_issue_orchestrator_uses_dispatch_only_for_resume(tmp_path: Path, monkeypatch) -> None:
    resume_script = tmp_path / "resume_issue_agent.sh"
    resume_script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    resume_script.chmod(0o755)

    captured: list[tuple[list[str], dict[str, str] | None]] = []

    def _fake_run(cmd, check, capture_output, text, timeout, env=None):  # noqa: ANN001
        captured.append((cmd, env))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.daemons.issue_orchestrator.subprocess.run", _fake_run)

    worker = object.__new__(IssueOrchestratorWorker)
    worker.config = SimpleNamespace(host_agent_control_state_path=tmp_path / "host-control.json")
    worker.worker_config = SimpleNamespace(
        resume_script_path=resume_script,
        wake_state_path=tmp_path / "wake-state.json",
        vm_user="agent",
        resume_timeout_seconds=30.0,
        runtime_dir=tmp_path,
        cooldown_seconds=60.0,
        directories=["root/agent-sandbox/test"],
        poll_interval_seconds=30.0,
        jitter_seconds=0.0,
        busy_check_script_path=tmp_path / "check_busy.sh",
        lock_path=tmp_path / "issue-orchestrator.lock",
    )

    assert worker._resume_session(  # noqa: SLF001
        _session(session_id="bug-session", vm_id="bug-finder-vm", role="bug_finder"),
        prompt="Find one bug",
    )

    assert len(captured) == 1
    cmd, env = captured[0]
    assert cmd[0] == str(resume_script)
    assert "--worker-mode" in cmd
    assert "--dispatch-only" in cmd
    assert "--vm-name" in cmd and "bug-finder-vm" in cmd
    assert "--session-id" in cmd and "bug-session" in cmd
    assert env is None


def test_issue_orchestrator_sets_agent_model_for_resume(tmp_path: Path, monkeypatch) -> None:
    resume_script = tmp_path / "resume_issue_agent.sh"
    resume_script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    resume_script.chmod(0o755)

    captured: dict[str, object] = {}

    def _fake_run(cmd, check, capture_output, text, timeout, env=None):  # noqa: ANN001
        captured["cmd"] = cmd
        captured["env"] = env
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("agent_sandbox.daemons.issue_orchestrator.subprocess.run", _fake_run)
    monkeypatch.setattr(
        "agent_sandbox.daemons.issue_orchestrator.resolve_effective_issue_setup_config",
        lambda **_: SimpleNamespace(default_model="gpt-5.4"),
    )

    worker = object.__new__(IssueOrchestratorWorker)
    worker.config = SimpleNamespace(host_agent_control_state_path=tmp_path / "host-control.json")
    worker.worker_config = SimpleNamespace(
        resume_script_path=resume_script,
        wake_state_path=tmp_path / "wake-state.json",
        vm_user="agent",
        resume_timeout_seconds=30.0,
        runtime_dir=tmp_path,
        cooldown_seconds=60.0,
        directories=["root/agent-sandbox/test"],
        poll_interval_seconds=30.0,
        jitter_seconds=0.0,
        busy_check_script_path=tmp_path / "check_busy.sh",
        lock_path=tmp_path / "issue-orchestrator.lock",
    )
    worker.runtime = SimpleNamespace(directories=object(), sessions=object())

    assert worker._resume_session(  # noqa: SLF001
        _session(session_id="bug-session", vm_id="bug-finder-vm", role="bug_finder"),
        prompt="Find one bug",
    )

    env = captured["env"]
    assert isinstance(env, dict)
    assert env["AGENT_MODEL"] == "gpt-5.4"
