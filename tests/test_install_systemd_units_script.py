from pathlib import Path


def _script_text() -> str:
    return (Path(__file__).resolve().parents[1] / "scripts" / "install_systemd_units.sh").read_text(encoding="utf-8")


def test_install_systemd_units_supports_preflight_help_output() -> None:
    text = _script_text()

    assert "Usage: install_systemd_units.sh [--preflight] [--help]" in text
    assert "--preflight" in text
    assert "Run validation checks only (non-destructive)." in text


def test_install_systemd_units_runs_preflight_before_install() -> None:
    text = _script_text()

    assert 'if [[ "${MODE}" == "preflight" ]]; then' in text
    assert "run_preflight_checks" in text
    assert "Aborting install because preflight checks failed." in text


def test_install_systemd_units_preflight_reports_failures_with_hints() -> None:
    text = _script_text()

    assert "[PASS]" in text
    assert "[FAIL]" in text
    assert "hint:" in text
    assert "Result: FAIL (critical checks failed)." in text
