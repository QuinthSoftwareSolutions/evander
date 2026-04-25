from __future__ import annotations

from agent_sandbox.execution import (
    ExecutionBackendCapabilities,
    ExecutionBackendKind,
    ExecutionHandle,
    ExecutionSpec,
    ExecutionStatus,
    ExecutionStatusKind,
    PreparedExecution,
)


def test_execution_spec_defaults_and_metadata_preservation() -> None:
    spec = ExecutionSpec(
        target_name=" vm-test ",
        session_id=" session-1 ",
        metadata={"vm_name": "vm-test", "custom": {"a": 1}},
    )

    assert spec.backend == ExecutionBackendKind.LIBVIRT_VM
    assert spec.target_name == "vm-test"
    assert spec.session_id == "session-1"
    assert spec.directory_path == "root"
    assert spec.metadata == {"vm_name": "vm-test", "custom": {"a": 1}}


def test_execution_models_accept_backend_enum_values() -> None:
    prepared = PreparedExecution(
        backend="libvirt_vm",
        execution_id="vm-test",
        target_name="vm-test",
        session_id="session-1",
        directory_path="root/team",
        metadata={"directory_path": "root/team"},
    )
    handle = ExecutionHandle(**prepared.model_dump())
    status = ExecutionStatus(
        backend=ExecutionBackendKind.LIBVIRT_VM,
        execution_id=handle.execution_id,
        target_name=handle.target_name,
        status="running",
        metadata=handle.metadata,
    )

    assert prepared.backend == ExecutionBackendKind.LIBVIRT_VM
    assert handle.target_name == "vm-test"
    assert status.status == ExecutionStatusKind.RUNNING
    assert status.metadata == {"directory_path": "root/team"}


def test_execution_backend_capabilities_default_to_phase_1_supported_operations() -> None:
    capabilities = ExecutionBackendCapabilities()

    assert capabilities.supports_prepare is True
    assert capabilities.supports_stop is True
    assert capabilities.supports_destroy is True
    assert capabilities.supports_log_snapshot is True
