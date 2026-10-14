import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def addon_pythonpath(monkeypatch):
    """Child-process tests must use the same add-on source as their parent."""
    paths = [str(Path(__file__).resolve().parents[1])]
    if os.environ.get("PYTHONPATH"):
        paths.append(os.environ["PYTHONPATH"])
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(paths))


@pytest.fixture
def socket_tmp_path(tmp_path_factory):
    return tmp_path_factory.mktemp("sock")
