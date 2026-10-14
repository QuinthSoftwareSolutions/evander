"""Legacy workflow node IDs; execution is supplied by the optional add-on."""

from evander.optional_integrations import optional_azure_devops_class
from evander.queries.providers.base import QueryProviderBase

AdoWorkItemsQueryProvider = optional_azure_devops_class('evander_azure_devops_addon.queries.ado_work_items', 'AdoWorkItemsQueryProvider', base=QueryProviderBase)
