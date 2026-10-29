from __future__ import annotations

import pytest

from evander.config import GatewayConfig
from evander.execution import ExecutionBackendKind, build_execution_backend
from evander.execution.container_worker_backend import ContainerWorkerVmExecutionBackend
from evander.execution.libvirt_backend import LibvirtVmExecutionBackend


class FakeAdminService:
    vm_agent_admin = object()


def test_execution_backend_factory_defaults_to_libvirt_vm(tmp_path) -> None:
    config = GatewayConfig(runtime_dir=tmp_path)

    backend = build_execution_backend(
        config, admin_service=FakeAdminService(), project_dir=tmp_path
    )

    assert isinstance(backend, LibvirtVmExecutionBackend)
    assert backend.kind == ExecutionBackendKind.LIBVIRT_VM


def test_execution_backend_factory_reports_container_worker_url_is_required(tmp_path) -> None:
    config = GatewayConfig(runtime_dir=tmp_path, execution_backend="container_worker_vm")

    with pytest.raises(ValueError, match="container_worker_base_url"):
        build_execution_backend(config, admin_service=FakeAdminService(), project_dir=tmp_path)


def test_execution_backend_factory_builds_container_worker_backend_when_configured(
    tmp_path,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test:11888",
    )

    backend = build_execution_backend(
        config, admin_service=FakeAdminService(), project_dir=tmp_path
    )

    assert isinstance(backend, ContainerWorkerVmExecutionBackend)
    assert backend.kind == ExecutionBackendKind.CONTAINER_WORKER_VM
