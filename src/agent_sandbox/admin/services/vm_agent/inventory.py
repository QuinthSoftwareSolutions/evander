from __future__ import annotations

from agent_sandbox.admin.services.vm_agent.inventory_container_records import (
    VmAgentContainerRecordInventoryMixin,
)
from agent_sandbox.admin.services.vm_agent.inventory_listing import VmAgentInventoryListingMixin
from agent_sandbox.admin.services.vm_agent.inventory_prompt_queue import (
    VmAgentPromptQueueInventoryMixin,
)
from agent_sandbox.admin.services.vm_agent.inventory_vm_records import VmAgentVmRecordInventoryMixin


class VmAgentInventoryMixin(
    VmAgentPromptQueueInventoryMixin,
    VmAgentVmRecordInventoryMixin,
    VmAgentContainerRecordInventoryMixin,
    VmAgentInventoryListingMixin,
):
    pass
