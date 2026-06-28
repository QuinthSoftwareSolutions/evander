import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "setup"


def _script_text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_setup_bootstrap_script_has_valid_bash_syntax() -> None:
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_setup_bootstrap_script_help_describes_one_step_setup() -> None:
    result = subprocess.run(
        [str(SCRIPT), "--help"],
        check=True,
        text=True,
        capture_output=True,
    )

    assert "Usage: scripts/setup [OPTIONS] [-- EVANDER_CLI_SETUP_ARGS...]" in result.stdout
    assert "then launch the guided setup checklist" in result.stdout
    assert "./scripts/setup -- --runtime-dir runtime" in result.stdout


def test_setup_bootstrap_script_installs_python_prereqs_and_repo_venv() -> None:
    script = _script_text()

    assert "apt-get update" in script
    assert "apt-get install -y python3 python3-venv python3-pip" in script
    assert 'elif command -v sudo >/dev/null 2>&1; then' in script
    assert "elif command -v pkexec >/dev/null 2>&1; then" in script
    assert '"${PYTHON_BIN}" -m venv "${VENV_DIR}"' in script
    assert '"${VENV_DIR}/bin/python" -m pip install -e "${install_spec}"' in script


def test_setup_bootstrap_script_transitions_to_evander_setup() -> None:
    script = _script_text()

    assert 'local evander_cli="${VENV_DIR}/bin/evander-cli"' in script
    assert 'exec "${evander_cli}" setup "${SETUP_ARGS[@]}"' in script
    assert "--bootstrap-only|--no-setup)" in script
    assert "EVANDER_SETUP_BOOTSTRAP_ONLY=1" in script
    assert 'install_spec=".[dev]"' in script
