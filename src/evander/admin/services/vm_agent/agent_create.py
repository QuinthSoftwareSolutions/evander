from __future__ import annotations

from evander.admin.services.vm_agent.agent_create_operations import (
    VmAgentCreateOperationMixin,
)
from evander.admin.services.vm_agent.agent_create_request import VmAgentCreateRequestMixin


class VmAgentCreateMixin(
    VmAgentCreateOperationMixin,
    VmAgentCreateRequestMixin,
):
    pass
