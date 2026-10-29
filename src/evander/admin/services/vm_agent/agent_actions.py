from __future__ import annotations

from evander.admin.services.vm_agent.agent_logs import VmAgentLogPageMixin
from evander.admin.services.vm_agent.agent_mutation import VmAgentMutationMixin
from evander.admin.services.vm_agent.agent_prompt import VmAgentPromptMixin
from evander.admin.services.vm_agent.agent_session_state import VmAgentSessionStateMixin
from evander.admin.services.vm_agent.agent_skills import VmAgentSkillInstallMixin
from evander.admin.services.vm_agent.git_reconciliation import (
    VmAgentManagedGitReconciliationMixin,
)


class VmAgentActionMixin(
    VmAgentManagedGitReconciliationMixin,
    VmAgentSessionStateMixin,
    VmAgentSkillInstallMixin,
    VmAgentMutationMixin,
    VmAgentLogPageMixin,
    VmAgentPromptMixin,
):
    pass
