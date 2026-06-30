from __future__ import annotations

from evander.admin.services.vm_agent.vm_log_snapshot import VmAgentLogSnapshotMixin
from evander.admin.services.vm_agent.vm_process_control import VmAgentProcessControlMixin
from evander.admin.services.vm_agent.vm_resume import VmAgentResumeMixin


class VmAgentLogProcessMixin(
    VmAgentLogSnapshotMixin,
    VmAgentProcessControlMixin,
    VmAgentResumeMixin,
):
    pass
