from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_playwright_bootstrap_docs_and_script_are_present() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    ops = (ROOT / "docs" / "operations.md").read_text(encoding="utf-8")
    script = (ROOT / "scripts" / "bootstrap_playwright_vm.sh").read_text(encoding="utf-8")
    guest_ssh_lib = (ROOT / "scripts" / "lib" / "guest_ssh.sh").read_text(encoding="utf-8")

    assert "bootstrap_playwright_vm.sh" in readme
    assert "bootstrap_playwright_vm.sh" in ops
    assert "guest_ssh_resolve_vm_ip" in script
    assert "sudo -n virsh domifaddr" in guest_ssh_lib
    assert "PLAYWRIGHT_BROWSERS_PATH" in script
    assert "playwright-core" in script
