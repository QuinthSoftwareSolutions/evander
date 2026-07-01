from __future__ import annotations

from evander.admin.services.vm_agent.container_session_actions import (
    VmAgentContainerSessionActionMixin,
)
from evander.admin.services.vm_agent.container_session_helpers import (
    VmAgentContainerSessionHelperMixin,
)
from evander.admin.services.vm_agent.container_session_lifecycle import (
    VmAgentContainerSessionLifecycleMixin,
)
from evander.admin.services.vm_agent.container_session_policy import (
    VmAgentContainerSessionPolicyMixin,
)


class VmAgentContainerSessionMixin(
    VmAgentContainerSessionActionMixin,
    VmAgentContainerSessionHelperMixin,
    VmAgentContainerSessionLifecycleMixin,
    VmAgentContainerSessionPolicyMixin,
):
    pass
