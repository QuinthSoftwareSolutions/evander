from __future__ import annotations

from evander.admin.services.vm_agent.agent_create_operations import (
    VmAgentCreateOperationMixin,
)
from evander.admin.services.vm_agent.agent_create_request import VmAgentCreateRequestMixin
from evander.admin.services.vm_agent.agent_copy import VmAgentCopyMixin


class VmAgentCreateMixin(
    VmAgentCopyMixin,
    VmAgentCreateOperationMixin,
    VmAgentCreateRequestMixin,
):
    pass
