"""Test reporting; production state and services are never shared between cases."""

import pytest

pytest_plugins = ["suite_reporting"]


@pytest.fixture
def socket_tmp_path(tmp_path_factory: pytest.TempPathFactory):
    """Keep AF_UNIX addresses below the OS limit with xdist's worker directory."""
    return tmp_path_factory.mktemp("sock")
