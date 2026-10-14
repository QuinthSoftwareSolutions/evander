"""Legacy registration surface for optional Azure DevOps tools."""
from evander.optional_integrations import azure_devops_enabled, require_azure_devops_module

def register_ado_tools(server, context, deps, **kwargs):
    if not azure_devops_enabled(context.config):
        return
    return require_azure_devops_module("evander_azure_devops_addon.gateway_tools", context.config).register_ado_tools(server, context, deps, **kwargs)
