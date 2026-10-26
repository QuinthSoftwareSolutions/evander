import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("mode,count", [("--reap", 1), ("--reap-loop", 2)])
def test_fixture_cleanup_builds_one_service(tmp_path, monkeypatch, mode, count):
    from evander.resources import reaper
    from evander_test_fixtures_addon import worker_api

    config = tmp_path / "worker.json"
    config.write_text(
        json.dumps(
            {
                "worker_id": "worker-a",
                "runtime_dir": str(tmp_path),
                "token_env": "TEST_TOKEN",
            }
        )
    )
    instances, ticks = [], []

    class Service:
        def __init__(self, settings):
            instances.append(self)

        def tick(self):
            ticks.append(self)

    monkeypatch.setattr(worker_api, "WorkerService", Service)
    monkeypatch.setattr(reaper, "run_cleanup_loop", lambda tick: (tick(), tick()))
    monkeypatch.setattr(sys, "argv", ["worker", "--config", str(config), mode])
    worker_api.main()
    assert len(instances) == 1
    assert ticks == [instances[0]] * count


def test_fixture_worker_import_has_no_control_plane_dependencies():
    project = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(project.parents[1] / "src"), str(project)]),
    }
    script = """
import sys
import evander_test_fixtures_addon.worker_api
for prefix in ('evander.admin', 'evander.gateway', 'mcp'):
    assert not any(n == prefix or n.startswith(prefix + '.') for n in sys.modules), prefix
"""
    result = subprocess.run(
        [sys.executable, "-c", script], env=env, text=True, capture_output=True, timeout=10
    )
    assert result.returncode == 0, result.stderr
