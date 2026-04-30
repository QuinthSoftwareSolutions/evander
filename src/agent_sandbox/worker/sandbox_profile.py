from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal


WorkerSandboxProfileName = Literal["default", "strict"]


@dataclass(frozen=True)
class WorkerSandboxProfile:
    name: str
    network_mode: str = "none"
    cap_drop: tuple[str, ...] = ("ALL",)
    security_opt: tuple[str, ...] = ("no-new-privileges",)
    privileged: bool = False
    read_only_rootfs: bool = False
    tmpfs: tuple[str, ...] = ()
    user: str | None = None
    workdir: str = "/workspace"
    default_pids_limit: int | None = None

    def with_overrides(
        self,
        *,
        network_mode: str | None = None,
        read_only_rootfs: bool | None = None,
        tmpfs: tuple[str, ...] | list[str] | None = None,
        user: str | None = None,
    ) -> "WorkerSandboxProfile":
        updated = replace(
            self,
            network_mode=self.network_mode if network_mode is None else network_mode,
            read_only_rootfs=self.read_only_rootfs
            if read_only_rootfs is None
            else read_only_rootfs,
            tmpfs=self.tmpfs if tmpfs is None else tuple(tmpfs),
            user=self.user if user is None else user,
        )
        return validate_worker_sandbox_profile(updated)


def default_worker_sandbox_profile() -> WorkerSandboxProfile:
    return WorkerSandboxProfile(name="default")


def strict_worker_sandbox_profile() -> WorkerSandboxProfile:
    return WorkerSandboxProfile(
        name="strict",
        read_only_rootfs=True,
        tmpfs=("/tmp:rw,noexec,nosuid,size=512m",),
        default_pids_limit=512,
    )


def worker_sandbox_profile(name: str) -> WorkerSandboxProfile:
    normalized = str(name or "").strip().lower()
    if normalized == "default":
        return default_worker_sandbox_profile()
    if normalized == "strict":
        return strict_worker_sandbox_profile()
    raise ValueError("sandbox profile must be one of: default, strict")


def validate_worker_sandbox_profile(profile: WorkerSandboxProfile) -> WorkerSandboxProfile:
    network_mode = str(profile.network_mode or "none").strip()
    if network_mode == "host":
        raise ValueError("container sandbox profile cannot use host networking")
    if profile.privileged:
        raise ValueError("privileged worker containers are not allowed")
    if not profile.workdir.startswith("/"):
        raise ValueError("container sandbox workdir must be an absolute container path")
    if profile.default_pids_limit is not None and profile.default_pids_limit <= 0:
        raise ValueError("container sandbox default pids limit must be positive")
    if any(not str(item).strip() for item in profile.cap_drop):
        raise ValueError("container sandbox cap_drop entries must be non-empty")
    if any(not str(item).strip() for item in profile.security_opt):
        raise ValueError("container sandbox security_opt entries must be non-empty")
    if any(not str(item).strip() for item in profile.tmpfs):
        raise ValueError("container sandbox tmpfs entries must be non-empty")
    user = profile.user
    if user is not None:
        user = str(user).strip() or None
    return replace(profile, network_mode=network_mode or "none", user=user)


@dataclass(frozen=True)
class WorkerReadOnlyMount:
    host_path: str
    container_path: str

    @property
    def docker_volume_arg(self) -> str:
        return f"{self.host_path}:{self.container_path}:ro"


def parse_worker_read_only_mount(spec: str) -> WorkerReadOnlyMount:
    parts = str(spec or "").strip().split(":")
    if len(parts) == 2:
        host_path, container_path = parts
    elif len(parts) == 3 and parts[2] == "ro":
        host_path, container_path = parts[0], parts[1]
    else:
        raise ValueError(
            "read-only mounts must use /host/path:/container/path or "
            "/host/path:/container/path:ro"
        )
    mount = WorkerReadOnlyMount(host_path=host_path, container_path=container_path)
    validate_worker_read_only_mount(mount)
    return mount


def validate_worker_read_only_mount(mount: WorkerReadOnlyMount) -> WorkerReadOnlyMount:
    reject_unsafe_mount(mount.host_path, mount.container_path)
    if not mount.host_path.startswith("/"):
        raise ValueError("read-only mount host path must be absolute")
    if not mount.container_path.startswith("/"):
        raise ValueError("read-only mount container path must be absolute")
    if mount.container_path == "/":
        raise ValueError("read-only mount container path must not be /")
    for writable_root in ("/workspace", "/home/agent", "/artifacts"):
        if mount.container_path == writable_root or mount.container_path.startswith(
            f"{writable_root}/"
        ):
            raise ValueError(
                f"read-only mounts must not shadow writable session mount {writable_root}"
            )
    return mount


def reject_unsafe_mount(host_path: object, container_path: object) -> None:
    paths = {str(host_path), str(container_path)}
    if "/var/run/docker.sock" in paths:
        raise ValueError("Docker socket mounts are not allowed in worker containers")
