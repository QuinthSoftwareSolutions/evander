from __future__ import annotations

from agent_sandbox.admin.services.vm_agent.vm_create import VmAgentCreateVmMixin
from agent_sandbox.admin.services.vm_agent.vm_delete import VmAgentDeleteMixin
from agent_sandbox.admin.services.vm_agent.vm_logs import VmAgentLogProcessMixin
from agent_sandbox.admin.services.vm_agent.vm_repo import VmAgentRepoMixin
from agent_sandbox.admin.services.vm_agent.vm_runtime import VmAgentRuntimeMixin


class VmAgentLifecycleMixin(
    VmAgentRepoMixin,
    VmAgentRuntimeMixin,
    VmAgentLogProcessMixin,
    VmAgentCreateVmMixin,
    VmAgentDeleteMixin,
):
    pass
