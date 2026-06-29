from __future__ import annotations

from agent_sandbox.admin.services.vm_agent.vm_log_snapshot import VmAgentLogSnapshotMixin
from agent_sandbox.admin.services.vm_agent.vm_process_control import VmAgentProcessControlMixin
from agent_sandbox.admin.services.vm_agent.vm_resume import VmAgentResumeMixin


class VmAgentLogProcessMixin(
    VmAgentLogSnapshotMixin,
    VmAgentProcessControlMixin,
    VmAgentResumeMixin,
):
    pass
