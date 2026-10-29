"""Run the optional integration's tests against this project's source tree."""

import os
from pathlib import Path
import sys

import pytest

ADDON_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON_ROOT))


@pytest.fixture(autouse=True)
def addon_pythonpath(monkeypatch):
    """Child-process tests must use the same add-on source as their parent."""
    paths = [str(ADDON_ROOT)]
    if os.environ.get("PYTHONPATH"):
        paths.append(os.environ["PYTHONPATH"])
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(paths))
