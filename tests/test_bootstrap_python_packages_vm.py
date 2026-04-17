from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_python_package_bootstrap_uses_file_based_package_spec_handoff() -> None:
    script = (ROOT / "scripts" / "bootstrap_python_packages_vm.sh").read_text(encoding="utf-8")

    assert 'TEMP_PACKAGE_SPECS_JSON="$(mktemp)"' in script
    assert "package-specs.json" in script
    assert 'scp "${SSH_OPTS[@]}" "${TEMP_PACKAGE_SPECS_JSON}" "${VM_USER}@${VM_IP}:${GUEST_PACKAGE_SPECS_JSON}"' in script
    assert "GUEST_PACKAGE_SPECS_JSON" in script
