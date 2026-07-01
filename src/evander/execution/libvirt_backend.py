from __future__ import annotations

from typing import Any

from .backend import ExecutionTarget
from .models import (
    ExecutionBackendCapabilities,
    ExecutionBackendKind,
    ExecutionHandle,
    ExecutionSpec,
    ExecutionStatus,
    ExecutionStatusKind,
    PreparedExecution,
)


class LibvirtVmExecutionBackend:
    def __init__(
        self, *, admin_service: Any | None = None, vm_admin_service: Any | None = None
    ) -> None:
        if admin_service is None and vm_admin_service is None:
            raise ValueError(
                "LibvirtVmExecutionBackend requires an admin_service or vm_admin_service."
            )
        self._admin_service = admin_service
        self._vm_admin_service = vm_admin_service

    @property
    def kind(self) -> ExecutionBackendKind:
        return ExecutionBackendKind.LIBVIRT_VM

    @property
    def capabilities(self) -> ExecutionBackendCapabilities:
        return ExecutionBackendCapabilities()

    def prepare(self, spec: ExecutionSpec) -> PreparedExecution:
        target_name = spec.target_name or self._vm_name_for_session(spec.session_id)
        if not target_name:
            raise ValueError("ExecutionSpec requires target_name or a session_id mapped to a VM.")
        metadata = {
            "vm_name": target_name,
            "session_id": spec.session_id,
            "directory_path": spec.directory_path,
            **spec.metadata,
        }
        if spec.vm_provider:
            metadata["vm_provider"] = spec.vm_provider
        return PreparedExecution(
            backend=self.kind,
            execution_id=target_name,
            target_name=target_name,
            session_id=spec.session_id,
            directory_path=spec.directory_path,
            metadata=metadata,
        )

    def start(self, target: ExecutionTarget):
        return self._delegate.start_vm(self._target_name(target))

    def stop(self, target: ExecutionTarget, *, timeout_seconds: int = 60):
        return self._delegate.shutdown_vm(
            self._target_name(target), timeout_seconds=timeout_seconds
        )

    def inspect(self, target: ExecutionTarget) -> ExecutionStatus:
        target_name = self._target_name(target)
        for summary in self._delegate.list_vms(include_inactive=True):
            if self._summary_matches(summary, target_name):
                vm_name = str(getattr(summary, "vm_name", "") or target_name)
                session_ids = list(getattr(summary, "session_ids", []) or [])
                status = self._status_from_power_state(
                    str(getattr(summary, "power_state", "") or "")
                )
                message = f"{vm_name} is {status.value}"
                metadata = {
                    "vm_name": vm_name,
                    "directory_paths": list(getattr(summary, "directory_paths", []) or []),
                    "vm_provider": getattr(summary, "provider", None),
                    "provider_state": getattr(summary, "provider_state", None),
                    "provider_details": dict(getattr(summary, "provider_details", {}) or {}),
                    "ip_address": getattr(summary, "ip_address", None),
                    "agent_state": getattr(summary, "agent_state", None),
                }
                return ExecutionStatus(
                    backend=self.kind,
                    execution_id=vm_name,
                    target_name=vm_name,
                    status=status,
                    session_id=session_ids[0] if len(session_ids) == 1 else None,
                    message=message,
                    metadata=metadata,
                )
        return ExecutionStatus(
            backend=self.kind,
            execution_id=target_name,
            target_name=target_name,
            status=ExecutionStatusKind.UNKNOWN,
            message=f"VM not found: {target_name}",
            metadata={"vm_name": target_name},
        )

    def destroy(self, target: ExecutionTarget):
        return self._delegate.destroy_vm(self._target_name(target))

    def get_log_snapshot(
        self,
        target: ExecutionTarget,
        *,
        lines: int = 50,
        repo_name: str | None = None,
        vm_user: str = "agent",
    ) -> str:
        return self._delegate.get_agent_log_snapshot(
            self._target_name(target),
            lines=lines,
            repo_name=repo_name,
            vm_user=vm_user,
        )

    @property
    def _delegate(self) -> Any:
        if self._vm_admin_service is not None:
            return self._vm_admin_service
        return self._admin_service.vm_agent_admin

    def _target_name(self, target: ExecutionTarget) -> str:
        if isinstance(target, PreparedExecution | ExecutionHandle):
            return target.target_name
        raw_target = str(target or "").strip()
        resolver = getattr(self._delegate, "_resolve_vm_name_from_target", None)
        if callable(resolver):
            try:
                resolved = str(resolver(raw_target) or "").strip()
            except Exception:
                resolved = ""
            if resolved:
                return resolved
        return raw_target

    def _vm_name_for_session(self, session_id: str | None) -> str | None:
        if not session_id:
            return None
        runtime = getattr(self._admin_service, "runtime", None) or getattr(
            self._delegate, "runtime", None
        )
        session_repository = getattr(runtime, "session_repository", None) or getattr(
            runtime, "sessions", None
        )
        if session_repository is None:
            return None
        get_session = getattr(session_repository, "get_session", None)
        session = get_session(session_id) if callable(get_session) else None
        if session is None:
            get = getattr(session_repository, "get", None)
            session = get(session_id) if callable(get) else None
        vm_id = str(getattr(session, "vm_id", "") or "").strip() if session is not None else ""
        return vm_id or None

    def _summary_matches(self, summary: Any, target_name: str) -> bool:
        if str(getattr(summary, "vm_name", "") or "") == target_name:
            return True
        return target_name in set(getattr(summary, "session_ids", []) or [])

    def _status_from_power_state(self, power_state: str) -> ExecutionStatusKind:
        normalized = power_state.strip().casefold()
        if normalized in {"running", "active"}:
            return ExecutionStatusKind.RUNNING
        if normalized in {"shut off", "shutoff", "stopped", "inactive"}:
            return ExecutionStatusKind.STOPPED
        if normalized in {"destroyed"}:
            return ExecutionStatusKind.DESTROYED
        if normalized in {"failed", "crashed"}:
            return ExecutionStatusKind.FAILED
        if normalized in {"starting", "booting"}:
            return ExecutionStatusKind.STARTING
        return ExecutionStatusKind.UNKNOWN
