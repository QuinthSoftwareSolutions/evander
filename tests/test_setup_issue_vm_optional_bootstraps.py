from pathlib import Path


def test_setup_issue_vm_supports_opt_in_bootstraps() -> None:
    setup_issue_vm = Path("/home/jonathan/agent-sandbox/scripts/setup_issue_vm.sh").read_text(
        encoding="utf-8"
    )

    assert "--dotnet" in setup_issue_vm
    assert "--nuget" in setup_issue_vm
    assert "--mssql" in setup_issue_vm
    assert 'BOOTSTRAP_DOTNET=0' in setup_issue_vm
    assert 'BOOTSTRAP_NUGET=0' in setup_issue_vm
    assert 'BOOTSTRAP_MSSQL=0' in setup_issue_vm
    assert '"${SCRIPT_DIR}/bootstrap_dotnet_vm.sh"' in setup_issue_vm
    assert '"${SCRIPT_DIR}/bootstrap_nuget_vm.sh"' in setup_issue_vm
    assert '"${SCRIPT_DIR}/bootstrap_mssql_vm.sh"' in setup_issue_vm
