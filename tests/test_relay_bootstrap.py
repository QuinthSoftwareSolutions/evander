from pathlib import Path
from types import ModuleType

import pytest

from agent_sandbox.relays import http_target, nuget, openai


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("relay_module", [http_target, nuget, openai])
def test_relay_entrypoints_bootstrap_from_repo_runtime(relay_module: ModuleType) -> None:
    assert relay_module._project_root() == ROOT
    assert relay_module._project_root() / "runtime" == ROOT / "runtime"
