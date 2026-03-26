from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_cli_module_entrypoint_runs_help() -> None:
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    src_path = str(root / "src")
    env["PYTHONPATH"] = src_path if not existing_pythonpath else f"{src_path}:{existing_pythonpath}"

    result = subprocess.run(
        [sys.executable, "-m", "agent_sandbox.cli", "--help"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert "Utilities for the agent sandbox host control plane." in result.stdout
