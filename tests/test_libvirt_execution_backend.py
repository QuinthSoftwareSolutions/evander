from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from agent_sandbox.execution import ExecutionBackendKind, ExecutionSpec, ExecutionStatusKind
from agent_sandbox.execution.libvirt_backend import LibvirtVmExecutionBackend
from agent_sandbox.models import VmActionResult, VmSummary


@dataclass
class CallRecord:
    name: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


class FakeVmAdminService:
    def __init__(self) -> None:
        self.calls: list[CallRecord] = []
        self.summaries = [
            VmSummary(
                vm_name="vm-test",
                provider="libvirt",
                session_ids=["session-1"],
                directory_paths=["root/team"],
                power_state="running",
                provider_state="running",
                ip_address="192.168.250.10",
                agent_state="enabled",
            )
        ]

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append(CallRecord(name=name, args=args, kwargs=dict(kwargs)))

    def _resolve_vm_name_from_target(self, target: str) -> str:
        return {"session-1": "vm-test"}.get(target, target)

    def list_vms(self, **kwargs: Any) -> list[VmSummary]:
        self._record("list_vms", **kwargs)
        return self.summaries

    def start_vm(self, target: str) -> VmActionResult:
        self._record("start_vm", target)
        return VmActionResult(success=True, vm_name=target, action="start_vm", message="started")

    def shutdown_vm(self, target: str, *, timeout_seconds: int = 60) -> VmActionResult:
        self._record("shutdown_vm", target, timeout_seconds=timeout_seconds)
        return VmActionResult(success=True, vm_name=target, action="shutdown_vm", message="stopped")

    def destroy_vm(self, target: str) -> VmActionResult:
        self._record("destroy_vm", target)
        return VmActionResult(
            success=True, vm_name=target, action="destroy_vm", message="destroyed"
        )

    def get_agent_log_snapshot(
        self,
        target: str,
        *,
        lines: int = 50,
        repo_name: str | None = None,
        vm_user: str = "agent",
    ) -> str:
        self._record(
            "get_agent_log_snapshot", target, lines=lines, repo_name=repo_name, vm_user=vm_user
        )
        return "log text"


def test_libvirt_backend_prepare_maps_spec_to_vm_target() -> None:
    backend = LibvirtVmExecutionBackend(vm_admin_service=FakeVmAdminService())

    prepared = backend.prepare(
        ExecutionSpec(
            target_name="vm-test",
            session_id="session-1",
            directory_path="root/team",
            vm_provider="libvirt",
            metadata={"custom": "value"},
        )
    )

    assert prepared.backend == ExecutionBackendKind.LIBVIRT_VM
    assert prepared.execution_id == "vm-test"
    assert prepared.target_name == "vm-test"
    assert prepared.session_id == "session-1"
    assert prepared.metadata == {
        "vm_name": "vm-test",
        "session_id": "session-1",
        "directory_path": "root/team",
        "custom": "value",
        "vm_provider": "libvirt",
    }


def test_libvirt_backend_prepare_resolves_vm_from_session_mapping() -> None:
    vm_admin = FakeVmAdminService()
    admin = SimpleNamespace(
        vm_agent_admin=vm_admin,
        runtime=SimpleNamespace(
            session_repository=SimpleNamespace(
                get_session=lambda session_id: SimpleNamespace(vm_id="vm-from-session")
            )
        ),
    )
    backend = LibvirtVmExecutionBackend(admin_service=admin)

    prepared = backend.prepare(ExecutionSpec(session_id="session-1"))

    assert prepared.execution_id == "vm-from-session"
    assert prepared.target_name == "vm-from-session"


def test_libvirt_backend_delegates_lifecycle_and_logs() -> None:
    vm_admin = FakeVmAdminService()
    backend = LibvirtVmExecutionBackend(vm_admin_service=vm_admin)

    assert backend.start("session-1").message == "started"
    assert backend.stop("vm-test", timeout_seconds=12).message == "stopped"
    assert backend.destroy("vm-test").message == "destroyed"
    assert (
        backend.get_log_snapshot("vm-test", lines=20, repo_name="repo", vm_user="service")
        == "log text"
    )

    assert vm_admin.calls == [
        CallRecord("start_vm", ("vm-test",), {}),
        CallRecord("shutdown_vm", ("vm-test",), {"timeout_seconds": 12}),
        CallRecord("destroy_vm", ("vm-test",), {}),
        CallRecord(
            "get_agent_log_snapshot",
            ("vm-test",),
            {"lines": 20, "repo_name": "repo", "vm_user": "service"},
        ),
    ]


def test_libvirt_backend_inspect_maps_vm_summary_to_execution_status() -> None:
    vm_admin = FakeVmAdminService()
    backend = LibvirtVmExecutionBackend(vm_admin_service=vm_admin)

    status = backend.inspect("session-1")

    assert status.backend == ExecutionBackendKind.LIBVIRT_VM
    assert status.execution_id == "vm-test"
    assert status.target_name == "vm-test"
    assert status.status == ExecutionStatusKind.RUNNING
    assert status.session_id == "session-1"
    assert status.metadata["directory_paths"] == ["root/team"]
    assert status.metadata["ip_address"] == "192.168.250.10"


def test_libvirt_backend_inspect_returns_unknown_when_missing() -> None:
    vm_admin = FakeVmAdminService()
    vm_admin.summaries = []
    backend = LibvirtVmExecutionBackend(vm_admin_service=vm_admin)

    status = backend.inspect("missing-vm")

    assert status.status == ExecutionStatusKind.UNKNOWN
    assert status.message == "VM not found: missing-vm"
