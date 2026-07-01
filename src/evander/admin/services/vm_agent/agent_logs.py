from __future__ import annotations

from evander.admin.services.vm_agent.agent_log_chunks import VmAgentLogChunkMixin
from evander.admin.services.vm_agent.agent_log_snapshot import VmAgentAdminLogSnapshotMixin
from evander.admin.services.vm_agent.agent_log_threads import VmAgentThreadLogMixin


class VmAgentLogPageMixin(
    VmAgentAdminLogSnapshotMixin,
    VmAgentLogChunkMixin,
    VmAgentThreadLogMixin,
):
    pass
