from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .backend import ExecutionBackend
from .libvirt_backend import LibvirtVmExecutionBackend
from .models import ExecutionBackendKind

if TYPE_CHECKING:
    from agent_sandbox.config import GatewayConfig


def build_execution_backend(
    config: GatewayConfig,
    *,
    admin_service: Any | None = None,
    project_dir: Path | None = None,
) -> ExecutionBackend:
    del project_dir
    backend = ExecutionBackendKind(str(config.execution_backend))
    if backend == ExecutionBackendKind.LIBVIRT_VM:
        return LibvirtVmExecutionBackend(admin_service=admin_service)
    if backend == ExecutionBackendKind.CONTAINER_WORKER_VM:
        raise NotImplementedError(
            "Execution backend 'container_worker_vm' has a worker daemon, "
            "but gateway-side orchestration is not wired yet."
        )
    raise ValueError(f"Unsupported execution backend: {backend.value}")
