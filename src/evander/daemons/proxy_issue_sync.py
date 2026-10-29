"""Compatibility imports for the separately installed Azure DevOps add-on."""

from evander.optional_integrations import require_azure_devops_module

def __getattr__(name):
    if name.startswith("__"):
        raise AttributeError(name)
    return getattr(require_azure_devops_module('evander_azure_devops_addon.workers.proxy_sync'), name)

DEFAULT_PROXY_ISSUE_SYNC_FRESHNESS_SECONDS = 300.0
DEFAULT_PROXY_ISSUE_SYNC_INTERVAL_SECONDS = 300.0

def run_proxy_issue_sync_worker(*args, **kwargs):
    return require_azure_devops_module('evander_azure_devops_addon.workers.proxy_sync').run_proxy_issue_sync_worker(*args, **kwargs)

def run_once_from_runtime(*args, **kwargs):
    return require_azure_devops_module('evander_azure_devops_addon.workers.proxy_sync').run_once_from_runtime(*args, **kwargs)

def main(*args, **kwargs):
    return require_azure_devops_module('evander_azure_devops_addon.workers.proxy_sync').main(*args, **kwargs)
