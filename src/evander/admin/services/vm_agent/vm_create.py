from __future__ import annotations

from evander.admin.services.vm_agent.vm_bootstrap import VmAgentBootstrapMixin
from evander.admin.services.vm_agent.vm_create_options import VmAgentCreateOptionsMixin
from evander.admin.services.vm_agent.vm_issue_create import VmAgentIssueCreateMixin


class VmAgentCreateVmMixin(
    VmAgentIssueCreateMixin,
    VmAgentCreateOptionsMixin,
    VmAgentBootstrapMixin,
):
    pass
