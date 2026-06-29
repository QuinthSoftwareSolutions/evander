from __future__ import annotations

from agent_sandbox.admin.services.vm_agent.agent_create_operations import (
    VmAgentCreateOperationMixin,
)
from agent_sandbox.admin.services.vm_agent.agent_create_request import VmAgentCreateRequestMixin


class VmAgentCreateMixin(
    VmAgentCreateOperationMixin,
    VmAgentCreateRequestMixin,
):
    pass
