from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_mssql_bootstrap_docs_and_script_are_present() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    ops = (ROOT / "docs" / "operations.md").read_text(encoding="utf-8")
    script = (ROOT / "scripts" / "bootstrap_mssql_vm.sh").read_text(encoding="utf-8")
    tools_script = (ROOT / "scripts" / "bootstrap_mssql_tools_vm.sh").read_text(encoding="utf-8")
    server_script = (ROOT / "scripts" / "bootstrap_mssql_server_vm.sh").read_text(encoding="utf-8")

    assert "bootstrap_mssql_vm.sh" in readme
    assert "bootstrap_mssql_vm.sh" in ops
    assert "guest-local Microsoft SQL Server" in readme
    assert "guest-local Microsoft SQL Server" in ops
    assert "--init-root PATH" in script
    assert "MSSQL_SA_PASSWORD" in script
    assert "bootstrap_mssql_tools_vm.sh" in script
    assert "bootstrap_mssql_server_vm.sh" in script
    assert 'HOST_MSSQL_TOOLS_ROOT="${HOST_MSSQL_TOOLS_ROOT:-/opt/mssql-tools18}"' in tools_script
    assert 'HOST_MSSQL_ODBC_ROOT="${HOST_MSSQL_ODBC_ROOT:-/opt/microsoft/msodbcsql18}"' in tools_script
    assert 'Expected sqlcmd launcher missing at $HOME/toolchains/mssql-tools/bin/sqlcmd' in tools_script
    assert 'Expected ODBC driver registration missing at $HOME/toolchains/mssql-tools/etc/odbcinst.ini' in tools_script
    assert 'ODBCSYSINI="$HOME/toolchains/mssql-tools/etc"' in tools_script
    assert 'PATH="$HOME/toolchains/mssql-tools/bin:$PATH"' in tools_script
    assert "sqlcmd -? >/dev/null" in tools_script
    assert 'HOST_MSSQL_SERVER_ROOT="${HOST_MSSQL_SERVER_ROOT:-/opt/mssql}"' in server_script
    assert 'HOST_MSSQL_SERVICE_FILE="${HOST_MSSQL_SERVICE_FILE:-/usr/lib/systemd/system/mssql-server.service}"' in server_script
    assert 'export LD_LIBRARY_PATH="/opt/mssql/lib:/opt/mssql-host-libs:${LD_LIBRARY_PATH:-}"' in server_script
    assert '/opt/mssql/bin/mssql-conf -n setup' in server_script
    assert '/opt/mssql/bin/mssql-conf -n set-sa-password' in server_script
    assert 'reset_only=1' in server_script
    assert 'systemctl restart mssql-server' in server_script
    assert 'sqlcmd -S localhost,1433' in script
