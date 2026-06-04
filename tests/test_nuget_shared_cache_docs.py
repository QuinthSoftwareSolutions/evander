from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_shared_nuget_cache_paths_are_documented_in_scripts() -> None:
    launch_script = (ROOT / "scripts" / "launch_agent_vm.sh").read_text(encoding="utf-8")
    cloud_init = (ROOT / "config" / "cloud-init-user-data.yaml").read_text(encoding="utf-8")
    prewarm_script = (ROOT / "scripts" / "prewarm_nuget_cache.sh").read_text(encoding="utf-8")

    assert (
        'SHARED_NUGET_CACHE_DIR="${SHARED_NUGET_CACHE_DIR:-${VM_STORAGE_DIR}/nuget-cache/global-packages}"'
        in launch_script
    )
    assert (
        'VIRT_INSTALL_FILESYSTEM_ARG="source=${SHARED_NUGET_CACHE_DIR},target=${SHARED_NUGET_CACHE_TAG},driver.type=virtiofs"'
        in launch_script
    )
    assert '--filesystem "${VIRT_INSTALL_FILESYSTEM_ARG}"' in launch_script
    assert (
        "__NUGET_CACHE_TAG__ /opt/agent-sandbox/nuget-cache-ro __NUGET_CACHE_FS_TYPE__ __NUGET_CACHE_MOUNT_OPTIONS__ 0 0"
        in cloud_init
    )
    assert (
        'SHARED_NUGET_CACHE_DIR="${SHARED_NUGET_CACHE_DIR:-/var/lib/libvirt/images/agent-sandbox/nuget-cache/global-packages}"'
        in prewarm_script
    )
    assert "-p:NuGetAudit=false" in prewarm_script


def test_container_worker_nuget_cache_prewarm_script_updates_worker_fallback_cache() -> None:
    script = (ROOT / "scripts" / "prewarm_container_worker_nuget_cache.sh").read_text(
        encoding="utf-8"
    )
    operations = (ROOT / "docs" / "operations.md").read_text(encoding="utf-8")

    assert "--worker HOST" in script
    assert "--local" in script
    assert "--target PATH" in script
    assert "--dotnet PATH" in script
    assert (
        'CACHE_PATH="${CONTAINER_WORKER_NUGET_CACHE_PATH:-/var/lib/agent-worker/toolchain-cache/nuget-cache-ro}"'
        in script
    )
    assert 'DOTNET_COMMAND="${DOTNET_COMMAND:-dotnet}"' in script
    assert 'NUGET_RELAY_BASE_URL="${NUGET_RELAY_BASE_URL:-http://127.0.0.1:11666}"' in script
    assert "Microsoft.Extensions.FileSystemGlobbing" in script
    assert 'NUGET_PACKAGES="${STAGE_PACKAGES}"' in script
    assert '"${DOTNET_COMMAND}" restore "${target}" --configfile "${NUGET_CONFIG}"' in script
    assert 'scp "${ARCHIVE}" "${worker}:${remote_archive}"' in script
    assert 'tar -C "${CACHE_PATH}" -xzf "${ARCHIVE}"' in script
    assert "Missing after extraction" in script
    assert "prewarm_container_worker_nuget_cache.sh" in operations
