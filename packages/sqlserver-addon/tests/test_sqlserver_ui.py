from pathlib import Path
import os
import shutil
import subprocess
import pytest


def test_directory_actions_and_private_credential_form():
    node = os.environ.get("EVANDER_TEST_NODE") or shutil.which("node")
    if not node:
        pytest.skip("Node is required for operator UI behavior checks")
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_name("ui_scope.test.cjs"))],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
