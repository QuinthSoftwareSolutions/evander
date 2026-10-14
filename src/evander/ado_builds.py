"""Directory-scoped Azure DevOps build reads shared by workflow providers."""

from __future__ import annotations
from evander.optional_integrations import optional_azure_devops_class

from typing import Any





def positive_id(value: Any, name: str) -> int:
    if isinstance(value, bool) or not str(value).isdigit() or int(value) <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return int(value)



AdoBuildClient = optional_azure_devops_class('evander_azure_devops_addon.builds', 'AdoBuildClient')
