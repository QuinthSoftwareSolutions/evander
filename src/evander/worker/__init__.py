from __future__ import annotations

from importlib import import_module

# Importing a journal or model must not initialize the daemon/API dependency tree.
_EXPORT_MODULES = {
    "api": ("WorkerAuthMiddleware", "create_app"),
    "docker_runtime": ("DockerContainerRuntime",),
    "runtime": ("ContainerRuntime",),
    "service": ("WorkerDaemon",),
    "store": ("WorkerJobStore",),
    "sandbox_profile": (
        "WorkerReadOnlyMount",
        "WorkerSandboxProfile",
        "default_worker_sandbox_profile",
        "parse_worker_read_only_mount",
        "strict_worker_sandbox_profile",
        "validate_worker_read_only_mount",
        "validate_worker_sandbox_profile",
        "worker_sandbox_profile",
    ),
}

__all__ = [
    "DEFAULT_WORKER_ROOT",
    "SAFE_ID_PATTERN",
    "ContainerRuntime",
    "DockerContainerRuntime",
    "WorkerAuthMiddleware",
    "WorkerDaemon",
    "WorkerFileWrite",
    "WorkerFileWriteRecord",
    "WorkerFileWriteRequest",
    "WorkerFailureCause",
    "WorkerGitWriteKeyRecord",
    "WorkerGitWriteKeyRequest",
    "WorkerDiskTelemetry",
    "WorkerJobRecord",
    "WorkerJobRequest",
    "WorkerJobTelemetry",
    "WorkerMssqlDatabaseDeleteRecord",
    "WorkerMssqlDatabaseListRecord",
    "WorkerMssqlDatabaseRecord",
    "WorkerMssqlDatabaseRequest",
    "WorkerMssqlDatabaseState",
    "WorkerMssqlSessionDeleteRecord",
    "WorkerMssqlSessionState",
    "WorkerJobStore",
    "WorkerReadOnlyMount",
    "WorkerRepoCloneRecord",
    "WorkerRepoCloneRequest",
    "WorkerResourceLimits",
    "WorkerSandboxProfile",
    "WorkerSessionPrepareRequest",
    "WorkerSessionPaths",
    "WorkerSessionRecord",
    "WorkerSessionStorageTelemetry",
    "WorkerTaskDependencyGitSource",
    "WorkerTaskWorktreeCleanupRecord",
    "WorkerTaskWorktreeCleanupRequest",
    "WorkerTaskWorktreeRecord",
    "WorkerTaskWorktreeRequest",
    "WorkerTelemetryPayload",
    "create_app",
    "default_worker_sandbox_profile",
    "parse_worker_read_only_mount",
    "strict_worker_sandbox_profile",
    "validate_worker_read_only_mount",
    "validate_worker_sandbox_profile",
    "worker_sandbox_profile",
]

_EXPORTS = {name: module for module, names in _EXPORT_MODULES.items() for name in names}
_EXPORTS.update({name: "models" for name in __all__ if name not in _EXPORTS})


def __getattr__(name: str):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f"{__name__}.{module}"), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
