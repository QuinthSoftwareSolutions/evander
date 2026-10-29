from pathlib import Path
import shutil
import subprocess

import pytest


def test_operator_ui_binds_mutations_to_loaded_directory(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the operator UI behavior regression")
    result = subprocess.run(
        [node, str(Path(__file__).with_name("ui_scope.test.cjs"))],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
