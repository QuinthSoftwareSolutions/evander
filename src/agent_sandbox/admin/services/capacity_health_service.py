from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from agent_sandbox.admin.service import CapacityHealthPayload


class _CapacityHealthAdminServiceHost(Protocol):
    def _read_capacity_health_impl(self) -> "CapacityHealthPayload": ...


class CapacityHealthAdminService:
    """Capacity and health telemetry operations extracted from AdminService."""

    def __init__(self, host: _CapacityHealthAdminServiceHost) -> None:
        self._host = host

    def read_capacity_health(self) -> "CapacityHealthPayload":
        return self._host._read_capacity_health_impl()
