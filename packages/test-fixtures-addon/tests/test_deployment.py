from configparser import ConfigParser
from pathlib import Path
import shlex

import pytest


@pytest.mark.parametrize("unit", ["worker", "reap"])
def test_fixture_services_allow_sqlite_sorting_without_broad_host_writes(unit):
    """A missing private temp dir breaks job reads once SQLite spills its sort."""
    path = (
        Path(__file__).resolve().parents[1]
        / "examples/systemd"
        / f"evander-test-fixtures-{unit}.service"
    )
    config = ConfigParser(interpolation=None)
    config.read(path)
    service = config["Service"]

    assert service.getboolean("PrivateTmp")
    assert service["ProtectSystem"] == "strict"
    assert service.getboolean("ProtectHome")
    assert service.getboolean("NoNewPrivileges")
    assert set(shlex.split(service["ReadWritePaths"])) == {
        "/var/lib/evander-test-fixtures",
        "/var/lib/agent-worker/runtime",
    }
