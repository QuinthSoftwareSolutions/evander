"""Compatibility imports for the separately installed Azure DevOps add-on."""

from evander.optional_integrations import require_azure_devops_module

def __getattr__(name):
    if name.startswith("__"):
        raise AttributeError(name)
    return getattr(require_azure_devops_module('evander_azure_devops_addon.oauth'), name)
