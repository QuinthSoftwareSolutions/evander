from pathlib import Path


def test_dotnet_bootstrap_docs_and_script_are_present() -> None:
    readme = Path("/home/jonathan/agent-sandbox/README.md").read_text(encoding="utf-8")
    ops = Path("/home/jonathan/agent-sandbox/docs/operations.md").read_text(encoding="utf-8")
    script = Path("/home/jonathan/agent-sandbox/scripts/bootstrap_dotnet_vm.sh").read_text(
        encoding="utf-8"
    )

    assert "bootstrap_dotnet_vm.sh" in readme
    assert "bootstrap_dotnet_vm.sh" in ops
    assert "sudo -n virsh domifaddr" in script
    assert 'HOST_DOTNET_ROOT="${HOST_DOTNET_ROOT:-/usr/lib/dotnet}"' in script
    assert 'HOST_DOTNET_TOOLS_ROOT="${HOST_DOTNET_TOOLS_ROOT:-${CALLER_HOME}/.dotnet}"' in script
    assert 'HOST_NUGET_PACKAGES_ROOT="${HOST_NUGET_PACKAGES_ROOT:-${CALLER_HOME}/.nuget/packages}"' in script
    assert 'tar -C "${HOST_DOTNET_ROOT}" -cf - .' in script
    assert 'Expected dotnet launcher missing at $HOME/toolchains/dotnet/dotnet' in script
    assert 'grep -q \'DOTNET_ROOT="$HOME/toolchains/dotnet"\' ~/.bashrc' in script
    assert 'export DOTNET_ROOT="$HOME/toolchains/dotnet"' in script
    assert 'export PATH="$HOME/toolchains/dotnet:$HOME/.dotnet/tools:$PATH"' in script
    assert 'dotnet-ef --version' in script
