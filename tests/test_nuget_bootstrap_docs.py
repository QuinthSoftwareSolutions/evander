from pathlib import Path


def test_nuget_bootstrap_docs_and_script_are_present() -> None:
    readme = Path("/home/jonathan/agent-sandbox/README.md").read_text(encoding="utf-8")
    ops = Path("/home/jonathan/agent-sandbox/docs/operations.md").read_text(encoding="utf-8")
    script = Path("/home/jonathan/agent-sandbox/scripts/bootstrap_nuget_vm.sh").read_text(
        encoding="utf-8"
    )

    assert "bootstrap_nuget_vm.sh" in readme
    assert "bootstrap_nuget_vm.sh" in ops
    assert 'NUGET_RELAY_BASE_URL="${NUGET_RELAY_BASE_URL:-http://192.168.250.1:11666}"' in script
    assert 'NUGET_FALLBACK_PACKAGES="${NUGET_FALLBACK_PACKAGES:-/opt/agent-sandbox/nuget-cache-ro}"' in script
    assert "fallbackPackageFolders" in script
    assert "globalPackagesFolder" in script
    assert "sandbox-nuget" in script
    assert "sandbox-azure" in script
    assert "dotnet nuget list source" in script
    assert "prewarm_nuget_cache.sh" in readme
    assert "Host Shared NuGet Cache" in ops
