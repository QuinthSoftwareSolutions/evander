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
