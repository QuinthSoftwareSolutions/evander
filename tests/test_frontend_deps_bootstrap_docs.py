from pathlib import Path


def test_frontend_deps_bootstrap_docs_and_script_are_present() -> None:
    readme = Path("/home/jonathan/agent-sandbox/README.md").read_text(encoding="utf-8")
    ops = Path("/home/jonathan/agent-sandbox/docs/operations.md").read_text(encoding="utf-8")
    script = Path("/home/jonathan/agent-sandbox/scripts/bootstrap_frontend_deps_vm.sh").read_text(
        encoding="utf-8"
    )

    assert "bootstrap_frontend_deps_vm.sh" in readme
    assert "bootstrap_frontend_deps_vm.sh" in ops
    assert "HOST_CHECKOUT_ROOT" in script
    assert "install-roots" in script
    assert "Missing package.json under install root" in script
    assert 'tar -C "${HOST_NODE_ROOT}" -cf - .' in script
    assert 'ssh -n "${SSH_OPTS[@]}"' in script
    assert "npm --version" in script
    assert "npx --version" in script
