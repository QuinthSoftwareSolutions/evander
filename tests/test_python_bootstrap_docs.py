from pathlib import Path


def test_python_bootstrap_docs_and_script_are_present() -> None:
    readme = Path("/home/jonathan/agent-sandbox/README.md").read_text(encoding="utf-8")
    ops = Path("/home/jonathan/agent-sandbox/docs/operations.md").read_text(encoding="utf-8")
    script = Path("/home/jonathan/agent-sandbox/scripts/bootstrap_python_vm.sh").read_text(
        encoding="utf-8"
    )

    assert "bootstrap_python_vm.sh" in readme
    assert "bootstrap_python_vm.sh" in ops
    assert 'HOST_PYTHON_BIN="${HOST_PYTHON_BIN:-python3}"' in script
    assert 'GUEST_PYTHON_BIN="${GUEST_PYTHON_BIN:-python3}"' in script
    assert '"${HOST_PYTHON_BIN}" -m pip wheel' in script
    assert '"${GUEST_PYTHON_BIN}" -m venv "${GUEST_REPO_ROOT}/.venv"' in script
    assert '--no-build-isolation \\' in script
    assert '-e "${GUEST_REPO_ROOT}[${PYTHON_EXTRAS}]"' in script
