from __future__ import annotations

import os
import stat
import subprocess
import time
from pathlib import Path


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def test_guest_heartbeat_cycle_uses_persisted_model_from_env_file(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    script_path = root / "scripts" / "guest" / "heartbeat_cycle.sh"

    home = tmp_path / "home"
    lib_dir = home / ".config" / "agent-sandbox" / "agent-runner-lib"
    env_file = home / ".config" / "agent-sandbox" / "guest-heartbeat.env"
    fake_bin = tmp_path / "bin"
    lib_dir.mkdir(parents=True)
    fake_bin.mkdir()

    (lib_dir / "agent_runner.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    env_file.write_text(
        "AGENT_MODEL='gpt-5.4'\nAGENT_SANDBOX_GUEST_HEARTBEAT_PROMPT='Resume from env file'\n",
        encoding="utf-8",
    )
    _write_executable(
        fake_bin / "agent_runner_resume_last",
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "${HOME}/resume-call.log"
sleep 0.2
""",
    )

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env.pop("AGENT_MODEL", None)

    result = subprocess.run(
        ["bash", str(script_path)],
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    time.sleep(0.1)

    assert "Guest heartbeat resumed agent pid=" in result.stdout
    resume_call = (home / "resume-call.log").read_text(encoding="utf-8")
    assert "--model gpt-5.4" in resume_call
    assert "Resume from env file" in resume_call
