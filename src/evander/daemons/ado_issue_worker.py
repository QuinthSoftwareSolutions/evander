"""Compatibility imports for the separately installed Azure DevOps add-on."""

from evander.optional_integrations import require_azure_devops_module

def __getattr__(name):
    if name.startswith("__"):
        raise AttributeError(name)
    return getattr(require_azure_devops_module('evander_azure_devops_addon.workers.pr_events'), name)

def run_worker_command(*args, **kwargs):
    return require_azure_devops_module('evander_azure_devops_addon.workers.pr_events').run_worker_command(*args, **kwargs)

def main(*args, **kwargs):
    return require_azure_devops_module('evander_azure_devops_addon.workers.pr_events').main(*args, **kwargs)
