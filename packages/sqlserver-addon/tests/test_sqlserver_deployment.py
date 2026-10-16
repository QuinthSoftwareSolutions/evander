from configparser import ConfigParser
from pathlib import Path
import shlex

import pytest


@pytest.mark.parametrize("unit", ["worker", "reap"])
def test_worker_and_independent_reaper_keep_private_writable_sqlite_temp(unit):
    root = Path(__file__).resolve().parents[1] / "examples/systemd"
    config = ConfigParser(interpolation=None)
    config.read(root / f"evander-sqlserver-{unit}.service")
    service = config["Service"]
    assert service.getboolean("PrivateTmp")
    assert service["ProtectSystem"] == "strict" and service.getboolean("NoNewPrivileges")
    assert set(shlex.split(service["ReadWritePaths"])) == {
        "/var/lib/evander-sqlserver-addon",
        "/var/lib/agent-worker/runtime",
    }
    command = shlex.split(service["ExecStart"])
    assert ("--reap" in command) == (unit == "reap")
    if unit == "reap":
        timer = ConfigParser(interpolation=None)
        timer.read(root / "evander-sqlserver-reap.timer")
        assert 0 < int(timer["Timer"]["OnUnitActiveSec"]) <= 5
