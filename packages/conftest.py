"""Collect independent add-on tests directly from their project sources."""

from pathlib import Path
import sys


PACKAGES = Path(__file__).resolve().parent
projects = sorted(path for path in PACKAGES.iterdir() if (path / "tests").is_dir())
paths = [str(project / "tests") for project in projects] + [str(project) for project in projects]
core_tests = str(PACKAGES.parent / "tests")
position = sys.path.index(core_tests) + 1 if core_tests in sys.path else 0
sys.path[position:position] = [path for path in paths if path not in sys.path]
