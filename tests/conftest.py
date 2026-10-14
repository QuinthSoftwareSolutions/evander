"""Test reporting; production state and services are never shared between cases."""

import pytest

pytest_plugins = ["suite_reporting"]


@pytest.fixture(autouse=True)
def isolate_systemd_invocation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests opt into service-generation checks instead of inheriting the test host."""
    monkeypatch.delenv("INVOCATION_ID", raising=False)


@pytest.fixture
def socket_tmp_path(tmp_path_factory: pytest.TempPathFactory):
    """Keep AF_UNIX addresses below the OS limit with xdist's worker directory."""
    return tmp_path_factory.mktemp("sock")
