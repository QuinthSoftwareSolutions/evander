"""Keep child-process tests on the same add-on source as their parent."""

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def addon_pythonpath(monkeypatch):
    paths = [str(Path(__file__).resolve().parent)]
    if os.environ.get("PYTHONPATH"):
        paths.append(os.environ["PYTHONPATH"])
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(paths))
