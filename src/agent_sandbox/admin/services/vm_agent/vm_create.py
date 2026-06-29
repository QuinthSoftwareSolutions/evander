from __future__ import annotations

from agent_sandbox.admin.services.vm_agent.vm_bootstrap import VmAgentBootstrapMixin
from agent_sandbox.admin.services.vm_agent.vm_create_options import VmAgentCreateOptionsMixin
from agent_sandbox.admin.services.vm_agent.vm_issue_create import VmAgentIssueCreateMixin


class VmAgentCreateVmMixin(
    VmAgentIssueCreateMixin,
    VmAgentCreateOptionsMixin,
    VmAgentBootstrapMixin,
):
    pass
