from pathlib import Path
from types import ModuleType

import pytest

from evander.gateway import cli as gateway_cli
from evander.relays import http_target, nuget, openai


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("relay_module", [http_target, nuget, openai])
def test_relay_entrypoints_bootstrap_from_repo_runtime(relay_module: ModuleType) -> None:
    assert relay_module._project_root() == ROOT
    assert relay_module._project_root() / "runtime" == ROOT / "runtime"


def test_gateway_entrypoint_bootstraps_from_repo_runtime() -> None:
    assert gateway_cli._project_root() == ROOT
    assert gateway_cli._project_root() / "runtime" == ROOT / "runtime"
