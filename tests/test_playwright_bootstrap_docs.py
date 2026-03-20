from pathlib import Path


def test_playwright_bootstrap_docs_and_script_are_present() -> None:
    readme = Path("/home/jonathan/agent-sandbox/README.md").read_text(encoding="utf-8")
    ops = Path("/home/jonathan/agent-sandbox/docs/operations.md").read_text(encoding="utf-8")
    script = Path("/home/jonathan/agent-sandbox/scripts/bootstrap_playwright_vm.sh").read_text(
        encoding="utf-8"
    )

    assert "bootstrap_playwright_vm.sh" in readme
    assert "bootstrap_playwright_vm.sh" in ops
    assert "sudo -n virsh domifaddr" in script
    assert "PLAYWRIGHT_BROWSERS_PATH" in script
    assert "playwright-core" in script
