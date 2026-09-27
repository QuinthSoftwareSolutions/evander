from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_privacy_maintenance_is_bounded_and_installed_without_guest_port():
    service = (ROOT / "systemd/evander-privacy-maintenance.service.template").read_text()
    timer = (ROOT / "systemd/evander-privacy-maintenance.timer.template").read_text()
    installer = (ROOT / "scripts/install_systemd_units.sh").read_text()
    assert "Type=oneshot" in service
    assert "privacy maintenance --config __PROJECT_DIR__/runtime --limit 50" in service
    assert "TimeoutStartSec=55" in service
    assert "UMask=0077" in service
    assert "OnUnitInactiveSec=1min" in timer
    assert "evander-privacy-maintenance.service.template" in installer
    assert "evander-privacy-maintenance.timer.template" in installer
    assert '"${EVANDER_TIMERS[@]}"' in installer
    assert "ListenStream" not in service + timer


def test_privacy_docs_disclose_partial_coverage_and_migration():
    doc = (ROOT / "docs/privacy-operations.md").read_text()
    assert "not completion" in doc
    assert "one customer per installation" in doc
    assert "communication_artifacts" in doc
    assert "not implement" in doc or "not yet" in doc
    assert "Old binaries cannot read" in doc
    assert "No live service deployment" in doc
    for name in (
        "ropa-technical-input",
        "dpia-screening-pack",
        "subprocessor-register",
        "retention-schedule",
        "security-appendix",
        "dsr-procedure",
        "personal-data-breach-runbook",
    ):
        assert (ROOT / f"docs/privacy-review/{name}.md").is_file()
