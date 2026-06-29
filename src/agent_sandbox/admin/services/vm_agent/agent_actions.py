from __future__ import annotations

from agent_sandbox.admin.services.vm_agent.agent_logs import VmAgentLogPageMixin
from agent_sandbox.admin.services.vm_agent.agent_mutation import VmAgentMutationMixin
from agent_sandbox.admin.services.vm_agent.agent_prompt import VmAgentPromptMixin
from agent_sandbox.admin.services.vm_agent.agent_session_state import VmAgentSessionStateMixin
from agent_sandbox.admin.services.vm_agent.agent_skills import VmAgentSkillInstallMixin


class VmAgentActionMixin(
    VmAgentSessionStateMixin,
    VmAgentSkillInstallMixin,
    VmAgentMutationMixin,
    VmAgentLogPageMixin,
    VmAgentPromptMixin,
):
    pass
