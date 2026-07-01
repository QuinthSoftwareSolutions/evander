from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_python_package_bootstrap_docs_and_script_are_present() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    ops = (ROOT / "docs" / "operations.md").read_text(encoding="utf-8")
    config = (ROOT / "docs" / "configuration-reference.md").read_text(encoding="utf-8")
    script = (ROOT / "scripts" / "bootstrap_python_packages_vm.sh").read_text(encoding="utf-8")

    assert "bootstrap_python_packages_vm.sh" in readme
    assert "bootstrap_python_packages_vm.sh" in ops
    assert "bootstrap_python_packages_vm.sh" in config
    assert 'HOST_PYTHON_BIN="${HOST_PYTHON_BIN:-python3}"' in script
    assert 'GUEST_PYTHON_BIN="${GUEST_PYTHON_BIN:-python3}"' in script
    assert (
        'GUEST_VENV_PATH="${GUEST_VENV_PATH:-/home/${VM_USER}/.evander/python-tools-venv}"'
        in script
    )
    assert (
        'GUEST_WHEELHOUSE_DIR="${GUEST_WHEELHOUSE_DIR:-/home/${VM_USER}/.evander/python-wheelhouse/tools}"'
        in script
    )
    assert '"${HOST_PYTHON_BIN}" -m ensurepip --upgrade' in script
    assert '"${HOST_PYTHON_BIN}" -m pip wheel \\' in script
    assert "from virtualenv import cli_run" in script
    assert '"${GUEST_VENV_PATH}/bin/python" -m pip install \\' in script
    assert "--no-index \\" in script
    assert '--find-links "${GUEST_WHEELHOUSE_DIR}" \\' in script
