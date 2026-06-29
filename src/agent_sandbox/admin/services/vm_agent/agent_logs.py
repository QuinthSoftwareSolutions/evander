from __future__ import annotations

from agent_sandbox.admin.services.vm_agent.agent_log_chunks import VmAgentLogChunkMixin
from agent_sandbox.admin.services.vm_agent.agent_log_snapshot import VmAgentAdminLogSnapshotMixin
from agent_sandbox.admin.services.vm_agent.agent_log_threads import VmAgentThreadLogMixin


class VmAgentLogPageMixin(
    VmAgentAdminLogSnapshotMixin,
    VmAgentLogChunkMixin,
    VmAgentThreadLogMixin,
):
    pass
