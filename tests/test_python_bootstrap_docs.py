from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_python_bootstrap_docs_and_script_are_present() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    ops = (ROOT / "docs" / "operations.md").read_text(encoding="utf-8")
    script = (ROOT / "scripts" / "bootstrap_python_vm.sh").read_text(encoding="utf-8")

    assert "bootstrap_python_vm.sh" in readme
    assert "bootstrap_python_vm.sh" in ops
    assert 'HOST_PYTHON_BIN="${HOST_PYTHON_BIN:-python3}"' in script
    assert 'GUEST_PYTHON_BIN="${GUEST_PYTHON_BIN:-python3}"' in script
    assert '"${HOST_PYTHON_BIN}" -m ensurepip --upgrade' in script
    assert '"${HOST_PYTHON_BIN}" -m pip wheel' in script
    assert 'virtualenv' in script
    assert 'from virtualenv import cli_run' in script
    assert 'cli_run([venv_path])' in script
    assert '--no-build-isolation \\' in script
    assert '-e "${GUEST_REPO_ROOT}[${PYTHON_EXTRAS}]"' in script
