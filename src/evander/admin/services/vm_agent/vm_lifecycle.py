from __future__ import annotations

from evander.admin.services.vm_agent.vm_create import VmAgentCreateVmMixin
from evander.admin.services.vm_agent.vm_delete import VmAgentDeleteMixin
from evander.admin.services.vm_agent.vm_logs import VmAgentLogProcessMixin
from evander.admin.services.vm_agent.vm_runtime import VmAgentRuntimeMixin


class VmAgentLifecycleMixin(
    VmAgentRuntimeMixin,
    VmAgentLogProcessMixin,
    VmAgentCreateVmMixin,
    VmAgentDeleteMixin,
):
    pass
