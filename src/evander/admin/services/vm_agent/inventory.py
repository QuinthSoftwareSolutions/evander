from __future__ import annotations

from evander.admin.services.vm_agent.inventory_container_records import (
    VmAgentContainerRecordInventoryMixin,
)
from evander.admin.services.vm_agent.inventory_listing import VmAgentInventoryListingMixin
from evander.admin.services.vm_agent.inventory_prompt_queue import (
    VmAgentPromptQueueInventoryMixin,
)
from evander.admin.services.vm_agent.inventory_vm_records import VmAgentVmRecordInventoryMixin


class VmAgentInventoryMixin(
    VmAgentPromptQueueInventoryMixin,
    VmAgentVmRecordInventoryMixin,
    VmAgentContainerRecordInventoryMixin,
    VmAgentInventoryListingMixin,
):
    pass
