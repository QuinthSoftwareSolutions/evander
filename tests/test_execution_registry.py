from __future__ import annotations

import pytest

from agent_sandbox.config import GatewayConfig
from agent_sandbox.execution import ExecutionBackendKind, build_execution_backend
from agent_sandbox.execution.libvirt_backend import LibvirtVmExecutionBackend


class FakeAdminService:
    vm_agent_admin = object()


def test_execution_backend_factory_defaults_to_libvirt_vm(tmp_path) -> None:
    config = GatewayConfig(runtime_dir=tmp_path)

    backend = build_execution_backend(config, admin_service=FakeAdminService(), project_dir=tmp_path)

    assert isinstance(backend, LibvirtVmExecutionBackend)
    assert backend.kind == ExecutionBackendKind.LIBVIRT_VM


def test_execution_backend_factory_rejects_reserved_container_backend(tmp_path) -> None:
    config = GatewayConfig(runtime_dir=tmp_path, execution_backend="container_worker_vm")

    with pytest.raises(NotImplementedError, match="container_worker_vm"):
        build_execution_backend(config, admin_service=FakeAdminService(), project_dir=tmp_path)
