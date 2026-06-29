from __future__ import annotations

from agent_sandbox.admin.services.vm_agent.container_session_actions import (
    VmAgentContainerSessionActionMixin,
)
from agent_sandbox.admin.services.vm_agent.container_session_helpers import (
    VmAgentContainerSessionHelperMixin,
)
from agent_sandbox.admin.services.vm_agent.container_session_lifecycle import (
    VmAgentContainerSessionLifecycleMixin,
)
from agent_sandbox.admin.services.vm_agent.container_session_policy import (
    VmAgentContainerSessionPolicyMixin,
)


class VmAgentContainerSessionMixin(
    VmAgentContainerSessionActionMixin,
    VmAgentContainerSessionHelperMixin,
    VmAgentContainerSessionLifecycleMixin,
    VmAgentContainerSessionPolicyMixin,
):
    pass
