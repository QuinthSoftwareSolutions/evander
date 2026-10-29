from __future__ import annotations

from evander.admin.services.vm_agent_log_snapshot_service import (
    VmAgentLogSnapshotDependencies,
    VmAgentLogSnapshotService,
)


class VmAgentLogSnapshotMixin:
    """Compatibility adapter for the composed guest-log reader."""

    def get_agent_log_snapshot(
        self,
        target: str,
        *,
        lines: int = 50,
        repo_name: str | None = None,
        vm_user: str = "agent",
    ) -> str:
        service = getattr(self, "_vm_log_snapshots", None)
        if service is None:
            service = VmAgentLogSnapshotService(
                VmAgentLogSnapshotDependencies(
                    resolve_vm_name=lambda selected: self._resolve_vm_name_from_target(selected),
                    control_ssh_users=lambda selected: self._control_ssh_users(selected),
                )
            )
        return service.read(target, lines=lines, repo_name=repo_name, vm_user=vm_user)
