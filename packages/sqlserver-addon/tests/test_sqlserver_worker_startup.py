import os
from pathlib import Path
import subprocess
import sys


def test_worker_import_avoids_control_plane_and_supports_cleanup_modes():
    project = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(project.parents[1] / "src"), str(project)]),
    }
    script = """
import sys
from evander_sqlserver_addon.worker import main
for prefix in ('evander.admin', 'evander.gateway', 'mcp'):
    assert not any(n == prefix or n.startswith(prefix + '.') for n in sys.modules), prefix
main()
"""
    result = subprocess.run(
        [sys.executable, "-c", script, "--help"],
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "--reap-loop" in result.stdout and "--reap" in result.stdout
