from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .base import QueryProviderBase


_VM_STATE_CODE_BY_LIBVIRT_STATE: dict[str, str] = {
    "running": "running",
    "idle": "running",
    "paused": "paused",
    "in shutdown": "stopping",
    "shut off": "shut_off",
    "crashed": "crashed",
    "pmsuspended": "suspended",
}


class VmRunStateQueryProvider(QueryProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(resolved_config or {})
        session_id = self._normalize_optional_string(payload.get("session_id"))
        vm_name = self._normalize_optional_string(payload.get("vm_name"))
        if not session_id and not vm_name:
            raise ValueError("query:vm:run-state requires session_id or vm_name")

        context = self._context()
        candidates = context.admin_service.list_vms(include_inactive=True, omit_state=True)
        selected = [
            vm
            for vm in candidates
            if (session_id is None or session_id in vm.session_ids)
            and (vm_name is None or vm.vm_name == vm_name)
        ]
        if not selected:
            raise RuntimeError(
                "No VM matched query:vm:run-state"
                f" for session_id={session_id!r} vm_name={vm_name!r}"
            )
        if len(selected) > 1:
            raise RuntimeError(
                "query:vm:run-state matched multiple VMs"
                f" for session_id={session_id!r} vm_name={vm_name!r}"
            )

        vm = selected[0]
        libvirt_state = str(vm.libvirt_state or "unknown").strip().lower()
        state_code = _VM_STATE_CODE_BY_LIBVIRT_STATE.get(libvirt_state, "unknown")
        is_running = state_code == "running"
        is_terminal = state_code in {"shut_off", "crashed"}
        return {
            "vm_name": vm.vm_name,
            "session_ids": list(vm.session_ids),
            "libvirt_state": vm.libvirt_state,
            "state_code": state_code,
            "is_running": is_running,
            "is_terminal": is_terminal,
        }

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("VmRunStateQueryProvider has no runtime context")
        return self.runtime_context

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None
