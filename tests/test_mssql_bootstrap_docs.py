from pathlib import Path


def test_mssql_bootstrap_docs_and_script_are_present() -> None:
    readme = Path("/home/jonathan/agent-sandbox/README.md").read_text(encoding="utf-8")
    ops = Path("/home/jonathan/agent-sandbox/docs/operations.md").read_text(encoding="utf-8")
    script = Path("/home/jonathan/agent-sandbox/scripts/bootstrap_mssql_vm.sh").read_text(
        encoding="utf-8"
    )

    assert "bootstrap_mssql_vm.sh" in readme
    assert "bootstrap_mssql_vm.sh" in ops

    assert 'HOST_MSSQL_TOOLS_ROOT="${HOST_MSSQL_TOOLS_ROOT:-/opt/mssql-tools18}"' in script
    assert 'Expected sqlcmd launcher missing at $HOME/toolchains/mssql-tools/bin/sqlcmd' in script
    assert 'PATH="$HOME/toolchains/mssql-tools/bin:$PATH"' in script
    assert "sqlcmd -? >/dev/null" in script
